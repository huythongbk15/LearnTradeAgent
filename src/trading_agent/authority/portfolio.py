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

import enum
import logging
import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from trading_agent.authority.causation import CausationChain, new_chain
from trading_agent.authority.config import (
    AuthorityConfig,
    ExposureConfig,
    get_authority_config,
)
from trading_agent.authority.decision import TargetExposure

logger = logging.getLogger(__name__)


# ── Shared portfolio snapshot / batch target vector (Milestone C) ───────


class ReconciliationState(str, enum.Enum):
    RECONCILED = "RECONCILED"
    DEGRADED = "DEGRADED"  # reconciled but untracked exposure could not be valued
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Authoritative shared portfolio truth for ONE decision batch.

    Every pair decision in a cycle references this same snapshot. After the
    reduction stage a REFRESHED snapshot is built before BUY revalidation.
    """

    equity: float
    available_cash: float
    positions: dict[str, float]  # symbol -> quantity
    symbol_exposures: dict[str, float]  # symbol -> notional/equity (incl. untracked)
    gross_exposure: float
    untracked_symbols: tuple[str, ...] = ()
    untracked_exposure: float = 0.0  # valued notional/equity of untracked holdings
    untracked_valued: bool = True  # False ⇒ portfolio truth incomplete
    reserved_cash: float = 0.0
    reserved_inventory: float = 0.0
    observed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    reconciliation_state: ReconciliationState = ReconciliationState.NOT_RUN

    def __post_init__(self) -> None:
        for name in (
            "equity",
            "available_cash",
            "gross_exposure",
            "untracked_exposure",
        ):
            v = float(getattr(self, name))
            if not math.isfinite(v) or v < 0:
                raise ValueError(
                    f"PortfolioSnapshot.{name} must be finite ≥ 0, got {v}"
                )

    @property
    def new_exposure_allowed(self) -> bool:
        """Fail-closed gate: unvalued holdings or failed reconciliation mean
        portfolio truth is UNKNOWN — unknown truth is not zero risk."""
        return (
            self.untracked_valued
            and self.reconciliation_state is ReconciliationState.RECONCILED
        )


@dataclass(frozen=True)
class PortfolioTargetVector:
    """Canonical portfolio allocation result for one cycle.

    Deterministic function of the request SET + snapshot: permuting the
    input order MUST produce an identical vector (enforced by tests).
    """

    cycle_id: str
    equity: float
    available_cash: float
    targets: dict[str, float]  # symbol -> approved target exposure pct
    gross_target_exposure: float
    cash_reserve_pct: float
    allocation_reasons: dict[str, str]
    rejected_symbols: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    def __post_init__(self) -> None:
        gross = 0.0
        for sym, tgt in self.targets.items():
            if not math.isfinite(tgt) or tgt < 0:
                raise ValueError(f"target[{sym}] must be finite ≥ 0, got {tgt}")
            gross += tgt
        if abs(gross - self.gross_target_exposure) > 1e-9:
            raise ValueError(
                f"gross_target_exposure {self.gross_target_exposure} != "
                f"sum(targets) {gross}"
            )

    def target_for(self, symbol: str) -> float | None:
        return self.targets.get(symbol)


@dataclass(frozen=False)
class BatchAllocationEntry:
    """Per-symbol outcome of allocate_batch (allocation-level, pre-exposure)."""

    symbol: str
    strategy_id: str
    requested: float
    approved: float
    reason: str
    causation_chain: CausationChain | None = None
    signed_approved: float | None = None


@dataclass(frozen=False)
class BatchAllocationOutcome:
    """Whole-batch allocation result → assembled into PortfolioTargetVector."""

    entries: tuple[BatchAllocationEntry, ...]
    scale_factor: float  # 1.0 when everything fits; <1.0 when pro-rata scaled
    total_requested: float
    total_approved: float
    budget_available: float

    @property
    def approved_by_symbol(self) -> dict[str, float]:
        approved: dict[str, float] = {}
        for entry in self.entries:
            approved[entry.symbol] = approved.get(entry.symbol, 0.0) + entry.approved
        return approved

    @property
    def approved_by_strategy(self) -> dict[str, float]:
        approved: dict[str, float] = {}
        for entry in self.entries:
            approved[entry.strategy_id] = (
                approved.get(entry.strategy_id, 0.0) + entry.approved
            )
        return approved

    @property
    def net_by_symbol(self) -> dict[str, float]:
        """Net signed forecast target by symbol (long-only output clamps later)."""
        net: dict[str, float] = {}
        for entry in self.entries:
            signed = (
                entry.approved
                if entry.signed_approved is None
                else entry.signed_approved
            )
            net[entry.symbol] = net.get(entry.symbol, 0.0) + signed
        return net


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
    # Total exposure already held on this symbol across ALL strategies.
    # None → falls back to current_exposure (single-strategy-per-symbol case).
    symbol_total_exposure: float | None = None
    causation_chain: CausationChain | None = None
    desired_exposure: float | None = None
    regime_risk_multiplier: float = 1.0
    no_trade_band: float = 0.0
    average_daily_notional: float | None = None
    max_order_participation: float = 0.01

    def __post_init__(self) -> None:
        for name in ("regime_risk_multiplier", "no_trade_band"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.regime_risk_multiplier > 1.0:
            raise ValueError("regime_risk_multiplier cannot exceed 1")
        if self.desired_exposure is not None and (
            not math.isfinite(self.desired_exposure) or abs(self.desired_exposure) > 1.0
        ):
            raise ValueError("desired_exposure must be finite and in [-1, 1]")
        if not math.isfinite(self.max_order_participation) or not (
            0.0 < self.max_order_participation <= 1.0
        ):
            raise ValueError("max_order_participation must be in (0, 1]")
        if self.average_daily_notional is not None and (
            not math.isfinite(self.average_daily_notional)
            or self.average_daily_notional <= 0.0
        ):
            raise ValueError("average_daily_notional must be finite and positive")


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

            # Symbol-level cap: remaining headroom under max_single_symbol_exposure
            # measured against TOTAL exposure on this symbol across all strategies
            symbol_total = (
                request.symbol_total_exposure
                if request.symbol_total_exposure is not None
                else request.current_exposure
            )
            symbol_headroom = max(
                0.0,
                self.exposure_config.max_single_symbol_exposure - symbol_total,
            )
            allocation = min(allocation, symbol_headroom)

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
            elif request.risk_decision.reduce_only and request.symbol in budget.symbols:
                # Exit-to-flat (or reduce) frees the previously allocated
                # budget for this symbol. Without this release the strategy
                # budget leaks upward on every close and all later entries
                # get starved to zero allocation.
                released = budget.symbols.pop(request.symbol)
                budget.allocated_exposure = max(
                    0.0, budget.allocated_exposure - released
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

    def allocate_batch(
        self,
        requests: list[AllocationRequest],
        snapshot: PortfolioSnapshot,
        *,
        cash_reserve_pct: float | None = None,
    ) -> BatchAllocationOutcome:
        """Allocate ONE portfolio batch across all candidates deterministically.

        V1 policy (deterministic, documented):
        1. Requests are processed in sorted (strategy_id, symbol) order —
           input permutation cannot change the result.
        2. Each request's raw ask = min(allowed_target_exposure, max_new_exposure).
        3. Individual caps are applied first (per-strategy budget, per-symbol
           headroom vs TOTAL symbol exposure, correlation-cluster adjustment).
        4. Global increase budget = min(
               max_portfolio_exposure − gross_exposure(snapshot),
               available_cash/equity − cash_reserve_pct )
           where snapshot.gross_exposure INCLUDES valued untracked exposure.
        5. If Σ capped asks > budget: every candidate scales down PRO-RATA
           by a single factor s = budget / Σ asks (never alphabetical
           first-come-first-served).
        6. Strategy-budget bookkeeping commits only AFTER the whole batch
           computes successfully (all-or-nothing accounting).

        This method performs NO broker I/O and NO exchange access.
        """
        if cash_reserve_pct is None:
            # Implicit reserve: at full gross allocation, (1 - max_portfolio)
            # of equity remains uninvested.
            cash_reserve_pct = max(
                0.0, 1.0 - float(self.exposure_config.max_portfolio_exposure)
            )

        equity = float(snapshot.equity)
        if equity <= 0:
            zero_entries = tuple(
                BatchAllocationEntry(
                    symbol=r.symbol,
                    strategy_id=r.strategy_id,
                    requested=0.0,
                    approved=0.0,
                    reason="non_positive_equity",
                    causation_chain=self._batch_chain(r),
                )
                for r in sorted(requests, key=lambda x: (x.strategy_id, x.symbol))
            )
            return BatchAllocationOutcome(
                entries=zero_entries,
                scale_factor=0.0,
                total_requested=0.0,
                total_approved=0.0,
                budget_available=0.0,
            )

        ordered = sorted(requests, key=lambda x: (x.strategy_id, x.symbol))
        request_keys = [(request.strategy_id, request.symbol) for request in ordered]
        if len(request_keys) != len(set(request_keys)):
            raise ValueError("duplicate strategy_id/symbol allocation request")

        # ── Step 1: individual caps per request ────────────────────────
        capped: dict[tuple[str, str], float] = {}
        raw_ask: dict[tuple[str, str], float] = {}
        reasons: dict[tuple[str, str], str] = {}
        req_by_key: dict[tuple[str, str], AllocationRequest] = {}
        direction: dict[tuple[str, str], float] = {}

        for req in ordered:
            chain = self._batch_chain(req)
            try:
                budget = self._get_or_create_budget(req.strategy_id, req)
                strat_available = (
                    budget.max_exposure - budget.allocated_exposure
                ) * self._cluster_adjustment(req.correlation_cluster)
                asked = min(
                    float(req.risk_decision.allowed_target_exposure),
                    float(req.risk_decision.max_new_exposure),
                )
                signed_desired = req.desired_exposure
                if signed_desired is not None:
                    direction[(req.strategy_id, req.symbol)] = (
                        -1.0 if signed_desired < 0.0 else 1.0
                    )
                    asked = min(asked, abs(signed_desired))
                else:
                    direction[(req.strategy_id, req.symbol)] = 1.0
                asked = max(0.0, asked) * req.regime_risk_multiplier
                if asked < req.no_trade_band:
                    asked = 0.0
                symbol_total = (
                    req.symbol_total_exposure
                    if req.symbol_total_exposure is not None
                    else req.current_exposure
                )
                symbol_headroom = max(
                    0.0,
                    float(self.exposure_config.max_single_symbol_exposure)
                    - float(symbol_total),
                )
                liquidity_headroom = float("inf")
                if req.average_daily_notional is not None:
                    liquidity_headroom = (
                        req.average_daily_notional
                        * req.max_order_participation
                        / equity
                    )
                c = min(
                    asked,
                    max(0.0, strat_available),
                    symbol_headroom,
                    liquidity_headroom,
                )
                c = max(0.0, c)
                capped[(req.strategy_id, req.symbol)] = c
                raw_ask[(req.strategy_id, req.symbol)] = asked
                req_by_key[(req.strategy_id, req.symbol)] = req
                reasons[(req.strategy_id, req.symbol)] = (
                    f"capped_from_{asked:.6f}_"
                    f"strat_{max(0.0, strat_available):.6f}_"
                    f"sym_headroom_{symbol_headroom:.6f}_"
                    f"liquidity_{liquidity_headroom:.6f}"
                )
            except Exception as e:  # fail-closed per request, never raise upward
                logger.error("allocate_batch: cap computation failed: %s", e)
                capped[(req.strategy_id, req.symbol)] = 0.0
                raw_ask[(req.strategy_id, req.symbol)] = 0.0
                reasons[(req.strategy_id, req.symbol)] = f"cap_error:{e}"

        # Aggregate constraints must be applied to the SET, not independently.
        # Without this stage, two requests can each consume the same strategy,
        # symbol, or correlation headroom and jointly exceed the configured cap.
        group_factors: dict[tuple[str, str], float] = {key: 1.0 for key in capped}

        def apply_group_factor(keys: list[tuple[str, str]], available: float) -> None:
            total = sum(capped[key] for key in keys)
            factor = min(1.0, max(0.0, available) / total) if total > 0.0 else 1.0
            for key in keys:
                group_factors[key] = min(group_factors[key], factor)

        for strategy_id in sorted({key[0] for key in capped}):
            keys = [key for key in capped if key[0] == strategy_id]
            budget = self._strategy_budgets[strategy_id]
            apply_group_factor(keys, budget.max_exposure - budget.allocated_exposure)

        for symbol in sorted({key[1] for key in capped}):
            keys = [key for key in capped if key[1] == symbol]
            current = max(
                float(snapshot.symbol_exposures.get(symbol, 0.0)),
                *(float(req_by_key[key].symbol_total_exposure or 0.0) for key in keys),
            )
            apply_group_factor(
                keys,
                float(self.exposure_config.max_single_symbol_exposure) - current,
            )

        clusters = sorted(
            {
                request.correlation_cluster
                for request in ordered
                if request.correlation_cluster is not None
            }
        )
        for cluster in clusters:
            keys = [
                key for key in capped if req_by_key[key].correlation_cluster == cluster
            ]
            cluster_symbols = {key[1] for key in keys}
            current = sum(
                float(snapshot.symbol_exposures.get(symbol, 0.0))
                for symbol in cluster_symbols
            )
            apply_group_factor(
                keys,
                float(self.exposure_config.max_correlated_exposure) - current,
            )

        for key, factor in group_factors.items():
            if factor < 1.0 and capped[key] > 0.0:
                capped[key] *= factor
                reasons[key] += f"|aggregate_constraint_scale_{factor:.6f}"

        # ── Step 2: global increase budget from SHARED snapshot ────────
        gross_now = float(snapshot.gross_exposure)
        gross_budget = max(
            0.0, float(self.exposure_config.max_portfolio_exposure) - gross_now
        )
        cash_budget = max(
            0.0, float(snapshot.available_cash) / equity - float(cash_reserve_pct)
        )
        if not snapshot.new_exposure_allowed:
            # Unknown portfolio truth (unvalued holdings / failed reconcile):
            # UNKNOWN TRUTH IS NOT ZERO RISK → zero new exposure.
            gross_budget = 0.0
            cash_budget = 0.0
        budget_available = min(gross_budget, cash_budget)

        total_ask = sum(raw_ask.values())
        total_capped = sum(capped.values())

        # ── Step 3: pro-rata scaling when over budget ──────────────────
        if total_capped > budget_available and total_capped > 0:
            scale = budget_available / total_capped
        else:
            scale = 1.0

        entries: list[BatchAllocationEntry] = []
        for req in ordered:
            key = (req.strategy_id, req.symbol)
            c = capped[key]
            approved = c * scale
            reason = reasons[key]
            if scale < 1.0 and c > 0:
                reason += f"|pro_rata_scale_{scale:.6f}"
            elif c == 0 and raw_ask[key] > 0:
                reason += "|rejected_cap_zero"
            entries.append(
                BatchAllocationEntry(
                    symbol=req.symbol,
                    strategy_id=req.strategy_id,
                    requested=raw_ask[key],
                    approved=approved,
                    reason=reason,
                    causation_chain=self._append_batch_link(req, approved, scale),
                    signed_approved=approved * direction[key],
                )
            )

        total_approved = sum(e.approved for e in entries)

        # ── Step 4: commit strategy budgets atomically post-computation ─
        for entry in entries:
            if entry.approved > 0:
                key = (entry.strategy_id, entry.symbol)
                budget = self._get_or_create_budget(
                    entry.strategy_id,
                    req_by_key[key],
                )
                budget.allocated_exposure += entry.approved
                budget.symbols[entry.symbol] = (
                    budget.symbols.get(entry.symbol, 0.0) + entry.approved
                )

        return BatchAllocationOutcome(
            entries=tuple(entries),
            scale_factor=scale,
            total_requested=total_ask,
            total_approved=total_approved,
            budget_available=budget_available,
        )

    def build_target_vector(
        self,
        outcome: BatchAllocationOutcome,
        snapshot: PortfolioSnapshot,
        cycle_id: str,
    ) -> PortfolioTargetVector:
        """Assemble the canonical PortfolioTargetVector from a batch outcome."""
        # Net opposing strategy forecasts before emitting the long-only target
        # vector. A negative net target means reduce to flat, never open a short.
        targets = {
            symbol: round(max(0.0, approved), 12)
            for symbol, approved in outcome.net_by_symbol.items()
        }
        reasons: dict[str, str] = {}
        for entry in outcome.entries:
            existing = reasons.get(entry.symbol)
            item = f"{entry.strategy_id}:{entry.reason}"
            reasons[entry.symbol] = f"{existing}|{item}" if existing else item
        requested_by_symbol: dict[str, float] = {}
        for entry in outcome.entries:
            requested_by_symbol[entry.symbol] = (
                requested_by_symbol.get(entry.symbol, 0.0) + entry.requested
            )
        rejected = tuple(
            symbol
            for symbol, requested in sorted(requested_by_symbol.items())
            if requested > 0.0 and targets.get(symbol, 0.0) <= 0.0
        )
        reserve_pct = max(0.0, 1.0 - float(self.exposure_config.max_portfolio_exposure))
        return PortfolioTargetVector(
            cycle_id=cycle_id,
            equity=float(snapshot.equity),
            available_cash=float(snapshot.available_cash),
            targets=targets,
            gross_target_exposure=sum(targets.values()),
            cash_reserve_pct=reserve_pct,
            allocation_reasons=reasons,
            rejected_symbols=rejected,
        )

    def _batch_chain(self, request: AllocationRequest) -> CausationChain:
        return request.causation_chain or new_chain(
            {
                "authority": "PortfolioAllocator",
                "stage": "allocate_batch",
                "strategy_id": request.strategy_id,
                "symbol": request.symbol,
            }
        )

    def _append_batch_link(
        self, request: AllocationRequest, approved: float, scale: float
    ) -> CausationChain:
        chain = self._batch_chain(request)
        return chain.append(
            authority="PortfolioAllocator",
            inputs={
                "stage": "allocate_batch",
                "strategy_id": request.strategy_id,
                "symbol": request.symbol,
                "requested": float(
                    min(
                        request.risk_decision.allowed_target_exposure,
                        request.risk_decision.max_new_exposure,
                    )
                ),
            },
            outputs={"approved": approved, "batch_scale": scale},
        )

    def release_allocation(self, strategy_id: str, symbol: str, amount: float) -> None:
        """Release allocation when position is closed."""
        if strategy_id in self._strategy_budgets:
            budget = self._strategy_budgets[strategy_id]
            budget.allocated_exposure = max(0.0, budget.allocated_exposure - amount)
            if symbol in budget.symbols:
                budget.symbols[symbol] = max(0.0, budget.symbols[symbol] - amount)

    def reconcile(self, live_symbol_exposures: dict[str, float]) -> dict[str, Any]:
        """Reconcile strategy budgets against LIVE exchange positions.

        The exchange is the single exposure truth. Budget bookkeeping is
        advisory and MUST be corrected every cycle BEFORE any new allocation:
        closed positions release their budget automatically; over-held
        positions consume it. This replaces the old write-only accounting
        where allocated_exposure only ever grew.

        Args:
            live_symbol_exposures: symbol -> notional/equity from the exchange.

        Returns:
            Audit dict with released/consumed amounts per (strategy, symbol).
        """
        audit: dict[str, Any] = {"released": {}, "consumed": {}, "untracked": []}

        # Symbols tracked by at least one budget
        tracked_symbols: set[str] = set()
        for budget in self._strategy_budgets.values():
            tracked_symbols.update(budget.symbols.keys())

        # Attribution: live exposure of a symbol is split across its trackers
        # proportionally to their last recorded shares.
        attributed: dict[str, dict[str, float]] = {
            sid: {} for sid in self._strategy_budgets
        }
        for sym in sorted(tracked_symbols):
            trackers = [
                (sid, b)
                for sid, b in self._strategy_budgets.items()
                if sym in b.symbols
            ]
            live = float(live_symbol_exposures.get(sym, 0.0))
            recorded_total = sum(b.symbols[sym] for _sid, b in trackers)

            if recorded_total <= 0.0 or live <= 0.0:
                # Nothing held (or nothing recorded): clear stale entries
                for sid, b in trackers:
                    before = b.symbols.get(sym, 0.0)
                    b.symbols[sym] = 0.0
                    if before > 0.0:
                        audit["released"][f"{sid}:{sym}"] = round(before, 10)
                continue

            for sid, b in trackers:
                share = b.symbols[sym] / recorded_total
                attributed[sid][sym] = live * share

        # Untracked live exposure — visible, not silently ignored
        for sym in sorted(live_symbol_exposures):
            if sym not in tracked_symbols and live_symbol_exposures[sym] > 1e-12:
                audit["untracked"].append(sym)

        # Commit truth back into budgets
        for sid, budget in self._strategy_budgets.items():
            new_current = sum(attributed.get(sid, {}).values())
            for sym, val in attributed.get(sid, {}).items():
                budget.symbols[sym] = val
            delta = new_current - budget.current_exposure
            if delta > 1e-12:
                audit["consumed"][sid] = round(delta, 10)
            budget.current_exposure = new_current
            # Committed budget == actually held exposure (exchange truth)
            budget.allocated_exposure = new_current

        return audit

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
    "PortfolioSnapshot",
    "ReconciliationState",
    "PortfolioTargetVector",
    "BatchAllocationEntry",
    "BatchAllocationOutcome",
]
