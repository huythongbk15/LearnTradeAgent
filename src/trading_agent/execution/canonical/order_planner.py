"""OrderPlanner — pure deterministic component that converts TargetExposure to OrderIntent.

All sizing, rebalancing, and validation happens HERE.  No I/O, no exchange
calls, no randomness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from trading_agent.execution.canonical.events import IdempotencyKeys
from trading_agent.execution.canonical.market_observation import (
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
        if self.min_order_qty <= 0.0:
            raise ValueError("min_order_qty must be positive")
        if self.max_order_qty < self.min_order_qty:
            raise ValueError("max_order_qty must be >= min_order_qty")
        if self.qty_step <= 0.0:
            raise ValueError("qty_step must be positive")
        if self.min_notional <= 0.0:
            raise ValueError("min_notional must be positive")


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
        Static per-symbol constraints (min qty, step, long-only, etc.)
    strategy_version:
        Used in idempotency key derivation.
    """

    def __init__(
        self,
        instrument_rules: InstrumentRules,
        strategy_version: str = "v1",
    ) -> None:
        if not isinstance(instrument_rules, InstrumentRules):
            raise TypeError("instrument_rules must be an InstrumentRules")
        self._rules = instrument_rules
        self._strategy_version = strategy_version

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
        if observation.bar_state.value != "closed":
            raise ValueError(
                f"cannot plan from {observation.bar_state.value} observation"
            )
        if target.symbol != portfolio.symbol:
            raise ValueError("target symbol must match portfolio symbol")
        if target.symbol != price.symbol:
            raise ValueError("target symbol must match price symbol")
        if target.symbol != self._rules.symbol:
            raise ValueError("target symbol must match instrument rules")

        # Spot-long-only: reject negative target
        if self._rules.spot_long_only and target.exposure < 0.0:
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
        max_allowed_by_leverage = self._rules.max_leverage
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
        quantity = raw_quantity
        adjustment_reasons: list[AdjustmentReason] = []

        # Round to qty_step — floor to avoid overspending cash
        if self._rules.qty_step > 0:
            stepped_qty = math.floor(quantity / self._rules.qty_step) * self._rules.qty_step
            if abs(stepped_qty - quantity) > 1e-12:
                adjustment_reasons.append(AdjustmentReason.QTY_STEP)
            quantity = stepped_qty

        # min_order_qty
        if quantity < self._rules.min_order_qty:
            adjustment_reasons.append(AdjustmentReason.MIN_QTY)
            quantity = self._rules.min_order_qty

        # max_order_qty
        if quantity > self._rules.max_order_qty:
            adjustment_reasons.append(AdjustmentReason.MAX_QTY)
            quantity = self._rules.max_order_qty

        # min_notional
        notional = quantity * execution_price
        if notional < self._rules.min_notional:
            min_qty_for_notional = self._rules.min_notional / execution_price
            if min_qty_for_notional > quantity:
                adjustment_reasons.append(AdjustmentReason.MIN_NOTIONAL)
                quantity = min_qty_for_notional

        # max_notional
        if self._rules.max_notional is not None:
            if notional > self._rules.max_notional:
                max_qty_for_notional = self._rules.max_notional / execution_price
                if max_qty_for_notional < quantity:
                    adjustment_reasons.append(AdjustmentReason.MAX_NOTIONAL)
                    quantity = max_qty_for_notional

        # Available cash check (for BUY) — cash feasibility intersection
        if executable_delta > 0:
            required_cash = quantity * execution_price
            if required_cash > portfolio.available_cash + 1e-9:
                adjustment_reasons.append(AdjustmentReason.INSUFFICIENT_CASH)
                cash_feasible_qty = portfolio.available_cash / execution_price
                # Round DOWN to qty_step, never up (mathematically safe floor)
                if self._rules.qty_step > 0:
                    cash_feasible_qty = (
                        math.floor(cash_feasible_qty / self._rules.qty_step)
                        * self._rules.qty_step
                    )
                # If cash cannot even cover min_order_qty after rounding down, BLOCK
                if cash_feasible_qty < self._rules.min_order_qty - 1e-12:
                    return OrderPlanningResult(
                        status=OrderPlanningStatus.BLOCKED,
                        intent=None,
                        reason_codes=("INSUFFICIENT_CASH_FOR_MIN_ORDER",)
                        + tuple(str(r) for r in adjustment_reasons),
                        requested_delta=requested_delta,
                        executable_delta=0.0,
                    )
                quantity = cash_feasible_qty
                # Clamp to [min, max]
                quantity = max(self._rules.min_order_qty, quantity)
                quantity = min(self._rules.max_order_qty, quantity)

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

        # ── Determine side and effect ────────────────────────────────────
        if executable_delta > 1e-12:
            side = "buy"
            effect = ExposureEffect.INCREASE
        elif executable_delta < -1e-12:
            side = "sell"
            effect = ExposureEffect.REDUCE
        else:
            # Should not reach here due to NOOP check above, but defensive
            return OrderPlanningResult(
                status=OrderPlanningStatus.NOOP,
                intent=None,
                reason_codes=("ZERO_DELTA_AFTER_CLAMP",),
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
            self._rules.qty_step * execution_price / portfolio.equity
            if self._rules.qty_step > 0 and portfolio.equity > 0
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
            asset_class=self._rules.asset_class,
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
