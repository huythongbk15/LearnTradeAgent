"""The new perp pair book on a fake Binance USD-M demo venue: no network."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import ccxt
import pytest

import trading_agent.exchanges.ccxt_adapter as ca
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter, ExchangeConfig
from trading_agent.execution.live_safety import LiveRiskStateStore, LiveSafetyError
from trading_agent.execution.perp import rules
from trading_agent.execution.perp.book import Book, PairStore, PerpBookError, run_cycle
from trading_agent.execution.perp.venue import (
    DEMO_HOST,
    PerpDemoVenue,
    PerpVenueError,
    demo_config,
    load_demo_credentials,
)

KEY = "k" * 40
H = rules.HOUR_MS
T0 = 1_790_000_000_000


class FakeUsdm:
    """Just enough of ccxt.binanceusdm for the adapter and the venue."""

    def __init__(self, prices: dict[str, float]):
        self.prices = dict(prices)
        self.urls = {"api": {"fapiPublic": "https://fapi.binance.com/fapi/v1", "fapiPrivate": "https://fapi.binance.com/fapi/v1"}}
        self.demo_urls = {"fapiPublic": DEMO_HOST + "fapi/v1", "fapiPrivate": DEMO_HOST + "fapi/v1"}
        self.markets = {}
        for ex_id in prices:
            base = ex_id[:-4]
            sym = f"{base}/USDT:USDT"
            self.markets[sym] = {"id": ex_id, "symbol": sym, "base": base, "quote": "USDT", "settle": "USDT",
                                 "type": "swap", "swap": True, "linear": True, "active": True, "limits": {},
                                 "precision": {"amount": 0.001}}
        self.markets_by_id = {m["id"]: [m] for m in self.markets.values()}
        self.positions: dict[str, float] = {}     # ccxt symbol -> signed qty
        self.entry: dict[str, float] = {}
        self.orders: dict[str, dict] = {}         # client id -> order
        self.algo: dict[str, dict] = {}           # open stop orders by client id
        self.hedged = False
        self.calls: list[tuple] = []
        self.fail_next: str | None = None
        self.leverage: dict[str, int] = {}
        self.ack_without_price = False   # demo answered a filled market order with average 0

    # connection
    def enable_demo_trading(self, on):
        self.urls["api"] = dict(self.demo_urls)

    def set_sandbox_mode(self, on):
        raise AssertionError("sandbox must not be used for perps")

    def load_markets(self):
        return self.markets

    def fetch_time(self):
        return T0

    def market(self, sym):
        return self.markets[sym]

    def amount_to_precision(self, sym, amt):  # ccxt truncates to the step
        return f"{math.floor(float(amt) * 1000 + 1e-9) / 1000:.3f}"

    def price_to_precision(self, sym, px):
        return f"{float(px):.4f}"

    def close(self):
        return None

    # account
    def fetch_position_mode(self, symbol=None):
        return {"hedged": self.hedged}

    def set_margin_mode(self, mode, sym):
        self.calls.append(("margin", mode, sym))

    def set_leverage(self, lev, sym):
        self.leverage[sym] = lev

    def fetch_balance(self):
        return {"info": {"totalMarginBalance": "10000"}, "USDT": {"free": 10000, "used": 0, "total": 10000}}

    def fetch_positions(self, symbols=None):
        return [
            {"symbol": s, "contracts": abs(q), "side": "short" if q < 0 else "long", "entryPrice": self.entry[s],
             "markPrice": self.prices[self.markets[s]["id"]], "leverage": 1}
            for s, q in self.positions.items() if abs(q) > 1e-12
        ]

    def fetch_ticker(self, sym):
        px = self.prices[self.markets[sym]["id"]]
        return {"timestamp": T0, "bid": px, "ask": px, "last": px, "high": px, "low": px, "open": px, "close": px,
                "baseVolume": 1, "quoteVolume": px, "change": 0, "percentage": 0}

    # orders
    def create_order(self, sym, typ, side, amount, price=None, params=None):
        params = params or {}
        cid = params["clientOrderId"]
        self.calls.append(("order", sym, typ, side, amount, dict(params)))
        if self.fail_next == cid or (self.fail_next == "stop" and "stopLossPrice" in params):
            raise ccxt.NetworkError("timeout")
        if "stopLossPrice" in params:
            o = {"id": f"a{len(self.algo)}", "clientOrderId": cid, "symbol": sym, "type": "stop_market", "side": side,
                 "amount": amount, "filled": 0, "status": "open", "triggerPrice": params["stopLossPrice"], "reduceOnly": True}
            self.algo[cid] = o
            return dict(o)
        q = self.positions.get(sym, 0.0)
        signed = amount if side == "buy" else -amount
        if params.get("reduceOnly") and (q == 0 or abs(q + signed) > abs(q) + 1e-12):
            raise ccxt.InvalidOrder("ReduceOnly Order is rejected")
        px = self.prices[self.markets[sym]["id"]]
        if abs(q + signed) > 1e-12:
            self.entry[sym] = px if q == 0 or (q > 0) != (signed > 0) else self.entry[sym]
        self.positions[sym] = q + signed
        o = {"id": f"o{len(self.orders)}", "clientOrderId": cid, "symbol": sym, "type": "market", "side": side,
             "amount": amount, "filled": amount, "average": px, "cost": amount * px, "status": "closed", "reduceOnly": bool(params.get("reduceOnly"))}
        self.orders[cid] = o
        if self.ack_without_price:
            return {**o, "average": None, "cost": 0}
        return dict(o)

    def fetch_order(self, oid, sym, params=None):
        cid = (params or {}).get("origClientOrderId")
        if cid in self.orders:
            return dict(self.orders[cid])
        raise ccxt.OrderNotFound("unknown order")

    def fetch_open_orders(self, sym=None, since=None, limit=None, params=None):
        if (params or {}).get("trigger"):
            return [dict(o) for o in self.algo.values() if sym is None or o["symbol"] == sym]
        return []

    def fetch_closed_orders(self, sym=None, since=None, limit=None):
        return []

    def fetch_my_trades(self, sym=None, since=None, limit=None):
        return []

    def fetch_funding_history(self, sym=None, since=None, limit=None, params=None):
        self.calls.append(("funding", sym, since, dict(params or {})))
        return [{"symbol": sym, "amount": -0.25}] if sym in self.positions else []

    def cancel_order(self, oid, sym, params=None):
        cid = (params or {}).get("clientOrderId")
        if cid not in self.algo:
            raise ccxt.OrderNotFound("unknown order")
        del self.algo[cid]
        return {}

    # test helpers
    def fire_stop(self, ex_id):
        sym = f"{ex_id[:-4]}/USDT:USDT"
        cid = next(c for c, o in self.algo.items() if o["symbol"] == sym)
        del self.algo[cid]
        self.positions[sym] = 0.0


def info(*listings: tuple[str, int, list[str] | None, str]) -> dict:
    return {"symbols": [
        {"symbol": s, "onboardDate": t, "underlyingSubType": sub or [], "underlyingType": u, "contractType": "PERPETUAL",
         "quoteAsset": "USDT", "status": "TRADING"} for s, t, sub, u in listings]}


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake = FakeUsdm({"BTCUSDT": 80_000.0, "NEWUSDT": 2.0, "OLDUSDT": 10.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    venue = PerpDemoVenue(CCXTAdapter(demo_config("demo-key", "demo-secret")))
    venue.connect()
    orders = LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY)
    pairs = PairStore.load(tmp_path / "pairs.json", KEY)
    book = Book(venue, orders, pairs, tmp_path / "audit.jsonl", 100.0, sleep=lambda s: None)
    return fake, book, tmp_path


def test_rules_pick_new_non_alpha_coin_perps_in_their_window():
    i = info(("NEWUSDT", T0, None, "COIN"), ("ALPUSDT", T0, ["Alpha"], "COIN"), ("TSLAUSDT", T0, None, "EQUITY"),
             ("LATEUSDT", T0 - 10 * H, None, "COIN"), ("GONEUSDT", T0, None, "COIN"))
    due, skipped = rules.due_entries(i, {"NEWUSDT", "ALPUSDT", "TSLAUSDT", "LATEUSDT"}, T0 + 4 * H, set())
    assert [d.symbol for d in due] == ["NEWUSDT"]
    assert {(s["symbol"], s["reason"]) for s in skipped} == {("LATEUSDT", "missed_window"), ("GONEUSDT", "not_on_demo")}
    assert rules.due_entries(i, {"NEWUSDT"}, T0 + 3 * H, set())[0] == []
    assert rules.due_entries(i, {"NEWUSDT"}, T0 + 4 * H, {"NEWUSDT"})[0] == []


def test_venue_refuses_anything_but_demo(monkeypatch):
    fake = FakeUsdm({"BTCUSDT": 1.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    venue = PerpDemoVenue(CCXTAdapter(demo_config("k", "s")))
    venue.connect()
    fake.urls["api"]["fapiPrivate"] = "https://fapi.binance.com/fapi/v1"
    with pytest.raises(PerpVenueError):
        venue.assert_demo_only()
    with pytest.raises(PerpVenueError):
        PerpDemoVenue(CCXTAdapter(ExchangeConfig(id="binanceusdm", name="x", testnet=True)))


def test_short_position_keeps_its_sign(world):
    fake, book, _ = world
    fake.positions["NEW/USDT:USDT"] = -50.0
    fake.entry["NEW/USDT:USDT"] = 2.0
    assert book.venue.positions()["NEWUSDT"].qty == -50.0


def test_dry_run_sends_nothing(world):
    fake, book, _ = world
    rep = run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=info(("NEWUSDT", T0, None, "COIN")), execute=False)
    assert rep["due"] == ["NEWUSDT"]
    assert not [c for c in fake.calls if c[0] == "order"]


def test_entry_opens_short_hedge_and_exchange_stop_then_exits_after_hold(world):
    fake, book, tmp = world
    i = info(("NEWUSDT", T0, None, "COIN"))
    rep = run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=i, execute=True)
    assert rep["entered"] == ["NEWUSDT"]
    assert fake.positions["NEW/USDT:USDT"] == pytest.approx(-50.0)
    assert fake.positions["BTC/USDT:USDT"] == pytest.approx(0.001)  # 100 USDT / 80k, three decimals
    (stop,) = fake.algo.values()
    assert stop["triggerPrice"] == pytest.approx(3.0) and stop["side"] == "buy"
    assert fake.leverage == {"NEW/USDT:USDT": 1, "BTC/USDT:USDT": 1}
    # a second run inside the hold changes nothing
    rep = run_cycle(book, now_ms=T0 + 30 * H, mainnet_info=i, execute=True)
    assert rep["entered"] == [] and fake.positions["NEW/USDT:USDT"] == pytest.approx(-50.0)
    # after the hold both legs close and the stop is cancelled
    rep = run_cycle(book, now_ms=T0 + (4 + rules.HOLD_H) * H, mainnet_info=i, execute=True)
    assert fake.positions["NEW/USDT:USDT"] == pytest.approx(0) and fake.positions["BTC/USDT:USDT"] == pytest.approx(0)
    assert fake.algo == {}
    pairs = PairStore.load(tmp / "pairs.json", KEY)
    (p,) = pairs.pairs.values()
    assert p["status"] == "closed" and p["close_reason"] == "hold"
    assert book.orders.unfinished_orders() == {}
    assert all(c[5].get("reduceOnly") for c in fake.calls if c[0] == "order" and c[5]["clientOrderId"].endswith("x"))


def test_fired_stop_closes_the_hedge(world):
    fake, book, tmp = world
    i = info(("NEWUSDT", T0, None, "COIN"))
    run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=i, execute=True)
    fake.fire_stop("NEWUSDT")
    run_cycle(book, now_ms=T0 + 20 * H, mainnet_info=i, execute=True)
    assert fake.positions["BTC/USDT:USDT"] == pytest.approx(0)
    (p,) = PairStore.load(tmp / "pairs.json", KEY).pairs.values()
    assert p["close_reason"] == "stop"


def test_stray_position_stops_the_run_before_any_entry(world):
    fake, book, tmp = world
    fake.positions["OLD/USDT:USDT"] = -5.0
    fake.entry["OLD/USDT:USDT"] = 10.0
    with pytest.raises(PerpBookError):
        run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=info(("NEWUSDT", T0, None, "COIN")), execute=True)
    assert not [c for c in fake.calls if c[0] == "order"]
    events = [json.loads(x)["event"] for x in (tmp / "audit.jsonl").read_text().splitlines()]
    assert "perp_unexplained_position" in events


def test_failed_stop_takes_the_pair_off(world):
    fake, book, tmp = world
    fake.fail_next = "stop"
    with pytest.raises(PerpBookError):
        run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=info(("NEWUSDT", T0, None, "COIN")), execute=True)
    assert fake.positions["NEW/USDT:USDT"] == pytest.approx(0)
    assert fake.positions["BTC/USDT:USDT"] == pytest.approx(0)
    (p,) = PairStore.load(tmp / "pairs.json", KEY).pairs.values()
    assert p["status"] == "failed"


def test_uncertain_submission_is_reconciled_by_client_key(world):
    fake, book, tmp = world
    short_key = f"pp-NEWUSDT-{T0 // 1000:x}-s"
    fake.fail_next = short_key
    original = fake.create_order

    def sent_then_timeout(sym, typ, side, amount, price=None, params=None):
        if params and params.get("clientOrderId") == fake.fail_next:
            fake.fail_next = None
            original(sym, typ, side, amount, price, params)   # the venue took it
            raise ccxt.NetworkError("timeout")
        return original(sym, typ, side, amount, price, params)

    fake.create_order = sent_then_timeout
    i = info(("NEWUSDT", T0, None, "COIN"))
    with pytest.raises(PerpBookError):
        run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=i, execute=True)
    # The venue took the short, so the failed open took it off again.
    assert fake.positions["NEW/USDT:USDT"] == pytest.approx(0)
    # The next run settles the uncertain intent by its key and finds the book flat.
    book2 = Book(book.venue, LiveRiskStateStore(tmp / "order_state.json", integrity_key=KEY),
                 PairStore.load(tmp / "pairs.json", KEY), tmp / "audit.jsonl", 100.0, sleep=lambda s: None)
    rep = run_cycle(book2, now_ms=T0 + 5 * H, mainnet_info=i, execute=True)
    assert book2.orders.state.order_ledger[short_key]["status"] == "filled"
    assert rep["entered"] == [] and book2.orders.unfinished_orders() == {}


def test_tampered_pair_store_is_refused(world):
    fake, book, tmp = world
    run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=info(("NEWUSDT", T0, None, "COIN")), execute=True)
    raw = json.loads((tmp / "pairs.json").read_text())
    next(iter(raw["pairs"].values()))["short_qty"] = 1.0
    (tmp / "pairs.json").write_text(json.dumps(raw))
    with pytest.raises(LiveSafetyError):
        PairStore.load(tmp / "pairs.json", KEY)


def test_rehearsal_opens_on_an_existing_symbol_and_closes_after_its_hold(world):
    fake, book, tmp = world
    run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, rehearse="OLDUSDT", rehearse_hold_h=2)
    assert fake.positions["OLD/USDT:USDT"] == pytest.approx(-10.0)
    run_cycle(book, now_ms=T0 + 2 * H, mainnet_info={}, execute=True)
    assert fake.positions["OLD/USDT:USDT"] == pytest.approx(0)
    with pytest.raises(PerpBookError):
        run_cycle(book, now_ms=T0 + 3 * H, mainnet_info={}, execute=True, rehearse="BTCUSDT")


def test_entry_lock_still_allows_exits(world):
    fake, book, _ = world
    i = info(("NEWUSDT", T0, None, "COIN"))
    rep = run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=i, execute=True, entry_lock="TRADING_ENTRY_KILL_SWITCH is active")
    assert rep["entered"] == [] and not [c for c in fake.calls if c[0] == "order"]


def test_self_generated_key_is_read_from_an_owner_only_pem(tmp_path):
    pem = tmp_path / "ed25519_private.pem"
    pem.write_text("-----BEGIN PRIVATE KEY-----\nMC4CAQAwBQYDK2VwBCIEIAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n-----END PRIVATE KEY-----\n")
    pem.chmod(0o600)
    env = {"BINANCE_DEMO_FUTURES_API_KEY": "api", "BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE": str(pem)}
    key, secret = load_demo_credentials(env)
    assert key == "api" and secret.startswith("-----BEGIN PRIVATE KEY-----") and len(secret) <= 120
    pem.chmod(0o644)
    with pytest.raises(PerpVenueError, match="owner only"):
        load_demo_credentials(env)
    pem.chmod(0o600)
    with pytest.raises(PerpVenueError, match="not both"):
        load_demo_credentials({**env, "BINANCE_DEMO_FUTURES_API_SECRET": "s"})
    assert load_demo_credentials({"BINANCE_DEMO_FUTURES_API_KEY": "api", "BINANCE_DEMO_FUTURES_API_SECRET": "s"}) == ("api", "s")
    with pytest.raises(PerpVenueError):
        load_demo_credentials({"BINANCE_DEMO_FUTURES_API_KEY": "api"})


def test_fill_price_is_read_back_when_the_venue_acks_without_one(world):
    fake, book, tmp = world
    fake.ack_without_price = True
    rep = run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, rehearse="OLDUSDT", rehearse_hold_h=2)
    assert rep["entered"] == ["OLDUSDT"]
    (p,) = PairStore.load(tmp / "pairs.json", KEY).pairs.values()
    assert p["status"] == "open" and p["short_avg"] == pytest.approx(10.0)
    assert fake.positions["BTC/USDT:USDT"] == pytest.approx(0.001)
    (stop,) = fake.algo.values()
    assert stop["triggerPrice"] == pytest.approx(15.0)


def test_a_failure_after_the_short_takes_off_what_the_venue_holds(world):
    fake, book, tmp = world
    fake.fail_next = f"pp-OLDUSDT-{T0 // 1000:x}-h"   # the hedge order times out
    with pytest.raises(Exception):
        run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, rehearse="OLDUSDT", rehearse_hold_h=2)
    assert fake.positions["OLD/USDT:USDT"] == pytest.approx(0)
    (p,) = PairStore.load(tmp / "pairs.json", KEY).pairs.values()
    assert p["status"] == "failed"


def test_hedge_is_sized_to_the_nearest_step(world):
    fake, book, _ = world
    fake.prices["BTCUSDT"] = 60_000.0   # 100 / 60k = 0.00167: down is 0.001, nearest 0.002
    run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, rehearse="OLDUSDT", rehearse_hold_h=2)
    assert fake.positions["BTC/USDT:USDT"] == pytest.approx(0.002)


# ── book LV on the same account ─────────────────────────────────────────────

from trading_agent.execution.perp import lowvol  # noqa: E402
from trading_agent.execution.perp.lowvol import LowVolBook  # noqa: E402

LV_PRICES = {"BTCUSDT": 80_000.0, "NEWUSDT": 2.0, "AAAUSDT": 1.0, "BBBUSDT": 2.0, "CCCUSDT": 5.0,
             "1000PEPEUSDT": 0.01, "EEEUSDT": 4.0, "ETHUSDT": 2_000.0}
MON = lowvol.week_of(T0)[1] + 7 * lowvol.DAY_MS    # a Monday 00:00 UTC
ROLL = MON + 4 * H + 15 * 60_000                    # the hourly run after the 04:00 open
WEEK = 7 * lowvol.DAY_MS


class Killed(BaseException):
    """A run that dies mid-roll without running its except blocks."""


def iso(ms: int) -> str:
    return lowvol.week_of(ms)[0]


def write_picks(path: Path, *rows: tuple[int, list[str], list[str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"monday": iso(m), "long": lo, "short": sh, "ranked": 18}) + "\n" for m, lo, sh in rows))


@pytest.fixture
def lvworld(tmp_path, monkeypatch):
    fake = FakeUsdm(LV_PRICES)
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    venue = PerpDemoVenue(CCXTAdapter(demo_config("demo-key", "demo-secret")))
    venue.connect()
    orders = LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY)
    book = Book(venue, orders, PairStore.load(tmp_path / "pairs.json", KEY), tmp_path / "audit.jsonl", 100.0, sleep=lambda s: None)
    picks = tmp_path / "selection" / "lv_book" / "picks.jsonl"
    lv = LowVolBook(book, PairStore.load(tmp_path / "lv_blocks.json", KEY), 1000.0, picks, lambda c: c in {"ETH"})
    return fake, book, lv, picks


def cycle(book, lv, at, **kw):
    return run_cycle(book, now_ms=at, mainnet_info=kw.pop("mainnet_info", {}), execute=True, lv=lv, **kw)


def pos(fake, ex_id):
    return fake.positions.get(f"{ex_id[:-4]}/USDT:USDT", 0.0)


def lv_orders(fake, tag=""):
    return [c for c in fake.calls if c[0] == "order" and c[5]["clientOrderId"].startswith(f"lv{tag}")]


def test_week_of_finds_the_monday():
    monday, ms = lowvol.week_of(ROLL)
    assert ms == MON and monday == iso(MON)
    assert lowvol.week_of(MON - 1)[1] == MON - WEEK
    assert lowvol.perp_symbol("PEPE", {"1000PEPEUSDT"}) == "1000PEPEUSDT"
    assert lowvol.perp_symbol("XAUT", {"PAXGUSDT"}) is None


def test_lv_rolls_into_the_pick_in_its_window_with_stops_on_its_shorts(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE", "XAUT"]))
    assert cycle(book, lv, MON + 3 * H)["lv"] == {"week": iso(MON)}       # before 04:00: nothing
    assert not lv_orders(fake)
    rep = cycle(book, lv, ROLL)
    assert rep["lv"]["rolled"] and rep["lv"]["long"] == 2 and rep["lv"]["short"] == 2
    # 1000 USDT book, half a side, equal weight over the names the venue lists
    assert pos(fake, "AAAUSDT") == pytest.approx(250) and pos(fake, "BBBUSDT") == pytest.approx(125)
    assert pos(fake, "CCCUSDT") == pytest.approx(-50) and pos(fake, "1000PEPEUSDT") == pytest.approx(-25_000)
    stops = {o["symbol"]: o for o in fake.algo.values()}
    assert set(stops) == {"CCC/USDT:USDT", "1000PEPE/USDT:USDT"}
    assert stops["CCC/USDT:USDT"]["triggerPrice"] == pytest.approx(7.5) and stops["CCC/USDT:USDT"]["side"] == "buy"
    (block,) = PairStore.load(lv.blocks.path, KEY).pairs.values()
    assert block["status"] == "open" and block["skipped"] == {"XAUT": "not_on_demo"}
    # later runs that week, inside or after the window, change nothing; the pair book sees no stray
    n = len(fake.calls)
    cycle(book, lv, ROLL + H)
    cycle(book, lv, ROLL + 30 * H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]


def test_lv_next_roll_trades_only_the_difference(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + WEEK, ["AAA", "CCC"], ["BBB", "EEE"]))
    cycle(book, lv, ROLL)
    n = len(fake.calls)
    cycle(book, lv, ROLL + WEEK)
    week2 = [c for c in fake.calls[n:] if c[0] == "order"]
    assert not [c for c in week2 if c[1] == "AAA/USDT:USDT"]             # carried at its target: no trade
    assert pos(fake, "AAAUSDT") == pytest.approx(250)
    assert pos(fake, "CCCUSDT") == pytest.approx(50) and pos(fake, "BBBUSDT") == pytest.approx(-125)
    assert pos(fake, "EEEUSDT") == pytest.approx(-62.5) and pos(fake, "1000PEPEUSDT") == pytest.approx(0)
    # a flip goes through flat: the reduce-only close comes before the opening order
    ccc = [c for c in week2 if c[1] == "CCC/USDT:USDT" and "stopLossPrice" not in c[5]]
    assert [c[5].get("reduceOnly", False) for c in ccc] == [True, False]
    assert {o["symbol"] for o in fake.algo.values()} == {"BBB/USDT:USDT", "EEE/USDT:USDT"}
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    assert sorted(b["status"] for b in blocks.values()) == ["closed", "open"]
    assert all(k.endswith("-st") for k in book.orders.unfinished_orders())   # only the two live stops


def test_lv_fired_stop_leaves_the_short_flat_until_the_next_roll(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "PEPE"]))
    cycle(book, lv, ROLL)
    fake.fire_stop("CCCUSDT")
    n = len(fake.calls)
    cycle(book, lv, ROLL + 30 * H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    (block,) = PairStore.load(lv.blocks.path, KEY).pairs.values()
    assert block["legs"]["CCCUSDT"]["state"] == "stopped"
    assert book.orders.state.order_ledger[block["legs"]["CCCUSDT"]["stop_key"]]["status"] == "filled"
    cycle(book, lv, ROLL + WEEK)
    assert pos(fake, "CCCUSDT") == pytest.approx(-50)                      # back in at the next roll


def test_lv_week_without_a_pick_ends_flat_at_the_window_end(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]))
    cycle(book, lv, ROLL)
    assert "missed" not in cycle(book, lv, ROLL + WEEK)["lv"]               # still inside the next window
    assert pos(fake, "AAAUSDT") == pytest.approx(250)
    rep = cycle(book, lv, MON + WEEK + 6 * H + 60_000)
    assert rep["lv"]["missed"] == "no_pick" and rep["lv"]["closed"] == "hold"
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    closes = [c for c in lv_orders(fake) if c[5]["clientOrderId"].endswith("c0")]
    assert closes and all(c[5].get("reduceOnly") for c in closes)
    assert book.orders.unfinished_orders() == {}
    assert ("funding", "AAA/USDT:USDT") in {c[:2] for c in fake.calls if c[0] == "funding"}


def test_lv_entry_lock_skips_the_roll_but_not_the_exit(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "PEPE"]))
    cycle(book, lv, ROLL)
    lock = "TRADING_ENTRY_KILL_SWITCH is active"
    n = len(fake.calls)
    cycle(book, lv, ROLL + WEEK, entry_lock=lock)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    rep = cycle(book, lv, MON + WEEK + 6 * H + 60_000, entry_lock=lock)
    assert rep["lv"]["missed"] == "entry_lock" and rep["lv"]["closed"] == "hold"
    assert all(abs(q) < 1e-9 for q in fake.positions.values())


def test_lv_failed_stop_flattens_the_roll_and_never_retries_the_week(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]))
    fake.fail_next = "stop"
    with pytest.raises(PerpBookError):
        cycle(book, lv, ROLL)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    (block,) = PairStore.load(lv.blocks.path, KEY).pairs.values()
    assert block["status"] == "failed"
    fake.fail_next = None
    n = len(fake.calls)
    cycle(book, lv, ROLL + H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    assert book.orders.unfinished_orders() == {}


def test_lv_roll_killed_midway_is_flattened_by_the_next_run(lvworld, tmp_path):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]))
    original = fake.create_order

    def dies_on_the_third(sym, typ, side, amount, price=None, params=None):
        out = original(sym, typ, side, amount, price, params)
        if len([c for c in fake.calls if c[0] == "order"]) == 3:
            raise Killed()       # the venue took the order; the process died before recording it
        return out

    fake.create_order = dies_on_the_third
    with pytest.raises(Killed):
        cycle(book, lv, ROLL)
    fake.create_order = original
    assert any(abs(q) > 0 for q in fake.positions.values())
    # a new process: its stores come from disk
    orders = LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY)
    book2 = Book(book.venue, orders, PairStore.load(tmp_path / "pairs.json", KEY), tmp_path / "audit.jsonl", 100.0, sleep=lambda s: None)
    lv2 = LowVolBook(book2, PairStore.load(lv.blocks.path, KEY), 1000.0, picks, lambda c: False)
    cycle(book2, lv2, ROLL + H)
    assert all(abs(q) < 1e-9 for q in fake.positions.values())
    (block,) = PairStore.load(lv.blocks.path, KEY).pairs.values()
    assert block["status"] == "failed" and block["close_reason"] == "interrupted"
    events = [json.loads(x)["event"] for x in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert "perp_lv_roll_interrupted" in events


def test_lv_refuses_a_masked_coin_before_any_order(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "ETH"]))
    with pytest.raises(PerpBookError, match="masked"):
        cycle(book, lv, ROLL)
    assert not lv_orders(fake)


def test_lv_too_big_for_the_account_misses_the_week(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]))
    lv.book_usd = 5000.0
    fake.fetch_balance = lambda: {"info": {"totalMarginBalance": "1000"}}
    assert cycle(book, lv, ROLL)["lv"]["missed"] == "equity"
    assert not lv_orders(fake)


def test_pair_book_and_lv_share_the_account(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]))
    i = info(("NEWUSDT", MON, None, "COIN"))
    rep = cycle(book, lv, ROLL, mainnet_info=i)
    assert rep["entered"] == ["NEWUSDT"] and rep["lv"]["rolled"]
    cycle(book, lv, ROLL + H, mainnet_info=i)                             # each book explains its own legs
    fake.positions["EEE/USDT:USDT"] = 3.0                                 # a stray is still refused
    fake.entry["EEE/USDT:USDT"] = 4.0
    with pytest.raises(PerpBookError):
        cycle(book, lv, ROLL + 2 * H, mainnet_info=i)


def test_lv_rehearsal_rolls_now_and_closes_after_its_hold(lvworld):
    fake, book, lv, picks = lvworld
    t = MON + 2 * lowvol.DAY_MS
    rep = lv.rehearse(["AAA"], ["CCC"], t, 2)
    assert rep["rolled"].startswith("r")
    assert pos(fake, "AAAUSDT") == pytest.approx(500) and pos(fake, "CCCUSDT") == pytest.approx(-100)
    cycle(book, lv, t + H)
    assert pos(fake, "AAAUSDT") == pytest.approx(500)
    cycle(book, lv, t + 2 * H)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    write_picks(picks, (MON + WEEK, ["AAA", "BBB"], ["CCC", "PEPE"]))
    cycle(book, lv, ROLL + WEEK)
    with pytest.raises(PerpBookError, match="weekly"):
        lv.rehearse(["AAA"], ["CCC"], ROLL + WEEK + H, 2)


def test_lv_dry_run_reports_the_pick_and_sends_nothing(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE", "XAUT"]))
    rep = run_cycle(book, now_ms=ROLL, mainnet_info={}, execute=False, lv=lv)
    assert rep["lv"] == {"week": iso(MON), "in_window": True, "pick": True, "long": 2, "short": 2,
                         "skipped": {"XAUT": "not_on_demo"}}
    assert not [c for c in fake.calls if c[0] == "order"]


# ── shadow ledger on mainnet prices ─────────────────────────────────────────

from tools.newperp import fetch as nfx  # noqa: E402
from tools.newperp import shadow  # noqa: E402

M1 = 60_000
L0 = T0 - T0 % H   # listings open on the hour


class Mainnet:
    """Public mainnet calls: NEW lists at T0, trades at 2.0 and touches 3.2 at L0 + 30 h; SLOW never stops."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, timeout):
        nfx.check_url(url)
        u = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        self.calls.append(u.path)
        if u.path == nfx.EXCHANGE_INFO:
            return 200, json.dumps(info(("NEWUSDT", L0, None, "COIN"), ("SLOWUSDT", L0, None, "COIN"),
                                        ("ALPUSDT", L0, ["Alpha"], "COIN"))).encode()
        if u.path == nfx.FUNDING:
            return 200, b"[]"
        a, b = int(q["startTime"]), int(q["endTime"])
        rows = []
        for t in range(a, min(b + 1, a + 1500 * M1), M1):
            px = {"BTCUSDT": 80_000.0, "NEWUSDT": 2.0, "SLOWUSDT": 5.0}[q["symbol"]]
            hi = 3.2 if q["symbol"] == "NEWUSDT" and t == L0 + 30 * H else px
            rows.append([t, str(px), str(hi), str(px), str(px), "10", t + M1 - 1, str(10 * px), 1, "0", "0", "0"])
        return 200, json.dumps(rows).encode()


def shadow_client(venue):
    return nfx.Client(venue, pace=0.0, sleep=lambda s: None)


def read_ledger(root):
    return [json.loads(x) for x in shadow.ledger_path(root).read_text().splitlines()]


def test_shadow_opens_due_listings_then_records_stop_and_hold(tmp_path):
    venue = Mainnet()
    c = shadow_client(venue)
    i = c.get(nfx.EXCHANGE_INFO, {})
    first = shadow.step(c, i, L0 + 4 * H + 5 * M1 + 7, tmp_path)
    assert first == {"opened": 2, "closed": 0, "skipped": 0}
    opened = {r["symbol"]: r for r in read_ledger(tmp_path)}
    assert set(opened) == {"NEWUSDT", "SLOWUSDT"} and opened["NEWUSDT"]["entry"] == 2.0 and opened["NEWUSDT"]["delay_min"] == 5
    assert shadow.step(c, i, L0 + 20 * H, tmp_path) == {"opened": 0, "closed": 0, "skipped": 0}
    assert shadow.step(c, i, L0 + 31 * H, tmp_path)["closed"] == 1
    stop = [r for r in read_ledger(tmp_path) if r["event"] == "shadow_close"][0]
    assert stop["symbol"] == "NEWUSDT" and stop["reason"] == "stop" and stop["stop"]["f1"] == pytest.approx(1.5)
    end = L0 + 4 * H + 5 * M1 + rules.HOLD_H * H
    assert shadow.step(c, i, end + M1, tmp_path)["closed"] == 1
    hold = [r for r in read_ledger(tmp_path) if r["event"] == "shadow_close"][1]
    assert hold["symbol"] == "SLOWUSDT" and hold["reason"] == "hold" and hold["exit"] == 5.0 and hold["t"] == end
    assert shadow.load(tmp_path)["open"] == {}
    assert set(venue.calls) <= {nfx.EXCHANGE_INFO, nfx.KLINES, nfx.FUNDING}


def test_shadow_records_a_listing_first_seen_after_its_window(tmp_path):
    c = shadow_client(Mainnet())
    out = shadow.step(c, c.get(nfx.EXCHANGE_INFO, {}), L0 + 7 * H, tmp_path)
    assert out == {"opened": 0, "closed": 0, "skipped": 2}
    assert {r["reason"] for r in read_ledger(tmp_path)} == {"missed_window"}


# ── hourly wrapper and task ─────────────────────────────────────────────────

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "ops" / "run_perp_demo.sh"
TASKS = REPO / "scripts" / "ops" / "windows" / "register_tasks.ps1"


def wrapper_at(tmp_path: Path, hhmm: str, job: str) -> subprocess.CompletedProcess:
    (tmp_path / "scripts" / "ops").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    script = tmp_path / "scripts" / "ops" / "run_perp_demo.sh"
    script.write_text(WRAPPER.read_text(encoding="utf8"), encoding="utf8")
    script.chmod(0o755)
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text("#!/usr/bin/env bash\n" + job, encoding="utf8")
    py.chmod(0o755)
    stand = tmp_path / "bin"
    stand.mkdir(exist_ok=True)
    (stand / "date").write_text(f'#!/usr/bin/env bash\ncase "$*" in *%H*) echo {hhmm[:2]};; *%M*) echo {hhmm[2:]};; *) /bin/date "$@";; esac\n', encoding="utf8")
    (stand / "date").chmod(0o755)
    return subprocess.run([str(script)], env={**os.environ, "PATH": f"{stand}:{os.environ['PATH']}"}, timeout=60, check=False)


@pytest.mark.parametrize("hhmm", ["1050", "1100", "1105"])
def test_the_wrapper_keeps_out_of_the_testnet_window(tmp_path: Path, hhmm: str) -> None:
    assert wrapper_at(tmp_path, hhmm, "touch ran\n").returncode == 0
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("hhmm", ["1015", "1115"])
def test_the_wrapper_runs_the_book_with_shadow_and_logs_its_end(tmp_path: Path, hhmm: str) -> None:
    assert wrapper_at(tmp_path, hhmm, 'echo "$TRADING_MODE $*"; exit 3\n').returncode == 3
    lines = (tmp_path / "logs" / "perp_demo.log").read_text(encoding="utf8").splitlines()
    assert lines[1] == "testnet -u scripts/live_perp_demo.py --execute --shadow --lv --pair-usd 500"
    assert lines[2] == "testnet -u scripts/live_spot_hold_demo.py --execute"
    assert lines[3] == " -u -m tools.learnpaper.run"
    assert re.fullmatch(r"=== \S+Z perp_demo end rc=3", lines[4])
    assert not re.search(r"^[^#]*\btimeout\b", WRAPPER.read_text(encoding="utf8"), re.M)


def test_the_task_runs_the_wrapper_hourly_and_headless() -> None:
    text = TASKS.read_text(encoding="utf8")
    assert '-TaskName "TradingAgent-PerpDemo" -Action $perpdemo' in text
    assert "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_perp_demo.sh" in text
    assert re.search(r"perpdemoTrigger = .*?-RepetitionInterval \(New-TimeSpan -Hours 1\)", text, re.S)
