"""SQLite append-only store for execution lifecycle events.

Wave C — audit durability.

Design (kept deliberately simple, per spec: "Không cần Kafka nếu SQLite
append-only table đủ. Ưu tiên đơn giản."):

* ``execution_events`` — append-only log.
    - ``event_id`` UNIQUE → idempotent appends (INSERT OR IGNORE).
    - ``(aggregate_id, seq)`` UNIQUE → sequence validation.
* ``execution_snapshots`` — durable snapshot + restore.
    - schema_version, state_version, last_seq, checksum → corrupt /
      partial / old-schema snapshots are rejected, never silently loaded.

Crash safety:
* WAL journal + ``synchronous=FULL`` → a crash cannot tear an append.
* ``append`` is a single-statement transaction; a process killed between
  event creation and append simply means the event is absent from the log —
  deterministic replay then sees exactly what was persisted (crash recovery
  is handled at the lifecycle level).
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from trading_agent.execution.lifecycle.events import (
    EVENT_SCHEMA_VERSION,
    ExecutionEvent,
    ExecutionEventType,
    UnknownEventTypeError,
)

CUTOVER_MIGRATION_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS execution_events (
    event_id        TEXT PRIMARY KEY,
    seq             INTEGER NOT NULL,
    aggregate_id    TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    payload         TEXT NOT NULL,
    correlation_id  TEXT,
    causation_id    TEXT,
    occurred_at     TEXT NOT NULL,
    ingested_at     TEXT NOT NULL,
    global_seq      INTEGER NOT NULL CHECK (global_seq > 0 OR global_seq = -1),
    UNIQUE (aggregate_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_exec_agg_seq
    ON execution_events (aggregate_id, seq);
CREATE INDEX IF NOT EXISTS idx_exec_event_type
    ON execution_events (event_type);
CREATE INDEX IF NOT EXISTS idx_exec_global_seq
    ON execution_events (global_seq);
CREATE UNIQUE INDEX IF NOT EXISTS idx_exec_global_seq_positive
    ON execution_events (global_seq)
    WHERE global_seq > 0;

CREATE TABLE IF NOT EXISTS execution_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    aggregate_id    TEXT NOT NULL,
    schema_version  INTEGER NOT NULL,
    state_version   INTEGER NOT NULL,
    last_seq        INTEGER NOT NULL,
    state_json      TEXT NOT NULL,
    checksum        TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE (aggregate_id)
);

CREATE TABLE IF NOT EXISTS execution_cutover_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    aggregate_id    TEXT NOT NULL UNIQUE CHECK (aggregate_id = 'global'),
    schema_version  INTEGER NOT NULL,
    state_version   INTEGER NOT NULL,
    last_seq        INTEGER NOT NULL CHECK (last_seq = 0),
    state_json      TEXT NOT NULL,
    checksum        TEXT NOT NULL,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_migration_state (
    migration_id         TEXT PRIMARY KEY,
    migration_version    INTEGER NOT NULL,
    snapshot_id          TEXT NOT NULL UNIQUE,
    snapshot_checksum    TEXT NOT NULL,
    source_event_count   INTEGER NOT NULL,
    source_event_checksum TEXT NOT NULL,
    legacy_event_count   INTEGER NOT NULL,
    cutover_at           TEXT NOT NULL,
    first_post_cutover_global_seq INTEGER NOT NULL CHECK (
        first_post_cutover_global_seq = 1
    ),
    schema_version       INTEGER NOT NULL,
    state_version        INTEGER NOT NULL,
    source_provenance    TEXT NOT NULL,
    source_db_fingerprint TEXT,
    created_at           TEXT NOT NULL,
    FOREIGN KEY (snapshot_id)
        REFERENCES execution_cutover_snapshots(snapshot_id)
);

CREATE TABLE IF NOT EXISTS execution_sell_reservations (
    intent_id            TEXT PRIMARY KEY,
    symbol               TEXT NOT NULL,
    authorized_quantity  REAL NOT NULL,
    reserved_quantity    REAL NOT NULL,
    filled_quantity      REAL NOT NULL DEFAULT 0,
    released_quantity    REAL NOT NULL DEFAULT 0,
    status               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exec_sell_reservation_symbol
    ON execution_sell_reservations (symbol, status);

CREATE TABLE IF NOT EXISTS execution_order_intents (
    intent_id        TEXT PRIMARY KEY,
    idempotency_key  TEXT NOT NULL UNIQUE,
    symbol           TEXT NOT NULL,
    side             TEXT NOT NULL,
    size             REAL NOT NULL,
    status           TEXT NOT NULL,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_exec_order_intent_idem
    ON execution_order_intents (idempotency_key);

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


class SequenceGapError(RuntimeError):
    """Raised when appending an event whose seq is not max_seq + 1."""


class SnapshotIntegrityError(RuntimeError):
    """Raised when a snapshot is corrupt, partial or schema-incompatible."""


class LegacyMigrationError(SnapshotIntegrityError):
    """Raised when legacy history cannot be migrated without guessing truth."""


class LegacyCutoverStateRequired(LegacyMigrationError):
    """Raised when unordered legacy history has no verified cutover authority."""


class ReservationConflictError(RuntimeError):
    """Raised when concurrent SELL locks exceed authorized inventory."""


@dataclass(frozen=True)
class Snapshot:
    aggregate_id: str
    schema_version: int
    state_version: int
    last_global_seq: int
    state: dict[str, Any]
    checksum: str
    created_at: datetime


@dataclass(frozen=True)
class CutoverMetadata:
    migration_id: str
    migration_version: int
    snapshot_id: str
    snapshot_checksum: str
    source_event_count: int
    source_event_checksum: str
    legacy_event_count: int
    cutover_at: datetime
    first_post_cutover_global_seq: int
    schema_version: int
    state_version: int
    source_provenance: str
    source_db_fingerprint: str | None


def snapshot_checksum(state: dict[str, Any]) -> str:
    """Deterministic checksum over the snapshot state."""
    blob = json.dumps(state, default=str, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


_SOURCE_EVENT_FIELDS = (
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
)


def source_event_checksum(rows: Sequence[Mapping[str, Any] | sqlite3.Row]) -> str:
    """Bind migration metadata to immutable legacy rows without inventing order."""
    normalized = [{field: row[field] for field in _SOURCE_EVENT_FIELDS} for row in rows]
    normalized.sort(key=lambda item: str(item["event_id"]))
    blob = json.dumps(normalized, default=str, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ExecutionEventStore:
    """Append-only SQLite store for execution lifecycle events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # ── Connection ──────────────────────────────────────────────────────

    def connect(self) -> "ExecutionEventStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.commit()
        positive_index = next(
            (
                row
                for row in conn.execute("PRAGMA index_list(execution_events)")
                if row["name"] == "idx_exec_global_seq_positive"
            ),
            None,
        )
        if (
            positive_index is None
            or not bool(positive_index["unique"])
            or not bool(positive_index["partial"])
        ):
            conn.close()
            raise SnapshotIntegrityError(
                "positive global_seq must have a unique partial DB index"
            )
        self._conn = conn
        # Legacy cutover is never automatic. The dedicated migration command
        # validates authority and commits snapshot + marker + row marking as
        # one transaction before append/recovery will accept legacy rows.
        return self

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "ExecutionEventStore":
        return self.connect()

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("store not connected")
        return self._conn

    # ── Append ──────────────────────────────────────────────────────────

    def _rebuild_sell_reservations_if_needed(self) -> None:
        count = self.conn.execute(
            "SELECT COUNT(*) AS c FROM execution_sell_reservations"
        ).fetchone()["c"]
        if count:
            return
        events = self.read_events()
        if not any(
            event.event_type == ExecutionEventType.ORDER_SUBMITTED
            and event.payload.get("side") == "sell"
            for event in events
        ):
            return
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            for event in events:
                self._apply_sell_reservation_projection(event)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _apply_sell_reservation_projection(self, event: ExecutionEvent) -> None:
        """Update the rebuildable SELL-lock projection inside the event tx."""
        payload = event.payload
        event_type = event.event_type
        if (
            event_type == ExecutionEventType.ORDER_SUBMITTED
            and str(payload.get("side", "")).lower() == "sell"
        ):
            intent_id = event.aggregate_id
            # Skip reservation for protective stop orders (intent_id starts with "prot_")
            # Protective stops don't consume sellable inventory - they are safety nets
            # that trigger only on adverse price movement.
            if intent_id.startswith("prot_"):
                return
            symbol = str(payload.get("symbol") or "")
            authorized = float(payload.get("authorized_quantity", 0.0))
            reserved = float(payload.get("reserved_quantity", 0.0))
            if (
                not symbol
                or not math.isfinite(authorized)
                or not math.isfinite(reserved)
                or authorized < 0
                or reserved <= 0
            ):
                raise ReservationConflictError("invalid SELL reservation evidence")
            row = self.conn.execute(
                """
                SELECT COALESCE(SUM(
                    reserved_quantity - filled_quantity - released_quantity
                ), 0) AS active
                FROM execution_sell_reservations
                WHERE symbol = ? AND status = 'active'
                """,
                (symbol,),
            ).fetchone()
            active = float(row["active"] if row is not None else 0.0)
            if active + reserved > authorized + 1e-9:
                raise ReservationConflictError(
                    f"active SELL reservations {active + reserved} exceed "
                    f"authorized inventory {authorized} for {symbol}"
                )
            self.conn.execute(
                """
                INSERT INTO execution_sell_reservations
                (intent_id, symbol, authorized_quantity, reserved_quantity,
                 filled_quantity, released_quantity, status)
                VALUES (?, ?, ?, ?, 0, 0, 'active')
                """,
                (event.aggregate_id, symbol, authorized, reserved),
            )
            return

        if event_type in {
            ExecutionEventType.PARTIAL_FILL_RECEIVED,
            ExecutionEventType.FILL_RECEIVED,
        }:
            row = self.conn.execute(
                """
                SELECT reserved_quantity, filled_quantity
                FROM execution_sell_reservations WHERE intent_id = ?
                """,
                (event.aggregate_id,),
            ).fetchone()
            if row is None:
                return
            filled = float(row["filled_quantity"]) + float(payload["size"])
            reserved = float(row["reserved_quantity"])
            if filled > reserved + 1e-9:
                raise ReservationConflictError(
                    f"SELL fills {filled} exceed reservation {reserved}"
                )
            self.conn.execute(
                """
                UPDATE execution_sell_reservations
                SET filled_quantity = ?, status = ? WHERE intent_id = ?
                """,
                (
                    filled,
                    "filled" if filled >= reserved - 1e-9 else "active",
                    event.aggregate_id,
                ),
            )
            return

        if event_type in {
            ExecutionEventType.CANCEL_CONFIRMED,
            ExecutionEventType.ORDER_REJECTED,
        }:
            self.conn.execute(
                """
                UPDATE execution_sell_reservations
                SET released_quantity = MAX(
                        0, reserved_quantity - filled_quantity
                    ),
                    status = ?
                WHERE intent_id = ?
                """,
                (
                    "canceled"
                    if event_type == ExecutionEventType.CANCEL_CONFIRMED
                    else "rejected",
                    event.aggregate_id,
                ),
            )

    def active_sell_reservations(self, symbol: str) -> float:
        row = self.conn.execute(
            """
            SELECT COALESCE(SUM(
                reserved_quantity - filled_quantity - released_quantity
            ), 0) AS active
            FROM execution_sell_reservations
            WHERE symbol = ? AND status = 'active'
            """,
            (symbol,),
        ).fetchone()
        return float(row["active"] if row is not None else 0.0)

    def claim_submission(
        self,
        *,
        intent_id: str,
        claimed_by: str,
        idempotency_key: str,
        payload_hash: str,
        now: str | None = None,
    ) -> bool:
        """Atomically claim a submission intent.

        Returns True if the claim succeeded. Returns False if another
        connection already claimed this intent.
        """
        now = now or datetime.now(UTC).isoformat()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            existing = self.conn.execute(
                "SELECT 1 FROM execution_submission_claims WHERE intent_id = ?",
                (intent_id,),
            ).fetchone()
            if existing is not None:
                self.conn.rollback()
                return False
            self.conn.execute(
                """
                INSERT INTO execution_submission_claims
                    (intent_id, claimed_by, claimed_at, idempotency_key, payload_hash, status)
                VALUES (?, ?, ?, ?, ?, 'claimed')
                """,
                (intent_id, claimed_by, now, idempotency_key, payload_hash),
            )
            self.conn.commit()
            return True
        except Exception:
            self.conn.rollback()
            raise

    def release_submission_claim(self, intent_id: str) -> None:
        """Release a submission claim after completion or cancellation."""
        self.conn.execute(
            "DELETE FROM execution_submission_claims WHERE intent_id = ?",
            (intent_id,),
        )
        self.conn.commit()

    def submission_claim(self, intent_id: str) -> dict | None:
        """Return the current claim for an intent, or None."""
        row = self.conn.execute(
            "SELECT * FROM execution_submission_claims WHERE intent_id = ?",
            (intent_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def _assert_append_ready(self) -> None:
        self.validate_global_sequence()
        uncut = self.conn.execute(
            "SELECT COUNT(*) AS c FROM execution_events WHERE global_seq = 0"
        ).fetchone()["c"]
        if uncut:
            raise LegacyCutoverStateRequired(
                "legacy execution history has no trustworthy global ordering and "
                "has not completed verified cutover; refusing append"
            )
        legacy = self.conn.execute(
            "SELECT COUNT(*) AS c FROM execution_events WHERE global_seq = -1"
        ).fetchone()["c"]
        if legacy:
            self.validate_cutover_authority()

    def append(
        self,
        event: ExecutionEvent,
        *,
        expect_seq: bool = True,
    ) -> bool:
        """Append one event and its reservation projection atomically."""
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self._assert_append_ready()
            existing = self.conn.execute(
                "SELECT 1 FROM execution_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing is not None:
                self.conn.rollback()
                return False
            if expect_seq:
                expected = self.max_seq(event.aggregate_id) + 1
                if event.seq != expected:
                    raise SequenceGapError(
                        f"aggregate {event.aggregate_id}: expected seq {expected}, "
                        f"got {event.seq}"
                    )
            row = event.to_row()
            row["ingested_at"] = datetime.now(UTC).isoformat()
            max_global = self.conn.execute(
                "SELECT COALESCE(MAX(CASE WHEN global_seq > 0 "
                "THEN global_seq END), 0) FROM execution_events"
            ).fetchone()[0]
            row["global_seq"] = int(max_global) + 1
            self.conn.execute(
                """
                INSERT INTO execution_events
                (event_id, seq, aggregate_id, event_type, schema_version,
                 payload, correlation_id, causation_id, occurred_at, ingested_at,
                 global_seq)
                VALUES
                (:event_id, :seq, :aggregate_id, :event_type, :schema_version,
                 :payload, :correlation_id, :causation_id, :occurred_at, :ingested_at,
                 :global_seq)
                """,
                row,
            )
            self._apply_sell_reservation_projection(event)
            self.conn.commit()
            return True
        except ReservationConflictError:
            self.conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            raise SequenceGapError(
                f"aggregate {event.aggregate_id}: duplicate seq {event.seq}"
            ) from exc
        except Exception:
            self.conn.rollback()
            raise

    def append_batch(
        self,
        events: list[ExecutionEvent],
        *,
        expect_seq: bool = True,
    ) -> list[bool]:
        """Append events atomically in one transaction with BEGIN IMMEDIATE."""
        if not events:
            return []
        results: list[bool] = []
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            self._assert_append_ready()
            if expect_seq:
                # Validate under the same writer lock used for allocation.
                expected_by_aggregate: dict[str, int] = {}
                for event in events:
                    agg = event.aggregate_id
                    if agg not in expected_by_aggregate:
                        expected_by_aggregate[agg] = self.max_seq(agg) + 1
                    if event.seq != expected_by_aggregate[agg]:
                        raise SequenceGapError(
                            f"aggregate {agg}: expected seq "
                            f"{expected_by_aggregate[agg]}, got {event.seq}"
                        )
                    expected_by_aggregate[agg] += 1
            # Pre-allocate global_seq for the entire batch to ensure
            # strict monotonicity within the transaction.
            max_global = self.conn.execute(
                "SELECT COALESCE(MAX(CASE WHEN global_seq > 0 "
                "THEN global_seq END), 0) FROM execution_events"
            ).fetchone()[0]
            global_seq_counter = int(max_global)
            for idx, event in enumerate(events):
                next_global_seq = global_seq_counter + 1
                row = event.to_row()
                row["ingested_at"] = datetime.now(UTC).isoformat()
                row["global_seq"] = next_global_seq
                cur = self.conn.execute(
                    """
                    INSERT OR IGNORE INTO execution_events
                    (event_id, seq, aggregate_id, event_type, schema_version,
                     payload, correlation_id, causation_id, occurred_at,
                     ingested_at, global_seq)
                    VALUES
                    (:event_id, :seq, :aggregate_id, :event_type,
                     :schema_version, :payload, :correlation_id,
                     :causation_id, :occurred_at, :ingested_at,
                     :global_seq)
                    """,
                    row,
                )
                inserted = cur.rowcount == 1
                if inserted:
                    global_seq_counter = next_global_seq
                    self._apply_sell_reservation_projection(event)
                results.append(inserted)
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            self.conn.rollback()
            raise SequenceGapError("duplicate seq in batch") from exc
        except Exception:
            self.conn.rollback()
            raise
        return results

    # ── Read / replay ───────────────────────────────────────────────────

    def read_events(
        self,
        aggregate_id: str | None = None,
        *,
        after_seq: int = 0,
        limit: int | None = None,
        order_by_global: bool = False,
    ) -> list[ExecutionEvent]:
        """Read events ordered by seq (or global_seq for cross-aggregate replay)."""
        sql = (
            "SELECT event_id, seq, aggregate_id, event_type, schema_version,"
            " payload, correlation_id, causation_id, occurred_at, ingested_at, global_seq"
            " FROM execution_events"
        )
        params: list[Any] = []
        clauses: list[str] = []
        if aggregate_id is not None:
            clauses.append("aggregate_id = ?")
            params.append(aggregate_id)
        if order_by_global:
            # Global recovery never replays unordered legacy rows (-1/0).
            clauses.append("global_seq > ?")
            params.append(after_seq)
        elif after_seq:
            if not order_by_global:
                clauses.append("seq > ?")
                params.append(after_seq)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        if order_by_global:
            sql += " ORDER BY global_seq ASC"
        else:
            sql += " ORDER BY aggregate_id, seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        events: list[ExecutionEvent] = []
        for r in rows:
            try:
                events.append(ExecutionEvent.from_row(dict(r)))
            except UnknownEventTypeError as exc:
                # Fail closed: unknown event_type indicates corrupted or
                # incompatible data.  Do NOT silently skip.
                raise RuntimeError(
                    f"unknown event_type in execution_events: {exc}"
                ) from exc
        return events

    def read_events_global(self, *, after_global_seq: int = 0) -> list[ExecutionEvent]:
        """Read all events ordered by global_seq (cross-aggregate replay)."""
        return self.read_events(after_seq=after_global_seq, order_by_global=True)

    def max_seq(self, aggregate_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(seq) AS m FROM execution_events WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def max_global_seq(self) -> int:
        row = self.conn.execute(
            "SELECT MAX(CASE WHEN global_seq > 0 THEN global_seq END) AS m "
            "FROM execution_events"
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def validate_global_sequence(self) -> None:
        """Reject gaps or a non-one post-cutover sequence origin."""
        row = self.conn.execute(
            "SELECT COUNT(*) AS c, MIN(global_seq) AS lo, MAX(global_seq) AS hi "
            "FROM execution_events WHERE global_seq > 0"
        ).fetchone()
        count = int(row["c"])
        if count == 0:
            return
        minimum = int(row["lo"])
        maximum = int(row["hi"])
        if minimum != 1 or count != maximum:
            raise SnapshotIntegrityError(
                "post-cutover global_seq must be contiguous from 1"
            )

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM execution_events").fetchone()
        return int(row["c"])

    def aggregates(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT aggregate_id FROM execution_events ORDER BY aggregate_id"
        ).fetchall()
        return [r["aggregate_id"] for r in rows]

    # ── Durable idempotency registry ────────────────────────────────────

    def upsert_order_intent(
        self,
        intent_id: str,
        idempotency_key: str,
        symbol: str,
        side: str,
        size: float,
        status: str = "PENDING",
    ) -> str:
        """Insert a new order intent or return existing intent_id atomically.

        This is the durable idempotency boundary for order creation.
        If the idempotency_key already exists, returns the existing intent_id.
        Otherwise, inserts a new row and returns the provided intent_id.
        """
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                "SELECT intent_id FROM execution_order_intents WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if row is not None:
                self.conn.commit()
                return row["intent_id"]
            self.conn.execute(
                """
                INSERT INTO execution_order_intents
                (intent_id, idempotency_key, symbol, side, size, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    idempotency_key,
                    symbol,
                    side,
                    size,
                    status,
                    datetime.now(UTC).isoformat(),
                ),
            )
            self.conn.commit()
            return intent_id
        except Exception:
            self.conn.rollback()
            raise

    def get_intent_by_idempotency_key(self, idempotency_key: str) -> str | None:
        """Return intent_id for a given idempotency_key, or None."""
        row = self.conn.execute(
            "SELECT intent_id FROM execution_order_intents WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        return row["intent_id"] if row else None

    def get_latest_authorization(self, intent_id: str) -> dict[str, Any] | None:
        """Return the latest ORDER_AUTHORIZED event payload for an intent, or None."""
        row = self.conn.execute(
            """
            SELECT payload FROM execution_events
            WHERE aggregate_id = ? AND event_type = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (intent_id, "exec.order_authorized"),
        ).fetchone()
        if row is None:
            return None
        import json

        return json.loads(row["payload"])

    def get_latest_authorization_by_auth_id(
        self, authorization_id: str
    ) -> dict[str, Any] | None:
        """Return the latest ORDER_AUTHORIZED event payload by authorization_id, or None."""
        row = self.conn.execute(
            """
            SELECT payload FROM execution_events
            WHERE event_type = ? AND json_extract(payload, '$.authorization_id') = ?
            ORDER BY seq DESC LIMIT 1
            """,
            ("exec.order_authorized", authorization_id),
        ).fetchone()
        if row is None:
            return None
        import json

        return json.loads(row["payload"])

    def get_latest_submission_request(self, intent_id: str) -> dict[str, Any] | None:
        """Return the latest BROKER_SUBMISSION_REQUESTED event payload for an intent, or None."""
        row = self.conn.execute(
            """
            SELECT payload FROM execution_events
            WHERE aggregate_id = ? AND event_type = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (intent_id, "exec.broker_submission_requested"),
        ).fetchone()
        if row is None:
            return None
        import json

        return json.loads(row["payload"])

    def get_latest_broker_event(
        self, intent_id: str
    ) -> tuple[str, dict[str, Any]] | None:
        """Return the latest broker outcome event type and payload for an intent.

        Scans in order: BROKER_ACKNOWLEDGED, ORDER_REJECTED,
        BROKER_STATE_UNKNOWN, LOCAL_SUBMISSION_FAILED, PARTIAL_FILL_RECEIVED,
        FILL_RECEIVED.
        """
        event_types = [
            "exec.broker_acknowledged",
            "exec.order_rejected",
            "exec.broker_state_unknown",
            "exec.local_submission_failed",
            "exec.partial_fill_received",
            "exec.fill_received",
        ]
        for etype in event_types:
            row = self.conn.execute(
                """
                SELECT payload FROM execution_events
                WHERE aggregate_id = ? AND event_type = ?
                ORDER BY seq DESC LIMIT 1
                """,
                (intent_id, etype),
            ).fetchone()
            if row is not None:
                import json

                return etype, json.loads(row["payload"])
        return None

    # ── Integrity / audit ───────────────────────────────────────────────

    def integrity_check(self) -> dict[str, Any]:
        """Audit the log: seq gaps, duplicate ids, schema versions."""
        gaps: list[dict[str, Any]] = []
        duplicates: list[str] = []
        bad_schema: list[str] = []
        for aggregate_id in self.aggregates():
            events = self.read_events(aggregate_id)
            seqs = [e.seq for e in events]
            for i, seq in enumerate(seqs):
                if i and seq != seqs[i - 1] + 1:
                    gaps.append(
                        {
                            "aggregate_id": aggregate_id,
                            "gap_after": seqs[i - 1],
                            "seq": seq,
                        }
                    )
            ids = [e.event_id for e in events]
            if len(ids) != len(set(ids)):
                duplicates.append(aggregate_id)
            if any(e.schema_version != EVENT_SCHEMA_VERSION for e in events):
                bad_schema.append(aggregate_id)
        return {
            "ok": not gaps and not duplicates and not bad_schema,
            "gaps": gaps,
            "duplicate_event_ids": duplicates,
            "schema_mismatch": bad_schema,
            "total_events": self.count(),
        }

    # ── Durable snapshot + restore ──────────────────────────────────────

    def _table_columns(self, table: str) -> set[str]:
        return {
            str(row["name"])
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }

    def load_cutover_metadata(self) -> CutoverMetadata | None:
        """Load the single migration marker, rejecting old/partial schemas."""
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
        columns = self._table_columns("execution_migration_state")
        if not columns:
            return None
        missing = expected - columns
        if missing:
            raise SnapshotIntegrityError(
                "legacy cutover metadata uses an incompatible schema; missing "
                + ", ".join(sorted(missing))
            )
        rows = self.conn.execute(
            "SELECT * FROM execution_migration_state ORDER BY created_at"
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise SnapshotIntegrityError(
                "multiple cutover markers found; refusing ambiguous recovery"
            )
        row = rows[0]
        return CutoverMetadata(
            migration_id=str(row["migration_id"]),
            migration_version=int(row["migration_version"]),
            snapshot_id=str(row["snapshot_id"]),
            snapshot_checksum=str(row["snapshot_checksum"]),
            source_event_count=int(row["source_event_count"]),
            source_event_checksum=str(row["source_event_checksum"]),
            legacy_event_count=int(row["legacy_event_count"]),
            cutover_at=datetime.fromisoformat(str(row["cutover_at"])),
            first_post_cutover_global_seq=int(row["first_post_cutover_global_seq"]),
            schema_version=int(row["schema_version"]),
            state_version=int(row["state_version"]),
            source_provenance=str(row["source_provenance"]),
            source_db_fingerprint=row["source_db_fingerprint"],
        )

    def load_cutover_snapshot(self) -> Snapshot | None:
        """Load the immutable snapshot that established legacy cutover truth."""
        columns = self._table_columns("execution_cutover_snapshots")
        if not columns:
            return None
        rows = self.conn.execute(
            "SELECT * FROM execution_cutover_snapshots WHERE aggregate_id = 'global'"
        ).fetchall()
        if not rows:
            return None
        if len(rows) != 1:
            raise SnapshotIntegrityError("multiple global cutover snapshots found")
        row = rows[0]
        try:
            state = json.loads(row["state_json"])
        except json.JSONDecodeError as exc:
            raise SnapshotIntegrityError(
                "cutover snapshot contains unreadable state_json"
            ) from exc
        checksum = snapshot_checksum(state)
        if checksum != row["checksum"]:
            raise SnapshotIntegrityError("cutover snapshot checksum mismatch")
        if int(row["schema_version"]) != EVENT_SCHEMA_VERSION:
            raise SnapshotIntegrityError("cutover snapshot schema is incompatible")
        if int(row["last_seq"]) != 0:
            raise SnapshotIntegrityError(
                "cutover snapshot must precede global_seq allocation at zero"
            )
        return Snapshot(
            aggregate_id="global",
            schema_version=int(row["schema_version"]),
            state_version=int(row["state_version"]),
            last_global_seq=0,
            state=state,
            checksum=str(row["checksum"]),
            created_at=datetime.fromisoformat(str(row["created_at"])),
        )

    def validate_cutover_authority(self) -> Snapshot | None:
        """Prove that every unordered legacy row is bound to cutover truth."""
        uncut = self.conn.execute(
            "SELECT COUNT(*) AS c FROM execution_events WHERE global_seq = 0"
        ).fetchone()["c"]
        if uncut:
            raise LegacyCutoverStateRequired(
                "legacy execution history has no trustworthy global ordering and "
                "has not completed verified cutover"
            )
        legacy_rows = self.conn.execute(
            "SELECT * FROM execution_events WHERE global_seq = -1"
        ).fetchall()
        if not legacy_rows:
            return None
        for row in legacy_rows:
            try:
                event = ExecutionEvent.from_row(dict(row))
                if event.schema_version != EVENT_SCHEMA_VERSION:
                    raise ValueError("unsupported legacy event schema")
            except (UnknownEventTypeError, KeyError, TypeError, ValueError) as exc:
                raise SnapshotIntegrityError(
                    "unknown or malformed legacy execution event blocks recovery"
                ) from exc
        metadata = self.load_cutover_metadata()
        snapshot = self.load_cutover_snapshot()
        if metadata is None or snapshot is None:
            raise LegacyCutoverStateRequired(
                "pre-cutover events exist without a verified snapshot and marker"
            )
        if metadata.migration_version != CUTOVER_MIGRATION_VERSION:
            raise SnapshotIntegrityError("unsupported legacy cutover migration version")
        if (
            metadata.snapshot_id
            != self.conn.execute(
                "SELECT snapshot_id FROM execution_cutover_snapshots "
                "WHERE aggregate_id = 'global'"
            ).fetchone()["snapshot_id"]
        ):
            raise SnapshotIntegrityError("cutover marker references another snapshot")
        if metadata.snapshot_checksum != snapshot.checksum:
            raise SnapshotIntegrityError(
                "cutover marker checksum does not bind snapshot"
            )
        if metadata.schema_version != snapshot.schema_version:
            raise SnapshotIntegrityError("cutover marker schema version mismatch")
        if metadata.state_version != snapshot.state_version:
            raise SnapshotIntegrityError("cutover marker state version mismatch")
        if metadata.first_post_cutover_global_seq != 1:
            raise SnapshotIntegrityError("invalid first post-cutover global sequence")
        if metadata.legacy_event_count != len(legacy_rows):
            raise SnapshotIntegrityError("legacy event count changed after cutover")
        if metadata.source_event_count != len(legacy_rows):
            raise SnapshotIntegrityError("cutover source event count mismatch")
        if metadata.source_event_checksum != source_event_checksum(legacy_rows):
            raise SnapshotIntegrityError("legacy event checksum changed after cutover")
        return snapshot

    def save_snapshot(
        self,
        aggregate_id: str,
        state: dict[str, Any],
        *,
        state_version: int,
        last_global_seq: int,
        schema_version: int = EVENT_SCHEMA_VERSION,
    ) -> Snapshot:
        """Persist a snapshot with integrity metadata."""
        checksum = snapshot_checksum(state)
        snapshot_id = str(uuid.uuid4())
        self.conn.execute(
            """
            INSERT OR REPLACE INTO execution_snapshots
            (snapshot_id, aggregate_id, schema_version, state_version, last_seq,
             state_json, checksum, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                aggregate_id,
                schema_version,
                state_version,
                last_global_seq,
                json.dumps(state, default=str, sort_keys=True),
                checksum,
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()
        return Snapshot(
            aggregate_id=aggregate_id,
            schema_version=schema_version,
            state_version=state_version,
            last_global_seq=last_global_seq,
            state=dict(state),
            checksum=checksum,
            created_at=datetime.now(UTC),
        )

    def load_snapshot(self, aggregate_id: str) -> Snapshot | None:
        """Load a validated snapshot.

        Rejects (raises :class:`SnapshotIntegrityError`) on:
        * checksum mismatch (corruption);
        * schema version mismatch (old/incompatible snapshot).
        """
        row = self.conn.execute(
            "SELECT * FROM execution_snapshots WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        if row is None:
            return None
        state_json = row["state_json"]
        try:
            state = json.loads(state_json)
        except json.JSONDecodeError as exc:
            raise SnapshotIntegrityError(
                f"snapshot {aggregate_id}: unreadable state_json (partial write?)"
            ) from exc
        checksum = snapshot_checksum(state)
        if checksum != row["checksum"]:
            raise SnapshotIntegrityError(
                f"snapshot {aggregate_id}: checksum mismatch — corrupt snapshot"
            )
        if int(row["schema_version"]) != EVENT_SCHEMA_VERSION:
            raise SnapshotIntegrityError(
                f"snapshot {aggregate_id}: schema version {row['schema_version']} "
                f"incompatible with {EVENT_SCHEMA_VERSION} — refusing unsafe load"
            )
        return Snapshot(
            aggregate_id=aggregate_id,
            schema_version=int(row["schema_version"]),
            state_version=int(row["state_version"]),
            last_global_seq=int(row["last_seq"]),
            state=state,
            checksum=row["checksum"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def delete_snapshot(self, aggregate_id: str) -> None:
        self.conn.execute(
            "DELETE FROM execution_snapshots WHERE aggregate_id = ?",
            (aggregate_id,),
        )
        self.conn.commit()


__all__ = [
    "ExecutionEventStore",
    "SequenceGapError",
    "SnapshotIntegrityError",
    "LegacyMigrationError",
    "LegacyCutoverStateRequired",
    "Snapshot",
    "CutoverMetadata",
    "CUTOVER_MIGRATION_VERSION",
    "snapshot_checksum",
    "source_event_checksum",
]
