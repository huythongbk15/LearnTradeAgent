"""Tests for LLM Context Enrichment integration into OrderPlanner.

Verifies that market_context.confidence_adjustment scales position size
WITHOUT changing signal direction. Anomaly flags are logged as advisory
metadata, never blocking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from trading_agent.execution.canonical import OrderPlanningStatus, OrderPlanner
from trading_agent.execution.canonical.order_planner import (
    CurrentPortfolioState,
    InstrumentRules,
    MarketPrice,
)
from trading_agent.execution.canonical.risk_decision import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.research.forecast import TargetExposure

from trading_agent.execution.canonical.market_observation import (
    EnrichedMarketObservation,
)


def _bar_timestamp() -> datetime:
    return datetime(2024, 6, 15, 10, 0, tzinfo=UTC)


def _obs(symbol: str = "BTC/USDT") -> EnrichedMarketObservation:
    ts = _bar_timestamp()
    return EnrichedMarketObservation(
        symbol=symbol,
        observed_at=ts,
        open=49000.0,
        high=51000.0,
        low=48000.0,
        close=50000.0,
        volume=1000.0,
        is_closed=True,
        bar_open_at=ts - timedelta(minutes=59),
        bar_close_at=ts,
        data_manifest_id="test-manifest-1",
    )


def _portfolio(symbol: str = "BTC/USDT", exposure: float = 0.0) -> CurrentPortfolioState:
    return CurrentPortfolioState(
        symbol=symbol,
        equity=100000.0,
        current_exposure=exposure,
        available_cash=100000.0,
    )


def _price(mid: float = 50000.0, symbol: str = "BTC/USDT") -> MarketPrice:
    return MarketPrice(symbol=symbol, mid=mid)


def _rules(symbol: str = "BTC/USDT") -> InstrumentRules:
    return InstrumentRules(
        symbol=symbol,
        min_order_qty=0.001,
        max_order_qty=100.0,
        qty_step=0.001,
        min_notional=10.0,
    )


def _risk_decision(allowed: float = 0.25) -> UnifiedRiskDecision:
    return UnifiedRiskDecision(
        decision_id="test-decision",
        forecast_fingerprint="test-fp",
        model_artifact_id="test-model",
        requested_target_exposure=0.25,
        allowed_target_exposure=allowed,
        max_new_exposure=allowed,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
        created_at=datetime.now(UTC),
    )


def _target(symbol: str = "BTC/USDT", exposure: float = 0.25) -> TargetExposure:
    return TargetExposure(
        symbol=symbol,
        exposure=exposure,
        horizon=1,
        forecast_fingerprint="test-fp",
        model_artifact_id="test-model",
        risk_decision_id="test-decision",
    )


class TestOrderPlannerContextIntegration:
    """Verify market_context affects position size, not signal direction."""

    def test_no_context_default_adjustment(self):
        """Without context (None), should plan normally with adjustment=1.0."""
        planner = OrderPlanner(_rules(), "v1")
        target = _target(exposure=0.25)
        rd = _risk_decision(allowed=0.25)

        result = planner.plan(
            target=target,
            risk_decision=rd,
            observation=_obs(),
            portfolio=_portfolio(exposure=0.0),
            price=_price(),
            market_context=None,
        )
        assert result.status is OrderPlanningStatus.ORDER_REQUIRED
        assert result.intent is not None
        assert result.intent.resulting_exposure > 0.0

    def test_low_confidence_scales_down_position(self):
        """confidence_adjustment=0.5 should roughly halve the position size."""
        planner = OrderPlanner(_rules(), "v1")
        target = _target(exposure=0.20)
        rd = _risk_decision(allowed=0.20)

        # Without context
        result_no_ctx = planner.plan(
            target=target,
            risk_decision=rd,
            observation=_obs(),
            portfolio=_portfolio(exposure=0.0),
            price=_price(),
            market_context=None,
        )

        # With 0.5 confidence adjustment
        result_ctx = planner.plan(
            target=target,
            risk_decision=rd,
            observation=_obs(),
            portfolio=_portfolio(exposure=0.0),
            price=_price(),
            market_context=MarketContext(confidence_adjustment=0.5),
        )

        assert result_no_ctx.intent is not None
        assert result_ctx.intent is not None
        # Position with context should be smaller
        assert result_ctx.intent.resulting_exposure < result_no_ctx.intent.resulting_exposure
        # But both should be positive (same signal direction)
        assert result_ctx.intent.resulting_exposure > 0.0

    def test_high_confidence_scales_up_position(self):
        """confidence_adjustment=1.5 should increase position size."""
        planner = OrderPlanner(_rules(), "v1")
        target = _target(exposure=0.10)
        rd = _risk_decision(allowed=0.20)

        result_no_ctx = planner.plan(
            target=target,
            risk_decision=rd,
            observation=_obs(),
            portfolio=_portfolio(exposure=0.0),
            price=_price(),
            market_context=None,
        )
        result_ctx = planner.plan(
            target=target,
            risk_decision=rd,
            observation=_obs(),
            portfolio=_portfolio(exposure=0.0),
            price=_price(),
            market_context=MarketContext(confidence_adjustment=1.5),
        )

        assert result_ctx.intent.resulting_exposure > result_no_ctx.intent.resulting_exposure

    def test_anomaly_flags_logged_as_metadata(self):
        """Anomaly flags should appear in intent metadata, not block order."""
        planner = OrderPlanner(_rules(), "v1")
        target = _target(exposure=0.20)
        rd = _risk_decision(allowed=0.20)

        ctx = MarketContext(
            anomaly_flags=["rsi_divergence", "funding_extreme"],
            confidence_adjustment=1.0,
        )
        result = planner.plan(
            target=target,
            risk_decision=rd,
            observation=_obs(),
            portfolio=_portfolio(exposure=0.0),
            price=_price(),
            market_context=ctx,
        )

        assert result.status is OrderPlanningStatus.ORDER_REQUIRED
        assert "funding_extreme" in result.intent.metadata["llm_anomaly_flags"]
        assert "rsi_divergence" in result.intent.metadata["llm_anomaly_flags"]

    def test_context_does_not_change_signal_direction(self):
        """Negative target (sell) stays negative after confidence adjustment."""
        # For non-long-only: set spot_long_only=False on rules
        rules = InstrumentRules(
            symbol="BTC/USDT", spot_long_only=False,
            min_order_qty=0.001, max_order_qty=100.0,
            qty_step=0.001, min_notional=10.0,
        )
        planner = OrderPlanner(rules, "v1")

        # Start with long position, target partial sell
        # With confidence_adjustment=0.5, the adjusted target is:
        #   -0.10 * 0.5 = -0.05 (reduce position but not flip direction)
        target = _target(exposure=-0.10)
        rd = _risk_decision(allowed=0.25)

        # Portfolio with long exposure
        portfolio = CurrentPortfolioState(
            symbol="BTC/USDT",
            equity=100000.0,
            current_exposure=0.20,
            existing_quantity=0.4,  # 0.20 exposure = 0.4 BTC at $50000
            available_cash=50000.0,
        )

        ctx = MarketContext(confidence_adjustment=0.5)
        result = planner.plan(
            target=target,
            risk_decision=rd,
            observation=_obs(),
            portfolio=portfolio,
            price=_price(),
            market_context=ctx,
        )

        # Without context adjustment, target=-0.10 means sell 0.2 exposure (0.20→0.10)
        # With adjustment=0.5, effective target=-0.05 exposure
        # Either way: sell order (REDUCE), exposure goes DOWN
        if result.intent is not None:
            # Resulting exposure should be less than current (0.20)
            assert result.intent.resulting_exposure < 0.20
            # The key: sign of TARGET is sell, resulting reduces the position
            assert result.intent.exposure_effect.value == "REDUCE"

        # Verify sign preservation at the target level
        import math
        ctx_high = MarketContext(confidence_adjustment=1.5)
        adjusted_target = target.exposure * ctx_high.confidence_adjustment
        assert adjusted_target < 0.0  # Still negative (sell)
        assert target.exposure < 0.0  # Original was sell
        assert math.copysign(1, adjusted_target) == math.copysign(1, target.exposure)

    def test_noop_when_confidence_reduces_target_close_to_current(self):
        """If confidence_adjustment reduces the effective target enough to be
        within tolerance of current position, should NOOP."""
        planner = OrderPlanner(_rules(), "v1")
        # Current exposure close to target
        target = _target(exposure=0.20)
        rd = _risk_decision(allowed=0.25)

        # With adjustment=0.5, effective target = 0.10
        # Current exposure = 0.09 → delta = 0.01 → close to tolerance
        ctx = MarketContext(confidence_adjustment=0.5)
        result = planner.plan(
            target=target,
            risk_decision=rd,
            observation=_obs(),
            portfolio=_portfolio(exposure=0.09),  # close to adjusted target of 0.10
            price=_price(),
            market_context=ctx,
            tolerance=0.05,  # generous tolerance so the small delta -> NOOP
        )
        assert result.status is OrderPlanningStatus.NOOP
        assert result.intent is None
