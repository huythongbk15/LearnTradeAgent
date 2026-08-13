"""
Order Router - Smart Order Routing

Provides intelligent order execution across multiple exchanges:
- Best price routing (price improvement)
- Order splitting (TWAP/VWAP)
- Multi-venue execution
- Slippage estimation
- Execution quality analytics
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from collections import defaultdict

from trading_agent.exchanges.models import (
    Symbol,
    Order,
    OrderSide,
    OrderType,
    OrderStatus,
    TimeInForce,
    Ticker,
    OrderBook,
    AssetClass,
    Balance,
    Position,
)
from trading_agent.exchanges.ccxt_adapter import MultiExchangeManager
from trading_agent.exchanges.alpaca_adapter import AlpacaAdapter
from trading_agent.exchanges.oanda_adapter import OANDAAdapter

logger = logging.getLogger(__name__)


class RoutingStrategy(str, Enum):
    """Order routing strategies"""

    BEST_PRICE = "best_price"  # Route to best bid/ask
    TWAP = "twap"  # Time-weighted average price
    VWAP = "vwap"  # Volume-weighted average price
    SPLIT = "split"  # Split across venues
    MARKETABLE = "marketable"  # Take liquidity at best price
    PASSIVE = "passive"  # Provide liquidity (post-only)


@dataclass
class ExecutionPlan:
    """Order execution plan"""

    symbol: Symbol
    side: OrderSide
    total_size: Decimal
    strategy: RoutingStrategy
    child_orders: list[dict] = field(
        default_factory=list
    )  # exchange, size, price, type
    estimated_slippage: Decimal = Decimal(0)
    estimated_fees: Decimal = Decimal(0)
    time_horizon: timedelta = timedelta(minutes=5)
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class VenueQuote:
    """Quote from a single venue"""

    exchange_id: str
    adapter: Any  # ExchangeAdapter
    ticker: Ticker
    order_book: OrderBook
    latency_ms: float
    fee_rate: Decimal


class ExecutionAlgorithm(ABC):
    """Base class for execution algorithms"""

    @abstractmethod
    async def create_plan(
        self,
        symbol: Symbol,
        side: OrderSide,
        size: Decimal,
        venues: list[VenueQuote],
        time_horizon: timedelta,
        max_participation: float = 0.1,
    ) -> ExecutionPlan:
        pass

    @abstractmethod
    async def execute(self, plan: ExecutionPlan) -> list[Order]:
        pass


class BestPriceRouter(ExecutionAlgorithm):
    """Route to venue with best price"""

    async def create_plan(
        self,
        symbol: Symbol,
        side: OrderSide,
        size: Decimal,
        venues: list[VenueQuote],
        time_horizon: timedelta,
        max_participation: float = 0.1,
    ) -> ExecutionPlan:
        if not venues:
            raise ValueError("No venues available")

        # Find best price
        if side == OrderSide.BUY:
            best = min(venues, key=lambda v: v.ticker.ask or float("inf"))
            price = best.ticker.ask
        else:
            best = max(venues, key=lambda v: v.ticker.bid or 0)
            price = best.ticker.bid

        # Estimate slippage from order book
        ob = best.order_book
        if side == OrderSide.BUY and ob.asks:
            available = sum(a.size for a in ob.asks[:5])
            if float(available) < float(size):
                slippage = Decimal("0.001")  # 10 bps estimate
            else:
                slippage = Decimal("0.0002")
        else:
            slippage = Decimal("0.0002")

        return ExecutionPlan(
            symbol=symbol,
            side=side,
            total_size=size,
            strategy=RoutingStrategy.BEST_PRICE,
            child_orders=[
                {
                    "exchange": best.exchange_id,
                    "adapter": best.adapter,
                    "size": size,
                    "price": price,
                    "type": OrderType.LIMIT,
                    "time_in_force": TimeInForce.IOC,
                }
            ],
            estimated_slippage=slippage,
            estimated_fees=size * price * best.fee_rate,
        )

    async def execute(self, plan: ExecutionPlan) -> list[Order]:
        results = []
        for child in plan.child_orders:
            order = Order(
                id=f"{plan.symbol.base}_{datetime.now().timestamp()}",
                symbol=plan.symbol,
                side=plan.side,
                type=child["type"],
                size=child["size"],
                price=child["price"],
                time_in_force=child["time_in_force"],
            )
            try:
                executed = await child["adapter"].create_order(order)
                results.append(executed)
            except Exception as e:
                logger.error(f"BestPriceRouter execution failed: {e}")
                order.status = OrderStatus.REJECTED
                order.error = str(e)
                results.append(order)
        return results


class TWAPRouter(ExecutionAlgorithm):
    """Time-Weighted Average Price execution"""

    def __init__(self, slice_interval: timedelta = timedelta(minutes=1)):
        self.slice_interval = slice_interval

    async def create_plan(
        self,
        symbol: Symbol,
        side: OrderSide,
        size: Decimal,
        venues: list[VenueQuote],
        time_horizon: timedelta,
        max_participation: float = 0.1,
    ) -> ExecutionPlan:
        if not venues:
            raise ValueError("No venues available")

        # Use best venue for TWAP
        best = venues[0]  # Could be enhanced to pick by liquidity

        n_slices = max(
            1, int(time_horizon.total_seconds() / self.slice_interval.total_seconds())
        )
        slice_size = size / n_slices

        child_orders = []
        for i in range(n_slices):
            child_orders.append(
                {
                    "exchange": best.exchange_id,
                    "adapter": best.adapter,
                    "size": slice_size,
                    "price": None,  # Market orders for TWAP
                    "type": OrderType.MARKET,
                    "time_in_force": TimeInForce.IOC,
                    "delay": i * self.slice_interval,
                }
            )

        return ExecutionPlan(
            symbol=symbol,
            side=side,
            total_size=size,
            strategy=RoutingStrategy.TWAP,
            child_orders=child_orders,
            estimated_slippage=Decimal("0.0005") * n_slices,
            estimated_fees=sum(
                slice_size * best.ticker.last * best.fee_rate for _ in range(n_slices)
            )
            if best.ticker.last
            else Decimal(0),
            time_horizon=time_horizon,
        )

    async def execute(self, plan: ExecutionPlan) -> list[Order]:
        results = []
        for child in plan.child_orders:
            await asyncio.sleep(child["delay"].total_seconds())
            order = Order(
                id=f"{plan.symbol.base}_twap_{datetime.now().timestamp()}",
                symbol=plan.symbol,
                side=plan.side,
                type=child["type"],
                size=child["size"],
                time_in_force=child["time_in_force"],
            )
            try:
                executed = await child["adapter"].create_order(order)
                results.append(executed)
            except Exception as e:
                logger.error(f"TWAP execution failed: {e}")
                order.status = OrderStatus.REJECTED
                order.error = str(e)
                results.append(order)
        return results


class SplitRouter(ExecutionAlgorithm):
    """Split order across multiple venues"""

    async def create_plan(
        self,
        symbol: Symbol,
        side: OrderSide,
        size: Decimal,
        venues: list[VenueQuote],
        time_horizon: timedelta,
        max_participation: float = 0.1,
    ) -> ExecutionPlan:
        if not venues:
            raise ValueError("No venues available")

        # Distribute proportionally to venue liquidity (simplified: equal split)
        n_venues = len(venues)
        per_venue = size / n_venues

        child_orders = []
        total_fees = Decimal(0)
        total_slippage = Decimal(0)

        for venue in venues:
            price = venue.ticker.ask if side == OrderSide.BUY else venue.ticker.bid
            child_orders.append(
                {
                    "exchange": venue.exchange_id,
                    "adapter": venue.adapter,
                    "size": per_venue,
                    "price": price,
                    "type": OrderType.LIMIT,
                    "time_in_force": TimeInForce.IOC,
                }
            )
            total_fees += per_venue * (price or Decimal(0)) * venue.fee_rate
            total_slippage += Decimal("0.0003")

        return ExecutionPlan(
            symbol=symbol,
            side=side,
            total_size=size,
            strategy=RoutingStrategy.SPLIT,
            child_orders=child_orders,
            estimated_slippage=total_slippage / n_venues,
            estimated_fees=total_fees,
        )

    async def execute(self, plan: ExecutionPlan) -> list[Order]:
        # Execute all child orders concurrently
        tasks = []
        for child in plan.child_orders:
            order = Order(
                id=f"{plan.symbol.base}_split_{datetime.now().timestamp()}",
                symbol=plan.symbol,
                side=plan.side,
                type=child["type"],
                size=child["size"],
                price=child["price"],
                time_in_force=child["time_in_force"],
            )
            tasks.append(child["adapter"].create_order(order))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        executed_orders = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(f"SplitRouter child {i} failed: {result}")
                order = Order(
                    id=f"{plan.symbol.base}_split_{i}",
                    symbol=plan.symbol,
                    side=plan.side,
                    type=plan.child_orders[i]["type"],
                    size=plan.child_orders[i]["size"],
                    status=OrderStatus.REJECTED,
                    error=str(result),
                )
                executed_orders.append(order)
            else:
                executed_orders.append(result)
        return executed_orders


class VWAPRouter(ExecutionAlgorithm):
    """Volume-Weighted Average Price - simplified version"""

    async def create_plan(
        self,
        symbol: Symbol,
        side: OrderSide,
        size: Decimal,
        venues: list[VenueQuote],
        time_horizon: timedelta,
        max_participation: float = 0.1,
    ) -> ExecutionPlan:
        # Simplified: delegate to TWAP for now
        twap = TWAPRouter()
        return await twap.create_plan(
            symbol, side, size, venues, time_horizon, max_participation
        )

    async def execute(self, plan: ExecutionPlan) -> list[Order]:
        twap = TWAPRouter()
        return await twap.execute(plan)


class OrderRouter:
    """
    Smart Order Router

    Features:
    - Multi-venue price discovery
    - Multiple execution algorithms (Best Price, TWAP, VWAP, Split)
    - Latency-aware routing
    - Fee optimization
    - Execution quality monitoring
    """

    def __init__(
        self,
        multi_exchange: MultiExchangeManager,
        alpaca: AlpacaAdapter | None = None,
        oanda: OANDAAdapter | None = None,
        default_strategy: RoutingStrategy = RoutingStrategy.BEST_PRICE,
    ):
        self.multi_exchange = multi_exchange
        self.alpaca = alpaca
        self.oanda = oanda
        self.default_strategy = default_strategy
        self._algorithms: dict[RoutingStrategy, ExecutionAlgorithm] = {
            RoutingStrategy.BEST_PRICE: BestPriceRouter(),
            RoutingStrategy.TWAP: TWAPRouter(),
            RoutingStrategy.VWAP: VWAPRouter(),
            RoutingStrategy.SPLIT: SplitRouter(),
        }
        self._execution_history: list[dict] = []

    def get_algorithm(self, strategy: RoutingStrategy) -> ExecutionAlgorithm:
        return self._algorithms.get(strategy, self._algorithms[self.default_strategy])

    async def get_venue_quotes(self, symbol: Symbol) -> list[VenueQuote]:
        """Fetch quotes from all available venues"""
        venues = []

        # CCXT exchanges
        for exchange_id, adapter in self.multi_exchange.exchanges.items():
            if not adapter.is_healthy():
                continue
            try:
                ticker = await adapter.fetch_ticker(symbol)
                ob = await adapter.fetch_order_book(symbol, limit=10)
                venues.append(
                    VenueQuote(
                        exchange_id=exchange_id,
                        adapter=adapter,
                        ticker=ticker,
                        order_book=ob,
                        latency_ms=50,  # Estimated
                        fee_rate=Decimal("0.001"),  # 10 bps default
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to get quote from {exchange_id}: {e}")

        # Alpaca (stocks)
        if (
            self.alpaca
            and self.alpaca.is_connected()
            and symbol.asset_class.value == "stock"
        ):
            try:
                ticker = await self.alpaca.fetch_ticker(symbol)
                ob = (
                    await self.alpaca.fetch_order_book(symbol)
                    if hasattr(self.alpaca, "fetch_order_book")
                    else OrderBook(symbol=symbol, timestamp=datetime.now())
                )
                venues.append(
                    VenueQuote(
                        exchange_id="alpaca",
                        adapter=self.alpaca,
                        ticker=ticker,
                        order_book=ob,
                        latency_ms=100,
                        fee_rate=Decimal("0.0005"),  # 5 bps
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to get quote from alpaca: {e}")

        # OANDA (forex)
        if (
            self.oanda
            and self.oanda.is_connected()
            and symbol.asset_class.value == "forex"
        ):
            try:
                ticker = await self.oanda.fetch_ticker(symbol)
                ob = await self.oanda.fetch_order_book(symbol)
                venues.append(
                    VenueQuote(
                        exchange_id="oanda",
                        adapter=self.oanda,
                        ticker=ticker,
                        order_book=ob,
                        latency_ms=80,
                        fee_rate=Decimal("0.0001"),  # 1 pip spread cost
                    )
                )
            except Exception as e:
                logger.debug(f"Failed to get quote from oanda: {e}")

        return venues

    async def create_execution_plan(
        self,
        symbol: Symbol,
        side: OrderSide,
        size: Decimal,
        strategy: RoutingStrategy | None = None,
        time_horizon: timedelta = timedelta(minutes=5),
        max_participation: float = 0.1,
    ) -> ExecutionPlan:
        """Create optimal execution plan"""
        strategy = strategy or self.default_strategy
        venues = await self.get_venue_quotes(symbol)

        if not venues:
            raise ValueError(f"No liquid venues for {symbol}")

        algorithm = self.get_algorithm(strategy)
        plan = await algorithm.create_plan(
            symbol, side, size, venues, time_horizon, max_participation
        )
        return plan

    async def execute_plan(self, plan: ExecutionPlan) -> list[Order]:
        """Execute an execution plan"""
        algorithm = self.get_algorithm(plan.strategy)
        orders = await algorithm.execute(plan)

        # Record execution
        self._execution_history.append(
            {
                "plan": plan,
                "orders": orders,
                "timestamp": datetime.now(),
                "filled_size": sum(o.filled_size for o in orders),
                "avg_price": sum(o.avg_fill_price * o.filled_size for o in orders)
                / sum(o.filled_size for o in orders)
                if orders
                else Decimal(0),
            }
        )

        return orders

    async def smart_order(
        self,
        symbol: Symbol,
        side: OrderSide,
        size: Decimal,
        strategy: RoutingStrategy | None = None,
        time_horizon: timedelta = timedelta(minutes=5),
    ) -> list[Order]:
        """One-shot smart order: plan + execute"""
        plan = await self.create_execution_plan(
            symbol, side, size, strategy, time_horizon
        )
        return await self.execute_plan(plan)

    def get_execution_quality(self, symbol: Symbol | None = None) -> dict:
        """Analyze execution quality"""
        history = self._execution_history
        if symbol:
            history = [h for h in history if h["plan"].symbol == symbol]

        if not history:
            return {}

        total_filled = sum(h["filled_size"] for h in history)
        total_slippage = sum(
            (h["avg_price"] - h["plan"].child_orders[0].get("price", h["avg_price"]))
            / h["avg_price"]
            for h in history
            if h["avg_price"] > 0
        ) / len(history)

        return {
            "total_orders": len(history),
            "total_filled": float(total_filled),
            "avg_slippage_bps": float(total_slippage * 10000),
            "fill_rate": sum(1 for h in history if h["filled_size"] > 0) / len(history),
        }


class AccountManager:
    """
    Unified Account Manager across all exchanges

    Provides:
    - Aggregated balances across venues
    - Net position calculation (long/short netting)
    - Margin/leverage monitoring
    - P&L aggregation
    """

    def __init__(
        self,
        multi_exchange: MultiExchangeManager,
        alpaca: AlpacaAdapter | None = None,
        oanda: OANDAAdapter | None = None,
    ):
        self.multi_exchange = multi_exchange
        self.alpaca = alpaca
        self.oanda = oanda

    async def fetch_total_balance(self) -> dict[AssetClass, Balance]:
        """Fetch and aggregate balances across all venues"""
        all_balances: dict[AssetClass, dict[str, dict]] = defaultdict(
            lambda: defaultdict(
                lambda: {"free": Decimal(0), "used": Decimal(0), "total": Decimal(0)}
            )
        )

        # CCXT exchanges
        for exchange_id, adapter in self.multi_exchange.exchanges.items():
            if not adapter.is_healthy():
                continue
            try:
                balances = await adapter.fetch_balance()
                for asset_class, balance in balances.items():
                    for currency, amounts in balance.assets.items():
                        all_balances[asset_class][currency]["free"] += Decimal(
                            str(amounts["free"])
                        )
                        all_balances[asset_class][currency]["used"] += Decimal(
                            str(amounts["used"])
                        )
                        all_balances[asset_class][currency]["total"] += Decimal(
                            str(amounts["total"])
                        )
            except Exception as e:
                logger.debug(f"Failed to fetch balance from {exchange_id}: {e}")

        # Alpaca
        if self.alpaca and self.alpaca.is_connected():
            try:
                balances = await self.alpaca.fetch_balance()
                for asset_class, balance in balances.items():
                    for currency, amounts in balance.assets.items():
                        all_balances[asset_class][currency]["free"] += Decimal(
                            str(amounts["free"])
                        )
                        all_balances[asset_class][currency]["used"] += Decimal(
                            str(amounts["used"])
                        )
                        all_balances[asset_class][currency]["total"] += Decimal(
                            str(amounts["total"])
                        )
            except Exception as e:
                logger.debug(f"Failed to fetch balance from alpaca: {e}")

        # OANDA
        if self.oanda and self.oanda.is_connected():
            try:
                balances = await self.oanda.fetch_balance()
                for asset_class, balance in balances.items():
                    for currency, amounts in balance.assets.items():
                        all_balances[asset_class][currency]["free"] += Decimal(
                            str(amounts["free"])
                        )
                        all_balances[asset_class][currency]["used"] += Decimal(
                            str(amounts["used"])
                        )
                        all_balances[asset_class][currency]["total"] += Decimal(
                            str(amounts["total"])
                        )
            except Exception as e:
                logger.debug(f"Failed to fetch balance from oanda: {e}")

        # Convert to Balance objects
        result = {}
        for asset_class, currencies in all_balances.items():
            result[asset_class] = Balance(
                asset_class=asset_class,
                assets={
                    k: {
                        "free": float(v["free"]),
                        "used": float(v["used"]),
                        "total": float(v["total"]),
                    }
                    for k, v in currencies.items()
                },
            )
        return result

    async def fetch_net_positions(self) -> dict[Symbol, Position]:
        """Fetch and net positions across venues"""
        positions: dict[Symbol, list[Position]] = defaultdict(list)

        # CCXT
        for exchange_id, adapter in self.multi_exchange.exchanges.items():
            if not adapter.is_healthy():
                continue
            try:
                pos_list = await adapter.fetch_positions()
                for pos in pos_list:
                    positions[pos.symbol].append(pos)
            except Exception as e:
                logger.debug(f"Failed to fetch positions from {exchange_id}: {e}")

        # Alpaca
        if self.alpaca and self.alpaca.is_connected():
            try:
                pos_list = await self.alpaca.fetch_positions()
                for pos in pos_list:
                    positions[pos.symbol].append(pos)
            except Exception as e:
                logger.debug(f"Failed to fetch positions from alpaca: {e}")

        # OANDA
        if self.oanda and self.oanda.is_connected():
            try:
                pos_list = await self.oanda.fetch_positions()
                for pos in pos_list:
                    positions[pos.symbol].append(pos)
            except Exception as e:
                logger.debug(f"Failed to fetch positions from oanda: {e}")

        # Net positions
        netted = {}
        for symbol, pos_list in positions.items():
            total_size = sum(p.size for p in pos_list)
            if total_size == 0:
                continue

            # Weighted average entry price
            total_notional = sum(p.size * p.entry_price for p in pos_list)
            avg_entry = total_notional / total_size if total_size != 0 else Decimal(0)

            # Current mark price (weighted)
            total_mark_notional = sum(p.size * p.mark_price for p in pos_list)
            avg_mark = (
                total_mark_notional / total_size if total_size != 0 else Decimal(0)
            )

            netted[symbol] = Position(
                symbol=symbol,
                size=total_size,
                entry_price=avg_entry,
                mark_price=avg_mark,
                unrealized_pnl=sum(p.unrealized_pnl for p in pos_list),
                realized_pnl=sum(p.realized_pnl for p in pos_list),
                leverage=Decimal(1),
                margin_used=sum(p.margin_used for p in pos_list),
            )

        return netted

    async def get_total_portfolio_value(self) -> Decimal:
        """Calculate total portfolio value in USD"""
        balances = await self.fetch_total_balance()
        positions = await self.fetch_net_positions()

        total = Decimal(0)

        # Add cash balances (assume USD stablecoins = $1)
        for asset_class, balance in balances.items():
            for currency, amounts in balance.assets.items():
                if currency in ("USDT", "USDC", "BUSD", "DAI", "FDUSD", "TUSD", "USD"):
                    total += Decimal(str(amounts["total"]))

        # Add position values
        for symbol, pos in positions.items():
            total += pos.notional

        return total

    def get_margin_health(self) -> dict:
        """Get margin health across all accounts"""
        # This would require fetching margin info from each venue
        # Simplified version
        return {
            "healthy": True,
            "total_margin_used": 0,
            "total_margin_available": 0,
            "margin_ratio": 0,
        }
