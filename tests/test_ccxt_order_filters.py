from __future__ import annotations

import asyncio
from decimal import ROUND_DOWN, Decimal
from types import SimpleNamespace

import ccxt
import pytest

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderConstraintError,
    OrderSide,
    OrderStatus,
    OrderType,
    Symbol,
)


class FilterExchange:
    markets = {"BTC/USDT": {}}

    def market(self, symbol):
        return {
            "symbol": symbol,
            "active": True,
            "limits": {
                "amount": {"min": 0.001, "max": 10},
                "cost": {"min": 10, "max": 100_000},
            },
        }

    def amount_to_precision(self, symbol, amount):
        return str(Decimal(str(amount)).quantize(Decimal("0.001"), rounding=ROUND_DOWN))


def adapter_with_filters():
    adapter = object.__new__(CCXTAdapter)
    adapter.exchange = FilterExchange()
    adapter._reverse_symbol_map = {}
    return adapter


def btc_symbol():
    return Symbol(
        base="BTC",
        quote="USDT",
        asset_class=AssetClass.CRYPTO,
        market_type=MarketType.SPOT,
        exchange="binance",
    )


def test_amount_is_truncated_to_exchange_precision():
    normalized = adapter_with_filters().normalize_order_amount(
        btc_symbol(),
        Decimal("0.0019"),
        reference_price=Decimal("10000"),
    )
    assert normalized == Decimal("0.001")


def test_minimum_notional_filter_is_enforced():
    with pytest.raises(OrderConstraintError, match="notional is below") as exc_info:
        adapter_with_filters().normalize_order_amount(
            btc_symbol(),
            Decimal("0.001"),
            reference_price=Decimal("100"),
        )
    assert exc_info.value.constraint == "minimum_notional"


def test_spot_stop_maps_to_ccxt_market_stop_loss_price():
    adapter = adapter_with_filters()
    order = Order(
        id="",
        client_order_id="lta-ps-1",
        symbol=btc_symbol(),
        side=OrderSide.SELL,
        type=OrderType.STOP,
        size=Decimal("0.01"),
        stop_price=Decimal("90"),
    )
    assert adapter._ccxt_order_type(order) == "market"
    assert adapter._order_to_ccxt_params(order) == {
        "stopLossPrice": 90.0,
        "clientOrderId": "lta-ps-1",
    }


def test_ccxt_stop_loss_response_is_parsed_as_protective_stop():
    parsed = adapter_with_filters()._parse_order(
        {
            "id": "stop-1",
            "clientOrderId": "lta-ps-1",
            "status": "open",
            "symbol": "BTC/USDT",
            "side": "sell",
            "type": "stop_loss",
            "amount": 0.01,
            "filled": 0,
            "average": None,
            "price": None,
            "stopPrice": 90,
            "fee": None,
            "timeInForce": None,
            "timestamp": None,
            "lastTradeTimestamp": None,
        },
        btc_symbol(),
    )
    assert parsed.type == OrderType.STOP
    assert parsed.stop_price == Decimal("90")


def test_unknown_order_status_and_all_fill_evidence_are_preserved():
    parsed = adapter_with_filters()._parse_order(
        {
            "id": "order-1",
            "clientOrderId": "client-1",
            "status": "pending_new_variant",
            "symbol": "BTC/USDT",
            "side": "buy",
            "type": "market",
            "amount": 0.1,
            "filled": 0.04,
            "average": 100,
            "cost": 4,
            "price": None,
            "stopPrice": None,
            "fees": [
                {"cost": 0.004, "currency": "USDT"},
                {"cost": 0.0001, "currency": "BNB"},
            ],
            "trades": [{"id": "trade-1"}, {"id": "trade-2"}],
            "timeInForce": None,
            "timestamp": None,
            "lastTradeTimestamp": None,
        },
        btc_symbol(),
    )
    assert parsed.status == OrderStatus.UNKNOWN
    assert parsed.raw_status == "pending_new_variant"
    assert parsed.quote_cost == Decimal("4")
    assert parsed.fee_breakdown == {
        "USDT": Decimal("0.004"),
        "BNB": Decimal("0.0001"),
    }
    assert parsed.trade_ids == ("trade-1", "trade-2")


def test_client_order_lookup_falls_back_to_trade_history():
    class NoopRateLimiter:
        async def acquire(self, exchange_id, weight=1):
            return None

    class HistoryExchange:
        def fetch_order(self, order_id, symbol, params):
            raise ccxt.OrderNotFound("not found")

        def fetch_open_orders(self, symbol, since, limit):
            return []

        def fetch_closed_orders(self, symbol, since, limit):
            return []

        def fetch_my_trades(self, symbol, since, limit):
            return [
                {
                    "id": "trade-1",
                    "order": "exchange-1",
                    "timestamp": 1_786_339_200_000,
                    "symbol": symbol,
                    "side": "buy",
                    "amount": 0.04,
                    "price": 100,
                    "cost": 4,
                    "fee": {"cost": 0.0001, "currency": "BNB"},
                    "info": {"clientOrderId": "client-1"},
                }
            ]

    adapter = object.__new__(CCXTAdapter)
    adapter.exchange = HistoryExchange()
    adapter.config = SimpleNamespace(id="binance")
    adapter._rate_limiter = NoopRateLimiter()
    adapter._reverse_symbol_map = {}
    parsed = asyncio.run(adapter.fetch_order_by_client_id("client-1", btc_symbol()))
    assert parsed is not None
    assert parsed.status == OrderStatus.UNKNOWN
    assert parsed.raw_status == "trade_history_only"
    assert parsed.exchange_order_id is None
    assert parsed.id == "exchange-1"
    assert parsed.filled_size == Decimal("0.04")
    assert parsed.quote_cost == Decimal("4")
    assert parsed.trade_ids == ("trade-1",)
    assert parsed.fee_breakdown == {"BNB": Decimal("0.0001")}


def test_client_order_lookup_falls_back_to_closed_order_history():
    class NoopRateLimiter:
        async def acquire(self, exchange_id, weight=1):
            return None

    class HistoryExchange:
        def fetch_order(self, order_id, symbol, params):
            raise ccxt.OrderNotFound("not found")

        def fetch_open_orders(self, symbol, since, limit):
            return []

        def fetch_closed_orders(self, symbol, since, limit):
            return [
                {
                    "id": "exchange-1",
                    "clientOrderId": "client-1",
                    "status": "closed",
                    "symbol": symbol,
                    "side": "buy",
                    "type": "market",
                    "amount": 0.1,
                    "filled": 0.1,
                    "average": 100,
                    "cost": 10,
                    "price": None,
                    "fee": {"cost": 0.01, "currency": "USDT"},
                    "timeInForce": None,
                    "timestamp": None,
                    "lastTradeTimestamp": None,
                }
            ]

        def fetch_my_trades(self, symbol, since, limit):
            raise ccxt.NotSupported("trade history unavailable")

    adapter = object.__new__(CCXTAdapter)
    adapter.exchange = HistoryExchange()
    adapter.config = SimpleNamespace(id="binance")
    adapter._rate_limiter = NoopRateLimiter()
    adapter._reverse_symbol_map = {}
    parsed = asyncio.run(adapter.fetch_order_by_client_id("client-1", btc_symbol()))
    assert parsed is not None
    assert parsed.status == OrderStatus.FILLED
    assert parsed.raw_status == "closed"
    assert parsed.quote_cost == Decimal("10")
    assert parsed.fee_breakdown == {"USDT": Decimal("0.01")}
