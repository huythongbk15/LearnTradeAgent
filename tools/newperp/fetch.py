"""Public USDT-M futures market data of the NL-P paper log: REST, no key, no private endpoint.

One host, four paths, a short list of parameters, no redirect, no proxy, no credential. A masked symbol is refused before a byte leaves. Retry and stop rules come from tools.selection.fetch; a test gives its own transport and never reaches the network.
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
ALLOWED = {
    EXCHANGE_INFO: frozenset(),
    KLINES: frozenset({"symbol", "interval", "startTime", "endTime", "limit"}),
    DEPTH: frozenset({"symbol", "limit"}),
    FUNDING: frozenset({"symbol", "startTime", "endTime", "limit"}),
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


def dumps(x) -> str:
    return json.dumps(x, sort_keys=True)
