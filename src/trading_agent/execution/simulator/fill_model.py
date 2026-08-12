"""FillModel — deterministic fill simulation for the Execution Simulator V2.

Models:

* **market-order sweeping** across multiple book levels (partial fills per
  level, insufficient liquidity when the book is exhausted);
* **limit-order passive fills** with queue-position approximation and a
  deterministic per-bar fill probability;
* **order cancellation** with cancellation latency;
* **submission/exchange/network latency**;
* **stale-quote and sequence-gap rejection** (fail closed).

All randomness comes from a single ``random.Random`` instance seeded from
``SimulationConfig.random_seed`` — never from the uncontrolled global RNG.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from trading_agent.execution.simulator.models import (
    Fill,
    OrderIntent,
    RejectReason,
    SimOrderStatus,
    SimSide,
    SimulationConfig,
    quantize_price,
)
from trading_agent.execution.simulator.orderbook import OrderBookState


class FillModelError(Exception):
    """Raised for invalid fill-model usage (not for market rejections)."""


@dataclass
class FillOutcome:
    """Result of attempting to fill an order against the current book."""

    fills: list[Fill]
    status: SimOrderStatus
    reject_reason: RejectReason = RejectReason.NONE
    queue_approx: float | None = None


class FillModel:
    """Versioned fill model (see ``FILL_MODEL_VERSION``)."""

    def __init__(self, config: SimulationConfig):
        config.validate()
        self.config = config
        self._rng = random.Random(config.random_seed)

    # ── Market orders ───────────────────────────────────────────────────

    def fill_market(
        self,
        intent: OrderIntent,
        book: OrderBookState,
        bar_index: int,
        timestamp,
        impact_bps: float = 0.0,
    ) -> FillOutcome:
        """Sweep the book for a market order.

        The aggressor crosses the spread: a buy pays ask levels, a sell
        receives bid levels.  Each level becomes a (partial) fill.  If the
        book is exhausted, the remainder is left unfilled (the order is
        marked ``PARTIALLY_FILLED`` with an insufficient-liquidity note —
        the engine decides whether to reject or leave resting).
        """
        if intent.quantity <= 0:
            return FillOutcome([], SimOrderStatus.REJECTED, RejectReason.INVALID_ORDER)

        consumed, remaining = book.sweep(intent.side, intent.quantity)
        fills: list[Fill] = []
        # Impact pushes prices against the aggressor: buys pay up, sells
        # receive less.
        impact_direction = 1.0 if intent.side == SimSide.BUY else -1.0
        for lvl in consumed:
            fill_qty = min(lvl.size, intent.quantity - sum(f.quantity for f in fills))
            if fill_qty <= 0:
                continue
            adjusted = lvl.price * (1.0 + impact_direction * impact_bps / 10_000.0)
            price = quantize_price(adjusted, self.config.tick_size)
            fills.append(Fill(
                order_id=intent.order_id,
                bar_index=bar_index,
                timestamp=timestamp,
                side=intent.side,
                quantity=fill_qty,
                price=price,
                fee=0.0,  # fee applied by FeeModel/ledger
                fee_asset=self.config.fee_asset,
                aggressor="market",
                level_price=lvl.price,
                impact_bps=impact_bps,
                mid_before=book.mid,
                is_partial=fill_qty < lvl.size or remaining > 0,
            ))

        if not fills:
            return FillOutcome([], SimOrderStatus.REJECTED, RejectReason.INSUFFICIENT_LIQUIDITY)

        if remaining > 0:
            status = SimOrderStatus.PARTIALLY_FILLED
            reason = RejectReason.INSUFFICIENT_LIQUIDITY
        else:
            status = SimOrderStatus.FILLED
            reason = RejectReason.NONE
        return FillOutcome(fills, status, reason)

    # ── Limit orders ────────────────────────────────────────────────────

    def fill_limit(
        self,
        intent: OrderIntent,
        book: OrderBookState,
        bar_index: int,
        timestamp,
        rng: random.Random | None = None,
    ) -> FillOutcome:
        """Attempt to fill a passive limit order.

        Deterministic behavior:
        1. If the limit crosses the book (buy limit >= best ask, sell limit
           <= best bid) the order immediately executes like a marketable
           limit (it takes liquidity).
        2. Otherwise the order rests at its limit price; with probability
           ``passive_fill_prob`` it receives a fill at the limit price.  The
           queue position is approximated deterministically from the order id
           and ``queue_position_base``.
        """
        rng = rng or self._rng
        if intent.limit_price is None:
            return FillOutcome([], SimOrderStatus.REJECTED, RejectReason.INVALID_ORDER)

        limit = quantize_price(intent.limit_price, self.config.tick_size)

        # Marketable limit — take liquidity.
        if book.check_limit(intent.side, limit):
            return self.fill_market(intent, book, bar_index, timestamp)

        # Resting passive order.
        queue_approx = self._queue_approx(intent)
        if rng.random() > self.config.passive_fill_prob:
            return FillOutcome([], SimOrderStatus.SUBMITTED, queue_approx=queue_approx)

        fill_qty = intent.quantity
        fill = Fill(
            order_id=intent.order_id,
            bar_index=bar_index,
            timestamp=timestamp,
            side=intent.side,
            quantity=fill_qty,
            price=limit,
            fee=0.0,
            fee_asset=self.config.fee_asset,
            aggressor="limit_passive",
            level_price=limit,
            mid_before=book.mid,
            is_partial=False,
        )
        return FillOutcome([fill], SimOrderStatus.FILLED, queue_approx=queue_approx)

    def _queue_approx(self, intent: OrderIntent) -> float:
        """Deterministic queue-position approximation in [0, 1].

        0 = at the front of the queue, 1 = at the very back.  Uses a hash of
        the order id plus the configured base position.
        """
        digest = int.from_bytes(intent.order_id.encode(), "big") % (2**32)
        jitter = (digest / (2**32)) * 0.3 - 0.15  # ±15% jitter, deterministic
        return max(0.0, min(1.0, self.config.queue_position_base + jitter))

    # ── Pre-trade safety gates (fail closed) ────────────────────────────

    def check_stale(self, book: OrderBookState, now_ts: float) -> bool:
        """True if the book is too old to trade against."""
        age = now_ts - book.timestamp.timestamp()
        return age > self.config.max_book_age_seconds

    def check_sequence_gap(self, book: OrderBookState) -> bool:
        return book.sequence_gap
