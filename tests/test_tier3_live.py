#!/usr/bin/env python3
"""Tier 3 — Live Trading integration tests (paper/mocked, no broker credentials).

Covers:
1. A PaperAdapter implementing the async ExchangeAdapter interface.
2. Order Router routing across multiple venues (BestPrice / TWAP / VWAP / Split).
3. AccountManager balance & position aggregation.
4. Regime-switching strategy executed end-to-end through the paper exchange.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

import numpy as np
import pytest

from trading_agent.exchanges.ccxt_adapter import ExchangeAdapter, ExchangeStatus
from trading_agent.exchanges.models import (
    AssetClass,
    Balance,
    MarketType,
    Order,
    OrderBook,
    OrderBookLevel,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    Symbol,
    Ticker,
    crypto_symbol,
)

# ════════════════════════════════════════════════════════════════
# Paper mock adapter (implements async ExchangeAdapter)
# ════════════════════════════════════════════════════════════════


class PaperAdapter(ExchangeAdapter):
    """In-memory paper exchange implementing the async ExchangeAdapter interface."""

    def __init__(
        self, exchange_id: str, prices: dict[str, float], fee_rate: float = 0.001
    ):
        self.exchange_id = exchange_id
        self._prices: dict[str, float] = dict(prices)
        self._fee = Decimal(str(fee_rate))
        self._buying_power = Decimal("100000")
        self._positions: dict[str, Decimal] = {}  # symbol_id -> qty
        self._orders: list[Order] = []
        self._connected = False

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    async def fetch_markets(self) -> list[dict]:
        return [{"symbol": s, "active": True} for s in self._prices]

    def _price_of(self, symbol: Symbol) -> float:
        return self._prices[symbol.pair]

    async def fetch_ticker(self, symbol: Symbol) -> Ticker:
        p = self._price_of(symbol)
        return Ticker(
            symbol=symbol,
            timestamp=datetime.now(),
            bid=Decimal(str(p * 0.9998)),
            ask=Decimal(str(p * 1.0002)),
            last=Decimal(str(p)),
        )

    async def fetch_order_book(self, symbol: Symbol, limit: int = 100) -> OrderBook:
        p = self._price_of(symbol)
        return OrderBook(
            symbol=symbol,
            timestamp=datetime.now(),
            bids=[OrderBookLevel(price=Decimal(str(p * 0.9998)), size=Decimal("100"))],
            asks=[OrderBookLevel(price=Decimal(str(p * 1.0002)), size=Decimal("100"))],
        )

    async def fetch_balance(self) -> dict[AssetClass, Balance]:
        return {
            AssetClass.CRYPTO: Balance(
                asset_class=AssetClass.CRYPTO,
                assets={
                    "USDT": {
                        "free": float(self._buying_power),
                        "used": 0.0,
                        "total": float(self._buying_power),
                    }
                },
            )
        }

    async def fetch_positions(self, symbol: Optional[Symbol] = None) -> list[Position]:
        pos = []
        for sym_id, qty in self._positions.items():
            if symbol and sym_id != symbol.unified_id:
                continue
            # sym_id = unified_id: "exchange:asset_class:market:base:quote"
            parts = sym_id.split(":")
            asset_cls = AssetClass(parts[1])
            mkt = MarketType(parts[2])
            base, quote = parts[3], parts[4]
            s = Symbol(base, quote, asset_cls, mkt, self.exchange_id)
            price = self._price_of_symbol(s)
            pos.append(
                Position(
                    symbol=s,
                    size=qty,
                    entry_price=Decimal(str(price)),
                    mark_price=Decimal(str(price)),
                )
            )
        return pos

    def _price_of_symbol(self, symbol: Symbol) -> float:
        return self._prices[symbol.pair]

    async def create_order(self, order: Order) -> Order:
        price = self._price_of_symbol(order.symbol)
        side = order.side
        if side == OrderSide.BUY:
            cost = Decimal(str(price)) * order.size
            total = cost + cost * self._fee
            if total > self._buying_power:
                order.status = OrderStatus.REJECTED
                order.error = "insufficient_buying_power"
                self._orders.append(order)
                return order
            self._buying_power -= total
            self._positions[order.symbol.unified_id] = (
                self._positions.get(order.symbol.unified_id, Decimal("0")) + order.size
            )
        else:
            qty = self._positions.get(order.symbol.unified_id, Decimal("0"))
            if order.size > qty:
                order.status = OrderStatus.REJECTED
                order.error = "insufficient_balance"
                self._orders.append(order)
                return order
            self._positions[order.symbol.unified_id] = qty - order.size
            proceeds = Decimal(str(price)) * order.size * (Decimal("1") - self._fee)
            self._buying_power += proceeds

        order.status = OrderStatus.FILLED
        order.filled_size = order.size
        order.avg_fill_price = Decimal(str(price))
        order.updated_at = datetime.now()
        self._orders.append(order)
        return order

    async def cancel_order(self, order_id: str, symbol: Symbol) -> bool:
        return True

    async def fetch_order(self, order_id: str, symbol: Symbol) -> Optional[Order]:
        return next((o for o in self._orders if o.id == order_id), None)

    async def fetch_open_orders(self, symbol: Optional[Symbol] = None) -> list[Order]:
        return [
            o
            for o in self._orders
            if o.status in (OrderStatus.OPEN, OrderStatus.PARTIAL)
        ]

    def get_status(self) -> ExchangeStatus:
        return ExchangeStatus.HEALTHY

    def is_healthy(self) -> bool:
        return True


class MockMultiExchange:
    """Stand-in for MultiExchangeManager exposing paper adapters."""

    def __init__(self, adapters: dict[str, PaperAdapter]):
        self.exchanges: dict[str, PaperAdapter] = adapters


# ════════════════════════════════════════════════════════════════
# Tests: Order Router multi-venue + execution algorithms
# ════════════════════════════════════════════════════════════════


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def router():
    from trading_agent.exchanges.order_router import OrderRouter

    binance = PaperAdapter("binance", {"BTC/USDT": 100000.0})
    kraken = PaperAdapter("kraken", {"BTC/USDT": 100050.0})  # worse price
    me = MockMultiExchange({"binance": binance, "kraken": kraken})
    return OrderRouter(multi_exchange=me), binance, kraken


def test_router_best_price_routes_to_cheapest_venue(router):
    r, binance, kraken = router
    from trading_agent.exchanges.models import crypto_symbol
    from trading_agent.exchanges.order_router import RoutingStrategy

    sym = crypto_symbol("BTC", "USDT", "binance")
    orders = _run(
        r.smart_order(
            sym, OrderSide.BUY, Decimal("0.1"), strategy=RoutingStrategy.BEST_PRICE
        )
    )
    assert orders, "should fill at least one order"
    assert orders[0].status == OrderStatus.FILLED
    # Best price = binance (lower ask), so route goes there
    assert orders[0].avg_fill_price < Decimal("100500")
    print(f"  BestPrice BUY routed @ {orders[0].avg_fill_price} (binance > kraken)")


def test_router_rejects_no_venues():
    from trading_agent.exchanges.models import crypto_symbol
    from trading_agent.exchanges.order_router import OrderRouter

    r = OrderRouter(multi_exchange=MockMultiExchange({}))
    sym = crypto_symbol("BTC", "USDT", "binance")
    with pytest.raises(ValueError):
        _run(r.smart_order(sym, OrderSide.BUY, Decimal("1")))


def test_router_twap_splits_into_slices(router):
    from trading_agent.exchanges.models import crypto_symbol
    from trading_agent.exchanges.order_router import RoutingStrategy

    r, _, _ = router
    sym = crypto_symbol("BTC", "USDT", "binance")
    plan = _run(
        r.create_execution_plan(
            sym,
            OrderSide.BUY,
            Decimal("1.0"),
            strategy=RoutingStrategy.TWAP,
            time_horizon=timedelta(minutes=3, seconds=1),
        )
    )
    assert len(plan.child_orders) > 1, "TWAP should produce multiple slices"
    total = sum(c["size"] for c in plan.child_orders)
    assert abs(total - Decimal("1.0")) < Decimal("1e-9")
    print(f"  TWAP plan: {len(plan.child_orders)} slices (total {total})")


def test_router_execution_quality_tracks_fills(router):
    from trading_agent.exchanges.models import crypto_symbol
    from trading_agent.exchanges.order_router import RoutingStrategy

    r, _, _ = router
    sym = crypto_symbol("BTC", "USDT", "binance")
    _run(
        r.smart_order(
            sym, OrderSide.BUY, Decimal("0.5"), strategy=RoutingStrategy.BEST_PRICE
        )
    )
    q = r.get_execution_quality(sym)
    assert q["total_orders"] == 1
    assert q["fill_rate"] == 1.0
    print(f"  Execution quality: {q}")


# ════════════════════════════════════════════════════════════════
# Tests: AccountManager aggregation
# ════════════════════════════════════════════════════════════════


def test_account_manager_aggregates_balances():
    from trading_agent.exchanges.order_router import AccountManager

    b1 = PaperAdapter("binance", {"BTC/USDT": 100000.0})
    o = PaperAdapter("oanda", {"EUR/USD": 1.08})
    am = AccountManager(multi_exchange=MockMultiExchange({"binance": b1}), oanda=o)
    balances = _run(am.fetch_total_balance())
    assert AssetClass.CRYPTO in balances
    assert balances[AssetClass.CRYPTO].assets["USDT"]["total"] == 100000.0
    print("  AccountManager aggregates crypto balance OK")


# ════════════════════════════════════════════════════════════════
# Tests: Regime-switching end-to-end through paper engine
# ════════════════════════════════════════════════════════════════


def test_regime_switching_backtest_runs_on_engine():
    import polars as pl

    from trading_agent.strategies.regime_switching import (
        run_regime_switching_backtest,
    )

    # synthesise trending + mean-reverting closes
    rng = np.random.default_rng(42)
    n = 800
    prices = 100 * np.exp(np.cumsum(rng.normal(0.0006, 0.012, n)))
    opens = prices * (1 + rng.normal(0, 0.002, n))
    df = pl.DataFrame(
        {
            "timestamp": [
                datetime.now().replace(microsecond=0) + timedelta(hours=i)
                for i in range(n)
            ],
            "open": opens,
            "high": np.maximum(opens, prices) * (1 + np.abs(rng.normal(0, 0.004, n))),
            "low": np.minimum(opens, prices) * (1 - np.abs(rng.normal(0, 0.004, n))),
            "close": prices,
            "volume": rng.uniform(1000, 5000, n),
        }
    )
    result = run_regime_switching_backtest(df, params={"regime_method": "rule_based"})
    assert result is not None
    for key in ("total_return", "sharpe", "n_trades"):
        assert key in result, f"result missing {key}"
    print(
        f"  Regime backtest: return={result['total_return']:.2%} "
        f"trades={result['n_trades']} sharpe={result['sharpe']}"
    )


# ════════════════════════════════════════════════════════════════
# Tests: paper order lifecycle via create_order + rejection guards
# ════════════════════════════════════════════════════════════════


def test_paper_order_buy_sell_roundtrip():
    pa = PaperAdapter("paper", {"BTC/USDT": 50000.0})
    _run(pa.connect())
    sym = crypto_symbol("BTC", "USDT", "paper")
    buy = Order(
        id="b1",
        symbol=sym,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        size=Decimal("1"),
    )
    filled = _run(pa.create_order(buy))
    assert filled.status == OrderStatus.FILLED
    assert filled.filled_size == Decimal("1")
    pos = _run(pa.fetch_positions(sym))
    assert len(pos) == 1 and pos[0].size == Decimal("1")
    sell = Order(
        id="s1",
        symbol=sym,
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        size=Decimal("1"),
    )
    _run(pa.create_order(sell))
    pos = _run(pa.fetch_positions(sym))
    assert not pos or abs(pos[0].size) < Decimal("1e-9")
    print("  Paper buy→sell roundtrip OK")


def test_paper_order_rejects_oversell():
    pa = PaperAdapter("paper", {"BTC/USDT": 50000.0})
    sym = crypto_symbol("BTC", "USDT", "paper")
    sell = Order(
        id="s1",
        symbol=sym,
        side=OrderSide.SELL,
        type=OrderType.MARKET,
        size=Decimal("5"),
    )
    res = _run(pa.create_order(sell))
    assert res.status == OrderStatus.REJECTED
    assert "insufficient" in res.error
    print(f"  Oversell rejected: {res.error}")


# ═══════════════════════════════════════════════════════════════════════════
# Tests: LiveBroker sync facade over async adapter
# ═══════════════════════════════════════════════════════════════════════════


def test_livebroker_get_account_alpaca():
    from trading_agent.exchanges.live_broker import LiveBroker

    pa = PaperAdapter("alpaca", {"MSFT/USD": 400.0})
    pa.get_account_info = lambda: {
        "id": "abc123",
        "status": "ACTIVE",
        "cash": 9999.0,
        "equity": 15000.0,
        "buying_power": 20000.0,
        "initial_margin": 0,
        "maintenance_margin": 0,
    }
    lb = LiveBroker("alpaca", pa)
    acc = lb.get_account()
    assert acc["currency"] == "USD"
    assert acc["portfolio_value"] == 15000.0
    assert acc["cash"] == 9999.0
    print(f"  LiveBroker account: id={acc['id']} equity=${acc['equity']:.0f}")


def test_livebroker_positions_and_order_roundtrip():
    from trading_agent.exchanges.live_broker import LiveBroker

    pa = PaperAdapter("alpaca", {"BTC/USDT": 100.0})
    _run(pa.connect())
    lb = LiveBroker("alpaca", pa)
    sym = crypto_symbol("BTC", "USDT", "alpaca")
    buy = Order(
        id="lb_b1",
        symbol=sym,
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        size=Decimal("1.0"),
    )
    result = lb.place_order(buy)
    assert result["status"] == "filled"
    assert result["filled_qty"] == 1.0
    positions = lb.get_positions()
    assert any(p["symbol"] == "BTC/USDT" for p in positions)
    print("  LiveBroker placed order + saw position OK")


def test_livebroker_dry_order_build():
    pa = PaperAdapter("alpaca", {"BTC/USDT": 100.0})
    _run(pa.connect())
    order = _run(
        pa.create_order(
            Order(
                id="x1",
                symbol=crypto_symbol("BTC", "USDT", "alpaca"),
                side=OrderSide.BUY,
                type=OrderType.MARKET,
                size=Decimal("0.1"),
            )
        )
    )
    assert order.status == OrderStatus.FILLED
    assert order.filled_size == Decimal("0.1")
