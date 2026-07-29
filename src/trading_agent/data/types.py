"""
Shared data types for the trading system.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class Timeframe(str, Enum):
    """Standardized timeframes."""

    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    H4 = "4h"
    D1 = "1d"
    W1 = "1w"

    def to_seconds(self) -> int:
        mapping = {
            "1m": 60,
            "5m": 300,
            "15m": 900,
            "30m": 1800,
            "1h": 3600,
            "4h": 14400,
            "1d": 86400,
            "1w": 604800,
        }
        return mapping[self.value]

    def to_milliseconds(self) -> int:
        return self.to_seconds() * 1000


@dataclass
class OHLCV:
    """Single OHLCV candle."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class OHLCVList:
    """Collection of OHLCV candles with metadata."""

    exchange: str
    symbol: str
    timeframe: str
    candles: list[OHLCV] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.candles)

    def to_dataframe(self):
        """Convert to polars DataFrame."""
        import polars as pl

        records = [c.to_dict() for c in self.candles]
        if not records:
            return pl.DataFrame(
                schema={
                    "timestamp": pl.Datetime,
                    "open": pl.Float64,
                    "high": pl.Float64,
                    "low": pl.Float64,
                    "close": pl.Float64,
                    "volume": pl.Float64,
                }
            )
        df = pl.DataFrame(records)
        return df.with_columns(
            pl.col("exchange").lit(self.exchange),
            pl.col("symbol").lit(self.symbol),
            pl.col("timeframe").lit(self.timeframe),
        )
