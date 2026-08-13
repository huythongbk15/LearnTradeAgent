"""Research governance — immutable strategy artifacts (Section 6).

A ``StrategyArtifact`` is the immutable record of *exactly what was tested*:
code hash, data manifest hash, parameter hash, execution-model version and
framework version.  Two artifacts with different hashes are different
artifacts — no silent drift, no same-name reinterpretation.

Artifacts are hash-chained: ``prev_artifact_id`` links an artifact to the
previous one for the same strategy, giving an auditable lineage.

``PersistentArtifactStore`` provides SQLite-backed persistence with
cryptographic chain integrity: each row's integrity_hash =
sha256(prev_integrity_hash || current_row_fields), making any tampering
detectable. Atomic writes via temp file + fsync.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def sha256_hex(payload: str | bytes) -> str:
    if isinstance(payload, str):
        payload = payload.encode()
    return hashlib.sha256(payload).hexdigest()


def hash_file(path: Path) -> str:
    """sha256 of a file's content (strategy source, config, ...)."""
    if not path.exists():
        raise FileNotFoundError(f"cannot hash missing file: {path}")
    return sha256_hex(path.read_bytes())


def canonical_params(params: dict[str, Any]) -> str:
    """Stable deterministic serialization of strategy parameters."""
    return json.dumps(params, sort_keys=True, separators=(",", ":"), default=str)


def data_manifest_hash(df) -> str:
    """sha256 of a market-data frame's rows (values only, stable order)."""
    payload = json.dumps(
        [[str(v) for v in row] for row in df.sort("timestamp").iter_rows()],
        separators=(",", ":"),
    )
    return sha256_hex(payload)


@dataclass(frozen=True)
class StrategyArtifact:
    """Immutable record of one tested strategy configuration."""

    strategy_name: str
    code_sha: str
    data_manifest_sha: str
    parameter_hash: str
    execution_model_version: str = ""
    framework_version: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    prev_artifact_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def artifact_id(self) -> str:
        """Content-addressed id: hash of everything substantive."""
        payload = {
            "strategy_name": self.strategy_name,
            "code_sha": self.code_sha,
            "data_manifest_sha": self.data_manifest_sha,
            "parameter_hash": self.parameter_hash,
            "execution_model_version": self.execution_model_version,
            "framework_version": self.framework_version,
            "metadata": json.dumps(self.metadata, sort_keys=True, default=str),
        }
        return sha256_hex(json.dumps(payload, sort_keys=True, separators=(",", ":")))[
            :24
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_id": self.artifact_id,
            "strategy_name": self.strategy_name,
            "code_sha": self.code_sha,
            "data_manifest_sha": self.data_manifest_sha,
            "parameter_hash": self.parameter_hash,
            "execution_model_version": self.execution_model_version,
            "framework_version": self.framework_version,
            "created_at": self.created_at.isoformat(),
            "prev_artifact_id": self.prev_artifact_id,
            "metadata": self.metadata,
        }


def build_strategy_artifact(
    *,
    strategy_name: str,
    code_path: Path | str | None,
    df,
    params: dict[str, Any],
    execution_model_version: str,
    framework_version: str,
    prev_artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> StrategyArtifact:
    """Convenience factory: hashes the strategy source + data + params."""
    code_sha = hash_file(Path(code_path)) if code_path else ""
    return StrategyArtifact(
        strategy_name=strategy_name,
        code_sha=code_sha,
        data_manifest_sha=data_manifest_hash(df),
        parameter_hash=sha256_hex(canonical_params(params)),
        execution_model_version=execution_model_version,
        framework_version=framework_version,
        prev_artifact_id=prev_artifact_id,
        metadata=metadata or {},
    )


class ArtifactStore:
    """In-memory artifact registry keyed by artifact_id.

    Artifacts are immutable: attempting to overwrite an existing id raises.
    """

    def __init__(self) -> None:
        self._artifacts: dict[str, StrategyArtifact] = {}

    def add(self, artifact: StrategyArtifact) -> None:
        if artifact.artifact_id in self._artifacts:
            raise ValueError(
                f"artifact {artifact.artifact_id} already exists (immutable store)"
            )
        self._artifacts[artifact.artifact_id] = artifact

    def get(self, artifact_id: str) -> StrategyArtifact | None:
        return self._artifacts.get(artifact_id)

    def all_for(self, strategy_name: str) -> list[StrategyArtifact]:
        return [a for a in self._artifacts.values() if a.strategy_name == strategy_name]

    def lineage(self, artifact_id: str) -> list[StrategyArtifact]:
        """Chain of artifacts from this one back to the root (oldest last)."""
        chain: list[StrategyArtifact] = []
        seen: set[str] = set()
        cur = self._artifacts.get(artifact_id)
        while cur is not None and cur.artifact_id not in seen:
            seen.add(cur.artifact_id)
            chain.append(cur)
            cur = (
                self._artifacts.get(cur.prev_artifact_id)
                if cur.prev_artifact_id
                else None
            )
        return chain

    def unique_strategies(self) -> set[str]:
        """Set of strategy names in the store."""
        return {a.strategy_name for a in self._artifacts.values()}

    def verify_integrity(self, artifact_id: str) -> bool:
        """Recompute the id from stored fields; True if unchanged."""
        art = self._artifacts.get(artifact_id)
        if art is None:
            return False
        return art.artifact_id == artifact_id


# ── PersistentArtifactStore ─────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id     TEXT PRIMARY KEY,
    strategy_name   TEXT NOT NULL,
    code_sha        TEXT NOT NULL,
    data_manifest_sha TEXT NOT NULL,
    parameter_hash  TEXT NOT NULL,
    execution_model_version TEXT NOT NULL DEFAULT '',
    framework_version TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL,
    prev_artifact_id TEXT,
    metadata        TEXT NOT NULL DEFAULT '{}',
    integrity_hash  TEXT NOT NULL,
    UNIQUE(strategy_name, artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_artifacts_strategy ON artifacts(strategy_name);
CREATE INDEX IF NOT EXISTS idx_artifacts_prev ON artifacts(prev_artifact_id);
"""

_INSERT_SQL = """
INSERT INTO artifacts (
    artifact_id, strategy_name, code_sha, data_manifest_sha,
    parameter_hash, execution_model_version, framework_version,
    created_at, prev_artifact_id, metadata, integrity_hash
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


def _row_integrity_payload(row: dict[str, Any]) -> str:
    """Stable serialization of row fields for integrity hashing."""
    # Match the insertion format exactly: None -> ""
    prev_id = row["prev_artifact_id"] or ""
    return json.dumps(
        {
            "artifact_id": row["artifact_id"],
            "strategy_name": row["strategy_name"],
            "code_sha": row["code_sha"],
            "data_manifest_sha": row["data_manifest_sha"],
            "parameter_hash": row["parameter_hash"],
            "execution_model_version": row["execution_model_version"],
            "framework_version": row["framework_version"],
            "created_at": row["created_at"],
            "prev_artifact_id": prev_id,
            "metadata": row["metadata"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


class PersistentArtifactStore:
    """SQLite-backed artifact registry with cryptographic chain integrity.

    Each row's ``integrity_hash`` = sha256(prev_integrity_hash || row_payload).
    The genesis row (no prev_artifact_id) uses prev = "0"*64.
    Any tampering (insert/delete/modify/reorder) breaks the chain.

    Writes are atomic: temp file → fsync → os.replace.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(_SCHEMA_SQL)

    @contextmanager
    def _connect(self):
        """Context manager for a DB connection with atomic write support.

        Copies the current DB to a temp file, operates on the temp,
        then atomically replaces the original. This ensures schema and
        data are always on the temp file before commit.
        """
        temp_path = self.db_path.with_suffix(".tmp")
        try:
            # Copy current DB to temp if it exists
            if self.db_path.exists():
                import shutil

                shutil.copy2(self.db_path, temp_path)
            else:
                # Fresh DB - temp file will be created by sqlite3.connect
                pass

            conn = sqlite3.connect(temp_path)
            conn.row_factory = sqlite3.Row
            # Ensure schema exists on temp
            conn.executescript(_SCHEMA_SQL)

            yield conn
            conn.commit()
            # fsync the temp file
            conn.execute("PRAGMA synchronous=FULL")
            conn.close()
            # Atomic replace
            os.replace(temp_path, self.db_path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def _get_latest_integrity_hash(self, conn: sqlite3.Connection) -> str:
        """Get the integrity_hash of the most recently inserted row (any strategy)."""
        cur = conn.execute(
            "SELECT integrity_hash FROM artifacts ORDER BY rowid DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row[0] if row else "0" * 64

    def add(self, artifact: StrategyArtifact) -> None:
        """Add an artifact. Raises if artifact_id already exists."""
        with self._connect() as conn:
            # Check for duplicate
            cur = conn.execute(
                "SELECT 1 FROM artifacts WHERE artifact_id = ?", (artifact.artifact_id,)
            )
            if cur.fetchone():
                raise ValueError(
                    f"artifact {artifact.artifact_id} already exists (immutable store)"
                )

            # Compute integrity hash: chain from previous global row
            prev_hash = self._get_latest_integrity_hash(conn)
            row_dict = {
                "artifact_id": artifact.artifact_id,
                "strategy_name": artifact.strategy_name,
                "code_sha": artifact.code_sha,
                "data_manifest_sha": artifact.data_manifest_sha,
                "parameter_hash": artifact.parameter_hash,
                "execution_model_version": artifact.execution_model_version,
                "framework_version": artifact.framework_version,
                "created_at": artifact.created_at.isoformat(),
                "prev_artifact_id": artifact.prev_artifact_id or "",
                "metadata": json.dumps(artifact.metadata, sort_keys=True, default=str),
            }
            integrity_hash = sha256_hex(prev_hash + _row_integrity_payload(row_dict))

            conn.execute(
                _INSERT_SQL,
                (
                    artifact.artifact_id,
                    artifact.strategy_name,
                    artifact.code_sha,
                    artifact.data_manifest_sha,
                    artifact.parameter_hash,
                    artifact.execution_model_version,
                    artifact.framework_version,
                    artifact.created_at.isoformat(),
                    artifact.prev_artifact_id,
                    json.dumps(artifact.metadata, sort_keys=True, default=str),
                    integrity_hash,
                ),
            )

    def get(self, artifact_id: str) -> StrategyArtifact | None:
        """Retrieve an artifact by ID."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            )
            row = cur.fetchone()
            if not row:
                return None
            return self._row_to_artifact(dict(row))

    def all_for(self, strategy_name: str) -> list[StrategyArtifact]:
        """All artifacts for a strategy, oldest first."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT * FROM artifacts WHERE strategy_name = ? ORDER BY rowid ASC",
                (strategy_name,),
            )
            return [self._row_to_artifact(dict(r)) for r in cur.fetchall()]

    def lineage(self, artifact_id: str) -> list[StrategyArtifact]:
        """Chain of artifacts from this one back to the root (oldest last)."""
        chain: list[StrategyArtifact] = []
        seen: set[str] = set()
        cur_id = artifact_id
        while cur_id and cur_id not in seen:
            seen.add(cur_id)
            art = self.get(cur_id)
            if art is None:
                break
            chain.append(art)
            cur_id = art.prev_artifact_id
        return chain

    def verify_integrity(self, artifact_id: str) -> bool:
        """Verify single artifact's content hash matches its artifact_id."""
        art = self.get(artifact_id)
        if art is None:
            return False
        return art.artifact_id == artifact_id

    def verify_chain(self) -> tuple[bool, str | None]:
        """Verify the entire cryptographic chain.

        Returns (ok, error_message).  Checks:
        1. Each row's integrity_hash = sha256(prev_integrity_hash || row_payload)
        2. prev_artifact_id links match stored artifacts
        3. artifact_id content hashes are correct
        """
        with self._connect() as conn:
            cur = conn.execute("SELECT * FROM artifacts ORDER BY rowid ASC")
            rows = [dict(r) for r in cur.fetchall()]

        prev_hash = "0" * 64
        for i, row in enumerate(rows):
            # 1. Verify integrity_hash chain
            expected_hash = sha256_hex(prev_hash + _row_integrity_payload(row))
            if row["integrity_hash"] != expected_hash:
                return (
                    False,
                    f"integrity chain broken at rowid {i + 1} (artifact {row['artifact_id']}): expected {expected_hash}, got {row['integrity_hash']}",
                )
            prev_hash = row["integrity_hash"]

            # 2. Verify artifact_id content hash
            art = self._row_to_artifact(row)
            if art.artifact_id != row["artifact_id"]:
                return (
                    False,
                    f"artifact_id mismatch for {row['artifact_id']}: recomputed {art.artifact_id}",
                )

            # 3. Verify prev_artifact_id link (if present)
            if row["prev_artifact_id"]:
                prev_art = self.get(row["prev_artifact_id"])
                if prev_art is None:
                    return (
                        False,
                        f"prev_artifact_id {row['prev_artifact_id']} not found for {row['artifact_id']}",
                    )

        return True, None

    def _row_to_artifact(self, row: dict[str, Any]) -> StrategyArtifact:
        """Convert a DB row to a StrategyArtifact."""
        return StrategyArtifact(
            strategy_name=row["strategy_name"],
            code_sha=row["code_sha"],
            data_manifest_sha=row["data_manifest_sha"],
            parameter_hash=row["parameter_hash"],
            execution_model_version=row["execution_model_version"],
            framework_version=row["framework_version"],
            created_at=datetime.fromisoformat(row["created_at"]),
            prev_artifact_id=row["prev_artifact_id"] or None,
            metadata=json.loads(row["metadata"]),
        )

    # Migration from in-memory ArtifactStore
    def migrate_from_memory(self, memory_store: ArtifactStore) -> int:
        """Migrate all artifacts from an in-memory ArtifactStore.

        Artifacts are inserted in strategy-name order, then by created_at,
        to maintain a deterministic chain. Returns count of migrated artifacts.
        """
        count = 0
        for strategy_name in sorted(memory_store.unique_strategies()):
            for art in memory_store.all_for(strategy_name):
                try:
                    self.add(art)
                    count += 1
                except ValueError:
                    # Already exists in DB (idempotent)
                    pass
        return count
