"""The only place perp orders leave the process: Binance USD-M.

Every write goes through ``CCXTAdapter`` with the internal client key, so a
crash between reserve and acknowledge can be reconciled by that key.

``PerpDemoVenue`` points at Demo Trading only: before any private call the
adapter must point at demo-fapi; mainnet and the old futures testnet are
refused. ``PerpLiveVenue`` is mainnet, for perp tier R0 only
(docs/operations/PERP_LIVE_R0.md): every request must go to fapi.binance.com,
checked before the first one leaves the process, and it refuses every write
unless it was built to trade.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
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
MAINNET_HOST = "https://fapi.binance.com/"
EXCHANGE_ID = "binanceusdm"
# ccxt's options that, with a key, make load_markets sign requests on the spot API (api.binance.com/sapi):
# the currency list and the margin pairs. The live venue needs neither and must stay on fapi.binance.com.
LIVE_OFF_OPTIONS = ("fetchCurrencies", "fetchMargins")
# ccxt's proxy settings on an exchange object (applied after the live venue's host check): none may be set.
PROXY_ATTRS = ("proxy", "proxyUrl", "proxy_url", "proxyUrlCallback", "proxy_url_callback", "httpProxy",
               "http_proxy", "httpProxyCallback", "http_proxy_callback", "httpsProxy", "https_proxy",
               "httpsProxyCallback", "https_proxy_callback", "socksProxy", "socks_proxy", "socksProxyCallback", "aiohttpProxy",
               "aiohttp_proxy", "socks_proxy_callback")
# Binance codes that leave an order's outcome unknown although the HTTP status is 4xx (the futures API docs:
# "execution status unknown"); a write answered with one of them is never read as refused.
UNKNOWN_OUTCOME_CODES = frozenset({-1000, -1001, -1006, -1007})
TRADE_WINDOW_MS = 7 * 24 * 3_600_000    # Binance's longest span for one read of the account's trades
TRADE_PAGE = 1000                       # ... and its most trades in one answer


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


def load_live_credentials(env: Mapping[str, str]) -> tuple[str, str]:
    """API key and Ed25519 private key of the R0 sub-account (D9 and D10 of PERP_LIVE_R0.md).

    Only a self-generated Ed25519 key is accepted: ``BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE`` names a PEM file
    that is a regular file (not a link), owned by the user running this process and readable by that user only.
    An HMAC secret is refused. No message carries the key or the path's contents.
    """

    key = env.get("BINANCE_LIVE_FUTURES_API_KEY", "").strip()
    path = env.get("BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE", "").strip()
    if env.get("BINANCE_LIVE_FUTURES_API_SECRET", "").strip():
        raise PerpVenueError("the live venue takes an Ed25519 key file only; remove BINANCE_LIVE_FUTURES_API_SECRET")
    if not key or not path:
        raise PerpVenueError("BINANCE_LIVE_FUTURES_API_KEY and BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE are required")
    # One open, without following a link, then every check on that handle: nothing can be swapped in between.
    try:
        fd = os.open(Path(path).expanduser(), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise PerpVenueError("BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE cannot be opened as a regular file") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise PerpVenueError("BINANCE_LIVE_FUTURES_PRIVATE_KEY_FILE is not a regular file")
        if st.st_uid != os.getuid():
            raise PerpVenueError("the live private key file must be owned by the user running the live task")
        if st.st_mode & 0o077:
            raise PerpVenueError("the live private key file must be readable by its owner only (chmod 600)")
        raw = os.read(fd, 16_384)
    finally:
        os.close(fd)
    try:
        text = raw.decode("ascii").strip()
    except UnicodeDecodeError:
        raise PerpVenueError("the live private key file is not ASCII PEM") from None
    if "PRIVATE KEY-----" not in text or "ENCRYPTED" in text:
        raise PerpVenueError("the live private key file is not an unencrypted PEM private key")
    from cryptography.exceptions import UnsupportedAlgorithm
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, load_pem_private_key

    try:
        parsed = load_pem_private_key(text.encode("ascii"), password=None)
    except (ValueError, TypeError, UnsupportedAlgorithm):
        raise PerpVenueError("the live private key file does not parse as a private key") from None
    if not isinstance(parsed, Ed25519PrivateKey):
        raise PerpVenueError("the live private key is not Ed25519")
    # The key alone, as standard PKCS8 PEM: ccxt signs with Ed25519 only when the secret is at most 120
    # characters, so a file that also holds the public key or a comment would otherwise be signed as RSA and fail.
    secret = parsed.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii").strip()
    return key, secret


def live_config(api_key: str, secret: str) -> ExchangeConfig:
    return ExchangeConfig(
        id=EXCHANGE_ID,
        name="Binance USD-M mainnet (perp R0)",
        api_key=api_key,
        secret=secret,
        options={"defaultType": "future", **{k: False for k in LIVE_OFF_OPTIONS}},
        markets=[MarketType.PERPETUAL],
    )


class PerpVenue:
    """Sync surface over a connected ``binanceusdm`` adapter. Use ``PerpDemoVenue`` or ``PerpLiveVenue``."""

    def __init__(self, adapter: CCXTAdapter):
        self.adapter = adapter
        self._wire: list[dict] | None = None    # each write request ccxt sent, once ``_watch`` is in place

    @property
    def _ex(self) -> Any:
        if self.adapter.exchange is None:
            raise PerpVenueError("perp venue is not connected")
        return self.adapter.exchange

    def connect(self) -> None:
        raise NotImplementedError

    # ── what a failed write did on the wire ────────────────────────────────

    def _watch(self, ex: Any) -> None:
        """Note every write request (any method but GET) as ccxt hands it to HTTP, and the status and body of its
        answer. ``Book._send`` asks after a send that raised whether a request left at all (``wire_since``).

        Only on a real ccxt exchange: a test double that answers ccxt's calls without HTTP leaves ``_wire`` None,
        and then nothing is known about a failed send (its outcome stays unknown, as before).
        """

        import ccxt

        if not isinstance(ex, ccxt.Exchange):
            return
        wire: list[dict] = []
        send, answered = ex.fetch, ex.on_rest_response

        def fetch(url, method="GET", headers=None, body=None):
            if str(method).upper() != "GET":
                wire.append({"answer": None})
            return send(url, method, headers, body)

        def on_rest_response(code, reason, url, method, response_headers, response_body, request_headers,
                             request_body):
            if str(method).upper() != "GET" and wire:
                wire[-1]["answer"] = (code, response_body)
            return answered(code, reason, url, method, response_headers, response_body, request_headers,
                            request_body)

        ex.fetch, ex.on_rest_response = fetch, on_rest_response
        self._wire = wire

    def wire_mark(self) -> int | None:
        """A mark to pass to ``wire_since`` after a send; None when the venue cannot see its requests."""

        return None if self._wire is None else len(self._wire)

    def wire_since(self, mark: int | None) -> str | None:
        """After a send that raised: what its write requests show.

        - ``"not_sent"``: no write request left the process (a local check refused it, or the live venue's guard);
        - ``"refused:<code>"``: the last one was answered with a definite refusal: HTTP 4xx other than 408, with a
          Binance error code that does not leave the outcome unknown (``UNKNOWN_OUTCOME_CODES``);
        - None: anything else (no answer, a timeout, a 5xx, a body without a code): the outcome is unknown.
        """

        if mark is None or self._wire is None:
            return None
        sent = self._wire[mark:]
        if not sent:
            return "not_sent"
        answer = sent[-1]["answer"]
        if answer is None:
            return None
        status, body = answer
        try:
            code = int(json.loads(body).get("code"))
        except (TypeError, ValueError, AttributeError):
            return None
        if 400 <= int(status) < 500 and int(status) != 408 and code < 0 and code not in UNKNOWN_OUTCOME_CODES:
            return f"refused:{code}"
        return None

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

    def open_order_keys(self, exchange_id: str) -> set[str]:
        """Client keys of the open plain (not conditional) orders on a symbol."""

        orders = _run(self.adapter.fetch_open_orders(self.symbol(exchange_id)))
        return {str(o.client_order_id) for o in orders if o.client_order_id}

    def trade_order_ids(self, exchange_id: str, since_ms: int, until_ms: int) -> list[str]:
        """The venue order id of each of the account's trades on a symbol from ``since_ms`` to ``until_ms``. No
        price or amount: a read for evidence only. Binance answers 7 days and 1000 trades at a time; the span is read
        in 7-day windows, and a full page fails closed rather than paging."""

        sym = self.adapter.get_exchange_symbol(self.symbol(exchange_id))
        ids: list[str] = []
        start = since_ms
        while start <= until_ms:
            end = min(start + TRADE_WINDOW_MS - 1, until_ms)
            rows = self._ex.fetch_my_trades(sym, start, TRADE_PAGE, {"endTime": end})
            if len(rows) >= TRADE_PAGE:
                raise PerpVenueError(f"{exchange_id}: a full page of trades in one window; too many to check")
            ids += [str(r.get("order") or "") for r in rows]
            start = end + 1
        return ids

    def funding_paid(self, exchange_ids: list[str], since_ms: int, until_ms: int) -> float:
        """Funding the account booked on these symbols from ``since_ms`` to ``until_ms``, in USDT; paid is negative."""

        total = 0.0
        for ex_id in exchange_ids:
            sym = self.adapter.get_exchange_symbol(self.symbol(ex_id))
            rows = self._ex.fetch_funding_history(sym, since_ms, 1000, {"endTime": until_ms})
            total += sum(float(r.get("amount") or 0.0) for r in rows)
        return total


class PerpDemoVenue(PerpVenue):
    """Binance USD-M Demo Trading."""

    def __init__(self, adapter: CCXTAdapter):
        if adapter.config.id != EXCHANGE_ID or not adapter.config.demo:
            raise PerpVenueError("perp venue requires the binanceusdm adapter in demo mode")
        super().__init__(adapter)

    def connect(self) -> None:
        _run(self.adapter.connect())
        self.assert_demo_only()
        self._watch(self._ex)

    def assert_demo_only(self) -> None:
        """Every futures API root must be demo-fapi before anything is signed."""

        api = self._ex.urls.get("api") or {}
        roots = {k: v for k, v in api.items() if str(k).startswith("fapi")}
        if not roots or any(not str(v).startswith(DEMO_HOST) for v in roots.values()):
            raise PerpVenueError("futures API is not Binance Demo Trading; refusing")


class PerpLiveVenue(PerpVenue):
    """Binance USD-M mainnet, perp tier R0 only (docs/operations/PERP_LIVE_R0.md).

    - Hosts: before the adapter sends its first request (on mainnet with a key, ``load_markets`` would sign
      requests on the spot API), the venue checks that every futures API root is ``MAINNET_HOST`` and wraps the
      exchange's HTTP call so that a request to any other host is refused before it leaves the process.
    - Writes: built with ``allow_orders=False`` (the default; stage B's read-only soak) it refuses every order,
      cancel and account setting before the network. The read-only key would refuse them too; this does not
      depend on it.
    """

    def __init__(self, adapter: CCXTAdapter, *, allow_orders: bool = False):
        cfg = adapter.config
        if cfg.id != EXCHANGE_ID or cfg.demo or cfg.sandbox or cfg.testnet:
            raise PerpVenueError("live venue requires the binanceusdm adapter on mainnet (no demo, sandbox or testnet)")
        if any(cfg.options.get(k) is not False for k in LIVE_OFF_OPTIONS):
            raise PerpVenueError("live venue requires live_config (no spot API calls when markets load)")
        if adapter.exchange is not None:
            raise PerpVenueError("live venue requires an adapter it connects itself")
        super().__init__(adapter)
        self._allow_orders = bool(allow_orders)

    @property
    def allow_orders(self) -> bool:
        """Fixed when the venue is built."""

        return self._allow_orders

    @property
    def adapter(self) -> CCXTAdapter:
        """The adapter, only while its exchange (if any) carries the host guard: every venue call goes through
        here, so an exchange connected some other way is never used."""

        ex = self._adapter.exchange
        if ex is not None and not getattr(ex.fetch, "perp_live_guard", False):
            raise PerpVenueError("live venue: the host guard is not in place; refusing")
        return self._adapter

    @adapter.setter
    def adapter(self, value: CCXTAdapter) -> None:
        self._adapter = value

    def connect(self) -> None:
        try:
            _run(self._adapter.connect(before_load=self._guard))
        except Exception:
            self._adapter.exchange = None     # an exchange the guard refused is never used
            raise
        self.assert_mainnet_only()

    def _guard(self, ex: Any) -> None:
        """Called by the adapter on the new exchange object, before any request.

        Every request must go to ``MAINNET_HOST``; while orders are not allowed, only GET requests leave (every
        Binance write is a POST, PUT or DELETE), so no write reaches the network whichever method sends it. No
        proxy is allowed (ccxt applies it after this check) and no redirect is followed.
        """

        _check_mainnet_roots(ex)
        if any((getattr(ex, "options", None) or {}).get(k) is not False for k in LIVE_OFF_OPTIONS):
            raise PerpVenueError("live venue: ccxt would sign spot API requests while loading markets; refusing")
        if any(getattr(ex, a, None) for a in PROXY_ATTRS):
            raise PerpVenueError("live venue: a proxy is set on the exchange; refusing")
        session = getattr(ex, "session", None)
        if session is not None and hasattr(session, "max_redirects"):
            session.max_redirects = 0
        self._watch(ex)       # inside the guard: a write the guard refuses never reaches the wire's record
        send = ex.fetch
        venue = self

        def fetch(url, method="GET", headers=None, body=None):
            if not str(url).startswith(MAINNET_HOST):
                raise PerpVenueError("live venue: a request to a host other than fapi.binance.com was refused")
            if str(method).upper() != "GET" and not venue._allow_orders:
                raise PerpVenueError("live venue is read-only: a write request was refused before the network")
            return send(url, method, headers, body)

        fetch.perp_live_guard = True
        ex.fetch = fetch

    def assert_mainnet_only(self) -> None:
        """Every futures API root is fapi.binance.com and the host guard is in place."""

        _check_mainnet_roots(self._ex)

    def _writable(self, what: str) -> None:
        if not self.allow_orders:
            raise PerpVenueError(f"live venue is read-only: {what} refused before the network")

    def require_account_shape(self) -> None:
        """Section 3 of PERP_LIVE_R0.md: one-way position mode, single-asset mode, fees not paid in BNB, a USDT row
        and no other asset in the futures wallet, and cross margin at 1x on every position held. Read-only; stops
        the run if any differs or a reply lacks the field (fail closed)."""

        if self._ex.fetch_position_mode().get("hedged") is not False:
            raise PerpVenueError("account is in hedge mode or its mode is unreadable; R0 needs one-way mode")
        multi = self._ex.fapiPrivateGetMultiAssetsMargin()
        if str((multi or {}).get("multiAssetsMargin")).lower() != "false":
            raise PerpVenueError("account is in multi-assets mode; R0 needs single-asset mode")
        burn = self._ex.fapiPrivateGetFeeBurn()
        if str((burn or {}).get("feeBurn")).lower() != "false":
            raise PerpVenueError("fees are paid in BNB; R0 needs USDT fees (turn BNB fee discount off)")
        assets = ((self._ex.fetch_balance() or {}).get("info") or {}).get("assets")
        if not isinstance(assets, list) or not any(str(a.get("asset")) == "USDT" for a in assets):
            raise PerpVenueError("the futures wallet reply has no USDT row; refusing")
        other = 0
        for a in assets:
            if str(a.get("asset")) == "USDT":
                continue
            try:
                held = float(a["walletBalance"])
            except (KeyError, TypeError, ValueError):
                raise PerpVenueError("a futures wallet row has no readable balance; refusing") from None
            other += held != 0.0
        if other:
            raise PerpVenueError(f"the futures wallet holds {other} asset(s) besides USDT; R0 holds USDT only")
        for p in self._ex.fetch_positions():
            if not float(p.get("contracts") or 0.0):
                continue
            if str(p.get("marginMode")).lower() != "cross" or float(p.get("leverage") or 0.0) != 1.0:
                raise PerpVenueError("a position is not cross margin at 1x; R0 holds cross at 1x only")

    def prepare(self, exchange_id: str) -> None:
        self._writable("margin and leverage settings")
        super().prepare(exchange_id)

    def market(self, exchange_id: str, side: str, qty: float, *, key: str, reduce_only: bool) -> dict:
        self._writable("market order")
        return super().market(exchange_id, side, qty, key=key, reduce_only=reduce_only)

    def stop(self, exchange_id: str, side: str, qty: float, stop_price: float, *, key: str) -> dict:
        self._writable("stop order")
        return super().stop(exchange_id, side, qty, stop_price, key=key)

    def cancel_stop(self, exchange_id: str, *, key: str) -> bool:
        self._writable("stop cancel")
        return super().cancel_stop(exchange_id, key=key)


def _check_mainnet_roots(ex: Any) -> None:
    api = (getattr(ex, "urls", None) or {}).get("api") or {}
    roots = {k: v for k, v in api.items() if str(k).startswith("fapi")}
    if not roots or any(not str(v).startswith(MAINNET_HOST) for v in roots.values()):
        raise PerpVenueError("futures API is not Binance USD-M mainnet; refusing")


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
