"""NL-P book bybit: universe point in time against Binance, allowlist, kline layout and paging, and a week on fake venues. No network."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.newperp import bybit as by
from tools.newperp import config as cf
from tools.newperp import fetch as fx
from tools.newperp import run as rn
from tools.selection import fetch as sf

H = 3_600_000
D = 24 * H
M = 60_000
STAMP = int(datetime(2026, 11, 9, tzinfo=UTC).timestamp() * 1000)
ENTRY = STAMP + 4 * H
EXIT = ENTRY + 7 * D


def binance_info():
    def s(sym, onboard, ctype="PERPETUAL"):
        return {"symbol": sym, "baseAsset": sym[:-4], "quoteAsset": "USDT", "contractType": ctype, "status": "TRADING",
                "underlyingType": "COIN", "underlyingSubType": [], "onboardDate": onboard}
    return {"symbols": [s("NEWUSDT", STAMP - 10 * D), s("MATICUSDT", STAMP - 900 * D), s("BTCUSDT", STAMP - 2000 * D), s("SOONUSDT", STAMP + D),
                        s("GMEUSDT", STAMP - 300 * D, ctype="TRADIFI_PERPETUAL")]}


def instr(sym, base, launch, stype="", status="Trading", phase=None):
    x = {"symbol": sym, "baseCoin": base, "quoteCoin": "USDT", "contractType": "LinearPerpetual", "status": status,
         "symbolType": stype, "launchTime": str(launch)}
    if phase:
        x["preListingInfo"] = {"curAuctionPhase": phase}
    return x


INSTR = [instr("NEWBUSDT", "NEWB", STAMP - 10 * D), instr("INNOUSDT", "INNO", STAMP - 5 * D, "innovation"),
         instr("LATEUSDT", "LATE", STAMP - 40 * D), instr("AAPLUSDT", "AAPL", STAMP - 5 * D, "stock"),
         instr("USD1USDT", "USD1", STAMP - 5 * D), instr("NEWUSDT", "NEW", STAMP - 9 * D), instr("POLUSDT", "POL", STAMP - 3 * D),
         instr("OLDBUSDT", "OLDB", STAMP - 70 * D), instr("SOONUSDT", "SOON", STAMP - 2 * D), instr("ADAUSDT", "ADA", STAMP - 3 * D),
         instr("1000ZZUSDT", "1000ZZ", STAMP - 6 * D), instr("PREMUSDT", "PREM", STAMP - 4 * D, status="PreLaunch", phase="ContinuousTrading"),
         instr("PREXUSDT", "PREX", STAMP - 4 * D, status="PreLaunch", phase="CallAuction"), instr("GMEUSDT", "GME", STAMP - 3 * D)]
FIRST_TRADE = {"NEWBUSDT": STAMP - 10 * D, "INNOUSDT": STAMP - 2 * D, "LATEUSDT": STAMP - 20 * D, "POLUSDT": STAMP - 3 * D,
               "SOONUSDT": STAMP - 2 * D, "1000ZZUSDT": STAMP - 6 * D, "PREMUSDT": STAMP - 4 * D, "GMEUSDT": STAMP - 3 * D}
FOUND = ["LATEUSDT", "NEWBUSDT", "1000ZZUSDT", "INNOUSDT", "PREMUSDT", "GMEUSDT", "SOONUSDT"]


def ok(result):
    return 200, json.dumps({"retCode": 0, "retMsg": "OK", "result": result}).encode()


class Bybit:
    """Answers the five allowed calls, newest first like Bybit. New perps open at 1.0 and exit at 0.8; NEWB's minute 3 touches 1.6,
    its mark touches 1.55 on minute 4. BTC opens at 100 and ends at 110."""

    def __init__(self, fail_instruments=False):
        self.calls = []
        self.fail = fail_instruments

    def __call__(self, url, timeout):
        by.check_url(url)
        p = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        self.calls.append((p.path, q))
        if p.path == by.INSTRUMENTS:
            if self.fail:
                return 200, json.dumps({"retCode": 10016, "retMsg": "svc error"}).encode()
            return ok({"list": [x for x in INSTR if x["status"] == q["status"]], "nextPageCursor": ""})
        if p.path == by.ORDERBOOK:
            return ok({"b": [["0.99", "1000"], ["0.98", "100000"]], "a": [["1.01", "1000"], ["1.02", "100000"]]})
        if p.path == by.FUNDING:
            a, b = int(q["startTime"]), int(q["endTime"])
            ev = [{"fundingRate": "0.0001", "fundingRateTimestamp": str(t)} for t in range(ENTRY, EXIT, 8 * H) if a <= t <= b]
            return ok({"list": ev[::-1]})
        a, b, sym, iv = int(q["start"]), int(q["end"]), q["symbol"], q["interval"]
        if iv == "60":
            rows = []
            for t in range((a + H - 1) // H * H, b + 1, H):
                if sym == "BTCUSDT":
                    o = 100.0 if t < EXIT else 110.0
                else:
                    if t < FIRST_TRADE.get(sym, 0):
                        rows.append([str(t), "1", "1", "1", "1", "0", "0"])
                        continue
                    o = 1.0 if t < EXIT else 0.8
                rows.append([str(t), str(o), str(o), str(o), str(o), "5", str(5 * o)])
            rows = rows[::-1][: int(q["limit"])]
            return ok({"list": rows})
        rows = []
        for t in range(a, min(b + 1, a + 10 * M), M):
            h = 1.0
            if sym == "NEWBUSDT" and p.path == by.KLINE and t == a + 3 * M:
                h = 1.6
            if sym == "NEWBUSDT" and p.path == by.MARK_KLINE and t == a + 4 * M:
                h = 1.55
            if sym == "INNOUSDT" and p.path == by.MARK_KLINE and t == a + 6 * M:
                h = 1.52                      # the mark crosses the stop, the last price never does
            rows.append([str(t), "1.0", str(h), "1.0", "1.0", "2", "2"] if p.path == by.KLINE else [str(t), "1.0", str(h), "1.0", "1.0"])
        return ok({"list": rows[::-1]})


class Binance:
    def __call__(self, url, timeout):
        fx.check_url(url)
        p = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        if p.path == fx.EXCHANGE_INFO:
            return 200, json.dumps(binance_info()).encode()
        if p.path == fx.DEPTH:
            return 200, json.dumps({"bids": [["0.99", "1000"]], "asks": [["1.01", "1000"]]}).encode()
        if p.path == fx.FUNDING:
            return 200, b"[]"
        a = int(q["startTime"])
        o = (100.0 if a < EXIT else 110.0) if q["symbol"] == "BTCUSDT" else (1.0 if a < EXIT else 0.8)
        if q["interval"] == "1h":
            return 200, json.dumps([[a, str(o), str(o), str(o), str(o), "1", a + H - 1, str(o)]]).encode()
        return 200, json.dumps([[t, "1", "1", "1", "1", "1", t + M - 1, "1"] for t in range(a, a + 5 * M, M)]).encode()


def bclient(v):
    return by.Client(v, pace=0, sleep=lambda s: None)


def fclient():
    return fx.Client(Binance(), pace=0, sleep=lambda s: None)


def test_norm_strips_rebased_units():
    assert [by.norm(x) for x in ("1000PEPE", "10000LADYS", "1MBABYDOGE", "SHIB1000", "10000000AIDOGE", "0G", "BTC")] == \
        ["PEPE", "LADYS", "BABYDOGE", "SHIB", "AIDOGE", "0G", "BTC"]


def test_candidates_are_crypto_open_and_not_on_binance_at_the_stamp():
    on = by.binance_bases(binance_info(), STAMP)
    assert on == {"NEW", "MATIC", "BTC"}          # SOON lists on Binance after the stamp; GME there is a stock perp, not a coin
    got = [c["symbol"] for c in by.candidates(INSTR, STAMP, on)]
    # stock, stablecoin, NEW (on Binance), POL (MATIC renamed), OLDB (too old), ADA (masked), PREX (pre-market auction) are out
    assert got == FOUND


def test_client_refuses_other_hosts_paths_parameters_and_masked_coins():
    c = bclient(Bybit())
    with pytest.raises(ValueError):
        c.url("/v5/order/create", {})
    with pytest.raises(ValueError):
        c.url(by.KLINE, {"symbol": "NEWBUSDT", "apiKey": "x"})
    with pytest.raises(Exception):
        c.url(by.KLINE, {"category": "linear", "symbol": "ADAUSDT", "interval": "60"})
    with pytest.raises(ValueError):
        by.check_url("https://api.bybit.com.evil/v5/market/kline")
    with pytest.raises(ValueError):
        by.check_url("http://api.bybit.com/v5/market/kline")


def test_klines_come_back_oldest_first_in_the_binance_layout_across_pages():
    v = Bybit()
    rows = by.klines(bclient(v), "BTCUSDT", "60", ENTRY - 1200 * H, ENTRY + 2 * H)
    assert len(rows) == 1202 and rows[0][0] == ENTRY - 1200 * H and rows[-1][0] == ENTRY + H
    assert all(rows[i][0] < rows[i + 1][0] for i in range(len(rows) - 1))
    assert rows[0][6] == rows[0][0] + H - 1 and float(rows[0][7]) == 500.0
    assert sum(1 for p, _ in v.calls if p == by.KLINE) == 2


def test_a_bybit_error_code_is_raised():
    with pytest.raises(sf.FetchError):
        by.instruments(bclient(Bybit(fail_instruments=True)))


def test_listing_is_the_first_traded_bar():
    rows = by.klines(bclient(Bybit()), "LATEUSDT", "60", STAMP - 47 * D, STAMP)
    assert by.listing_ms(rows) == STAMP - 20 * D


def test_a_week_opens_and_settles_the_bybit_book(monkeypatch, tmp_path):
    v = Bybit()
    now = ENTRY + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: now / 1000)
    s = rn.run(now, fclient(), tmp_path, log=lambda x: None, by_client=bclient(v))
    recs = rn.load(cf.ledger_path(tmp_path))
    week = next(r for r in recs if r["kind"] == "week_open")
    assert week["bybit_found"] == FOUND
    legs = [r for r in recs if r["kind"] == "leg_open" and r["book"] == cf.BYBIT_BOOK]
    shorts = [r for r in legs if r["side"] == "short"]
    assert len(shorts) == 7 and all(r["venue"] == "bybit" and r["weight"] == pytest.approx(-.5 / 7) for r in shorts)
    assert sum(r["weight"] for r in legs) == pytest.approx(0)
    btc = next(r for r in legs if r["symbol"] == "BTCUSDT")
    assert btc["id"].endswith("-bybit-BTCUSDT") and btc["entry_open"] == 100.0 and btc["entry_book"]["paper"]["cost_bps"] > 0
    late = next(r for r in shorts if r["symbol"] == "LATEUSDT")
    assert late["onboard"] == rn.iso(STAMP - 20 * D) and late["weeks_since_listing"] == 3
    assert s[cf.BYBIT_BOOK]["short_legs"] == 7 and s["core"]["short_legs"] == 1
    later = EXIT + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: later / 1000)
    s = rn.run(later, fclient(), tmp_path, log=lambda x: None, by_client=bclient(v))
    closes = {r["id"]: r for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "leg_close"}
    newb = closes[f"{rn.iso(STAMP)[:10]}-bybit-NEWBUSDT"]
    assert newb["stop"]["f1"] == pytest.approx(1.5) and newb["stop"]["minute_ms"] == ENTRY + 3 * M
    assert newb["stop"]["mark_minute_ms"] == ENTRY + 4 * M and [r[0] for r in newb["stop"]["path"]] == list(range(ENTRY + M, ENTRY + 10 * M, M))
    assert newb["exit_open"] == 0.8 and newb["funding"] == pytest.approx(0.0001)   # only the event at entry falls before the stop
    inno = closes[f"{rn.iso(STAMP)[:10]}-bybit-INNOUSDT"]
    assert inno["stop"] is None and inno["funding"] == pytest.approx(0.0001 * 21) and inno["mark_stop_ms"] == ENTRY + 6 * M
    assert newb["mark_stop_ms"] == ENTRY + 4 * M and closes[f"{rn.iso(STAMP)[:10]}-bybit-LATEUSDT"]["mark_stop_ms"] is None
    assert s[cf.BYBIT_BOOK]["settled_symbol_weeks"] == 7 and s[cf.BYBIT_BOOK]["stopped"] == 1
    assert all(p in by.ALLOWED and p.startswith("/v5/market/") for p, _ in v.calls)


def test_a_bybit_outage_misses_its_week_but_not_the_binance_books(monkeypatch, tmp_path):
    now = ENTRY + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: now / 1000)
    s = rn.run(now, fclient(), tmp_path, log=lambda x: None, by_client=bclient(Bybit(fail_instruments=True)))
    week = next(r for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "week_open")
    assert "bybit_error" in week and s["core"]["short_legs"] == 1 and s[cf.BYBIT_BOOK]["short_legs"] == 0


def test_bybit_legs_wait_without_a_bybit_client(monkeypatch, tmp_path):
    now = ENTRY + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: now / 1000)
    rn.run(now, fclient(), tmp_path, log=lambda x: None, by_client=bclient(Bybit()))
    later = EXIT + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: later / 1000)
    s = rn.run(later, fclient(), tmp_path, log=lambda x: None)
    assert s[cf.BYBIT_BOOK]["settled_symbol_weeks"] == 0 and s["core"]["settled_symbol_weeks"] == 1


class Flaky(Bybit):
    """Answers a rate-limit code first, or an HTTP status, on the chosen path."""

    def __init__(self, path, reply, times=1):
        super().__init__()
        self.path, self.reply, self.left = path, reply, times

    def __call__(self, url, timeout):
        if urlsplit(url).path == self.path and self.left:
            self.left -= 1
            self.calls.append((self.path, {}))
            return self.reply
        return super().__call__(url, timeout)


RATE = (200, json.dumps({"retCode": 10006, "retMsg": "Too many visits"}).encode())


def test_a_rate_limit_code_is_waited_out():
    waits = []
    c = by.Client(Flaky(by.INSTRUMENTS, RATE, times=2), pace=0, sleep=waits.append)
    assert len(by.instruments(c)) == len(INSTR)       # Trading and PreLaunch, each once
    assert [w for w in waits if w] == [2.0, 4.0]


def test_a_rate_limit_that_does_not_clear_or_a_429_costs_only_the_bybit_week(monkeypatch, tmp_path):
    now = ENTRY + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: now / 1000)
    for i, v in enumerate((Flaky(by.INSTRUMENTS, RATE, times=99), Flaky(by.INSTRUMENTS, (429, b""), times=99))):
        root = tmp_path / str(i)
        s = rn.run(now, fclient(), root, log=lambda x: None, by_client=bclient(v))
        week = next(r for r in rn.load(cf.ledger_path(root)) if r["kind"] == "week_open")
        assert "bybit_error" in week and s["core"]["short_legs"] == 1 and s[cf.BYBIT_BOOK]["short_legs"] == 0


def test_bybit_trouble_at_settle_stops_bybit_calls_and_leaves_its_legs_open(monkeypatch, tmp_path):
    now = ENTRY + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: now / 1000)
    rn.run(now, fclient(), tmp_path, log=lambda x: None, by_client=bclient(Bybit()))
    later = EXIT + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: later / 1000)
    v = Flaky(by.KLINE, (429, b""), times=99)
    s = rn.run(later, fclient(), tmp_path, log=lambda x: None, by_client=bclient(v))
    assert s[cf.BYBIT_BOOK]["settled_symbol_weeks"] == 0 and s["core"]["settled_symbol_weeks"] == 1
    # one refused call settling (it ends Bybit settling for the run), one opening the next week (its Bybit book is missed)
    assert len([c for c in v.calls if c[0] == by.KLINE]) == 2
    weeks = [r for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "week_open"]
    assert len(weeks) == 2 and "bybit_error" in weeks[1] and "bybit_error" not in weeks[0]


SVC = (200, json.dumps({"retCode": 10016, "retMsg": "svc error"}).encode())


def test_a_transient_service_error_is_waited_out_and_the_week_opens(monkeypatch, tmp_path):
    now = ENTRY + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: now / 1000)
    s = rn.run(now, fclient(), tmp_path, log=lambda x: None, by_client=bclient(Flaky(by.KLINE, SVC, times=1)))
    week = next(r for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "week_open")
    assert "bybit_error" not in week and s[cf.BYBIT_BOOK]["short_legs"] == 7


class OneSymbol(Bybit):
    """Refuses every call for one symbol with a non-transient code, as for a contract Bybit renamed."""

    def __init__(self, sym):
        super().__init__()
        self.sym = sym

    def __call__(self, url, timeout):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        if q.get("symbol") == self.sym:
            self.calls.append((urlsplit(url).path, q))
            return 200, json.dumps({"retCode": 10001, "retMsg": "params error: symbol invalid"}).encode()
        return super().__call__(url, timeout)


def test_one_refused_symbol_leaves_only_its_leg_open(monkeypatch, tmp_path):
    now = ENTRY + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: now / 1000)
    rn.run(now, fclient(), tmp_path, log=lambda x: None, by_client=bclient(Bybit()))
    later = EXIT + 7 * M
    monkeypatch.setattr(rn.time, "time", lambda: later / 1000)
    s = rn.run(later, fclient(), tmp_path, log=lambda x: None, by_client=bclient(OneSymbol("LATEUSDT")))
    closes = {r["id"] for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "leg_close"}
    assert f"{rn.iso(STAMP)[:10]}-bybit-LATEUSDT" not in closes and s[cf.BYBIT_BOOK]["settled_symbol_weeks"] == 6
