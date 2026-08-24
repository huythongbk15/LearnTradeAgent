"""
PortfolioAllocator & PositionSizer — Multi-pair capital allocation layer.

This is the FOURTH authority in the chain (for multi-pair). It sits ABOVE
individual ExecutionAuthorities and distributes risk budget across strategies×symbols.

Responsibilities:
1. Allocate risk budget per (strategy, symbol) from total portfolio risk budget
2. Apply correlation adjustments (reduce allocation to correlated clusters)
3. Convert allocation → target_exposure_pct (PositionSizer)
4. Enforce portfolio-level caps (single source of truth)
5. Emit causation links for audit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from trading_agent.authority.causation import CausationChain, new_chain
from trading_agent.authority.config import (
    AuthorityConfig,
    ExposureConfig,
    get_authority_config,
)
from trading_agent.authority.decision import TargetExposure

logger = logging.getLogger(__name__)


# ── Types ───────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class AllocationRequest:
    """Request for capital allocation."""

    strategy_id: str
    symbol: str
    risk_decision: Any  # UnifiedRiskDecision (avoid circular import)
    current_exposure: float
    equity: float
    available_cash: float
    portfolio_exposure: float
    correlation_cluster: str | None = None  # e.g., "BTC_ETH", "MAJORS"
    causation_chain: CausationChain | None = None


@dataclass(frozen=True, slots=True)
class AllocationResult:
    """Result of capital allocation."""

    target_exposure: TargetExposure
    allocation_pct: float  # Fraction of risk budget allocated
    causation_chain: CausationChain
    reason: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=False, slots=True)
class StrategyBudget:
    """Risk budget for a strategy."""

    strategy_id: str
    max_exposure: float  # Max exposure for this strategy
    current_exposure: float
    allocated_exposure: float  # Sum of all symbol allocations
    symbols: dict[str, float]  # symbol -> allocated exposure


# ── PositionSizer ───────────────────────────────────────────────────────


class PositionSizer:
    """
    Converts risk allocation → target_exposure_pct.

    Supports multiple sizing methods:
    - fixed_fraction: Fixed fraction of equity
    - kelly: Kelly criterion (requires win_rate, avg_win/avg_loss)
    - vol_target: Volatility targeting
    - risk_parity: Equal risk contribution
    """

    def __init__(self, config: AuthorityConfig | None = None):
        self.config = config or get_authority_config()

    def size(
        self,
        *,
        allocation_pct: float,
        equity: float,
        available_cash: float,
        current_exposure: float,
        risk_decision: Any,  # UnifiedRiskDecision
        instrument_rules: Any,  # InstrumentRules
        method: str = "vol_target",
        **method_kwargs,
    ) -> TargetExposure:
        """Compute target exposure from allocation."""

        # Base target from allocation
        base_target = allocation_pct

        # Apply risk decision caps
        target = min(base_target, risk_decision.allowed_target_exposure)
        max_new = min(base_target, risk_decision.max_new_exposure)

        # Reduce-only override
        if risk_decision.reduce_only:
            target = 0.0
            max_new = 0.0

        # Method-specific adjustments
        if method == "vol_target":
            target = self._vol_target_sizing(target, risk_decision, **method_kwargs)
        elif method == "kelly":
            target = self._kelly_sizing(target, risk_decision, **method_kwargs)
        elif method == "fixed_fraction":
            target = self._fixed_fraction_sizing(target, **method_kwargs)

        # Instrument rules caps
        target = min(
            target, instrument_rules.max_notional / equity if equity > 0 else 0.0
        )
        target = min(target, available_cash / equity if equity > 0 else 0.0)

        # Min notional floor
        min_notional_pct = instrument_rules.min_notional / equity if equity > 0 else 0.0
        if target > 0 and target < min_notional_pct:
            target = 0.0
            max_new = 0.0

        return TargetExposure(
            target_exposure_pct=target,
            max_new_exposure_pct=min(max_new, target),
            reduce_only=risk_decision.reduce_only,
            confidence=0.5,
            authority_chain=(),
        )

    def _vol_target_sizing(
        self,
        base_target: float,
        risk_decision: Any,
        target_vol: float = 0.15,
        current_vol: float | None = None,
    ) -> float:
        """Volatility targeting: scale position to target portfolio vol."""
        if current_vol is None or current_vol <= 0:
            return base_target

        # Scale inversely with current volatility
        vol_scale = target_vol / current_vol
        vol_scale = max(0.25, min(2.0, vol_scale))  # Clamp 0.25x to 2x
        return base_target * vol_scale

    def _kelly_sizing(
        self,
        base_target: float,
        risk_decision: Any,
        win_rate: float | None = None,
        avg_win: float | None = None,
        avg_loss: float | None = None,
    ) -> float:
        """Kelly criterion sizing (fractional Kelly for safety)."""
        if win_rate is None or avg_win is None or avg_loss is None:
            return base_target

        if avg_loss <= 0:
            return base_target

        kelly_fraction = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        kelly_fraction = max(0.0, min(1.0, kelly_fraction))

        # Use half-Kelly for safety
        return base_target * (kelly_fraction * 0.5)

    def _fixed_fraction_sizing(
        self, base_target: float, fraction: float = 1.0
    ) -> float:
        """Fixed fraction of allocated budget."""
        return base_target * fraction


# ── PortfolioAllocator ──────────────────────────────────────────────────


class PortfolioAllocator:
    """
    Allocates portfolio risk budget across strategies and symbols.

    Single source of truth for portfolio-level exposure caps.
    All symbol-level ExposureAuthorities must respect these allocations.
    """

    def __init__(self, config: AuthorityConfig | None = None):
        self.config = config or get_authority_config()
        self.exposure_config: ExposureConfig = self.config.exposure
        self.position_sizer = PositionSizer(config)
        self._strategy_budgets: dict[str, StrategyBudget] = {}

    def allocate(self, request: AllocationRequest) -> AllocationResult:
        """
        Allocate risk budget for a (strategy, symbol) pair.

        Returns TargetExposure with allocation embedded in causation chain.
        """

        chain = request.causation_chain or new_chain(
            {
                "authority": "PortfolioAllocator",
                "strategy_id": request.strategy_id,
                "symbol": request.symbol,
            }
        )

        try:
            # Get or create strategy budget
            budget = self._get_or_create_budget(request.strategy_id, request)

            # Compute available budget for this strategy
            strategy_budget_available = budget.max_exposure - budget.allocated_exposure

            # Portfolio-level available budget
            portfolio_available = (
                self.exposure_config.max_portfolio_exposure - request.portfolio_exposure
            )

            # Correlation cluster adjustment
            cluster_adjustment = self._cluster_adjustment(request.correlation_cluster)
            strategy_budget_available *= cluster_adjustment
            portfolio_available *= cluster_adjustment

            # Requested allocation from risk decision
            requested = request.risk_decision.allowed_target_exposure
            requested = min(requested, request.risk_decision.max_new_exposure)

            # Final allocation = min(requested, strategy_budget, portfolio_budget)
            allocation = min(requested, strategy_budget_available, portfolio_available)
            allocation = max(0.0, allocation)

            # Symbol-level cap
            allocation = min(
                allocation, self.exposure_config.max_single_symbol_exposure
            )

            # Cash availability
            max_by_cash = (
                request.available_cash / request.equity if request.equity > 0 else 0.0
            )
            allocation = min(allocation, max_by_cash)

            # Convert to TargetExposure via PositionSizer
            # Note: instrument_rules needed — caller should provide
            # For now, create basic TargetExposure
            target = TargetExposure(
                target_exposure_pct=allocation,
                max_new_exposure_pct=min(
                    allocation, request.risk_decision.max_new_exposure
                ),
                reduce_only=request.risk_decision.reduce_only,
                confidence=0.5,
                authority_chain=(),
            )

            # Update budget tracking
            if allocation > 0:
                budget.allocated_exposure += allocation
                budget.symbols[request.symbol] = (
                    budget.symbols.get(request.symbol, 0.0) + allocation
                )

            # Causation chain
            chain = chain.append(
                authority="PortfolioAllocator",
                inputs={
                    "strategy_id": request.strategy_id,
                    "symbol": request.symbol,
                    "requested_allocation": requested,
                    "strategy_budget_available": strategy_budget_available,
                    "portfolio_available": portfolio_available,
                    "cluster_adjustment": cluster_adjustment,
                },
                outputs={
                    "allocated": allocation,
                    "allocation_pct": allocation
                    / self.exposure_config.max_portfolio_exposure
                    if self.exposure_config.max_portfolio_exposure > 0
                    else 0.0,
                },
            )

            return AllocationResult(
                target_exposure=target,
                allocation_pct=allocation,
                causation_chain=chain,
                reason="Allocation computed",
                warnings=(),
            )

        except Exception as e:
            logger.error(f"PortfolioAllocator failed: {e}", exc_info=True)
            chain = chain.append(
                authority="PortfolioAllocator",
                inputs={"strategy_id": request.strategy_id, "symbol": request.symbol},
                outputs={"error": str(e)},
                metadata={"error": True},
            )
            return AllocationResult(
                target_exposure=TargetExposure(
                    target_exposure_pct=0.0,
                    max_new_exposure_pct=0.0,
                    reduce_only=True,
                    confidence=0.0,
                    authority_chain=chain.links,
                ),
                allocation_pct=0.0,
                causation_chain=chain,
                reason=f"Allocation failed: {e}",
                warnings=("allocation_error",),
            )

    def _get_or_create_budget(
        self, strategy_id: str, request: AllocationRequest
    ) -> StrategyBudget:
        """Get or create strategy budget."""
        if strategy_id not in self._strategy_budgets:
            self._strategy_budgets[strategy_id] = StrategyBudget(
                strategy_id=strategy_id,
                max_exposure=self.exposure_config.max_single_strategy_exposure,
                current_exposure=request.current_exposure,
                allocated_exposure=0.0,
                symbols={},
            )
        return self._strategy_budgets[strategy_id]

    def _cluster_adjustment(self, cluster: str | None) -> float:
        """Adjust allocation based on correlation cluster."""
        if cluster is None:
            return 1.0

        # Known clusters and their max allocations
        cluster_caps = {
            "BTC_ETH": 0.6,  # BTC+ETH max 60% of portfolio
            "MAJORS": 0.8,  # BTC, ETH, BNB, SOL, XRP
            "DEFI": 0.4,  # DeFi tokens
            "MEME": 0.2,  # DOGE, SHIB, etc.
        }

        return cluster_caps.get(cluster, 1.0)

    def release_allocation(self, strategy_id: str, symbol: str, amount: float) -> None:
        """Release allocation when position is closed."""
        if strategy_id in self._strategy_budgets:
            budget = self._strategy_budgets[strategy_id]
            budget.allocated_exposure = max(0.0, budget.allocated_exposure - amount)
            if symbol in budget.symbols:
                budget.symbols[symbol] = max(0.0, budget.symbols[symbol] - amount)

    def get_portfolio_snapshot(self) -> dict[str, Any]:
        """Get current portfolio allocation snapshot."""
        return {
            "total_allocated": sum(
                b.allocated_exposure for b in self._strategy_budgets.values()
            ),
            "strategies": {
                sid: {
                    "max_exposure": b.max_exposure,
                    "current_exposure": b.current_exposure,
                    "allocated_exposure": b.allocated_exposure,
                    "symbols": b.symbols,
                }
                for sid, b in self._strategy_budgets.items()
            },
        }


__all__ = [
    "PortfolioAllocator",
    "PositionSizer",
    "AllocationRequest",
    "AllocationResult",
    "StrategyBudget",
]
