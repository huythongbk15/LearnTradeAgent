"""
S2: Range Mean Reversion Strategy

Mean reversion within range-bound market.
- RSI + Bollinger BandWidth compression filter
- Signal when RSI oversold/overbought AND bands are compressed
- z-score of price from rolling mean provides additional edge

Requires columns: open, high, low, close, volume (polars DataFrame)
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy
from trading_agent.technical.indicators import rsi_func, sma_func, std_func, bollinger_bands_func

_NAME = "range_mean_reversion"


@register_strategy(_NAME)
class RangeMeanReversionStrategy(Strategy):
    """Range mean reversion with BB compression + RSI filter."""

    name = _NAME

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.rsi_period = int(self.params.get("vwap_window", 20))
        self.zscore_entry = float(self.params.get("zscore_entry", 2.0))
        self.zscore_exit = float(self.params.get("zscore_exit", 0.5))
        self.bb_lookback = int(self.params.get("bb_lookback", 20))
        self.bb_std = float(self.params.get("bb_std", 2.0))
        self.rsi_oversold = int(self.params.get("rsi_oversold", 30))
        self.rsi_overbought = int(self.params.get("rsi_overbought", 70))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        close = pl.col("close")
        sma = sma_func(close, self.rsi_period)
        std_val = std_func(close, self.rsi_period)

        upper_band = (sma + self.bb_std * std_val).alias("bb_upper")
        lower_band = (sma - self.bb_std * std_val).alias("bb_lower")
        bb_width = ((upper_band - lower_band) / sma).alias("bb_width")

        return df.with_columns([
            sma.alias("sma"),
            std_val.alias("sma_std"),
            upper_band,
            lower_band,
            bb_width,
            bb_width.rolling_mean(5).alias("bb_width_avg"),
            rsi_func(close, self.rsi_period).alias("rsi"),
            close.rolling_mean(self.bb_lookback).alias("rolling_mean"),
            close.rolling_std(self.bb_lookback).alias("rolling_std"),
        ]).with_columns([
            (pl.col("bb_width") / (pl.col("bb_width_avg") + 1e-9)).alias("bb_compression"),
        ]).with_columns([
            ((close - pl.col("rolling_mean")) / (pl.col("rolling_std") + 1e-9)).alias("zscore"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        # Long signal when:
        # 1. RSI < oversold (oversold condition)
        # 2. BB compression < 1.0 (bands contracting)
        # 3. z-score < -entry (price below rolling mean)
        # Short signal when:
        # 1. RSI > overbought
        # 2. BB compression < 1.0
        # 3. z-score > entry
        # Long signal when z-score crosses below -entry AND regime is compressed or RSI is oversold.
        # Short signal when z-score crosses above +entry AND regime is compressed or RSI is overbought.
        # Exit when z-score reverts to within exit threshold.
        # Position maintained via forward-fill.
        return df.select(
            pl.when(
                (pl.col("zscore") < pl.lit(-self.zscore_entry))
                & (
                    (pl.col("bb_compression") < pl.lit(1.2))
                    | (pl.col("rsi") < pl.lit(self.rsi_oversold))
                )
            )
            .then(1)
            .when(
                (pl.col("zscore") > pl.lit(self.zscore_entry))
                & (
                    (pl.col("bb_compression") < pl.lit(1.2))
                    | (pl.col("rsi") > pl.lit(self.rsi_overbought))
                )
            )
            .then(-1)
            .when(
                pl.col("zscore").abs() <= pl.lit(self.zscore_exit)
            )
            .then(-1)
            .otherwise(None)
            .alias("raw_signal"),
        ).with_columns([
            pl.col("raw_signal").forward_fill().fill_null(0).alias("signal"),
        ]).select("signal").to_series()


PARAM_GRID = {
    "vwap_window": [14, 20, 30],
    "zscore_entry": [1.5, 2.0, 2.5],
    "zscore_exit": [0.5],
    "bb_lookback": [20],
}

DEFAULT_PARAMS = {
    "vwap_window": 20,
    "zscore_entry": 2.0,
    "zscore_exit": 0.5,
    "bb_lookback": 20,
}
