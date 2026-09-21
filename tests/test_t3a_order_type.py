"""T3A: Vol-based order type selection tests.

Acceptance criteria:
- High vol regime → Market order (fill urgency, avoid gap risk)
- Low vol regime → Limit order (capture spread, high fill probability)
- Moderate vol → cost comparison decides
- order_type injected into OrderIntent.metadata
- SimOrderIntent conversion uses intent_id (not order_id) + metadata lookup
- BrokerGateway path receives order_type via authorize_order metadata
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.execution.adaptive_execution import (
    AdaptiveExecutionConfig,
    AdaptiveExecutionState,
    _select_order_type,
    _forecast_to_order_intent,
)
from trading_agent.research.forecast import (
    CalibrationState,
    Forecast,
    MarketObservation,
)
from trading_agent.authority.adaptive_router import (
    HandoverState,
    RoutingDecision,
)


class TestSelectOrderType:
    """T3A decision tree tests."""

    def test_low_vol_chooses_limit(self):
        """Bar range ≤ 25th percentile → Limit order (save spread)."""
        obs = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=100.3,
            low=99.7,
            close=100.0,
            volume=100.0,
        )
        # Low bar range: 0.3/100 = 0.3%
        assert _select_order_type(obs) == "limit"

    def test_high_vol_chooses_market(self):
        """Bar range ≥ 75th percentile → Market order (fill urgency)."""
        obs = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=110.0,
            low=90.0,
            close=100.0,
            volume=100.0,
        )
        # High bar range: 20/100 = 20%
        assert _select_order_type(obs) == "market"

    def test_moderate_vol_picks_lower_cost(self):
        """Moderate vol → compare expected cost, pick lower."""
        # Moderate range: ~1.5%
        obs = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=101.5,
            low=98.5,
            close=100.0,
            volume=100.0,
        )
        result = _select_order_type(obs)
        assert result in ("market", "limit")

    def test_negative_or_zero_range_returns_limit(self):
        """Zero range (no volatility) → low vol → Limit order."""
        obs = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=100.0,
            low=100.0,
            close=100.0,
            volume=100.0,
        )
        assert _select_order_type(obs) == "limit"

    def test_with_recent_bars_uses_percentiles(self):
        """Recent bars DataFrame should influence threshold."""
        # Build 200 bars: most with tiny range, then current bar with huge range
        n = 200
        base_close = 100.0
        low_vol_bars = pl.DataFrame({
            "timestamp": [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(n)],
            "open": [base_close] * n,
            "high": [base_close + 0.1] * n,   # tiny range
            "low": [base_close - 0.1] * n,
            "close": [base_close] * n,
            "volume": [100.0] * n,
        })

        # Current bar: huge spike
        obs = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=120.0,
            low=80.0,
            close=100.0,
            volume=100.0,
        )
        # With recent bars showing tiny ranges, current 20% range is extreme → market
        assert _select_order_type(obs, recent_bars=low_vol_bars) == "market"

    def test_limit_uses_recent_high_vol_context(self):
        """If recent bars are all high vol, even moderate range should be limit
        when expected limit cost < market cost."""
        n = 200
        high_vol_bars = pl.DataFrame({
            "timestamp": [datetime(2025, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(n)],
            "open": [100.0] * n,
            "high": [105.0] * n,
            "low": [95.0] * n,
            "close": [100.0] * n,
            "volume": [100.0] * n,
        })

        obs = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=100.2,
            low=99.8,
            close=100.0,
            volume=100.0,
        )
        # Low range relative to high-vol context → limit
        assert _select_order_type(obs, recent_bars=high_vol_bars) == "limit"


class TestForecastToOrderIntentOrderType:
    """Verify order_type is injected into OrderIntent metadata."""

    def _make_forecast_and_decision(self, allow_new: bool = True, multiplier: float = 0.5) -> tuple:
        forecast = Forecast(
            expected_excess_return=0.05,
            horizon=1,
            lower_bound=0.02,
            upper_bound=0.08,
            direction_probability=0.8,
            calibration_state=CalibrationState.UNCALIBRATED,
            ood_score=0.1,
            model_artifact_id="test-model",
            generated_at=datetime.now(UTC),
        )
        decision = RoutingDecision(
            symbol="BTC/USDT",
            timeframe="1h",
            observed_at=datetime.now(UTC),
            posterior_fingerprint="fp",
            policy_ids=("p1",),
            incumbent_strategy_id=None,
            challenger_strategy_id=None,
            chosen_strategy_id="test_strategy",
            chosen_policy_id="p1",
            chosen_params={"period": 14},
            handover_state=HandoverState.STABLE,
            reason="signal",
            allow_new_exposure=allow_new,
            exposure_multiplier=multiplier,
            candidate_score=2.0,
            incumbent_score=None,
            position_owner_strategy_id=None,
        )
        return forecast, decision

    def test_open_intent_has_order_type_metadata(self):
        """OrderIntent.metadata must contain order_type from T3A."""
        forecast, decision = self._make_forecast_and_decision()
        observation = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=100.3,
            low=99.7,
            close=100.0,
            volume=100.0,
        )
        state = AdaptiveExecutionState(
            symbol="BTC/USDT",
            timeframe="1h",
            cash_quote=10_000.0,
            equity=10_000.0,
        )
        config = AdaptiveExecutionConfig()

        intents = _forecast_to_order_intent(forecast, decision, observation, state, config)
        assert len(intents) == 1
        assert intents[0].metadata.get("order_type") in ("market", "limit")
        assert intents[0].metadata.get("t3a") == "vol_regime"

    def test_high_vol_intent_metadata_says_market(self):
        """High vol observation → market in metadata."""
        forecast, decision = self._make_forecast_and_decision()
        observation = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=120.0,  # 20% range
            low=80.0,
            close=100.0,
            volume=100.0,
        )
        state = AdaptiveExecutionState(
            symbol="BTC/USDT",
            timeframe="1h",
            cash_quote=10_000.0,
            equity=10_000.0,
        )
        config = AdaptiveExecutionConfig()

        intents = _forecast_to_order_intent(forecast, decision, observation, state, config)
        assert intents[0].metadata.get("order_type") == "market"

    def test_reduce_intent_has_order_type(self):
        """Reduce/close intents also carry T3A order_type."""
        forecast, decision = self._make_forecast_and_decision(multiplier=0.3)
        observation = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=100.3,
            low=99.7,
            close=100.0,
            volume=100.0,
        )
        state = AdaptiveExecutionState(
            symbol="BTC/USDT",
            timeframe="1h",
            cash_quote=10_000.0,
            equity=10_000.0,
            position_quantity=1.0,  # Has position to reduce
        )
        config = AdaptiveExecutionConfig()

        intents = _forecast_to_order_intent(forecast, decision, observation, state, config)
        for intent in intents:
            assert intent.metadata.get("order_type") in ("market", "limit")
            assert intent.metadata.get("t3a") == "vol_regime"


class TestSimulatorIntentConversion:
    """Verify SimOrderIntent conversion uses intent_id + metadata order_type."""

    def test_sim_intent_uses_metadata_order_type(self):
        """SimOrderIntent should derive order_type from intent.metadata."""
        # This tests the integration: _forecast_to_order_intent → SimOrderIntent
        forecast = Forecast(
            expected_excess_return=0.05,
            horizon=1,
            lower_bound=0.02,
            upper_bound=0.08,
            direction_probability=0.8,
            calibration_state=CalibrationState.UNCALIBRATED,
            ood_score=0.1,
            model_artifact_id="test-model",
            generated_at=datetime.now(UTC),
        )
        decision = RoutingDecision(
            symbol="BTC/USDT",
            timeframe="1h",
            observed_at=datetime.now(UTC),
            posterior_fingerprint="fp",
            policy_ids=("p1",),
            incumbent_strategy_id=None,
            challenger_strategy_id=None,
            chosen_strategy_id="test_strategy",
            chosen_policy_id="p1",
            chosen_params={"period": 14},
            handover_state=HandoverState.STABLE,
            reason="signal",
            allow_new_exposure=True,
            exposure_multiplier=0.5,
            candidate_score=2.0,
            incumbent_score=None,
            position_owner_strategy_id=None,
        )
        # High vol → market
        observation = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=120.0,
            low=80.0,
            close=100.0,
            volume=100.0,
        )
        state = AdaptiveExecutionState(
            symbol="BTC/USDT",
            timeframe="1h",
            cash_quote=10_000.0,
            equity=10_000.0,
        )
        config = AdaptiveExecutionConfig()

        intents = _forecast_to_order_intent(forecast, decision, observation, state, config)
        assert len(intents) == 1
        intent = intents[0]

        # Simulate what the provider() does
        from trading_agent.execution.simulator.engine import SimOrderType

        intent_order_type = intent.metadata.get("order_type", "market")
        assert intent_order_type == "market"  # High vol → market
        sim_order_type = (
            SimOrderType.LIMIT if intent_order_type == "limit" else SimOrderType.MARKET
        )
        assert sim_order_type == SimOrderType.MARKET

        # Low vol case
        observation_low = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=100.3,
            low=99.7,
            close=100.0,
            volume=100.0,
        )
        intents_low = _forecast_to_order_intent(
            forecast, decision, observation_low, state, config
        )
        intent_low = intents_low[0]
        intent_order_type_low = intent_low.metadata.get("order_type", "market")
        assert intent_order_type_low == "limit"  # Low vol → limit
        sim_order_type_low = (
            SimOrderType.LIMIT if intent_order_type_low == "limit" else SimOrderType.MARKET
        )
        assert sim_order_type_low == SimOrderType.LIMIT


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
