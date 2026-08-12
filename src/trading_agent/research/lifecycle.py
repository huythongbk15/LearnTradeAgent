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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class PromotionState(Enum):
    EXPLORATORY = "exploratory"
    REVIEWED = "reviewed"
    CANARY_ELIGIBLE = "canary_eligible"
    REJECTED = "rejected"
    CANARY_PROMOTED = "canary_promoted"
    CANARY_LIVE = "canary_live"
    SUSPENDED = "suspended"


_VALID_TRANSITIONS: dict[PromotionState, set[PromotionState]] = {
    PromotionState.EXPLORATORY: {PromotionState.REVIEWED, PromotionState.REJECTED, PromotionState.SUSPENDED},
    PromotionState.REVIEWED: {PromotionState.CANARY_ELIGIBLE, PromotionState.REJECTED, PromotionState.SUSPENDED},
    PromotionState.CANARY_ELIGIBLE: {PromotionState.CANARY_PROMOTED, PromotionState.REJECTED, PromotionState.SUSPENDED},
    PromotionState.CANARY_PROMOTED: {PromotionState.CANARY_LIVE, PromotionState.SUSPENDED},
    PromotionState.CANARY_LIVE: {PromotionState.SUSPENDED},
    PromotionState.REJECTED: set(),
    PromotionState.SUSPENDED: {PromotionState.REVIEWED, PromotionState.CANARY_ELIGIBLE, PromotionState.CANARY_LIVE},
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
        artifact_ok: bool = True,
    ) -> PromotionEvent:
        """Move the artifact to ``to_state``.

        Fail closed: invalid transitions, tries to resume from a rejected
        artifact, or transitions while the artifact's hashes are not intact
        raise PromotionError and change nothing.
        """
        if not artifact_ok:
            raise PromotionError(
                f"artifact {self.artifact_id} integrity check failed; "
                "immutable artifacts cannot be promoted with altered hashes"
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