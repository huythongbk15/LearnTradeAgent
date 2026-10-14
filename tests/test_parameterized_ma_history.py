from datetime import datetime, timedelta, timezone
import math

import polars as pl
import pytest

from trading_agent.backtest.tournament import canonical_signal_series
from trading_agent.strategies.canonical.candidates import (
    build_parameterized_adapter, campaign_history_contract, resolved_warmup_bars,
)


@pytest.mark.parametrize("params,minimum", [
    ({"slow_period": 200}, 201),
    ({"adx_period": 100, "atr_period": 100}, 201),
    ({"momentum_period": 100}, 101),
])
def test_enhanced_ma_history_follows_effective_parameters(params, minimum):
    descriptor, _ = build_parameterized_adapter("enhanced_ma", params)
    assert descriptor.warmup_bars >= minimum
    assert descriptor.warmup_bars == resolved_warmup_bars("enhanced_ma", params)


def test_enhanced_ma_grid_history_uses_longest_candidate():
    contract = campaign_history_contract("enhanced_ma", {
        "fast_period": [10, 20], "slow_period": [80, 200],
        "adx_period": [14, 100], "atr_period": [14, 100],
    })
    assert contract["warmup_bars"] == 201


def test_enhanced_ma_long_adx_is_ready_with_resolved_history():
    from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

    params = {"adx_period": 100, "atr_period": 100, "slow_period": 200}
    history = resolved_warmup_bars("enhanced_ma", params)
    prices = [100 + i * 0.1 + math.sin(i / 12) for i in range(history + 1)]
    frame = pl.DataFrame({
        "open": prices, "high": [x + 1 for x in prices],
        "low": [x - 1 for x in prices], "close": prices,
        "volume": [100.0] * len(prices),
    })
    strategy = EnhancedMaCrossover(params)
    measured = strategy.compute_indicators(frame)
    for column in ("adx", "atr", "ma_200"):
        assert measured[column][-1] is not None
        assert math.isfinite(measured[column][-1])
    assert strategy.compute_indicators(frame.head(100))["adx"][-1] is None


def test_enhanced_ma_empty_grid_fails_closed():
    with pytest.raises(ValueError, match="nonempty grid"):
        campaign_history_contract("enhanced_ma", {"slow_period": []})


@pytest.mark.parametrize("slow", [20, 50, 60, 80, 120])
def test_effective_ma_history_and_prefix_causality(slow):
    params = {"fast_period": 10, "slow_period": slow}
    descriptor, adapter = build_parameterized_adapter("ma_crossover", params)
    assert descriptor.warmup_bars == resolved_warmup_bars("ma_crossover", params)
    assert descriptor.warmup_bars >= slow + 1
    prices = [100 + 8 * math.sin(i / 12) for i in range(280)]
    frame = pl.DataFrame({
        "time": [datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=4*i) for i in range(280)],
        "open": prices, "high": [x+1 for x in prices], "low": [x-1 for x in prices],
        "close": prices, "volume": [100.0]*280,
    })
    signals = canonical_signal_series(adapter, frame, warmup_bars=descriptor.warmup_bars,
                                      symbol="BTC/USDT", end_index=230)
    prefix = canonical_signal_series(adapter, frame.head(230), warmup_bars=descriptor.warmup_bars,
                                     symbol="BTC/USDT", end_index=230)
    assert signals[:230] == prefix
    assert any(signals)


def test_grid_history_is_conservative_and_order_independent():
    grid = {"fast_period": [10, 20, 30], "slow_period": [60, 80, 120]}
    expected = {"version": 1, "warmup_bars": 121, "buffer_bars": 100}
    assert campaign_history_contract("ma_crossover", grid) == expected
    assert campaign_history_contract("ma_crossover", dict(reversed(list(grid.items())))) == expected


def test_invalid_ma_periods_fail_closed():
    with pytest.raises(ValueError):
        resolved_warmup_bars("ma_crossover", {"fast_period": -1, "slow_period": 60})


@pytest.mark.parametrize("overrides", [None, (7, 9)])
def test_frozen_fold_spans_match_runtime_history_gaps(overrides):
    from dataclasses import replace
    from trading_agent.backtest.campaign_plan import plan_folds_from_spec
    from trading_agent.backtest.nested_wfo import _default_purge_embargo, _get_fold_indices
    from trading_agent.strategies.canonical.candidates import build_default_registry

    history = campaign_history_contract("ma_crossover", {"slow_period": [120]})
    descriptor = replace(build_default_registry().describe("ma_crossover"),
                         warmup_bars=history["warmup_bars"])
    purge, embargo = overrides or _default_purge_embargo(descriptor)
    stamps = [datetime(2024, 1, 1, tzinfo=timezone.utc) + timedelta(hours=i) for i in range(4200)]
    frame = pl.DataFrame({"timestamp": stamps})
    kwargs = dict(timeframe="1h", train_months=2, val_months=1, test_months=1,
                  step_months=1, purge=purge, embargo=embargo)
    plan = plan_folds_from_spec(frame, **kwargs)
    runtime = _get_fold_indices(frame.height, **kwargs)
    assert plan and len(plan) == len(runtime)
    for frozen, actual in zip(plan, runtime):
        for name, start, end in (
            ("train_span", actual.inner_train_start, actual.inner_train_end),
            ("val_span", actual.inner_val_start, actual.inner_val_end),
            ("test_span", actual.outer_test_start, actual.outer_test_end),
        ):
            assert frozen[name] == [stamps[start].isoformat(), stamps[end-1].isoformat()]
