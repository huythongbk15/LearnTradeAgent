"""Book `bybit` of the NL-P paper log: new Bybit USDT perps whose coin has no Binance USD-M perp at the stamp.

Public Bybit v5 market data only: one host, five paths, a short list of parameters, no key, no redirect, no proxy.
A masked symbol is refused before a byte leaves. Rows come back in the Binance kline layout
[open_ms, open, high, low, close, volume, close_ms, turnover] so the NL-P rules read both venues alike.
Basis: H-NLP-BYBIT-13 (data/bybit_nlp_20261104_01, CONSISTENT, point-in-time book +178.4 bps a week, t 3.05).
"""
from __future__ import annotations

import http.client
import re
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from tools.newperp import config as cf
from tools.selection import config as sel
from tools.selection import fetch as sf

HOST = "api.bybit.com"
ORIGIN = f"https://{HOST}"
INSTRUMENTS = "/v5/market/instruments-info"
KLINE = "/v5/market/kline"
MARK_KLINE = "/v5/market/mark-price-kline"
ORDERBOOK = "/v5/market/orderbook"
FUNDING = "/v5/market/funding/history"
ALLOWED = {
    INSTRUMENTS: frozenset({"category", "status", "limit", "cursor"}),
    KLINE: frozenset({"category", "symbol", "interval", "start", "end", "limit"}),
    MARK_KLINE: frozenset({"category", "symbol", "interval", "start", "end", "limit"}),
    ORDERBOOK: frozenset({"category", "symbol", "limit"}),
    FUNDING: frozenset({"category", "symbol", "startTime", "endTime", "limit"}),
}
INTERVAL_MS = {"1": 60_000, "60": 3_600_000}
CRYPTO_TYPES = ("", "innovation")          # Bybit symbolType; stock, ETF, commodity and forex are out
NOT_CRYPTO = frozenset({                   # stablecoins, wrappers, ratio perps, pre-IPO names, metals
    "USDC", "USDE", "FDUSD", "BUSD", "TUSD", "DAI", "USD1", "RLUSD", "UST", "USTC", "PYUSD", "USDD", "USDP", "EURC", "EURI",
    "ETHBTC", "PUMPBTC", "WBTC", "CBBTC", "STETH", "WETH", "WSTETH", "BTCDOM",
    "ANTHROPIC", "OPENAI", "SPACEX", "XAI", "STRIPE", "DATABRICKS", "PERPLEXITY",
    "XAUT", "PAXG", "GOLD", "SILVER", "XAU", "XAG", "OIL", "WTI", "BRENT", "NATGAS", "COPPER"})
RENAMES = {                                # one coin under two tickers: a Bybit base counts as on Binance if any alias is
    "POL": {"MATIC"}, "MATIC": {"POL"}, "RENDER": {"RNDR"}, "RNDR": {"RENDER"}, "SKY": {"MKR"}, "MKR": {"SKY"},
    "A": {"EOS"}, "EOS": {"A"}, "KAIA": {"KLAY"}, "KLAY": {"KAIA"}, "BEAM": {"BEAMX"}, "BEAMX": {"BEAM"},
    "RAYDIUM": {"RAY"}, "RAY": {"RAYDIUM"}, "PUMPFUN": {"PUMP"}, "PUMP": {"PUMPFUN"}, "NEIROCTO": {"NEIRO"}, "NEIRO": {"NEIROCTO"}}
BTC = "BTCUSDT"
LAUNCH_SLACK_DAYS = 30                     # a contract may open for trading weeks after its launchTime (pre-market)


def norm(base: str) -> str:
    """Coin name without a rebased unit: 1000PEPE, 10000LADYS, 1MBABYDOGE, SHIB1000 -> PEPE, LADYS, BABYDOGE, SHIB."""
    b = re.sub(r"[^0-9A-Z]", "", base.upper())
    b = re.sub(r"^(?:10{2,}|1M)(?=[A-Z])", "", b)
    return re.sub(r"(?<=[A-Z])10{2,}$", "", b)


def check_url(url: str) -> None:
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != HOST or p.path not in ALLOWED or p.fragment:
        raise ValueError(f"not an allowed public endpoint: {p.netloc}{p.path}")


def transport(url: str, timeout: float) -> tuple[int, bytes]:
    """One GET: no proxy, no cookie, a redirect comes back as its status."""
    check_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), sf._NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, headers={"User-Agent": "research-client/1.0"}), timeout=timeout) as r:
            body = r.read(sf.MAX_BYTES)
            sent = r.headers.get("Content-Length")
            if sent is not None and len(body) != int(sent):
                raise http.client.IncompleteRead(body, int(sent) - len(body))
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read(sf.MAX_BYTES)


RETRY_CODES = (10006, 10018, 10000, 10016)  # Bybit: too many visits, server timeout, service error
RATE_WAITS = (2.0, 4.0, 8.0, 16.0)
PACE_SECONDS = 0.25


class VenueError(sf.FetchError):
    """Bybit as a whole is not answering: stop calling it this run."""


class CallError(sf.FetchError):
    """Bybit refused this one call (for example a symbol it no longer knows): other calls may still work."""


class Client(sf.Client):
    """Paced GETs on the Bybit host, with the selection run's retry rules. A rate-limit or transient server code is waited out a few
    times. Errors cost the Bybit book only, never the Binance books: VenueError ends Bybit calls for the run, CallError one leg."""

    def __init__(self, transport_=None, **kw):
        kw.setdefault("pace", PACE_SECONDS)
        super().__init__(transport_ or transport, **kw)

    def url(self, path: str, params: dict) -> str:
        if path not in ALLOWED or set(params) - ALLOWED[path]:
            raise ValueError(f"not an allowed call: {path} {sorted(params)}")
        if "symbol" in params:
            s = params["symbol"]
            if s != BTC:
                sel.assert_open(s)
            if s != s.upper() or not s.endswith("USDT") or not s.isascii() or not s.isalnum():
                raise ValueError(f"not a USDT perp symbol: {s}")
        url = ORIGIN + path + (f"?{urlencode(params)}" if params else "")
        check_url(url)
        return url

    def result(self, path: str, params: dict) -> dict:
        for wait in RATE_WAITS + (None,):
            try:
                body = self.get(path, params)
            except sf.FetchError as e:             # HTTP 418 or 429, or transport and 5xx retries used up: this venue only
                raise VenueError(f"bybit {e}") from e
            code = body.get("retCode") if isinstance(body, dict) else None
            if code in RETRY_CODES and wait is not None:
                self.sleep(wait)
                continue
            if code in RETRY_CODES:
                raise VenueError(f"bybit retCode {code} did not clear")
            if code != 0:
                raise CallError(f"bybit retCode {code}")
            return body.get("result") or {}
        raise VenueError("bybit did not answer")


def instruments(client: Client) -> list[dict]:
    """Trading and pre-market (PreLaunch) linear contracts: the research counted a contract from its first traded bar, pre-market included."""
    out = []
    for status in ("Trading", "PreLaunch"):
        cursor = ""
        while True:
            p = {"category": "linear", "status": status, "limit": 1000}
            if cursor:
                p["cursor"] = cursor
            r = client.result(INSTRUMENTS, p)
            out += r.get("list") or []
            cursor = r.get("nextPageCursor") or ""
            if not cursor:
                break
    return out


def trading(x: dict) -> bool:
    return x.get("status") == "Trading" or (x.get("status") == "PreLaunch" and (x.get("preListingInfo") or {}).get("curAuctionPhase") == "ContinuousTrading")


def _klines(client: Client, path: str, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Bars opening in [start_ms, end_ms), oldest first, in the Binance layout. Bybit serves newest first, at most 1,000 a page."""
    step = INTERVAL_MS[interval]
    rows: dict[int, list] = {}
    end = end_ms - 1
    while end >= start_ms:
        page = client.result(path, {"category": "linear", "symbol": symbol, "interval": interval, "start": start_ms, "end": end, "limit": 1000}).get("list") or []
        for r in page:
            t = int(r[0])
            if start_ms <= t < end_ms:
                vol, turnover = (r[5], r[6]) if len(r) > 5 else ("0", "0")     # mark-price bars carry no volume
                rows[t] = [t, r[1], r[2], r[3], r[4], vol, t + step - 1, turnover]
        if not page:
            break
        oldest = min(int(r[0]) for r in page)
        if oldest <= start_ms:
            break
        end = oldest - 1
    return [rows[t] for t in sorted(rows)]


def klines(client: Client, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    return _klines(client, KLINE, symbol, interval, start_ms, end_ms)


def mark_klines(client: Client, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    return _klines(client, MARK_KLINE, symbol, interval, start_ms, end_ms)


def funding(client: Client, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    out: dict[int, float] = {}
    end = end_ms - 1
    while end >= start_ms:
        page = client.result(FUNDING, {"category": "linear", "symbol": symbol, "startTime": start_ms, "endTime": end, "limit": 200}).get("list") or []
        for r in page:
            t = int(r["fundingRateTimestamp"])
            if start_ms <= t < end_ms:
                out[t] = float(r["fundingRate"])
        if len(page) < 200:
            break
        end = min(int(r["fundingRateTimestamp"]) for r in page) - 1
    return sorted(out.items())


def depth(client: Client, symbol: str) -> dict:
    """Order book in the Binance layout for rules.book_cost."""
    r = client.result(ORDERBOOK, {"category": "linear", "symbol": symbol, "limit": 200})
    return {"bids": r.get("b") or [], "asks": r.get("a") or []}


def binance_bases(info: dict, stamp_ms: int) -> set[str]:
    """Coins with a Binance USD-M perp at the stamp, any quote, from today's exchangeInfo (a perp delisted before the stamp is not there)."""
    out = set()
    for s in info.get("symbols", []):
        if s.get("contractType") == "PERPETUAL" and int(s.get("onboardDate") or 0) <= stamp_ms:      # as the research; TradFi tickers are not coins
            out.add(norm(s.get("baseAsset") or ""))
    return out


def candidates(instr: list[dict], stamp_ms: int, on_binance: set[str]) -> list[dict]:
    """Bybit USDT perps that could be new at the stamp: crypto, open, not masked, no Binance perp at the stamp under any alias.
    Listing time is fixed later from the first traded 1h bar."""
    out = []
    lo = stamp_ms - (cf.NEW_DAYS + LAUNCH_SLACK_DAYS) * 86_400_000
    for x in instr:
        if x.get("quoteCoin") != "USDT" or x.get("contractType") != "LinearPerpetual" or not trading(x):
            continue
        if x.get("symbolType", "") not in CRYPTO_TYPES:
            continue
        base = norm(x.get("baseCoin") or "")
        if not base or base in NOT_CRYPTO or sel.is_sealed(f"{base}USDT") or sel.is_sealed(x["symbol"]):
            continue
        if base in on_binance or RENAMES.get(base, set()) & on_binance:
            continue
        launch = int(x.get("launchTime") or 0)
        if lo <= launch < stamp_ms:
            out.append({"symbol": x["symbol"], "base": base, "launch_ms": launch, "symbol_type": x.get("symbolType", "")})
    return sorted(out, key=lambda c: (c["launch_ms"], c["symbol"]))


def listing_ms(rows: list) -> int | None:
    """First 1h bar with turnover, the NL-P listing time on Bybit."""
    for r in rows:
        if float(r[7]) > 0:
            return int(r[0])
    return None


def stop_path(m1: list, minute_ms: int, before: int = 2, after: int = 10) -> list:
    """The 1m bars around a stop trigger: open, high, low, close, turnover."""
    return [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])]
            for r in m1 if minute_ms - before * 60_000 <= int(r[0]) <= minute_ms + after * 60_000]


def mark_trigger(mark_1m: list, entry: float, stop: float = cf.STOP) -> int | None:
    """First minute whose mark-price high reaches the stop: when a mark-price stop would fire."""
    for r in mark_1m:
        if float(r[2]) >= stop * entry:
            return int(r[0])
    return None
