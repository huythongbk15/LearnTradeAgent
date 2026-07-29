"""
MA Crossover strategy — mua khi fast MA cắt lên trên slow MA, bán khi ngược lại.
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy


@register_strategy("ma_crossover")
class MaCrossover(Strategy):
    """MA Crossover — mua khi fast MA cắt lên trên slow MA, bán khi ngược lại.

    Parameters
    ----------
    fast_period : int  (default 20)
    slow_period : int  (default 50)
    """
    name = "ma_crossover"

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.fast = int(self.params.get("fast_period", 20))
        self.slow = int(self.params.get("slow_period", 50))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        return df.with_columns([
            pl.col("close")
            .rolling_mean(window_size=self.fast)
            .alias(f"ma_{self.fast}"),
            pl.col("close")
            .rolling_mean(window_size=self.slow)
            .alias(f"ma_{self.slow}"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        fast_col = f"ma_{self.fast}"
        slow_col = f"ma_{self.slow}"

        # 1 khi fast > slow, -1 khi fast < slow, 0 khi bằng
        raw = (
            pl.when(pl.col(fast_col) > pl.col(slow_col))
            .then(1)
            .when(pl.col(fast_col) < pl.col(slow_col))
            .then(-1)
            .otherwise(0)
        )

        # Chỉ lấy tín hiệu khi có **sự thay đổi** (crossover)
        signal = raw.alias("_raw")
        prev = raw.shift(1).alias("_prev")

        return (
            df.with_columns(signal, prev)
            .select(
                pl.when(
                    (pl.col("_raw") != pl.col("_prev"))
                    & (pl.col("_raw") != 0)
                )
                .then(pl.col("_raw"))
                .otherwise(0)
                .alias("signal")
            )
            .to_series()
        )
