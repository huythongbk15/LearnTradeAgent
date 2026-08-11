from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

import pytest

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
from trading_agent.exchanges.models import AssetClass, MarketType, Symbol


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
    with pytest.raises(Exception, match="notional is below"):
        adapter_with_filters().normalize_order_amount(
            btc_symbol(),
            Decimal("0.001"),
            reference_price=Decimal("100"),
        )
