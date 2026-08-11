"""Contract tests for CCXTAdapter (audit Phase 4).

Pin the contract between the adapter and the CCXT response/request format
with a fake exchange object — no network, no real keys.  If a future CCXT
version changes the wire format, these tests break first.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderSide,
    OrderType,
    Symbol,
    TimeInForce,
)

pytest.importorskip("ccxt")

BTC_USDT = Symbol("BTC", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "binance")


def make_adapter(sandbox: bool = False, testnet: bool = False) -> CCXTAdapter:
    config = ExchangeConfig(
        id="binance",
        name="Binance",
        sandbox=sandbox,
        testnet=testnet,
        rate_limit=0,
    )
    return CCXTAdapter(config)


def make_fake_exchange() -> SimpleNamespace:
    return SimpleNamespace(
        markets={
            "BTC/USDT": {
                "symbol": "BTC/USDT",
                "base": "BTC",
                "quote": "USDT",
                "type": "spot",
                "active": True,
            }
        },
        fetch_ohlcv=lambda *a, **k: [
            [1700000000000, 42000.0, 42100.0, 41900.0, 42050.0, 12.5],
            [1700003600000, 42050.0, 42200.0, 42000.0, 42100.0, 8.0],
        ],
        create_order=lambda *a, **k: {
            "id": "ex-123",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "limit",
            "amount": 0.001,
            "price": 42000.0,
            "status": "closed",
            "filled": 0.001,
            "remaining": 0.0,
            "cost": 42.0,
            "average": 42000.0,
            "fee": {"cost": 0.02, "currency": "USDT"},
            "datetime": "2026-01-01T00:00:00.000Z",
        },
        fetch_balance=lambda *a, **k: {
            "info": {},
            "USDT": {"free": 9000.0, "used": 500.0, "total": 9500.0},
            "BTC": {"free": 0.5, "used": 0.1, "total": 0.6},
            "USD": {"free": 0.0, "used": 0.0, "total": 0.0},
        },
        set_sandbox_mode=lambda enabled: None,
        load_markets=lambda: None,
        fetch_time=lambda: 1700000000000,
    )


def wire_adapter(adapter: CCXTAdapter, exchange: SimpleNamespace) -> CCXTAdapter:
    adapter.exchange = exchange
    adapter._build_symbol_maps()
    return adapter


class TestFetchOHLCVContract:
    async def test_parses_ccxt_candles(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())
        candles = await adapter.fetch_ohlcv(BTC_USDT, "1h", limit=100)
        assert len(candles) == 2
        c = candles[0]
        assert c.symbol == BTC_USDT
        assert c.timeframe == "1h"
        assert c.timestamp == datetime.fromtimestamp(1700000000000 / 1000, tz=UTC)
        assert c.open == 42000.0
        assert c.high == 42100.0
        assert c.low == 41900.0
        assert c.close == 42050.0
        assert c.volume == 12.5

    async def test_propagates_exchange_errors(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())

        def boom(*a, **k):
            raise RuntimeError("connection refused")

        adapter.exchange.fetch_ohlcv = boom
        with pytest.raises(RuntimeError, match="connection refused"):
            await adapter.fetch_ohlcv(BTC_USDT, "1h")


class TestCreateOrderContract:
    def test_limit_order_sends_time_in_force(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())
        order = Order(
            id="o1",
            symbol=BTC_USDT,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            size=Decimal("0.001"),
            price=Decimal("42000"),
            time_in_force=TimeInForce.GTC,
        )
        params = adapter._order_to_ccxt_params(order)
        assert params["timeInForce"] == "gtc"

    def test_market_order_never_sends_time_in_force(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())
        order = Order(
            id="o2",
            symbol=BTC_USDT,
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=Decimal("0.001"),
            time_in_force=TimeInForce.FOK,
        )
        params = adapter._order_to_ccxt_params(order)
        assert "timeInForce" not in params  # Binance rejects -1106 otherwise

    def test_stop_order_requires_stop_price(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())
        order = Order(
            id="o3",
            symbol=BTC_USDT,
            side=OrderSide.SELL,
            type=OrderType.STOP,
            size=Decimal("0.001"),
            price=Decimal("40000"),
        )
        with pytest.raises(Exception, match="stop price"):
            adapter._order_to_ccxt_params(order)

    def test_stop_limit_requires_limit_price(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())
        order = Order(
            id="o4",
            symbol=BTC_USDT,
            side=OrderSide.SELL,
            type=OrderType.STOP_LIMIT,
            size=Decimal("0.001"),
            stop_price=Decimal("40000"),
        )
        with pytest.raises(Exception, match="limit price"):
            adapter._order_to_ccxt_params(order)

    def test_extra_flags_mapped(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())
        order = Order(
            id="o5",
            symbol=BTC_USDT,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            size=Decimal("0.001"),
            price=Decimal("42000"),
            reduce_only=True,
            post_only=True,
            client_order_id="cli-99",
        )
        params = adapter._order_to_ccxt_params(order)
        assert params["reduceOnly"] is True
        assert params["postOnly"] is True
        assert params["clientOrderId"] == "cli-99"

    async def test_create_order_roundtrip(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())
        order = Order(
            id="o6",
            symbol=BTC_USDT,
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            size=Decimal("0.001"),
            price=Decimal("42000"),
        )
        result = await adapter.create_order(order)
        # Adapter maps the CCXT order id into Order.id (client_order_id is
        # only set when the response carries one).
        assert result.id == "ex-123"
        assert result.exchange_order_id is None
        assert result.status.value == "filled"
        assert result.filled_size == Decimal("0.001")
        assert result.avg_fill_price == Decimal("42000.0")
        assert result.fee == Decimal("0.02")
        assert result.fee_currency == "USDT"


class TestFetchBalanceContract:
    async def test_parses_crypto_and_skips_zero(self) -> None:
        adapter = wire_adapter(make_adapter(), make_fake_exchange())
        balances = await adapter.fetch_balance()
        assert AssetClass.CRYPTO in balances
        crypto = balances[AssetClass.CRYPTO]
        assert crypto.assets["USDT"] == {"free": 9000.0, "used": 500.0, "total": 9500.0}
        assert crypto.assets["BTC"] == {"free": 0.5, "used": 0.1, "total": 0.6}
        # USD has total 0 -> omitted entirely.
        assert "USD" not in crypto.assets


class TestConnectivityContract:
    async def test_sandbox_mode_enables_ccxt_sandbox(self, monkeypatch) -> None:
        adapter = make_adapter(sandbox=True)
        exchange = make_fake_exchange()
        calls: list[bool] = []

        def set_sandbox(enabled: bool) -> None:
            calls.append(enabled)

        exchange.set_sandbox_mode = set_sandbox
        # connect() instantiates the ccxt class by config.id — stub it.
        monkeypatch.setattr("ccxt.binance", lambda config: exchange)
        await adapter.connect()
        assert calls == [True]

    async def test_connect_marks_healthy(self, monkeypatch) -> None:
        adapter = make_adapter()
        exchange = make_fake_exchange()
        monkeypatch.setattr("ccxt.binance", lambda config: exchange)
        await adapter.connect()
        assert adapter._connected is True
        assert adapter._status.value == "healthy"
        assert "BTC/USDT" in adapter._symbol_map
        assert adapter._symbol_map["BTC/USDT"] == BTC_USDT
