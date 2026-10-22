"""Proposed candidate: DI/ADX state-edge entry/exit.

PROPOSAL ONLY — not registered, not eligible for any campaign.

This module implements the contract in
``data/bnb_di_adx_stateedge_20261020_01/STATE_EDGE_CONTRACT.md``. It is
deliberately NOT decorated with ``@register_strategy`` and is NOT imported by
``trading_agent/strategies/__init__.py``, so it cannot be selected by name by
any campaign, worker or registry until the contract has been independently
reviewed and registration has been preregistered (step 3).

Relationship to the frozen control
----------------------------------
The control ``ma_adx`` (``MaAdxCrossover``) is an **event-crossover** policy:
it trades the MA20/MA80 crossing and uses ADX/DI as a filter. This candidate is
a **state** policy: it trades the rising and falling edges of the bull state
``plus_di > minus_di AND adx > 25``. The ADX and DI arithmetic is copied
unchanged from the control so the two candidates differ only in the trigger.
The MA pair is not part of the trigger here.

It inherits **no** result, metric or parameter from the control.

Signal semantics
----------------
    B(k)      := plus_di(k) > minus_di(k) AND adx(k) > adx_threshold
    prev(k)   := B(k-1)

    BUY  (+1) : B(k) and prev(k) is False   (rising edge)
    SELL (-1) : not B(k) and prev(k) is True (falling edge)
    otherwise:  0

Three clauses are load-bearing and are covered by the synthetic suite:

* A BUY is emitted only on a rising edge, so a continuing bull state never
  produces a second BUY.
* A transition needs a prior observation: at k = 0 the predecessor is unknown,
  so no edge is asserted (a null predecessor is not False).
* The SELL is the falling edge of the same state — not an MA down-crossing.

Re-entry: a BUY after a protective-stop exit requires a NEW rising edge. While
the state stays on after a stop, the candidate stays flat. There is no
re-entry inside the same episode and no candidate-specific cooldown; the
native 24 h circuit-breaker cooldown keeps its native meaning.

Stop, authority, sizing, cost handling and terminal-inventory semantics are
the native ones and are untouched by this module.
"""

from __future__ import annotations

from typing import Any

import polars as pl

from trading_agent.strategies.base import Strategy


class DiAdxStateEdgeStrategy(Strategy):
    """DI/ADX state-edge candidate: trade the edges of the trending state.

    There is **no moving-average component**. The trigger is the edge of the
    state ``plus_di > minus_di AND adx > adx_threshold``; the MA pair that
    triggered the frozen control is not part of this candidate and must not be
    reintroduced within this experiment.

    Parameters
    ----------
    adx_period : int
        ADX/DI smoothing period. Default 14 (native).
    adx_threshold : float
        Minimum ADX for the state to be considered trending. Default 25.0
        (native).
    """

    name = "di_adx_state_edge"

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        super().__init__(params)
        self.adx_period = int(self.params.get("adx_period", 14))
        self.adx_threshold = float(self.params.get("adx_threshold", 25.0))

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        """Add the native ADX/DI columns and the bull state.

        The ADX/DI arithmetic is identical to ``MaAdxCrossover`` so that the
        control and this candidate differ only in the trigger.
        """
        high = pl.col("high")
        low = pl.col("low")
        close = pl.col("close")
        prev_close = close.shift(1)
        tr1 = high - low
        tr2 = (high - prev_close).abs()
        tr3 = (low - prev_close).abs()
        tr = pl.max_horizontal(tr1, tr2, tr3)
        atr = tr.rolling_mean(window_size=self.adx_period)

        up_move = high - high.shift(1)
        down_move = low.shift(1) - low
        plus_dm = (
            pl.when((up_move > down_move) & (up_move > 0)).then(up_move).otherwise(0.0)
        )
        minus_dm = (
            pl.when((down_move > up_move) & (down_move > 0)).then(down_move).otherwise(0.0)
        )
        plus_di = 100 * (
            plus_dm.rolling_mean(window_size=self.adx_period) / (atr + 1e-9)
        )
        minus_di = 100 * (
            minus_dm.rolling_mean(window_size=self.adx_period) / (atr + 1e-9)
        )
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
        adx = dx.rolling_mean(window_size=self.adx_period).alias("adx")
        trend_up = (plus_di > minus_di).alias("trend_up")

        bull_state = (
            (plus_di > minus_di) & (adx > self.adx_threshold)
        ).alias("bull_state")

        return df.with_columns([adx, trend_up, bull_state])

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        """Rising edge -> BUY, falling edge -> SELL, everything else flat.

        The predecessor comparison is strict: a null predecessor (the first
        bar, or a bar before the state is defined) is NOT treated as False, so
        no transition is asserted without a prior observation.
        """
        bull = pl.col("bull_state")
        prev_bull = bull.shift(1)

        rising_edge = (bull & (prev_bull == pl.lit(False))).fill_null(False)
        falling_edge = ((~bull) & (prev_bull == pl.lit(True))).fill_null(False)

        signal = (
            pl.when(rising_edge)
            .then(pl.lit(1))
            .when(falling_edge)
            .then(pl.lit(-1))
            .otherwise(pl.lit(0))
        )

        return df.select(signal.alias("signal")).to_series()
