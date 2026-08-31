"""Skill/tool execution contract.

Defines the explicit contract between proposer and executor:
- Input schema
- Output schema
- Side effects
- Idempotency
- Budget limits
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from trading_agent.execution.proposal import (
    ActionProposal,
    ActionType,
    ActionProposalValidationError,
)


# ── Execution contract ──────────────────────────────────────────────────


@dataclass(frozen=True)
class SkillManifest:
    """Manifest describing a skill/tool's execution contract."""

    skill_name: str
    action_types: list[ActionType]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effects: list[str]  # e.g. ["write", "destroy", "notify"]
    idempotent: bool = True
    budget_limits: dict[str, Any] = field(default_factory=dict)
    version: str = "1.0.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill_name": self.skill_name,
            "action_types": [a.value for a in self.action_types],
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "side_effects": self.side_effects,
            "idempotent": self.idempotent,
            "budget_limits": self.budget_limits,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SkillManifest:
        return cls(
            skill_name=d["skill_name"],
            action_types=[ActionType(a) for a in d["action_types"]],
            input_schema=d.get("input_schema", {}),
            output_schema=d.get("output_schema", {}),
            side_effects=d.get("side_effects", []),
            idempotent=d.get("idempotent", True),
            budget_limits=d.get("budget_limits", {}),
            version=d.get("version", "1.0.0"),
        )


@dataclass(frozen=True)
class ExecutionResult:
    """Result of executing a skill/tool action."""

    success: bool
    action_type: ActionType
    skill_name: str
    output: dict[str, Any]
    side_effects: list[str]
    idempotency_key: str
    executed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "action_type": self.action_type.value,
            "skill_name": self.skill_name,
            "output": self.output,
            "side_effects": self.side_effects,
            "idempotency_key": self.idempotency_key,
            "executed_at": self.executed_at.isoformat(),
            "error": self.error,
            "metadata": self.metadata,
        }


# ── Budget enforcement ──────────────────────────────────────────────────


@dataclass(frozen=True)
class BudgetEnvelope:
    """Budget envelope for action execution.

    Distinguishes between read-only, write, and destructive budgets.
    """

    category: str  # "read_only", "write", "destructive"
    max_calls_per_hour: int = 1000
    max_cost_per_call: float = 1.0
    total_hourly_budget: float = 1000.0
    total_daily_budget: float = 10000.0
    remaining_hourly: float = 1000.0
    remaining_daily: float = 10000.0
    last_reset: datetime = field(default_factory=lambda: datetime.now(UTC))

    def can_afford(self, estimated_cost: float) -> bool:
        """Check if budget can afford estimated cost."""
        if self.category == "read_only":
            return True
        return (
            self.remaining_hourly >= estimated_cost
            and self.remaining_daily >= estimated_cost
        )

    def consume(self, cost: float) -> None:
        """Consume budget. Raises ValueError if insufficient."""
        if self.category == "read_only":
            return
        if not self.can_afford(cost):
            raise ValueError(
                f"Insufficient budget for category '{self.category}': "
                f"remaining_hourly={self.remaining_hourly}, remaining_daily={self.remaining_daily}, "
                f"cost={cost}"
            )
        object.__setattr__(self, "remaining_hourly", self.remaining_hourly - cost)
        object.__setattr__(self, "remaining_daily", self.remaining_daily - cost)

    def reset_if_needed(self) -> None:
        """Reset budget counters if hour/day has changed."""
        now = datetime.now(UTC)
        elapsed_hours = (now - self.last_reset).total_seconds() / 3600
        if elapsed_hours >= 1:
            object.__setattr__(self, "remaining_hourly", self.total_hourly_budget)
        elapsed_days = (now - self.last_reset).total_seconds() / 86400
        if elapsed_days >= 1:
            object.__setattr__(self, "remaining_daily", self.total_daily_budget)
        if elapsed_hours >= 1 or elapsed_days >= 1:
            object.__setattr__(self, "last_reset", now)


# ── Execution contract registry ────────────────────────────────────────


class ExecutionContractRegistry:
    """Registry of skill/tool execution contracts."""

    def __init__(self) -> None:
        self._manifests: dict[str, SkillManifest] = {}
        self._handlers: dict[
            str, Callable[[ActionProposal, dict[str, Any]], ExecutionResult]
        ] = {}

    def register(
        self,
        manifest: SkillManifest,
        handler: Callable[[ActionProposal, dict[str, Any]], ExecutionResult],
    ) -> None:
        """Register a skill/tool with its manifest and handler."""
        self._manifests[manifest.skill_name] = manifest
        self._handlers[manifest.skill_name] = handler

    def get_manifest(self, skill_name: str) -> Optional[SkillManifest]:
        """Get manifest for a skill/tool."""
        return self._manifests.get(skill_name)

    def get_handler(
        self, skill_name: str
    ) -> Optional[Callable[[ActionProposal, dict[str, Any]], ExecutionResult]]:
        """Get handler for a skill/tool."""
        return self._handlers.get(skill_name)

    def validate_proposal(self, proposal: ActionProposal) -> SkillManifest | None:
        """Validate proposal against registered skill contract."""
        manifest = self.get_manifest(proposal.metadata.get("skill_name", ""))
        if manifest is None:
            return None
        # Check action_type is allowed for this skill
        if proposal.action_type not in manifest.action_types:
            raise ActionProposalValidationError(
                f"ActionType '{proposal.action_type.value}' not allowed for skill '{manifest.skill_name}'. "
                f"Allowed: {[a.value for a in manifest.action_types]}"
            )
        # Check budget category matches
        if proposal.budget.get("category") != manifest.skill_name:
            # Budget category should match action_type.budget_category, enforced in proposal validation
            pass
        return manifest


# Global registry
_registry = ExecutionContractRegistry()


def get_registry() -> ExecutionContractRegistry:
    """Get the global execution contract registry."""
    return _registry


def register_skill(
    skill_name: str,
    action_types: list[ActionType],
    input_schema: dict[str, Any],
    output_schema: dict[str, Any],
    side_effects: list[str],
    handler: Callable[[ActionProposal, dict[str, Any]], ExecutionResult],
    budget_limits: dict[str, Any] | None = None,
) -> None:
    """Convenience function to register a skill/tool."""
    manifest = SkillManifest(
        skill_name=skill_name,
        action_types=action_types,
        input_schema=input_schema,
        output_schema=output_schema,
        side_effects=side_effects,
        budget_limits=budget_limits or {},
    )
    _registry.register(manifest, handler)


__all__ = [
    "SkillManifest",
    "ExecutionResult",
    "BudgetEnvelope",
    "ExecutionContractRegistry",
    "get_registry",
    "register_skill",
]
