"""
Bollinger Bands strategy — mua khi giá chạm band dưới, bán khi chạm band trên.
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy


@register_strategy("bbands")
class BBandsStrategy(Strategy):
    """Bollinger Bands — mua khi giá chạm band dưới, bán khi chạm band trên.

    Parameters
    ----------
    period : int       (default 20)
    std_dev : float    (default 2.0)
    """

    name = "bbands"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.period = int(self.params.get("period", 20))
        self.std_dev = float(self.params.get("std_dev", 2.0))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        sma = pl.col("close").rolling_mean(window_size=self.period)
        std = pl.col("close").rolling_std(window_size=self.period)

        return df.with_columns(
            [
                sma.alias("bb_mid"),
                (sma + self.std_dev * std).alias("bb_upper"),
                (sma - self.std_dev * std).alias("bb_lower"),
                pl.col("close").alias("bb_close"),
            ]
        )

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        return df.select(
            pl.when(pl.col("close") < pl.col("bb_lower"))
            .then(1)
            .when(pl.col("close") > pl.col("bb_upper"))
            .then(-1)
            .otherwise(0)
            .alias("signal")
        ).to_series()
