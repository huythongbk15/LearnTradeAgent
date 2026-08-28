"""Selection Policy Artifact — Immutable policy for runtime consumption.

This module defines the canonical policy artifact that bridges research
evidence to runtime execution. Policies are content-addressed, versioned,
and bound to code/data/feature manifests for full provenance.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping, Optional

from trading_agent.research.promotion import (
    EvidenceArtifact,
    EvidenceKind,
    EvidenceSource,
    ResearchStage,
)


class PolicyStatus(str, Enum):
    """Lifecycle status of a policy artifact."""

    DRAFT = "draft"                    # Created, not yet validated
    VALIDATED = "validated"            # Evidence verified, ready for promotion
    ACTIVE = "active"                  # Currently deployed to runtime
    EXPIRED = "expired"                # Validity window ended
    ROLLED_BACK = "rolled_back"        # Superseded by rollback
    DEPRECATED = "deprecated"          # Superseded by new policy


@dataclass(frozen=True)
class ParamArtifact:
    """Content-addressed parameter blob for a strategy."""

    strategy_id: str
    params: Mapping[str, Any]
    param_hash: str = field(init=False)

    def __post_init__(self) -> None:
        payload = json.dumps(
            {"strategy_id": self.strategy_id, "params": dict(self.params)},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        object.__setattr__(self, "param_hash", hashlib.sha256(payload).hexdigest()[:16])


@dataclass(frozen=True)
class SelectionPolicyArtifact:
    """
    Immutable selection policy for one (symbol, timeframe, regime).

    A policy binds a strategy with specific parameters to a trading pair
    and regime, backed by evidence artifacts. The policy is content-addressed
    and cannot be mutated after creation — any change produces a new policy_id.

    Attributes:
        policy_id: SHA256 hash of all fields (content-addressed)
        symbol: Trading symbol (e.g., "BTC/USDT")
        timeframe: Bar timeframe (e.g., "1h")
        regime: Market regime this policy applies to (e.g., "TRENDING_UP")
        incumbent: Currently active ParamArtifact
        challengers: Alternative ParamArtifacts under evaluation
        scores: Evaluation metrics {metric_name: value}
        evidence_ids: List of EvidenceArtifact.evidence_id backing this policy
        validity_window: (start, end) datetime for policy activation
        fallback: Fallback action if policy cannot execute ("NO_TRADE" or strategy_id)
        risk_cap: Maximum position size as fraction of equity (e.g., 0.25)
        status: Current lifecycle status
        created_at: Creation timestamp
        activated_at: When this policy was activated (None if not yet)
        activated_by: Actor who activated (operator name or system component)
        activation_ticket: Approval ticket ID (e.g., "GH-123")
        policy_commit_sha: Git commit SHA that generated this policy
        policy_data_manifest_sha: Hash of training data used
        policy_feature_manifest_sha: Hash of features used
        policy_release_digest: Docker image digest (if deployed)
        previous_policy_id: Previous policy ID (for rollback chain)
        rollback_reason: Reason if rolled back
    """

    # Identity
    policy_id: str = field(init=False)
    symbol: str
    timeframe: str
    regime: str

    # Strategy selection
    incumbent: ParamArtifact
    challengers: tuple[ParamArtifact, ...] = field(default_factory=tuple)

    # Evidence & scoring
    scores: Mapping[str, float] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = field(default_factory=tuple)

    # Validity & risk
    validity_start: datetime = field(default_factory=lambda: datetime.now(UTC))
    validity_end: Optional[datetime] = None  # None = no expiry
    fallback: str = "NO_TRADE"
    risk_cap: float = 0.25

    # Lifecycle
    status: PolicyStatus = PolicyStatus.DRAFT
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    activated_at: Optional[datetime] = None
    activated_by: Optional[str] = None
    activation_ticket: Optional[str] = None

    # Provenance bindings
    policy_commit_sha: str = "unknown"
    policy_data_manifest_sha: str = "unknown"
    policy_feature_manifest_sha: str = "unknown"
    policy_release_digest: str = "unknown"

    # Rollback chain
    previous_policy_id: Optional[str] = None
    rollback_reason: Optional[str] = None

    def __post_init__(self) -> None:
        # Compute content hash
        payload = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "incumbent": {
                "strategy_id": self.incumbent.strategy_id,
                "params": dict(self.incumbent.params),
                "param_hash": self.incumbent.param_hash,
            },
            "challengers": [
                {
                    "strategy_id": c.strategy_id,
                    "params": dict(c.params),
                    "param_hash": c.param_hash,
                }
                for c in self.challengers
            ],
            "scores": dict(self.scores),
            "evidence_ids": list(self.evidence_ids),
            "validity_start": self.validity_start.isoformat(),
            "validity_end": self.validity_end.isoformat() if self.validity_end else None,
            "fallback": self.fallback,
            "risk_cap": self.risk_cap,
            "policy_commit_sha": self.policy_commit_sha,
            "policy_data_manifest_sha": self.policy_data_manifest_sha,
            "policy_feature_manifest_sha": self.policy_feature_manifest_sha,
            "policy_release_digest": self.policy_release_digest,
            "previous_policy_id": self.previous_policy_id,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        object.__setattr__(self, "policy_id", hashlib.sha256(encoded).hexdigest()[:24])

    def is_valid(self, now: Optional[datetime] = None) -> bool:
        """Check if policy is valid at given time (default: now)."""
        now = now or datetime.now(UTC)
        if self.status != PolicyStatus.ACTIVE:
            return False
        if self.validity_end and now > self.validity_end:
            return False
        if now < self.validity_start:
            return False
        return True

    def is_stale(self, max_age_days: int = 30, now: Optional[datetime] = None) -> bool:
        """Check if policy evidence is stale."""
        now = now or datetime.now(UTC)
        age = (now - self.created_at).days
        return age > max_age_days

    def activate(self, actor: str, ticket: str, now: Optional[datetime] = None) -> SelectionPolicyArtifact:
        """Return new policy with ACTIVE status."""
        now = now or datetime.now(UTC)
        return replace(
            self,
            status=PolicyStatus.ACTIVE,
            activated_at=now,
            activated_by=actor,
            activation_ticket=ticket,
        )

    def expire(self, now: Optional[datetime] = None) -> SelectionPolicyArtifact:
        """Return new policy with EXPIRED status."""
        now = now or datetime.now(UTC)
        return replace(self, status=PolicyStatus.EXPIRED)

    def rollback(self, reason: str, previous: SelectionPolicyArtifact, now: Optional[datetime] = None) -> SelectionPolicyArtifact:
        """Return new policy representing rollback to previous."""
        now = now or datetime.now(UTC)
        return replace(
            self,
            status=PolicyStatus.ROLLED_BACK,
            rollback_reason=reason,
            previous_policy_id=previous.policy_id,
        )

    def to_json(self) -> str:
        """Serialize to JSON with all fields."""
        return json.dumps({
            "policy_id": self.policy_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "incumbent": {
                "strategy_id": self.incumbent.strategy_id,
                "params": dict(self.incumbent.params),
                "param_hash": self.incumbent.param_hash,
            },
            "challengers": [
                {
                    "strategy_id": c.strategy_id,
                    "params": dict(c.params),
                    "param_hash": c.param_hash,
                }
                for c in self.challengers
            ],
            "scores": dict(self.scores),
            "evidence_ids": list(self.evidence_ids),
            "validity_start": self.validity_start.isoformat(),
            "validity_end": self.validity_end.isoformat() if self.validity_end else None,
            "fallback": self.fallback,
            "risk_cap": self.risk_cap,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "activated_at": self.activated_at.isoformat() if self.activated_at else None,
            "activated_by": self.activated_by,
            "activation_ticket": self.activation_ticket,
            "policy_commit_sha": self.policy_commit_sha,
            "policy_data_manifest_sha": self.policy_data_manifest_sha,
            "policy_feature_manifest_sha": self.policy_feature_manifest_sha,
            "policy_release_digest": self.policy_release_digest,
            "previous_policy_id": self.previous_policy_id,
            "rollback_reason": self.rollback_reason,
        }, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_json(cls, data: str) -> SelectionPolicyArtifact:
        """Deserialize from JSON."""
        d = json.loads(data)
        incumbent = ParamArtifact(
            strategy_id=d["incumbent"]["strategy_id"],
            params=d["incumbent"]["params"],
        )
        challengers = tuple(
            ParamArtifact(strategy_id=c["strategy_id"], params=c["params"])
            for c in d.get("challengers", [])
        )
        return cls(
            symbol=d["symbol"],
            timeframe=d["timeframe"],
            regime=d["regime"],
            incumbent=incumbent,
            challengers=challengers,
            scores=d.get("scores", {}),
            evidence_ids=tuple(d.get("evidence_ids", [])),
            validity_start=datetime.fromisoformat(d["validity_start"]),
            validity_end=datetime.fromisoformat(d["validity_end"]) if d.get("validity_end") else None,
            fallback=d.get("fallback", "NO_TRADE"),
            risk_cap=d.get("risk_cap", 0.25),
            status=PolicyStatus(d.get("status", "draft")),
            created_at=datetime.fromisoformat(d["created_at"]),
            activated_at=datetime.fromisoformat(d["activated_at"]) if d.get("activated_at") else None,
            activated_by=d.get("activated_by"),
            activation_ticket=d.get("activation_ticket"),
            policy_commit_sha=d.get("policy_commit_sha", "unknown"),
            policy_data_manifest_sha=d.get("policy_data_manifest_sha", "unknown"),
            policy_feature_manifest_sha=d.get("policy_feature_manifest_sha", "unknown"),
            policy_release_digest=d.get("policy_release_digest", "unknown"),
            previous_policy_id=d.get("previous_policy_id"),
            rollback_reason=d.get("rollback_reason"),
        )

    def verify_integrity(self) -> bool:
        """Verify policy_id matches content hash (tamper detection)."""
        # Reconstruct and compare
        reconstructed = SelectionPolicyArtifact(
            symbol=self.symbol,
            timeframe=self.timeframe,
            regime=self.regime,
            incumbent=self.incumbent,
            challengers=self.challengers,
            scores=self.scores,
            evidence_ids=self.evidence_ids,
            validity_start=self.validity_start,
            validity_end=self.validity_end,
            fallback=self.fallback,
            risk_cap=self.risk_cap,
            status=self.status,
            created_at=self.created_at,
            activated_at=self.activated_at,
            activated_by=self.activated_by,
            activation_ticket=self.activation_ticket,
            policy_commit_sha=self.policy_commit_sha,
            policy_data_manifest_sha=self.policy_data_manifest_sha,
            policy_feature_manifest_sha=self.policy_feature_manifest_sha,
            policy_release_digest=self.policy_release_digest,
            previous_policy_id=self.previous_policy_id,
            rollback_reason=self.rollback_reason,
        )
        return reconstructed.policy_id == self.policy_id


@dataclass(frozen=True)
class PolicyRegistryEntry:
    """Registry entry for a policy artifact."""

    policy: SelectionPolicyArtifact
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SelectionPolicyRegistry:
    """Append-only registry for selection policies.

    Stores policies as JSON files with content-addressed names.
    Maintains an index for fast lookup by symbol/timeframe/regime.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._index_path = self.root / "index.json"
        self._index: dict[str, dict] = self._load_index()

    def _load_index(self) -> dict[str, dict]:
        if self._index_path.exists():
            return json.loads(self._index_path.read_text())
        return {}

    def _save_index(self) -> None:
        self._index_path.write_text(json.dumps(self._index, indent=2))

    def _policy_path(self, policy_id: str) -> Path:
        return self.root / f"{policy_id}.json"

    def add(self, policy: SelectionPolicyArtifact) -> str:
        """Add policy to registry. Returns policy_id."""
        if not policy.verify_integrity():
            raise ValueError(f"Policy integrity check failed: {policy.policy_id}")

        path = self._policy_path(policy.policy_id)
        path.write_text(policy.to_json())

        # Update index
        key = f"{policy.symbol}:{policy.timeframe}:{policy.regime}"
        if key not in self._index:
            self._index[key] = {}
        self._index[key][policy.policy_id] = {
            "status": policy.status.value,
            "strategy_id": policy.incumbent.strategy_id,
            "created_at": policy.created_at.isoformat(),
            "validity_start": policy.validity_start.isoformat(),
            "validity_end": policy.validity_end.isoformat() if policy.validity_end else None,
        }
        self._save_index()
        return policy.policy_id

    def get(self, policy_id: str, *, verify: bool = True) -> Optional[SelectionPolicyArtifact]:
        """Get policy by ID.

        Args:
            policy_id: Expected policy ID
            verify: If True, verify that loaded content matches policy_id (tamper detection)

        Returns:
            Policy if found and (if verify=True) integrity verified
        """
        path = self._policy_path(policy_id)
        if not path.exists():
            return None
        policy = SelectionPolicyArtifact.from_json(path.read_text())
        if verify and policy.policy_id != policy_id:
            raise ValueError(
                f"Policy integrity check failed: expected {policy_id}, "
                f"computed {policy.policy_id} (content tampered)"
            )
        return policy

    def get_active(self, symbol: str, timeframe: str, regime: str, now: Optional[datetime] = None) -> Optional[SelectionPolicyArtifact]:
        """Get currently active policy for symbol/timeframe/regime."""
        key = f"{symbol}:{timeframe}:{regime}"
        if key not in self._index:
            return None

        now = now or datetime.now(UTC)
        best: Optional[SelectionPolicyArtifact] = None
        for policy_id, meta in self._index[key].items():
            policy = self.get(policy_id)
            if policy and policy.is_valid(now):
                if best is None or policy.created_at > best.created_at:
                    best = policy
        return best

    def list_all(self, symbol: Optional[str] = None, timeframe: Optional[str] = None, regime: Optional[str] = None) -> list[SelectionPolicyArtifact]:
        """List all policies, optionally filtered."""
        results = []
        for key, policies in self._index.items():
            parts = key.split(":")
            if symbol and parts[0] != symbol:
                continue
            if timeframe and parts[1] != timeframe:
                continue
            if regime and parts[2] != regime:
                continue
            for policy_id in policies:
                policy = self.get(policy_id)
                if policy:
                    results.append(policy)
        return results

    def get_lineage(self, policy_id: str) -> list[SelectionPolicyArtifact]:
        """Get full rollback chain from policy to genesis."""
        chain = []
        current = self.get(policy_id)
        while current:
            chain.append(current)
            if current.previous_policy_id:
                current = self.get(current.previous_policy_id)
            else:
                break
        return chain