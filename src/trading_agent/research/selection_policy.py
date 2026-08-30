"""Selection Policy Artifact — Immutable policy for runtime consumption.

This module defines the canonical policy artifact that bridges research
evidence to runtime execution. Policies are content-addressed, versioned,
and bound to code/data/feature manifests for full provenance.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional


class PolicyStatus(str, Enum):
    """Lifecycle status of a policy artifact."""

    DRAFT = "draft"  # Created, not yet validated
    VALIDATED = "validated"  # Evidence verified, ready for promotion
    ACTIVE = "active"  # Currently deployed to runtime
    EXPIRED = "expired"  # Validity window ended
    ROLLED_BACK = "rolled_back"  # Superseded by rollback
    DEPRECATED = "deprecated"  # Superseded by new policy


PROMOTION_STAGE_ORDER: tuple[str, ...] = (
    "exploratory",
    "research_validated",
    "paper_eligible",
    "testnet_eligible",
    "shadow_eligible",
    "canary_eligible",
    "canary",
    "production",
)


@dataclass(frozen=True)
class ParamArtifact:
    """Content-addressed parameter blob for a strategy."""

    strategy_id: str
    params: Mapping[str, Any]
    code_sha: str = "unknown"
    param_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not self.strategy_id.strip():
            raise ValueError("strategy_id is required")
        payload = json.dumps(
            {
                "strategy_id": self.strategy_id,
                "params": dict(self.params),
                "code_sha": self.code_sha,
            },
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
    promotion_stage: str = "research_validated"

    # Rollback chain
    previous_policy_id: Optional[str] = None
    rollback_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if not self.symbol.strip() or not self.timeframe.strip() or not self.regime.strip():
            raise ValueError("symbol, timeframe and regime are required")
        if not math.isfinite(self.risk_cap) or not 0.0 < self.risk_cap <= 1.0:
            raise ValueError("risk_cap must be finite and in (0, 1]")
        if not self.fallback.strip():
            raise ValueError("fallback is required")
        if self.promotion_stage not in PROMOTION_STAGE_ORDER:
            raise ValueError(f"unknown promotion_stage: {self.promotion_stage}")
        for timestamp in (self.validity_start, self.validity_end, self.created_at):
            if timestamp is not None and (
                timestamp.tzinfo is None or timestamp.utcoffset() is None
            ):
                raise ValueError("policy timestamps must be timezone-aware")
        if self.validity_end is not None and self.validity_end <= self.validity_start:
            raise ValueError("validity_end must be after validity_start")
        if any(not math.isfinite(float(value)) for value in self.scores.values()):
            raise ValueError("policy scores must be finite")
        if self.status in {PolicyStatus.VALIDATED, PolicyStatus.ACTIVE}:
            if not self.evidence_ids:
                raise ValueError("validated/active policy requires evidence_ids")
            provenance = (
                self.policy_commit_sha,
                self.policy_data_manifest_sha,
                self.policy_feature_manifest_sha,
                self.policy_release_digest,
            )
            if any(not value.strip() or value == "unknown" for value in provenance):
                raise ValueError("validated/active policy requires complete provenance")
            if self.incumbent.code_sha in {"", "unknown"}:
                raise ValueError("validated/active policy requires strategy code_sha")
        if self.status is PolicyStatus.ACTIVE:
            if self.activated_at is None:
                raise ValueError("active policy requires activated_at")
            if not (self.activated_by or "").strip() or not (
                self.activation_ticket or ""
            ).strip():
                raise ValueError("active policy requires actor and activation ticket")
        object.__setattr__(self, "policy_id", self.compute_policy_id())

    def _identity_payload(self) -> dict[str, Any]:
        """Every persisted field participates in immutable policy identity."""
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "regime": self.regime,
            "incumbent": {
                "strategy_id": self.incumbent.strategy_id,
                "params": dict(self.incumbent.params),
                "code_sha": self.incumbent.code_sha,
                "param_hash": self.incumbent.param_hash,
            },
            "challengers": [
                {
                    "strategy_id": c.strategy_id,
                    "params": dict(c.params),
                    "code_sha": c.code_sha,
                    "param_hash": c.param_hash,
                }
                for c in self.challengers
            ],
            "scores": dict(self.scores),
            "evidence_ids": list(self.evidence_ids),
            "validity_start": self.validity_start.isoformat(),
            "validity_end": self.validity_end.isoformat()
            if self.validity_end
            else None,
            "fallback": self.fallback,
            "risk_cap": self.risk_cap,
            "policy_commit_sha": self.policy_commit_sha,
            "policy_data_manifest_sha": self.policy_data_manifest_sha,
            "policy_feature_manifest_sha": self.policy_feature_manifest_sha,
            "policy_release_digest": self.policy_release_digest,
            "promotion_stage": self.promotion_stage,
            "previous_policy_id": self.previous_policy_id,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "activated_at": self.activated_at.isoformat()
            if self.activated_at
            else None,
            "activated_by": self.activated_by,
            "activation_ticket": self.activation_ticket,
            "rollback_reason": self.rollback_reason,
        }

    def compute_policy_id(self) -> str:
        encoded = json.dumps(
            self._identity_payload(),
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

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
        # Activation creates a new lifecycle artifact, but must never refresh the
        # age of the underlying research evidence.
        age = (now - self.validity_start).days
        return age > max_age_days

    def activate(
        self, actor: str, ticket: str, now: Optional[datetime] = None
    ) -> SelectionPolicyArtifact:
        """Return new policy with ACTIVE status."""
        now = now or datetime.now(UTC)
        if self.status is not PolicyStatus.VALIDATED:
            raise ValueError("only a validated policy can be activated")
        if not actor.strip() or not ticket.strip():
            raise ValueError("activation actor and ticket are required")
        return replace(
            self,
            status=PolicyStatus.ACTIVE,
            created_at=now,
            activated_at=now,
            activated_by=actor,
            activation_ticket=ticket,
        )

    def expire(self, now: Optional[datetime] = None) -> SelectionPolicyArtifact:
        """Return new policy with EXPIRED status."""
        now = now or datetime.now(UTC)
        return replace(self, status=PolicyStatus.EXPIRED, created_at=now)

    def rollback(
        self,
        reason: str,
        previous: SelectionPolicyArtifact,
        now: Optional[datetime] = None,
    ) -> SelectionPolicyArtifact:
        """Return new policy representing rollback to previous."""
        now = now or datetime.now(UTC)
        return replace(
            self,
            status=PolicyStatus.ROLLED_BACK,
            created_at=now,
            rollback_reason=reason,
            previous_policy_id=previous.policy_id,
        )

    def to_json(self) -> str:
        """Serialize to JSON with all fields."""
        return json.dumps(
            {"policy_id": self.policy_id, **self._identity_payload()},
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def from_json(cls, data: str) -> SelectionPolicyArtifact:
        """Deserialize from JSON."""
        d = json.loads(data)
        incumbent = ParamArtifact(
            strategy_id=d["incumbent"]["strategy_id"],
            params=d["incumbent"]["params"],
            code_sha=d["incumbent"].get("code_sha", "unknown"),
        )
        challengers = tuple(
            ParamArtifact(
                strategy_id=c["strategy_id"],
                params=c["params"],
                code_sha=c.get("code_sha", "unknown"),
            )
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
            validity_end=datetime.fromisoformat(d["validity_end"])
            if d.get("validity_end")
            else None,
            fallback=d.get("fallback", "NO_TRADE"),
            risk_cap=d.get("risk_cap", 0.25),
            status=PolicyStatus(d.get("status", "draft")),
            created_at=datetime.fromisoformat(d["created_at"]),
            activated_at=datetime.fromisoformat(d["activated_at"])
            if d.get("activated_at")
            else None,
            activated_by=d.get("activated_by"),
            activation_ticket=d.get("activation_ticket"),
            policy_commit_sha=d.get("policy_commit_sha", "unknown"),
            policy_data_manifest_sha=d.get("policy_data_manifest_sha", "unknown"),
            policy_feature_manifest_sha=d.get("policy_feature_manifest_sha", "unknown"),
            policy_release_digest=d.get("policy_release_digest", "unknown"),
            promotion_stage=d.get("promotion_stage", "research_validated"),
            previous_policy_id=d.get("previous_policy_id"),
            rollback_reason=d.get("rollback_reason"),
        )

    def verify_integrity(self) -> bool:
        """Verify policy_id matches content hash (tamper detection)."""
        return hmac.compare_digest(self.compute_policy_id(), self.policy_id)


@dataclass(frozen=True)
class PolicySignatureEnvelope:
    """Detached HMAC-SHA256 signature for one immutable policy artifact."""

    policy_id: str
    key_id: str
    signature: str
    signed_at: datetime
    algorithm: str = "HMAC-SHA256"

    @classmethod
    def sign(
        cls,
        policy: SelectionPolicyArtifact,
        *,
        key: bytes,
        key_id: str,
        signed_at: datetime | None = None,
    ) -> PolicySignatureEnvelope:
        if not key or not key_id.strip():
            raise ValueError("non-empty signing key and key_id are required")
        signed_at = signed_at or datetime.now(UTC)
        signature = hmac.new(key, policy.to_json().encode(), hashlib.sha256).hexdigest()
        return cls(
            policy_id=policy.policy_id,
            key_id=key_id,
            signature=signature,
            signed_at=signed_at,
        )

    def verify(self, policy: SelectionPolicyArtifact, *, key: bytes) -> bool:
        if self.algorithm != "HMAC-SHA256" or self.policy_id != policy.policy_id:
            return False
        expected = hmac.new(key, policy.to_json().encode(), hashlib.sha256).hexdigest()
        return policy.verify_integrity() and hmac.compare_digest(expected, self.signature)

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "key_id": self.key_id,
            "signature": self.signature,
            "signed_at": self.signed_at.isoformat(),
            "algorithm": self.algorithm,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> PolicySignatureEnvelope:
        return cls(
            policy_id=str(value["policy_id"]),
            key_id=str(value["key_id"]),
            signature=str(value["signature"]),
            signed_at=datetime.fromisoformat(str(value["signed_at"])),
            algorithm=str(value.get("algorithm", "HMAC-SHA256")),
        )


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
        tmp = self._index_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self._index, indent=2, sort_keys=True))
        tmp.replace(self._index_path)

    def _policy_path(self, policy_id: str) -> Path:
        return self.root / f"{policy_id}.json"

    def add(self, policy: SelectionPolicyArtifact) -> str:
        """Add policy to registry. Returns policy_id."""
        if not policy.verify_integrity():
            raise ValueError(f"Policy integrity check failed: {policy.policy_id}")

        path = self._policy_path(policy.policy_id)
        serialized = policy.to_json()
        if path.exists():
            if path.read_text() != serialized:
                raise ValueError(f"policy id collision: {policy.policy_id}")
        else:
            tmp = path.with_suffix(".json.tmp")
            tmp.write_text(serialized)
            tmp.replace(path)

        # Update index
        key = f"{policy.symbol}:{policy.timeframe}:{policy.regime}"
        if key not in self._index:
            self._index[key] = {}
        self._index[key][policy.policy_id] = {
            "status": policy.status.value,
            "strategy_id": policy.incumbent.strategy_id,
            "created_at": policy.created_at.isoformat(),
            "validity_start": policy.validity_start.isoformat(),
            "validity_end": policy.validity_end.isoformat()
            if policy.validity_end
            else None,
        }
        self._save_index()
        return policy.policy_id

    def add_signature(self, envelope: PolicySignatureEnvelope) -> Path:
        if self.get(envelope.policy_id) is None:
            raise ValueError("cannot sign a policy that is not in the registry")
        path = self.root / f"{envelope.policy_id}.{envelope.key_id}.sig.json"
        serialized = json.dumps(envelope.to_dict(), sort_keys=True, separators=(",", ":"))
        if path.exists():
            if path.read_text() != serialized:
                raise ValueError("signature envelope already exists with different content")
            return path
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(serialized)
        tmp.replace(path)
        return path

    def get_signature(
        self, policy_id: str, key_id: str
    ) -> PolicySignatureEnvelope | None:
        path = self.root / f"{policy_id}.{key_id}.sig.json"
        if not path.exists():
            return None
        return PolicySignatureEnvelope.from_dict(json.loads(path.read_text()))

    def get_active_verified(
        self,
        symbol: str,
        timeframe: str,
        regime: str,
        *,
        key: bytes,
        key_id: str,
        now: datetime | None = None,
        max_age_days: int = 30,
    ) -> SelectionPolicyArtifact | None:
        policy = self.get_active(symbol, timeframe, regime, now=now)
        if policy is None or policy.is_stale(max_age_days=max_age_days, now=now):
            return None
        envelope = self.get_signature(policy.policy_id, key_id)
        if envelope is None or not envelope.verify(policy, key=key):
            return None
        return policy

    def get(
        self, policy_id: str, *, verify: bool = True
    ) -> Optional[SelectionPolicyArtifact]:
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

    def get_active(
        self, symbol: str, timeframe: str, regime: str, now: Optional[datetime] = None
    ) -> Optional[SelectionPolicyArtifact]:
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

    def list_all(
        self,
        symbol: Optional[str] = None,
        timeframe: Optional[str] = None,
        regime: Optional[str] = None,
    ) -> list[SelectionPolicyArtifact]:
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


class SelectionPolicyBuilder:
    """Fail-closed bridge from promotable S3 WFO evidence to an S4 policy."""

    @staticmethod
    def from_wfo_result(
        result: Any,
        *,
        regime: str,
        release_digest: str,
        validity_days: int = 30,
        risk_cap: float = 0.25,
        now: datetime | None = None,
        challengers: tuple[ParamArtifact, ...] = (),
    ) -> SelectionPolicyArtifact:
        now = now or datetime.now(UTC)
        if validity_days <= 0:
            raise ValueError("validity_days must be positive")
        if not release_digest.strip() or release_digest == "unknown":
            raise ValueError("release_digest is required")
        metrics = getattr(result, "aggregate_metrics", {})
        if not bool(getattr(result, "passes_hard_gates", False)):
            raise ValueError("WFO result did not pass hard gates")
        if not bool(metrics.get("promotable")):
            raise ValueError("WFO result is not promotion-eligible")
        holdout = getattr(result, "final_holdout", None) or {}
        if holdout.get("status") != "COMPLETED":
            raise ValueError("completed frozen holdout is required")
        manifest = getattr(result, "study_manifest", None)
        if manifest is None or not bool(getattr(manifest, "provenance_eligible", False)):
            raise ValueError("complete real-market study provenance is required")
        outer_results = list(getattr(result, "outer_results", ()))
        if not outer_results:
            raise ValueError("at least one outer result is required")
        selected_params = dict(outer_results[-1].params)
        selected_params.pop("cost_scenario", None)
        evidence_ids = [manifest.manifest_id]
        evidence_ids.extend(
            outer.artifact.artifact_id
            for outer in outer_results
            if getattr(outer, "artifact", None) is not None
        )
        holdout_id = holdout.get("holdout_id")
        if holdout_id:
            evidence_ids.append(str(holdout_id))
        if len(evidence_ids) < 3:
            raise ValueError("insufficient independent evidence artifacts")
        scores = {
            "selection_score": float(metrics.get("median_test_sharpe", 0.0)),
            "median_test_sharpe": float(metrics.get("median_test_sharpe", 0.0)),
            "median_test_return_pct": float(metrics.get("median_test_return_pct", 0.0)),
            "positive_outer_folds_pct": float(
                metrics.get("positive_outer_folds_pct", 0.0)
            ),
        }
        return SelectionPolicyArtifact(
            symbol=result.spec.symbol,
            timeframe=result.spec.timeframe,
            regime=regime,
            incumbent=ParamArtifact(
                result.spec.strategy_id,
                selected_params,
                code_sha=manifest.strategy_code_sha,
            ),
            challengers=challengers,
            scores=scores,
            evidence_ids=tuple(dict.fromkeys(evidence_ids)),
            validity_start=now,
            validity_end=now + timedelta(days=validity_days),
            fallback="NO_TRADE",
            risk_cap=risk_cap,
            status=PolicyStatus.VALIDATED,
            created_at=now,
            policy_commit_sha=manifest.commit_sha,
            policy_data_manifest_sha=manifest.data_manifest_sha,
            policy_feature_manifest_sha=manifest.feature_schema_hash,
            policy_release_digest=release_digest,
            promotion_stage="research_validated",
        )


class PolicyActivationService:
    """Atomic append-only activation and rollback with signed audit events."""

    def __init__(
        self,
        registry: SelectionPolicyRegistry,
        *,
        signing_key: bytes,
        key_id: str,
        audit_path: Path,
    ) -> None:
        if not signing_key or not key_id.strip():
            raise ValueError("signing key and key_id are required")
        self.registry = registry
        self.signing_key = signing_key
        self.key_id = key_id
        self.audit_path = Path(audit_path)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)

    def _audit(self, event: Mapping[str, Any]) -> None:
        previous_hash = "GENESIS"
        if self.audit_path.exists():
            lines = [line for line in self.audit_path.read_text().splitlines() if line]
            if lines:
                previous_hash = str(json.loads(lines[-1])["event_hash"])
        payload = {**event, "previous_hash": previous_hash}
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        record = {
            **payload,
            "event_hash": hashlib.sha256(encoded.encode()).hexdigest(),
        }
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def activate(
        self,
        policy_id: str,
        *,
        actor: str,
        ticket: str,
        now: datetime | None = None,
        expected_previous_policy_id: str | None = None,
    ) -> SelectionPolicyArtifact:
        now = now or datetime.now(UTC)
        policy = self.registry.get(policy_id)
        if policy is None:
            raise ValueError(f"policy not found: {policy_id}")
        if PROMOTION_STAGE_ORDER.index(policy.promotion_stage) < PROMOTION_STAGE_ORDER.index(
            "paper_eligible"
        ):
            raise ValueError(
                "policy must pass canonical promotion lifecycle through paper_eligible "
                "before activation"
            )
        current = self.registry.get_active(
            policy.symbol, policy.timeframe, policy.regime, now=now
        )
        current_id = current.policy_id if current else None
        if current_id != expected_previous_policy_id:
            raise ValueError(
                f"active policy changed: expected {expected_previous_policy_id}, got {current_id}"
            )
        active = replace(policy, previous_policy_id=current_id).activate(actor, ticket, now)
        self.registry.add(active)
        envelope = PolicySignatureEnvelope.sign(
            active, key=self.signing_key, key_id=self.key_id, signed_at=now
        )
        self.registry.add_signature(envelope)
        self._audit(
            {
                "event": "ACTIVATE",
                "policy_id": active.policy_id,
                "source_policy_id": policy.policy_id,
                "previous_policy_id": current_id,
                "actor": actor,
                "ticket": ticket,
                "timestamp": now.isoformat(),
            }
        )
        return active

    def advance_stage(
        self,
        policy_id: str,
        *,
        to_stage: str,
        actor: str,
        ticket: str,
        evidence_ids: tuple[str, ...] | list[str],
        now: datetime | None = None,
    ) -> SelectionPolicyArtifact:
        """Advance exactly one canonical promotion stage immutably.

        The canonical ``ResearchLifecycle`` remains responsible for deciding
        whether the supplied evidence is semantically sufficient. This bridge
        records its resulting stage on a new policy artifact and prevents stage
        skipping at the policy boundary.
        """

        now = now or datetime.now(UTC)
        if not actor.strip() or not ticket.strip():
            raise ValueError("stage advancement actor and ticket are required")
        policy = self.registry.get(policy_id)
        if policy is None:
            raise ValueError(f"policy not found: {policy_id}")
        if policy.status is not PolicyStatus.VALIDATED:
            raise ValueError("only a validated policy can advance promotion stage")
        if to_stage not in PROMOTION_STAGE_ORDER:
            raise ValueError(f"unknown promotion stage: {to_stage}")
        current_index = PROMOTION_STAGE_ORDER.index(policy.promotion_stage)
        target_index = PROMOTION_STAGE_ORDER.index(to_stage)
        if target_index != current_index + 1:
            raise ValueError(
                f"promotion stage skip: {policy.promotion_stage} -> {to_stage}"
            )
        additions = tuple(str(item) for item in evidence_ids if str(item).strip())
        if not additions:
            raise ValueError("stage advancement requires evidence_ids")
        advanced = replace(
            policy,
            promotion_stage=to_stage,
            evidence_ids=tuple(dict.fromkeys((*policy.evidence_ids, *additions))),
            created_at=now,
        )
        self.registry.add(advanced)
        self._audit(
            {
                "event": "PROMOTION_STAGE_ADVANCED",
                "source_policy_id": policy.policy_id,
                "policy_id": advanced.policy_id,
                "from_stage": policy.promotion_stage,
                "to_stage": to_stage,
                "actor": actor,
                "ticket": ticket,
                "evidence_ids": list(advanced.evidence_ids),
                "timestamp": now.isoformat(),
            }
        )
        return advanced

    def rollback(
        self,
        *,
        symbol: str,
        timeframe: str,
        regime: str,
        previous_policy_id: str,
        actor: str,
        ticket: str,
        reason: str,
        now: datetime | None = None,
    ) -> SelectionPolicyArtifact:
        now = now or datetime.now(UTC)
        if not reason.strip():
            raise ValueError("rollback reason is required")
        current = self.registry.get_active(symbol, timeframe, regime, now=now)
        previous = self.registry.get(previous_policy_id)
        if current is None or previous is None:
            raise ValueError("current and previous policies are required for rollback")
        if (previous.symbol, previous.timeframe, previous.regime) != (
            symbol,
            timeframe,
            regime,
        ):
            raise ValueError("rollback policy scope mismatch")
        restored = replace(
            previous,
            status=PolicyStatus.VALIDATED,
            created_at=now,
            activated_at=None,
            activated_by=None,
            activation_ticket=None,
            previous_policy_id=current.policy_id,
            rollback_reason=reason,
        ).activate(actor, ticket, now)
        self.registry.add(restored)
        envelope = PolicySignatureEnvelope.sign(
            restored, key=self.signing_key, key_id=self.key_id, signed_at=now
        )
        self.registry.add_signature(envelope)
        self._audit(
            {
                "event": "ROLLBACK",
                "policy_id": restored.policy_id,
                "restored_from_policy_id": previous_policy_id,
                "previous_active_policy_id": current.policy_id,
                "actor": actor,
                "ticket": ticket,
                "reason": reason,
                "timestamp": now.isoformat(),
            }
        )
        return restored
