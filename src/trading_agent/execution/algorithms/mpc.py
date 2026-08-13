"""MPC feasibility layer — Wave D, spec §15.

This is the *abstraction* for a model-predictive-control style execution
scheduler.  It defines:

* typed objectives — spread cost, impact cost, delay risk, opportunity cost,
  inventory risk;
* typed constraints — remaining qty, deadline, max participation, slippage
  budget, risk limits, market liquidity;
* a deterministic *reference solver* (greedy, closed-form) that either finds
  a feasible slice satisfying every constraint or fails closed with a typed
  infeasibility reason.

The reference solver is deliberately simple: the spec says *"Không cần triển
khai thuật toán quá phức tạp nếu simulator chưa đủ dữ liệu"*.  A real
optimizer can be plugged in behind the same interface later (no RL until the
simulator is calibrated).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from trading_agent.execution.algorithms.base import (
    SliceContext,
    SliceResult,
)


@dataclass(frozen=True)
class MpcPlan:
    """A feasible working plan for a parent order (interface output).

    The reference solver produces one slice per decision; a future optimizer
    may produce a full horizon of slices through the same interface.
    """

    slices: list[float]
    feasible: bool = True
    reason: str = "ok"


@dataclass(frozen=True)
class MpcObjectives:
    """Weights for the five objective terms (all >= 0)."""

    w_spread: float = 1.0
    w_impact: float = 1.0
    w_delay: float = 1.0
    w_opportunity: float = 1.0
    w_inventory: float = 1.0

    def validate(self) -> None:
        for name in ("w_spread", "w_impact", "w_delay", "w_opportunity", "w_inventory"):
            w = getattr(self, name)
            if w < 0:
                raise ValueError(f"{name} must be >= 0, got {w}")


@dataclass(frozen=True)
class MpcConstraints:
    """Constraints every feasible slice must satisfy (fail closed)."""

    max_participation: float  # 0..1 — cap as fraction of observed volume
    slippage_budget_bps: float  # hard cap on accumulated slippage
    deadline_bars: int  # parent deadline in bars
    risk_limit_qty: float = math.inf  # max quantity per slice from risk limits
    min_liquidity: float = 0.0  # min required depth (same side) for a slice

    def validate(self) -> None:
        if not 0 < self.max_participation <= 1:
            raise ValueError(
                f"max_participation must be in (0, 1], got {self.max_participation}"
            )
        if self.slippage_budget_bps < 0:
            raise ValueError(
                f"slippage_budget_bps must be >= 0, got {self.slippage_budget_bps}"
            )
        if self.deadline_bars <= 0:
            raise ValueError(f"deadline_bars must be > 0, got {self.deadline_bars}")
        if self.risk_limit_qty <= 0:
            raise ValueError(f"risk_limit_qty must be > 0, got {self.risk_limit_qty}")
        if self.min_liquidity < 0:
            raise ValueError(f"min_liquidity must be >= 0, got {self.min_liquidity}")


@dataclass(frozen=True)
class ObjectiveCosts:
    """The five objective components for a candidate slice (in bps)."""

    spread_cost_bps: float = 0.0
    impact_cost_bps: float = 0.0
    delay_cost_bps: float = 0.0
    opportunity_cost_bps: float = 0.0
    inventory_risk_bps: float = 0.0

    @property
    def total(self) -> float:
        return (
            self.spread_cost_bps
            + self.impact_cost_bps
            + self.delay_cost_bps
            + self.opportunity_cost_bps
            + self.inventory_risk_bps
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "spread_cost_bps": self.spread_cost_bps,
            "impact_cost_bps": self.impact_cost_bps,
            "delay_cost_bps": self.delay_cost_bps,
            "opportunity_cost_bps": self.opportunity_cost_bps,
            "inventory_risk_bps": self.inventory_risk_bps,
            "total_bps": self.total,
        }


@dataclass(frozen=True)
class MpcResult:
    """Outcome of the feasibility layer for the next slice."""

    feasible: bool
    slice_qty: float
    reason: str  # "ok" | "done" | "INFEASIBLE_*"
    costs: ObjectiveCosts = ObjectiveCosts()

    @property
    def has_slice(self) -> bool:
        return self.feasible and self.slice_qty > 0


def objective_cost(
    ctx: SliceContext,
    slice_qty: float,
    objectives: MpcObjectives,
    *,
    est_impact_coeff: float = 1.0,
) -> ObjectiveCosts:
    """Compute the five objective components for a candidate slice.

    Deterministic, closed-form proxies — documented as such.  They are
    *interface* inputs for a future optimizer, not calibrated costs.
    """
    objectives.validate()
    snap = ctx.snapshot

    spread_cost = snap.spread_bps / 2.0 if slice_qty > 0 else 0.0

    depth = snap.depth_for_side(ctx.is_buy)
    participation = slice_qty / max(depth, 1e-12) if slice_qty > 0 else 0.0
    impact_cost = (
        est_impact_coeff * snap.volatility_bps * math.sqrt(max(participation, 0.0))
        if slice_qty > 0
        else 0.0
    )

    # Delay risk: opportunity to move against us while we wait.
    remaining_time = max(ctx.remaining_bars, 0)
    delay_cost = snap.volatility_bps * math.sqrt(remaining_time + 1e-9)

    # Opportunity cost: cost of not having the position yet.
    opportunity_cost = ctx.participation_remaining * snap.volatility_bps

    # Inventory risk: exposure from the quantity still to trade (for a buy,
    # the risk of holding the position; symmetric proxy).
    inventory_risk = (
        (ctx.filled_qty + slice_qty) / max(ctx.filled_qty + ctx.remaining_qty, 1e-12)
    ) * snap.volatility_bps

    return ObjectiveCosts(
        spread_cost_bps=objectives.w_spread * spread_cost,
        impact_cost_bps=objectives.w_impact * impact_cost,
        delay_cost_bps=objectives.w_delay * delay_cost,
        opportunity_cost_bps=objectives.w_opportunity * opportunity_cost,
        inventory_risk_bps=objectives.w_inventory * inventory_risk,
    )


class MpcFeasibilityLayer:
    """Deterministic reference solver for the MPC abstraction.

    ``plan`` returns a feasible slice (satisfying every constraint) or a
    fail-closed ``INFEASIBLE_*`` result.
    """

    def __init__(self, *, min_slice_qty: float = 0.0) -> None:
        if min_slice_qty < 0:
            raise ValueError(f"min_slice_qty must be >= 0, got {min_slice_qty}")
        self.min_slice_qty = min_slice_qty

    def plan(
        self,
        ctx: SliceContext,
        constraints: MpcConstraints,
        objectives: MpcObjectives | None = None,
    ) -> MpcResult:
        ctx.validate()
        constraints.validate()
        objectives = objectives or MpcObjectives()
        objectives.validate()

        if ctx.remaining_qty <= 0:
            return MpcResult(True, 0.0, "done")

        if ctx.elapsed_bars >= constraints.deadline_bars:
            return MpcResult(False, 0.0, "INFEASIBLE_DEADLINE")

        snap = ctx.snapshot
        remaining_bars = constraints.deadline_bars - ctx.elapsed_bars

        # Constraint: market liquidity.
        depth = snap.depth_for_side(ctx.is_buy)
        if constraints.min_liquidity > 0 and depth < constraints.min_liquidity:
            return MpcResult(False, 0.0, "INFEASIBLE_LIQUIDITY")

        # Constraint: participation (deadline must be reachable within cap).
        if snap.recent_volume > 0:
            required_participation = (
                ctx.remaining_qty / remaining_bars / snap.recent_volume
            )
            if required_participation > constraints.max_participation:
                return MpcResult(False, 0.0, "INFEASIBLE_PARTICIPATION")
        else:
            return MpcResult(False, 0.0, "INFEASIBLE_LIQUIDITY")

        # Candidate: TWAP rate, capped by participation and risk limit.
        twap_rate = ctx.remaining_qty / remaining_bars
        candidate = min(
            twap_rate,
            constraints.max_participation * snap.recent_volume,
            constraints.risk_limit_qty,
        )

        # Constraint: slippage budget.  Shrink until the estimated cost fits.
        if constraints.slippage_budget_bps > 0 and snap.mid > 0:
            est_cost_bps = snap.spread_bps / 2.0 + 1.0
            budget_qty = (
                max(0.0, constraints.slippage_budget_bps - ctx.slippage_paid_bps)
                / est_cost_bps
                * snap.mid
                / max(snap.mid, 1e-12)
            )
            candidate = min(candidate, budget_qty)

        candidate = min(candidate, ctx.remaining_qty)

        if candidate < self.min_slice_qty:
            if self.min_slice_qty > 0 and candidate > 0:
                # Too small to be a meaningful slice — refuse rather than
                # chip with dust (fail closed).
                return MpcResult(False, 0.0, "INFEASIBLE_MIN_SLICE")
            return MpcResult(False, 0.0, "INFEASIBLE_BUDGET")

        if candidate <= 0:
            return MpcResult(False, 0.0, "INFEASIBLE_BUDGET")

        costs = objective_cost(ctx, candidate, objectives)
        return MpcResult(True, round(candidate, 10), "ok", costs=costs)


def slice_result_from_mpc(result: MpcResult) -> SliceResult:
    """Adapt an MpcResult to the base SliceResult shape."""
    if not result.feasible:
        return SliceResult(0.0, reason=result.reason)
    return SliceResult(result.slice_qty, reason=result.reason)
