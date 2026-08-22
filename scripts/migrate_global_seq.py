#!/usr/bin/env python3
"""Safe global_seq cutover migration for legacy execution event stores.

Policy (P0-4):
    - Legacy events (pre-migration) are NOT assigned fabricated historical
      cross-aggregate order. Their true order is unknowable.
    - We mark legacy events with global_seq = -1 to indicate "pre-migration".
    - A verified snapshot is created as the cutover boundary.
    - All NEW events appended after migration receive strictly monotonic
      global_seq > 0.
    - Replay must handle pre-migration events separately (via snapshot or
      aggregate-local replay).
    - Migration is idempotent and supports dry-run.

Usage:
    python scripts/migrate_global_seq.py <db_path> [--dry-run] [--force]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def _snapshot_checksum(state: dict[str, Any]) -> str:
    blob = json.dumps(state, default=str, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def migrate(db_path: str, *, dry_run: bool = False, force: bool = False) -> int:
    """Perform safe cutover migration.

    Returns the number of legacy events marked with global_seq = -1.
    """
    if not Path(db_path).exists():
        print(f"ERROR: database not found: {db_path}")
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Ensure schema is up to date
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_submission_claims (
                intent_id            TEXT PRIMARY KEY,
                claimed_by           TEXT NOT NULL,
                claimed_at           TEXT NOT NULL,
                idempotency_key      TEXT NOT NULL,
                payload_hash         TEXT NOT NULL,
                status               TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_exec_submission_claim_idem
                ON execution_submission_claims (idempotency_key);

            CREATE TABLE IF NOT EXISTS execution_migration_state (
                migration_id         TEXT PRIMARY KEY,
                snapshot_id          TEXT NOT NULL,
                cutover_at           TEXT NOT NULL,
                first_post_cutover_global_seq INTEGER NOT NULL,
                legacy_event_count   INTEGER NOT NULL,
                checksum             TEXT NOT NULL,
                created_at           TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_migration_single
                ON execution_migration_state (migration_id);
            """
        )
        conn.commit()

        # Check if migration already completed
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM execution_migration_state"
        ).fetchone()
        if row and row["c"] > 0 and not force:
            print("Migration already completed. Use --force to rerun.")
            return 0

        # Count legacy events needing migration
        cursor = conn.execute(
            "SELECT COUNT(*) AS c FROM execution_events WHERE global_seq = 0"
        )
        row = cursor.fetchone()
        legacy_count = row["c"] if row else 0

        if legacy_count == 0:
            print(f"No pre-migration events found in {db_path}. Nothing to do.")
            return 0

        print(f"Found {legacy_count} pre-migration events in {db_path}.")

        if dry_run:
            print("DRY RUN — no changes will be made.")
            sample = conn.execute(
                "SELECT event_id, aggregate_id, seq, occurred_at, global_seq "
                "FROM execution_events WHERE global_seq = 0 "
                "ORDER BY occurred_at, aggregate_id, seq LIMIT 5"
            ).fetchall()
            print("Sample events to mark as legacy:")
            for r in sample:
                print(
                    f"  {r['event_id']} | {r['aggregate_id']} | seq={r['seq']} | "
                    f"occurred_at={r['occurred_at']}"
                )
            return legacy_count

        # Create verified cutover snapshot
        snapshot_state = {
            "migration_policy": "cutover",
            "legacy_event_count": legacy_count,
            "cutover_at": datetime.now(UTC).isoformat(),
            "note": (
                "Pre-migration events are NOT assigned fabricated global_seq. "
                "Post-cutover events receive strict monotonic global_seq > 0."
            ),
        }
        snapshot_id = f"migration-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        snapshot_checksum = _snapshot_checksum(snapshot_state)

        conn.execute("BEGIN")
        try:
            # 1. Mark legacy events as pre-migration (global_seq = -1)
            conn.execute(
                "UPDATE execution_events SET global_seq = -1 WHERE global_seq = 0"
            )
            legacy_updated = conn.total_changes

            # 2. Record migration metadata
            conn.execute(
                """
                INSERT INTO execution_migration_state
                (migration_id, snapshot_id, cutover_at, first_post_cutover_global_seq,
                 legacy_event_count, checksum, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    snapshot_id,
                    snapshot_state["cutover_at"],
                    1,  # first post-cutover global_seq
                    legacy_count,
                    snapshot_checksum,
                    datetime.now(UTC).isoformat(),
                ),
            )

            conn.commit()
            print(
                f"Migration complete. {legacy_updated} legacy events marked with global_seq = -1."
            )
            print(f"Snapshot ID: {snapshot_id}")
            return legacy_updated
        except Exception as exc:
            conn.rollback()
            print(f"ERROR during migration: {exc}")
            raise
    finally:
        conn.close()


def verify(db_path: str) -> bool:
    """Verify migration integrity."""
    if not Path(db_path).exists():
        print(f"ERROR: database not found: {db_path}")
        return False

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Check migration metadata exists
        row = conn.execute(
            "SELECT * FROM execution_migration_state ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            print("No migration metadata found.")
            return False

        print(f"Migration ID: {row['migration_id']}")
        print(f"Cutover at: {row['cutover_at']}")
        print(f"Legacy events: {row['legacy_event_count']}")

        # Check no legacy events have global_seq = 0
        legacy = conn.execute(
            "SELECT COUNT(*) AS c FROM execution_events WHERE global_seq = 0"
        ).fetchone()
        if legacy and legacy["c"] > 0:
            print(f"WARNING: {legacy['c']} events still have global_seq = 0!")
            return False

        # Check post-cutover events are strictly monotonic
        post = conn.execute(
            "SELECT global_seq FROM execution_events WHERE global_seq > 0 ORDER BY global_seq"
        ).fetchall()
        seqs = [r["global_seq"] for r in post]
        if len(seqs) != len(set(seqs)):
            print("WARNING: duplicate global_seq values found in post-cutover events!")
            return False
        if seqs != list(range(1, len(seqs) + 1)):
            print("WARNING: post-cutover global_seq is not strictly monotonic from 1!")
            return False

        print("Verification passed.")
        return True
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Safe global_seq cutover migration for execution event stores."
    )
    parser.add_argument("db_path", help="Path to SQLite database")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without mutating the DB",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force rerun even if migration already completed",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify migration integrity instead of running",
    )
    args = parser.parse_args()

    if args.verify:
        ok = verify(args.db_path)
        raise SystemExit(0 if ok else 1)

    count = migrate(args.db_path, dry_run=args.dry_run, force=args.force)
    if args.dry_run:
        print(f"\nDry run complete. {count} events would be marked as legacy.")
    else:
        print(f"\nMigration complete. {count} legacy events marked.")


if __name__ == "__main__":
    main()
