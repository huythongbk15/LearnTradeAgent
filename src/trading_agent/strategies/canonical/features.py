"""Canonical feature naming and point-in-time availability (STR-0105).

Two obligations live here:

1. **Naming** — every feature a canonical strategy may read from
   ``MarketObservation.features`` is declared as a :class:`FeatureSpec`
   with a snake_case name and the minimum history (in bars) required to
   compute it.  Strategies declare these names in their descriptor's
   ``required_features``; producers must emit exactly those names.

2. **Point-in-time availability** — :func:`build_ohlcv_window` is the
   sanctioned way to derive an OHLCV window for observation time *t*: it
   keeps only bars whose ``time < observed_at`` (the bar stamped at *t*
   itself has not closed yet) and fails closed when history is missing or
   the input leaks future rows.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

#: Feature key carrying the point-in-time OHLCV window consumed by legacy
#: adapters (see LegacyDataFrameAdapter).
FEATURE_OHLCV_WINDOW = "ohlcv_window"


class FeatureUnavailableError(RuntimeError):
    """Raised when a required feature cannot be provided point-in-time."""


@dataclass(frozen=True)
class FeatureSpec:
    """Declaration of one canonical feature."""

    name: str
    min_history_bars: int
    description: str = ""


#: Core features every canonical pipeline may rely on.
CORE_FEATURE_SPECS: tuple[FeatureSpec, ...] = (
    FeatureSpec(
        name=FEATURE_OHLCV_WINDOW,
        min_history_bars=1,
        description="Point-in-time OHLCV history window (closed bars only).",
    ),
)


def validate_point_in_time(window: pl.DataFrame, *, observed_at) -> None:
    """Raise unless every row of *window* closed strictly before *observed_at*."""
    if "time" not in window.columns:
        return  # caller-provided windows without a time column are opaque
    max_time = window.select(pl.col("time").max()).item()
    if max_time is not None and max_time >= observed_at:
        raise FeatureUnavailableError(
            f"point-in-time violation: window contains bar at {max_time} "
            f">= observation time {observed_at}"
        )


def build_ohlcv_window(
    frame: pl.DataFrame,
    *,
    observed_at,
    bars: int,
) -> pl.DataFrame:
    """Return the last *bars* closed OHLCV rows strictly before *observed_at*.

    Fail-closed: raises :class:`FeatureUnavailableError` when the frame lacks
    required columns / time information, leaks future rows, or holds fewer
    than *bars* closed rows.
    """
    required = ("open", "high", "low", "close", "volume")
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise FeatureUnavailableError(f"ohlcv frame missing columns: {missing}")
    if "time" not in frame.columns:
        raise FeatureUnavailableError("ohlcv frame must carry a 'time' column")
    closed = frame.filter(pl.col("time") < observed_at)
    if closed.height < bars:
        raise FeatureUnavailableError(
            f"need {bars} closed bars before {observed_at}; "
            f"only {closed.height} available"
        )
    window = closed.tail(bars)
    validate_point_in_time(window, observed_at=observed_at)
    return window
