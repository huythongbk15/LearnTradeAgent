"""
S5: Funding Rate Carry Strategy

Perpetual swap funding rates create drift between spot and perp.
When funding is negative (shorts pay longs), long perp captures the funding flow.

LONG-ONLY strategy (backtest engine does not support short positions):
  1  → enter long when funding_rate <= funding_entry_threshold
  -1 → exit/close position when funding_rate >= funding_exit_threshold
  0  → hold current position

Real Binance funding rates are positive ~86% of the time (median ~0.000063).
The entry test uses a *rolling minimum* (fr_min) over fr_lookback_bars so that
a transient negative-funding episode inside the inference window still produces
an actionable entry signal at the decision bar.  The exit test uses the
latest forward-filled funding rate so the position is closed promptly when
funding reverts.

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
        self.fr_lookback_bars = int(self.params.get("fr_lookback_bars", 22))  # full warmup window

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        vol = df["close"].pct_change().rolling_std(self.vol_window).alias("real_vol")
        exprs = [vol]
        # Real funding rate is merged into parquet via scripts/merge_funding_rates.py.
        # If not present, fall back to synthetic (momentum-based, for dev/testing only).
        if "funding_rate" in df.columns:
            fr_expr = (
                pl.col("funding_rate")
                .forward_fill()
                .backward_fill()
                .fill_null(0.0)
            )
            exprs.append(fr_expr.alias("fr_filled"))
            # Rolling minimum over the full warmup window so transient
            # negative-funding episodes inside the inference window are
            # still detected at the decision (last) bar.
            exprs.append(fr_expr.rolling_min(self.fr_lookback_bars).forward_fill().alias("fr_min"))
        else:
            synthetic = (
                (pl.col("close").pct_change().rolling_mean(12) * 1.5)
                .clip(-0.001, 0.001)
                .fill_null(0.0)
            )
            exprs.append(synthetic.alias("fr_filled"))
            exprs.append(synthetic.rolling_min(self.fr_lookback_bars).forward_fill().alias("fr_min"))
        return df.with_columns(*exprs).with_columns([
            pl.col("real_vol").rolling_mean(self.vol_window).alias("vol_median"),
        ])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Generate funding carry signals.

        Signal semantics (per backtest engine):
          1  → enter LONG (shorts are paying longs — funding dropped below
                entry_threshold within the lookback window)
         -1 → EXIT/close position (funding reverted to or above exit_threshold)
          0  → HOLD (no action)

        Uses *fr_min* (rolling minimum of forward-filled funding rate over
        ``fr_lookback_bars``) for the entry test so that a fleeting
        negative-funding bar inside the window is not missed at the decision
        bar.  Exit uses the latest ``fr_filled`` to react promptly when
        funding reverts.
        """
        return df.select(
            pl.when(
                pl.col("fr_min") <= pl.lit(self.funding_entry_threshold)
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
    "max_hold_periods": [0],
    "vol_window": [20],
    "fr_lookback_bars": [22],
}

DEFAULT_PARAMS = {
    "funding_entry_threshold": -0.00008,
    "funding_exit_threshold": 0.00005,
    "max_hold_periods": 0,  # 0 = disabled (hold through funding period)
    "vol_window": 20,
    "fr_lookback_bars": 22,
}
