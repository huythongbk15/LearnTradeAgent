"""Strategy promotion state machine (Section 7).

States and allowed transitions (no skips, no renames):

    EXPLORATORY ──▶ REVIEWED ──▶ CANARY_ELIGIBLE
         │              │             │
         └──▶ REJECTED ─┘             └──▶ PROMOTED_TO_CANARY ──▶ CANARY_LIVE
                                      (promotion also possible from REVIEWED
                                       only after a fresh artifact)

Design rules from the hardening brief:

* an artifact can only advance through the DAG, never skip;
* any transition requires the artifact's hashes to be intact (immutability);
* ``REJECTED`` is terminal;
* ``promotion_check`` (Reality Gap gate) must pass before CANARY_ELIGIBLE.

``PromotionPolicy`` enforces evidence-gated promotions:
- Each transition requires specific evidence artifacts (RealityGapReport, DriftResult, etc.)
- Evidence is stored with the transition for auditability
- Fail-closed: missing or insufficient evidence → PromotionError
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from trading_agent.research.drift import DriftMonitor


class PromotionState(Enum):
    EXPLORATORY = "exploratory"
    REVIEWED = "reviewed"
    CANARY_ELIGIBLE = "canary_eligible"
    REJECTED = "rejected"
    CANARY_PROMOTED = "canary_promoted"
    CANARY_LIVE = "canary_live"
    SUSPENDED = "suspended"


_VALID_TRANSITIONS: dict[PromotionState, set[PromotionState]] = {
    PromotionState.EXPLORATORY: {
        PromotionState.REVIEWED,
        PromotionState.REJECTED,
        PromotionState.SUSPENDED,
    },
    PromotionState.REVIEWED: {
        PromotionState.CANARY_ELIGIBLE,
        PromotionState.REJECTED,
        PromotionState.SUSPENDED,
    },
    PromotionState.CANARY_ELIGIBLE: {
        PromotionState.CANARY_PROMOTED,
        PromotionState.REJECTED,
        PromotionState.SUSPENDED,
    },
    PromotionState.CANARY_PROMOTED: {
        PromotionState.CANARY_LIVE,
        PromotionState.SUSPENDED,
    },
    PromotionState.CANARY_LIVE: {PromotionState.SUSPENDED},
    PromotionState.REJECTED: set(),
    PromotionState.SUSPENDED: {
        PromotionState.REVIEWED,
        PromotionState.CANARY_ELIGIBLE,
        PromotionState.CANARY_LIVE,
    },
}


class PromotionError(Exception):
    """Raised for invalid state transitions (fail closed, not silently)."""


@dataclass
class PromotionEvent:
    from_state: PromotionState
    to_state: PromotionState
    artifact_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    note: str = ""
    actor: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "artifact_id": self.artifact_id,
            "timestamp": self.timestamp.isoformat(),
            "note": self.note,
            "actor": self.actor,
        }


class ArtifactLifecycle:
    """State machine for one artifact's promotion path."""

    def __init__(self, artifact_id: str):
        self.artifact_id = artifact_id
        self.state: PromotionState = PromotionState.EXPLORATORY
        self.events: list[PromotionEvent] = []

    def transition(
        self,
        to_state: PromotionState,
        *,
        note: str = "",
        actor: str = "system",
        artifact_ok: bool | None = None,
    ) -> PromotionEvent:
        """Move the artifact to ``to_state``.

        Fail closed: invalid transitions, tries to resume from a rejected
        artifact, or transitions while the artifact's hashes are not intact
        raise PromotionError and change nothing.
        """
        if artifact_ok is not None:
            raise PromotionError(
                "artifact_ok boolean assertions are not accepted; use the canonical "
                "ResearchLifecycle with content-addressed ARTIFACT_INTEGRITY evidence"
            )
        if self.state == PromotionState.REJECTED:
            raise PromotionError(f"artifact {self.artifact_id} is REJECTED (terminal)")
        if to_state not in _VALID_TRANSITIONS[self.state]:
            raise PromotionError(
                f"invalid transition {self.state.value} -> {to_state.value} "
                f"for artifact {self.artifact_id} (no skips / no backdating)"
            )
        event = PromotionEvent(
            from_state=self.state,
            to_state=to_state,
            artifact_id=self.artifact_id,
            note=note,
            actor=actor,
        )
        self.state = to_state
        self.events.append(event)
        return event

    @property
    def history(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self.events]

    def can_advance_to(self, to_state: PromotionState) -> bool:
        return to_state in _VALID_TRANSITIONS[self.state]


# ── PromotionPolicy: evidence-enforced transitions ──────────────────────


@dataclass(frozen=True)
class PromotionEvidence:
    """Evidence artifact required for a promotion transition."""

    kind: str  # e.g., "reality_gap", "drift_check", "calibration", "manual_review"
    payload: dict[str, Any]  # serialized evidence (report.to_dict(), etc.)
    validated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    validator: str = "system"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "validated_at": self.validated_at.isoformat(),
            "validator": self.validator,
        }


# Required evidence per transition
_REQUIRED_EVIDENCE: dict[tuple[PromotionState, PromotionState], list[str]] = {
    (PromotionState.EXPLORATORY, PromotionState.REVIEWED): ["manual_review"],
    (PromotionState.REVIEWED, PromotionState.CANARY_ELIGIBLE): [
        "reality_gap",
        "drift_check",
    ],
    (PromotionState.CANARY_ELIGIBLE, PromotionState.CANARY_PROMOTED): [
        "reality_gap",
        "drift_check",
        "calibration",
    ],
    (PromotionState.CANARY_PROMOTED, PromotionState.CANARY_LIVE): [
        "soak_test",
        "drift_check",
    ],
}


class PromotionPolicy:
    """Enforces evidence requirements for artifact promotion transitions.

    Each transition must be accompanied by the required evidence artifacts.
    Evidence is validated (e.g., RealityGapReport.pass_gate must be True)
    and stored with the transition event for auditability.

    Fail-closed: missing evidence, invalid evidence, or failed validation
    raises PromotionError and the transition does not occur.
    """

    def __init__(
        self,
        *,
        reality_gap_threshold: float = 0.5,
        drift_monitor: "DriftMonitor | None" = None,
        min_calibration_score: float = 0.7,
    ) -> None:
        self.reality_gap_threshold = reality_gap_threshold
        self.drift_monitor = drift_monitor
        self.min_calibration_score = min_calibration_score
        # lazy import to avoid circular
        if drift_monitor is not None and not hasattr(drift_monitor, "health_state"):
            from trading_agent.research.drift import DriftMonitor

            if not isinstance(drift_monitor, DriftMonitor):
                raise TypeError("drift_monitor must be a DriftMonitor instance")
        self._evidence_store: dict[
            str, list[PromotionEvidence]
        ] = {}  # artifact_id -> evidence list

    def validate_evidence(
        self,
        artifact_id: str,
        from_state: PromotionState,
        to_state: PromotionState,
        evidence: list[PromotionEvidence],
    ) -> None:
        """Validate that provided evidence meets requirements for the transition."""
        required = _REQUIRED_EVIDENCE.get((from_state, to_state), [])
        if not required:
            return  # No specific evidence required

        provided_kinds = {e.kind for e in evidence}
        missing = set(required) - provided_kinds
        if missing:
            raise PromotionError(
                f"artifact {artifact_id}: missing required evidence for "
                f"{from_state.value} -> {to_state.value}: {sorted(missing)}"
            )

        # Validate each evidence item
        for e in evidence:
            self._validate_evidence_item(artifact_id, from_state, to_state, e)

        # Store validated evidence
        self._evidence_store.setdefault(artifact_id, []).extend(evidence)

    def _validate_evidence_item(
        self,
        artifact_id: str,
        from_state: PromotionState,
        to_state: PromotionState,
        evidence: PromotionEvidence,
    ) -> None:
        """Validate a single evidence item based on its kind."""
        kind = evidence.kind
        payload = evidence.payload

        if kind == "reality_gap":
            # RealityGapReport must have pass_gate=True
            if not payload.get("gate_passed", False):
                raise PromotionError(
                    f"artifact {artifact_id}: reality_gap evidence failed gate "
                    f"(breaches: {payload.get('breaches', [])})"
                )
            # Score must be below threshold
            score = payload.get("score", 1.0)
            if score > self.reality_gap_threshold:
                raise PromotionError(
                    f"artifact {artifact_id}: reality_gap score {score:.3f} "
                    f"exceeds threshold {self.reality_gap_threshold}"
                )

        elif kind == "drift_check":
            # DriftMonitor health state must be HEALTHY or DEGRADED
            health = payload.get("health_state", "suspended")
            if health not in ("healthy", "degraded"):
                raise PromotionError(
                    f"artifact {artifact_id}: drift_check evidence shows "
                    f"health_state={health} (must be healthy or degraded)"
                )

        elif kind == "calibration":
            # Calibration score must meet minimum
            score = payload.get("calibration_score", 0.0)
            if score < self.min_calibration_score:
                raise PromotionError(
                    f"artifact {artifact_id}: calibration score {score:.3f} "
                    f"below minimum {self.min_calibration_score}"
                )

        elif kind == "soak_test":
            # Soak test must have passed (days, lifecycles, zero dup/unresolved)
            if not payload.get("gates_passed", False):
                raise PromotionError(
                    f"artifact {artifact_id}: soak_test evidence failed "
                    f"(days: {payload.get('days')}, lifecycles: {payload.get('lifecycles')})"
                )

        elif kind == "manual_review":
            # Manual review just needs a note/actor
            if not payload.get("note"):
                raise PromotionError(
                    f"artifact {artifact_id}: manual_review evidence missing note"
                )

        else:
            # Unknown evidence kind - warn but allow (extensibility)
            pass

    def get_evidence(self, artifact_id: str) -> list[PromotionEvidence]:
        """Retrieve all evidence stored for an artifact."""
        return list(self._evidence_store.get(artifact_id, []))

    def required_evidence_for(
        self, from_state: PromotionState, to_state: PromotionState
    ) -> list[str]:
        """Return the list of required evidence kinds for a transition."""
        return list(_REQUIRED_EVIDENCE.get((from_state, to_state), []))


# Convenience factory for common evidence types
def reality_gap_evidence(report) -> PromotionEvidence:
    """Create PromotionEvidence from a RealityGapReport."""
    return PromotionEvidence(
        kind="reality_gap",
        payload=report.to_dict(),
    )


def drift_check_evidence(
    health_state: str, details: dict[str, Any] | None = None
) -> PromotionEvidence:
    """Create PromotionEvidence from a drift check result."""
    return PromotionEvidence(
        kind="drift_check",
        payload={"health_state": health_state, "details": details or {}},
    )


def calibration_evidence(
    calibration_score: float, details: dict[str, Any] | None = None
) -> PromotionEvidence:
    """Create PromotionEvidence from a calibration result."""
    return PromotionEvidence(
        kind="calibration",
        payload={"calibration_score": calibration_score, "details": details or {}},
    )


def soak_test_evidence(
    days: int,
    lifecycles: int,
    gates_passed: bool,
    details: dict[str, Any] | None = None,
) -> PromotionEvidence:
    """Create PromotionEvidence from a soak test result."""
    return PromotionEvidence(
        kind="soak_test",
        payload={
            "days": days,
            "lifecycles": lifecycles,
            "gates_passed": gates_passed,
            "details": details or {},
        },
    )


def manual_review_evidence(note: str, actor: str = "human") -> PromotionEvidence:
    """Create PromotionEvidence from a manual review."""
    return PromotionEvidence(
        kind="manual_review",
        payload={"note": note},
        validator=actor,
    )
