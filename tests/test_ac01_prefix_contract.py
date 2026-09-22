"""AC01 behavioral prefix checks; no network or production artifacts.

Timestamps represent available closed bars. Numerical tolerance is fixed at
1e-10; labels/signals and lengths must match exactly. Model tests freeze fit
on the first 180 bars. They do not certify runtime training-cutoff enforcement.
"""
from copy import deepcopy

import numpy as np
import pandas as pd
import polars as pl
import pytest
from polars.testing import assert_frame_equal, assert_series_equal

from trading_agent.ml.regime_detection import HMMStrategy, RuleBasedStrategy
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover


def prices():
    rng = np.random.default_rng(2209)
    return pd.Series(
        100 * np.exp(np.cumsum(rng.normal(0, 0.015, 420))),
        index=pd.date_range('2024-01-01', periods=420, freq='h', tz='UTC'),
    )


def assert_states_equal(left, right):
    assert len(left) == len(right)
    for a, b in zip(left, right, strict=True):
        assert a.regime == b.regime
        assert set(a.probability) == set(b.probability)
        np.testing.assert_allclose(a.confidence, b.confidence, atol=1e-10, rtol=1e-10)
        for key in a.probability:
            np.testing.assert_allclose(a.probability[key], b.probability[key], atol=1e-10, rtol=1e-10)


@pytest.mark.parametrize('cut', [220, 310])
def test_rule_based_future_mutation_and_append(cut):
    original = prices()
    mutated = original.copy()
    mutated.iloc[cut:] *= np.linspace(2, 8, len(mutated) - cut)
    prefix = RuleBasedStrategy().detect_all(original.iloc[:cut])
    assert_states_equal(prefix, RuleBasedStrategy().detect_all(original)[:cut])
    assert_states_equal(prefix, RuleBasedStrategy().detect_all(mutated)[:cut])


@pytest.mark.parametrize('cut', [220, 310])
def test_enhanced_ma_features_and_signals_prefix(cut):
    def evaluate(series):
        close = series.to_numpy()
        frame = pl.DataFrame({'timestamp': series.index.to_pydatetime().tolist(),
                              'open': close, 'high': close * 1.01,
                              'low': close * .99, 'close': close,
                              'volume': np.full(len(close), 1000.)})
        strategy = EnhancedMaCrossover()
        features = strategy.compute_indicators(frame)
        return features, strategy.generate_signals(features)

    original = prices()
    mutated = original.copy()
    mutated.iloc[cut:] *= 5
    features, signals = evaluate(original.iloc[:cut])
    for candidate in [original, mutated]:
        full_features, full_signals = evaluate(candidate)
        assert_frame_equal(features, full_features.head(cut), rel_tol=1e-10, abs_tol=1e-10)
        assert_series_equal(signals, full_signals.head(cut), check_exact=True)


def test_hmm_frozen_training_prefix():
    pytest.importorskip('hmmlearn')
    original = prices()
    model = HMMStrategy(n_regimes=3).fit(original.iloc[:180])
    prefix = deepcopy(model).predict_all(original.iloc[:300])
    mutated = original.copy()
    mutated.iloc[300:] *= 4
    assert_states_equal(prefix, deepcopy(model).predict_all(mutated)[:len(prefix)])


def test_hmm_cache_respects_new_input_length():
    pytest.importorskip('hmmlearn')
    original = prices()
    model = HMMStrategy(n_regimes=3).fit(original.iloc[:180])
    model.predict_all(original.iloc[:300])
    actual = model.predict_all(original)
    fresh = HMMStrategy(n_regimes=3).fit(original.iloc[:180]).predict_all(original)
    assert len(actual) == len(fresh), 'prediction cache reused for a different input window'
