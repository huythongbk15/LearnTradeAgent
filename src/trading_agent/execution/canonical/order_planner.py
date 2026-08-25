"""OrderPlanner — pure deterministic component that converts TargetExposure to OrderIntent.

All sizing, rebalancing, and validation happens HERE.  No I/O, no exchange
calls, no randomness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from collections.abc import Mapping
from typing import Any

from trading_agent.execution.canonical.events import IdempotencyKeys
from trading_agent.execution.canonical.market_observation import (
    BarState,
    EnrichedMarketObservation,
)
from trading_agent.execution.canonical.risk_decision import UnifiedRiskDecision
from trading_agent.research.forecast import TargetExposure


class ExposureEffect(str, Enum):
    """Directional effect of this intent on the current portfolio exposure."""

    INCREASE = "INCREASE"
    REDUCE = "REDUCE"
    NEUTRAL = "NEUTRAL"


class OrderPlanningStatus(str, Enum):
    """Result status of order planning."""

    ORDER_REQUIRED = "ORDER_REQUIRED"
    NOOP = "NOOP"
    BLOCKED = "BLOCKED"


class AdjustmentReason(str, Enum):
    """Explicit reason when feasibility adjusts canonical quantity."""

    NONE = "NONE"
    MIN_QTY = "MIN_QTY"
    MAX_QTY = "MAX_QTY"
    QTY_STEP = "QTY_STEP"
    MIN_NOTIONAL = "MIN_NOTIONAL"
    MAX_NOTIONAL = "MAX_NOTIONAL"
    INSUFFICIENT_CASH = "INSUFFICIENT_CASH"
    INSUFFICIENT_INVENTORY = "INSUFFICIENT_INVENTORY"
    MAX_LEVERAGE = "MAX_LEVERAGE"


@dataclass(frozen=True)
class CurrentPortfolioState:
    """Immutable snapshot of the current portfolio for planning purposes."""

    symbol: str
    equity: float  # portfolio equity in quote currency (e.g., USD)
    current_exposure: float  # signed exposure [-1, 1]
    existing_quantity: float = 0.0
    avg_entry_price: float = 0.0
    existing_reservations: float = 0.0  # reserved qty not yet filled
    available_cash: float = 0.0  # cash available for new orders

    def __post_init__(self) -> None:
        if self.equity <= 0.0:
            raise ValueError("equity must be positive")
        if not (-1.0 <= self.current_exposure <= 1.0):
            raise ValueError("current_exposure must be in [-1, 1]")
        if self.existing_quantity < 0.0:
            raise ValueError("existing_quantity must be non-negative")
        if self.existing_reservations < 0.0:
            raise ValueError("existing_reservations must be non-negative")
        if self.available_cash < 0.0:
            raise ValueError("available_cash must be non-negative")

    @property
    def current_notional(self) -> float:
        """Current position notional in quote currency."""
        return self.equity * abs(self.current_exposure)


@dataclass(frozen=True)
class InstrumentRules:
    """Static constraints for a trading instrument."""

    symbol: str
    asset_class: str = "spot"
    min_order_qty: float = 0.001
    max_order_qty: float = 1.0
    qty_step: float = 0.001
    price_precision: int = 2
    spot_long_only: bool = True
    max_leverage: float = 1.0
    min_notional: float = 10.0  # minimum order notional in quote currency
    max_notional: float | None = None  # optional max notional

    def __post_init__(self) -> None:
        if not math.isfinite(self.min_order_qty) or self.min_order_qty <= 0.0:
            raise ValueError("min_order_qty must be positive")
        if not math.isfinite(self.max_order_qty):
            raise ValueError("max_order_qty must be finite")
        if self.max_order_qty < self.min_order_qty:
            raise ValueError("max_order_qty must be >= min_order_qty")
        if not math.isfinite(self.qty_step) or self.qty_step <= 0.0:
            raise ValueError("qty_step must be positive")
        if not math.isfinite(self.min_notional) or self.min_notional <= 0.0:
            raise ValueError("min_notional must be positive")
        if self.max_notional is not None:
            if not math.isfinite(self.max_notional):
                raise ValueError("max_notional must be finite")
            if self.max_notional < self.min_notional:
                raise ValueError("max_notional must be >= min_notional")


@dataclass(frozen=True)
class OrderIntent:
    """Deterministic order intent produced by OrderPlanner.

    All sizing and direction decisions are encoded here.  The BrokerGateway
    is the ONLY component that may turn an OrderIntent into a broker order.
    """

    # ── Identity ────────────────────────────────────────────────────────
    intent_id: str
    decision_id: str
    forecast_fingerprint: str
    model_artifact_id: str

    # ── Instrument ──────────────────────────────────────────────────────
    symbol: str
    asset_class: str

    # ── Execution ───────────────────────────────────────────────────────
    side: str  # "buy" | "sell"
    quantity: float
    current_exposure: float
    target_exposure: float
    resulting_exposure: float
    exposure_effect: ExposureEffect
    price_reference: float  # reference price for the intent

    # ── Idempotency ─────────────────────────────────────────────────────
    idempotency_key: str

    # ── Audit ───────────────────────────────────────────────────────────
    created_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.side not in ("buy", "sell"):
            raise ValueError(f"side must be 'buy' or 'sell', got {self.side!r}")
        if self.quantity <= 0.0:
            raise ValueError("quantity must be positive")
        if not (-1.0 <= self.current_exposure <= 1.0):
            raise ValueError("current_exposure must be in [-1, 1]")
        if not (-1.0 <= self.target_exposure <= 1.0):
            raise ValueError("target_exposure must be in [-1, 1]")
        if not (-1.0 <= self.resulting_exposure <= 1.0):
            raise ValueError("resulting_exposure must be in [-1, 1]")
        if self.price_reference <= 0.0:
            raise ValueError("price_reference must be positive")


@dataclass(frozen=True)
class MarketPrice:
    """Reference price bundle for planning."""

    symbol: str
    mid: float
    bid: float = 0.0
    ask: float = 0.0
    last: float = 0.0

    def __post_init__(self) -> None:
        if self.mid <= 0.0:
            raise ValueError("mid must be positive")
        if self.bid < 0.0 or self.ask < 0.0 or self.last < 0.0:
            raise ValueError("bid/ask/last must be non-negative")


@dataclass(frozen=True)
class OrderPlanningResult:
    """Result of the planning operation."""

    status: OrderPlanningStatus
    intent: OrderIntent | None
    reason_codes: tuple[str, ...]
    requested_delta: float  # target_exposure - current_exposure (pre-clamp)
    executable_delta: (
        float  # resulting_exposure - current_exposure (post-clamp, post-feasibility)
    )

    @property
    def is_noop(self) -> bool:
        return self.status is OrderPlanningStatus.NOOP

    @property
    def is_blocked(self) -> bool:
        return self.status is OrderPlanningStatus.BLOCKED

    @property
    def requires_order(self) -> bool:
        return self.status is OrderPlanningStatus.ORDER_REQUIRED


class OrderPlanner:
    """Pure deterministic component: TargetExposure -> OrderPlanningResult.

    Parameters
    ----------
    instrument_rules:
        Static per-symbol constraints (min qty, step, long-only, etc.).
        Either a single InstrumentRules (single-symbol engines) or a mapping
        symbol -> InstrumentRules (multi-pair runtime). Unknown symbols are
        rejected fail-closed at plan() time.
    strategy_version:
        Used in idempotency key derivation.
    """

    def __init__(
        self,
        instrument_rules: InstrumentRules | Mapping[str, InstrumentRules],
        strategy_version: str = "v1",
    ) -> None:
        if isinstance(instrument_rules, InstrumentRules):
            self._rules_map: dict[str, InstrumentRules] = {
                instrument_rules.symbol: instrument_rules
            }
            # Backward-compat handle for single-rule planners.
            self._rules = instrument_rules
        elif isinstance(instrument_rules, Mapping):
            self._rules_map = dict(instrument_rules)
            if not self._rules_map:
                raise ValueError("instrument_rules mapping is empty")
            for sym, rules in self._rules_map.items():
                if not isinstance(rules, InstrumentRules):
                    raise TypeError(
                        f"instrument_rules[{sym}] must be an InstrumentRules"
                    )
                if rules.symbol != sym:
                    raise ValueError(
                        f"rule key {sym!r} does not match rules.symbol "
                        f"{rules.symbol!r}"
                    )
            self._rules = (
                next(iter(self._rules_map.values()))
                if len(self._rules_map) == 1
                else None  # type: ignore[assignment]
            )
        else:
            raise TypeError(
                "instrument_rules must be an InstrumentRules or a "
                "symbol -> InstrumentRules mapping"
            )
        self._strategy_version = strategy_version

    def rules_for(self, symbol: str) -> InstrumentRules | None:
        """Per-symbol instrument rules; None if symbol is not registered."""
        return self._rules_map.get(symbol)

    @property
    def symbols(self) -> tuple[str, ...]:
        """Symbols this planner can trade."""
        return tuple(sorted(self._rules_map))

    def plan(
        self,
        *,
        target: TargetExposure,
        risk_decision: UnifiedRiskDecision,
        observation: EnrichedMarketObservation,
        portfolio: CurrentPortfolioState,
        price: MarketPrice,
        existing_reservations: float = 0.0,
        tolerance: float = 1e-4,
    ) -> OrderPlanningResult:
        """Produce an OrderPlanningResult from pipeline inputs.

        Parameters
        ----------
        target:
            The TargetExposure from the risk layer.
        risk_decision:
            The unified risk decision.
        observation:
            Enriched market observation (must be closed for execution).
        portfolio:
            Current portfolio state (MUST include equity for notional sizing).
        price:
            Reference price bundle.
        existing_reservations:
            Quantity already reserved but not yet filled.
        tolerance:
            Deadband for NOOP determination (default 1e-4 = 0.01%).

        Returns
        -------
        OrderPlanningResult
            Contains status, optional intent, reason codes, and deltas.

        Raises
        ------
        ValueError
            If binding checks fail, target violates instrument rules, or
            spot-long-only constraint is violated.
        """
        # ── Pre-flight validation ────────────────────────────────────────
        if observation.bar_state is not BarState.SOURCE_CONFIRMED_CLOSED:
            raise ValueError(
                f"cannot plan from {observation.bar_state.value} observation"
            )
        if target.symbol != portfolio.symbol:
            raise ValueError("target symbol must match portfolio symbol")
        if target.symbol != price.symbol:
            raise ValueError("target symbol must match price symbol")
        rules = self._rules_map.get(target.symbol)
        if rules is None:
            # Fail-closed: no instrument rules registered for this symbol
            raise ValueError(
                f"no instrument rules registered for symbol {target.symbol} "
                f"(registered: {', '.join(sorted(self._rules_map))})"
            )

        # Spot-long-only: reject negative target
        if rules.spot_long_only and target.exposure < 0.0:
            raise ValueError(
                f"spot-long-only instrument rejected negative target exposure "
                f"{target.exposure}"
            )

        # Compute raw exposure delta
        requested_delta = target.exposure - portfolio.current_exposure

        # If requesting exposure increase but risk decision doesn't approve any exposure
        if requested_delta > 0 and not risk_decision.approved:
            return OrderPlanningResult(
                status=OrderPlanningStatus.BLOCKED,
                intent=None,
                reason_codes=("RISK_DECISION_NOT_APPROVED",),
                requested_delta=requested_delta,
                executable_delta=0.0,
            )

        # ── Target/Risk binding verification (P0 §4) ─────────────────────
        if target.risk_decision_id != risk_decision.decision_id:
            raise ValueError(
                f"target.risk_decision_id ({target.risk_decision_id}) != "
                f"risk_decision.decision_id ({risk_decision.decision_id})"
            )
        if target.forecast_fingerprint != risk_decision.forecast_fingerprint:
            raise ValueError(
                f"target.forecast_fingerprint ({target.forecast_fingerprint}) != "
                f"risk_decision.forecast_fingerprint ({risk_decision.forecast_fingerprint})"
            )
        if target.model_artifact_id != risk_decision.model_artifact_id:
            raise ValueError(
                f"target.model_artifact_id ({target.model_artifact_id}) != "
                f"risk_decision.model_artifact_id ({risk_decision.model_artifact_id})"
            )

        # ── max_new_exposure enforcement (P0 §4) — check BEFORE clamping ──
        if requested_delta > 0:
            if requested_delta > risk_decision.max_new_exposure + 1e-9:
                return OrderPlanningResult(
                    status=OrderPlanningStatus.BLOCKED,
                    intent=None,
                    reason_codes=("MAX_NEW_EXPOSURE_EXCEEDED",),
                    requested_delta=requested_delta,
                    executable_delta=0.0,
                )

        # ── reduce_only enforcement (P0 §4) — check BEFORE clamping ──────
        if risk_decision.reduce_only and requested_delta > 1e-9:
            return OrderPlanningResult(
                status=OrderPlanningStatus.BLOCKED,
                intent=None,
                reason_codes=("REDUCE_ONLY_VIOLATION",),
                requested_delta=requested_delta,
                executable_delta=0.0,
            )

        # Determine resulting exposure (clamped to instrument limits and risk decision)
        max_allowed_by_leverage = rules.max_leverage
        max_allowed_by_risk = risk_decision.allowed_target_exposure
        resulting_exposure = max(
            -max_allowed_by_leverage,
            min(max_allowed_by_leverage, max_allowed_by_risk, target.exposure),
        )
        executable_delta = resulting_exposure - portfolio.current_exposure

        # ── NOOP determination (P0 §2) ───────────────────────────────────
        if abs(executable_delta) <= tolerance:
            # Within deadband → NOOP, no order intent
            keys = IdempotencyKeys.compute(
                decision_id=risk_decision.decision_id,
                symbol=target.symbol,
                target_exposure=target.exposure,
                horizon=target.horizon,
            )
            return OrderPlanningResult(
                status=OrderPlanningStatus.NOOP,
                intent=None,
                reason_codes=("WITHIN_TOLERANCE",),
                requested_delta=requested_delta,
                executable_delta=0.0,
            )

        # ── Canonical notional-based sizing (P0 §3) ──────────────────────
        # target_notional = equity * target_exposure
        # current_notional = equity * current_exposure
        # delta_notional = target_notional - current_notional
        # raw_quantity = abs(delta_notional) / execution_price
        target_notional = portfolio.equity * resulting_exposure
        current_notional = portfolio.equity * portfolio.current_exposure
        delta_notional = target_notional - current_notional
        execution_price = price.mid
        raw_quantity = abs(delta_notional) / execution_price

        # ── Apply exchange feasibility (P0 §3) ──────────────────────────
        # All constraints form one intersection on the qty_step lattice. Applying
        # them independently can make a later adjustment invalidate an earlier
        # one (for example min_notional can push quantity above max_notional).
        adjustment_reasons: list[AdjustmentReason] = []

        def add_reason(reason: AdjustmentReason) -> None:
            if reason not in adjustment_reasons:
                adjustment_reasons.append(reason)

        step = rules.qty_step
        rounding_epsilon = max(step * 1e-12, math.ulp(step) * 8)

        def step_units(value: float) -> float:
            units = value / step
            nearest = round(units)
            ulp_tolerance = max(math.ulp(units) * 8, 1e-12)
            if abs(units - nearest) <= ulp_tolerance:
                return float(nearest)
            return units

        def ceil_to_step(value: float) -> float:
            return math.ceil(step_units(value)) * step

        def floor_to_step(value: float) -> float:
            return math.floor(step_units(value)) * step

        min_qty_from_notional = rules.min_notional / execution_price
        lower_bound = max(rules.min_order_qty, min_qty_from_notional)
        min_feasible_qty = ceil_to_step(lower_bound)

        upper_bound = rules.max_order_qty
        if rules.max_notional is not None:
            upper_bound = min(upper_bound, rules.max_notional / execution_price)

        available_inventory = math.inf
        if executable_delta > 0:
            side = "buy"
            effect = ExposureEffect.INCREASE
            upper_bound = min(upper_bound, portfolio.available_cash / execution_price)
        elif executable_delta < 0:
            side = "sell"
            effect = ExposureEffect.REDUCE
            reserved_inventory = max(
                existing_reservations, portfolio.existing_reservations
            )
            available_inventory = max(
                0.0, portfolio.existing_quantity - reserved_inventory
            )
            upper_bound = min(upper_bound, available_inventory)
            # A reduce-to-flat request is inventory-bound. Reconstructing it from
            # exposure/notional can land one lattice step low through float error.
            if abs(resulting_exposure) <= tolerance:
                raw_quantity = available_inventory

        else:
            return OrderPlanningResult(
                status=OrderPlanningStatus.NOOP,
                intent=None,
                reason_codes=("ZERO_DELTA_AFTER_CLAMP",),
                requested_delta=requested_delta,
                executable_delta=0.0,
            )

        max_feasible_qty = floor_to_step(max(0.0, upper_bound))

        if min_feasible_qty > max_feasible_qty + rounding_epsilon:
            cash_capacity = portfolio.available_cash / execution_price
            if side == "buy" and cash_capacity < lower_bound:
                add_reason(AdjustmentReason.INSUFFICIENT_CASH)
                blocked_reason = "INSUFFICIENT_CASH_FOR_MIN_ORDER"
            elif side == "sell" and available_inventory < lower_bound:
                add_reason(AdjustmentReason.INSUFFICIENT_INVENTORY)
                blocked_reason = "INSUFFICIENT_INVENTORY_FOR_MIN_ORDER"
            else:
                blocked_reason = "NO_FEASIBLE_QUANTITY"
            return OrderPlanningResult(
                status=OrderPlanningStatus.BLOCKED,
                intent=None,
                reason_codes=(blocked_reason,)
                + tuple(str(r) for r in adjustment_reasons),
                requested_delta=requested_delta,
                executable_delta=0.0,
            )

        # Floor the requested quantity first so a step adjustment never
        # overspends cash or overshoots the requested exposure.
        quantity = floor_to_step(raw_quantity)
        notional = quantity * execution_price
        if abs(quantity - raw_quantity) > rounding_epsilon:
            add_reason(AdjustmentReason.QTY_STEP)

        if quantity < rules.min_order_qty - rounding_epsilon:
            add_reason(AdjustmentReason.MIN_QTY)
        if notional < rules.min_notional - 1e-9:
            add_reason(AdjustmentReason.MIN_NOTIONAL)
        if quantity < min_feasible_qty:
            quantity = min_feasible_qty
            notional = quantity * execution_price

        if quantity > rules.max_order_qty + rounding_epsilon:
            add_reason(AdjustmentReason.MAX_QTY)
        if (
            rules.max_notional is not None
            and notional > rules.max_notional + 1e-9
        ):
            add_reason(AdjustmentReason.MAX_NOTIONAL)
        if side == "buy" and notional > portfolio.available_cash + 1e-9:
            add_reason(AdjustmentReason.INSUFFICIENT_CASH)
        if side == "sell" and quantity > available_inventory + rounding_epsilon:
            add_reason(AdjustmentReason.INSUFFICIENT_INVENTORY)
        if quantity > max_feasible_qty:
            quantity = max_feasible_qty
            notional = quantity * execution_price

        # Final check: if quantity became zero or negative after constraints
        if quantity <= 1e-12:
            return OrderPlanningResult(
                status=OrderPlanningStatus.NOOP,
                intent=None,
                reason_codes=("NON_EXECUTABLE_AFTER_CONSTRAINTS",)
                + tuple(str(r) for r in adjustment_reasons),
                requested_delta=requested_delta,
                executable_delta=0.0,
            )

        # Defensive final invariants after every quantity adjustment.
        notional = quantity * execution_price
        if quantity < min_feasible_qty - rounding_epsilon:
            return OrderPlanningResult(
                status=OrderPlanningStatus.BLOCKED,
                intent=None,
                reason_codes=("NO_FEASIBLE_QUANTITY",)
                + tuple(str(r) for r in adjustment_reasons),
                requested_delta=requested_delta,
                executable_delta=0.0,
            )
        if side == "sell" and quantity > available_inventory + rounding_epsilon:
            return OrderPlanningResult(
                status=OrderPlanningStatus.BLOCKED,
                intent=None,
                reason_codes=("SELL_QUANTITY_EXCEEDS_AVAILABLE_INVENTORY",),
                requested_delta=requested_delta,
                executable_delta=0.0,
            )

        # Compute final resulting exposure from actual executable quantity
        final_notional = quantity * execution_price * (1 if side == "buy" else -1)
        final_resulting_exposure = (
            current_notional + final_notional
        ) / portfolio.equity
        final_exposure_delta = final_resulting_exposure - portfolio.current_exposure

        # ── Post-feasibility risk revalidation (P0 §3) ───────────────────
        # After all feasibility adjustments, revalidate against risk decision.
        # Use a tolerance of at least one qty_step to allow rounding.
        qty_step_tolerance = (
            rules.qty_step * execution_price / portfolio.equity
            if rules.qty_step > 0 and portfolio.equity > 0
            else 1e-9
        )
        tolerance = max(qty_step_tolerance, 1e-9)
        if side == "buy":
            # INCREASE: must not exceed allowed_target_exposure or max_new_exposure
            if (
                final_resulting_exposure
                > risk_decision.allowed_target_exposure + tolerance
            ):
                return OrderPlanningResult(
                    status=OrderPlanningStatus.BLOCKED,
                    intent=None,
                    reason_codes=("POST_FEASIBILITY_EXPOSURE_EXCEEDS_ALLOWED",)
                    + tuple(str(r) for r in adjustment_reasons),
                    requested_delta=requested_delta,
                    executable_delta=0.0,
                )
            if final_exposure_delta > risk_decision.max_new_exposure + tolerance:
                return OrderPlanningResult(
                    status=OrderPlanningStatus.BLOCKED,
                    intent=None,
                    reason_codes=("POST_FEASIBILITY_DELTA_EXCEEDS_MAX_NEW",)
                    + tuple(str(r) for r in adjustment_reasons),
                    requested_delta=requested_delta,
                    executable_delta=0.0,
                )
            # Also validate against target (allow rounding by tolerance)
            if final_resulting_exposure > target.exposure + tolerance:
                return OrderPlanningResult(
                    status=OrderPlanningStatus.BLOCKED,
                    intent=None,
                    reason_codes=("POST_FEASIBILITY_OVERSHOOT_TARGET",)
                    + tuple(str(r) for r in adjustment_reasons),
                    requested_delta=requested_delta,
                    executable_delta=0.0,
                )
        else:
            # REDUCE: resulting exposure must not increase
            if (
                abs(final_resulting_exposure)
                > abs(portfolio.current_exposure) + tolerance
            ):
                return OrderPlanningResult(
                    status=OrderPlanningStatus.BLOCKED,
                    intent=None,
                    reason_codes=("POST_FEASIBILITY_REDUCE_INCREASES_EXPOSURE",)
                    + tuple(str(r) for r in adjustment_reasons),
                    requested_delta=requested_delta,
                    executable_delta=0.0,
                )

        # Compute idempotency keys
        keys = IdempotencyKeys.compute(
            decision_id=risk_decision.decision_id,
            symbol=target.symbol,
            target_exposure=target.exposure,
            horizon=target.horizon,
        )

        intent_id = (
            f"intent_{risk_decision.decision_id}_{target.symbol}"
            f"_{side}_{keys.target_exposure_key}"
        )

        intent = OrderIntent(
            intent_id=intent_id,
            decision_id=risk_decision.decision_id,
            forecast_fingerprint=risk_decision.forecast_fingerprint,
            model_artifact_id=risk_decision.model_artifact_id,
            symbol=target.symbol,
            asset_class=rules.asset_class,
            side=side,
            quantity=quantity,
            current_exposure=portfolio.current_exposure,
            target_exposure=resulting_exposure,
            resulting_exposure=final_resulting_exposure,
            exposure_effect=effect,
            price_reference=price.mid,
            idempotency_key=keys.intent_idempotency_key,
            created_at=datetime.now(UTC),
            metadata={
                "strategy_version": self._strategy_version,
                "target_exposure_key": keys.target_exposure_key,
                "regime_entropy": risk_decision.regime_entropy,
                "ood_score": risk_decision.ood_score,
                "requested_delta": requested_delta,
                "executable_delta": executable_delta,
                "raw_quantity": raw_quantity,
                "adjustment_reasons": [str(r) for r in adjustment_reasons],
                "target_notional": target_notional,
                "current_notional": current_notional,
                "delta_notional": delta_notional,
            },
        )

        return OrderPlanningResult(
            status=OrderPlanningStatus.ORDER_REQUIRED,
            intent=intent,
            reason_codes=tuple(str(r) for r in adjustment_reasons)
            if adjustment_reasons
            else ("NONE",),
            requested_delta=requested_delta,
            executable_delta=executable_delta,
        )


__all__ = [
    "ExposureEffect",
    "OrderPlanningStatus",
    "AdjustmentReason",
    "CurrentPortfolioState",
    "InstrumentRules",
    "MarketPrice",
    "OrderIntent",
    "OrderPlanningResult",
    "OrderPlanner",
]
