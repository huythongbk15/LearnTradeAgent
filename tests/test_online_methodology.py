from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from trading_agent.ml.online.adaptive import (
    AdaptiveConfig,
    AdaptiveEMA,
    FastExpert,
    MediumExpert,
    OnlineWeightAllocator,
    SlowExpert,
)
from trading_agent.ml.online.indicators import (
    OnlineATR,
    OnlineBollingerBands,
    OnlineCorrelation,
    OnlineEMA,
    OnlineRSI,
    OnlineSMA,
    OnlineStandardDeviation,
)
from trading_agent.ml.regime_detection import (
    MarketRegime,
    RegimePosterior,
    RegimeState,
    mix_regime_forecasts,
    regime_posterior_from_state,
)


@pytest.mark.parametrize("period", [1, 2, 5, 17, 40])
def test_streaming_sma_equals_batch_rolling(period: int) -> None:
    rng = np.random.default_rng(period)
    values = rng.normal(size=250)
    online = OnlineSMA(period)
    actual = np.array([online.update(value) for value in values])
    expected = pd.Series(values).rolling(period, min_periods=1).mean().to_numpy()
    np.testing.assert_allclose(actual, expected, atol=1e-12)


@pytest.mark.parametrize("period", [2, 5, 20])
def test_streaming_ema_equals_batch_ewm(period: int) -> None:
    rng = np.random.default_rng(period + 100)
    values = rng.normal(size=200)
    online = OnlineEMA(period)
    actual = np.array([online.update(value) for value in values])
    expected = pd.Series(values).ewm(alpha=2.0 / (period + 1), adjust=False).mean()
    np.testing.assert_allclose(actual, expected.to_numpy(), atol=1e-12)


def _batch_wilder_rsi(values: np.ndarray, period: int) -> np.ndarray:
    output = np.full(len(values), 50.0)
    gains: list[float] = []
    losses: list[float] = []
    average_gain = None
    average_loss = None
    for index in range(1, len(values)):
        change = values[index] - values[index - 1]
        gain, loss = max(change, 0.0), max(-change, 0.0)
        gains.append(gain)
        losses.append(loss)
        if len(gains) < period:
            continue
        if average_gain is None:
            average_gain = np.mean(gains[-period:])
            average_loss = np.mean(losses[-period:])
        else:
            average_gain = (average_gain * (period - 1) + gain) / period
            average_loss = (average_loss * (period - 1) + loss) / period
        output[index] = (
            100.0
            if average_loss == 0.0
            else 100.0 - 100.0 / (1.0 + average_gain / average_loss)
        )
    return output


def test_streaming_rsi_equals_wilder_batch() -> None:
    rng = np.random.default_rng(31)
    values = 100.0 + np.cumsum(rng.normal(size=300))
    online = OnlineRSI(14)
    actual = np.array([online.update(value) for value in values])
    np.testing.assert_allclose(actual, _batch_wilder_rsi(values, 14), atol=1e-12)


def test_streaming_bollinger_equals_batch_population_std() -> None:
    rng = np.random.default_rng(44)
    values = rng.normal(size=200)
    period = 20
    online = OnlineBollingerBands(period, 2.0)
    actual = np.array([online.update(value) for value in values])
    series = pd.Series(values)
    middle = series.rolling(period, min_periods=1).mean().to_numpy()
    std = series.rolling(period, min_periods=period).std(ddof=0).to_numpy()
    std = np.where(np.isfinite(std), std, 0.0)
    expected = np.column_stack([middle, middle + 2.0 * std, middle - 2.0 * std])
    np.testing.assert_allclose(actual, expected, atol=1e-10)


def _batch_wilder_atr(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int
) -> np.ndarray:
    output = np.zeros(len(closes))
    true_ranges: list[float] = []
    average = None
    for index in range(1, len(closes)):
        value = max(
            highs[index] - lows[index],
            abs(highs[index] - closes[index - 1]),
            abs(lows[index] - closes[index - 1]),
        )
        true_ranges.append(value)
        if len(true_ranges) < period:
            continue
        average = (
            float(np.mean(true_ranges[-period:]))
            if average is None
            else (average * (period - 1) + value) / period
        )
        output[index] = average
    return output


def test_streaming_atr_equals_wilder_batch() -> None:
    rng = np.random.default_rng(53)
    closes = 100.0 + np.cumsum(rng.normal(size=250))
    highs = closes + rng.uniform(0.0, 2.0, len(closes))
    lows = closes - rng.uniform(0.0, 2.0, len(closes))
    online = OnlineATR(14)
    actual = np.array(
        [
            online.update(high, low, close)
            for high, low, close in zip(highs, lows, closes)
        ]
    )
    np.testing.assert_allclose(actual, _batch_wilder_atr(highs, lows, closes, 14))


@pytest.mark.parametrize("period", [5, 20, 50])
def test_streaming_std_and_correlation_equal_batch(period: int) -> None:
    rng = np.random.default_rng(period + 77)
    x = rng.normal(size=400)
    y = 0.4 * x + rng.normal(size=400)
    std_online = OnlineStandardDeviation(period)
    corr_online = OnlineCorrelation(period)
    actual_std = np.array([std_online.update(value) for value in x])
    actual_corr = np.array([corr_online.update(a, b) for a, b in zip(x, y)])
    expected_std = (
        pd.Series(x).rolling(period, min_periods=2).std(ddof=1).fillna(0.0).to_numpy()
    )
    expected_corr = pd.Series(x).rolling(period, min_periods=2).corr(pd.Series(y))
    expected_corr = (
        expected_corr.replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy()
    )
    np.testing.assert_allclose(actual_std, expected_std, atol=1e-10)
    np.testing.assert_allclose(actual_corr, expected_corr, atol=1e-10)


def test_market_and_outcome_updates_are_separate_and_single() -> None:
    values = np.linspace(100.0, 110.0, 80)
    adaptive = AdaptiveEMA(AdaptiveConfig(min_period=5, max_period=30))
    reference = OnlineEMA(5)
    actual = []
    expected = []
    for index, value in enumerate(values):
        if index:
            adaptive.observe_outcome(0.001)
        actual.append(adaptive.observe_market(value))
        expected.append(reference.update(value))
    np.testing.assert_allclose(actual, expected)
    assert adaptive.market_observations == len(values)
    assert adaptive.outcome_observations == len(values) - 1
    assert adaptive.current_period == 5


def _run_allocator(turnover_penalty: float) -> tuple[np.ndarray, OnlineWeightAllocator]:
    allocator = OnlineWeightAllocator(
        [FastExpert(3), MediumExpert(8), SlowExpert(20)],
        learning_rate=20.0,
        max_weight=0.6,
        turnover_penalty=turnover_penalty,
        min_observations=5,
    )
    prices = 100.0 + np.sin(np.arange(120) / 5.0) + np.arange(120) * 0.03
    history = []
    for index, price in enumerate(prices):
        forecast = allocator.observe_market(float(price))
        realized = 0.0 if index == len(prices) - 1 else prices[index + 1] / price - 1.0
        weights = allocator.observe_outcome(
            realized, observation_id=forecast.observation_id
        )
        history.append([weights[name] for name in ("fast", "medium", "slow")])
    return np.asarray(history), allocator


def test_fixed_expert_allocator_constraints_history_and_reproducibility() -> None:
    first_history, first = _run_allocator(turnover_penalty=1.0)
    second_history, second = _run_allocator(turnover_penalty=1.0)
    np.testing.assert_allclose(first_history, second_history)
    np.testing.assert_allclose(first_history.sum(axis=1), 1.0)
    assert np.all(first_history >= 0.0)
    assert np.all(first_history <= 0.6 + 1e-12)
    assert all(expert.observation_count == 120 for expert in first.experts)
    assert all(expert.outcome_count == 120 for expert in first.experts)
    assert first.outcome_count == second.outcome_count == 120


def test_turnover_penalty_reduces_weight_turnover() -> None:
    unpenalized, _ = _run_allocator(turnover_penalty=0.0)
    penalized, _ = _run_allocator(turnover_penalty=20.0)
    unpenalized_turnover = np.abs(np.diff(unpenalized, axis=0)).sum()
    penalized_turnover = np.abs(np.diff(penalized, axis=0)).sum()
    assert penalized_turnover <= unpenalized_turnover + 1e-12


def test_regime_entropy_never_increases_conviction_and_unknown_abstains() -> None:
    forecasts = {
        name: 1.0 for name in ("trend", "mean_reversion", "high_vol", "crisis", "other")
    }
    posteriors = [
        RegimePosterior(1.0, 0.0, 0.0, 0.0, 0.0),
        RegimePosterior(0.8, 0.05, 0.05, 0.05, 0.05),
        RegimePosterior(0.6, 0.1, 0.1, 0.1, 0.1),
        RegimePosterior(0.2, 0.2, 0.2, 0.2, 0.2),
    ]
    mixtures = [mix_regime_forecasts(posterior, forecasts) for posterior in posteriors]
    entropies = [mixture.normalized_entropy for mixture in mixtures]
    convictions = [abs(mixture.forecast) for mixture in mixtures]
    assert entropies == sorted(entropies)
    assert convictions == sorted(convictions, reverse=True)
    assert mixtures[-1].abstained
    assert mixtures[-1].forecast == 0.0

    unknown = regime_posterior_from_state(
        RegimeState(MarketRegime.UNKNOWN, 0.0, {}, pd.Timestamp("2025-01-01"))
    )
    assert unknown == RegimePosterior(0.2, 0.2, 0.2, 0.2, 0.2)
