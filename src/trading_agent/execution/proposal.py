"""Structured action proposal schema and validation.

This module defines the canonical ActionProposal dataclass, ActionType enum,
and validation utilities for agent-generated action proposals.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError


# ── ActionType ──────────────────────────────────────────────────────────


class ActionType(str, Enum):
    """Explicit action types for the trading agent execution contract."""

    OBSERVE = "observe"
    PROPOSE = "propose"
    EXECUTE = "execute"
    CANCEL = "cancel"
    HOLD = "hold"
    REDUCE = "reduce"
    INCREASE = "increase"
    CLOSE = "close"
    MODIFY = "modify"
    NOOP = "noop"

    @property
    def is_mutable(self) -> bool:
        """True if this action type mutates state (writes/destructive)."""
        return self in {
            ActionType.EXECUTE,
            ActionType.CANCEL,
            ActionType.CLOSE,
            ActionType.MODIFY,
            ActionType.REDUCE,
            ActionType.INCREASE,
        }

    @property
    def is_destructive(self) -> bool:
        """True if this action type is destructive (cannot be safely retried)."""
        return self in {
            ActionType.CANCEL,
            ActionType.CLOSE,
            ActionType.MODIFY,
        }

    @property
    def budget_category(self) -> str:
        """Budget category for enforcement."""
        if not self.is_mutable:
            return "read_only"
        if self.is_destructive:
            return "destructive"
        return "write"


# ── ActionProposal schema ───────────────────────────────────────────────


@dataclass(frozen=True)
class ActionProposal:
    """Structured action proposal from agent to execution layer.

    All fields are required unless marked optional. Proposals must be
    validated against this schema before execution.
    """

    action_type: ActionType
    symbol: str
    params: dict[str, Any]
    budget: dict[str, Any]
    idempotency_key: str
    context_delta: dict[str, Any]
    justification: str
    proposed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    proposal_id: str = ""
    parent_proposal_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.proposal_id:
            object.__setattr__(
                self,
                "proposal_id",
                self._compute_proposal_id(),
            )

    def _compute_proposal_id(self) -> str:
        payload = {
            "action_type": self.action_type.value,
            "symbol": self.symbol,
            "params": self.params,
            "budget": self.budget,
            "idempotency_key": self.idempotency_key,
            "proposed_at": self.proposed_at.isoformat(),
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()[:32]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type.value,
            "symbol": self.symbol,
            "params": self.params,
            "budget": self.budget,
            "idempotency_key": self.idempotency_key,
            "context_delta": self.context_delta,
            "justification": self.justification,
            "proposed_at": self.proposed_at.isoformat(),
            "proposal_id": self.proposal_id,
            "parent_proposal_id": self.parent_proposal_id,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ActionProposal:
        return cls(
            action_type=ActionType(d["action_type"]),
            symbol=d["symbol"],
            params=d.get("params", {}),
            budget=d.get("budget", {}),
            idempotency_key=d["idempotency_key"],
            context_delta=d.get("context_delta", {}),
            justification=d.get("justification", ""),
            proposed_at=datetime.fromisoformat(d["proposed_at"])
            if d.get("proposed_at")
            else datetime.now(UTC),
            proposal_id=d.get("proposal_id", ""),
            parent_proposal_id=d.get("parent_proposal_id"),
            metadata=d.get("metadata", {}),
        )


# ── Pydantic models for validated structured output ─────────────────────


class ActionProposalModel(BaseModel):
    """Pydantic model for validated LLM structured output."""

    action_type: str = Field(..., description="One of: observe, propose, execute, cancel, hold, reduce, increase, close, modify, noop")
    symbol: str = Field(..., description="Trading symbol, e.g. BTC/USDT")
    params: dict[str, Any] = Field(default_factory=dict, description="Action-specific parameters")
    budget: dict[str, Any] = Field(default_factory=dict, description="Budget envelope for this action")
    idempotency_key: str = Field(..., description="Deterministic idempotency key for deduplication")
    context_delta: dict[str, Any] = Field(default_factory=dict, description="Context changes that prompted this proposal")
    justification: str = Field(..., description="Human-readable justification for this action")

    def to_canonical(self) -> ActionProposal:
        """Convert validated model to canonical ActionProposal dataclass."""
        return ActionProposal(
            action_type=ActionType(self.action_type),
            symbol=self.symbol,
            params=self.params,
            budget=self.budget,
            idempotency_key=self.idempotency_key,
            context_delta=self.context_delta,
            justification=self.justification,
        )


# ── Validation ──────────────────────────────────────────────────────────


class ActionProposalValidationError(Exception):
    """Raised when an ActionProposal fails validation."""
    pass


def validate_action_proposal(data: Mapping[str, Any]) -> ActionProposal:
    """Validate raw dict data against ActionProposal schema.

    Returns a validated ActionProposal dataclass.
    Raises ActionProposalValidationError if validation fails.
    """
    try:
        model = ActionProposalModel.model_validate(dict(data))
        proposal = model.to_canonical()
        # Additional semantic checks
        if not proposal.symbol:
            raise ActionProposalValidationError("symbol must be non-empty")
        if not proposal.justification:
            raise ActionProposalValidationError("justification must be non-empty")
        if not proposal.idempotency_key:
            raise ActionProposalValidationError("idempotency_key must be non-empty")
        # Budget must contain category matching action_type
        budget_cat = proposal.budget.get("category")
        expected_cat = proposal.action_type.budget_category
        if budget_cat and budget_cat != expected_cat:
            raise ActionProposalValidationError(
                f"budget category '{budget_cat}' does not match action_type '{proposal.action_type.value}' "
                f"(expected '{expected_cat}')"
            )
        return proposal
    except PydanticValidationError as exc:
        raise ActionProposalValidationError(
            f"Pydantic validation failed: {exc.errors()}"
        ) from exc
    except (ValueError, KeyError) as exc:
        raise ActionProposalValidationError(str(exc)) from exc


def validate_structured_output(raw_output: str) -> ActionProposal:
    """Validate raw LLM string output as JSON against ActionProposal schema.

    Returns validated ActionProposal.
    Raises ActionProposalValidationError if parsing or validation fails.
    """
    try:
        data = json.loads(raw_output)
    except json.JSONDecodeError as exc:
        raise ActionProposalValidationError(
            f"Failed to parse JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise ActionProposalValidationError(
            f"Expected JSON object, got {type(data).__name__}"
        )
    return validate_action_proposal(data)


# ── IdempotencyKey enforcement ─────────────────────────────────────────


def enforce_idempotency_key(proposal: ActionProposal) -> None:
    """Enforce that proposal carries a valid idempotency key.

    Raises ActionProposalValidationError if key is missing or malformed.
    """
    if not proposal.idempotency_key:
        raise ActionProposalValidationError("idempotency_key is required")
    if not isinstance(proposal.idempotency_key, str):
        raise ActionProposalValidationError("idempotency_key must be a string")
    if len(proposal.idempotency_key) < 16:
        raise ActionProposalValidationError(
            "idempotency_key must be at least 16 characters"
        )


# ── Context delta helpers ───────────────────────────────────────────────


def compute_context_delta(
    previous_context: Mapping[str, Any],
    current_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Compute the delta between two context snapshots.

    Returns a dict of changed keys with old/new values.
    """
    delta: dict[str, Any] = {}
    all_keys = set(previous_context) | set(current_context)
    for key in all_keys:
        old_val = previous_context.get(key)
        new_val = current_context.get(key)
        if old_val != new_val:
            delta[key] = {"old": old_val, "new": new_val}
    return delta


def is_context_stale(
    proposal_context_delta: Mapping[str, Any],
    current_context: Mapping[str, Any],
    max_staleness_bars: int = 3,
) -> bool:
    """Check if a proposal's context is stale relative to current context.

    A proposal is stale if the current context has changed beyond the
    proposal's context_delta by more than max_staleness_bars.
    """
    # Simple implementation: if any current context key is not in the
    # proposal's context_delta, the proposal may be stale.
    for key in current_context:
        if key not in proposal_context_delta:
            return True
    return False


__all__ = [
    "ActionType",
    "ActionProposal",
    "ActionProposalModel",
    "ActionProposalValidationError",
    "validate_action_proposal",
    "validate_structured_output",
    "enforce_idempotency_key",
    "compute_context_delta",
    "is_context_stale",
]
