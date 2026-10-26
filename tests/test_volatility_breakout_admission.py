"""Admission only for the registered volatility-expansion default. No market input."""
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.strategies.canonical.candidates import validate_params
from trading_agent.strategies.volatility_breakout import VolatilityBreakoutStrategy

PARAMS = validate_params("volatility_breakout", {
    "bb_period": 14,
    "bb_std": 2.0,
    "compression_percentile": 0.03,
    "atr_spike_mult": 1.2,
    "max_hold_bars": 20,
    "atr_period": 14,
    "volume_lookback": 20,
})


def _strategy():
    return VolatilityBreakoutStrategy(PARAMS)


def _frame():
    n = 160
    prices = [100.0 + (0.05 if i % 2 == 0 else -0.05) for i in range(n)]
    prices[90] = 112.0
    for i in range(91, n):
        prices[i] = 112.0 - 0.35 * (i - 90)
    high = [p + 0.4 for p in prices]
    low = [p - 0.4 for p in prices]
    high[90] = 113.0
    low[90] = 99.0
    return pl.DataFrame({
        "time": [datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(n)],
        "open": prices,
        "high": high,
        "low": low,
        "close": prices,
        "volume": [1000.0] * n,
    })


def test_upper_band_cross_needs_an_atr_spike_and_exit_is_separate():
    strategy = _strategy()
    frame = pl.DataFrame({
        "close": [100.0, 110.0],
        "bb_upper": [105.0, 105.0],
        "bb_lower": [90.0, 90.0],
        "sma": [100.0, 100.0],
        "atr_spike": [2.0, 2.0],
    })
    assert strategy.generate_signals(frame).to_list() == [0, 1]
    quiet = frame.with_columns(pl.lit(1.0).alias("atr_spike"))
    assert strategy.generate_signals(quiet).to_list()[-1] != 1
    # A cross back through the average emits the long-only exit value.
    exiting = pl.DataFrame({
        "close": [110.0, 99.0],
        "bb_upper": [120.0, 120.0],
        "bb_lower": [80.0, 80.0],
        "sma": [100.0, 100.0],
        "atr_spike": [1.0, 1.0],
    })
    assert strategy.generate_signals(exiting).to_list()[-1] == -1


def test_actual_indicator_fixture_is_causal():
    strategy = _strategy()
    frame = _frame()
    signals = strategy.generate_signals(strategy.compute_indicators(frame)).to_list()
    assert signals[90] == 1
    assert signals[101] == -1
    assert signals[90:101] == [1] * 11
    changed = frame.with_row_index("i").with_columns(
        pl.when(pl.col("i") > 95).then(50.0).otherwise(pl.col("close")).alias("close"),
        pl.when(pl.col("i") > 95).then(51.0).otherwise(pl.col("high")).alias("high"),
        pl.when(pl.col("i") > 95).then(49.0).otherwise(pl.col("low")).alias("low"),
    )
    changed_signals = strategy.generate_signals(strategy.compute_indicators(changed.drop("i"))).to_list()
    assert changed_signals[:96] == signals[:96]


def test_flat_prices_have_no_buy():
    frame = _frame().with_columns(
        pl.lit(100.0).alias("open"),
        pl.lit(100.0).alias("close"),
        pl.lit(100.2).alias("high"),
        pl.lit(99.8).alias("low"),
    )
    signals = _strategy().generate_signals(_strategy().compute_indicators(frame)).to_list()
    assert 1 not in signals


def test_canonical_native_trade_and_two_bar_timing(tmp_path):
    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.backtest.tournament import (
        EvaluationCellSpec,
        SCENARIO_BASE,
        _bridge_runtime_factory,
        canonical_signal_series,
    )
    from trading_agent.strategies.canonical.candidates import build_parameterized_adapter

    frame = _frame()
    strategy = _strategy()
    full = strategy.generate_signals(strategy.compute_indicators(frame)).to_list()
    descriptor, adapter = build_parameterized_adapter("volatility_breakout", PARAMS)
    canonical = canonical_signal_series(
        adapter, frame, warmup_bars=descriptor.warmup_bars, symbol="BTC/USDT", end_index=159,
    )
    # The registered 36-bar window sees the expansion, then exits earlier than a
    # full-history pass. The engine measures this window, so the test pins it.
    assert descriptor.warmup_bars == 36
    assert canonical[91] == 1
    assert canonical[96] == -1
    assert full[90] == 1
    spec = EvaluationCellSpec(
        strategy_id="volatility_breakout",
        symbol="BTC/USDT",
        timeframe="1h",
        params=PARAMS,
        cost_scenario=SCENARIO_BASE,
    )
    report = FullSystemSimulator(
        symbol="BTC/USDT",
        timeframe="1h",
        strategy_name="volatility_breakout",
        source_df_override=frame.rename({"time": "timestamp"}),
        data_manifest_id="d" * 64,
        signal_series=canonical,
        runtime_factory=_bridge_runtime_factory(spec, adapter, descriptor),
        state_dir=tmp_path / "execution",
        report_path=tmp_path / "report.json",
        funnel_enabled=True,
    ).run()
    assert len(report["trades"]) == 1
    trade = report["trades"][0]
    assert trade["reason"] == "signal"
    assert datetime.fromisoformat(trade["entry_time"]) == frame["time"][92]
    assert datetime.fromisoformat(trade["exit_time"]) == frame["time"][97]
    assert trade["entry_price"] == pytest.approx(frame["open"][92] * 1.0005, rel=1e-8)
    assert trade["exit_price"] == pytest.approx(frame["open"][97] * 0.9995, rel=1e-8)
    assert report.get("measured_accounting_error") is None
