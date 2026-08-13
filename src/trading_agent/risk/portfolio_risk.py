#!/usr/bin/env python3
"""
Advanced Portfolio Risk Management.

Implements:
1. Value-at-Risk (VaR) & Conditional VaR (Expected Shortfall) — parametric & historical
2. Portfolio-level risk budgeting across strategies/assets
3. Drawdown-based position controls (tilt factor)
4. Cross-asset correlation monitoring with breach alerts
5. Risk-adjusted position re-scaling
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
import scipy.stats as stats


@dataclass
class DrawdownConfig:
    """Drawdown-based risk control tiers."""

    # [drawdown_level, position_scale_factor]
    tiers: list[tuple[float, float]] = field(
        default_factory=lambda: [
            (0.05, 0.75),  # -5%  dd → scale to 75%
            (0.10, 0.50),  # -10% dd → scale to 50%
            (0.15, 0.25),  # -15% dd → scale to 25%
            (0.20, 0.00),  # -20% dd → halt
        ]
    )


@dataclass
class RiskMetrics:
    """Output of VaR/CVaR computation."""

    method: str
    confidence: float
    var: float  # positive number = loss
    cvar: float  # expected shortfall (loss beyond VaR)
    max_loss: float
    n: int


class HistoricalVaR:
    """Historical simulation VaR / CVaR (no distributional assumption)."""

    def __init__(self, confidence: float = 0.95, window: int = 252):
        self.confidence = confidence
        self.window = window

    def compute(self, returns: Sequence[float]) -> RiskMetrics:
        if len(returns) < 20:
            return RiskMetrics("hist", self.confidence, 0.0, 0.0, 0.0, len(returns))
        arr = np.asarray(returns, dtype=float)
        arr = np.sort(arr)[::-1]  # descending
        # VaR at confidence: quantile of losses
        q = 1 - self.confidence
        var = np.quantile(arr, q)  # negative value
        cvar = arr[arr <= var].mean() if (arr <= var).any() else var
        return RiskMetrics(
            "historical",
            self.confidence,
            var=-var,
            cvar=-cvar,
            max_loss=-arr.min(),
            n=len(arr),
        )


class ParametricVaR:
    """Gaussian (variance-covariance) VaR / CVaR."""

    def __init__(self, confidence: float = 0.95):
        self.confidence = confidence
        self.z = stats.norm.ppf(confidence)
        self.z_cvar = stats.norm.ppf(confidence)

    def compute(self, returns: Sequence[float]) -> RiskMetrics:
        if len(returns) < 3:
            return RiskMetrics(
                "parametric", self.confidence, 0.0, 0.0, 0.0, len(returns)
            )
        arr = np.asarray(returns, dtype=float)
        mu, sigma = arr.mean(), arr.std(ddof=1)
        if sigma <= 0:
            return RiskMetrics("parametric", self.confidence, 0.0, 0.0, 0.0, len(arr))
        # VaR = -mu + sigma * z_critical (positive loss)
        var = sigma * self.z - mu
        # CVaR (normal): mu - sigma * phi(z)/(1-alpha), negative return = loss
        phi = stats.norm.pdf(self.z)
        cvar = mu - sigma * phi / (1 - self.confidence)
        return RiskMetrics(
            "parametric",
            self.confidence,
            var=max(var, 0.0),
            cvar=max(-cvar, 0.0),
            max_loss=-arr.min(),
            n=len(arr),
        )


class PortfolioRiskManager:
    """
    Portfolio-level risk controller integrating VaR, drawdown, correlation.

    Usage:
        pm = PortfolioRiskManager(config)
        pm.update(equity_curve=[...], positions={symbol: {...}}, returns={...})
        scale = pm.position_scale_factor()   # 0..1 multiplier
        blocked = pm.is_trading_halted()
    """

    def __init__(self, config: DrawdownConfig | None = None):
        self.config = config or DrawdownConfig()
        self.equity_curve: list[float] = []
        self.peak_equity: float = 0.0
        self.current_dd: float = 0.0
        self.var_confidence: float = 0.95
        self.corr_threshold: float = 0.8
        self.last_metrics: dict = {}
        self._positions: dict[str, dict] = {}

    # -- drawdown ---------------------------------------------------------
    def update_equity(self, equity: float) -> float:
        """Push equity, update peak & current drawdown. Returns current DD."""
        self.equity_curve.append(equity)
        self.peak_equity = max(self.peak_equity, equity)
        self.current_dd = (
            (self.peak_equity - equity) / self.peak_equity if self.peak_equity else 0.0
        )
        return self.current_dd

    def position_scale_factor(self) -> float:
        """Scale factor (0..1) from current drawdown tier."""
        scale = 1.0
        for level, s in self.config.tiers:
            if self.current_dd >= level:
                scale = s
        return scale

    def is_trading_halted(self) -> bool:
        return self.current_dd >= self.config.tiers[-1][0]

    # -- VaR --------------------------------------------------------------
    def portfolio_var(
        self, returns: Sequence[float], method: str = "historical"
    ) -> RiskMetrics:
        if method == "parametric":
            return ParametricVaR(self.var_confidence).compute(returns)
        return HistoricalVaR(
            confidence=self.var_confidence, window=len(returns)
        ).compute(returns)

    # -- correlation ------------------------------------------------------
    @staticmethod
    def correlation_breach(
        pairs_corr: dict[str, float], threshold: float = 0.8
    ) -> list[str]:
        """Return list of crossing pair labels exceeding correlation threshold."""
        return [k for k, v in pairs_corr.items() if v > threshold]

    # -- position re-scaling (integration end-point) ----------------------
    def rescale_position(self, base_size: float) -> float:
        return base_size * self.position_scale_factor()

    def report(self) -> dict:
        return {
            "current_dd": self.current_dd,
            "peak_equity": self.peak_equity,
            "position_scale": self.position_scale_factor(),
            "halted": self.is_trading_halted(),
            "n_equity_points": len(self.equity_curve),
        }

    # -- risk budget helpers ----------------------------------------------
    def risk_budget_cvar(
        self,
        returns_matrix: np.ndarray,
        weights: np.ndarray,
        confidence: float = 0.95,
    ) -> dict:
        """Compute per-asset CVaR contribution (Euler decomposition)."""
        port_cvar = compute_portfolio_cvar(returns_matrix, weights, confidence)
        contributions = []
        for i in range(returns_matrix.shape[0]):
            w = weights.copy()
            w[i] = 0.0
            # marginal: contribution = w_i * d(CVaR)/dw_i approximated by finite diff
            eps = 1e-4
            w_up = weights.copy()
            w_up[i] += eps
            w_up = w_up / w_up.sum()
            w_dn = weights.copy()
            w_dn[i] -= eps
            w_dn = np.maximum(w_dn, 0)
            w_dn = w_dn / (w_dn.sum() + 1e-12)
            cvar_up = compute_portfolio_cvar(returns_matrix, w_up, confidence)
            cvar_dn = compute_portfolio_cvar(returns_matrix, w_dn, confidence)
            marginal = (cvar_up - cvar_dn) / (2 * eps)
            contributions.append(float(weights[i] * marginal))
        return {
            "portfolio_cvar": float(port_cvar),
            "contributions": contributions,
            "weights": weights.tolist(),
        }


def compute_portfolio_cvar(
    returns_matrix: np.ndarray,  # (n_assets, n_obs)
    weights: np.ndarray,
    confidence: float = 0.95,
) -> float:
    """CVaR of a weighted portfolio from returns matrix (historical)."""
    port_returns = returns_matrix.T @ weights
    var = np.quantile(port_returns, 1 - confidence)
    tail = port_returns[port_returns <= var]
    return -tail.mean()


def max_drawdown(equity_curve: Sequence[float]) -> float:
    """Return maximum drawdown as a positive fraction."""
    peak = -np.inf
    mdd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak if peak else 0.0
        mdd = max(mdd, dd)
    return mdd


# Fix dataclass import for field default_factory
from dataclasses import field


if __name__ == "__main__":
    import random

    pm = PortfolioRiskManager()
    eq = 10000
    rng = random.Random(42)
    for i in range(300):
        ret = rng.gauss(0.001, 0.02)
        eq *= 1 + ret
        pm.update_equity(eq)

    print("Portfolio report:", pm.report())
    returns = [rng.gauss(0.001, 0.02) for _ in range(252)]
    hv = HistoricalVaR(0.95).compute(returns)
    print(
        f"Historical VaR(95%) = {hv.var:.2%}, CVaR = {hv.cvar:.2%}, max loss {hv.max_loss:.2%}"
    )
    pv = ParametricVaR(0.95).compute(returns)
    print(f"Parametric  VaR(95%) = {pv.var:.2%}, CVaR = {pv.cvar:.2%}")
    print("Position scale:", pm.position_scale_factor())
    # risk budget test
    M = np.random.RandomState(1).randn(3, 500) * np.array([[0.02], [0.03], [0.04]])
    rb = pm.risk_budget_cvar(M, np.array([0.4, 0.3, 0.3]))
    print(
        "Risk budget CVaR:", rb["portfolio_cvar"] if hasattr(rb, "__getitem__") else rb
    )
