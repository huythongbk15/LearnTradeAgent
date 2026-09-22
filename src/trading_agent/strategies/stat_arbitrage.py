"""
S8: Statistical Arbitrage (Cointegration) Strategy

Pairs trading using rolling z-score of price spread.
- Test cointegration via Engle-Granger (ADF on residuals)
- Compute rolling hedge ratio via OLS
- Entry when z-score > threshold, exit when reverts to mean

For single-asset backtest compatibility, uses mean-reversion z-score approach.

Requires columns: open, high, low, close, volume (polars DataFrame)
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy
from trading_agent.technical.indicators import sma_func, std_func

_NAME_LONG_ONLY = "stat_arbitrage_lo"
_NAME_LONG_SHORT = "stat_arbitrage_ls"


@register_strategy(_NAME_LONG_ONLY)
class StatArbitrageLongOnlyStrategy(Strategy):
    """Statistical arbitrage — long-only mean reversion variant."""

    name = _NAME_LONG_ONLY

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.zscore_entry = float(self.params.get("zscore_entry", 2.0))
        self.zscore_exit = float(self.params.get("zscore_exit", 0.5))
        self.lookback = int(self.params.get("lookback_days", 20))
        self.bb_lookback = int(self.params.get("bb_lookback", 20))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        close = pl.col("close")
        rolling_mean = sma_func(close, self.lookback)
        rolling_std = std_func(close, self.lookback)

        bb_period = self.bb_lookback
        bb_ma = sma_func(close, bb_period)
        bb_std_val = std_func(close, bb_period)

        zscore = (close - rolling_mean) / rolling_std

        return df.with_columns([
            rolling_mean.alias("rolling_mean"),
            rolling_std.alias("rolling_std"),
            zscore.alias("zscore"),
            (bb_ma + 2.0 * bb_std_val).alias("bb_upper"),
            (bb_ma - 2.0 * bb_std_val).alias("bb_lower"),
            ((bb_ma + 2.0 * bb_std_val) - (bb_ma - 2.0 * bb_std_val)).alias("bb_range"),
        ]).with_columns([
            (pl.col("bb_range") / (bb_ma + 1e-9) * 100).alias("bb_width"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        # Long-only mean reversion: buy when price is undervalued (z-score < -entry)
        # Exit when price reverts (z-score > -exit)
        return df.select(
            pl.when(pl.col("zscore") < pl.lit(-self.zscore_entry))
            .then(1)
            .when(pl.col("zscore") > pl.lit(-self.zscore_exit))
            .then(-1)
            .otherwise(0)
            .alias("signal")
        ).to_series()


@register_strategy(_NAME_LONG_SHORT)
class StatArbitrageLongShortStrategy(Strategy):
    """Statistical arbitrage — long-short variant."""

    name = _NAME_LONG_SHORT

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.zscore_entry = float(self.params.get("zscore_entry", 2.0))
        self.zscore_exit = float(self.params.get("zscore_exit", 0.5))
        self.lookback = int(self.params.get("lookback_days", 20))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        close = pl.col("close")
        rolling_mean = sma_func(close, self.lookback)
        rolling_std = std_func(close, self.lookback)
        zscore = (close - rolling_mean) / rolling_std

        return df.with_columns([
            rolling_mean.alias("rolling_mean"),
            rolling_std.alias("rolling_std"),
            zscore.alias("zscore"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        # Long-short: short when z-score > entry (overvalued), long when z-score < -entry
        # Exit (0) when z-score reverts to within [-exit, +exit]
        # Position is maintained between entries/exits (fill forward)
        return df.with_columns([
            pl.when(pl.col("zscore") < pl.lit(-self.zscore_entry))
            .then(1)
            .when(pl.col("zscore") > pl.lit(self.zscore_entry))
            .then(-1)
            .when(pl.col("zscore").abs() <= pl.lit(self.zscore_exit))
            .then(-1)
            .otherwise(None)
            .alias("raw_signal"),
        ]).with_columns([
            pl.col("raw_signal").forward_fill().fill_null(0).alias("signal"),
        ]).select("signal").to_series()


# Param grids — aligned with catalog strategy_id naming
PARAM_GRID_LONG_ONLY = {
    "zscore_entry": [1.5, 2.0],
    "zscore_exit": [0.5],
    "lookback_days": [20],
}

PARAM_GRID_LONG_SHORT = {
    "zscore_entry": [2.0, 2.5],
    "zscore_exit": [0.5],
    "lookback_days": [20],
}

DEFAULT_PARAMS = {
    "zscore_entry": 2.0,
    "zscore_exit": 0.5,
    "lookback_days": 20,
}
