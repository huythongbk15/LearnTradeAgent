"""The locked window is one fold, and the strategy signal is the frozen rule."""

from datetime import UTC, datetime, timedelta

import polars as pl

from scripts.run_wfo_parallel import build_canonical_cells
from trading_agent.backtest.nested_wfo import WFOSpec
from trading_agent.research.btc_context_rule import CANONICAL_WARMUP_BARS
from trading_agent.strategies.own24_context import Own24ContextStrategy, attach_btc_close


def frame(count, *, jump_at=None, price=100.0):
    start = datetime(2024, 1, 1, tzinfo=UTC)
    closes = []
    for index in range(count):
        closes.append(price + 10.0 if jump_at is not None and index >= jump_at else price)
    return pl.DataFrame({
        "timestamp": [start + timedelta(hours=index) for index in range(count)],
        "time": [start + timedelta(hours=index) for index in range(count)],
        "open": closes,
        "high": [value + 0.1 for value in closes],
        "low": [value - 0.1 for value in closes],
        "close": closes,
        "volume": [1.0] * count,
    })


def test_explicit_measurement_is_one_unshifted_fold():
    spec = WFOSpec(
        strategy_id="rsi",
        symbol="ETH/USDT",
        param_grid={"period": [14]},
        evidence_class="SYNTHETIC_TEST_ONLY",
    )
    measurement = {
        "measurement_start": 216,
        "measurement_end": 300,
        "purge_bars": 216,
        "embargo_bars": 216,
    }
    cells, folds = build_canonical_cells(
        spec, frame=frame(300), explicit_measurement=measurement,
    )
    assert len(folds) == 1
    assert (folds[0].inner_val_start, folds[0].inner_val_end) == (216, 300)
    assert folds[0].inner_train_end == 216
    assert all(cell[4] == 216 and cell[5] == 300 for cell in cells)


def test_bundle_rejects_a_different_clock_and_holdout():
    target = frame(4)
    shifted = frame(4)
    shifted = shifted.with_columns(pl.col("timestamp") + pl.duration(hours=1))
    try:
        attach_btc_close(target, shifted)
        raise AssertionError("different clocks were joined")
    except ValueError as exc:
        assert "clocks" in str(exc)
    late = datetime(2026, 2, 6, 16, tzinfo=UTC)
    held = target.with_columns(pl.lit(late).alias("timestamp"))
    btc = held.with_columns(pl.col("close").alias("close"))
    try:
        attach_btc_close(held, held)
        raise AssertionError("holdout frame was joined")
    except ValueError as exc:
        assert "holdout" in str(exc)


def test_last_signal_follows_the_frozen_arm_rules():
    warmup = CANONICAL_WARMUP_BARS
    target = frame(warmup + 1, jump_at=warmup)
    btc = frame(warmup + 1, jump_at=warmup, price=50.0)
    joined = attach_btc_close(target, btc).with_columns(pl.col("timestamp").alias("time"))
    assert Own24ContextStrategy({"arm": "A0"}).generate_signals(target)[-1] == 1
    assert Own24ContextStrategy({"arm": "A1"}).generate_signals(target)[-1] == 0
    assert Own24ContextStrategy({"arm": "A1"}).generate_signals(joined)[-1] == 1
    flat_btc = attach_btc_close(target, frame(warmup + 1, price=50.0))
    flat_btc = flat_btc.with_columns(pl.col("timestamp").alias("time"))
    assert Own24ContextStrategy({"arm": "A2"}).generate_signals(flat_btc)[-1] == 0


def test_native_replay_keeps_btc_close_for_a1(tmp_path):
    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.backtest.tournament import (
        SCENARIO_BASE,
        EvaluationCellSpec,
        _bridge_runtime_factory,
        canonical_signal_series,
    )
    from trading_agent.strategies.canonical.candidates import build_parameterized_adapter

    count = CANONICAL_WARMUP_BARS + 30
    jump = CANONICAL_WARMUP_BARS + 5
    joined = attach_btc_close(
        frame(count, jump_at=jump),
        frame(count, jump_at=jump, price=50.0),
    )
    descriptor, adapter = build_parameterized_adapter("own24_context", {"arm": "A1"})
    signals = canonical_signal_series(
        adapter,
        joined.rename({"timestamp": "time"}) if "time" not in joined.columns else joined,
        warmup_bars=descriptor.warmup_bars,
        symbol="ETH/USDT",
    )
    assert any(signal != 0 for signal in signals)
    spec = EvaluationCellSpec(
        strategy_id="own24_context",
        symbol="ETH/USDT",
        timeframe="1h",
        params={"arm": "A1"},
        cost_scenario=SCENARIO_BASE,
    )
    report = FullSystemSimulator(
        symbol="ETH/USDT",
        timeframe="1h",
        strategy_name="own24_context",
        strategy_params_override={"arm": "A1"},
        source_df_override=joined,
        data_manifest_id="d" * 64,
        signal_series=signals,
        runtime_factory=_bridge_runtime_factory(spec, adapter, descriptor),
        state_dir=tmp_path / "execution",
        report_path=tmp_path / "report.json",
        funnel_enabled=False,
    ).run()
    assert report["total_trades"] > 0 or report["open_positions"]

