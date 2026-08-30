"""Runtime module decomposition boundary.

Defines the clean interface between:
- proposal generation
- validation
- execution
- monitoring

No cross-module state leakage is allowed across these boundaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from trading_agent.execution.contract import ExecutionResult
    from trading_agent.execution.proposal import ActionProposal


class IProposalGenerator(Protocol):
    """Generates ActionProposal instances from agent decisions."""

    def generate_proposal(self, decision: Any) -> ActionProposal:
        """Create a structured action proposal from a raw decision."""
        ...


class IProposalValidator(Protocol):
    """Validates ActionProposal instances against schema and contracts."""

    def validate(self, proposal: ActionProposal) -> ActionProposal:
        """Return validated proposal or raise ActionProposalValidationError."""
        ...


class IProposalExecutor(Protocol):
    """Executes validated proposals with budget and idempotency enforcement."""

    def execute(self, proposal: ActionProposal) -> ExecutionResult:
        """Execute a validated proposal."""
        ...


class IProposalMonitor(Protocol):
    """Monitors proposal execution outcomes and updates metrics/logs."""

    def record(self, proposal: ActionProposal, result: ExecutionResult) -> None:
        """Record execution outcome for monitoring."""
        ...


# ── Boundary guard ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModuleBoundaryViolation(Exception):
    """Raised when a module boundary is violated."""

    module: str
    violation: str


class BoundaryGuard:
    """Enforces module boundaries at runtime."""

    def __init__(self) -> None:
        self._allowed_imports: dict[str, set[str]] = {
            "proposal": {"proposal", "contract"},
            "validator": {"proposal", "contract"},
            "executor": {"proposal", "contract", "proposal_executor", "boundaries"},
            "monitor": {"proposal", "contract"},
        }

    def check_import(self, caller: str, callee: str) -> None:
        """Check if caller is allowed to import callee."""
        allowed = self._allowed_imports.get(caller, set())
        if callee not in allowed:
            raise ModuleBoundaryViolation(
                module=caller,
                violation=f"Module '{caller}' cannot import '{callee}'. "
                f"Allowed imports: {sorted(allowed)}",
            )


# Global boundary guard
_boundary_guard = BoundaryGuard()


def get_boundary_guard() -> BoundaryGuard:
    """Get the global boundary guard."""
    return _boundary_guard


__all__ = [
    "IProposalGenerator",
    "IProposalValidator",
    "IProposalExecutor",
    "IProposalMonitor",
    "ModuleBoundaryViolation",
    "BoundaryGuard",
    "get_boundary_guard",
]
