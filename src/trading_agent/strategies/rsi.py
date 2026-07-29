"""
RSI strategy — mua khi RSI < oversold, bán khi RSI > overbought.
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy


@register_strategy("rsi")
class RsiStrategy(Strategy):
    """RSI — mua khi RSI < oversold, bán khi RSI > overbought.

    Parameters
    ----------
    period : int      (default 14)
    oversold : int    (default 30)
    overbought : int  (default 70)
    """
    name = "rsi"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.period = int(self.params.get("period", 14))
        self.oversold = int(self.params.get("oversold", 30))
        self.overbought = int(self.params.get("overbought", 70))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        # RSI = 100 - (100 / (1 + RS)) where RS = avg_gain / avg_loss
        delta = pl.col("close").diff()

        gain = pl.when(delta > 0).then(delta).otherwise(0.0)
        loss = pl.when(delta < 0).then(-delta).otherwise(0.0)

        avg_gain = gain.rolling_mean(window_size=self.period)
        avg_loss = loss.rolling_mean(window_size=self.period)

        rs = avg_gain / (avg_loss + 1e-9)  # tránh chia 0
        rsi = (100 - 100 / (1 + rs)).alias("rsi")

        return df.with_columns(rsi)

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        return (
            df.select(
                pl.when(pl.col("rsi") < self.oversold)
                .then(1)
                .when(pl.col("rsi") > self.overbought)
                .then(-1)
                .otherwise(0)
                .alias("signal")
            )
            .to_series()
        )
