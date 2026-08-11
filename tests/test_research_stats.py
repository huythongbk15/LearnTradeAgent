"""Tests for alpha_research.stats — P2 statistical hardening toolkit."""

from __future__ import annotations

import numpy as np
import pytest

from trading_agent.alpha_research.stats import (
    block_bootstrap_sharpe_ci,
    deflated_sharpe_ratio,
    expected_max_sharpe,
    min_trades_check,
    probabilistic_sharpe_ratio,
    series_stats,
    summarize_sharpe,
)


def _normal_returns(n: int = 2000, mean: float = 0.0, std: float = 1.0, seed: int = 7) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(mean, std, size=n)


class TestSeriesStats:
    def test_zero_volatility_returns_zero_sharpe(self) -> None:
        s = series_stats(np.full(100, 1.0), periods_per_year=252)
        assert s.sharpe == 0.0
        assert s.annualized_sharpe == 0.0
        assert s.n == 100

    def test_positive_mean_positive_sharpe(self) -> None:
        s = series_stats(_normal_returns(mean=0.05, std=1.0), periods_per_year=252)
        assert s.sharpe > 0
        assert s.annualized_sharpe == pytest.approx(s.sharpe * np.sqrt(252), rel=1e-6)

    def test_normal_series_skew_near_zero(self) -> None:
        s = series_stats(_normal_returns(n=200_000), periods_per_year=252)
        assert abs(s.skew) < 0.05
        assert abs(s.excess_kurtosis) < 0.2

    def test_too_few_returns_raises(self) -> None:
        with pytest.raises(ValueError):
            series_stats(np.array([1.0, 2.0]), periods_per_year=252)


class TestBlockBootstrap:
    def test_zero_mean_ci_contains_zero(self) -> None:
        lo, hi, boot = block_bootstrap_sharpe_ci(
            _normal_returns(mean=0.0), periods_per_year=252, iters=200, seed=1
        )
        assert lo < 0 < hi
        assert len(boot) == 200

    def test_positive_mean_ci_mostly_positive(self) -> None:
        lo, hi, _ = block_bootstrap_sharpe_ci(
            _normal_returns(mean=0.15, std=1.0), periods_per_year=252, iters=200, seed=2
        )
        assert lo > 0

    def test_seed_reproducible(self) -> None:
        a = block_bootstrap_sharpe_ci(
            _normal_returns(), periods_per_year=252, iters=100, seed=9
        )
        b = block_bootstrap_sharpe_ci(
            _normal_returns(), periods_per_year=252, iters=100, seed=9
        )
        assert a[0] == b[0] and a[1] == b[1] and a[2] == b[2]

    def test_too_few_returns_raises(self) -> None:
        with pytest.raises(ValueError):
            block_bootstrap_sharpe_ci(
                np.array([1.0, 2.0, 3.0]), periods_per_year=252, iters=10
            )


class TestPSR:
    def test_high_sharpe_near_one(self) -> None:
        psr = probabilistic_sharpe_ratio(
            0.5, sr_benchmark=0.0, skew=0.0, excess_kurtosis=0.0, n=1000
        )
        assert psr > 0.99

    def test_negative_sharpe_near_zero(self) -> None:
        psr = probabilistic_sharpe_ratio(
            -0.5, sr_benchmark=0.0, skew=0.0, excess_kurtosis=0.0, n=1000
        )
        assert psr < 0.01

    def test_benchmark_raises_bar(self) -> None:
        low = probabilistic_sharpe_ratio(
            0.1, sr_benchmark=0.0, skew=0.0, excess_kurtosis=0.0, n=500
        )
        high = probabilistic_sharpe_ratio(
            0.1, sr_benchmark=0.2, skew=0.0, excess_kurtosis=0.0, n=500
        )
        assert high < low


class TestExpectedMaxSharpe:
    def test_single_trial_zero(self) -> None:
        assert expected_max_sharpe(1, 0.1) == 0.0

    def test_grows_with_trials(self) -> None:
        a = expected_max_sharpe(10, 0.01)
        b = expected_max_sharpe(1000, 0.01)
        assert b > a > 0.0

    def test_zero_variance_zero(self) -> None:
        assert expected_max_sharpe(100, 0.0) == 0.0


class TestDSR:
    def test_more_trials_deflates(self) -> None:
        # Small n keeps Var[SR] non-trivial so multiple-testing deflation shows.
        kwargs = dict(n=100, skew=0.0, excess_kurtosis=0.0, sr_benchmark=0.0)
        one = deflated_sharpe_ratio(0.3, trials=1, **kwargs)
        many = deflated_sharpe_ratio(0.3, trials=10_000, **kwargs)
        assert many < one

    def test_decent_sharpe_survives_few_trials(self) -> None:
        dsr = deflated_sharpe_ratio(0.5, n=200, trials=20, skew=0.0, excess_kurtosis=0.0)
        assert dsr > 0.9

    def test_strong_sharpe_deflated_by_many_trials(self) -> None:
        # After ~8000 parameter combos, a modest SR with small n deflates hard.
        dsr = deflated_sharpe_ratio(0.2, n=100, trials=8000, skew=0.0, excess_kurtosis=0.0)
        assert dsr < 0.5

    def test_zero_sharpe_below_expected_max(self) -> None:
        # SR=0 is below the expected max of 100 trials, so DSR < 0.5.
        dsr = deflated_sharpe_ratio(0.0, n=100, trials=100, skew=0.0, excess_kurtosis=0.0)
        assert dsr < 0.5


class TestMinTrades:
    def test_detects_short_folds(self) -> None:
        folds = [
            {"start": "2025-01-01", "trades": 25},
            {"start": "2025-04-01", "trades": 3},
            {"start": "2025-07-01", "trades": 40},
        ]
        violations = min_trades_check(folds, min_trades=10)
        assert len(violations) == 1
        assert "2025-04-01" in violations[0]

    def test_all_ok(self) -> None:
        folds = [{"start": "2025-01-01", "trades": 12}]
        assert min_trades_check(folds, min_trades=10) == []


class TestSummarize:
    def test_output_shape(self) -> None:
        summary = summarize_sharpe(
            _normal_returns(mean=0.02),
            periods_per_year=252,
            trials=100,
            bootstrap_iters=50,
        )
        for key in (
            "sharpe",
            "annualized_sharpe",
            "sharpe_ci95_lo",
            "sharpe_ci95_hi",
            "probabilistic_sharpe_ratio",
            "deflated_sharpe_ratio",
            "trials",
        ):
            assert key in summary
