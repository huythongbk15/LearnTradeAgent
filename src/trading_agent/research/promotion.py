"""Fail-closed research promotion ladder backed by immutable evidence."""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Callable, Mapping

from trading_agent.research.lifecycle import PromotionError


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def _digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        _jsonable(payload), sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ResearchStage(str, Enum):
    EXPLORATORY = "exploratory"
    RESEARCH_VALIDATED = "research_validated"
    PAPER_ELIGIBLE = "paper_eligible"
    TESTNET_ELIGIBLE = "testnet_eligible"
    SHADOW_ELIGIBLE = "shadow_eligible"
    CANARY_ELIGIBLE = "canary_eligible"
    CANARY = "canary"
    PRODUCTION = "production"


_STAGE_ORDER = tuple(ResearchStage)


class EvidenceKind(str, Enum):
    OUTER_OOS = "outer_oos"
    MINIMUM_TRADES = "minimum_trades"
    DEFLATED_SHARPE = "deflated_sharpe"
    PBO = "pbo"
    COST_STRESS = "cost_stress"
    PARAMETER_STABILITY = "parameter_stability"
    ARTIFACT_INTEGRITY = "artifact_integrity"
    EXECUTION_SIMULATION = "execution_simulation"
    REALITY_GAP = "reality_gap"
    EMPIRICAL_CALIBRATION = "empirical_calibration"
    DRIFT_UNCERTAINTY = "drift_uncertainty"
    TESTNET_OPERATIONAL = "testnet_operational"
    SHADOW_OPERATIONAL = "shadow_operational"
    OPERATOR_APPROVAL = "operator_approval"
    CANARY_OPERATIONAL = "canary_operational"
    PRODUCTION_APPROVAL = "production_approval"
    RELEASE_ATTESTATION = "release_attestation"


class EvidenceSource(str, Enum):
    RESEARCH = "research"
    SIMULATOR = "simulator"
    TESTNET = "testnet"
    SHADOW = "shadow"
    CANARY = "canary"
    OPERATOR = "operator"
    SYSTEM = "system"


@dataclass(frozen=True)
class EvidenceArtifact:
    """Content-addressed evidence; callers cannot assert integrity with a bool."""

    evidence_id: str
    kind: EvidenceKind
    subject_artifact_id: str
    source: EvidenceSource
    payload: Mapping[str, Any]
    content_hash: str
    created_at: datetime
    validator: str

    def __post_init__(self) -> None:
        if not self.subject_artifact_id.strip() or not self.validator.strip():
            raise ValueError("subject_artifact_id and validator are required")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        frozen = _freeze(self.payload)
        object.__setattr__(self, "payload", frozen)
        expected_hash = self.compute_hash(
            kind=self.kind,
            subject_artifact_id=self.subject_artifact_id,
            source=self.source,
            payload=frozen,
            created_at=self.created_at,
            validator=self.validator,
        )
        if self.content_hash != expected_hash:
            raise ValueError("evidence content hash does not match content")
        if self.evidence_id != f"ev_{expected_hash[:32]}":
            raise ValueError("evidence_id is not content-addressed")

    @staticmethod
    def compute_hash(
        *,
        kind: EvidenceKind,
        subject_artifact_id: str,
        source: EvidenceSource,
        payload: Mapping[str, Any],
        created_at: datetime,
        validator: str,
    ) -> str:
        return _digest(
            {
                "kind": kind,
                "subject_artifact_id": subject_artifact_id,
                "source": source,
                "payload": payload,
                "created_at": created_at,
                "validator": validator,
            }
        )

    @classmethod
    def create(
        cls,
        *,
        kind: EvidenceKind,
        subject_artifact_id: str,
        source: EvidenceSource,
        payload: Mapping[str, Any],
        validator: str,
        created_at: datetime | None = None,
    ) -> "EvidenceArtifact":
        timestamp = created_at or datetime.now(UTC)
        content_hash = cls.compute_hash(
            kind=kind,
            subject_artifact_id=subject_artifact_id,
            source=source,
            payload=payload,
            created_at=timestamp,
            validator=validator,
        )
        return cls(
            evidence_id=f"ev_{content_hash[:32]}",
            kind=kind,
            subject_artifact_id=subject_artifact_id,
            source=source,
            payload=payload,
            content_hash=content_hash,
            created_at=timestamp,
            validator=validator,
        )


@dataclass(frozen=True)
class PromotionAssessment:
    target_stage: ResearchStage
    passed: bool
    required: tuple[EvidenceKind, ...]
    satisfied: tuple[EvidenceKind, ...]
    missing: tuple[EvidenceKind, ...]
    failed: tuple[str, ...]


@dataclass(frozen=True)
class ResearchPromotionEvent:
    subject_artifact_id: str
    from_stage: ResearchStage
    to_stage: ResearchStage
    evidence_ids: tuple[str, ...]
    actor: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "subject_artifact_id": self.subject_artifact_id,
            "from_stage": self.from_stage.value,
            "to_stage": self.to_stage.value,
            "evidence_ids": list(self.evidence_ids),
            "actor": self.actor,
            "timestamp": self.timestamp.isoformat(),
        }


_TARGET_REQUIREMENTS: dict[ResearchStage, tuple[EvidenceKind, ...]] = {
    ResearchStage.EXPLORATORY: (),
    ResearchStage.RESEARCH_VALIDATED: (
        EvidenceKind.OUTER_OOS,
        EvidenceKind.MINIMUM_TRADES,
        EvidenceKind.DEFLATED_SHARPE,
        EvidenceKind.PBO,
        EvidenceKind.COST_STRESS,
        EvidenceKind.PARAMETER_STABILITY,
    ),
    ResearchStage.PAPER_ELIGIBLE: (EvidenceKind.ARTIFACT_INTEGRITY,),
    ResearchStage.TESTNET_ELIGIBLE: (
        EvidenceKind.ARTIFACT_INTEGRITY,
        EvidenceKind.EXECUTION_SIMULATION,
        EvidenceKind.REALITY_GAP,
    ),
    ResearchStage.SHADOW_ELIGIBLE: (
        EvidenceKind.EMPIRICAL_CALIBRATION,
        EvidenceKind.DRIFT_UNCERTAINTY,
    ),
    ResearchStage.CANARY_ELIGIBLE: (
        EvidenceKind.TESTNET_OPERATIONAL,
        EvidenceKind.SHADOW_OPERATIONAL,
        EvidenceKind.OPERATOR_APPROVAL,
    ),
    ResearchStage.CANARY: (
        EvidenceKind.TESTNET_OPERATIONAL,
        EvidenceKind.SHADOW_OPERATIONAL,
        EvidenceKind.OPERATOR_APPROVAL,
    ),
    ResearchStage.PRODUCTION: (
        EvidenceKind.CANARY_OPERATIONAL,
        EvidenceKind.PRODUCTION_APPROVAL,
        EvidenceKind.RELEASE_ATTESTATION,
    ),
}


class ResearchPromotionGate:
    """Validates content-addressed evidence and advances exactly one stage."""

    def __init__(
        self,
        *,
        minimum_trades: int = 30,
        minimum_dsr: float = 0.95,
        maximum_pbo: float = 0.20,
        maximum_reality_gap: float = 0.50,
        maximum_ece: float = 0.10,
    ) -> None:
        self.minimum_trades = minimum_trades
        self.minimum_dsr = minimum_dsr
        self.maximum_pbo = maximum_pbo
        self.maximum_reality_gap = maximum_reality_gap
        self.maximum_ece = maximum_ece

    def assess(
        self,
        subject_artifact_id: str,
        target_stage: ResearchStage,
        evidence: tuple[EvidenceArtifact, ...] | list[EvidenceArtifact],
    ) -> PromotionAssessment:
        if isinstance(evidence, bool) or not isinstance(evidence, (tuple, list)):
            raise TypeError("promotion evidence must be a sequence of EvidenceArtifact")
        required = _TARGET_REQUIREMENTS[target_stage]
        by_kind: dict[EvidenceKind, EvidenceArtifact] = {}
        failed: list[str] = []
        for item in evidence:
            if not isinstance(item, EvidenceArtifact):
                raise TypeError("boolean/dict promotion bypasses are forbidden")
            if item.subject_artifact_id != subject_artifact_id:
                failed.append(f"{item.kind.value}:subject_mismatch")
                continue
            by_kind[item.kind] = item

        missing = tuple(kind for kind in required if kind not in by_kind)
        satisfied: list[EvidenceKind] = []
        for kind in required:
            candidate = by_kind.get(kind)
            if candidate is None:
                continue
            failure = self._failure(candidate)
            if failure is None:
                satisfied.append(kind)
            else:
                failed.append(f"{kind.value}:{failure}")
        return PromotionAssessment(
            target_stage=target_stage,
            passed=not missing and not failed and len(satisfied) == len(required),
            required=required,
            satisfied=tuple(satisfied),
            missing=missing,
            failed=tuple(failed),
        )

    def _failure(self, evidence: EvidenceArtifact) -> str | None:
        payload = evidence.payload
        kind = evidence.kind

        def number(name: str) -> float:
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name}_missing_or_non_numeric")
            value = float(value)
            if not math.isfinite(value):
                raise ValueError(f"{name}_non_finite")
            return value

        try:
            if kind == EvidenceKind.OUTER_OOS and number("net_return") <= 0.0:
                return "non_positive"
            if (
                kind == EvidenceKind.MINIMUM_TRADES
                and number("trade_count") < self.minimum_trades
            ):
                return "insufficient_trades"
            if (
                kind == EvidenceKind.DEFLATED_SHARPE
                and number("dsr_probability") < self.minimum_dsr
            ):
                return "dsr_below_threshold"
            if kind == EvidenceKind.PBO and number("pbo") > self.maximum_pbo:
                return "pbo_above_threshold"
            if (
                kind == EvidenceKind.COST_STRESS
                and number("stressed_net_return") <= 0.0
            ):
                return "cost_stress_failed"
            if (
                kind == EvidenceKind.PARAMETER_STABILITY
                and number("stability_score") < 0.70
            ):
                return "unstable_parameters"
            if kind == EvidenceKind.ARTIFACT_INTEGRITY:
                if payload.get("verified_artifact_id") != evidence.subject_artifact_id:
                    return "artifact_id_not_verified"
                if number("integrity_failures") != 0.0:
                    return "integrity_failure"
            if kind == EvidenceKind.EXECUTION_SIMULATION:
                if evidence.source != EvidenceSource.SIMULATOR:
                    return "wrong_source"
                if number("scenarios") < 100.0 or number("invariant_breaches") != 0.0:
                    return "simulation_gate_failed"
            if kind == EvidenceKind.REALITY_GAP:
                if evidence.source not in (
                    EvidenceSource.TESTNET,
                    EvidenceSource.SHADOW,
                ):
                    return "non_empirical_source"
                if (
                    number("score") > self.maximum_reality_gap
                    or number("breach_count") != 0.0
                ):
                    return "reality_gap_failed"
            if kind == EvidenceKind.EMPIRICAL_CALIBRATION:
                if evidence.source != EvidenceSource.SHADOW:
                    return "non_shadow_source"
                if payload.get("status") != "empirical":
                    return "non_empirical_status"
                if number("sample_count") < 30.0 or number("ece") > self.maximum_ece:
                    return "calibration_gate_failed"
            if kind == EvidenceKind.DRIFT_UNCERTAINTY:
                if evidence.source != EvidenceSource.SHADOW:
                    return "non_shadow_source"
                if payload.get("health_state") not in ("healthy", "degraded"):
                    return "unhealthy"
            if kind == EvidenceKind.TESTNET_OPERATIONAL:
                if evidence.source != EvidenceSource.TESTNET:
                    return "wrong_source"
                if (
                    number("days") < 30.0
                    or number("complete_order_lifecycles") < 100.0
                    or number("unresolved_orders") != 0.0
                ):
                    return "testnet_soak_failed"
            if kind == EvidenceKind.SHADOW_OPERATIONAL:
                if evidence.source != EvidenceSource.SHADOW:
                    return "wrong_source"
                if number("days") < 30.0 or number("critical_alerts") != 0.0:
                    return "shadow_soak_failed"
            if kind in (
                EvidenceKind.OPERATOR_APPROVAL,
                EvidenceKind.PRODUCTION_APPROVAL,
            ):
                if evidence.source != EvidenceSource.OPERATOR:
                    return "wrong_source"
                if (
                    not str(payload.get("approver", "")).strip()
                    or not str(payload.get("ticket", "")).strip()
                ):
                    return "approval_identity_missing"
            if kind == EvidenceKind.RELEASE_ATTESTATION:
                if evidence.source != EvidenceSource.SYSTEM:
                    return "wrong_source"
                commit_sha = payload.get("commit_sha")
                image_digest = payload.get("image_digest")
                if not isinstance(commit_sha, str) or not re.fullmatch(
                    r"[0-9a-f]{40}", commit_sha
                ):
                    return "commit_sha_invalid"
                if not isinstance(image_digest, str) or not re.fullmatch(
                    r"sha256:[0-9a-f]{64}", image_digest
                ):
                    return "image_digest_invalid"
                required_verifications = (
                    "cosign_verified",
                    "sbom_verified",
                    "slsa_verified",
                    "provenance_verified",
                )
                if any(payload.get(name) is not True for name in required_verifications):
                    return "supply_chain_verification_failed"
                if not str(payload.get("verification_run_id", "")).strip():
                    return "verification_run_missing"
            if kind == EvidenceKind.CANARY_OPERATIONAL:
                if evidence.source != EvidenceSource.CANARY:
                    return "wrong_source"
                if (
                    number("days") < 30.0
                    or number("safety_breaches") != 0.0
                    or number("loss_budget_breaches") != 0.0
                ):
                    return "canary_gate_failed"
        except ValueError as exc:
            return str(exc)
        return None


class ResearchLifecycle:
    """Canonical promotion state machine.  Evidence is retained append-only."""

    def __init__(
        self, subject_artifact_id: str, gate: ResearchPromotionGate | None = None
    ) -> None:
        if not subject_artifact_id.strip():
            raise ValueError("subject_artifact_id is required")
        self.subject_artifact_id = subject_artifact_id
        self.stage = ResearchStage.EXPLORATORY
        self.gate = gate or ResearchPromotionGate()
        self._evidence: dict[str, EvidenceArtifact] = {}
        self.events: list[ResearchPromotionEvent] = []

    @property
    def evidence(self) -> tuple[EvidenceArtifact, ...]:
        return tuple(self._evidence.values())

    def promote(
        self,
        to_stage: ResearchStage,
        *,
        evidence: tuple[EvidenceArtifact, ...] | list[EvidenceArtifact],
        actor: str,
        on_event: Callable[[ResearchPromotionEvent], Any] | None = None,
    ) -> ResearchPromotionEvent:
        """Advance one stage and optionally bridge the event to runtime.

        ``on_event`` is the Milestone D research→runtime hook (see
        ``authority/promotion_hook.PromotionHook``). It runs AFTER the gate
        passes but BEFORE this lifecycle mutates its stage — so if the hook
        raises, the promotion is aborted atomically: stage and events are
        unchanged and :class:`PromotionError` propagates.
        """
        current_index = _STAGE_ORDER.index(self.stage)
        if (
            current_index + 1 >= len(_STAGE_ORDER)
            or _STAGE_ORDER[current_index + 1] != to_stage
        ):
            raise PromotionError(
                f"invalid promotion {self.stage.value} -> {to_stage.value}; stage skipping is forbidden"
            )
        if not actor.strip():
            raise PromotionError("promotion actor is required")
        proposed = dict(self._evidence)
        for item in evidence:
            if not isinstance(item, EvidenceArtifact):
                raise PromotionError(
                    "only immutable EvidenceArtifact instances are accepted"
                )
            existing = proposed.get(item.evidence_id)
            if existing is not None and existing != item:
                raise PromotionError("evidence id collision")
            proposed[item.evidence_id] = item
        assessment = self.gate.assess(
            self.subject_artifact_id, to_stage, list(proposed.values())
        )
        if not assessment.passed:
            raise PromotionError(
                f"promotion evidence failed; missing={[item.value for item in assessment.missing]}, "
                f"failed={list(assessment.failed)}"
            )
        event = ResearchPromotionEvent(
            subject_artifact_id=self.subject_artifact_id,
            from_stage=self.stage,
            to_stage=to_stage,
            evidence_ids=tuple(sorted(proposed)),
            actor=actor,
        )
        # Bridge BEFORE mutating state — atomic on hook failure (Milestone D).
        if on_event is not None:
            try:
                on_event(event)
            except Exception as exc:
                raise PromotionError(
                    f"promotion bridge failed for {self.subject_artifact_id}: "
                    f"{exc}; stage remains {self.stage.value}"
                ) from exc
        self._evidence = proposed
        self.stage = to_stage
        self.events.append(event)
        return event
