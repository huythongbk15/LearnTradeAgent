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

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
import hashlib
import json
import sqlite3
import uuid

from trading_agent.execution.lifecycle.events import (
    EVENT_SCHEMA_VERSION,
    ExecutionEvent,
)

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
    UNIQUE (aggregate_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_exec_agg_seq
    ON execution_events (aggregate_id, seq);
CREATE INDEX IF NOT EXISTS idx_exec_event_type
    ON execution_events (event_type);

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
"""


class SequenceGapError(RuntimeError):
    """Raised when appending an event whose seq is not max_seq + 1."""


class SnapshotIntegrityError(RuntimeError):
    """Raised when a snapshot is corrupt, partial or schema-incompatible."""


@dataclass(frozen=True)
class Snapshot:
    aggregate_id: str
    schema_version: int
    state_version: int
    last_seq: int
    state: dict[str, Any]
    checksum: str
    created_at: datetime


def snapshot_checksum(state: dict[str, Any]) -> str:
    """Deterministic checksum over the snapshot state."""
    blob = json.dumps(state, default=str, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ExecutionEventStore:
    """Append-only SQLite store for execution lifecycle events."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None

    # ── Connection ──────────────────────────────────────────────────────

    def connect(self) -> "ExecutionEventStore":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_SCHEMA)
        conn.commit()
        self._conn = conn
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

    def append(
        self,
        event: ExecutionEvent,
        *,
        expect_seq: bool = True,
    ) -> bool:
        """Append one event.

        Returns True if inserted, False if the event id was already present
        (idempotent duplicate handling).

        With ``expect_seq=True`` (default) a seq != max_seq+1 raises
        :class:`SequenceGapError` — no partial state is ever written.
        """
        # Idempotency first: an already-persisted event_id is a no-op
        # regardless of seq (e.g. a WS duplicate replayed after a restart).
        existing = self.conn.execute(
            "SELECT 1 FROM execution_events WHERE event_id = ?",
            (event.event_id,),
        ).fetchone()
        if existing is not None:
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
        try:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO execution_events
                (event_id, seq, aggregate_id, event_type, schema_version,
                 payload, correlation_id, causation_id, occurred_at, ingested_at)
                VALUES
                (:event_id, :seq, :aggregate_id, :event_type, :schema_version,
                 :payload, :correlation_id, :causation_id, :occurred_at, :ingested_at)
                """,
                row,
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            # UNIQUE(aggregate_id, seq) conflict — duplicate seq for aggregate.
            raise SequenceGapError(
                f"aggregate {event.aggregate_id}: duplicate seq {event.seq}"
            ) from exc
        return cur.rowcount == 1

    def append_batch(
        self,
        events: list[ExecutionEvent],
        *,
        expect_seq: bool = True,
    ) -> list[bool]:
        """Append events atomically in one transaction."""
        if not events:
            return []
        if expect_seq:
            for event in events:
                expected = self.max_seq(event.aggregate_id) + 1
                if event.seq != expected:
                    raise SequenceGapError(
                        f"aggregate {event.aggregate_id}: expected seq {expected}, "
                        f"got {event.seq}"
                    )
        results: list[bool] = []
        try:
            with self.conn:
                for event in events:
                    row = event.to_row()
                    row["ingested_at"] = datetime.now(UTC).isoformat()
                    cur = self.conn.execute(
                        """
                        INSERT OR IGNORE INTO execution_events
                        (event_id, seq, aggregate_id, event_type, schema_version,
                         payload, correlation_id, causation_id, occurred_at,
                         ingested_at)
                        VALUES
                        (:event_id, :seq, :aggregate_id, :event_type,
                         :schema_version, :payload, :correlation_id,
                         :causation_id, :occurred_at, :ingested_at)
                        """,
                        row,
                    )
                    results.append(cur.rowcount == 1)
        except sqlite3.IntegrityError as exc:
            raise SequenceGapError("duplicate seq in batch") from exc
        return results

    # ── Read / replay ───────────────────────────────────────────────────

    def read_events(
        self,
        aggregate_id: str | None = None,
        *,
        after_seq: int = 0,
        limit: int | None = None,
    ) -> list[ExecutionEvent]:
        """Read events ordered by seq (deterministic replay order)."""
        sql = (
            "SELECT event_id, seq, aggregate_id, event_type, schema_version,"
            " payload, correlation_id, causation_id, occurred_at"
            " FROM execution_events"
        )
        params: list[Any] = []
        clauses: list[str] = []
        if aggregate_id is not None:
            clauses.append("aggregate_id = ?")
            params.append(aggregate_id)
        if after_seq:
            clauses.append("seq > ?")
            params.append(after_seq)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY aggregate_id, seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return [ExecutionEvent.from_row(dict(r)) for r in rows]

    def max_seq(self, aggregate_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(seq) AS m FROM execution_events WHERE aggregate_id = ?",
            (aggregate_id,),
        ).fetchone()
        return int(row["m"]) if row and row["m"] is not None else 0

    def count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS c FROM execution_events").fetchone()
        return int(row["c"])

    def aggregates(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT DISTINCT aggregate_id FROM execution_events ORDER BY aggregate_id"
        ).fetchall()
        return [r["aggregate_id"] for r in rows]

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

    def save_snapshot(
        self,
        aggregate_id: str,
        state: dict[str, Any],
        *,
        state_version: int,
        last_seq: int,
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
                last_seq,
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
            last_seq=last_seq,
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
            last_seq=int(row["last_seq"]),
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
    "Snapshot",
    "snapshot_checksum",
]
