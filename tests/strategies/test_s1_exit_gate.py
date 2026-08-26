"""S1 exit-gate tests.

D2 — Legacy vs canonical parity on a deterministic synthetic fixture:
     for every closed bar the canonical adapter's action must match the
     legacy strategy's raw signal computed directly on the full frame
     (rolling indicators are finite-span, so windows >= span are exact).

D3 — Canonical NO_TRADE traverses the whole decision pipeline without
     producing any order intent (AbstainStrategy -> ForecastRiskPolicy ->
     zero exposure -> OrderPlanner no-action).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.execution.canonical.order_planner import (
    OrderPlanningStatus,
    OrderPlanner,
)
from trading_agent.research.forecast import ForecastRiskPolicy, MarketObservation
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.canonical import (
    FEATURE_OHLCV_WINDOW,
    AbstainStrategy,
    LegacyDataFrameAdapter,
)
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy

_OBS_AT = datetime(2026, 1, 15, tzinfo=UTC)


def _synthetic_frame(n_flat_head: int = 60) -> pl.DataFrame:
    """Deterministic OHLCV: flat → sharp drop → strong rally → flat."""
    seg_flat_a = [100.0] * n_flat_head
    seg_down = [100.0 - 2 * i for i in range(1, 31)]
    seg_up = [40.0 + 3 * i for i in range(1, 61)]
    seg_flat_b = [220.0] * 60
    closes = seg_flat_a + seg_down + seg_up + seg_flat_b
    frame = (
        pl.DataFrame({"close": closes})
        .with_columns(open=pl.col("close").shift(1).fill_null(pl.col("close")))
        .with_columns(
            high=pl.max_horizontal("open", "close") * 1.002,
            low=pl.min_horizontal("open", "close") * 0.998,
            volume=pl.lit(10.0),
        )
    )
    start = _OBS_AT - timedelta(hours=len(closes))
    return frame.with_columns(
        (pl.lit(start) + pl.duration(hours=pl.int_range(len(closes)))).alias("time")
    ).select("time", "open", "high", "low", "close", "volume")


_FRAME = _synthetic_frame()


# ── D2 parity ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("strategy_cls", "params", "warmup"),
    [
        (MaCrossover, {"fast_period": 5, "slow_period": 10}, 12),
        (RsiStrategy, {"period": 8, "oversold": 35, "overbought": 65}, 10),
        (
            BBandsStrategy,
            {"period": 12, "std_dev": 2.0},
            14,
        ),
    ],
)
def test_legacy_canonical_parity(strategy_cls, params, warmup):
    strategy = strategy_cls(params)
    with_indicators = strategy.compute_indicators(_FRAME)
    legacy_signals = strategy.generate_signals(with_indicators).to_numpy()

    adapter = LegacyDataFrameAdapter(
        strategy_cls(params),
        model_artifact_id="parity-test",
        warmup_bars=warmup,
        horizon_bars=1,
        strategy_id=strategy_cls.__name__,
    )

    times = _FRAME["time"].to_list()
    compared = 0
    for j in range(warmup + 1, len(_FRAME)):
        window = _FRAME.head(j).tail(warmup + 1)
        row = _FRAME.row(j, named=True)
        observation = MarketObservation(
            symbol="BTC/USDT",
            observed_at=times[j],
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            features={FEATURE_OHLCV_WINDOW: window},
        )
        forecast = adapter.forecast(observation)
        action = forecast.metadata["canonical_action"]

        legacy_value = float(legacy_signals[j - 1])
        expected = (
            "BUY" if legacy_value > 0 else "SELL" if legacy_value < 0 else "NO_TRADE"
        )
        assert action == expected, (
            f"parity break at bar {j}: canonical={action}, "
            f"legacy={expected} (raw={legacy_value})"
        )
        compared += 1

    # Sanity: fixture must exercise enough distinct actions.
    seen = set()
    for j in range(warmup + 1, len(_FRAME)):
        v = float(legacy_signals[j - 1])
        seen.add("BUY" if v > 0 else "SELL" if v < 0 else "NO_TRADE")
    assert compared > 150
    if strategy_cls is BBandsStrategy:
        # A perfectly linear rally keeps close inside 2σ of the rolling SMA,
        # so a steady-trend fixture legitimately never prints SELL for bands.
        assert len(seen) >= 2
    else:
        assert seen == {"BUY", "SELL", "NO_TRADE"}


# ── D3 NO_TRADE through the pipeline ───────────────────────────────────────


class TestNoTradePipeline:
    def test_abstain_zero_exposure_yields_no_intent(self):
        """Exit gate: NO_TRADE crosses the whole pipeline without an intent."""
        from trading_agent.execution.canonical.order_planner import (
            CurrentPortfolioState,
            InstrumentRules,
            MarketPrice,
            TargetExposure,
        )
        from trading_agent.execution.canonical.market_observation import (
            EnrichedMarketObservation,
        )
        from trading_agent.execution.canonical.risk_decision import (
            EvidenceState,
            RiskLevel,
            UnifiedRiskDecision,
        )
        from trading_agent.research.forecast import RiskReason

        # 1) Research layer: abstain forecast → zero allowed exposure.
        obs_research = MarketObservation(
            symbol="BTC/USDT",
            observed_at=datetime.now(UTC),
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
        )
        forecast = AbstainStrategy().forecast(obs_research)
        decision = ForecastRiskPolicy().evaluate(forecast, requested_exposure=0.40)
        assert float(decision.allowed_exposure) == 0.0

        # 2) Execution layer: zero-exposure target → no order intent.
        rules = InstrumentRules(
            symbol="BTCUSDT",
            asset_class="SPOT",
            min_order_qty=0.0001,
            max_order_qty=10.0,
            qty_step=0.0001,
            price_precision=2,
            spot_long_only=True,
            max_leverage=1.0,
        )
        now = datetime.now(UTC)
        observation = EnrichedMarketObservation(
            symbol="BTCUSDT",
            observed_at=now,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.5,
            volume=10.0,
            observation_id="obs-no-trade",
            venue="binance",
            timeframe="1h",
            bar_close_at=now,
            is_closed=True,
            data_manifest_id="manifest-nt",
        )
        portfolio = CurrentPortfolioState(
            symbol="BTCUSDT",
            equity=10_000.0,
            current_exposure=0.0,
            existing_quantity=0.0,
            avg_entry_price=0.0,
            existing_reservations=0.0,
            available_cash=10_000.0,
        )
        price = MarketPrice(
            symbol="BTCUSDT", mid=50000.0, bid=49990.0, ask=50010.0, last=50000.0
        )
        target = TargetExposure(
            symbol="BTCUSDT",
            exposure=0.0,
            horizon=1,
            forecast_fingerprint=forecast.fingerprint,
            model_artifact_id=forecast.model_artifact_id,
            risk_decision_id=decision.decision_id,
        )
        risk_decision = UnifiedRiskDecision(
            decision_id=decision.decision_id,
            forecast_fingerprint=forecast.fingerprint,
            model_artifact_id=forecast.model_artifact_id,
            risk_level=RiskLevel.LOW,
            requested_target_exposure=0.40,
            allowed_target_exposure=0.0,
            max_new_exposure=0.0,
            reduce_only=False,
            reason_codes=(RiskReason.NO_EXPECTED_EDGE,),
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-nt",
            calibration_ece=0.0,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.0,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.0,
            interval_width=0.0,
            created_at=now,
        )
        planner = OrderPlanner(instrument_rules=rules)
        result = planner.plan(
            target=target,
            risk_decision=risk_decision,
            observation=observation,
            portfolio=portfolio,
            price=price,
        )
        assert result.status == OrderPlanningStatus.NOOP
        assert result.intent is None
