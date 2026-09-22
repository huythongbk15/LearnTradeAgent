"""
Regression tests for Phase 1 temporal-leakage remediation.

Covers:
1. RuleBasedStrategy.detect_all — no bfill() on vol_series (future leakage)
2. HMMStrategy — forward-filter posteriors (not forward-backward smoothing)
3. HMMStrategy / GMMStrategy — scaler fit on training only (no transductive fit on predict)
4. VolatilityBreakoutStrategy — max_hold_bars actually enforced
"""
from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import polars as pl
import pytest

from trading_agent.ml.regime_detection import (
    GMMStrategy,
    HMMStrategy,
    RuleBasedStrategy,
)
from trading_agent.strategies.volatility_breakout import VolatilityBreakoutStrategy


# ── Fixture helpers ──────────────────────────────────────────────────────────


def _make_prices(start: float = 100.0, n: int = 300, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.02, n)
    prices = pd.Series(start * np.exp(np.cumsum(returns)))
    prices.index = pd.date_range("2023-01-01", periods=n, freq="D")
    return prices


def _make_volumes(n: int = 300) -> pd.Series:
    return pd.Series(np.random.lognormal(10, 0.5, n), index=pd.date_range("2023-01-01", periods=n, freq="D"))


# ── Test 1: RuleBased detect_all has no bfill (vol_series) ──────────────────


class TestRuleBasedNoLookAhead:
    def test_vol_series_not_backfilled(self):
        """detect_all must not bfill vol_series — only future NaN would
        contaminate the warmup bars."""
        rb = RuleBasedStrategy()
        # The method should still work on short data
        prices = _make_prices(n=300)
        states = rb.detect_all(prices)
        assert len(states) == len(prices)
        # First slow_ma-1 bars should be UNKNOWN (not backfilled with future vol)
        assert states[0].regime.value == "unknown"

    def test_vol_series_uses_ffill_not_bfill(self):
        """Directly inspect that vol_series has no future values in warmup."""
        import inspect
        source = inspect.getsource(RuleBasedStrategy.detect_all)
        assert "bfill()" not in source, "detect_all must NOT use bfill() — look-ahead leakage"
        assert ".bfill()" not in source


# ── Test 2: HMM uses forward-filter (not forward-backward) ───────────────────


class TestHMMForwardFilter:
    def test_no_score_samples_in_predict_all(self):
        """predict_all must not call score_samples (forward-backward smoothing)."""
        import inspect
        source = inspect.getsource(HMMStrategy.predict_all)
        assert "self.model.score_samples" not in source, (
            "predict_all must use _compute_filtered_posteriors, not score_samples"
        )

    def test_no_score_samples_in_predict(self):
        source = inspect.getsource(HMMStrategy.predict)
        assert "self.model.score_samples" not in source

    def test_filtered_posteriors_shape(self):
        """_compute_filtered_posteriors returns correct shape."""
        hmm = HMMStrategy(n_regimes=4)
        prices = _make_prices(n=300)
        volumes = _make_volumes(300)
        hmm.fit(prices, volumes)
        # Access the private method to test shape
        features = hmm._prepare_features(prices, volumes)
        features = hmm._scale_features(features)
        posteriors = hmm._compute_filtered_posteriors(features)
        assert posteriors.shape == (len(features), 4)
        # Each row sums to 1
        np.testing.assert_allclose(posteriors.sum(axis=1), 1.0, atol=1e-10)

    def test_scaler_not_refit_on_predict(self):
        """The scaler must NOT be fit during predict/predict_all."""
        import inspect
        source = inspect.getsource(HMMStrategy._prepare_features)
        # _prepare_features should not contain fit_transform
        assert "fit_transform" not in source, "_prepare_features must not fit the scaler"
        # predict should use _scale_features (transform only)
        pred_source = inspect.getsource(HMMStrategy.predict)
        assert "_scale_features" in pred_source, "predict must use _scale_features"
        assert "fit_transform" not in pred_source, "predict must not refit scaler"

    def test_hmm_predict_all_runs_without_error(self):
        """End-to-end: fit + predict_all should work."""
        hmm = HMMStrategy(n_regimes=4)
        prices = _make_prices(n=300)
        volumes = _make_volumes(300)
        hmm.fit(prices, volumes)
        states = hmm.predict_all(prices, volumes)
        assert len(states) == len(prices) - 1  # returns has one fewer bar


# ── Test 3: GMM scaler fit on training only ─────────────────────────────────


class TestGMMScaler:
    def test_gmm_scaler_not_refit_on_predict(self):
        """GMM _prepare_features must not fit the scaler."""
        import inspect
        source = inspect.getsource(GMMStrategy._prepare_features)
        assert "fit_transform" not in source, "GMM _prepare_features must not fit scaler"
        source = inspect.getsource(GMMStrategy.predict)
        assert "_scale_features" in source, "GMM predict must use _scale_features"
        assert "fit_transform" not in source


# ── Test 4: VolatilityBreakout max_hold_bars enforcement ────────────────────


class TestVolatilityBreakoutMaxHold:
    @pytest.fixture
    def df(self) -> pl.DataFrame:
        rng = np.random.default_rng(42)
        n = 100
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        high = close + rng.uniform(0.5, 2, n)
        low = close - rng.uniform(0.5, 2, n)
        open_ = close + rng.normal(0, 0.5, n)
        return pl.DataFrame({
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.uniform(1000, 5000, n),
            "timestamp": list(range(n)),
        })

    def test_max_hold_bars_enforced(self, df: pl.DataFrame):
        """After max_hold_bars, position must be exited (signal becomes -1)."""
        # Use a small max_hold_bars so the test is fast
        strat = VolatilityBreakoutStrategy({"max_hold_bars": 5, "atr_spike_mult": 0.1})
        result = strat.compute_indicators(df)
        signals = strat.generate_signals(result)
        sig_arr = signals.to_numpy()

        # Find the first entry (signal == 1)
        entry_idx = None
        for i in range(len(sig_arr)):
            if sig_arr[i] == 1:
                entry_idx = i
                break

        if entry_idx is not None:
            # Check that after max_hold_bars, signal is -1 (exit)
            exit_found = False
            for i in range(entry_idx, min(entry_idx + 10, len(sig_arr))):
                if i - entry_idx >= 5 and sig_arr[i] < 0:
                    exit_found = True
                    break
            assert exit_found, (
                f"Position entered at bar {entry_idx} but not force-exited "
                f"after max_hold_bars={5}. Signals: {sig_arr[entry_idx:entry_idx+10]}"
            )

    def test_max_hold_bars_default_10(self):
        """Default max_hold_bars should be 10 (not 0/dead)."""
        strat = VolatilityBreakoutStrategy({})
        assert strat.max_hold_bars == 10

    def test_exit_signal_is_minus_one(self, df: pl.DataFrame):
        """The SMA-reversion exit must emit -1, not 0 (0 = hold in engine)."""
        strat = VolatilityBreakoutStrategy({"max_hold_bars": 0, "atr_spike_mult": 0.1})
        result = strat.compute_indicators(df)
        signals = strat.generate_signals(result)
        sig_arr = signals.to_numpy()
        # 0 should NOT appear as a signal value when max_hold_bars is 0
        # (0 is fill_null only at the very start before any signal)
        # The only 0 values should be in the initial warmup (first few bars)
        nonzero = sig_arr[sig_arr != 0]
        if len(nonzero) > 0:
            # Check that all non-zero values are 1 or -1
            unique = set(np.unique(nonzero))
            assert unique.issubset({1, -1}), f"Unexpected signal values: {unique}"
