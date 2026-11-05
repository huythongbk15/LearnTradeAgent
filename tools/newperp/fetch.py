"""Public USDT-M futures market data of the NL-P paper log: REST, no key, no private endpoint.

One host, a short list of paths, a short list of parameters each, no redirect, no proxy, no credential. A masked symbol is refused before a byte leaves. Retry and stop rules come from tools.selection.fetch; a test gives its own transport and never reaches the network.

The four /futures/data series (open interest history, top trader position ratio, global account ratio, taker buy/sell volume) take a symbol and a period from PERIODS, at most 500 rows a call. Binance keeps about the last 30 days of them.
"""
from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from urllib.parse import urlencode, urlsplit

from tools.selection import config as sel
from tools.selection import fetch as sf

HOST = "fapi.binance.com"
ORIGIN = f"https://{HOST}"
EXCHANGE_INFO = "/fapi/v1/exchangeInfo"
KLINES = "/fapi/v1/klines"
DEPTH = "/fapi/v1/depth"
FUNDING = "/fapi/v1/fundingRate"
OPEN_INTEREST_HIST = "/futures/data/openInterestHist"
TOP_POSITION_RATIO = "/futures/data/topLongShortPositionRatio"
GLOBAL_ACCOUNT_RATIO = "/futures/data/globalLongShortAccountRatio"
TAKER_RATIO = "/futures/data/takerlongshortRatio"
STATS = (OPEN_INTEREST_HIST, TOP_POSITION_RATIO, GLOBAL_ACCOUNT_RATIO, TAKER_RATIO)
STAT_LIMIT = 500
PERIODS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
           "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000}
ALLOWED = {
    EXCHANGE_INFO: frozenset(),
    KLINES: frozenset({"symbol", "interval", "startTime", "endTime", "limit"}),
    DEPTH: frozenset({"symbol", "limit"}),
    FUNDING: frozenset({"symbol", "startTime", "endTime", "limit"}),
    **{path: frozenset({"symbol", "period", "limit", "startTime", "endTime"}) for path in STATS},
}


def check_url(url: str) -> None:
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != HOST or p.path not in ALLOWED or p.fragment:
        raise ValueError(f"not an allowed public endpoint: {p.netloc}{p.path}")


def transport(url: str, timeout: float) -> tuple[int, bytes]:
    """One GET, as the selection transport: no proxy, no header of ours, no cookie, a redirect comes back as its status."""
    check_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), sf._NoRedirect)
    try:
        with opener.open(url, timeout=timeout) as r:
            body = r.read(sf.MAX_BYTES)
            sent = r.headers.get("Content-Length")
            if sent is not None and len(body) != int(sent):
                raise http.client.IncompleteRead(body, int(sent) - len(body))
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read(sf.MAX_BYTES)


class Client(sf.Client):
    """Paced GETs on the futures host. Same retry and stop rules as the selection run."""

    def __init__(self, transport_=None, **kw):
        super().__init__(transport_ or transport, **kw)

    def url(self, path: str, params: dict) -> str:
        if path not in ALLOWED or set(params) - ALLOWED[path]:
            raise ValueError(f"not an allowed call: {path} {sorted(params)}")
        if "symbol" in params:
            s = params["symbol"]
            if s != "BTCUSDT":
                sel.assert_open(s)
            if s != s.upper() or not s.endswith("USDT") or not s.isascii() or not s.isalnum():
                raise ValueError(f"not a USDT perp symbol: {s}")
        if path in STATS:
            if "symbol" not in params or params.get("period") not in PERIODS:
                raise ValueError(f"a {path} call needs a symbol and a period in {sorted(PERIODS)}")
            if not 0 < int(params.get("limit", STAT_LIMIT)) <= STAT_LIMIT:
                raise ValueError(f"limit outside (0, {STAT_LIMIT}]")
        url = ORIGIN + path + (f"?{urlencode(params)}" if params else "")
        check_url(url)
        return url


def klines(client: Client, symbol: str, interval: str, start_ms: int, end_ms: int) -> list:
    """Bars opening in [start_ms, end_ms), oldest first, paged."""
    step = {"1m": 60_000, "1h": 3_600_000}[interval]
    out: list = []
    while start_ms < end_ms:
        rows = client.get(KLINES, {"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": end_ms - 1, "limit": 1500})
        rows = [r for r in rows if int(r[0]) < end_ms]
        out += rows
        if len(rows) < 1500:
            break
        start_ms = int(rows[-1][0]) + step
    return out


def funding(client: Client, symbol: str, start_ms: int, end_ms: int) -> list[tuple[int, float]]:
    rows = client.get(FUNDING, {"symbol": symbol, "startTime": start_ms, "endTime": end_ms - 1, "limit": 1000})
    return [(int(r["fundingTime"]), float(r["fundingRate"])) for r in rows]


def stats(client: Client, path: str, symbol: str, period: str, start_ms: int, end_ms: int) -> list[dict]:
    """Rows of one /futures/data series stamped in [start_ms, end_ms), oldest first, one row a stamp.

    Asked in windows of at most 500 periods, so each answer is whole whichever
    end of a window the exchange fills from. Rows older than about 30 days are
    not served.
    """
    if path not in STATS:
        raise ValueError(f"not a /futures/data series: {path}")
    step = PERIODS[period]
    out: dict[int, dict] = {}
    while start_ms < end_ms:
        stop = min(end_ms, start_ms + STAT_LIMIT * step)
        for r in client.get(path, {"symbol": symbol, "period": period, "startTime": start_ms, "endTime": stop - 1, "limit": STAT_LIMIT}):
            t = int(r["timestamp"])
            if start_ms <= t < stop:
                out[t] = r
        start_ms = stop
    return [out[t] for t in sorted(out)]


def dumps(x) -> str:
    return json.dumps(x, sort_keys=True)
