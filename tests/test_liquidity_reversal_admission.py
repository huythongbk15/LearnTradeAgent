"""Synthetic admission for H-LIQ-REVERSAL-01; never reads market input."""
from datetime import UTC, datetime, timedelta
import math

import polars as pl
import pytest

from trading_agent.strategies.liquidity_reversal import (
    DEFAULT_PARAMS, LiquidityReversalStrategy,
)
from trading_agent.strategies.canonical.candidates import (
    build_default_registry, build_parameterized_adapter, validate_params,
)


def frame(closes=None):
    closes = closes or [100.0] * 40 + [98.0] * 15 + [100.0] * 25 + [98.0] * 25
    return pl.DataFrame({
        "time": [datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=i)
                 for i in range(len(closes))],
        "open": closes, "high": [c + 0.2 for c in closes],
        "low": [c - 0.2 for c in closes], "close": closes,
        "volume": [1000.0] * len(closes),
    })


def signals(df):
    strategy = LiquidityReversalStrategy()
    return strategy.generate_signals(strategy.compute_indicators(df)).to_list()


def reference(closes):
    returns = [None]
    for old, new in zip(closes, closes[1:]):
        returns.append(new / old - 1 if old and new and old > 0 and new > 0
                       and math.isfinite(old) and math.isfinite(new) else None)
    entries = set()
    for k in range(7, len(closes)):
        prior = returns[k-6:k]
        if (returns[k] is not None and returns[k] <= -0.015
                and all(r is not None and r > -0.015 for r in prior)):
            entries.add(k)
    return [-1 if k-6 in entries else 1 if k in entries else 0
            for k in range(len(closes))]


def test_fixed_shock_and_scheduled_exit_without_forward_fill():
    values = signals(frame())
    assert [(i, x) for i, x in enumerate(values) if x] == [(40, 1), (46, -1), (80, 1), (86, -1)]


@pytest.mark.parametrize("last,expected", [(98.5, 1), (98.51, 0), (100.0, 0), (102.0, 0)])
def test_threshold_boundary_and_negative_controls(last, expected):
    assert signals(frame([100.0] * 20 + [last]))[-1] == expected


def test_clustered_shocks_do_not_extend_or_restart_the_timer():
    closes = [100.0] * 20 + [98.0, 96.0, 94.0] + [94.0] * 15
    values = signals(frame(closes))
    assert [(i, x) for i, x in enumerate(values) if x] == [(20, 1), (26, -1)]
    assert values == reference(closes)


def test_unknown_predecessor_and_invalid_prices_do_not_create_a_buy():
    for bad in [None, 0.0, -1.0, float("nan"), float("inf")]:
        closes = [100.0] * 20 + [bad, 98.0]
        df = frame().head(len(closes)).with_columns(pl.Series("close", closes))
        assert 1 not in signals(df)
    assert signals(frame([98.0] * 8)) == [0] * 8


def test_full_history_matches_independent_reference_and_every_sufficient_tail():
    closes = [100.0]
    for i in range(1, 160):
        closes.append(closes[-1] * (0.98 if i % 9 in (0, 1) else 1.002))
    df = frame(closes)
    actual = signals(df)
    assert actual == reference(closes)
    for k in range(16, len(closes)):
        assert signals(df.slice(k-15, 16))[-1] == actual[k]
    changed = df.with_row_index("i").with_columns(
        pl.when(pl.col("i") > 80).then(1.0).otherwise(pl.col("close")).alias("close")
    ).drop("i")
    assert signals(changed)[:81] == actual[:81]


@pytest.mark.parametrize("params", [
    {"hold_bars": 7}, {"hold_bars": True}, {"drop_threshold": float("nan")},
    {"drop_threshold": 0.01}, {"ma_fast": 20},
])
def test_parameter_changes_are_rejected(params):
    with pytest.raises(ValueError):
        LiquidityReversalStrategy(params)


def test_registry_is_research_only_and_not_in_legacy_or_tournament_pool():
    from trading_agent.authority.config import Environment
    from trading_agent.strategies.base import list_strategies
    from trading_agent.strategies.canonical.candidates import FIRST_WAVE_DESCRIPTORS
    from trading_agent.strategies.canonical.registry import RegistryIntegrityError
    registry = build_default_registry()
    descriptor = registry.describe("liquidity_reversal")
    assert descriptor.research_only and descriptor.warmup_bars == 15
    assert descriptor.supported_symbols == ("BTC/USDT",)
    assert "liquidity_reversal" not in list_strategies()
    assert "liquidity_reversal" not in FIRST_WAVE_DESCRIPTORS
    assert validate_params("liquidity_reversal", {}) == DEFAULT_PARAMS
    with pytest.raises(RegistryIntegrityError):
        registry.get("liquidity_reversal", environment=Environment.PAPER)


def run_native(df, tmp_path):
    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.backtest.tournament import (
        EvaluationCellSpec, SCENARIO_BASE, _bridge_runtime_factory,
        canonical_signal_series,
    )
    descriptor, adapter = build_parameterized_adapter("liquidity_reversal", DEFAULT_PARAMS)
    canonical = canonical_signal_series(adapter, df, warmup_bars=descriptor.warmup_bars,
                                        symbol="BTC/USDT")
    expected = signals(df)
    assert canonical[16:] == expected[15:-1]
    spec = EvaluationCellSpec(strategy_id="liquidity_reversal", symbol="BTC/USDT",
                              timeframe="1h", params=DEFAULT_PARAMS, cost_scenario=SCENARIO_BASE)
    report = FullSystemSimulator(
        symbol="BTC/USDT", timeframe="1h", strategy_name="liquidity_reversal",
        source_df_override=df.rename({"time": "timestamp"}), data_manifest_id="d" * 64,
        signal_series=canonical, runtime_factory=_bridge_runtime_factory(spec, adapter, descriptor),
        state_dir=tmp_path / "execution", report_path=tmp_path / "report.json", funnel_enabled=True,
    ).run()
    assert report.get("measured_accounting_error") is None
    return report


def test_native_fills_obey_k_plus_two_and_six_bar_hold(tmp_path):
    df = frame()
    report = run_native(df, tmp_path)
    assert len(report["trades"]) == 2
    for trade, k in zip(sorted(report["trades"], key=lambda t: t["entry_time"]), [40, 80]):
        assert trade["reason"] == "signal"
        assert datetime.fromisoformat(trade["entry_time"]) == df["time"][k+2]
        assert datetime.fromisoformat(trade["exit_time"]) == df["time"][k+8]
        assert trade["entry_price"] == pytest.approx(df["open"][k+2] * 1.0005)
        assert trade["exit_price"] == pytest.approx(df["open"][k+8] * 0.9995)


def test_native_stop_does_not_reenter_without_a_fresh_shock(tmp_path):
    df = frame().with_row_index("i").with_columns(
        pl.when(pl.col("i") == 44).then(90.0).otherwise(pl.col("low")).alias("low")
    ).drop("i")
    report = run_native(df, tmp_path)
    assert len(report["trades"]) == 2
    trades = sorted(report["trades"], key=lambda t: t["entry_time"])
    assert trades[0]["reason"] == "stop_loss"
    assert datetime.fromisoformat(trades[1]["entry_time"]) == df["time"][82]


def test_terminal_inventory_is_retained_instead_of_forced_close(tmp_path):
    report = run_native(frame().head(45), tmp_path)
    assert report["trades"] == []
    assert report["open_positions"] == 1
    accounting = report["measured_accounting"]
    assert accounting["open_quantity"] > 0
    assert accounting["equity_basis"] == "marked_equity"
    assert accounting["equity_delta"] == pytest.approx(
        accounting["net_pnl"] + accounting["open_unrealized"], abs=1e-8
    )
