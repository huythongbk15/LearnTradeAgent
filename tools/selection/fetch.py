"""Public market data of the selection run: REST, no key, no private endpoint.

Every call goes through Client.get: one host, two paths, a short list of parameters, no redirect, no proxy, no credential of any kind. A masked symbol is refused before a byte leaves. Calls are paced. A dropped connection, a body cut short or a 5xx is retried; a 418 or 429 stops all fetching at once and nothing more is asked that run. A run's first call waits out an outage for about a quarter of an hour, since a failed Monday is final.

Bars follow the store: only bars whose close time is before the cutoff, oldest page first, so a failed page leaves a shorter history, never a hole. A new coin asks from the start of the grid and pages forward. A known coin asks from its last stored bar, so a coin that left a list and came back fills what it missed.
"""
from __future__ import annotations

import http.client
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import polars as pl

from tools.selection import config, universe
from tools.selection import panel as pn
from tools.selection.config import BTC

HOST = "data-api.binance.vision"
ORIGIN = f"https://{HOST}"
KLINES = "/api/v3/klines"
EXCHANGE_INFO = "/api/v3/exchangeInfo"
ALLOWED = {KLINES: frozenset({"symbol", "interval", "startTime", "endTime", "limit"}), EXCHANGE_INFO: frozenset()}
QUOTE = "USDT"
PAGE = 1000
PACE_SECONDS = 0.12             # about 8 calls a second, near 1,000 of the 6,000 request weight a minute
TIMEOUT_SECONDS = 20
ATTEMPTS = 3
BACKOFF_SECONDS = 2.0
MAX_BYTES = 1 << 26             # exchangeInfo is near 18 MB
OUTAGE_WAITS = (30, 60, 120, 240, 240, 240)     # seconds between tries of the first call of a run, inside the job's 50 minutes
MAX_FAILED_IN_A_ROW = 5         # a dead network must not walk 520 pairs through every retry
DAILY_LOOKBACK_DAYS = 120       # before the first rule week: more than the 60 bars the rule counts, in one page

Transport = Callable[[str, float], tuple[int, bytes]]


class FetchError(Exception):
    """A call failed for good after its retries. The next name may still work."""


class FetchStop(FetchError):
    """Stop asking this run: the exchange said so, or too many calls failed in a row."""


class UnknownSymbol(FetchError):
    """The exchange has no such pair, so it has no more bars for it either."""


def check_url(url: str) -> None:
    """The allowlist. Nothing else leaves this module."""
    p = urlsplit(url)
    if p.scheme != "https" or p.netloc != HOST or p.path not in ALLOWED or p.fragment:
        raise ValueError(f"not an allowed public endpoint: {p.netloc}{p.path}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def urllib_transport(url: str, timeout: float) -> tuple[int, bytes]:
    """One GET. No proxy, whatever the environment says, no header of ours, no cookie, and a redirect comes back as its status, so the allowlist cannot be walked around. read with a size hands back what came when a connection is cut, so the length is checked here."""
    check_url(url)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect)
    try:
        with opener.open(url, timeout=timeout) as r:
            body = r.read(MAX_BYTES)
            sent = r.headers.get("Content-Length")
            if sent is not None and len(body) != int(sent):
                raise http.client.IncompleteRead(body, int(sent) - len(body))
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read(MAX_BYTES)


class Client:
    """Paced GETs. transport(url, timeout) returns (status, body); tests give their own, so no test can reach the network."""

    def __init__(self, transport: Transport | None = None, pace: float = PACE_SECONDS, sleep: Callable[[float], None] = time.sleep):
        self.transport = transport or urllib_transport
        self.pace = pace
        self.sleep = sleep
        self.calls = 0

    def url(self, path: str, params: dict) -> str:
        """The URL of a call, or a refusal: unknown path, unknown parameter, a pair outside USDT spot, a masked symbol."""
        if path not in ALLOWED or set(params) - ALLOWED[path]:
            raise ValueError(f"not an allowed call: {path} {sorted(params)}")
        if "symbol" in params:
            symbol = params["symbol"]
            config.assert_open(symbol)
            if symbol != symbol.upper() or not symbol.endswith(QUOTE) or not symbol.isalnum():
                raise ValueError(f"not a {QUOTE} symbol: {symbol}")
        url = ORIGIN + path + (f"?{urlencode(params)}" if params else "")
        check_url(url)
        return url

    def get(self, path: str, params: dict | None = None):
        url = self.url(path, params or {})
        why = ""
        for attempt in range(ATTEMPTS):
            if attempt:
                self.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))
            self.sleep(self.pace)
            self.calls += 1
            try:
                status, body = self.transport(url, TIMEOUT_SECONDS)
            except (OSError, http.client.HTTPException) as e:      # URLError and timeouts are OSError, a body cut short is not
                why = type(e).__name__
                continue
            if status in (418, 429):
                raise FetchStop(f"status {status}")
            if status == 200:
                try:
                    return json.loads(body)
                except ValueError:
                    why = "a body that is not JSON"
                    continue
            if status == 400 and b"-1121" in body:
                raise UnknownSymbol(params["symbol"] if params else path)
            if status < 500:
                raise FetchError(f"status {status}")
            why = f"status {status}"
        raise FetchError(why)


def exchange_info(client: Client) -> dict:
    return client.get(EXCHANGE_INFO)


def patient_exchange_info(client: Client) -> dict:
    """exchangeInfo, waiting out an outage. The exchange saying stop is not an outage."""
    for wait in OUTAGE_WAITS:
        try:
            return exchange_info(client)
        except FetchStop:
            raise
        except FetchError:
            client.sleep(wait)
    return exchange_info(client)


def pages(client: Client, symbol: str, interval: str, step_ms: int, start_ms: int, cut_ms: int) -> Iterator[list]:
    """Closed bars of a symbol, a page at a time, oldest first. A bar whose close time is not before the cutoff is dropped here and again by the store."""
    while start_ms < cut_ms:
        raw = client.get(KLINES, {"symbol": symbol, "interval": interval, "startTime": start_ms, "endTime": cut_ms - 1, "limit": PAGE})
        rows = [r for r in raw if int(r[6]) < cut_ms]
        if rows:
            yield rows
        if len(raw) < PAGE or not rows:
            return
        start_ms = int(rows[-1][0]) + step_ms


def _last_key(path: Path, column: str) -> int | None:
    if not path.exists():
        return None
    return pl.scan_parquet(path).select(pl.col(column).max()).collect().item()


def refresh_coin(client: Client, coin: str, cutoff, root: Path) -> int:
    """Hourly bars of one panel coin up to the cutoff, from its last stored bar, or from the start of the grid for a new coin. Rows added."""
    config.assert_open(coin)
    t0 = config.ts(config.T0) * 1000
    last = _last_key(config.bars_dir(root) / f"{coin}.parquet", "hour")
    start = t0 if last is None else t0 + (last + 1) * config.HOUR_MS
    added = 0
    for rows in pages(client, universe.exchange_base(coin) + QUOTE, "1h", config.HOUR_MS, start, config.ts(cutoff) * 1000):
        hours = pn.hours_from_open_ms([r[0] for r in rows])
        added += pn.append_bars(coin, hours, [float(r[1]) for r in rows], [float(r[4]) for r in rows], [float(r[7]) for r in rows], cutoff, root)
    return added


def refresh_pair(client: Client, base: str, cutoff, root: Path) -> int:
    """Daily bars of one exchange base up to the cutoff, from its last stored day. A first fetch starts far enough before the first rule week for the rule's bar count. Rows added."""
    config.assert_open(base)
    epoch = date(1970, 1, 1)
    last = _last_key(config.daily_dir(root) / f"{base}.parquet", "day")
    first = (date.fromisoformat(config.RULE_FIRST_WEEK) - timedelta(days=DAILY_LOOKBACK_DAYS) - epoch).days
    start = first if last is None else last + 1
    added = 0
    for rows in pages(client, base + QUOTE, "1d", config.DAY_MS, start * config.DAY_MS, config.ts(cutoff) * 1000):
        days = [epoch + timedelta(days=int(r[0]) // config.DAY_MS) for r in rows]
        added += pn.append_daily(base, days, [float(r[4]) for r in rows], [float(r[7]) for r in rows], cutoff, root)
    return added


@dataclass
class Report:
    """What a run's fetch did. trading holds the bases the exchange trades now, None if it could not be learned. stopped holds the coins the exchange says have no more bars: it does not trade them, or it has no such pair. A coin that still trades and lacks a bar is late, not stopped, however its fetch ended."""

    attempted: bool = False
    tradable: list[str] | None = None
    trading: set[str] | None = None
    daily_failed: list[str] = field(default_factory=list)
    hourly_failed: list[str] = field(default_factory=list)
    stopped: set[str] = field(default_factory=set)
    new_coins: list[str] = field(default_factory=list)
    stop: str | None = None
    errors: list[str] = field(default_factory=list)
    calls: int = 0

    @property
    def daily_fail_share(self) -> float:
        """Share of pairs whose daily fetch failed. A run that could not learn the pairs failed them all. No fetch asked means no failure."""
        if not self.attempted:
            return 0.0
        return len(self.daily_failed) / len(self.tradable) if self.tradable else 1.0


def _each(names: list[str], work: Callable[[str], object], failed: list[str], report: Report) -> None:
    """work(name) for every name. Whatever goes wrong with a name, a bad page or a full disk, marks that name and the next goes on; a stop marks all that are left. A masked name is not a failure: it goes up as the refusal it is."""
    if report.stop:
        failed.extend(names)
        return
    in_a_row = 0
    for i, name in enumerate(names):
        try:
            work(name)
            in_a_row = 0
        except config.SealedSymbolError:
            raise
        except FetchStop as e:
            report.stop = str(e)
            failed.extend(names[i:])
            return
        except Exception:
            failed.append(name)
            in_a_row += 1
            if in_a_row >= MAX_FAILED_IN_A_ROW:
                report.stop = "too many failures in a row"
                failed.extend(names[i + 1:])
                return


def refresh_pairs(client: Client, cutoff, root: Path, report: Report) -> None:
    """Learn the trading pairs, then the daily bars of each. A run that cannot learn the pairs leaves tradable None, and every pair counts as failed."""
    try:
        info = patient_exchange_info(client)
        report.tradable = universe.tradable_bases(info)
        trading = universe.trading_bases(info)
        report.trading = trading if BTC in trading else None        # an answer without BTC says nothing about who stopped
    except FetchStop as e:
        report.stop = str(e)
    except Exception as e:
        report.errors.append(f"exchangeInfo: {type(e).__name__}")
    if report.tradable:
        _each(report.tradable, lambda base: refresh_pair(client, base, cutoff, root), report.daily_failed, report)
    report.calls = client.calls


def refresh_hourly(client: Client, coins: list[str], cutoff, root: Path, report: Report) -> None:
    """Hourly bars of the coins a run reads. Stops at once if the daily step was stopped."""

    def work(coin: str) -> None:
        new = not (config.bars_dir(root) / f"{coin}.parquet").exists()
        try:
            added = refresh_coin(client, coin, cutoff, root)
        except UnknownSymbol:
            added = 0
            report.stopped.add(coin)
        if report.trading is not None and universe.exchange_base(coin) not in report.trading:
            report.stopped.add(coin)
        if new and added:
            report.new_coins.append(coin)

    _each(coins, work, report.hourly_failed, report)
    report.calls = client.calls
