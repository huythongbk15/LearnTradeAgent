"""Statistical hardening for strategy evidence (P2).

Point-estimate Sharpe is not enough: a strategy that "works" on 6 folds can
still be the best of thousands of trials by luck.  This module implements the
Bailey & Lopez de Prado toolkit used by the evidence generator:

  * ``block_bootstrap_sharpe_ci`` — circular block bootstrap confidence
    interval for annualized Sharpe (handles serial correlation in returns).
  * ``probabilistic_sharpe_ratio`` — PSR: probability the true Sharpe exceeds
    a benchmark given observed skew/kurtosis (Bailey & Lopez de Prado 2012).
  * ``deflated_sharpe_ratio`` — DSR: PSR against the *expected maximum* Sharpe
    of N independent trials, so multiple-testing deflates the estimate
    (Bailey & Lopez de Prado 2014).
  * ``min_trades_check`` — reject folds with too few trades for a stable
    estimate.

References
----------
- Bailey, D. and Lopez de Prado, M. (2012). "The Sharpe Ratio Efficient
  Frontier." Journal of Risk, 15(2).
- Bailey, D. and Lopez de Prado, M. (2014). "The Deflated Sharpe Ratio:
  Correcting for Selection Bias, Backtest Overfitting and Non-Normality."
  Journal of Portfolio Management, 40(5).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations
from typing import Any, Iterable, Protocol

import numpy as np
from scipy import stats

# Euler–Mascheroni constant used in the expected-maximum-Sharpe approximation.
_EULER_GAMMA = 0.5772156649015329


@dataclass(frozen=True)
class SharpeStats:
    """Aggregate statistics of a return series."""

    sharpe: float
    annualized_sharpe: float
    skew: float
    excess_kurtosis: float
    n: int


class ExperimentRegistryLike(Protocol):
    """Minimal registry surface needed for registry-derived DSR accounting."""

    def trial_counts(self, *, experiment_ids: Iterable[str] | None = None) -> Any: ...


def series_stats(
    returns: np.ndarray,
    periods_per_year: float,
    annualized: bool = True,
) -> SharpeStats:
    """Compute (annualized) Sharpe, skew and excess kurtosis of ``returns``."""
    returns = np.asarray(returns, dtype=np.float64)
    returns = returns[np.isfinite(returns)]
    n = len(returns)
    if n < 3:
        raise ValueError(f"need at least 3 returns, got {n}")
    mean = float(np.mean(returns))
    std = float(np.std(returns, ddof=1))
    if std == 0.0:
        # Degenerate series: no volatility, no tradeable signal.
        return SharpeStats(
            sharpe=0.0,
            annualized_sharpe=0.0,
            skew=0.0,
            excess_kurtosis=0.0,
            n=n,
        )
    centered = returns - mean
    skew = float(np.mean(centered**3) / std**3)
    excess_kurtosis = float(np.mean(centered**4) / std**4 - 3.0)
    sr = mean / std
    return SharpeStats(
        sharpe=sr,
        annualized_sharpe=sr * math.sqrt(periods_per_year) if annualized else sr,
        skew=skew,
        excess_kurtosis=excess_kurtosis,
        n=n,
    )


def _block_indices(n: int, block_len: int, rng: np.random.Generator) -> list[int]:
    """Circular block bootstrap index sequence.

    Picks ``n // block_len`` random blocks (with wrap-around) then a random
    prefix to reach exactly ``n`` samples, matching Politis & Romano's
    circular bootstrap.
    """
    full_blocks = n // block_len
    indices: list[int] = []
    for _ in range(full_blocks):
        start = int(rng.integers(0, n))
        for offset in range(block_len):
            indices.append((start + offset) % n)
    remainder = n - len(indices)
    if remainder > 0:
        start = int(rng.integers(0, n))
        for offset in range(remainder):
            indices.append((start + offset) % n)
    return indices


def block_bootstrap_sharpe_ci(
    returns: np.ndarray,
    *,
    periods_per_year: float,
    block_len: int | None = None,
    iters: int = 1_000,
    seed: int = 42,
    confidence: float = 0.95,
) -> tuple[float, float, list[float]]:
    """Circular-block bootstrap CI for annualized Sharpe.

    ``block_len`` defaults to ``max(1, int(sqrt(n)))``, a common heuristic for
    the optimal block size.  Returns ``(lo, hi, bootstrap_sharpes)`` where
    ``lo``/``hi`` are the ``(1-confidence)/2`` and ``(1+confidence)/2``
    quantiles of the bootstrap distribution.
    """
    returns = np.asarray(returns, dtype=np.float64)
    returns = returns[np.isfinite(returns)]
    n = len(returns)
    if n < 10:
        raise ValueError(f"need at least 10 returns for bootstrap, got {n}")
    if block_len is None:
        block_len = max(1, int(math.sqrt(n)))
    block_len = max(1, min(block_len, n))
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be in (0, 1)")
    rng = np.random.default_rng(seed)
    boot: list[float] = []
    for _ in range(iters):
        sample = returns[_block_indices(n, block_len, rng)]
        mean = float(np.mean(sample))
        std = float(np.std(sample, ddof=1))
        boot.append(mean / std * math.sqrt(periods_per_year) if std > 0 else 0.0)
    alpha = (1.0 - confidence) / 2.0
    lo, hi = float(np.quantile(boot, alpha)), float(np.quantile(boot, 1.0 - alpha))
    return lo, hi, boot


def _normal_cdf(value: float) -> float:
    return float(stats.norm.cdf(value))


def _normal_ppf(value: float) -> float:
    return float(stats.norm.ppf(value))


def _variance_of_sharpe(
    sr: float,
    skew: float,
    excess_kurtosis: float,
    n: int,
) -> float:
    """Asymptotic variance of the Sharpe estimate (Lo 2002, iid version)."""
    return (1.0 - skew * sr + (excess_kurtosis - 1.0) / 4.0 * sr**2) / max(n - 1, 1)


def probabilistic_sharpe_ratio(
    sr: float,
    *,
    sr_benchmark: float,
    skew: float,
    excess_kurtosis: float,
    n: int,
) -> float:
    """PSR: P(true SR > ``sr_benchmark``) given observed skew/kurtosis.

    Formula (Bailey & Lopez de Prado 2012):
        PSR = Phi( (SR - SR*) / sqrt( Var[SR] ) )
    where ``Var[SR] = (1 - skew*SR + (kurt-1)/4*SR^2) / (n-1)`` already
    accounts for sample size, so no extra sqrt(n-1) is applied here.
    """
    if n < 3:
        raise ValueError(f"need at least 3 observations, got {n}")
    denominator = math.sqrt(_variance_of_sharpe(sr, skew, excess_kurtosis, n))
    if denominator == 0.0:
        return 1.0 if sr > sr_benchmark else 0.0
    return _normal_cdf((sr - sr_benchmark) / denominator)


def expected_max_sharpe(trials: int, variance_of_sharpe: float) -> float:
    """E[max SR] of ``trials`` independent strategies (Bailey 2014, eq. 12)."""
    if trials < 1:
        raise ValueError("trials must be >= 1")
    if variance_of_sharpe <= 0.0:
        return 0.0
    if trials == 1:
        return 0.0
    z1 = _normal_ppf(1.0 - 1.0 / trials)
    z2 = _normal_ppf(1.0 - 1.0 / (trials * math.e))
    return math.sqrt(variance_of_sharpe) * (
        (1.0 - _EULER_GAMMA) * z1 + _EULER_GAMMA * z2
    )


def deflated_sharpe_ratio(
    sr: float,
    *,
    n: int,
    trials: int,
    skew: float,
    excess_kurtosis: float,
    sr_benchmark: float = 0.0,
) -> float:
    """DSR: PSR against the expected maximum Sharpe of ``trials`` backtests."""
    variance = _variance_of_sharpe(sr, skew, excess_kurtosis, n)
    expected_max = expected_max_sharpe(trials, variance)
    return probabilistic_sharpe_ratio(
        sr,
        sr_benchmark=sr_benchmark + expected_max,
        skew=skew,
        excess_kurtosis=excess_kurtosis,
        n=n,
    )


def combinatorially_symmetric_cross_validation(
    candidate_returns: np.ndarray,
    *,
    n_slices: int = 8,
) -> dict[str, object]:
    """Estimate backtest-selection overfitting with CSCV.

    Rows are chronological return observations and columns are candidate
    strategies.  Each symmetric split selects the best in-sample candidate,
    ranks it out-of-sample, and records its OOS degradation.  This complements
    DSR; it does not replace the multiple-testing adjustment in DSR.
    """

    values = np.asarray(candidate_returns, dtype=float)
    if values.ndim != 2:
        raise ValueError("candidate_returns must be a 2D observations x trials matrix")
    finite_rows = np.all(np.isfinite(values), axis=1)
    values = values[finite_rows]
    n_observations, n_candidates = values.shape
    if n_candidates < 2 or n_observations < 24:
        raise ValueError(
            "CSCV requires at least 24 finite chronological observations and "
            "2 candidate columns"
        )

    n_slices = max(4, min(int(n_slices), n_observations // 4))
    if n_slices % 2:
        n_slices -= 1
    slices = [
        part
        for part in np.array_split(np.arange(n_observations), n_slices)
        if len(part)
    ]
    n_slices = len(slices)
    if n_slices < 4 or n_slices % 2:
        raise ValueError("CSCV could not construct at least four symmetric slices")

    def score(sample: np.ndarray) -> np.ndarray:
        mean = np.mean(sample, axis=0)
        volatility = np.std(sample, axis=0, ddof=1)
        return np.divide(
            mean,
            volatility,
            out=np.full_like(mean, -np.inf),
            where=volatility > 1e-12,
        )

    logits: list[float] = []
    degradations: list[float] = []
    half = n_slices // 2
    all_slices = set(range(n_slices))
    for train_slice_ids in combinations(range(n_slices), half):
        # Count each symmetric train/test pair once.
        if 0 not in train_slice_ids:
            continue
        test_slice_ids = sorted(all_slices.difference(train_slice_ids))
        train_idx = np.concatenate([slices[i] for i in train_slice_ids])
        test_idx = np.concatenate([slices[i] for i in test_slice_ids])
        train_scores = score(values[train_idx])
        test_scores = score(values[test_idx])
        selected = int(np.argmax(train_scores))

        # Percentile rank in (0, 1), where values below 0.5 indicate that the
        # selected IS winner fell into the lower half OOS.
        rank = int(np.sum(test_scores < test_scores[selected]))
        relative_rank = (rank + 1.0) / (n_candidates + 1.0)
        relative_rank = min(max(relative_rank, 1e-9), 1.0 - 1e-9)
        logits.append(float(math.log(relative_rank / (1.0 - relative_rank))))
        degradations.append(float(test_scores[selected] - np.max(test_scores)))

    if not logits:
        raise ValueError("CSCV produced no valid symmetric splits")
    pbo = float(np.mean(np.asarray(logits) <= 0.0))
    return {
        "pbo": pbo,
        "logit_ranks": logits,
        "oos_degradation": degradations,
        "n_splits": len(logits),
    }


def probability_of_backtest_overfitting(
    candidate_returns: np.ndarray,
    *,
    n_slices: int = 8,
) -> float:
    """Convenience wrapper returning CSCV's PBO estimate."""

    value = combinatorially_symmetric_cross_validation(
        candidate_returns,
        n_slices=n_slices,
    )["pbo"]
    if not isinstance(value, (int, float, np.integer, np.floating)):
        raise TypeError("CSCV returned a non-numeric PBO value")
    return float(value)


def min_trades_check(
    folds: list[dict],
    min_trades: int,
    *,
    label: str = "portfolio",
) -> list[str]:
    """Return human-readable violations for folds under ``min_trades``.

    ``folds`` entries must contain ``trades`` and ``start`` keys (as produced
    by the evidence generator).
    """
    violations: list[str] = []
    for fold in folds:
        trades = int(fold.get("trades", 0))
        if trades < min_trades:
            violations.append(
                f"{label} fold {fold.get('start', '?')} has {trades} trades "
                f"< minimum {min_trades}"
            )
    return violations


def summarize_sharpe(
    returns: np.ndarray,
    *,
    periods_per_year: float,
    trials: int | None = None,
    experiment_registry: ExperimentRegistryLike | None = None,
    experiment_ids: Iterable[str] | None = None,
    bootstrap_iters: int = 1_000,
    block_len: int | None = None,
    seed: int = 42,
    sr_benchmark: float = 0.0,
) -> dict[str, float | int | str]:
    """One-call summary: point Sharpe, bootstrap CI, PSR and DSR."""
    trial_count_source = "manual"
    if experiment_registry is not None:
        if experiment_ids is None:
            trial_counts = experiment_registry.trial_counts()
        else:
            trial_counts = experiment_registry.trial_counts(
                experiment_ids=experiment_ids
            )
        trials = int(trial_counts.effective_trial_count)
        trial_count_source = (
            "experiment_registry_scoped"
            if experiment_ids is not None
            else "experiment_registry"
        )
    if trials is None:
        raise ValueError("trials or experiment_registry is required")
    if trials < 1:
        raise ValueError("effective trial count must be >= 1")
    s = series_stats(returns, periods_per_year)
    lo, hi, _ = block_bootstrap_sharpe_ci(
        returns,
        periods_per_year=periods_per_year,
        block_len=block_len,
        iters=bootstrap_iters,
        seed=seed,
    )
    psr = probabilistic_sharpe_ratio(
        s.sharpe,
        sr_benchmark=sr_benchmark,
        skew=s.skew,
        excess_kurtosis=s.excess_kurtosis,
        n=s.n,
    )
    dsr = deflated_sharpe_ratio(
        s.sharpe,
        n=s.n,
        trials=trials,
        skew=s.skew,
        excess_kurtosis=s.excess_kurtosis,
        sr_benchmark=sr_benchmark,
    )
    return {
        "sharpe": round(s.sharpe, 4),
        "annualized_sharpe": round(s.annualized_sharpe, 4),
        "sharpe_ci95_lo": round(lo, 4),
        "sharpe_ci95_hi": round(hi, 4),
        "skew": round(s.skew, 4),
        "excess_kurtosis": round(s.excess_kurtosis, 4),
        "n_returns": s.n,
        "probabilistic_sharpe_ratio": round(psr, 4),
        "deflated_sharpe_ratio": round(dsr, 4),
        "trials": trials,
        "trial_count_source": trial_count_source,
    }
