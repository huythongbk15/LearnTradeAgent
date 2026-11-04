"""The only place spot demo orders leave the process: Binance spot on Demo Trading.

Every write goes through ``CCXTAdapter`` with the internal client key, so a
crash between reserve and acknowledge can be reconciled by that key. Before
any private call the adapter must point at demo-api only; mainnet and the spot
testnet are refused. The key is the Demo Trading key the perp book uses: one
demo account, separate spot and futures wallets.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderConstraintError,
    OrderSide,
    OrderType,
    Symbol,
)
from trading_agent.execution.perp.venue import _result

DEMO_SPOT_HOST = "https://demo-api.binance.com/"
EXCHANGE_ID = "binance"


class SpotVenueError(RuntimeError):
    pass


def _run(coro):
    return asyncio.run(coro)


def spot_demo_config(api_key: str, secret: str) -> ExchangeConfig:
    return ExchangeConfig(
        id=EXCHANGE_ID,
        name="Binance Spot Demo Trading",
        api_key=api_key,
        secret=secret,
        demo=True,
        options={"defaultType": "spot"},
        markets=[MarketType.SPOT],
    )


class SpotDemoVenue:
    """Sync surface over a connected ``binance`` spot adapter in demo mode."""

    def __init__(self, adapter: CCXTAdapter):
        if adapter.config.id != EXCHANGE_ID or not adapter.config.demo:
            raise SpotVenueError("spot demo venue requires the binance adapter in demo mode")
        self.adapter = adapter

    @property
    def _ex(self) -> Any:
        if self.adapter.exchange is None:
            raise SpotVenueError("spot demo venue is not connected")
        return self.adapter.exchange

    def connect(self) -> None:
        _run(self.adapter.connect())
        self.assert_demo_only()

    def assert_demo_only(self) -> None:
        """The spot API roots must be demo-api, and no root may be a mainnet host, before anything is signed."""

        api = self._ex.urls.get("api") or {}
        spot = [api.get("public"), api.get("private")]
        if any(not str(v or "").startswith(DEMO_SPOT_HOST) for v in spot):
            raise SpotVenueError("spot API is not Binance Demo Trading; refusing")
        for v in api.values():
            s = str(v)
            if "binance" in s and "://demo-" not in s:
                raise SpotVenueError("a Binance API root outside Demo Trading is configured; refusing")

    @staticmethod
    def symbol(base: str, quote: str = "USDT") -> Symbol:
        return Symbol(base=base, quote=quote, asset_class=AssetClass.CRYPTO, market_type=MarketType.SPOT,
                      exchange=EXCHANGE_ID)

    # ── reads ──────────────────────────────────────────────────────────────

    def price(self, base: str) -> float:
        return float(_run(self.adapter.fetch_ticker(self.symbol(base))).last)

    def balance(self, asset: str) -> tuple[float, float]:
        """Free and total of one asset in the demo spot wallet."""

        bal = self._ex.fetch_balance()
        return float((bal.get("free") or {}).get(asset) or 0.0), float((bal.get("total") or {}).get(asset) or 0.0)

    def normalize(self, base: str, qty: float, price: float) -> float:
        market = self._ex.market(self.adapter.get_exchange_symbol(self.symbol(base)))
        out = float(self._ex.amount_to_precision(market["symbol"], qty))
        limits = market.get("limits") or {}
        if out < float((limits.get("amount") or {}).get("min") or 0.0) or out * price < float(
            (limits.get("cost") or {}).get("min") or 0.0
        ):
            raise OrderConstraintError(f"{base} quantity {qty} is below the venue minimum")
        return out

    def order_by_key(self, base: str, key: str) -> dict | None:
        found = _run(self.adapter.fetch_order_by_client_id(key, self.symbol(base)))
        return _result(found) if found is not None else None

    # ── writes ─────────────────────────────────────────────────────────────

    def market(self, base: str, side: str, qty: float, *, key: str) -> dict:
        order = Order(
            id=key,
            symbol=self.symbol(base),
            side=OrderSide(side),
            type=OrderType.MARKET,
            size=Decimal(str(qty)),
            client_order_id=key,
        )
        return _result(_run(self.adapter.create_order(order)))
