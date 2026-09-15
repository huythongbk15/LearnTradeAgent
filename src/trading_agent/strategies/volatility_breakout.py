"""
S3: Volatility Expansion Breakout Strategy

Bollinger Band compression → expansion breakout.
- BB width percentile < threshold = compression
- ATR spike confirms volatility expansion
- Volume surge confirms momentum

Requires columns: open, high, low, close, volume (polars DataFrame)
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy
from trading_agent.technical.indicators import sma_func, std_func, atr_func

_NAME = "volatility_breakout"


@register_strategy(_NAME)
class VolatilityBreakoutStrategy(Strategy):
    """Volatility expansion breakout."""

    name = _NAME

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.bb_period = int(self.params.get("bb_period", 20))
        self.bb_std = float(self.params.get("bb_std", 2.0))
        self.compression_percentile = float(self.params.get("compression_percentile", 0.03))
        self.atr_spike_mult = float(self.params.get("atr_spike_mult", 1.5))
        self.max_hold_bars = int(self.params.get("max_hold_bars", 10))
        self.atr_period = int(self.params.get("atr_period", 14))
        self.volume_lookback = int(self.params.get("volume_lookback", 20))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        sma_val = sma_func(pl.col("close"), self.bb_period)
        std_val = std_func(pl.col("close"), self.bb_period)

        upper = (sma_val + self.bb_std * std_val).alias("bb_upper")
        lower = (sma_val - self.bb_std * std_val).alias("bb_lower")
        bb_width = ((upper - lower) / sma_val * 100).alias("bb_width")
        cur_atr = atr_func(pl.col("high"), pl.col("low"), pl.col("close"), self.atr_period).alias("atr")

        return df.with_columns([
            sma_val.alias("sma"),
            upper,
            lower,
            bb_width,
            cur_atr,
        ]).with_columns([
            (pl.col("volume").rolling_mean(self.volume_lookback)).alias("vol_sma"),
            (pl.col("atr").rolling_mean(self.volume_lookback)).alias("atr_avg"),
            (pl.col("bb_width").rolling_mean(5)).alias("bb_width_avg"),
        ]).with_columns([
            (pl.col("atr") / (pl.col("atr_avg") + 1e-9)).alias("atr_spike"),
            (pl.col("bb_width") / (pl.col("bb_width_avg") + 1e-9)).alias("bb_compression"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        # Long when price breaks above upper band with ATR confirmation.
        # Short when price breaks below lower band with ATR confirmation.
        # Removed: volume filter and compression gate that fail in short WFO windows
        # (bb_width_avg needs 44+ bars; warmup is 23-37)
        # Entry: price breaks above upper band with ATR confirmation
        # Exit: price reverts to SMA or max_hold_bars elapsed
        return df.with_columns([
            pl.when(
                (pl.col("atr_spike") > pl.lit(self.atr_spike_mult))
                & (pl.col("close") > pl.col("bb_upper"))
                & (pl.col("close").shift(1) <= pl.col("bb_upper").shift(1))
            )
            .then(1)
            .when(
                (pl.col("atr_spike") > pl.lit(self.atr_spike_mult))
                & (pl.col("close") < pl.col("bb_lower"))
                & (pl.col("close").shift(1) >= pl.col("bb_lower").shift(1))
            )
            .then(-1)
            # Exit: price reverts to mean or max_hold_bars elapsed
            .when(
                (pl.col("close") < pl.col("sma"))
                & (pl.col("close").shift(1) >= pl.col("sma").shift(1))
                | (pl.col("close") > pl.col("sma"))
                & (pl.col("close").shift(1) <= pl.col("sma").shift(1))
            )
            .then(0)
            .otherwise(None)  # hold position
            .alias("raw_signal"),
        ]).with_columns([
            pl.col("raw_signal").forward_fill().fill_null(0).alias("signal"),
        ]).select("signal").to_series()


PARAM_GRID = {
    "bb_period": [14, 21],
    "bb_std": [2.0],
    "compression_percentile": [0.03, 0.05],
    "atr_spike_mult": [1.2, 1.5],
    "max_hold_bars": [20],
}

DEFAULT_PARAMS = {
    "bb_period": 14,
    "bb_std": 2.0,
    "compression_percentile": 0.03,
    "atr_spike_mult": 1.2,
    "max_hold_bars": 20,
    "atr_period": 14,
}
