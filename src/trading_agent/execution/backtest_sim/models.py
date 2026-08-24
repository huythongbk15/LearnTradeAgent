"""
Execution Simulator — Realistic Fill Modeling for Backtest & Paper Trading.

Models:
- Queue-based fill (position in order book)
- Latency simulation (network + processing)
- Slippage models (square-root, linear, fixed)
- Partial fills
- Market impact
- Maker/taker fee structure
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum

import numpy as np


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    STOP = "stop"
    STOP_LIMIT = "stop_limit"


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


class FillModel(str, Enum):
    """Fill simulation model."""

    QUEUE = "queue"  # Position in queue based on price-time priority
    IMMEDIATE = "immediate"  # Instant fill at market price
    PARTIAL = "partial"  # Random partial fills
    VWAP = "vwap"  # Volume-weighted over time window


class ImpactModel(str, Enum):
    """Market impact model."""

    NONE = "none"
    LINEAR = "linear"  # Impact ∝ quantity
    SQUARE_ROOT = "square_root"  # Impact ∝ sqrt(quantity) - Almgren-Chriss
    POWER_LAW = "power_law"  # Impact ∝ quantity^alpha


@dataclass(frozen=True, slots=True)
class SimulatedOrder:
    """Order in simulation."""

    order_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    price: float | None = None  # Limit price
    stop_price: float | None = None  # Stop trigger price
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    client_id: str = ""
    strategy_id: str = ""


@dataclass(frozen=True, slots=True)
class SimulatedFill:
    """Individual fill."""

    fill_id: str
    order_id: str
    symbol: str
    side: OrderSide
    quantity: float
    price: float
    timestamp: datetime
    fee: float
    is_maker: bool
    latency_ms: float
    mid_price_at_fill: float = 0.0  # Store mid price at fill time for slippage calc


@dataclass(frozen=True, slots=True)
class OrderBookSnapshot:
    """Simplified order book state."""

    symbol: str
    timestamp: datetime
    bid_price: float
    bid_size: float
    ask_price: float
    ask_size: float
    mid_price: float
    spread: float
    spread_bps: float
    volume_24h: float = 0.0
    volatility: float = 0.0


@dataclass(frozen=True, slots=True)
class SimulatorConfig:
    """Execution simulator configuration."""

    # Fill model
    fill_model: FillModel = FillModel.QUEUE

    # Impact model
    impact_model: ImpactModel = ImpactModel.SQUARE_ROOT
    impact_coefficient: float = 0.1  # Base impact coefficient
    impact_volatility_factor: float = 1.0  # Scale by volatility

    # Latency
    base_latency_ms: float = 50.0
    latency_jitter_ms: float = 20.0
    latency_distribution: str = "lognormal"  # lognormal, normal, uniform

    # Fees
    maker_fee_bps: float = 1.0
    taker_fee_bps: float = 5.0

    # Slippage
    base_slippage_bps: float = 2.0
    slippage_volatility_factor: float = 1.0

    # Queue model
    queue_fill_probability: float = 0.3  # Probability of being at front of queue
    partial_fill_prob: float = 0.1  # Probability of partial fill
    max_partial_fills: int = 3

    # Market hours (24/7 for crypto)
    market_open_hour: int = 0
    market_close_hour: int = 23

    # Risk limits
    max_order_size_pct_adv: float = 0.1  # Max order as % of ADV
    max_position_pct_equity: float = 0.2  # Max position as % of equity


@dataclass
class SimulatorState:
    """Runtime state of simulator."""

    open_orders: dict[str, SimulatedOrder] = field(default_factory=dict)
    order_queue: list[SimulatedOrder] = field(default_factory=list)
    fill_history: list[SimulatedFill] = field(default_factory=list)
    order_book: dict[str, OrderBookSnapshot] = field(default_factory=dict)
    current_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    rng: np.random.Generator = field(default_factory=np.random.default_rng)


class ExecutionSimulator:
    """
    High-fidelity execution simulator.

    Simulates realistic order execution including:
    - Latency (network + exchange processing)
    - Queue position for limit orders
    - Market impact (temporary + permanent)
    - Slippage
    - Partial fills
    - Maker/taker classification
    - Fees
    """

    def __init__(self, config: SimulatorConfig | None = None, seed: int | None = None):
        self.config = config or SimulatorConfig()
        self.state = SimulatorState()
        if seed is not None:
            self.state.rng = np.random.default_rng(seed)

    def update_order_book(self, snapshot: OrderBookSnapshot) -> None:
        """Update internal order book snapshot."""
        self.state.order_book[snapshot.symbol] = snapshot

    def submit_order(self, order: SimulatedOrder) -> SimulatedOrder:
        """Submit order to simulator."""
        self.state.open_orders[order.order_id] = order

        if order.order_type == OrderType.LIMIT:
            self.state.order_queue.append(order)

        return order

    def cancel_order(self, order_id: str) -> bool:
        """Cancel open order."""
        if order_id in self.state.open_orders:
            order = self.state.open_orders.pop(order_id)
            # Remove from queue
            self.state.order_queue = [
                o for o in self.state.order_queue if o.order_id != order_id
            ]
            return True
        return False

    def step(self, current_time: datetime | None = None) -> list[SimulatedFill]:
        """
        Advance simulation by one step.

        Processes queue, checks fills, applies latency.
        """
        if current_time:
            self.state.current_time = current_time

        fills = []

        # Process each open order
        for order_id, order in list(self.state.open_orders.items()):
            if order.order_type == OrderType.MARKET:
                fill = self._process_market_order(order)
                if fill:
                    fills.append(fill)
            elif order.order_type == OrderType.LIMIT:
                fill = self._process_limit_order(order)
                if fill:
                    fills.append(fill)

        # Record fills
        for fill in fills:
            self.state.fill_history.append(fill)

        return fills

    def _process_market_order(self, order: SimulatedOrder) -> SimulatedFill | None:
        """Process market order - immediate fill with slippage."""
        book = self.state.order_book.get(order.symbol)
        if not book:
            return None

        # Simulate latency
        latency = self._sample_latency()
        fill_time = self.state.current_time

        # Determine fill price with slippage
        if order.side == OrderSide.BUY:
            base_price = book.ask_price
            slippage = self._compute_slippage(book, order.quantity, OrderSide.BUY)
            fill_price = base_price * (1 + slippage)
            is_maker = False
        else:
            base_price = book.bid_price
            slippage = self._compute_slippage(book, order.quantity, OrderSide.SELL)
            fill_price = base_price * (1 - slippage)
            is_maker = False

        # Apply market impact
        impact = self._compute_impact(book, order.quantity, order.side)
        if order.side == OrderSide.BUY:
            fill_price *= 1 + impact
        else:
            fill_price *= 1 - impact

        # Partial fill?
        fill_qty = order.quantity
        if self.state.rng.random() < self.config.partial_fill_prob:
            fill_qty = order.quantity * self.state.rng.uniform(0.1, 0.9)

        # Fee
        fee_rate = self.config.taker_fee_bps / 10000
        fee = fill_qty * fill_price * fee_rate

        fill = SimulatedFill(
            fill_id=f"fill_{order.order_id}_{len(self.state.fill_history)}",
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_qty,
            price=fill_price,
            timestamp=fill_time,
            fee=fee,
            is_maker=is_maker,
            latency_ms=latency,
            mid_price_at_fill=book.mid_price,
        )

        # Remove filled order
        del self.state.open_orders[order.order_id]

        return fill

    def _process_limit_order(self, order: SimulatedOrder) -> SimulatedFill | None:
        """Process limit order - queue-based fill."""
        book = self.state.order_book.get(order.symbol)
        if not book:
            return None

        # Check if price is marketable
        if order.side == OrderSide.BUY and order.price >= book.ask_price:
            # Crosses spread - immediate fill (taker)
            return self._fill_limit_as_taker(order, book)
        elif order.side == OrderSide.SELL and order.price <= book.bid_price:
            return self._fill_limit_as_taker(order, book)

        # Check queue position
        queue_pos = self._get_queue_position(order)
        fill_prob = self.config.queue_fill_probability * (1.0 / (1.0 + queue_pos))

        if self.state.rng.random() < fill_prob:
            return self._fill_limit_as_maker(order, book)

        return None

    def _fill_limit_as_taker(
        self, order: SimulatedOrder, book: OrderBookSnapshot
    ) -> SimulatedFill:
        """Fill limit order that crosses spread (taker) - fills at market price up to limit."""
        latency = self._sample_latency()
        is_maker = False

        # Marketable limit orders fill at market price (ask for buy, bid for sell)
        # plus slippage/impact, capped at limit price
        slippage = self._compute_slippage(book, order.quantity, order.side)
        impact = self._compute_impact(book, order.quantity, order.side)

        if order.side == OrderSide.BUY:
            # Buy: fill at ask + slippage + impact, capped at limit price
            market_price = book.ask_price * (1 + slippage) * (1 + impact)
            fill_price = min(market_price, order.price)
        else:
            # Sell: fill at bid - slippage - impact, floored at limit price
            market_price = book.bid_price * (1 - slippage) * (1 - impact)
            fill_price = max(market_price, order.price)

        fill_qty = order.quantity
        if self.state.rng.random() < self.config.partial_fill_prob:
            fill_qty = order.quantity * self.state.rng.uniform(0.1, 0.9)

        fee_rate = self.config.taker_fee_bps / 10000
        fee = fill_qty * fill_price * fee_rate

        fill = SimulatedFill(
            fill_id=f"fill_{order.order_id}_{len(self.state.fill_history)}",
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_qty,
            price=fill_price,
            timestamp=self.state.current_time,
            fee=fee,
            is_maker=is_maker,
            latency_ms=latency,
            mid_price_at_fill=book.mid_price,
        )

        del self.state.open_orders[order.order_id]
        self.state.order_queue = [
            o for o in self.state.order_queue if o.order_id != order.order_id
        ]

        return fill

    def _fill_limit_as_maker(
        self, order: SimulatedOrder, book: OrderBookSnapshot
    ) -> SimulatedFill:
        """Fill limit order as maker (provides liquidity)."""
        latency = self._sample_latency()

        fill_price = order.price  # Filled at limit price
        is_maker = True

        # No slippage for maker, but still impact
        impact = (
            self._compute_impact(book, order.quantity, order.side) * 0.5
        )  # Reduced impact
        if order.side == OrderSide.BUY:
            fill_price *= 1 - impact  # Better price for maker
        else:
            fill_price *= 1 + impact

        fill_qty = order.quantity
        if self.state.rng.random() < self.config.partial_fill_prob:
            fill_qty = order.quantity * self.state.rng.uniform(0.1, 0.9)

        fee_rate = self.config.maker_fee_bps / 10000
        fee = fill_qty * fill_price * fee_rate

        fill = SimulatedFill(
            fill_id=f"fill_{order.order_id}_{len(self.state.fill_history)}",
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=fill_qty,
            price=fill_price,
            timestamp=self.state.current_time,
            fee=fee,
            is_maker=is_maker,
            latency_ms=latency,
            mid_price_at_fill=book.mid_price,
        )

        del self.state.open_orders[order.order_id]
        self.state.order_queue = [
            o for o in self.state.order_queue if o.order_id != order.order_id
        ]

        return fill

    def _get_queue_position(self, order: SimulatedOrder) -> int:
        """Get position in queue for limit order."""
        # Count orders at same price with earlier timestamp
        count = 0
        for o in self.state.order_queue:
            if (
                o.symbol == order.symbol
                and o.side == order.side
                and o.price == order.price
            ):
                if o.timestamp < order.timestamp:
                    count += 1
        return count

    def _sample_latency(self) -> float:
        """Sample latency from configured distribution."""
        if self.config.latency_distribution == "lognormal":
            # Log-normal: always positive, right-skewed
            mu = (
                np.log(self.config.base_latency_ms)
                - 0.5
                * (self.config.latency_jitter_ms / self.config.base_latency_ms) ** 2
            )
            sigma = self.config.latency_jitter_ms / self.config.base_latency_ms
            return float(self.state.rng.lognormal(mu, sigma))
        elif self.config.latency_distribution == "normal":
            return float(
                max(
                    1.0,
                    self.state.rng.normal(
                        self.config.base_latency_ms, self.config.latency_jitter_ms
                    ),
                )
            )
        else:  # uniform
            return float(
                self.state.rng.uniform(
                    self.config.base_latency_ms - self.config.latency_jitter_ms,
                    self.config.base_latency_ms + self.config.latency_jitter_ms,
                )
            )

    def _compute_slippage(
        self, book: OrderBookSnapshot, quantity: float, side: OrderSide
    ) -> float:
        """Compute slippage based on order size and market conditions."""
        # Base slippage in bps
        base_bps = self.config.base_slippage_bps

        # Scale by volatility
        vol_factor = 1.0 + book.volatility * self.config.slippage_volatility_factor

        # Scale by order size relative to book depth
        book_depth = book.bid_size if side == OrderSide.SELL else book.ask_size
        if book_depth > 0:
            size_factor = (
                1.0 + (quantity / book_depth) * 0.5
            )  # 0.5x slippage at 10% of depth
        else:
            size_factor = 1.5

        slippage_bps = base_bps * vol_factor * size_factor
        return slippage_bps / 10000

    def _compute_impact(
        self, book: OrderBookSnapshot, quantity: float, side: OrderSide
    ) -> float:
        """Compute market impact."""
        if self.config.impact_model == ImpactModel.NONE:
            return 0.0

        # Normalize quantity by daily volume
        adv = book.volume_24h / 24  # Rough hourly volume
        if adv <= 0:
            adv = book.bid_size + book.ask_size
        participation = quantity / max(adv, 1.0)

        base_impact = self.config.impact_coefficient

        if self.config.impact_model == ImpactModel.LINEAR:
            impact = base_impact * participation
        elif self.config.impact_model == ImpactModel.SQUARE_ROOT:
            impact = base_impact * np.sqrt(max(participation, 0.0))
        elif self.config.impact_model == ImpactModel.POWER_LAW:
            impact = base_impact * (max(participation, 0.0) ** 0.6)
        else:
            impact = 0.0

        # Scale by volatility
        impact *= 1.0 + book.volatility * self.config.impact_volatility_factor

        # Cap impact at reasonable level (0.5%)
        return min(impact, 0.005)

    def get_fills_for_order(self, order_id: str) -> list[SimulatedFill]:
        """Get all fills for an order."""
        return [f for f in self.state.fill_history if f.order_id == order_id]

    def get_total_filled(self, order_id: str) -> float:
        """Get total filled quantity for an order."""
        return sum(
            f.quantity for f in self.state.fill_history if f.order_id == order_id
        )

    def get_avg_fill_price(self, order_id: str) -> float | None:
        """Get VWAP fill price for an order."""
        fills = self.get_fills_for_order(order_id)
        if not fills:
            return None
        total_qty = sum(f.quantity for f in fills)
        vwap = sum(f.price * f.quantity for f in fills) / total_qty
        return vwap

    def get_total_fees(self, order_id: str) -> float:
        """Get total fees for an order."""
        return sum(f.fee for f in self.state.fill_history if f.order_id == order_id)


def create_execution_simulator(
    fill_model: FillModel = FillModel.QUEUE,
    impact_model: ImpactModel = ImpactModel.SQUARE_ROOT,
    impact_coefficient: float = 0.1,
    maker_fee_bps: float = 1.0,
    taker_fee_bps: float = 5.0,
    base_latency_ms: float = 50.0,
    latency_jitter_ms: float = 20.0,
    base_slippage_bps: float = 2.0,
    partial_fill_prob: float = 0.1,
    seed: int | None = None,
) -> ExecutionSimulator:
    """Factory function for ExecutionSimulator."""
    config = SimulatorConfig(
        fill_model=fill_model,
        impact_model=impact_model,
        impact_coefficient=impact_coefficient,
        maker_fee_bps=maker_fee_bps,
        taker_fee_bps=taker_fee_bps,
        base_latency_ms=base_latency_ms,
        latency_jitter_ms=latency_jitter_ms,
        base_slippage_bps=base_slippage_bps,
        partial_fill_prob=partial_fill_prob,
    )
    return ExecutionSimulator(config, seed)
