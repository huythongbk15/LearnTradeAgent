"""H-LIQ-REVERSAL-01: fixed, causal shock-reversal research candidate.

The liquidity explanation is a hypothesis, not an observed order-flow fact.
Signals are events, not position targets. Native execution owns fills/stops.
"""
from __future__ import annotations

import math

import polars as pl

from trading_agent.strategies.base import Strategy

DEFAULT_PARAMS = {"drop_threshold": 0.015, "hold_bars": 6}
WARMUP_BARS = 2 * DEFAULT_PARAMS["hold_bars"] + 3


class LiquidityReversalStrategy(Strategy):
    """Fixed one-hour close-return shock, de-clustered entry, timed exit."""

    name = "liquidity_reversal"

    def __init__(self, params: dict | None = None) -> None:
        resolved = {**DEFAULT_PARAMS, **(params or {})}
        if set(resolved) != set(DEFAULT_PARAMS):
            raise ValueError("liquidity_reversal accepts only its fixed parameters")
        if (type(resolved["hold_bars"]) is not int
                or resolved["hold_bars"] != DEFAULT_PARAMS["hold_bars"]
                or type(resolved["drop_threshold"]) not in (int, float)
                or not math.isfinite(resolved["drop_threshold"])
                or resolved["drop_threshold"] != DEFAULT_PARAMS["drop_threshold"]):
            raise ValueError("parameter change requires a new hypothesis, not a search")
        super().__init__(resolved)

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        close = pl.col("close")
        previous = close.shift(1)
        valid = (close.is_finite() & previous.is_finite()
                 & (close > 0) & (previous > 0)).fill_null(False)
        return df.with_columns(
            pl.when(valid).then(close / previous - 1.0)
            .otherwise(None).alias("closed_return")
        )

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        hold = self.params["hold_bars"]
        # A shock must be preceded by `hold` valid, non-shock returns.
        # This finite-memory rule is identical on full history and canonical
        # windows; no recursive position timer is reset at a window boundary.
        shock = (pl.col("closed_return") <= -self.params["drop_threshold"]).fill_null(False)
        prior_clear = (
            shock.cast(pl.Int64).shift(1).rolling_sum(hold) == 0
        ).fill_null(False)
        prior_valid = (
            pl.col("closed_return").is_not_null().cast(pl.Int64)
            .shift(1).rolling_sum(hold) == hold
        ).fill_null(False)
        entries = df.select((shock & prior_clear & prior_valid).alias("entry"))
        return entries.select(
            pl.when(pl.col("entry").shift(hold).fill_null(False)).then(-1)
            .when(pl.col("entry")).then(1).otherwise(0)
            .cast(pl.Int8).alias("signal")
        ).to_series()
