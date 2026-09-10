#!/usr/bin/env python3
"""
Portfolio Analysis — Statistical analysis of strategy interactions.

Computes:
- PnL correlation matrix (pairwise + rolling)
- Diversification ratio
- Marginal Sharpe contribution
- Tail dependence
- Drawdown correlation
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl


def compute_pnl_correlation(
    return_series: dict[str, list[float]],
    window: int = 14,
) -> dict[str, Any]:
    """Compute pairwise correlation between strategy PnL series.

    Args:
        return_series: dict mapping strategy_id → list of period returns
        window: Rolling window for time-varying correlation

    Returns:
        {
            "pearson": {pair: corr},
            "spearman": {pair: corr},
            "rolling_max": {pair: max_corr},
            "rolling_min": {pair: min_corr},
            "mean_corr": float (average off-diagonal),
        }
    """
    strategies = list(return_series.keys())
    n = len(strategies)

    if n < 2:
        return {"pearson": {}, "spearman": {}, "mean_corr": 0.0}

    # Align series to minimum length
    min_len = min(len(s) for s in return_series.values())
    aligned: dict[str, np.ndarray] = {
        s: np.array(r[-min_len:], dtype=np.float64)
        for s, r in return_series.items()
        if len(r) >= window
    }
    strategies = list(aligned.keys())
    n = len(strategies)

    if n < 2:
        return {"pearson": {}, "spearman": {}, "mean_corr": 0.0}

    data = np.array([aligned[s] for s in strategies])  # (n_strategies, n_periods)
    pearson = {}
    spearman = {}
    rolling_max_corr = {}
    rolling_min_corr = {}

    from scipy.stats import spearmanr

    for i in range(n):
        for j in range(i + 1, n):
            si, sj = strategies[i], strategies[j]
            pair = f"{si}__{sj}"

            # Pearson
            if np.std(data[i]) > 0 and np.std(data[j]) > 0:
                r = float(np.corrcoef(data[i], data[j])[0, 1])
            else:
                r = 0.0
            pearson[pair] = r

            # Spearman
            try:
                rho, _ = spearmanr(data[i], data[j])
                spearman[pair] = float(rho) if not np.isnan(rho) else 0.0
            except Exception:
                spearman[pair] = 0.0

            # Rolling correlation
            if min_len >= window:
                rolling_corrs = []
                for t in range(window, min_len):
                    x = data[i, t - window:t]
                    y = data[j, t - window:t]
                    if np.std(x) > 0 and np.std(y) > 0:
                        rc = float(np.corrcoef(x, y)[0, 1])
                        if not np.isnan(rc):
                            rolling_corrs.append(rc)
                if rolling_corrs:
                    rolling_max_corr[pair] = float(max(rolling_corrs))
                    rolling_min_corr[pair] = float(min(rolling_corrs))
                else:
                    rolling_max_corr[pair] = 0.0
                    rolling_min_corr[pair] = 0.0
            else:
                rolling_max_corr[pair] = 0.0
                rolling_min_corr[pair] = 0.0

    # Mean off-diagonal correlation
    corr_values = list(pearson.values())
    mean_corr = float(np.mean(corr_values)) if corr_values else 0.0

    # Full correlation matrix
    corr_matrix = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i == j:
                corr_matrix[i, j] = 1.0
            elif i < j:
                pair = f"{strategies[i]}__{strategies[j]}"
                corr_matrix[i, j] = pearson.get(pair, 0.0)
                corr_matrix[j, i] = pearson.get(pair, 0.0)

    return {
        "pearson": pearson,
        "spearman": spearman,
        "rolling_max": rolling_max_corr,
        "rolling_min": rolling_min_corr,
        "mean_corr": mean_corr,
        "matrix": corr_matrix.tolist(),
        "strategies": strategies,
    }


def compute_diversification_ratio(
    return_series: dict[str, list[float]],
    weights: dict[str, float] | None = None,
) -> float:
    """Compute the diversification ratio.

    DR = (sum of weighted individual volatilities) / portfolio volatility

    DR = 1 means no diversification benefit.
    DR > 1 means diversification benefit (lower correlation → higher DR).

    Args:
        return_series: dict mapping strategy_id → list of period returns
        weights: Optional portfolio weights (equal-weight if None)
    """
    strategies = list(return_series.keys())
    min_len = min(len(s) for s in return_series.values())

    if len(strategies) < 2 or min_len < 3:
        return 1.0

    data = np.array([
        np.array(r[-min_len:], dtype=np.float64)
        for r in return_series.values()
    ])  # (n_strategies, n_periods)

    # Individual volatilities
    vols = np.std(data, axis=1, ddof=1)

    # Weights
    if weights is None:
        w = np.ones(len(strategies)) / len(strategies)
    else:
        w = np.array([weights.get(s, 0.0) for s in strategies])
        if w.sum() == 0:
            w = np.ones(len(strategies)) / len(strategies)
        else:
            w = w / w.sum()

    # Weighted sum of individual vols
    weighted_vols_sum = float(np.sum(w * vols))

    # Portfolio volatility
    cov_matrix = np.cov(data)
    portfolio_var = float(w @ cov_matrix @ w)
    portfolio_vol = float(np.sqrt(portfolio_var)) if portfolio_var > 0 else 1e-9

    dr = weighted_vols_sum / portfolio_vol if portfolio_vol > 0 else 1.0
    return dr


def compute_marginal_sharpe_contribution(
    return_series: dict[str, list[float]],
    strategy_id: str,
    periods_per_year: int = 24 * 365,
) -> float:
    """Compute marginal Sharpe contribution of adding a strategy to the portfolio.

    MSC = SR(portfolio_with) - SR(portfolio_without)

    Args:
        return_series: dict mapping strategy_id → list of period returns
        strategy_id: Strategy to evaluate marginal contribution for
    """
    strategies = list(return_series.keys())
    if strategy_id not in strategies or len(strategies) < 2:
        return 0.0

    min_len = min(len(r) for r in return_series.values())

    # Portfolio without the target strategy
    others = [s for s in strategies if s != strategy_id]
    others_data = np.array([
        np.array(return_series[s][-min_len:], dtype=np.float64)
        for s in others
    ])
    equal_weights = np.ones(len(others)) / len(others)
    portfolio_without = equal_weights @ others_data
    sr_without = _compute_sharpe(portfolio_without, periods_per_year)

    # Portfolio with the target strategy
    all_data = np.array([
        np.array(return_series[s][-min_len:], dtype=np.float64)
        for s in strategies
    ])
    equal_weights_all = np.ones(len(strategies)) / len(strategies)
    portfolio_with = equal_weights_all @ all_data
    sr_with = _compute_sharpe(portfolio_with, periods_per_year)

    return float(sr_with - sr_without)


def _compute_sharpe(returns: np.ndarray, periods_per_year: int) -> float:
    """Compute annualized Sharpe ratio."""
    if len(returns) < 2 or np.std(returns, ddof=1) == 0:
        return 0.0
    mean_ret = float(np.mean(returns))
    std_ret = float(np.std(returns, ddof=1))
    sharpe = (mean_ret / std_ret) * np.sqrt(periods_per_year)
    return sharpe


def compute_drawdown_correlation(
    equity_curves: dict[str, list[float]],
) -> dict[str, float]:
    """Compute pairwise drawdown correlation.

    Args:
        equity_curves: dict mapping strategy_id → list of cumulative equity values
    """
    strategies = list(equity_curves.keys())
    if len(strategies) < 2:
        return {}

    min_len = min(len(c) for c in equity_curves.values())

    # Compute drawdowns
    dd_series: dict[str, np.ndarray] = {}
    for s in strategies:
        curve = np.array(equity_curves[s][-min_len:], dtype=np.float64)
        running_max = np.maximum.accumulate(curve)
        dd = (curve - running_max) / (running_max + 1e-9)
        dd_series[s] = dd

    correlations: dict[str, float] = {}
    for i in range(len(strategies)):
        for j in range(i + 1, len(strategies)):
            si, sj = strategies[i], strategies[j]
            pair = f"{si}__{sj}"
            di = dd_series[si]
            dj = dd_series[sj]
            if np.std(di) > 0 and np.std(dj) > 0:
                r = float(np.corrcoef(di, dj)[0, 1])
            else:
                r = 0.0
            correlations[pair] = r

    return correlations


__all__ = [
    "compute_pnl_correlation",
    "compute_diversification_ratio",
    "compute_marginal_sharpe_contribution",
    "compute_drawdown_correlation",
]
