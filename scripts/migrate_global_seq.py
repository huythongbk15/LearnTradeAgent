#!/usr/bin/env python3
"""Fail-closed cutover for execution logs without trustworthy global order.

Legacy rows retain ``global_seq = -1``. Their capital state comes from an
immutable verified snapshot; only events written after cutover receive the
database-enforced sequence ``1, 2, 3, ...``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_agent.execution.lifecycle.events import (  # noqa: E402
    EVENT_SCHEMA_VERSION,
    ExecutionEvent,
    ExecutionEventType,
    UnknownEventTypeError,
)
from trading_agent.execution.lifecycle.lifecycle import LifecycleState  # noqa: E402
from trading_agent.execution.lifecycle.store import (  # noqa: E402
    CUTOVER_MIGRATION_VERSION,
    LegacyCutoverStateRequired,
    LegacyMigrationError,
    snapshot_checksum,
    source_event_checksum,
)

FINANCIAL_EVENT_TYPES = frozenset(
    {
        ExecutionEventType.ORDER_INTENT_CREATED.value,
        ExecutionEventType.RISK_APPROVED.value,
        ExecutionEventType.ORDER_AUTHORIZED.value,
        ExecutionEventType.BROKER_SUBMISSION_REQUESTED.value,
        ExecutionEventType.BROKER_IO_STARTED.value,
        ExecutionEventType.ORDER_SUBMITTED.value,
        ExecutionEventType.BROKER_ACKNOWLEDGED.value,
        ExecutionEventType.PARTIAL_FILL_RECEIVED.value,
        ExecutionEventType.FILL_RECEIVED.value,
        ExecutionEventType.BROKER_STATE_UNKNOWN.value,
        ExecutionEventType.RECONCILIATION_STARTED.value,
        ExecutionEventType.PROTECTIVE_ORDER_CREATED.value,
        ExecutionEventType.PROTECTIVE_ORDER_ACKNOWLEDGED.value,
    }
)

REQUIRED_STATE_FIELDS = frozenset(
    {
        "orders",
        "protective_orders",
        "reconciliation",
        "execution_health",
        "protection_state",
        "manual_blocked",
        "unresolved_manual_intents",
        "last_event_ids",
        "state_version",
    }
)

REQUIRED_ORDER_FIELDS = frozenset(
    {
        "intent_id",
        "symbol",
        "side",
        "size",
        "status",
        "risk_approved",
        "risk_decision",
        "authorization_id",
        "idempotency_key",
        "payload_hash",
        "permission",
        "authorized_at",
        "submission_requested",
        "io_started",
        "broker_order_id",
        "exchange_order_id",
        "filled_size",
        "authorized_quantity",
        "reserved_quantity",
        "released_quantity",
        "avg_fill_price",
        "fees",
        "price_reference",
        "portfolio_equity",
        "current_position_quantity",
        "resulting_position_quantity",
        "current_exposure",
        "resulting_exposure",
        "incremental_exposure",
        "protective_order_ids",
        "manual_reasons",
    }
)

_CUTOVER_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS execution_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        aggregate_id TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        state_version INTEGER NOT NULL,
        last_seq INTEGER NOT NULL,
        state_json TEXT NOT NULL,
        checksum TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE (aggregate_id)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_cutover_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        aggregate_id TEXT NOT NULL UNIQUE CHECK (aggregate_id = 'global'),
        schema_version INTEGER NOT NULL,
        state_version INTEGER NOT NULL,
        last_seq INTEGER NOT NULL CHECK (last_seq = 0),
        state_json TEXT NOT NULL,
        checksum TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS execution_migration_state (
        migration_id TEXT PRIMARY KEY,
        migration_version INTEGER NOT NULL,
        snapshot_id TEXT NOT NULL UNIQUE,
        snapshot_checksum TEXT NOT NULL,
        source_event_count INTEGER NOT NULL,
        source_event_checksum TEXT NOT NULL,
        legacy_event_count INTEGER NOT NULL,
        cutover_at TEXT NOT NULL,
        first_post_cutover_global_seq INTEGER NOT NULL CHECK (
            first_post_cutover_global_seq = 1
        ),
        schema_version INTEGER NOT NULL,
        state_version INTEGER NOT NULL,
        source_provenance TEXT NOT NULL,
        source_db_fingerprint TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY (snapshot_id)
            REFERENCES execution_cutover_snapshots(snapshot_id)
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_global_seq_positive
    ON execution_events(global_seq)
    WHERE global_seq > 0
    """,
)


@dataclass(frozen=True)
class SnapshotAuthority:
    snapshot_id: str
    state: dict[str, Any]
    schema_version: int
    state_version: int
    last_global_seq: int
    checksum: str
    provenance: str
    verified_empty: bool


@dataclass(frozen=True)
class MigrationAnalysis:
    legacy_event_count: int
    known_event_count: int
    unknown_event_count: int
    existing_snapshot: bool
    migration_possible: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "legacy_event_count": self.legacy_event_count,
            "known_event_count": self.known_event_count,
            "unknown_event_count": self.unknown_event_count,
            "existing_snapshot": self.existing_snapshot,
            "migration_possible": self.migration_possible,
            "reason": self.reason,
        }


def _connect(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=30.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    ).fetchone()
    return row is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row["name"])
        for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _require_source_integrity(conn: sqlite3.Connection) -> None:
    row = conn.execute("PRAGMA integrity_check").fetchone()
    if row is None or row[0] != "ok":
        raise LegacyMigrationError("source database integrity check failed")
    if not _table_exists(conn, "execution_events"):
        raise LegacyMigrationError("execution_events table is missing")
    required = {
        "event_id",
        "seq",
        "aggregate_id",
        "event_type",
        "schema_version",
        "payload",
        "correlation_id",
        "causation_id",
        "occurred_at",
        "ingested_at",
        "global_seq",
    }
    missing = required - _table_columns(conn, "execution_events")
    if missing:
        raise LegacyMigrationError(
            "legacy execution schema is incomplete; missing "
            + ", ".join(sorted(missing))
        )


def _legacy_rows(conn: sqlite3.Connection, value: int = 0) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM execution_events WHERE global_seq = ? ORDER BY event_id",
        (value,),
    ).fetchall()


def _scan_known_events(rows: list[sqlite3.Row]) -> tuple[int, int]:
    known = 0
    unknown = 0
    for row in rows:
        try:
            event = ExecutionEvent.from_row(dict(row))
            if event.schema_version != EVENT_SCHEMA_VERSION:
                raise ValueError("unsupported legacy event schema")
            known += 1
        except (UnknownEventTypeError, KeyError, TypeError, ValueError):
            unknown += 1
    return known, unknown


def _normalize_snapshot_state(state: dict[str, Any]) -> dict[str, Any]:
    missing = REQUIRED_STATE_FIELDS - state.keys()
    if missing:
        raise LegacyMigrationError(
            "verified snapshot is missing semantic fields: "
            + ", ".join(sorted(missing))
        )
    orders = state.get("orders")
    if not isinstance(orders, dict):
        raise LegacyMigrationError("verified snapshot orders must be an object")
    for intent_id, order in orders.items():
        if not isinstance(order, dict):
            raise LegacyMigrationError(f"snapshot order {intent_id} is invalid")
        missing_order = REQUIRED_ORDER_FIELDS - order.keys()
        if missing_order:
            raise LegacyMigrationError(
                f"snapshot order {intent_id} is missing: "
                + ", ".join(sorted(missing_order))
            )
        if str(intent_id) != str(order["intent_id"]):
            raise LegacyMigrationError(
                f"snapshot order key {intent_id} does not match intent_id"
            )
        numeric_fields = (
            "size",
            "filled_size",
            "authorized_quantity",
            "reserved_quantity",
            "released_quantity",
            "avg_fill_price",
            "fees",
            "price_reference",
            "portfolio_equity",
            "current_position_quantity",
            "resulting_position_quantity",
            "current_exposure",
            "resulting_exposure",
            "incremental_exposure",
        )
        for field in numeric_fields:
            value = order.get(field)
            if value is not None:
                try:
                    finite = math.isfinite(float(value))
                except (TypeError, ValueError) as exc:
                    raise LegacyMigrationError(
                        f"snapshot order {intent_id} has invalid {field}"
                    ) from exc
                if not finite:
                    raise LegacyMigrationError(
                        f"snapshot order {intent_id} has non-finite {field}"
                    )
    protective_orders = state.get("protective_orders")
    if not isinstance(protective_orders, dict):
        raise LegacyMigrationError(
            "verified snapshot protective_orders must be an object"
        )
    for order_id, protective in protective_orders.items():
        if not isinstance(protective, dict) or str(order_id) != str(
            protective.get("order_id")
        ):
            raise LegacyMigrationError(
                f"snapshot protective order key {order_id} is inconsistent"
            )
    try:
        normalized = LifecycleState.from_dict(state).to_dict()
        round_tripped = LifecycleState.from_dict(normalized).to_dict()
    except (KeyError, TypeError, ValueError) as exc:
        raise LegacyMigrationError(
            "verified snapshot cannot reconstruct LifecycleState"
        ) from exc
    if normalized != round_tripped:
        raise LegacyMigrationError("verified snapshot semantic round-trip is lossy")
    return normalized


def _is_empty_state(state: dict[str, Any]) -> bool:
    return (
        not state["orders"]
        and not state["protective_orders"]
        and not state["protection_state"]
        and not state["manual_blocked"]
        and not state["unresolved_manual_intents"]
        and int(state["state_version"]) == 0
    )


def _existing_global_snapshot(conn: sqlite3.Connection) -> SnapshotAuthority | None:
    if not _table_exists(conn, "execution_snapshots"):
        return None
    row = conn.execute(
        "SELECT * FROM execution_snapshots WHERE aggregate_id = 'global'"
    ).fetchone()
    if row is None:
        return None
    try:
        state = json.loads(row["state_json"])
    except (json.JSONDecodeError, TypeError) as exc:
        raise LegacyMigrationError("existing global snapshot is unreadable") from exc
    normalized = _normalize_snapshot_state(state)
    checksum = snapshot_checksum(state)
    if checksum != row["checksum"]:
        raise LegacyMigrationError("existing global snapshot checksum mismatch")
    if int(row["schema_version"]) != EVENT_SCHEMA_VERSION:
        raise LegacyMigrationError("existing global snapshot schema is incompatible")
    if int(row["state_version"]) != int(normalized["state_version"]):
        raise LegacyMigrationError("existing global snapshot state_version mismatch")
    if int(row["last_seq"]) != 0:
        raise LegacyMigrationError(
            "existing snapshot is not a pre-cutover authority (last_seq must be 0)"
        )
    return SnapshotAuthority(
        snapshot_id=str(row["snapshot_id"]),
        state=normalized,
        schema_version=EVENT_SCHEMA_VERSION,
        state_version=int(normalized["state_version"]),
        last_global_seq=0,
        checksum=snapshot_checksum(normalized),
        provenance=f"existing-global-snapshot:{row['snapshot_id']}",
        verified_empty=False,
    )


def _operator_snapshot(path: str | Path) -> SnapshotAuthority:
    snapshot_path = Path(path)
    try:
        envelope = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LegacyMigrationError(
            f"operator snapshot cannot be read: {snapshot_path}"
        ) from exc
    if not isinstance(envelope, dict) or not isinstance(envelope.get("state"), dict):
        raise LegacyMigrationError("operator snapshot must contain a state object")
    provenance = envelope.get("provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        raise LegacyMigrationError("operator snapshot provenance is required")
    try:
        schema_version = int(envelope.get("schema_version", -1))
        last_global_seq = int(envelope.get("last_global_seq", -1))
        state_version = int(envelope.get("state_version", -1))
    except (TypeError, ValueError) as exc:
        raise LegacyMigrationError(
            "operator snapshot version metadata is invalid"
        ) from exc
    if schema_version != EVENT_SCHEMA_VERSION:
        raise LegacyMigrationError("operator snapshot schema is incompatible")
    if last_global_seq != 0:
        raise LegacyMigrationError("operator snapshot last_global_seq must be 0")
    normalized = _normalize_snapshot_state(envelope["state"])
    supplied_checksum = envelope.get("checksum")
    normalized_checksum = snapshot_checksum(normalized)
    if not isinstance(supplied_checksum, str) or not supplied_checksum:
        raise LegacyMigrationError("operator snapshot checksum is required")
    if supplied_checksum != normalized_checksum:
        raise LegacyMigrationError("operator snapshot checksum mismatch")
    if state_version != int(normalized["state_version"]):
        raise LegacyMigrationError("operator snapshot state_version mismatch")
    return SnapshotAuthority(
        snapshot_id=f"cutover-{uuid.uuid4()}",
        state=normalized,
        schema_version=schema_version,
        state_version=state_version,
        last_global_seq=0,
        checksum=normalized_checksum,
        provenance=provenance.strip(),
        verified_empty=envelope.get("verified_empty") is True,
    )


def _select_authority(
    conn: sqlite3.Connection, snapshot_path: str | Path | None
) -> SnapshotAuthority | None:
    if snapshot_path is not None:
        return _operator_snapshot(snapshot_path)
    return _existing_global_snapshot(conn)


def _validate_authority_for_rows(
    authority: SnapshotAuthority | None, rows: list[sqlite3.Row]
) -> SnapshotAuthority:
    if authority is None:
        raise LegacyCutoverStateRequired(
            "legacy execution history has no trustworthy global ordering and no "
            "verified pre-cutover snapshot is available; refusing migration"
        )
    event_types = {str(row["event_type"]) for row in rows}
    if event_types & FINANCIAL_EVENT_TYPES:
        if _is_empty_state(authority.state) and not authority.verified_empty:
            raise LegacyCutoverStateRequired(
                "non-empty capital-changing legacy history cannot use an empty "
                "authoritative snapshot without explicit empty-state verification"
            )
    return authority


def _source_db_fingerprint(db_path: str | Path, count: int, checksum: str) -> str:
    material = f"{Path(db_path).resolve()}|{count}|{checksum}".encode()
    return hashlib.sha256(material).hexdigest()


def analyze(
    db_path: str | Path, *, snapshot_path: str | Path | None = None
) -> MigrationAnalysis:
    """Return a read-only migration feasibility report."""
    path = Path(db_path)
    if not path.exists():
        return MigrationAnalysis(0, 0, 0, False, False, "database not found")
    conn = _connect(path)
    try:
        _require_source_integrity(conn)
        rows = _legacy_rows(conn)
        known, unknown = _scan_known_events(rows)
        existing = bool(
            _table_exists(conn, "execution_snapshots")
            and conn.execute(
                "SELECT 1 FROM execution_snapshots WHERE aggregate_id = 'global'"
            ).fetchone()
        )
        positive = conn.execute(
            "SELECT COUNT(*) AS c FROM execution_events WHERE global_seq > 0"
        ).fetchone()["c"]
        if not rows:
            return MigrationAnalysis(0, 0, 0, existing, True, "nothing to migrate")
        if positive:
            return MigrationAnalysis(
                len(rows),
                known,
                unknown,
                existing,
                False,
                "legacy and positive global sequences are mixed before cutover",
            )
        if unknown:
            return MigrationAnalysis(
                len(rows),
                known,
                unknown,
                existing,
                False,
                "unknown legacy execution events require an explicit translator",
            )
        try:
            authority = _select_authority(conn, snapshot_path)
            _validate_authority_for_rows(authority, rows)
        except LegacyMigrationError as exc:
            return MigrationAnalysis(
                len(rows), known, unknown, existing, False, str(exc)
            )
        return MigrationAnalysis(
            len(rows), known, 0, existing, True, "verified cutover is possible"
        )
    except LegacyMigrationError as exc:
        return MigrationAnalysis(0, 0, 0, False, False, str(exc))
    finally:
        conn.close()


def _assert_migration_schema(conn: sqlite3.Connection) -> None:
    expected = {
        "migration_id",
        "migration_version",
        "snapshot_id",
        "snapshot_checksum",
        "source_event_count",
        "source_event_checksum",
        "legacy_event_count",
        "cutover_at",
        "first_post_cutover_global_seq",
        "schema_version",
        "state_version",
        "source_provenance",
        "source_db_fingerprint",
        "created_at",
    }
    missing = expected - _table_columns(conn, "execution_migration_state")
    if missing:
        raise LegacyMigrationError(
            "migration metadata table is incompatible; missing "
            + ", ".join(sorted(missing))
        )


def _positive_index_is_unique(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA index_list(execution_events)").fetchall():
        if row["name"] == "idx_exec_global_seq_positive":
            return bool(row["unique"]) and bool(row["partial"])
    return False


def _verify_cutover_connection(conn: sqlite3.Connection) -> None:
    _require_source_integrity(conn)
    if _legacy_rows(conn, 0):
        raise LegacyMigrationError("unmigrated global_seq = 0 rows remain")
    legacy_rows = _legacy_rows(conn, -1)
    if legacy_rows:
        known, unknown = _scan_known_events(legacy_rows)
        if unknown or known != len(legacy_rows):
            raise LegacyMigrationError(
                "unknown legacy execution event blocks cutover verification"
            )
        if not _table_exists(conn, "execution_migration_state"):
            raise LegacyCutoverStateRequired("cutover metadata is missing")
        _assert_migration_schema(conn)
        metadata_rows = conn.execute(
            "SELECT * FROM execution_migration_state"
        ).fetchall()
        if len(metadata_rows) != 1:
            raise LegacyMigrationError("exactly one cutover marker is required")
        metadata = metadata_rows[0]
        if int(metadata["migration_version"]) != CUTOVER_MIGRATION_VERSION:
            raise LegacyMigrationError("unsupported cutover migration version")
        snapshot = conn.execute(
            "SELECT * FROM execution_cutover_snapshots WHERE snapshot_id = ?",
            (metadata["snapshot_id"],),
        ).fetchone()
        if snapshot is None:
            raise LegacyCutoverStateRequired("immutable cutover snapshot is missing")
        try:
            state = json.loads(snapshot["state_json"])
        except (json.JSONDecodeError, TypeError) as exc:
            raise LegacyMigrationError("cutover snapshot is unreadable") from exc
        normalized = _normalize_snapshot_state(state)
        checksum = snapshot_checksum(normalized)
        if (
            checksum != snapshot["checksum"]
            or checksum != metadata["snapshot_checksum"]
        ):
            raise LegacyMigrationError("cutover snapshot checksum mismatch")
        if int(metadata["legacy_event_count"]) != len(legacy_rows):
            raise LegacyMigrationError("legacy event count changed after cutover")
        if int(metadata["source_event_count"]) != len(legacy_rows):
            raise LegacyMigrationError("source event count changed after cutover")
        if metadata["source_event_checksum"] != source_event_checksum(legacy_rows):
            raise LegacyMigrationError("legacy source checksum changed after cutover")
        if int(metadata["first_post_cutover_global_seq"]) != 1:
            raise LegacyMigrationError("first post-cutover global sequence is not 1")
    positive = [
        int(row["global_seq"])
        for row in conn.execute(
            "SELECT global_seq FROM execution_events WHERE global_seq > 0 "
            "ORDER BY global_seq"
        ).fetchall()
    ]
    if positive and positive != list(range(1, positive[-1] + 1)):
        raise LegacyMigrationError("post-cutover global sequence is not contiguous")
    if not _positive_index_is_unique(conn):
        raise LegacyMigrationError("positive global_seq unique index is missing")


def migrate(
    db_path: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
    snapshot_path: str | Path | None = None,
    _before_commit: Callable[[], None] | None = None,
) -> int:
    """Atomically establish snapshot authority and mark legacy rows as ``-1``."""
    del force  # Authority and idempotency cannot be bypassed.
    path = Path(db_path)
    if not path.exists():
        raise LegacyMigrationError(f"database not found: {path}")
    if dry_run:
        report = analyze(path, snapshot_path=snapshot_path)
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return report.legacy_event_count

    conn = _connect(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_source_integrity(conn)
        rows = _legacy_rows(conn)
        if not rows:
            if _legacy_rows(conn, -1):
                _verify_cutover_connection(conn)
                conn.rollback()
                print("Migration already completed and verified.")
                return 0
            conn.rollback()
            print("No legacy events found. Nothing to migrate.")
            return 0
        positive = conn.execute(
            "SELECT COUNT(*) AS c FROM execution_events WHERE global_seq > 0"
        ).fetchone()["c"]
        if positive:
            raise LegacyMigrationError(
                "positive global sequences already exist beside uncut legacy rows; "
                "refusing ambiguous cutover"
            )
        known, unknown = _scan_known_events(rows)
        if unknown:
            raise LegacyMigrationError(
                f"{unknown} unknown legacy execution event(s) found; "
                "explicit versioned translation is required"
            )
        if known != len(rows):
            raise LegacyMigrationError("legacy event validation was incomplete")
        authority = _validate_authority_for_rows(
            _select_authority(conn, snapshot_path), rows
        )
        source_checksum = source_event_checksum(rows)
        source_count = len(rows)
        cutover_at = datetime.now(UTC).isoformat()
        migration_id = f"legacy-cutover-v{CUTOVER_MIGRATION_VERSION}-{uuid.uuid4()}"

        for statement in _CUTOVER_SCHEMA_STATEMENTS:
            conn.execute(statement)
        _assert_migration_schema(conn)
        if not _positive_index_is_unique(conn):
            raise LegacyMigrationError("positive global_seq index is not unique")
        if conn.execute("SELECT COUNT(*) FROM execution_migration_state").fetchone()[0]:
            raise LegacyMigrationError("migration marker already exists before cutover")

        state_json = json.dumps(authority.state, default=str, sort_keys=True)
        conn.execute(
            """
            INSERT INTO execution_cutover_snapshots
            (snapshot_id, aggregate_id, schema_version, state_version, last_seq,
             state_json, checksum, created_at)
            VALUES (?, 'global', ?, ?, 0, ?, ?, ?)
            """,
            (
                authority.snapshot_id,
                authority.schema_version,
                authority.state_version,
                state_json,
                authority.checksum,
                cutover_at,
            ),
        )
        conn.execute(
            """
            INSERT OR REPLACE INTO execution_snapshots
            (snapshot_id, aggregate_id, schema_version, state_version, last_seq,
             state_json, checksum, created_at)
            VALUES (?, 'global', ?, ?, 0, ?, ?, ?)
            """,
            (
                authority.snapshot_id,
                authority.schema_version,
                authority.state_version,
                state_json,
                authority.checksum,
                cutover_at,
            ),
        )
        updated = conn.execute(
            "UPDATE execution_events SET global_seq = -1 WHERE global_seq = 0"
        ).rowcount
        if updated != source_count:
            raise LegacyMigrationError("legacy row count changed during cutover")
        conn.execute(
            """
            INSERT INTO execution_migration_state
            (migration_id, migration_version, snapshot_id, snapshot_checksum,
             source_event_count, source_event_checksum, legacy_event_count,
             cutover_at, first_post_cutover_global_seq, schema_version,
             state_version, source_provenance, source_db_fingerprint, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            """,
            (
                migration_id,
                CUTOVER_MIGRATION_VERSION,
                authority.snapshot_id,
                authority.checksum,
                source_count,
                source_checksum,
                source_count,
                cutover_at,
                authority.schema_version,
                authority.state_version,
                authority.provenance,
                _source_db_fingerprint(path, source_count, source_checksum),
                cutover_at,
            ),
        )
        _verify_cutover_connection(conn)
        if _before_commit is not None:
            _before_commit()
        conn.commit()
        print(f"Migration complete. {updated} legacy events marked global_seq = -1.")
        print(f"Cutover snapshot: {authority.snapshot_id}")
        return updated
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def verify(db_path: str | Path) -> bool:
    path = Path(db_path)
    if not path.exists():
        print(f"ERROR: database not found: {path}")
        return False
    conn = _connect(path)
    try:
        _verify_cutover_connection(conn)
        print("Verification passed.")
        return True
    except LegacyMigrationError as exc:
        print(f"Verification failed: {exc}")
        return False
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fail-closed global_seq cutover for execution event stores"
    )
    parser.add_argument("db_path", help="Path to SQLite database")
    parser.add_argument("--dry-run", action="store_true", help="Read-only feasibility")
    parser.add_argument("--verify", action="store_true", help="Verify cutover")
    parser.add_argument(
        "--snapshot",
        help="Verified operator snapshot JSON envelope used as cutover authority",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Accepted for compatibility; never bypasses authority checks",
    )
    args = parser.parse_args()
    if args.verify:
        raise SystemExit(0 if verify(args.db_path) else 1)
    try:
        count = migrate(
            args.db_path,
            dry_run=args.dry_run,
            force=args.force,
            snapshot_path=args.snapshot,
        )
    except LegacyMigrationError as exc:
        print(f"MIGRATION BLOCKED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    if args.dry_run:
        print(f"Dry run complete. {count} legacy event(s) inspected.")


if __name__ == "__main__":
    main()
