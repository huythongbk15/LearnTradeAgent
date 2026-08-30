"""Proposal execution integration.

Wires ActionProposal into the existing execution engine with:
- budget category enforcement (read_only / write / destructive)
- context delta / staleness checks
- module boundary guard between proposal validation and execution
"""

from __future__ import annotations

from typing import Any

from trading_agent.execution.contract import (
    BudgetEnvelope,
    ExecutionResult,
    SkillManifest,
    get_registry,
)
from trading_agent.execution.proposal import (
    ActionProposal,
    ActionProposalValidationError,
    is_context_stale,
    validate_action_proposal,
)


class ProposalExecutionContext:
    """Context for executing proposals with budget and staleness enforcement."""

    def __init__(
        self,
        *,
        read_only_budget: BudgetEnvelope | None = None,
        write_budget: BudgetEnvelope | None = None,
        destructive_budget: BudgetEnvelope | None = None,
        max_staleness_bars: int = 3,
    ) -> None:
        self.read_only_budget = read_only_budget or BudgetEnvelope(
            category="read_only",
            max_calls_per_hour=10000,
            max_cost_per_call=0.0,
            total_hourly_budget=0.0,
            total_daily_budget=0.0,
            remaining_hourly=0.0,
            remaining_daily=0.0,
        )
        self.write_budget = write_budget or BudgetEnvelope(category="write")
        self.destructive_budget = destructive_budget or BudgetEnvelope(
            category="destructive"
        )
        self.max_staleness_bars = max_staleness_bars
        self._registry = get_registry()
        self._last_context: dict[str, Any] = {}
        self._executed_keys: set[str] = set()

    def _budget_for(self, proposal: ActionProposal) -> BudgetEnvelope:
        category = proposal.action_type.budget_category
        if category == "read_only":
            return self.read_only_budget
        if category == "destructive":
            return self.destructive_budget
        return self.write_budget

    def validate(self, proposal: ActionProposal) -> SkillManifest | None:
        """Validate proposal against schema and registered contract."""
        # Validate original proposal directly to preserve metadata.
        validate_action_proposal(proposal.to_dict())
        return self._registry.validate_proposal(proposal)

    def enforce_budget(self, proposal: ActionProposal, estimated_cost: float = 1.0) -> None:
        """Enforce budget limits for the proposal's category."""
        budget = self._budget_for(proposal)
        budget.reset_if_needed()
        if not budget.can_afford(estimated_cost):
            raise ActionProposalValidationError(
                f"Budget exceeded for category '{budget.category}': "
                f"remaining_hourly={budget.remaining_hourly}, "
                f"remaining_daily={budget.remaining_daily}"
            )
        budget.consume(estimated_cost)

    def check_staleness(
        self,
        proposal: ActionProposal,
        current_context: dict[str, Any],
    ) -> None:
        """Raise if proposal context is stale relative to current context."""
        if is_context_stale(
            proposal.context_delta,
            current_context,
            max_staleness_bars=self.max_staleness_bars,
        ):
            raise ActionProposalValidationError(
                "Proposal context is stale; proposal invalidated by context change"
            )

    def check_idempotency(self, proposal: ActionProposal) -> None:
        """Reject duplicate proposals by idempotency key."""
        if proposal.idempotency_key in self._executed_keys:
            raise ActionProposalValidationError(
                f"Duplicate proposal idempotency_key={proposal.idempotency_key}"
            )

    def record_execution(self, proposal: ActionProposal) -> None:
        """Record proposal as executed for idempotency deduplication."""
        self._executed_keys.add(proposal.idempotency_key)

    def update_context(self, context: dict[str, Any]) -> None:
        """Update the latest context snapshot."""
        self._last_context = dict(context)

    def execute(
        self,
        proposal: ActionProposal,
        handler_kwargs: dict[str, Any] | None = None,
    ) -> ExecutionResult:
        """Validate, enforce budgets/staleness/idempotency, then execute."""
        handler_kwargs = handler_kwargs or {}
        manifest = self.validate(proposal)
        if manifest is None:
            raise ActionProposalValidationError(
                "No registered skill manifest for proposal"
            )
        self.check_idempotency(proposal)
        self.enforce_budget(proposal)
        self.check_staleness(proposal, self._last_context)
        handler = self._registry.get_handler(manifest.skill_name)
        if handler is None:
            raise ActionProposalValidationError(
                f"No handler registered for skill '{manifest.skill_name}'"
            )
        result = handler(proposal, handler_kwargs)
        self.record_execution(proposal)
        return result


def execute_proposal(
    proposal: ActionProposal,
    *,
    context: dict[str, Any] | None = None,
    estimated_cost: float = 1.0,
) -> ExecutionResult:
    """Convenience function to execute a proposal with default context."""
    ctx = ProposalExecutionContext()
    if context:
        ctx.update_context(context)
    return ctx.execute(proposal)


__all__ = [
    "ProposalExecutionContext",
    "execute_proposal",
]
