"""
S4: Cross-Sectional Momentum Strategy

Rank assets by rolling returns, long top quantile.
- Long-only: long top 30%
- Long-short: long top 30%, short bottom 30%

This is a cross-sectional strategy. For single-asset backtest compatibility,
falls back to trend-following using a single asset's momentum.

Requires columns: open, high, low, close, volume (polars DataFrame)
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy
from trading_agent.technical.indicators import sma_func

_NAME_LONG_ONLY = "cross_sectional_momentum_lo"
_NAME_LONG_SHORT = "cross_sectional_momentum_ls"


@register_strategy(_NAME_LONG_ONLY)
class CrossSectionalMomentumLongOnlyStrategy(Strategy):
    """Cross-sectional momentum — long-only variant."""

    name = _NAME_LONG_ONLY

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.lookback = int(self.params.get("lookback_days", 60))
        self.fast_ma = int(self.params.get("fast_period", 20))
        self.slow_ma = int(self.params.get("slow_period", 60))
        self.rsi_period = int(self.params.get("rsi_period", 14))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        close = pl.col("close")
        returns = close.pct_change()

        return df.with_columns([
            returns.rolling_mean(self.lookback).alias("mom_60"),
            sma_func(close, self.fast_ma).alias("ma_fast"),
            sma_func(close, self.slow_ma).alias("ma_slow"),
            pl.col("close").pct_change().rolling_std(self.rsi_period).alias("vol"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        # Single-asset approximation of CS momentum:
        # Long when 60-day momentum > 0 AND price above fast MA
        # Exit when momentum < 0 or price below slow MA
        return df.select(
            pl.when(
                (pl.col("mom_60") > 0)
                & (pl.col("close") > pl.col("ma_fast"))
            )
            .then(1)
            .when(
                (pl.col("mom_60") < 0)
                | (pl.col("close") < pl.col("ma_slow"))
            )
            .then(-1)
            .otherwise(0)
            .alias("signal")
        ).to_series()


@register_strategy(_NAME_LONG_SHORT)
class CrossSectionalMomentumLongShortStrategy(Strategy):
    """Cross-sectional momentum — long-short variant."""

    name = _NAME_LONG_SHORT

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.lookback = int(self.params.get("lookback_days", 60))
        self.fast_ma = int(self.params.get("fast_period", 20))
        self.slow_ma = int(self.params.get("slow_period", 60))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        close = pl.col("close")
        return df.with_columns([
            close.pct_change().rolling_mean(self.lookback).alias("mom_60"),
            sma_func(close, self.fast_ma).alias("ma_fast"),
            sma_func(close, self.slow_ma).alias("ma_slow"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        # Aggressive momentum with trend filter
        return df.select(
            pl.when(
                (pl.col("mom_60") > 0)
                & (pl.col("close") > pl.col("ma_fast"))
            )
            .then(1)
            .when(
                (pl.col("mom_60") < 0)
                & (pl.col("close") < pl.col("ma_fast"))
            )
            .then(-1)
            .otherwise(0)
            .alias("signal")
        ).to_series()


# Param grids
PARAM_GRID_LONG_ONLY = {
    "lookback_days": [60, 90],
    "fast_period": [10, 20, 30],
    "slow_period": [40, 60],
    "rsi_period": [14],
}

PARAM_GRID_LONG_SHORT = {
    "lookback_days": [60, 90],
    "fast_period": [10, 20, 30],
    "slow_period": [40, 60],
}

DEFAULT_PARAMS = {
    "lookback_days": 60,
    "fast_period": 20,
    "slow_period": 60,
}
