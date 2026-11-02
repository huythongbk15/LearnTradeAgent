"""A fake of the public exchange on the synthetic market, the only network the selection tests see, and the world a weekly run lives in.

The world moves the rule's first week from 2026-08-10 to 2020-08-31, so the run meets a frozen seed, rule lists and fetched bars on the short synthetic market, in the same order the real one will.
"""
from __future__ import annotations

import json
import socket
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import numpy as np
import pytest

from tests.test_selection_fixture import Market, market, write_seed
from tools.selection import config, decide, fetch, run_weekly, universe

FROZEN_LAST = "2020-08-24"
RULE_FIRST = "2020-08-31"
SEED_END = "2020-08-27"
MONDAYS = ("2020-08-31", "2020-09-07", "2020-09-14", "2020-09-21", "2020-09-28", "2020-10-05", "2020-10-12", "2020-10-19", "2020-10-26")
T0_MS = config.ts(config.T0) * 1000


def refuse_network(monkeypatch) -> None:
    """Any real socket or the real transport fails the test."""

    def refuse(*args, **kwargs):
        raise AssertionError("a test reached for the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(fetch, "urllib_transport", refuse)


def hour_ms(hour: int) -> int:
    return T0_MS + int(hour) * config.HOUR_MS


def kline(open_ms: int, step_ms: int, o: float, c: float, q: float) -> list:
    return [open_ms, repr(o), repr(max(o, c)), repr(min(o, c)), repr(c), "1.0", open_ms + step_ms - 1, repr(q), 7, "0.5", "0.5", "0"]


class FakeExchange:
    """transport(url, timeout) of fetch.Client on a market: it pages, drops by endTime and limit, and sends strings, as the real API does. hook(call_no, path, query) can answer a call itself. Every call is kept, and a call for a masked coin is noted: none must ever come."""

    def __init__(self, mk: Market, ignore_end: bool = False, listed: tuple[str, ...] | None = None):
        self.mk = mk
        self.ignore_end = ignore_end
        self.listed = listed
        self.hook = None
        self.calls: list[tuple[str, dict]] = []
        self.masked_requests: list[str] = []

    def bases(self) -> list[str]:
        coins = [universe.exchange_base(c) for c in self.mk.coins]
        return coins if self.listed is None else [c for c in coins if c in self.listed]

    def trades(self, base: str) -> bool:
        """A coin with no bar at the market's last hour has stopped: the exchange no longer trades it, and serves the bars it has."""
        return not np.isnan(self.mk.closes[-1, self.column(f"{base}USDT")])

    def info(self) -> dict:
        return {"symbols": [{"symbol": f"{c}USDT", "status": "TRADING" if self.trades(c) else "BREAK", "baseAsset": c, "quoteAsset": "USDT", "isSpotTradingAllowed": True} for c in self.bases()]}

    def column(self, symbol: str) -> int | None:
        coin = universe.panel_name(symbol[:-4])
        return self.mk.coins.index(coin) if coin in self.mk.coins else None

    def hourly(self, j: int) -> list:
        o, c, q = self.mk.opens[:, j], self.mk.closes[:, j], self.mk.quotes[:, j]
        return [kline(hour_ms(h), config.HOUR_MS, float(o[h]), float(c[h]), float(q[h])) for h in np.flatnonzero(~np.isnan(c))]

    def daily(self, j: int) -> list:
        out = []
        c, q, o = self.mk.closes[:, j], self.mk.quotes[:, j].astype(np.float64), self.mk.opens[:, j]
        for day in range(0, len(c), 24):
            seen = np.flatnonzero(~np.isnan(c[day:day + 24]))
            if len(seen) and day + 24 <= len(c):
                out.append(kline(hour_ms(day), config.DAY_MS, float(o[day + seen[0]]), float(c[day + seen[-1]]), float(np.nansum(q[day:day + 24]))))
        return out

    def klines(self, q: dict) -> tuple[int, bytes]:
        j = self.column(q["symbol"])
        if j is None:
            return 400, b'{"code":-1121,"msg":"Invalid symbol."}'
        rows = self.hourly(j) if q["interval"] == "1h" else self.daily(j)
        start, end, limit = int(q["startTime"]), int(q["endTime"]), int(q["limit"])
        rows = [r for r in rows if r[0] >= start and (self.ignore_end or r[0] <= end)]
        return 200, json.dumps(rows[:limit]).encode()

    def __call__(self, url: str, timeout: float) -> tuple[int, bytes]:
        p = urlsplit(url)
        q = dict(parse_qsl(p.query))
        assert p.scheme == "https" and p.netloc == fetch.HOST and p.path in fetch.ALLOWED and timeout > 0
        self.calls.append((p.path, q))
        if self.hook is not None:
            out = self.hook(len(self.calls), p.path, q)
            if out is not None:
                return out
        if p.path == fetch.EXCHANGE_INFO:
            return 200, json.dumps(self.info()).encode()
        if config.is_sealed(q["symbol"]):
            self.masked_requests.append(q["symbol"])
        return self.klines(q)

    def kline_calls(self, symbol: str, interval: str = "1h") -> list[dict]:
        return [q for path, q in self.calls if path == fetch.KLINES and q["symbol"] == symbol and q["interval"] == interval]


def client_of(exchange) -> fetch.Client:
    return fetch.Client(transport=exchange, pace=0.0, sleep=lambda s: None)


def seed_market(mk: Market) -> Market:
    """The market as the seed holds it: bars to SEED_END, lists to the last frozen week."""
    T = config.hour_of(SEED_END)
    return replace(mk, opens=mk.opens[:T], closes=mk.closes[:T], quotes=mk.quotes[:T], weeks={k: v for k, v in mk.weeks.items() if k <= FROZEN_LAST})


def blank(mk: Market, coin: str, a: int, b: int) -> Market:
    """The same market with a coin's bars removed from hour a to hour b."""
    j = mk.coins.index(coin)
    Op, C, Q = mk.opens.copy(), mk.closes.copy(), mk.quotes.copy()
    Op[a:b, j] = C[a:b, j] = Q[a:b, j] = np.nan
    return replace(mk, opens=Op, closes=C, quotes=Q)


def without(mk: Market, coin: str) -> Market:
    """The same market without a coin, for a seed that never knew it."""
    keep = [j for j, c in enumerate(mk.coins) if c != coin]
    return replace(mk, coins=tuple(mk.coins[j] for j in keep), opens=mk.opens[:, keep], closes=mk.closes[:, keep], quotes=mk.quotes[:, keep])


def on_time(monday: str, minutes: int = 7) -> datetime:
    return datetime.fromisoformat(monday).replace(tzinfo=UTC) + timedelta(minutes=minutes)


@dataclass
class World:
    root: Path
    seed: Path
    exchange: FakeExchange

    def run(self, monday: str, now: datetime | None = None, fetching: bool = True, exchange=None) -> run_weekly.Outcome:
        client = client_of(exchange or self.exchange)
        return run_weekly.run(monday, fetching=fetching, client=client, now=now or on_time(monday))

    def decision(self, monday: str) -> dict:
        return decide.read_json(self.folder(monday) / "decision.json")

    def target(self, monday: str) -> dict:
        return decide.read_json(self.folder(monday) / "target.json")

    def status(self) -> dict:
        return decide.read_json(self.root / "status.json")

    def folder(self, monday: str) -> Path:
        return config.runs_dir(self.root) / monday

    def files(self, skip_wall_clock: bool = True) -> dict[str, bytes]:
        """Every byte under the root. The lock is not a record, and the manifest holds the wall clock."""
        names = {".lock", "manifest.json"} if skip_wall_clock else {".lock"}
        return {str(p.relative_to(self.root)): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file() and p.name not in names}


def make_world(tmp_path: Path, monkeypatch, seed_mk: Market | None = None, exchange_mk: Market | None = None) -> World:
    """A fresh root and a seed, with the rule's first week moved onto the synthetic market."""
    monkeypatch.setattr(config, "FROZEN_LAST_WEEK", FROZEN_LAST)
    monkeypatch.setattr(config, "RULE_FIRST_WEEK", RULE_FIRST)
    mk = market()
    seed = write_seed(tmp_path / "seed", seed_mk or seed_market(mk))
    monkeypatch.setenv("SELECTION_ROOT", str(tmp_path / "root"))
    monkeypatch.setenv("SELECTION_SEED", str(seed))
    return World(tmp_path / "root", seed, FakeExchange(exchange_mk or mk))


def test_the_fake_answers_like_the_public_api() -> None:
    mk = market()
    ex = FakeExchange(mk)
    url = f"{fetch.ORIGIN}{fetch.KLINES}?symbol=AAAUSDT&interval=1h&startTime={hour_ms(5)}&endTime={hour_ms(7)}&limit=2"
    status, body = ex(url, 1.0)
    rows = json.loads(body)
    j = mk.coins.index("AAA")
    assert status == 200 and [r[0] for r in rows] == [hour_ms(5), hour_ms(6)]       # a page of 2 from the start
    assert len(rows[0]) == 12 and rows[0][6] == hour_ms(6) - 1 and isinstance(rows[0][4], str)
    assert float(rows[0][1]) == mk.opens[5, j] and float(rows[0][4]) == mk.closes[5, j] and float(rows[0][7]) == float(mk.quotes[5, j])


def test_the_fake_builds_daily_bars_from_closed_days_of_the_hourly_bars() -> None:
    mk = market()
    ex = FakeExchange(mk)
    j = mk.coins.index("AAA")
    day = ex.daily(j)[2]
    assert day[0] == hour_ms(48) and day[6] == hour_ms(72) - 1
    assert float(day[4]) == mk.closes[71, j] and float(day[7]) == pytest.approx(float(mk.quotes[48:72, j].astype(float).sum()))
    assert len(ex.daily(j)) == mk.opens.shape[0] // 24


def test_the_fake_knows_luna_by_its_exchange_name_and_refuses_what_it_does_not_list() -> None:
    ex = FakeExchange(market())
    assert ex.column("LUNAUSDT") == ex.mk.coins.index("LUNA2")
    url = f"{fetch.ORIGIN}{fetch.KLINES}?symbol=NOPEUSDT&interval=1h&startTime=0&endTime=1&limit=1"
    assert ex(url, 1.0) == (400, b'{"code":-1121,"msg":"Invalid symbol."}')


def test_the_fake_does_not_trade_a_coin_that_stopped_but_still_serves_its_bars() -> None:
    ex = FakeExchange(market())
    status = {s["baseAsset"]: s["status"] for s in ex.info()["symbols"]}
    assert status["GONE"] == "BREAK" and status["AAA"] == "TRADING" and status["HOLE"] == "TRADING"
    assert ex.hourly(ex.column("GONEUSDT"))


def test_the_fake_lists_masked_coins_and_notes_a_request_for_one() -> None:
    ex = FakeExchange(market())
    assert "ETH" in [s["baseAsset"] for s in ex.info()["symbols"]]
    ex(f"{fetch.ORIGIN}{fetch.KLINES}?symbol=ETHUSDT&interval=1h&startTime=0&endTime=1&limit=1", 1.0)
    assert ex.masked_requests == ["ETHUSDT"]
