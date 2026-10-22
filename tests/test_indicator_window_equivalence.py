"""Synthetic regression: native per-bar window evaluation == full-frame evaluation
shifted by one bar.

Why this exists
---------------
The BNB native ADX25 bottleneck review (data/bnb_native_review_20261020_01)
decomposes the trade-count bottleneck by recomputing the candidate's indicators
on the full pre-holdout prefix and comparing the result with the action counts
recorded by the failed market campaign. That comparison only holds because the
canonical adapter decides on the window that ends STRICTLY BEFORE the action
bar and consumes the last signal of that window, which makes

    native_signal[j] == full_frame_signal[j - 1]

If a future change to the adapter, the point-in-time window builder, or the
indicator code breaks that equivalence, the decomposition silently becomes
wrong. This test locks the property.

Isolation
---------
Synthetic price series only. No market data, no parquet, no network, no
storage reads. Runs in well under a second and is safe inside a broad suite.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import polars as pl
import pytest

sys.path.insert(0, ".")

from trading_agent.backtest.tournament import (
    EvaluationCellSpec,
    _research_env,
    canonical_signal_series,
)
from trading_agent.strategies.canonical.candidates import build_default_registry
from trading_agent.strategies.enhanced_ma import MaAdxCrossover

PARAMS = {"fast_period": 20, "slow_period": 80, "adx_period": 14, "adx_threshold": 25.0}
N_BARS = 400
WARMUP = 100


def _cell_spec() -> EvaluationCellSpec:
    from trading_agent.backtest.tournament import SCENARIO_BASE

    return EvaluationCellSpec(
        strategy_id="ma_adx", symbol="BTC/USDT", timeframe="4h",
        params=PARAMS, cost_scenario=SCENARIO_BASE,
    )


def _synthetic_frame() -> pl.DataFrame:
    """Deterministic series with at least one MA20/80 crossover."""
    rows = []
    price = 100.0
    base = datetime(2024, 1, 1, tzinfo=timezone.utc)
    for i in range(N_BARS):
        # Saw-tooth regime so the fast MA crosses the slow MA repeatedly.
        drift = 0.9 if (i // 45) % 2 == 0 else -0.9
        price = max(5.0, price + drift)
        high = price * 1.01
        low = price * 0.99
        rows.append({
            "time": base + timedelta(hours=4 * i),
            "open": price,
            "high": high,
            "low": low,
            "close": price,
            "volume": 1000.0 + i,
        })
    return pl.DataFrame(rows)


def test_canonical_series_is_full_frame_signal_shifted_by_one():
    frame = _synthetic_frame()

    legacy = MaAdxCrossover(PARAMS)
    full_frame = legacy.generate_signals(legacy.compute_indicators(frame)).to_list()

    registry = build_default_registry()
    descriptor, adapter = registry.get("ma_adx", environment=_research_env())

    canonical = canonical_signal_series(
        adapter,
        frame,
        warmup_bars=descriptor.warmup_bars,
        symbol="BTC/USDT",
        start_index=0,
        end_index=len(frame) - 1,
    )

    assert len(canonical) == len(full_frame)

    # The window builder is fail-closed: below warmup_bars+1 there is not
    # enough history, so no signal is produced at all.
    warmup_region = [j for j in range(min(WARMUP + 1, len(canonical)))
                      if canonical[j] != 0]
    assert not warmup_region, (
        f"warmup region must be fail-closed, got signals at {warmup_region[:5]}"
    )

    # Above warmup_bars+1 the per-bar window evaluation must equal the
    # full-frame evaluation shifted by one bar.
    mismatches = [
        j
        for j in range(WARMUP + 1, len(canonical))
        if canonical[j] != full_frame[j - 1]
    ]
    assert not mismatches, (
        "canonical signal series no longer equals the full-frame series shifted "
        f"by one bar; first mismatching index {mismatches[:5]}"
    )


def test_canonical_series_is_not_equal_unshifted():
    """Guard against a tautological pass: the shift must be observable."""
    frame = _synthetic_frame()
    legacy = MaAdxCrossover(PARAMS)
    full_frame = legacy.generate_signals(legacy.compute_indicators(frame)).to_list()

    registry = build_default_registry()
    descriptor, adapter = registry.get("ma_adx", environment=_research_env())
    canonical = canonical_signal_series(
        adapter, frame, warmup_bars=descriptor.warmup_bars,
        symbol="BTC/USDT", start_index=0, end_index=len(frame) - 1,
    )
    unshifted_diffs = sum(1 for j in range(len(canonical)) if canonical[j] != full_frame[j])
    assert unshifted_diffs > 0, (
        "unshifted series already matches — the fixture does not exercise the "
        "point-in-time shift and the equivalence test is vacuous"
    )


def test_simulator_action_bar_is_two_bars_after_the_crossover_bar(tmp_path):
    """Lock the full chain: recorded action bar = crossover bar + 2.

    Two one-bar lags compose:
      1. canonical_signal_series decides on the window that ends strictly
         before bar j  ->  canonical_signal[j] == full_frame_signal[j-1]
      2. the simulator executes a signal one bar later than its index
         ->  action at bar j comes from canonical_signal[j-1]

    so action at execution_bar j == full_frame_signal[j-2].

    This was mis-derived as j-1 in the first review round: the per-window
    action COUNTS are identical under both alignments, so count matching
    cannot detect the error. Only the per-bar action vector can.
    """
    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.backtest.tournament import _bridge_runtime_factory

    frame = _synthetic_frame()
    legacy = MaAdxCrossover(PARAMS)
    full_frame = legacy.generate_signals(legacy.compute_indicators(frame)).to_list()

    registry = build_default_registry()
    descriptor, adapter = registry.get("ma_adx", environment=_research_env())
    canonical = canonical_signal_series(
        adapter, frame, warmup_bars=descriptor.warmup_bars,
        symbol="BTC/USDT", start_index=0, end_index=len(frame) - 1,
    )

    spec = _cell_spec()
    # The simulator's OHLCV validator expects a "timestamp" column; the
    # canonical signal builder expects "time".
    sim_frame = frame.rename({"time": "timestamp"})
    sim = FullSystemSimulator(
        symbol="BTC/USDT", timeframe="4h",
        source_df_override=sim_frame,
        data_manifest_id="d" * 64,
        strategy_name="ma_adx",
        strategy_params_override=PARAMS,
        signal_series=canonical,
        runtime_factory=_bridge_runtime_factory(spec, adapter, descriptor),
        state_dir=tmp_path / "execution",
        report_path=tmp_path / "report.json",
        funnel_enabled=True,
    )
    report = sim.run()

    records = (report.get("execution_funnel") or {}).get("records") or []
    assert records, "funnel produced no records — the fixture cannot prove the alignment"

    action = {1: "BUY", -1: "SELL", 0: "HOLD"}

    # (a) the simulator's own contract: the action at bar j comes from the
    #     signal series index j-1 (execute one bar later than the index).
    mismatches = []
    for rec in records:
        bar = rec["execution_bar"]
        idx = bar - 1
        expected = action[canonical[idx]] if 0 <= idx < len(canonical) else "HOLD"
        if rec["action"] != expected:
            mismatches.append((bar, rec["action"], expected))
    assert not mismatches, (
        "the simulator action at bar j is not the signal at index j-1; first "
        f"mismatches {mismatches[:5]}"
    )

    # (b) outside the warmup region that becomes crossover bar + 2, because
    #     canonical_signal[j] == full_frame_signal[j-1] there.
    warm = []
    for rec in records:
        bar = rec["execution_bar"]
        idx = bar - 2
        if idx < WARMUP + 1:
            continue
        expected = action[full_frame[idx]] if 0 <= idx < len(full_frame) else "HOLD"
        if rec["action"] != expected:
            warm.append((bar, rec["action"], expected))
    assert not warm, (
        "outside the warmup region the action bar is not the crossover bar + 2; "
        f"first mismatches {warm[:5]}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
