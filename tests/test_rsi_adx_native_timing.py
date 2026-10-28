"""Synthetic characterization, not economic qualification or market admission."""

import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.backtest.tournament import (
    EvaluationCellSpec, SCENARIO_BASE, _bridge_runtime_factory, canonical_signal_series,
)
from trading_agent.strategies.canonical.candidates import build_parameterized_adapter
from trading_agent.strategies.rsi_adx_regime_filter import RsiAdxRegimeFilterStrategy


def _frame(flat=False):
    # Deliberately distinct opens/closes to detect wrong fill-bar reference.
    prices = [100.0 if flat else 100.0 + 8 * math.sin(i / 7) for i in range(230)]
    return pl.DataFrame({
        "time": [datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(230)],
        "open": [p if flat else p + 0.13 for p in prices],
        "close": prices, "high": [p + 0.4 for p in prices],
        "low": [p - 0.4 for p in prices], "volume": [1000.0] * 230,
    })


def test_flat_rsi_semantics_are_neutral_no_trade():
    strategy = RsiAdxRegimeFilterStrategy()
    frame = strategy.compute_indicators(_frame(flat=True))
    assert frame["rsi"].slice(100).to_list() == [50.0] * 130
    assert frame["_adx"].slice(100).to_list() == [0.0] * 130
    assert strategy.generate_signals(frame).slice(100).to_list() == [0] * 130


def test_native_action_and_real_entry_fill_match_two_bar_timing(tmp_path):
    from scripts.full_system_backtest import FullSystemSimulator

    frame = _frame()
    descriptor, adapter = build_parameterized_adapter("rsi_adx_regime_filter", {})
    strategy = RsiAdxRegimeFilterStrategy()
    full = strategy.generate_signals(strategy.compute_indicators(frame)).to_list()
    canonical = canonical_signal_series(
        adapter, frame, warmup_bars=descriptor.warmup_bars, symbol="BTC/USDT",
        start_index=0, end_index=frame.height - 1,
    )
    assert {1, -1}.issubset(set(canonical)), "fixture must produce both actions"
    spec = EvaluationCellSpec(
        strategy_id="rsi_adx_regime_filter", symbol="BTC/USDT", timeframe="1h",
        params={}, cost_scenario=SCENARIO_BASE,
    )
    simulator = FullSystemSimulator(
        symbol="BTC/USDT", timeframe="1h", strategy_name="rsi_adx_regime_filter",
        source_df_override=frame.rename({"time": "timestamp"}),
        data_manifest_id="d" * 64, signal_series=canonical,
        runtime_factory=_bridge_runtime_factory(spec, adapter, descriptor),
        state_dir=tmp_path / "execution", report_path=tmp_path / "report.json",
        funnel_enabled=True,
    )
    report = simulator.run()
    records = report["execution_funnel"]["records"]
    actions = [row for row in records if row.get("action") in {"BUY", "SELL"}]
    assert {"BUY", "SELL"}.issubset({r["action"] for r in actions})
    for row in actions:
        bar = row["execution_bar"]
        assert bar >= 2
        assert full[bar - 2] == {"BUY": 1, "SELL": -1}[row["action"]]
    trades = report["trades"]
    assert len(trades) >= 1, "must have a real closed trade, not only signals"
    times = frame["time"].to_list()
    for trade in trades:
        bar = times.index(datetime.fromisoformat(trade["entry_time"]))
        assert full[bar - 2] == 1
        assert any(r["execution_bar"] == bar and r["action"] == "BUY" for r in actions)
        # SCENARIO_BASE uses native 0.0005 slippage. No broad tolerance band.
        assert trade["entry_price"] == pytest.approx(frame["open"][bar] * 1.0005, rel=1e-8)
    assert report.get("measured_accounting_error") is None
