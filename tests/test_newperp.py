"""NL-P paper log: universe, book walk, stop fill, allowlist, and a two-week run on a fake venue. No network."""
from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from tools.newperp import config as cf
from tools.newperp import fetch as fx
from tools.newperp import rules
from tools.newperp import run as rn

H = rules.HOUR_MS
STAMP = int(datetime(2026, 11, 9, tzinfo=UTC).timestamp() * 1000)
ENTRY = STAMP + 4 * H
EXIT = ENTRY + 7 * 24 * H


def info():
    def s(sym, onboard, kind="COIN", status="TRADING", quote="USDT", ctype="PERPETUAL", sub=("Crypto",)):
        return {"symbol": sym, "baseAsset": sym[:-4], "quoteAsset": quote, "contractType": ctype, "status": status,
                "underlyingType": kind, "underlyingSubType": list(sub), "onboardDate": onboard}
    return {"symbols": [s("NEWUSDT", STAMP - 10 * 24 * H), s("OLDUSDT", STAMP - 40 * 24 * H), s("ALPUSDT", STAMP - 5 * 24 * H, sub=("Meme", "Alpha")), s("AAPLUSDT", STAMP - 5 * 24 * H, kind="EQUITY"),
                        s("SOONUSDT", STAMP + H), s("HALTUSDT", STAMP - 3 * 24 * H, status="SETTLING"), s("NEWUSDC", STAMP - 2 * 24 * H, quote="USDC")]}


def bar(t, o, h=None, v=1.0, qv=None):
    h = o if h is None else h
    return [t, str(o), str(h), str(o), str(o), str(v), t + 59_999, str(qv if qv is not None else o * v)]


class Venue:
    """Answers the four allowed calls. NEW opens at 1.0, touches 1.6 on minute 3 of its week; BTC opens at 100 and ends at 110."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, timeout):
        fx.check_url(url)
        p = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        self.calls.append((p.path, q))
        if p.path == fx.EXCHANGE_INFO:
            return 200, json.dumps(info()).encode()
        if p.path == fx.DEPTH:
            return 200, json.dumps({"bids": [["0.99", "1000"], ["0.98", "100000"]], "asks": [["1.01", "1000"], ["1.02", "100000"]]}).encode()
        if p.path == fx.FUNDING:
            a, b = int(q["startTime"]), int(q["endTime"])
            ev = [{"fundingTime": t, "fundingRate": "0.0001"} for t in range(ENTRY, EXIT, 8 * H) if a <= t <= b]
            return 200, json.dumps(ev).encode()
        a, b = int(q["startTime"]), int(q["endTime"])
        sym = q["symbol"]
        if q["interval"] == "1h":
            o = (100.0 if a < EXIT else 110.0) if sym == "BTCUSDT" else (1.0 if a < EXIT else 0.8)
            return 200, json.dumps([bar(a, o)] if a <= b else []).encode()
        rows = [bar(t, 1.0, 1.0) for t in range(a, min(b + 1, a + 10 * 60_000), 60_000)]
        rows[3] = bar(rows[3][0], 1.2, 1.6, v=2.0, qv=3.1)
        return 200, json.dumps(rows).encode()


def client(v):
    return fx.Client(v, pace=0, sleep=lambda s: None)


def test_new_perps_keeps_only_young_usdt_coin_perpetuals_and_splits_alpha():
    new = rules.new_perps(info(), STAMP)
    assert [p["symbol"] for p in new] == ["NEWUSDT", "ALPUSDT"]
    assert [rules.book_of(p) for p in new] == ["core", "alpha"]


def test_book_cost_walks_the_bids_for_a_sell():
    d = {"bids": [["0.99", "1000"], ["0.98", "100000"]], "asks": [["1.01", "1000"]]}
    c = rules.book_cost(d, "sell", 1980)
    assert c["mid"] == pytest.approx(1.0) and c["spread_bps"] == pytest.approx(200)
    assert c["cost_bps"] == pytest.approx((1 - 1980 / (1000 + 990 / .98)) * 1e4)    # 1,000 at .99, then 990 USD at .98
    assert c["filled_usd"] == pytest.approx(1980)


def test_stop_fill_reads_the_first_touching_minute():
    m1 = [bar(0, 1.0, 1.4), bar(60_000, 1.0, 1.4, v=0), bar(120_000, 1.45, 1.7, v=2.0, qv=3.2)]
    s = rules.stop_fill(m1, 1.0)
    assert s["minute_ms"] == 120_000 and s["f1"] == pytest.approx(1.5) and s["f2"] == pytest.approx(1.6) and s["f3"] == pytest.approx(1.7)
    assert rules.stop_fill(m1[:2], 1.0) is None


def test_client_refuses_other_hosts_paths_parameters_and_masked_coins():
    c = client(Venue())
    with pytest.raises(ValueError):
        c.url("/fapi/v1/order", {})
    with pytest.raises(ValueError):
        c.url(fx.KLINES, {"symbol": "NEWUSDT", "signature": "x"})
    with pytest.raises(ValueError):
        fx.check_url("https://api.binance.com/fapi/v1/klines")
    with pytest.raises(ValueError):
        c.url(fx.KLINES, {"symbol": "ETHUSDT", "interval": "1h"})
    assert c.url(fx.KLINES, {"symbol": "BTCUSDT", "interval": "1h"}).startswith("https://fapi.binance.com/")


def test_client_allows_the_four_futures_data_series_with_their_parameters_only():
    c = client(Venue())
    full = {"symbol": "BTCUSDT", "period": "5m", "limit": 500, "startTime": 1, "endTime": 2}
    for path in fx.STATS:
        assert c.url(path, full).startswith(f"https://fapi.binance.com{path}?")
        fx.check_url(c.url(path, {"symbol": "NEWUSDT", "period": "1d"}))
    for bad in ({**full, "interval": "1h"}, {**full, "period": "3m"}, {"symbol": "BTCUSDT"}, {"period": "5m"},
                {**full, "limit": 501}, {**full, "limit": 0}):
        with pytest.raises(ValueError):
            c.url(fx.OPEN_INTEREST_HIST, bad)
    with pytest.raises(ValueError):
        c.url(fx.TOP_POSITION_RATIO, {"symbol": "SOLUSDT", "period": "5m"})       # masked
    with pytest.raises(ValueError):
        fx.check_url("https://fapi.binance.com/futures/data/basis")
    with pytest.raises(ValueError):
        fx.check_url("https://www.binance.com/futures/data/openInterestHist")
    with pytest.raises(ValueError):
        fx.stats(c, fx.KLINES, "BTCUSDT", "5m", 0, 1)


def test_stats_asks_in_whole_windows_and_returns_each_stamp_once_oldest_first():
    step = fx.PERIODS["5m"]
    calls = []

    def venue(url, timeout):      # answers the newest `limit` rows of the window, newest first
        fx.check_url(url)
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        calls.append(q)
        a, b, n = int(q["startTime"]), int(q["endTime"]), int(q["limit"])
        stamps = [t for t in range(0, 10_000 * step, step) if a <= t <= b][-n:][::-1]
        return 200, json.dumps([{"symbol": q["symbol"], "timestamp": t, "sumOpenInterest": "1"} for t in stamps]).encode()

    rows = fx.stats(client(venue), fx.OPEN_INTEREST_HIST, "BTCUSDT", "5m", 100 * step, 1300 * step)
    assert [r["timestamp"] for r in rows] == list(range(100 * step, 1300 * step, step))
    assert len(calls) == 3 and all(q["limit"] == "500" and q["period"] == "5m" for q in calls)


def test_root_stays_in_data_newperp(monkeypatch, tmp_path):
    monkeypatch.setenv("NEWPERP_ROOT", str(cf.REPO / "data" / "other"))
    with pytest.raises(ValueError):
        cf.root()
    monkeypatch.setenv("NEWPERP_ROOT", str(tmp_path))
    assert cf.root() == tmp_path.resolve()


def test_two_weeks_open_settle_and_do_not_repeat(monkeypatch, tmp_path):
    v = Venue()
    now = ENTRY + 7 * 60_000
    monkeypatch.setattr(rn.time, "time", lambda: now / 1000)
    s = rn.run(now, client(v), tmp_path, log=lambda x: None)
    assert s["core"]["short_legs"] == 1 and s["alpha"]["short_legs"] == 1 and s["open_legs"] == 4 and s["core"]["settled_symbol_weeks"] == 0
    recs = rn.load(cf.ledger_path(tmp_path))
    leg = next(r for r in recs if r["kind"] == "leg_open" and r["side"] == "short" and r["book"] == "core")
    assert {r["book"] for r in recs if r["kind"] == "leg_open" and r["symbol"] == "BTCUSDT"} == {"core", "alpha"}
    assert leg["weight"] == -.5 and leg["entry_open"] == 1.0 and leg["entry_book"]["paper"]["cost_bps"] > 0
    rn.run(now + 60_000, client(v), tmp_path, log=lambda x: None)
    assert sum(1 for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "week_open") == 1
    later = EXIT + 7 * 60_000
    monkeypatch.setattr(rn.time, "time", lambda: later / 1000)
    s = rn.run(later, client(v), tmp_path, log=lambda x: None)
    closes = {r["id"]: r for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "leg_close"}
    short = closes[leg["id"]]
    assert short["stop"]["f1"] == pytest.approx(1.5) and short["exit_open"] == 0.8 and short["funding"] > 0
    btc = next(c for k, c in closes.items() if k.endswith("BTCUSDT-core"))
    assert btc["exit_open"] == 110.0 and btc["stop"] is None
    assert s["core"]["settled_symbol_weeks"] == 1 and s["core"]["stopped"] == 1 and s["alpha"]["settled_symbol_weeks"] == 1
    assert not any(p == "/fapi/v1/order" for p, _ in v.calls)


def test_a_replay_records_no_order_book(monkeypatch, tmp_path):
    monkeypatch.setattr(rn.time, "time", lambda: (EXIT + 30 * 24 * H) / 1000)
    rn.run(ENTRY + 60_000, client(Venue()), tmp_path, log=lambda x: None)
    legs = [r for r in rn.load(cf.ledger_path(tmp_path)) if r["kind"] == "leg_open"]
    assert legs and all(r["entry_book"] is None for r in legs)


def test_status_shows_no_return(monkeypatch, tmp_path):
    monkeypatch.setattr(rn.time, "time", lambda: (ENTRY + 60_000) / 1000)
    s = rn.run(ENTRY + 60_000, client(Venue()), tmp_path, log=lambda x: None)
    flat = json.dumps(s)
    assert not any(k in flat for k in ("net", "pnl", "return", "bps_week"))
