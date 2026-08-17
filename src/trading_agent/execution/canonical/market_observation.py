"""Enriched MarketObservation — adds execution-layer identity and provenance.

Extends the broker-free ``trading_agent.research.forecast.MarketObservation``
with observation-level identifiers and data-manifest references required
for idempotency and audit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Mapping

from trading_agent.research.forecast import MarketObservation as BaseMarketObservation


class BarState(str, Enum):
    """Whether the observation is based on a closed or forming bar."""

    CLOSED = "closed"
    FORMING = "forming"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class EnrichedMarketObservation:
    """A point-in-time market input with execution-layer provenance.

    All fields from the base ``MarketObservation`` are preserved.  New fields
    are appended to support idempotent ingestion and cross-aggregate tracing.
    """

    # ── Base observation (broker-free) ──────────────────────────────────
    symbol: str
    observed_at: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    features: Mapping[str, Any] = field(default_factory=dict)

    # ── Execution-layer identity ────────────────────────────────────────
    observation_id: str = ""
    venue: str = ""
    source: str = ""
    timeframe: str = ""

    # ── Bar lifecycle ───────────────────────────────────────────────────
    bar_open_at: datetime | None = None
    bar_close_at: datetime | None = None
    is_closed: bool = False

    # ── Provenance ──────────────────────────────────────────────────────
    data_manifest_id: str = ""
    feature_artifact_id: str = ""

    def __post_init__(self) -> None:
        # Delegate base validation to the parent dataclass by reconstructing
        # a temporary instance.  We cannot call super().__post_init__()
        # because frozen dataclasses do not support it cleanly.
        base = BaseMarketObservation(
            symbol=self.symbol,
            observed_at=self.observed_at,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            features=self.features,
        )
        # The base __post_init__ raises on invalid data; if we reach here,
        # the values are valid.  We do not mutate self.

        if self.observation_id and not isinstance(self.observation_id, str):
            raise TypeError("observation_id must be a str")
        if not isinstance(self.venue, str):
            raise TypeError("venue must be a str")
        if not isinstance(self.source, str):
            raise TypeError("source must be a str")
        if not isinstance(self.timeframe, str):
            raise TypeError("timeframe must be a str")
        if self.bar_open_at is not None and self.bar_open_at.tzinfo is None:
            raise ValueError("bar_open_at must be timezone-aware")
        if self.bar_close_at is not None and self.bar_close_at.tzinfo is None:
            raise ValueError("bar_close_at must be timezone-aware")
        if self.is_closed and self.bar_close_at is None:
            raise ValueError("closed observations must have bar_close_at")

    @property
    def bar_state(self) -> BarState:
        if self.is_closed:
            return BarState.CLOSED
        if self.bar_close_at is not None and datetime.now(UTC) >= self.bar_close_at:
            return BarState.CLOSED
        if self.bar_open_at is not None and self.bar_close_at is not None and datetime.now(UTC) < self.bar_close_at:
            return BarState.FORMING
        return BarState.UNKNOWN

    @property
    def ohlcv(self) -> tuple[float, float, float, float, float]:
        """Return OHLCV as a tuple for hashing / serialization."""
        return (self.open, self.high, self.low, self.close, self.volume)

    def to_base(self) -> BaseMarketObservation:
        """Strip execution-layer fields back to the broker-free type."""
        return BaseMarketObservation(
            symbol=self.symbol,
            observed_at=self.observed_at,
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
            features=self.features,
        )


__all__ = ["BarState", "EnrichedMarketObservation"]
