"""
S5: Funding Rate Carry Strategy

Perpetual swap funding rates create drift between spot and perp.
When funding is negative (shorts pay longs), long perp captures the funding flow.

LONG-ONLY strategy (backtest engine does not support short positions):
  1  → enter long when funding_rate <= funding_entry_threshold (shorts pay longs)
  -1 → exit/close position when funding_rate >= funding_exit_threshold
  0  → hold current position

Requires columns: open, high, low, close, volume (polars DataFrame)
Optional columns: funding_rate (float) — real Binance funding rates merged by scripts/merge_funding_rates.py
"""

from __future__ import annotations

import polars as pl

from trading_agent.strategies.base import Strategy, register_strategy

_NAME = "funding_carry"


@register_strategy(_NAME)
class FundingCarryStrategy(Strategy):
    """Funding rate carry strategy — LONG-only."""

    name = _NAME

    def __init__(self, params: dict | None = None) -> None:
        super().__init__(params)
        self.funding_entry_threshold = float(self.params.get("funding_entry_threshold", -0.0001))
        self.funding_exit_threshold = float(self.params.get("funding_exit_threshold", 0.0))
        self.max_hold_periods = int(self.params.get("max_hold_periods", 12))  # ~12h max hold
        self.vol_window = int(self.params.get("vol_window", 20))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        vol = df["close"].pct_change().rolling_std(self.vol_window).alias("real_vol")
        exprs = [vol]
        # Real funding rate is merged into parquet via scripts/merge_funding_rates.py.
        # If not present, fall back to synthetic (momentum-based, for dev/testing only).
        if "funding_rate" in df.columns:
            exprs.append(
                pl.col("funding_rate")
                .forward_fill()
                .backward_fill()
                .fill_null(0.0)
                .alias("fr_filled")
            )
        else:
            exprs.append(
                (pl.col("close").pct_change().rolling_mean(12) * 1.5)
                .clip(-0.001, 0.001)
                .fill_null(0.0)
                .alias("fr_filled")
            )
        return df.with_columns(*exprs).with_columns([
            pl.col("real_vol").rolling_mean(self.vol_window).alias("vol_median"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Generate funding carry signals.

        Signal semantics (per backtest engine):
          1  → enter LONG (shorts are paying longs — funding < entry_threshold)
         -1 → EXIT/close position (funding has reverted to >= exit_threshold)
          0  → HOLD (no action)
        """
        return df.select(
            pl.when(
                pl.col("fr_filled") <= pl.lit(self.funding_entry_threshold)
            )
            .then(1)
            .when(
                pl.col("fr_filled") >= pl.lit(self.funding_exit_threshold)
            )
            .then(-1)
            .otherwise(0)
            .alias("signal")
        ).to_series()


PARAM_GRID = {
    "funding_entry_threshold": [-0.0001, -0.00008, -0.00005, -0.00003],
    "funding_exit_threshold": [0.0, 0.00005],
    "vol_window": [20],
}

DEFAULT_PARAMS = {
    "funding_entry_threshold": -0.00008,
    "funding_exit_threshold": 0.00005,
    "max_hold_periods": 0,  # 0 = disabled (hold through funding period)
    "vol_window": 20,
}
