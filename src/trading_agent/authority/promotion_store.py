"""Authoritative promotion state store — single source of truth for promotion stage.

Replaces artifact.metadata["promotion_stage"] with a durable, queryable store
keyed by artifact_id. Promotion events are append-only and content-addressed
where possible.

Environment mapping:
    RESEARCH:      EXPLORATORY or higher
    PAPER:         PAPER_ELIGIBLE or higher
    TESTNET:       TESTNET_ELIGIBLE or higher
    SHADOW:        SHADOW_ELIGIBLE or higher
    CANARY:        CANARY_ELIGIBLE / CANARY
    PRODUCTION:    not implemented (maintain NO-GO)
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from trading_agent.research.promotion import ResearchStage, ResearchPromotionEvent


def _sha256_hex(payload: str | bytes) -> str:
    import hashlib

    if isinstance(payload, str):
        payload = payload.encode()
    return hashlib.sha256(payload).hexdigest()


# ── Environment ↔ Stage mapping ────────────────────────────────────────

# Single source of truth for environment → minimum stage mapping
# Production intentionally absent — maintain NO-GO
ENV_MIN_STAGE: dict[str, ResearchStage] = {
    "research": ResearchStage.EXPLORATORY,
    "paper": ResearchStage.PAPER_ELIGIBLE,
    "testnet": ResearchStage.TESTNET_ELIGIBLE,
    "shadow": ResearchStage.SHADOW_ELIGIBLE,
    "canary": ResearchStage.CANARY_ELIGIBLE,
}

_STAGE_ORDER = (
    ResearchStage.EXPLORATORY,
    ResearchStage.RESEARCH_VALIDATED,
    ResearchStage.PAPER_ELIGIBLE,
    ResearchStage.TESTNET_ELIGIBLE,
    ResearchStage.SHADOW_ELIGIBLE,
    ResearchStage.CANARY_ELIGIBLE,
    ResearchStage.CANARY,
    ResearchStage.PRODUCTION,
)

_STAGE_RANK: dict[ResearchStage, int] = {s: i for i, s in enumerate(_STAGE_ORDER)}


def get_min_stage_for_environment(environment: str) -> ResearchStage | None:
    """Get minimum ResearchStage required for an environment.

    Returns None if environment is not recognized (e.g., 'production').
    """
    return ENV_MIN_STAGE.get(environment.lower())


def is_stage_compatible(stage: ResearchStage, environment: str) -> bool:
    """Check if a promotion stage is compatible with a runtime environment.

    Uses the single authoritative mapping from PromotionStateStore.
    """
    min_stage = get_min_stage_for_environment(environment)
    if min_stage is None:
        return False
    return _STAGE_RANK.get(stage, -1) >= _STAGE_RANK.get(min_stage, -1)


# ── Data model ─────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PromotionRecord:
    """Immutable promotion record for an artifact."""

    artifact_id: str
    stage: ResearchStage
    latest_event: ResearchPromotionEvent | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "stage": self.stage.value,
            "latest_event": self.latest_event.to_dict() if self.latest_event else None,
            "updated_at": self.updated_at.isoformat(),
        }


# ── SQLite store ───────────────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS promotion_registry (
    artifact_id     TEXT PRIMARY KEY,
    stage           TEXT NOT NULL,
    latest_event    TEXT,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_promo_stage ON promotion_registry(stage);
"""

_INSERT_SQL = """
INSERT INTO promotion_registry (artifact_id, stage, latest_event, updated_at)
VALUES (?, ?, ?, ?)
ON CONFLICT(artifact_id) DO UPDATE SET
    stage = excluded.stage,
    latest_event = excluded.latest_event,
    updated_at = excluded.updated_at;
"""

_LOOKUP_SQL = "SELECT artifact_id, stage, latest_event, updated_at FROM promotion_registry WHERE artifact_id = ?"

_STAGE_LOOKUP_SQL = "SELECT artifact_id, stage, latest_event, updated_at FROM promotion_registry WHERE stage = ?"


# One lock per resolved db path: the copy→modify→atomic-replace cycle in
# _connect() must be serialized across ALL store instances sharing that file
# (e.g. engine resolver + RuntimeLoader watcher thread), otherwise two
# interleaved connections can consume each other's shared tmp file
# (FileNotFoundError on os.replace) or a stale snapshot can overwrite a newer
# write (lost update). RLock allows same-thread reentry from nested calls.
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()


def _lock_for(db_path: Path) -> threading.RLock:
    key = str(db_path.resolve())
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
    return lock


class PromotionStateStore:
    """Durable promotion state registry.

    This is the AUTHORITATIVE source for promotion stage.
    No runtime code may treat artifact.metadata["promotion_stage"] as authority.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_lock = _lock_for(self.db_path)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        # Unique tmp per connection — a shared fixed name breaks under the
        # concurrent connections introduced by the hot-reload watcher thread.
        temp_path = self.db_path.with_suffix(f".{uuid.uuid4().hex[:12]}.tmp")
        try:
            with self._file_lock:
                if self.db_path.exists():
                    import shutil

                    shutil.copy2(self.db_path, temp_path)
                conn = sqlite3.connect(temp_path)
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA synchronous=FULL")
                conn.executescript(_SCHEMA_SQL)
                yield conn
                conn.commit()
                conn.close()
                _os_replace(temp_path, self.db_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def upsert(self, record: PromotionRecord) -> None:
        """Insert or update a promotion record."""
        latest_event_json = (
            json.dumps(record.latest_event.to_dict()) if record.latest_event else None
        )
        with self._connect() as conn:
            conn.execute(
                _INSERT_SQL,
                (
                    record.artifact_id,
                    record.stage.value,
                    latest_event_json,
                    record.updated_at.isoformat(),
                ),
            )

    def upsert_from_event(self, event: ResearchPromotionEvent) -> PromotionRecord:
        """Create/update record from a promotion event."""
        record = PromotionRecord(
            artifact_id=event.subject_artifact_id,
            stage=event.to_stage,
            latest_event=event,
        )
        self.upsert(record)
        return record

    def get(self, artifact_id: str) -> PromotionRecord | None:
        """Get promotion record by artifact_id."""
        with self._connect() as conn:
            cur = conn.execute(_LOOKUP_SQL, (artifact_id,))
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_record(row)

    def get_stage(self, artifact_id: str) -> ResearchStage | None:
        """Get current stage for an artifact, or None if not promoted."""
        record = self.get(artifact_id)
        return record.stage if record else None

    def get_latest_event(self, artifact_id: str) -> ResearchPromotionEvent | None:
        """Get latest promotion event for an artifact."""
        record = self.get(artifact_id)
        return record.latest_event if record else None

    def is_eligible(self, artifact_id: str, environment: str) -> bool:
        """Check if artifact is eligible for the given environment.

        Eligibility means the promotion stage meets or exceeds the minimum
        stage required for the environment.
        """
        stage = self.get_stage(artifact_id)
        if stage is None:
            return False
        return is_stage_compatible(stage, environment)

    def is_stage_compatible(self, stage_str: str, environment: str) -> bool:
        """Check if a promotion stage string is compatible with an environment.

        This is the single authoritative method for stage-environment compatibility.
        """
        try:
            stage = ResearchStage(stage_str)
        except ValueError:
            return False
        return is_stage_compatible(stage, environment)

    def list_by_stage(self, stage: ResearchStage) -> list[PromotionRecord]:
        """List all artifacts at a given stage."""
        with self._connect() as conn:
            cur = conn.execute(_STAGE_LOOKUP_SQL, (stage.value,))
            return [self._row_to_record(r) for r in cur.fetchall()]

    def list_eligible(self, environment: str) -> list[PromotionRecord]:
        """List all artifacts eligible for the given environment."""
        min_stage = get_min_stage_for_environment(environment)
        if min_stage is None:
            return []
        min_rank = _STAGE_RANK[min_stage]
        eligible: list[PromotionRecord] = []
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT artifact_id, stage, latest_event, updated_at FROM promotion_registry"
            )
            for row in cur.fetchall():
                stage = ResearchStage(row["stage"])
                if _STAGE_RANK.get(stage, -1) >= min_rank:
                    eligible.append(self._row_to_record(row))
        return eligible

    def all_artifact_ids(self) -> list[str]:
        """Return all registered artifact IDs."""
        with self._connect() as conn:
            cur = conn.execute("SELECT artifact_id FROM promotion_registry")
            return [r["artifact_id"] for r in cur.fetchall()]

    def count(self) -> int:
        """Count registered artifacts."""
        with self._connect() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM promotion_registry")
            row = cur.fetchone()
            return row[0] if row else 0

    def _row_to_record(self, row: sqlite3.Row) -> PromotionRecord:
        latest_event = None
        if row["latest_event"]:
            data = json.loads(row["latest_event"])
            latest_event = ResearchPromotionEvent(
                subject_artifact_id=data["subject_artifact_id"],
                from_stage=ResearchStage(data["from_stage"]),
                to_stage=ResearchStage(data["to_stage"]),
                evidence_ids=tuple(data.get("evidence_ids", [])),
                actor=data.get("actor", "system"),
                timestamp=datetime.fromisoformat(data["timestamp"]),
            )
        return PromotionRecord(
            artifact_id=row["artifact_id"],
            stage=ResearchStage(row["stage"]),
            latest_event=latest_event,
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )


def _os_replace(src: Path, dst: Path) -> None:
    """Atomic replace, compatible across OSes."""
    import os

    if hasattr(os, "replace"):
        os.replace(src, dst)
    else:
        os.rename(src, dst)
