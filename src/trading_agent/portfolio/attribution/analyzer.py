"""Performance attribution analysis."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional
from datetime import datetime

import numpy as np
import pandas as pd


@dataclass
class AttributionResult:
    """Performance attribution result."""

    total_return: Decimal
    allocation_effect: Decimal
    selection_effect: Decimal
    interaction_effect: Decimal
    by_strategy: dict[str, Decimal]
    by_asset: dict[str, Decimal]
    by_factor: dict[str, Decimal]
    benchmark_return: Decimal
    active_return: Decimal
    period_start: datetime
    period_end: datetime


class AttributionAnalyzer:
    """Brinson-style performance attribution."""

    def __init__(self, benchmark_returns: pd.Series, risk_free_rate: float = 0.02):
        self.benchmark_returns = benchmark_returns
        self.risk_free_rate = risk_free_rate

    def analyze(
        self,
        portfolio_returns: pd.Series,
        portfolio_weights: pd.DataFrame,
        benchmark_weights: pd.DataFrame,
        period_start: datetime,
        period_end: datetime,
    ) -> AttributionResult:
        """Perform Brinson attribution analysis."""
        # Align data
        common_dates = portfolio_returns.index.intersection(
            portfolio_weights.index
        ).intersection(benchmark_weights.index)

        port_ret = portfolio_returns.loc[common_dates]
        port_w = portfolio_weights.loc[common_dates]
        bench_w = benchmark_weights.loc[common_dates]
        bench_ret = self.benchmark_returns.loc[common_dates]

        # Asset returns (benchmark returns per asset)
        # This would need asset-level benchmark returns
        # For now, use portfolio return decomposition

        total_return = (1 + port_ret).prod() - 1
        bench_return = (1 + bench_ret).prod() - 1
        active_return = total_return - bench_return

        # Simplified attribution (single period)
        # Allocation effect: Σ (w_p - w_b) * (r_b - R_b)
        # Selection effect: Σ w_b * (r_p - r_b)
        # Interaction: Σ (w_p - w_b) * (r_p - r_b)

        # We need asset-level returns for full attribution
        # This is a simplified version

        return AttributionResult(
            total_return=Decimal(str(total_return)),
            allocation_effect=Decimal("0"),
            selection_effect=Decimal("0"),
            interaction_effect=Decimal("0"),
            by_strategy={},
            by_asset={},
            by_factor={},
            benchmark_return=Decimal(str(bench_return)),
            active_return=Decimal(str(active_return)),
            period_start=period_start,
            period_end=period_end,
        )

    def factor_attribution(
        self,
        portfolio_returns: pd.Series,
        factor_returns: pd.DataFrame,
        factor_loadings: pd.DataFrame,
    ) -> dict[str, Decimal]:
        """Factor-based attribution (Fama-French, Barra, etc.)."""
        # Align
        common = portfolio_returns.index.intersection(
            factor_returns.index
        ).intersection(factor_loadings.index)

        port_ret = portfolio_returns.loc[common]
        fac_ret = factor_returns.loc[common]
        loadings = factor_loadings.loc[common]

        # Regression: r_p = α + Σ β_i * f_i + ε
        X = fac_ret.values
        y = port_ret.values

        try:
            coeffs, residuals, rank, s = np.linalg.lstsq(X, y, rcond=None)
            alpha = y.mean() - np.dot(X.mean(axis=0), coeffs)

            # Factor contributions
            contrib = {}
            for i, col in enumerate(factor_returns.columns):
                contrib[col] = Decimal(str(coeffs[i] * fac_ret[col].mean()))

            contrib["alpha"] = Decimal(str(alpha))
            return contrib
        except np.linalg.LinAlgError:
            return {}


class StrategyAttribution:
    """Strategy-level performance attribution."""

    def __init__(self):
        self.strategy_returns: dict[str, pd.Series] = {}
        self.strategy_weights: dict[str, pd.Series] = {}

    def add_strategy(
        self, name: str, returns: pd.Series, weights: Optional[pd.Series] = None
    ) -> None:
        """Add strategy returns and weights."""
        self.strategy_returns[name] = returns
        if weights is not None:
            self.strategy_weights[name] = weights

    def attribute(
        self,
        portfolio_returns: pd.Series,
        period_start: datetime,
        period_end: datetime,
    ) -> dict[str, Decimal]:
        """Attribute portfolio return to strategies."""
        # Align all series
        all_returns = {k: v for k, v in self.strategy_returns.items()}
        all_returns["portfolio"] = portfolio_returns

        df = pd.DataFrame(all_returns).dropna()
        port_ret = df["portfolio"]
        strat_ret = df.drop("portfolio", axis=1)

        # If weights provided, use them; else assume equal weight
        if self.strategy_weights:
            weights_df = pd.DataFrame(self.strategy_weights).loc[df.index]
            weights_df = weights_df.div(weights_df.sum(axis=1), axis=0).fillna(0)
        else:
            weights_df = pd.DataFrame(
                1.0 / len(strat_ret.columns), index=df.index, columns=strat_ret.columns
            )

        # Contribution = weight * return
        contributions = (weights_df * strat_ret).sum(axis=1)

        # Total period contribution
        total_contrib = {}
        for col in strat_ret.columns:
            total_contrib[col] = Decimal(str((weights_df[col] * strat_ret[col]).sum()))

        return total_contrib

    def strategy_metrics(self, name: str) -> dict[str, Decimal]:
        """Calculate metrics for a single strategy."""
        returns = self.strategy_returns[name]

        return {
            "total_return": Decimal(str((1 + returns).prod() - 1)),
            "annualized_return": Decimal(str((1 + returns.mean()) ** 252 - 1)),
            "volatility": Decimal(str(returns.std() * np.sqrt(252))),
            "sharpe": Decimal(
                str(
                    (returns.mean() * 252) / (returns.std() * np.sqrt(252))
                    if returns.std() > 0
                    else 0
                )
            ),
            "max_drawdown": Decimal(str(self._max_drawdown(returns))),
            "win_rate": Decimal(str((returns > 0).mean())),
            "profit_factor": Decimal(str(self._profit_factor(returns))),
        }

    def _max_drawdown(self, returns: pd.Series) -> float:
        equity = (1 + returns).cumprod()
        running_max = equity.expanding().max()
        drawdown = (equity - running_max) / running_max
        return abs(drawdown.min())

    def _profit_factor(self, returns: pd.Series) -> float:
        wins = returns[returns > 0].sum()
        losses = abs(returns[returns < 0].sum())
        return wins / losses if losses > 0 else float("inf")


class AssetClassAttribution:
    """Asset class level attribution."""

    def __init__(self):
        self.asset_returns: dict[str, pd.Series] = {}
        self.asset_weights: dict[str, pd.Series] = {}

    def add_asset_class(
        self, name: str, returns: pd.Series, weights: Optional[pd.Series] = None
    ) -> None:
        self.asset_returns[name] = returns
        if weights is not None:
            self.asset_weights[name] = weights

    def attribute(self, portfolio_returns: pd.Series) -> dict[str, Decimal]:
        """Attribute return to asset classes."""
        df = pd.DataFrame(self.asset_returns).dropna()
        port = portfolio_returns.loc[df.index]

        if self.asset_weights:
            weights = pd.DataFrame(self.asset_weights).loc[df.index]
            weights = weights.div(weights.sum(axis=1), axis=0).fillna(0)
        else:
            weights = pd.DataFrame(
                1.0 / len(df.columns), index=df.index, columns=df.columns
            )

        contrib = {}
        for col in df.columns:
            contrib[col] = Decimal(str((weights[col] * df[col]).sum()))

        return contrib
