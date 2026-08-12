"""Research governance — immutable strategy artifacts (Section 6).

A ``StrategyArtifact`` is the immutable record of *exactly what was tested*:
code hash, data manifest hash, parameter hash, execution-model version and
framework version.  Two artifacts with different hashes are different
artifacts — no silent drift, no same-name reinterpretation.

Artifacts are hash-chained: ``prev_artifact_id`` links an artifact to the
previous one for the same strategy, giving an auditable lineage.
"""

from __future__ import annotations

import hashlib
import json
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
        [
            [str(v) for v in row]
            for row in df.sort("timestamp").iter_rows()
        ],
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
        return sha256_hex(json.dumps(payload, sort_keys=True, separators=(",", ":")))[:24]

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
            raise ValueError(f"artifact {artifact.artifact_id} already exists (immutable store)")
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
            cur = self._artifacts.get(cur.prev_artifact_id) if cur.prev_artifact_id else None
        return chain

    def verify_integrity(self, artifact_id: str) -> bool:
        """Recompute the id from stored fields; True if unchanged."""
        art = self._artifacts.get(artifact_id)
        if art is None:
            return False
        return art.artifact_id == artifact_id