"""The only place perp orders leave the process: Binance USD-M on Demo Trading.

Every write goes through ``CCXTAdapter`` with the internal client key, so a
crash between reserve and acknowledge can be reconciled by that key. Before
any private call the adapter must point at demo-fapi only; mainnet and the old
futures testnet are refused.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    Order,
    OrderSide,
    OrderType,
    Symbol,
)

DEMO_HOST = "https://demo-fapi.binance.com/"
EXCHANGE_ID = "binanceusdm"


class PerpVenueError(RuntimeError):
    pass


def _run(coro):
    return asyncio.run(coro)


@dataclass(frozen=True)
class PerpPosition:
    symbol: str          # exchange id, e.g. "BTCUSDT"
    qty: float           # signed: short is negative
    entry_price: float
    mark_price: float


def load_demo_credentials(env: Mapping[str, str]) -> tuple[str, str]:
    """API key and signing secret of the demo account.

    The secret is either an HMAC secret (``BINANCE_DEMO_FUTURES_API_SECRET``,
    a system-generated key) or the path of a self-generated Ed25519 or RSA
    private key in PEM (``BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE``). The file
    must be readable by its owner only. ccxt signs with whichever it gets.
    """

    key = env.get("BINANCE_DEMO_FUTURES_API_KEY", "").strip()
    secret = env.get("BINANCE_DEMO_FUTURES_API_SECRET", "").strip()
    path = env.get("BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE", "").strip()
    if secret and path:
        raise PerpVenueError("set BINANCE_DEMO_FUTURES_API_SECRET or _PRIVATE_KEY_FILE, not both")
    if path:
        pem = Path(path).expanduser()
        if not pem.is_file():
            raise PerpVenueError("BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE is not a file")
        if pem.stat().st_mode & 0o077:
            raise PerpVenueError("the demo private key file must be readable by its owner only (chmod 600)")
        secret = pem.read_text(encoding="ascii").strip()
        if "PRIVATE KEY-----" not in secret or "ENCRYPTED" in secret:
            raise PerpVenueError("the demo private key file is not an unencrypted PEM private key")
    if not key or not secret:
        raise PerpVenueError("demo API key and a secret or private key file are required")
    return key, secret


def demo_config(api_key: str, secret: str) -> ExchangeConfig:
    return ExchangeConfig(
        id=EXCHANGE_ID,
        name="Binance USD-M Demo Trading",
        api_key=api_key,
        secret=secret,
        demo=True,
        options={"defaultType": "future"},
        markets=[MarketType.PERPETUAL],
    )


class PerpDemoVenue:
    """Sync surface over a connected ``binanceusdm`` adapter in demo mode."""

    def __init__(self, adapter: CCXTAdapter):
        if adapter.config.id != EXCHANGE_ID or not adapter.config.demo:
            raise PerpVenueError("perp venue requires the binanceusdm adapter in demo mode")
        self.adapter = adapter

    @property
    def _ex(self) -> Any:
        if self.adapter.exchange is None:
            raise PerpVenueError("perp venue is not connected")
        return self.adapter.exchange

    def connect(self) -> None:
        _run(self.adapter.connect())
        self.assert_demo_only()

    def assert_demo_only(self) -> None:
        """Every futures API root must be demo-fapi before anything is signed."""

        api = self._ex.urls.get("api") or {}
        roots = {k: v for k, v in api.items() if str(k).startswith("fapi")}
        if not roots or any(not str(v).startswith(DEMO_HOST) for v in roots.values()):
            raise PerpVenueError("futures API is not Binance Demo Trading; refusing")

    # ── symbols ────────────────────────────────────────────────────────────

    def trading_symbols(self) -> set[str]:
        out = set()
        for m in self._ex.markets.values():
            if m.get("swap") and m.get("linear") and m.get("active", True) and m.get("quote") == "USDT":
                out.add(str(m["id"]))
        return out

    def symbol(self, exchange_id: str) -> Symbol:
        markets = self._ex.markets_by_id.get(exchange_id) or []
        market = next((m for m in markets if m.get("swap") and m.get("linear")), None)
        if market is None:
            raise PerpVenueError(f"no USD-M perpetual market for {exchange_id}")
        return Symbol(
            base=market["base"],
            quote=market["quote"],
            asset_class=AssetClass.CRYPTO,
            market_type=MarketType.PERPETUAL,
            exchange=EXCHANGE_ID,
        )

    # ── account ────────────────────────────────────────────────────────────

    def require_one_way_mode(self) -> None:
        mode = self._ex.fetch_position_mode()
        if mode.get("hedged"):
            raise PerpVenueError("account is in hedge mode; the book needs one-way mode")

    def prepare(self, exchange_id: str) -> None:
        """Cross margin at 1x for a symbol before its first order."""

        sym = self.adapter.get_exchange_symbol(self.symbol(exchange_id))
        try:
            self._ex.set_margin_mode("cross", sym)
        except Exception as exc:  # -4046: already cross
            if "-4046" not in str(exc) and "No need to change" not in str(exc):
                raise
        self._ex.set_leverage(1, sym)

    def equity_usdt(self) -> float:
        bal = self._ex.fetch_balance()
        info = bal.get("info") or {}
        if "totalMarginBalance" in info:
            return float(info["totalMarginBalance"])
        return float((bal.get("USDT") or {}).get("total") or 0.0)

    def positions(self) -> dict[str, PerpPosition]:
        out = {}
        for p in _run(self.adapter.fetch_positions()):
            ex_id = self._ex.market(self.adapter.get_exchange_symbol(p.symbol))["id"]
            out[ex_id] = PerpPosition(ex_id, float(p.size), float(p.entry_price), float(p.mark_price))
        return out

    def mark_price(self, exchange_id: str) -> float:
        t = _run(self.adapter.fetch_ticker(self.symbol(exchange_id)))
        if t.last is None or float(t.last) <= 0:
            raise PerpVenueError(f"no last price for {exchange_id}")
        return float(t.last)

    def normalize(self, exchange_id: str, qty: float, price: float) -> float:
        return float(
            self.adapter.normalize_order_amount(
                self.symbol(exchange_id), Decimal(str(qty)), reference_price=Decimal(str(price))
            )
        )

    def normalize_nearest(self, exchange_id: str, qty: float, price: float) -> float:
        """Like ``normalize`` but to the nearest amount step, not down: a
        hedge truncated to BTC's 0.0001 step left a $100 pair 6% short."""

        step = (self._ex.market(self.adapter.get_exchange_symbol(self.symbol(exchange_id))).get("precision") or {}).get("amount")
        if step:
            qty = max(round(qty / float(step)), 1) * float(step) * (1 + 1e-9)
        return self.normalize(exchange_id, qty, price)

    # ── writes ─────────────────────────────────────────────────────────────

    def market(self, exchange_id: str, side: str, qty: float, *, key: str, reduce_only: bool) -> dict:
        order = Order(
            id=key,
            symbol=self.symbol(exchange_id),
            side=OrderSide(side),
            type=OrderType.MARKET,
            size=Decimal(str(qty)),
            reduce_only=reduce_only,
            client_order_id=key,
        )
        return _result(_run(self.adapter.create_order(order)))

    def stop(self, exchange_id: str, side: str, qty: float, stop_price: float, *, key: str) -> dict:
        """Exchange-side STOP_MARKET, reduce-only (a Binance algo order)."""

        sym = self.symbol(exchange_id)
        px = float(self._ex.price_to_precision(self.adapter.get_exchange_symbol(sym), stop_price))
        order = Order(
            id=key,
            symbol=sym,
            side=OrderSide(side),
            type=OrderType.STOP,
            size=Decimal(str(qty)),
            stop_price=Decimal(str(px)),
            reduce_only=True,
            client_order_id=key,
        )
        return _result(_run(self.adapter.create_order(order)))

    def cancel_stop(self, exchange_id: str, *, key: str) -> bool:
        return bool(
            _run(self.adapter.cancel_order(None, self.symbol(exchange_id), trigger=True, client_order_id=key))
        )

    # ── reads for reconciliation ───────────────────────────────────────────

    def order_by_key(self, exchange_id: str, key: str) -> dict | None:
        found = _run(self.adapter.fetch_order_by_client_id(key, self.symbol(exchange_id)))
        return _result(found) if found is not None else None

    def open_stop_keys(self, exchange_id: str) -> set[str]:
        orders = _run(self.adapter.fetch_open_orders(self.symbol(exchange_id), trigger=True))
        return {str(o.client_order_id) for o in orders if o.client_order_id}


def _result(order: Order) -> dict:
    return {
        "client_order_id": order.client_order_id,
        "exchange_order_id": order.id,
        "status": order.status.value,
        "exchange_status": order.raw_status,
        "qty": float(order.size),
        "filled_qty": float(order.filled_size),
        "avg_fill_price": float(order.avg_fill_price),
        "quote_cost": float(order.quote_cost),
        "fees": {k: float(v) for k, v in order.fee_breakdown.items()},
    }
