#!/usr/bin/env python3
"""Safe global_seq migration for legacy execution event stores.

Usage:
    python scripts/migrate_global_seq.py <db_path> [--dry-run]

Policy:
    - Pre-migration events (global_seq = -1 or 0) are assigned new global_seq
      values based on (occurred_at, aggregate_id, seq) as a best-effort
      proxy for cross-aggregate ordering.
    - Post-migration events (global_seq > 0) are left untouched.
    - The migration is idempotent: running it twice produces the same result.
    - A dry-run mode shows what would change without modifying the DB.
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def migrate(db_path: str, *, dry_run: bool = False) -> int:
    """Assign global_seq to pre-migration events.

    Returns the number of rows updated.
    """
    if not Path(db_path).exists():
        print(f"ERROR: database not found: {db_path}")
        return 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # Ensure new tables exist (idempotent)
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
            """
        )
        conn.commit()

        # Check if migration is needed
        cursor = conn.execute(
            "SELECT COUNT(*) AS c FROM execution_events "
            "WHERE global_seq = -1 OR global_seq = 0"
        )
        row = cursor.fetchone()
        legacy_count = row["c"] if row else 0

        if legacy_count == 0:
            print(f"No pre-migration events found in {db_path}. Nothing to do.")
            return 0

        print(f"Found {legacy_count} pre-migration events in {db_path}.")

        if dry_run:
            print("DRY RUN — no changes will be made.")
            # Show a sample of what would be migrated
            sample = conn.execute(
                "SELECT event_id, aggregate_id, seq, occurred_at, global_seq "
                "FROM execution_events WHERE global_seq = -1 OR global_seq = 0 "
                "ORDER BY occurred_at, aggregate_id, seq LIMIT 5"
            ).fetchall()
            print("Sample events to migrate:")
            for r in sample:
                print(
                    f"  {r['event_id']} | {r['aggregate_id']} | seq={r['seq']} | "
                    f"occurred_at={r['occurred_at']} | current global_seq={r['global_seq']}"
                )
            return legacy_count

        # Assign new global_seq values based on (occurred_at, aggregate_id, seq)
        # This provides a deterministic, monotonic ordering.
        # We use a window function to assign row numbers.
        conn.execute("BEGIN")
        try:
            # Create a temporary table with the new ordering
            conn.execute(
                """
                WITH ordered AS (
                    SELECT
                        event_id,
                        ROW_NUMBER() OVER (
                            ORDER BY occurred_at ASC, aggregate_id ASC, seq ASC
                        ) AS new_global_seq
                    FROM execution_events
                    WHERE global_seq = -1 OR global_seq = 0
                )
                UPDATE execution_events
                SET global_seq = ordered.new_global_seq
                FROM ordered
                WHERE execution_events.event_id = ordered.event_id
                """
            )
            updated = conn.total_changes
            conn.commit()
            print(f"Successfully migrated {updated} events.")

            # Verify no duplicates or gaps in the migrated range
            min_seq = conn.execute(
                "SELECT MIN(global_seq) FROM execution_events WHERE global_seq > 0"
            ).fetchone()[0]
            max_seq = conn.execute(
                "SELECT MAX(global_seq) FROM execution_events WHERE global_seq > 0"
            ).fetchone()[0]
            expected = max_seq - min_seq + 1
            actual = conn.execute(
                "SELECT COUNT(*) FROM execution_events WHERE global_seq > 0"
            ).fetchone()[0]
            if actual != expected:
                print(
                    f"WARNING: global_seq has gaps or duplicates! "
                    f"expected {expected} rows in range [{min_seq}, {max_seq}], "
                    f"found {actual}"
                )
            else:
                print(
                    f"Verification passed: {actual} events with global_seq > 0, "
                    f"no gaps in [{min_seq}, {max_seq}]."
                )

            # Check for any remaining -1 or 0 values
            remaining = conn.execute(
                "SELECT COUNT(*) FROM execution_events "
                "WHERE global_seq = -1 OR global_seq = 0"
            ).fetchone()[0]
            if remaining > 0:
                print(f"WARNING: {remaining} events still have global_seq = -1 or 0!")
            else:
                print("All events now have positive global_seq.")

            return updated
        except Exception as exc:
            conn.rollback()
            print(f"ERROR during migration: {exc}")
            raise
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate legacy execution events to strict global_seq ordering."
    )
    parser.add_argument("db_path", help="Path to SQLite database")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without making changes",
    )
    args = parser.parse_args()

    count = migrate(args.db_path, dry_run=args.dry_run)
    if args.dry_run:
        print(f"\nDry run complete. {count} events would be migrated.")
    else:
        print(f"\nMigration complete. {count} events updated.")


if __name__ == "__main__":
    main()
