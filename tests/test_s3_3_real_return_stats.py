"""S3-3: statistical hardening on REAL return series (P0).

The pre-S3-3 code path approximated CI/PSR/DSR from a handful of fold-level
Sharpe aggregates.  This module exercises the new real-return-series path:

  * ``reporting.calculate_performance_metrics`` now emits ``return_series`` —
    the actual per-bar OOS return values of the measurement window.
  * ``nested_wfo.run_nested_wfo`` consumes ``return_series`` from every outer
    fold artifact, concatenates them into one continuous OOS sample, and runs
    block-bootstrap CI / PSR / DSR / CSCV-PBO on those REAL returns.
  * DSR's trial count still comes from the append-only registry (S3-2), not a
    hand-maintained counter.

These integration tests run on the synthetic evidence pipeline so they stay CI-
safe (no multi-year real data, no heavy backtest timeout).
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.alpha_research.stats import (
    block_bootstrap_sharpe_ci,
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
    probabilistic_sharpe_ratio,
    series_stats,
)
from trading_agent.backtest.nested_wfo import (
    NestedFold,
    WFOInnerResult,
    _build_pbo_candidate_returns,
    run_nested_wfo,
)
from trading_agent.backtest.synthetic_data import (
    generate_synthetic_ohlcv,
    synthetic_wfo_spec,
)


N_BARS = 1000
HOLDOUT_START = 800


def _synthetic_df():
    return generate_synthetic_ohlcv(
        symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS, seed=7
    )


def _fake_folds():
    # Outer windows aligned to the synthetic RSI candidate's actual trade bars
    # so each validation and outer-OOS window still contains real trades under
    # proper measurement-window isolation (S3-1).
    return [
        NestedFold(
            fold_id="f1",
            inner_train_start=0,
            inner_train_end=400,
            inner_val_start=400,
            inner_val_end=620,
            outer_test_start=620,
            outer_test_end=800,
            purge=0,
            embargo=0,
        ),
        NestedFold(
            fold_id="f2",
            inner_train_start=200,
            inner_train_end=600,
            inner_val_start=600,
            inner_val_end=720,
            outer_test_start=720,
            outer_test_end=800,
            purge=0,
            embargo=0,
        ),
    ]


@pytest.fixture
def patched(monkeypatch):
    df = _synthetic_df()
    folds = _fake_folds()

    def _load(*args, **kwargs):
        return df

    monkeypatch.setattr("trading_agent.data.storage.load_ohlcv", _load)
    monkeypatch.setattr("trading_agent.backtest.tournament.load_ohlcv", _load)
    monkeypatch.setattr(
        "trading_agent.backtest.nested_wfo._resolve_frozen_holdout_window",
        lambda *a, **kw: (HOLDOUT_START, N_BARS - 1),
    )
    monkeypatch.setattr(
        "trading_agent.backtest.nested_wfo._get_fold_indices",
        lambda *a, **kw: folds,
    )
    return df, folds


# ---------------------------------------------------------------------------
# Unit-level: reporting + stats module
# ---------------------------------------------------------------------------


def test_calculate_performance_metrics_emits_real_return_series():
    """reporting.calculate_performance_metrics exposes the measurement-window
    per-bar return series, not just aggregate metrics."""
    from trading_agent.backtest.reporting import calculate_performance_metrics

    equity = [
        (f"2025-01-01T{i:02d}:00:00+00:00", 100_000.0 + i * 10.0) for i in range(20)
    ]
    metrics = calculate_performance_metrics(
        equity,
        initial_capital=100_000.0,
        timeframe_delta=timedelta(hours=1),
        trades=[],
    )
    assert "return_series" in metrics
    series = metrics["return_series"]
    assert isinstance(series, list)
    assert len(series) == len(equity) - 1
    # All values must be finite floats.
    assert all(np.isfinite(v) for v in series)
    # Spot-check against direct computation.
    expected = equity[1][1] / equity[0][1] - 1.0
    assert abs(series[0] - expected) < 1e-12


def test_calculate_performance_metrics_empty_equity():
    """Degenerate equity curve yields an empty return series, not an error."""
    from trading_agent.backtest.reporting import calculate_performance_metrics

    metrics = calculate_performance_metrics(
        [("2025-01-01T00:00:00+00:00", 100_000.0)],
        initial_capital=100_000.0,
        timeframe_delta=timedelta(hours=1),
        trades=[],
    )
    assert metrics["return_series"] == []


def test_series_stats_uses_real_returns_not_proxies():
    """series_stats / PSR / DSR operate on real return observations."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0005, 0.01, 500)
    s = series_stats(returns, periods_per_year=252)
    assert s.n == 500
    assert s.sharpe != 0.0
    psr = probabilistic_sharpe_ratio(
        s.sharpe,
        sr_benchmark=0.0,
        skew=s.skew,
        excess_kurtosis=s.excess_kurtosis,
        n=s.n,
    )
    assert 0.0 <= psr <= 1.0
    dsr = deflated_sharpe_ratio(
        s.sharpe,
        n=s.n,
        trials=100,
        skew=s.skew,
        excess_kurtosis=s.excess_kurtosis,
        sr_benchmark=0.0,
    )
    assert dsr <= psr + 1e-10


def test_block_bootstrap_uses_real_returns():
    """Bootstrap CI on real returns yields finite bounds."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0.0005, 0.01, 200)
    lo, hi, _ = block_bootstrap_sharpe_ci(returns, periods_per_year=252, seed=42)
    assert np.isfinite(lo) and np.isfinite(hi)
    assert lo <= hi


def test_pbo_matrix_uses_aligned_chronological_candidate_returns():
    """CSCV input is observations x candidates, never fold Sharpe proxies."""

    def candidate(params, returns):
        return {
            "params": params,
            "cost_scenario": "1x",
            "val_metrics": {"return_series": returns},
        }

    fold_1 = WFOInnerResult(
        fold_id="f1",
        train_start=0,
        train_end=50,
        val_start=50,
        val_end=66,
        best_params={"x": 1},
        best_val_sharpe=1.0,
        val_metrics={},
        n_trials=2,
        candidate_metrics=[
            candidate({"x": 1}, [0.002, -0.001] * 8),
            candidate({"x": 2}, [-0.001, 0.002] * 8),
        ],
    )
    fold_2 = WFOInnerResult(
        fold_id="f2",
        train_start=16,
        train_end=66,
        val_start=66,
        val_end=82,
        best_params={"x": 2},
        best_val_sharpe=1.0,
        val_metrics={},
        n_trials=2,
        candidate_metrics=[
            candidate({"x": 1}, [0.001, -0.0005] * 8),
            candidate({"x": 2}, [-0.0005, 0.001] * 8),
        ],
    )

    matrix, evidence = _build_pbo_candidate_returns([fold_2, fold_1])
    assert matrix is not None
    assert matrix.shape == (32, 2)
    assert evidence["status"] == "READY"
    pbo = probability_of_backtest_overfitting(matrix, n_slices=8)
    assert 0.0 <= pbo <= 1.0


def test_pbo_rejects_insufficient_evidence():
    with pytest.raises(
        ValueError, match="at least 24 finite chronological observations"
    ):
        probability_of_backtest_overfitting(np.ones((16, 2)), n_slices=8)
    with pytest.raises(ValueError, match="2 candidate columns"):
        probability_of_backtest_overfitting(np.ones((32, 1)), n_slices=8)


# ---------------------------------------------------------------------------
# Integration: nested_wfo consuming real return series
# ---------------------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.wfo
def test_s3_3_statistical_hardening_on_synthetic(patched, tmp_path):
    """End-to-end: run_nested_wfo on synthetic data produces statistical
    hardening computed from REAL return series (not fold-Sharpe proxies)."""
    _df, _folds = patched
    spec, _, _ = synthetic_wfo_spec(
        strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=N_BARS
    )
    spec = replace(spec, registry_path=str(tmp_path / "wfo.sqlite3"))

    result = run_nested_wfo(spec, out_root=tmp_path / "wfo_evidence", run_holdout=False)

    sh = result.statistical_hardening
    # Real return series must have been collected from outer fold artifacts.
    assert sh.get("return_series_folds", 0) >= 1, sh
    assert sh.get("return_series_observations", 0) >= 3, sh

    # CI / PSR / DSR must be computed from real returns.
    assert sh.get("sharpe_ci95_lo") is not None, sh
    assert sh.get("sharpe_ci95_hi") is not None, sh
    assert sh.get("sharpe_ci95_lo") <= sh.get("sharpe_ci95_hi"), sh
    assert sh.get("psr") is not None, sh
    assert 0.0 <= sh["psr"] <= 1.0, sh
    assert sh.get("dsr") is not None, sh
    assert sh["dsr"] <= sh["psr"] + 1e-10, sh

    # DSR trial count must still come from the registry (S3-2), not hardcoded.
    assert sh.get("effective_trial_count", 0) >= 1, sh
    assert sh.get("dsr_trials", 0) >= 1, sh

    # This deliberately small synthetic search has only one candidate, so CSCV
    # must fail closed instead of inventing pbo=0.
    assert "pbo" in sh, sh
    assert sh["pbo"] is None, sh
    assert sh["pbo_matrix"]["status"] == "INVALID", sh
    assert "at least two candidates" in sh["pbo_matrix"]["reason"], sh
