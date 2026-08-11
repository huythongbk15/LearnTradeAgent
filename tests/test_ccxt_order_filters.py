from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

import pytest

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderConstraintError,
    OrderSide,
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
