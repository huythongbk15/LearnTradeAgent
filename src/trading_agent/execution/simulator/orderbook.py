"""OrderBookState — deterministic order book for the Execution Simulator V2.

Two sources are supported:

1. **OHLCV-derived** (default): when no real L2 data is available, a book is
   reconstructed from a bar — mid from the bar open, spread from
   ``SimulationConfig.spread_bps``, and depth from the previous bar's volume.
   This is an honest approximation: it is NOT a real L2 book, and callers must
   treat it as such (see RealityGapReport).
2. **Explicit L2 snapshots**: ``from_l2`` builds a book from real
   ``(price, size)`` levels.

All operations are deterministic — no uncontrolled randomness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Iterable

from trading_agent.execution.simulator.models import (
    BookLevel,
    SimSide,
    SimulationConfig,
    quantize_price,
)


@dataclass
class OrderBookState:
    """Snapshot of a limit order book."""

    symbol: str
    bids: list[BookLevel] = field(default_factory=list)  # descending price
    asks: list[BookLevel] = field(default_factory=list)  # ascending price
    mid: float = 0.0
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    sequence: int = 0
    stale: bool = False
    sequence_gap: bool = False

    def best_bid(self) -> float | None:
        return self.bids[0].price if self.bids else None

    def best_ask(self) -> float | None:
        return self.asks[0].price if self.asks else None

    def spread(self) -> float:
        bb, ba = self.best_bid(), self.best_ask()
        if bb is None or ba is None:
            return math.inf
        return ba - bb

    def spread_bps(self) -> float:
        spread = self.spread()
        if not math.isfinite(spread) or self.mid <= 0:
            return math.inf
        return spread / self.mid * 10_000.0

    def total_ask_size(self) -> float:
        return sum(lvl.size for lvl in self.asks)

    def total_bid_size(self) -> float:
        return sum(lvl.size for lvl in self.bids)

    def imbalance(self) -> float:
        """(bid_size - ask_size) / (bid_size + ask_size), in [-1, 1]."""
        bid, ask = self.total_bid_size(), self.total_ask_size()
        denom = bid + ask
        if denom <= 0:
            return 0.0
        return (bid - ask) / denom

    def market_capacity(self, side: SimSide) -> float:
        """Total resting size available to sweep on the given side."""
        if side == SimSide.BUY:
            return self.total_ask_size()
        return self.total_bid_size()

    def sweep(self, side: SimSide, quantity: float) -> tuple[list[BookLevel], float]:
        """Simulate sweeping ``quantity`` against the book.

        Returns ``(consumed_levels, remaining_qty)``.  Levels are consumed
        in price priority until the quantity is satisfied or the book is
        exhausted.  The returned levels keep their original sizes (the caller
        decides how much of the last level to take).
        """
        levels = self.asks if side == SimSide.BUY else self.bids
        remaining = quantity
        consumed: list[BookLevel] = []
        for lvl in levels:
            if remaining <= 0:
                break
            take = min(remaining, lvl.size)
            consumed.append(BookLevel(price=lvl.price, size=take))
            remaining -= take
        return consumed, max(0.0, remaining)

    def check_limit(self, side: SimSide, limit_price: float) -> bool:
        """Whether a passive limit order would cross/touch the book now."""
        if side == SimSide.BUY:
            ask = self.best_ask()
            return ask is not None and limit_price >= ask
        bid = self.best_bid()
        return bid is not None and limit_price <= bid

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "bids": [lvl.to_dict() for lvl in self.bids],
            "asks": [lvl.to_dict() for lvl in self.asks],
            "mid": self.mid,
            "timestamp": self.timestamp.isoformat(),
            "sequence": self.sequence,
            "stale": self.stale,
            "sequence_gap": self.sequence_gap,
        }


def build_book_from_bar(
    *,
    symbol: str,
    open_price: float,
    previous_volume: float,
    config: SimulationConfig,
    sequence: int,
    timestamp: datetime | None = None,
) -> OrderBookState:
    """Build a synthetic L1/L2 book from an OHLCV bar (deterministic).

    The mid is the bar open.  Spread is ``config.spread_bps``.  Depth per
    side is ``config.depth_volume_share * previous_volume`` split across
    ``config.depth_levels`` levels, sized geometrically (deeper levels are
    larger, which is a common observed shape).

    **No look-ahead**: ``previous_volume`` must be a volume known *before*
    the current bar (typically the previous bar's volume).
    """
    config.validate()
    if open_price <= 0:
        raise ValueError(f"open_price must be positive, got {open_price}")
    if previous_volume < 0:
        raise ValueError(f"previous_volume must be >= 0, got {previous_volume}")

    half_spread = open_price * config.spread_bps / 10_000.0 / 2.0
    tick = config.tick_size
    mid = quantize_price(open_price, tick)
    bid0 = quantize_price(mid - half_spread, tick)
    ask0 = quantize_price(mid + half_spread, tick)
    # Re-center mid so the quote is symmetric around the quantized mid.
    bid0 = max(tick, bid0)
    ask0 = max(bid0 + tick, ask0)
    mid = (bid0 + ask0) / 2.0

    total_depth = config.depth_volume_share * previous_volume
    bids: list[BookLevel] = []
    asks: list[BookLevel] = []
    for level_idx in range(1, config.depth_levels + 1):
        # Geometric depth profile: level k holds k / sum(1..N) of the depth.
        weight = level_idx / (config.depth_levels * (config.depth_levels + 1) / 2.0)
        size = total_depth * weight
        bid_price = quantize_price(bid0 - (level_idx - 1) * tick, tick)
        ask_price = quantize_price(ask0 + (level_idx - 1) * tick, tick)
        if bid_price <= 0:
            continue
        bids.append(BookLevel(price=bid_price, size=size))
        asks.append(BookLevel(price=ask_price, size=size))

    if not bids or not asks:
        raise ValueError("book is empty after construction; check tick_size/spread")

    return OrderBookState(
        symbol=symbol,
        bids=bids,
        asks=asks,
        mid=mid,
        timestamp=timestamp or datetime.now(UTC),
        sequence=sequence,
    )


def build_book_from_l2(
    *,
    symbol: str,
    bids: Iterable[tuple[float, float]],
    asks: Iterable[tuple[float, float]],
    sequence: int,
    timestamp: datetime | None = None,
) -> OrderBookState:
    """Build a book from explicit L2 ``(price, size)`` levels.

    Levels are sorted into price priority (bids descending, asks ascending).
    """
    bid_levels = sorted(
        (BookLevel(price=float(p), size=float(s)) for p, s in bids if s > 0),
        key=lambda lvl: lvl.price,
        reverse=True,
    )
    ask_levels = sorted(
        (BookLevel(price=float(p), size=float(s)) for p, s in asks if s > 0),
        key=lambda lvl: lvl.price,
    )
    if not bid_levels or not ask_levels:
        raise ValueError("L2 book must have at least one bid and one ask level")
    best_bid = bid_levels[0].price
    best_ask = ask_levels[0].price
    if best_bid >= best_ask:
        raise ValueError(f"crossed book: best_bid {best_bid} >= best_ask {best_ask}")
    return OrderBookState(
        symbol=symbol,
        bids=bid_levels,
        asks=ask_levels,
        mid=(best_bid + best_ask) / 2.0,
        timestamp=timestamp or datetime.now(UTC),
        sequence=sequence,
    )
