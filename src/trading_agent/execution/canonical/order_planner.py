"""OrderPlanner — pure deterministic component that converts TargetExposure to OrderIntent.

All sizing, rebalancing, and validation happens HERE.  No I/O, no exchange
calls, no randomness.
"""

from __future__ import annotations

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


@dataclass(frozen=True)
class CurrentPortfolioState:
    """Immutable snapshot of the current portfolio for planning purposes."""

    symbol: str
    current_exposure: float  # signed exposure [-1, 1]
    existing_quantity: float = 0.0
    avg_entry_price: float = 0.0
    existing_reservations: float = 0.0  # reserved qty not yet filled

    def __post_init__(self) -> None:
        if not (-1.0 <= self.current_exposure <= 1.0):
            raise ValueError("current_exposure must be in [-1, 1]")
        if self.existing_quantity < 0.0:
            raise ValueError("existing_quantity must be non-negative")
        if self.existing_reservations < 0.0:
            raise ValueError("existing_reservations must be non-negative")


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

    def __post_init__(self) -> None:
        if self.min_order_qty <= 0.0:
            raise ValueError("min_order_qty must be positive")
        if self.max_order_qty < self.min_order_qty:
            raise ValueError("max_order_qty must be >= min_order_qty")
        if self.qty_step <= 0.0:
            raise ValueError("qty_step must be positive")


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


class OrderPlanner:
    """Pure deterministic component: TargetExposure -> OrderIntent.

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
    ) -> OrderIntent:
        """Produce an OrderIntent from pipeline inputs.

        Parameters
        ----------
        target:
            The TargetExposure from the risk layer.
        risk_decision:
            The unified risk decision.
        observation:
            Enriched market observation (must be closed for execution).
        portfolio:
            Current portfolio state.
        price:
            Reference price bundle.
        existing_reservations:
            Quantity already reserved but not yet filled.

        Returns
        -------
        OrderIntent
            Deterministic order intent ready for BrokerGateway submission.

        Raises
        ------
        ValueError
            If the target exposure violates instrument rules or the
            spot-long-only constraint.
        """
        if observation.bar_state.value != "closed":
            raise ValueError(
                f"cannot plan from {observation.bar_state.value} observation"
            )
        if not risk_decision.approved:
            raise ValueError("risk decision not approved; cannot plan order")
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

        # Determine resulting exposure (clamped to instrument limits)
        resulting_exposure = max(
            -self._rules.max_leverage,
            min(self._rules.max_leverage, target.exposure),
        )

        # Compute required quantity change from exposure delta
        # For spot, exposure is approximately (quantity * price) / equity.
        # We need equity to compute quantity; use price_reference as a proxy
        # when equity is not provided (caller must supply price_reference).
        # Here we derive quantity from the exposure delta using the reference price.
        # For simplicity and determinism, we treat 1 unit of exposure as
        # 1 unit of notional at the reference price.
        exposure_delta = resulting_exposure - portfolio.current_exposure
        quantity = abs(exposure_delta) * price.mid

        # Apply instrument constraints
        quantity = max(self._rules.min_order_qty, quantity)
        quantity = min(self._rules.max_order_qty, quantity)
        # Round to step
        quantity = round(quantity / self._rules.qty_step) * self._rules.qty_step
        quantity = max(self._rules.min_order_qty, quantity)

        # Determine side and effect
        if exposure_delta > 1e-12:
            side = "buy"
            effect = ExposureEffect.INCREASE
        elif exposure_delta < -1e-12:
            side = "sell"
            effect = ExposureEffect.REDUCE
        else:
            side = "buy"  # neutral, no-op
            effect = ExposureEffect.NEUTRAL

        # Compute idempotency keys
        keys = IdempotencyKeys.compute(
            decision_id=risk_decision.decision_id,
            symbol=target.symbol,
            target_exposure=target.exposure,
            horizon=target.horizon,
        )

        intent_id = (
            f"intent_{risk_decision.decision_id}_{target.symbol}"
            f"_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
        )

        return OrderIntent(
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
            resulting_exposure=resulting_exposure,
            exposure_effect=effect,
            price_reference=price.mid,
            idempotency_key=keys.intent_idempotency_key,
            created_at=datetime.now(UTC),
            metadata={
                "strategy_version": self._strategy_version,
                "target_exposure_key": keys.target_exposure_key,
                "regime_entropy": risk_decision.regime_entropy,
                "ood_score": risk_decision.ood_score,
            },
        )


__all__ = [
    "ExposureEffect",
    "CurrentPortfolioState",
    "InstrumentRules",
    "MarketPrice",
    "OrderIntent",
    "OrderPlanner",
]
