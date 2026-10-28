"""Synthetic-only contracts; expected DM values do not use strategy helpers."""

import math
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.strategies.rsi_adx_regime_filter import RsiAdxRegimeFilterStrategy
from trading_agent.research.forecast import MarketObservation
from trading_agent.strategies.canonical import OHLCV_WINDOW_FEATURE
from trading_agent.strategies.canonical.candidates import build_parameterized_adapter


def _frame(highs, lows):
    close = [(high + low) / 2 for high, low in zip(highs, lows)]
    return pl.DataFrame({"open": close, "high": highs, "low": lows,
                         "close": close, "volume": [1.0] * len(close)})


@pytest.mark.parametrize("high,low,plus,minus", [
    (11.0, 5.0, 0.0, 0.0),  # inside: up=-1 > down=-2
    (10.0, 4.0, 0.0, 0.0),  # inside: down=-1 > up=-2
    (14.0, 4.0, 2.0, 0.0),
    (11.0, 1.0, 0.0, 2.0),
    (14.0, 1.0, 0.0, 0.0),  # tied outside bar: neither wins
])
def test_directional_movement_nonnegative(high, low, plus, minus):
    frame = _frame([12.0] * 110 + [high], [3.0] * 110 + [low])
    result = RsiAdxRegimeFilterStrategy().compute_indicators(frame)
    assert result["_plus_dm"][-1] == plus
    assert result["_minus_dm"][-1] == minus


def test_zero_range_is_finite_after_warmup():
    result = RsiAdxRegimeFilterStrategy().compute_indicators(
        _frame([10.0] * 150, [10.0] * 150)
    )
    for column in ("_plus_di", "_minus_di", "_dx", "_adx"):
        values = result[column].slice(100).to_list()
        assert len(values) == 50
        assert all(v is not None and math.isfinite(v) and v == 0 for v in values)


def test_prefix_indicators_and_signals_are_causal():
    prices = [100.0 + (i % 29) * 0.7 + i * 0.03 for i in range(240)]
    frame = _frame([v + 2 for v in prices], [v - 2 for v in prices])
    strategy = RsiAdxRegimeFilterStrategy()
    prefix = strategy.compute_indicators(frame.head(180))
    whole = strategy.compute_indicators(frame)
    for column in ("rsi", "_ma_fast", "_ma_slow", "_adx"):
        assert prefix[column].to_list() == whole[column].head(180).to_list()
    assert strategy.generate_signals(prefix).to_list() == strategy.generate_signals(whole).head(180).to_list()


def test_threshold_equality_and_regime_transition_do_not_invent_ma_edge():
    indicators = pl.DataFrame({
        "_adx": [10.0, 20.0, 20.0, 19.0],
        "rsi": [10.0, 10.0, 90.0, 90.0],
        "_ma_fast": [12.0, 12.0, 8.0, 8.0],
        "_ma_slow": [10.0] * 4,
    })
    # At threshold20 use TREND; switching to it is not itself an MA cross.
    assert RsiAdxRegimeFilterStrategy().generate_signals(indicators).to_list() == [1, 0, -1, -1]


def test_default_canonical_bounded_window_matches_full_frame_previous_bar():
    prices = [100.0 + 12 * math.sin(i / 8) for i in range(240)]
    times = [datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(240)]
    frame = _frame([v + 1 for v in prices], [v - 1 for v in prices]).with_columns(
        pl.Series("time", times)
    )
    strategy = RsiAdxRegimeFilterStrategy()
    signals = strategy.generate_signals(strategy.compute_indicators(frame)).to_list()
    descriptor, adapter = build_parameterized_adapter("rsi_adx_regime_filter", {})
    actions = set()
    for i in range(120, 220):
        window = frame.slice(i - descriptor.warmup_bars - 1, descriptor.warmup_bars + 1)
        observation = MarketObservation(
            symbol="BTC/USDT", observed_at=times[i], open=prices[i],
            high=prices[i] + 1, low=prices[i] - 1, close=prices[i], volume=1.0,
            features={OHLCV_WINDOW_FEATURE: window},
        )
        action = adapter.forecast(observation).metadata["canonical_action"]
        assert action == {1: "BUY", -1: "SELL", 0: "NO_TRADE"}[signals[i - 1]]
        actions.add(action)
    assert {"BUY", "SELL"}.issubset(actions)
