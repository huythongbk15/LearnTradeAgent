"""Canonical adapter for H-OWN24-CONTEXT-01.

The decision at the last closed bar of a window is the frozen rule. A timed
exit is the entry from HOLD_BARS earlier, so the canonical tail must be long
enough to see both. This class does not size, stop, or fill.
"""

from __future__ import annotations

import polars as pl

from trading_agent.research.btc_context_features import context_at, own_simple_return_24
from trading_agent.research.btc_context_rule import (
    CANONICAL_WARMUP_BARS,
    HOLD_BARS,
    entry_allowed,
)
from trading_agent.strategies.base import Strategy

WARMUP_BARS = CANONICAL_WARMUP_BARS


def attach_btc_close(target: pl.DataFrame, btc: pl.DataFrame) -> pl.DataFrame:
    """Add BTC close under an identical clock. Refuse a frame that reaches holdout."""
    from datetime import UTC, datetime

    if target.get_column("timestamp").to_list() != btc.get_column("timestamp").to_list():
        raise ValueError("BTC and target clocks differ")
    last = target.get_column("timestamp")[-1]
    if getattr(last, "tzinfo", None) is None:
        last = last.replace(tzinfo=UTC)
    if last >= datetime(2026, 2, 6, 16, tzinfo=UTC):
        raise ValueError("bundle reaches the holdout")
    return target.with_columns(btc.get_column("close").alias("btc_close"))


class Own24ContextStrategy(Strategy):
    """One frozen arm. A1 and A2 block when BTC context is absent."""

    name = "own24_context"

    def __init__(self, params: dict | None = None) -> None:
        resolved = {"arm": "A0", **dict(params or {})}
        if set(resolved) != {"arm"} or resolved["arm"] not in {"A0", "A1", "A2"}:
            raise ValueError("own24_context accepts only arm A0, A1 or A2")
        super().__init__(resolved)

    def compute_indicators(self, df: pl.DataFrame) -> pl.DataFrame:
        return df

    def generate_signals(self, df: pl.DataFrame) -> pl.Series:
        signals = [0] * df.height
        if df.height:
            last = df.height - 1
            if self._allowed(df, last):
                signals[last] = 1
            prior = last - HOLD_BARS
            if prior >= 0 and self._allowed(df, prior):
                signals[last] = -1
        return pl.Series("signal", signals, dtype=pl.Int8)

    def _allowed(self, df: pl.DataFrame, index: int) -> bool:
        view = df.head(index + 1)
        time_name = "time" if "time" in view.columns else "timestamp"
        if time_name not in view.columns or "close" not in view.columns:
            return False
        stamps = tuple(view[time_name].to_list())
        closes = tuple(float(value) for value in view["close"].to_list())
        try:
            own = own_simple_return_24(closes, stamps, index)
        except (ValueError, TypeError):
            return False
        arm = self.params["arm"]
        if arm == "A0":
            return entry_allowed("A0", own)
        if "btc_close" not in view.columns:
            return False
        btc = tuple(float(value) for value in view["btc_close"].to_list())
        try:
            features = {item.name: item for item in context_at(index, btc, closes, stamps)}
        except (ValueError, TypeError):
            return False
        return entry_allowed(arm, own, features)
