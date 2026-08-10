from __future__ import annotations

import asyncio

import pytest

from trading_agent.exchanges.alpaca_adapter import AlpacaAdapter, AlpacaConfig


class FakeTradingClient:
    def __init__(self):
        self.calls: list[bool] = []

    def close_all_positions(self, *, cancel_orders: bool):
        self.calls.append(cancel_orders)
        return [object(), object()]


def test_close_all_is_restricted_to_paper_account():
    adapter = AlpacaAdapter(AlpacaConfig("key", "secret", paper=False))
    adapter._connected = True
    adapter._trading_client = FakeTradingClient()

    with pytest.raises(RuntimeError, match="paper"):
        asyncio.run(adapter.close_all_positions())


def test_close_all_cancels_orders_and_returns_serializable_summary():
    adapter = AlpacaAdapter(AlpacaConfig("key", "secret", paper=True))
    client = FakeTradingClient()
    adapter._connected = True
    adapter._trading_client = client

    result = asyncio.run(adapter.close_all_positions(cancel_orders=True))

    assert client.calls == [True]
    assert result == {"requested": 2, "cancel_orders": True, "account": "paper"}
