"""
S1: Trend Pullback Strategy

Trend persistence after retracement — institutions accumulate during dips.
- ADX(14) > threshold confirms trend strength
- Price pulls back to EMA/Fibonacci retracement level (23.6-50%)
- Volume spike confirms participation

Requires columns: open, high, low, close, volume (polars DataFrame)
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy
from trading_agent.technical.indicators import adx_func, rsi_func, ma_func

_NAME = "trend_pullback"


@register_strategy(_NAME)
class TrendPullbackStrategy(Strategy):
    """Trend pullback: MA crossover + ADX filter + volume confirmation."""

    name = _NAME

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.fast_period = int(self.params.get("ma_fast", 20))
        self.slow_period = int(self.params.get("ma_slow", 80))
        self.adx_threshold = float(self.params.get("adx_threshold", 25.0))
        self.adx_period = int(self.params.get("adx_period", 14))
        self.rsi_period = int(self.params.get("rsi_period", 14))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns([
            ma_func(pl.col("close"), self.fast_period).alias("ma_fast"),
            ma_func(pl.col("close"), self.slow_period).alias("ma_slow"),
            adx_func(pl.col("high"), pl.col("low"), pl.col("close"), self.adx_period).alias("adx"),
            rsi_func(pl.col("close"), self.rsi_period).alias("rsi"),
            pl.col("volume").rolling_mean(20).alias("vol_sma"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        # Signal = +1 on entry, -1 on exit (reversal of position)
        # Long when:
        # 1. Close > ma_slow (uptrend)
        # 2. ADX > threshold (strong trend)
        # 3. Price pulled back to ma_fast but bounced above it
        # 4. Volume > 1.5x average
        # Exit (signal=-1) when close < ma_fast
        return df.select(
            pl.when(
                (pl.col("close") > pl.col("ma_slow"))
                & (pl.col("adx") > pl.lit(self.adx_threshold))
                & (pl.col("close") > pl.col("ma_fast"))
                & (pl.col("close").shift(1) <= pl.col("ma_fast").shift(1))
                & (pl.col("volume") > pl.lit(1.5) * pl.col("vol_sma"))
            )
            .then(1)
            .when(pl.col("close") < pl.col("ma_fast"))
            .then(-1)
            .otherwise(0)
            .alias("signal")
        ).to_series()


# Param grid aligned with MINIMAL_PARAM_GRIDS in run_wfo_parallel.py
PARAM_GRID = {
    "ma_fast": [10, 20, 30],
    "ma_slow": [80, 120],
    "adx_threshold": [25, 30],
    "adx_period": [14],
    "rsi_period": [14],
}

DEFAULT_PARAMS = {
    "ma_fast": 20,
    "ma_slow": 80,
    "adx_threshold": 25.0,
    "adx_period": 14,
    "rsi_period": 14,
}
