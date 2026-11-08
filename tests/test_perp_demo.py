"""The new perp pair book on a fake Binance USD-M demo venue: no network."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
from decimal import ROUND_DOWN, Decimal
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
        # ccxt truncates the amount to the step before it sends: 0.19999999999999998 goes out as 0.199
        amount = float(Decimal(repr(float(amount))).quantize(Decimal("0.001"), rounding=ROUND_DOWN))
        if amount <= 0:
            raise ccxt.InvalidOrder("amount must be greater than minimum amount precision of 0.001")
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
    exits = [c for c in fake.calls if c[0] == "order" and re.search(r"-[sh]x\d+$", c[5]["clientOrderId"])]
    assert len(exits) == 2 and all(c[5].get("reduceOnly") for c in exits)


def fresh_book(book: Book, tmp: Path) -> Book:
    """The pair book of a new process: every store read again from disk."""

    return Book(book.venue, LiveRiskStateStore(tmp / "order_state.json", integrity_key=KEY),
                PairStore.load(tmp / "pairs.json", KEY), tmp / "audit.jsonl", 100.0, sleep=lambda s: None)


@pytest.mark.parametrize("cut", ["hedge_reply_lost", "short_reserved_then_killed", "short_reply_lost"])
def test_a_pair_exit_cut_off_after_any_order_is_finished_by_the_next_run(world, monkeypatch, cut):
    """Exit keys are fresh, and a try that filled is read from the ledger, so a cut never jams the account."""

    fake, book, tmp = world
    i = info(("NEWUSDT", T0, None, "COIN"))
    run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=i, execute=True)
    original, update = fake.create_order, LiveRiskStateStore.update_order
    part = "-hx" if cut == "hedge_reply_lost" else "-sx"
    if cut.endswith("reply_lost"):
        def create(sym, typ, side, amount, price=None, params=None):
            out = original(sym, typ, side, amount, price, params)
            if part in (params or {}).get("clientOrderId", ""):
                raise ccxt.NetworkError("reply lost")      # the venue filled it; the answer never came
            return out
        fake.create_order = create
    else:
        def killed(self, key, *a, **k):
            if part in key and k.get("status") == "submitted":
                raise Killed()                             # the reserve is saved; the order never left
            return update(self, key, *a, **k)
        monkeypatch.setattr(LiveRiskStateStore, "update_order", killed)
    end = T0 + (4 + rules.HOLD_H) * H
    with pytest.raises((PerpBookError, Killed)):
        run_cycle(fresh_book(book, tmp), now_ms=end, mainnet_info=i, execute=True)
    fake.create_order = original
    monkeypatch.setattr(LiveRiskStateStore, "update_order", update)
    for k in (1, 2):
        b = fresh_book(book, tmp)
        run_cycle(b, now_ms=end + k * H, mainnet_info=i, execute=True)
        assert b.orders.unfinished_orders() == {}
    assert pos(fake, "NEWUSDT") == 0 and pos(fake, "BTCUSDT") == pytest.approx(0, abs=1e-12) and fake.algo == {}
    (p,) = PairStore.load(tmp / "pairs.json", KEY).pairs.values()
    assert p["status"] == "closed" and p["close_reason"] == "hold" and p["hedge_exit"] > 0 and p["short_exit"] > 0
    sells = [c for c in orders_sent(fake) if c[1] == "BTC/USDT:USDT" and c[3] == "sell"]
    assert len(sells) == 1                                 # the hedge is sold once


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


def test_lv_close_cut_off_after_its_stop_is_cancelled_is_finished_by_the_next_run(lvworld):
    """A read error between the stop's cancel and the buy-back: the close was recorded first, so it is finished."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]))
    cycle(book, lv, ROLL)
    assert pos(fake, "CCCUSDT") == pytest.approx(-100)
    exit_ms = lv.active()[1]["exit_ms"]
    real_cancel, real_open = fake.cancel_order, fake.fetch_open_orders
    cut = {"armed": False}

    def cancel(oid, sym, params=None):
        out = real_cancel(oid, sym, params)
        cut["armed"] = True
        return out

    def open_orders(sym=None, since=None, limit=None, params=None):
        if cut["armed"]:
            cut["armed"] = False
            raise ccxt.NetworkError("read timeout")       # the read after the cancel
        return real_open(sym, since, limit, params)

    fake.cancel_order, fake.fetch_open_orders = cancel, open_orders
    with pytest.raises(Exception):
        cycle(book, lv, exit_ms + 15 * 60_000)
    fake.cancel_order, fake.fetch_open_orders = real_cancel, real_open
    assert pos(fake, "CCCUSDT") == pytest.approx(-100) and fake.algo == {}     # a short without its stop
    lv2 = LowVolBook(book, PairStore.load(lv.blocks.path, KEY), 1000.0, picks, lambda c: c in {"ETH"})
    cycle(book, lv2, exit_ms + H + 15 * 60_000)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    (block,) = [b for b in PairStore.load(lv.blocks.path, KEY).pairs.values() if b.get("legs")]
    assert block["status"] == "closed" and all(leg["state"] == "closed" for leg in block["legs"].values())
    assert book.orders.unfinished_orders() == {}
    assert "perp_lv_close_finished" in [e["event"] for e in events(book.audit_path.parent)]


def test_lv_close_settled_from_a_ledger_without_a_price_is_priced(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]))
    cycle(book, lv, ROLL)
    fake.ack_without_price = True                         # the close fills with average 0; the run dies reading it back
    orig = fake.fetch_order

    def fetch(oid, sym, params=None):
        if ((params or {}).get("origClientOrderId") or "").startswith("lv"):
            raise Killed()
        return orig(oid, sym, params)

    fake.fetch_order = fetch
    end = MON + WEEK + 6 * H + 60_000
    with pytest.raises(Killed):
        cycle(book, lv, end)
    fake.fetch_order, fake.ack_without_price = orig, False
    (key,) = [k for k, v in book.orders.state.order_ledger.items() if k.startswith("lv") and k.endswith("-c0")]
    assert book.orders.state.order_ledger[key]["average_fill_price"] == 0.0
    lv2 = LowVolBook(fresh_book(book, book.audit_path.parent), PairStore.load(lv.blocks.path, KEY), 1000.0, picks,
                     lambda c: False)
    cycle(lv2.book, lv2, end + H)
    legs = {s: leg for b in PairStore.load(lv.blocks.path, KEY).pairs.values() for s, leg in b["legs"].items()}
    settled = [leg for leg in legs.values() if leg.get("exit_from")]
    assert len(settled) == 1 and settled[0]["exit"] == pytest.approx(LV_PRICES["1000PEPEUSDT"])
    assert settled[0]["exit_from"] == "venue"


def thin_book(fake, ex_id: str, *filled: float, lost: bool = False) -> list[str]:
    """A thin Demo Trading book: the venue fills only ``filled[i]`` of the i-th LV reduce-only market order on
    ``ex_id`` and expires the rest. ``lost``: the venue acts and the reply never comes back. Orders after the
    last given fill go through whole. Returns the client keys it cut, in order."""

    original, todo, cut = fake.create_order, list(filled), []
    sym = f"{ex_id[:-4]}/USDT:USDT"

    def create(s, typ, side, amount, price=None, params=None):
        p = params or {}
        cid = str(p.get("clientOrderId") or "")
        if not todo or s != sym or not cid.startswith("lv") or not p.get("reduceOnly") or "stopLossPrice" in p:
            return original(s, typ, side, amount, price, params)
        q = todo.pop(0)
        fake.calls.append(("order", s, typ, side, amount, dict(p)))
        fake.positions[s] += q if side == "buy" else -q
        px = fake.prices[ex_id]
        fake.orders[cid] = {"id": f"t{len(fake.orders)}", "clientOrderId": cid, "symbol": s, "type": "market",
                            "side": side, "amount": amount, "filled": q, "average": px, "cost": q * px,
                            "status": "expired", "reduceOnly": True}
        cut.append(cid)
        if lost:
            raise ccxt.NetworkError("timeout")
        return dict(fake.orders[cid])

    fake.create_order = create
    return cut


def lv_reload(lv, picks):
    """The next process's LV book: its store from disk."""

    return LowVolBook(lv.book, PairStore.load(lv.blocks.path, KEY), 1000.0, picks, lambda c: c in {"ETH"})


def lv_block(lv) -> dict:
    (block,) = [b for b in PairStore.load(lv.blocks.path, KEY).pairs.values() if b.get("legs")]
    return block


@pytest.mark.parametrize("lost", [False, True], ids=["expired", "reply_lost"])
def test_lv_short_close_filled_in_part_is_recorded_and_finished_by_the_next_run(lvworld, lost):
    """At the hold end the venue buys back 40 of the 100 short and expires the rest (or the reply is lost and the
    reconcile reads that back). The next run records the 40 from the order ledger and buys back the 60 left."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]))
    cycle(book, lv, ROLL)
    assert pos(fake, "CCCUSDT") == pytest.approx(-100)
    exit_ms = lv.active()[1]["exit_ms"]
    cut = thin_book(fake, "CCCUSDT", 40.0, lost=lost)
    with pytest.raises(PerpBookError, match="outcome unknown" if lost else "ended expired"):
        cycle(book, lv, exit_ms + 15 * 60_000)
    assert pos(fake, "CCCUSDT") == pytest.approx(-60) and fake.algo == {}       # a short without its stop
    assert book.orders.state.order_ledger[cut[0]]["status"] == ("reconciling" if lost else "expired")
    cycle(book, lv_reload(lv, picks), exit_ms + H + 15 * 60_000)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    block = lv_block(lv)
    leg = block["legs"]["CCCUSDT"]
    assert block["status"] == "closed" and all(x["state"] == "closed" for x in block["legs"].values())
    assert leg["qty"] == 0.0 and leg["fills"][1:] == [[cut[0], 40.0, 5.0], [cut[0][:-1] + "1", 60.0, 5.0]]
    buys = [c for c in lv_orders(fake) if c[1] == "CCC/USDT:USDT" and c[3] == "buy" and "stopLossPrice" not in c[5]]
    assert [(c[4], c[5].get("reduceOnly")) for c in buys] == [(100.0, True), (60.0, True)]   # never more than held
    assert book.orders.state.order_ledger[leg["stop_key"]]["status"] == "cancelled"
    assert book.orders.unfinished_orders() == {}
    assert "perp_lv_close_finished" in [e["event"] for e in events(book.audit_path.parent)]


def test_lv_long_close_filled_in_part_is_recorded_and_finished_by_the_next_run(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]))
    cycle(book, lv, ROLL)
    exit_ms = lv.active()[1]["exit_ms"]
    cut = thin_book(fake, "AAAUSDT", 100.0)                 # 100 of the 250 long sold, the rest expires
    with pytest.raises(PerpBookError, match="ended expired"):
        cycle(book, lv, exit_ms + 15 * 60_000)
    assert pos(fake, "AAAUSDT") == pytest.approx(150) and pos(fake, "CCCUSDT") == pytest.approx(-100)
    assert len(fake.algo) == 1                              # the short it never reached keeps its stop
    cycle(book, lv_reload(lv, picks), exit_ms + H + 15 * 60_000)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    block = lv_block(lv)
    assert block["status"] == "closed" and all(x["state"] == "closed" for x in block["legs"].values())
    leg = block["legs"]["AAAUSDT"]
    assert leg["qty"] == 0.0 and leg["fills"][1:] == [[cut[0], 100.0, 1.0], [cut[0][:-1] + "1", 150.0, 1.0]]
    sells = [c for c in lv_orders(fake) if c[1] == "AAA/USDT:USDT" and c[3] == "sell"]
    assert [(c[4], c[5].get("reduceOnly")) for c in sells] == [(250.0, True), (150.0, True)]
    assert book.orders.unfinished_orders() == {}


@pytest.mark.parametrize("lost", [False, True], ids=["expired", "reply_lost"])
def test_lv_roll_cut_filled_in_part_is_recorded_and_flattened(lvworld, lost):
    """A roll's cut fills 40 of 83.333 and expires: the roll is flattened, and every fill lands on its leg.

    Expired: the flatten's own sell also fills in part, so the block stays rolling and the next run records it
    and sells the rest. The short CCC, its stop cancelled by the roll, is bought back first in the same run.
    Reply lost: the flatten goes through, and the next run records the cut after the reconcile.
    """

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]))
    cycle(book, lv, ROLL)
    assert pos(fake, "AAAUSDT") == pytest.approx(250)
    cut = thin_book(fake, "AAAUSDT", 40.0, lost=True) if lost else thin_book(fake, "AAAUSDT", 40.0, 100.0)
    with pytest.raises(PerpBookError, match="outcome unknown" if lost else "ended expired"):
        cycle(book, lv, ROLL + WEEK)
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    if lost:
        assert all(abs(q) < 1e-9 for q in fake.positions.values()) and blocks[bid]["status"] == "failed"
    else:
        assert pos(fake, "AAAUSDT") == pytest.approx(110) and blocks[bid]["status"] == "rolling"
        assert pos(fake, "CCCUSDT") == pytest.approx(0) and fake.algo == {}      # no short left without its stop
    cycle(book, lv_reload(lv, picks), ROLL + WEEK + H)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "unsettled" not in b
    rows = sorted(b["legs"]["AAAUSDT"]["fills"])
    f = f"lv{bid}-AAAUSDT-f"
    want = [[cut[0], 40.0, 1.0], [f + "0", 210.0, 1.0]] if lost else \
        [[cut[0], 40.0, 1.0], [f + "0", 100.0, 1.0], [f + "1", 110.0, 1.0]]
    assert rows == want
    assert all(leg["qty"] == 0.0 and leg["state"] == "closed" for leg in b["legs"].values())
    assert all(leg.get("closing_key") is None for leg in b["legs"].values())
    assert sum(r[1] for r in b["legs"]["CCCUSDT"]["fills"]) == pytest.approx(100.0)
    sells = [c[4] for c in lv_orders(fake) if c[1] == "AAA/USDT:USDT" and c[3] == "sell"]
    assert sells == ([83.333, 210.0] if lost else [83.333, 210.0, 110.0])        # each at most what the venue held
    assert book.orders.unfinished_orders() == {}


def test_lv_short_without_its_stop_the_ledger_does_not_explain_stops_for_review(lvworld):
    """After the ledger's fills the venue must hold the leg's rest exactly; otherwise nothing is sent."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]))
    cycle(book, lv, ROLL)
    exit_ms = lv.active()[1]["exit_ms"]
    cut = thin_book(fake, "CCCUSDT", 99.7)                    # -100 + 99.7 is -0.29999999999999716 in floats
    with pytest.raises(PerpBookError):
        cycle(book, lv, exit_ms + 15 * 60_000)
    fake.positions["CCC/USDT:USDT"] = -0.2                    # 0.1 bought back that the ledger does not hold
    n = len(fake.calls)
    with pytest.raises(PerpBookError, match="no open stop"):
        cycle(book, lv_reload(lv, picks), exit_ms + H + 15 * 60_000)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    leg = lv_block(lv)["legs"]["CCCUSDT"]
    assert leg["qty"] == -0.3 and leg["fills"][-1] == [cut[0], 99.7, 5.0] and leg["closing_key"] is None


def test_lv_settle_records_a_terminal_fill_once_and_leaves_an_open_one(lvworld):
    fake, book, lv, picks = lvworld
    key = "lvx-CCCUSDT-c0"
    leg = {"qty": -100.0, "state": "held", "closing_key": key}
    b = {"legs": {"CCCUSDT": leg}}
    book.orders.reserve_order(key, symbol="CCCUSDT", side="buy", quantity=100.0, venue_client_order_id=key)
    for status in ("submitted", "acknowledged"):
        book.orders.update_order(key, status=status)
    book.orders.update_order(key, status="partial", filled_quantity=40.0, average_fill_price=5.0)
    assert lv._settle("x", b) == {} and leg["qty"] == -100.0 and leg["closing_key"] == key   # its outcome is not known yet
    book.orders.update_order(key, status="expired", filled_quantity=40.0, average_fill_price=5.0)
    assert lv._settle("x", b) == {"CCCUSDT": (5.0, "")}
    assert leg == {"qty": -60.0, "state": "held", "closing_key": None, "fills": [[key, 40.0, 5.0]]}
    assert lv._settle("x", b) == {} and leg["qty"] == -60.0                                     # recorded once


def read_fails_after_a_cancel(fake):
    """The venue cancels a stop, then the next read of open stops times out: the stop's intent stays open.
    Returns the function that puts the venue back."""

    real_cancel, real_open = fake.cancel_order, fake.fetch_open_orders
    armed = {"on": False}

    def cancel(oid, sym, params=None):
        out = real_cancel(oid, sym, params)
        armed["on"] = True
        return out

    def open_orders(sym=None, since=None, limit=None, params=None):
        if armed["on"]:
            armed["on"] = False
            raise ccxt.NetworkError("read timeout")
        return real_open(sym, since, limit, params)

    fake.cancel_order, fake.fetch_open_orders = cancel, open_orders

    def restore():
        fake.cancel_order, fake.fetch_open_orders = real_cancel, real_open

    return restore


def test_lv_close_settled_to_zero_closes_the_stop_intent_its_cut_off_cancel_left_open(lvworld):
    """The read after the stop's cancel fails (its intent stays open); the next run's buy-back fills but its reply is
    lost; the run after records it from the ledger, marks the leg closed and closes the stop's intent."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]))
    cycle(book, lv, ROLL)
    exit_ms = lv.active()[1]["exit_ms"]
    restore = read_fails_after_a_cancel(fake)
    with pytest.raises(Exception):
        cycle(book, lv, exit_ms + 15 * 60_000)
    restore()
    stop_key = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    assert pos(fake, "CCCUSDT") == pytest.approx(-100) and fake.algo == {}
    assert book.orders.state.order_ledger[stop_key]["status"] == "open"
    original = fake.create_order

    def lost(sym, typ, side, amount, price=None, params=None):
        out = original(sym, typ, side, amount, price, params)
        if sym == "CCC/USDT:USDT":
            raise ccxt.NetworkError("timeout")                # the whole buy-back fills; its reply never comes
        return out

    fake.create_order = lost
    with pytest.raises(PerpBookError, match="outcome unknown"):
        cycle(book, lv_reload(lv, picks), exit_ms + H + 15 * 60_000)
    fake.create_order = original
    assert pos(fake, "CCCUSDT") == pytest.approx(0)
    cycle(book, lv_reload(lv, picks), exit_ms + 2 * H + 15 * 60_000)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    block = lv_block(lv)
    leg = block["legs"]["CCCUSDT"]
    assert block["status"] == "closed" and leg["state"] == "closed" and leg["qty"] == 0.0 and leg["exit"] == 5.0
    assert book.orders.state.order_ledger[stop_key]["status"] == "cancelled"
    assert book.orders.unfinished_orders() == {}


def test_lv_short_settled_to_zero_with_its_stop_still_open_stops_for_review(lvworld):
    """A stale read lets the close buy back without cancelling the stop, and the run dies before recording it. The
    next run records the buy-back, finds the short gone and its stop open: it stops for review and sends nothing."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]))
    cycle(book, lv, ROLL)
    exit_ms = lv.active()[1]["exit_ms"]
    real_open, original = fake.fetch_open_orders, fake.create_order
    reads = []

    def open_orders(sym=None, since=None, limit=None, params=None):
        if (params or {}).get("trigger") and sym == "CCC/USDT:USDT":
            reads.append(sym)
            if len(reads) == 2:
                return []                                     # the close's read: stale, the stop looks gone
        return real_open(sym, since, limit, params)

    def dies(sym, typ, side, amount, price=None, params=None):
        out = original(sym, typ, side, amount, price, params)
        if sym == "CCC/USDT:USDT":
            raise Killed()
        return out

    fake.fetch_open_orders, fake.create_order = open_orders, dies
    with pytest.raises(Killed):
        cycle(book, lv, exit_ms + 15 * 60_000)
    fake.fetch_open_orders, fake.create_order = real_open, original
    assert pos(fake, "CCCUSDT") == pytest.approx(0) and len(fake.algo) == 1
    lv2 = LowVolBook(fresh_book(book, book.audit_path.parent), PairStore.load(lv.blocks.path, KEY), 1000.0, picks,
                     lambda c: c in {"ETH"})
    n = len(fake.calls)
    with pytest.raises(PerpBookError, match="short is gone but its stop is still open"):
        cycle(lv2.book, lv2, exit_ms + H + 15 * 60_000)
    assert not [c for c in fake.calls[n:] if c[0] == "order"] and len(fake.algo) == 1
    assert lv_block(lv)["legs"]["CCCUSDT"]["state"] == "held"


def test_lv_roll_flatten_is_not_held_up_by_a_fill_it_cannot_price(lvworld):
    """A roll's opening order fills 20 of 62.5 and expires with average 0 and cost 0, and the mark read fails. The
    same run still takes the whole roll off. The fill waits on the failed block (``unsettled``) until a run can
    price it; a run that cannot goes on."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "EEE"]))
    cycle(book, lv, ROLL)
    original, real_ticker = fake.create_order, fake.fetch_ticker

    def no_ticker(sym):
        raise ccxt.NetworkError("ticker timeout")

    def thin_open(s, typ, side, amount, price=None, params=None):
        p = params or {}
        cid = str(p.get("clientOrderId") or "")
        if s != "EEE/USDT:USDT" or not cid.startswith("lv") or p.get("reduceOnly") or "stopLossPrice" in p:
            return original(s, typ, side, amount, price, params)
        fake.create_order, fake.fetch_ticker = original, no_ticker
        fake.calls.append(("order", s, typ, side, amount, dict(p)))
        fake.positions[s] = fake.positions.get(s, 0.0) - 20.0
        fake.entry[s] = 4.0
        fake.orders[cid] = {"id": "x" + cid, "clientOrderId": cid, "symbol": s, "type": "market", "side": side,
                            "amount": amount, "filled": 20.0, "average": None, "cost": 0, "status": "expired",
                            "reduceOnly": False}
        return dict(fake.orders[cid])

    real_fetch = fake.fetch_order

    def fetch(oid, sym, params=None):
        fake.calls.append(("read", (params or {}).get("origClientOrderId")))
        return real_fetch(oid, sym, params)

    fake.create_order, fake.fetch_order = thin_open, fetch
    with pytest.raises(PerpBookError, match="ended expired"):
        cycle(book, lv, ROLL + WEEK)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}      # all off in the same run
    sent = [c for c in fake.calls if c[0] in ("order", "read")]
    first_read = next(i for i, c in enumerate(sent) if c[0] == "read")
    last_flatten = max(i for i, c in enumerate(sent) if c[0] == "order" and c[5]["clientOrderId"].endswith("-f0"))
    assert last_flatten < first_read                        # no read for the record before the flatten's orders
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "failed" and blocks[bid]["unsettled"] == 1
    flat_row = [f"lv{bid}-EEEUSDT-f0", 20.0, 4.0]
    assert blocks[bid]["legs"]["EEEUSDT"]["fills"] == [flat_row]   # the open's 20 waits for a price
    cycle(book, lv_reload(lv, picks), ROLL + WEEK + H)              # the mark still unreadable: the run goes on
    assert PairStore.load(lv.blocks.path, KEY).pairs[bid]["unsettled"] == 1
    fake.fetch_ticker = real_ticker
    cycle(book, lv_reload(lv, picks), ROLL + WEEK + 2 * H)
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert "unsettled" not in b
    assert b["legs"]["EEEUSDT"]["qty"] == 0.0
    assert b["legs"]["EEEUSDT"]["fills"] == [flat_row, [f"lv{bid}-EEEUSDT-o0", 20.0, 4.0, "mark"]]
    assert book.orders.unfinished_orders() == {}
    assert "perp_lv_fill_unpriced" in [e["event"] for e in events(book.audit_path.parent)]


def test_lv_roll_that_fails_at_the_old_stop_records_its_flatten_on_the_new_legs(lvworld):
    """The new block takes the current legs over before any cancel or order, so every flatten fill has a leg."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]))
    cycle(book, lv, ROLL)
    real_cancel = fake.cancel_order

    def cancel(oid, sym, params=None):
        fake.cancel_order = real_cancel                       # once: the flatten's cancel goes through
        raise ccxt.NetworkError("cancel timeout")

    fake.cancel_order = cancel
    with pytest.raises(Exception):
        cycle(book, lv, ROLL + WEEK)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    old, new = blocks[blocks[bid]["prev"]], blocks[bid]
    assert new["status"] == "failed" and old["status"] == "closed"
    assert {s: x["state"] for s, x in old["legs"].items()} == dict.fromkeys(["AAAUSDT", "BBBUSDT", "CCCUSDT"], "rolled")
    assert {s: (x["qty"], x["state"], x.get("fills")) for s, x in new["legs"].items()} == {
        "AAAUSDT": (0.0, "closed", [[f"lv{bid}-AAAUSDT-f0", 250.0, 1.0]]),
        "BBBUSDT": (0.0, "closed", [[f"lv{bid}-BBBUSDT-f0", 125.0, 2.0]]),
        "CCCUSDT": (0.0, "closed", [[f"lv{bid}-CCCUSDT-f0", 100.0, 5.0]]),
        "EEEUSDT": (0.0, "closed", None)}
    assert book.orders.unfinished_orders() == {}


def test_lv_roll_flatten_closes_the_old_stop_intent_a_cut_off_cancel_left_open(lvworld):
    """The roll cancels the old short's stop and the read after it fails: the flatten finds that stop gone and
    closes its intent as cancelled."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]))
    cycle(book, lv, ROLL)
    old_stop = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    restore = read_fails_after_a_cancel(fake)
    with pytest.raises(Exception):
        cycle(book, lv, ROLL + WEEK)
    restore()
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    assert book.orders.state.order_ledger[old_stop]["status"] == "cancelled"
    assert book.orders.unfinished_orders() == {}


def lv_process(lv, picks) -> tuple[Book, LowVolBook]:
    """A new process: the pair book's and LV's stores read again from disk."""

    b2 = fresh_book(lv.book, lv.book.audit_path.parent)
    return b2, LowVolBook(b2, PairStore.load(lv.blocks.path, KEY), 1000.0, picks, lambda c: c in {"ETH"})


def test_lv_roll_flatten_buys_shorts_back_first_and_goes_past_a_fill_without_a_price(lvworld):
    """The week-2 roll fails at EEE's open (expired in part). Its flatten buys back CCC, a carried short whose stop
    the roll had cancelled, before it sells a long. AAA's sell fills, but Demo answers it with average 0 and the
    read-back has no price for 15 s (``Book._fill_price`` raises): the flatten still takes EEE off, then raises
    with the block rolling. The next run sends nothing, records AAA's fill and marks the block failed."""

    import itertools
    import time

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "EEE"], ["CCC"]))
    cycle(book, lv, ROLL)
    ticks = itertools.count(0, 2)
    book.clock = lambda: next(ticks)                   # each read-back moves the clock 2 s: the 15 s wait runs out
    original, real_fetch = fake.create_order, fake.fetch_order
    no_price = set()

    def create(s, typ, side, amount, price=None, params=None):
        p = params or {}
        cid = str(p.get("clientOrderId") or "")
        if s == "EEE/USDT:USDT" and cid.startswith("lv") and not p.get("reduceOnly") and "stopLossPrice" not in p:
            fake.calls.append(("order", s, typ, side, amount, dict(p)))
            fake.positions[s] = fake.positions.get(s, 0.0) + 20.0
            fake.entry[s] = 4.0
            fake.orders[cid] = {"id": "x" + cid, "clientOrderId": cid, "symbol": s, "type": "market", "side": side,
                                "amount": amount, "filled": 20.0, "average": 4.0, "cost": 80.0, "status": "expired",
                                "reduceOnly": False}
            return dict(fake.orders[cid])
        if s == "AAA/USDT:USDT" and cid.endswith("-f0"):
            no_price.add(cid)
            fake.ack_without_price = True
            try:
                return original(s, typ, side, amount, price, params)
            finally:
                fake.ack_without_price = False
        return original(s, typ, side, amount, price, params)

    def fetch(oid, sym, params=None):
        o = real_fetch(oid, sym, params)
        return {**o, "average": None, "cost": 0} if o["clientOrderId"] in no_price else o

    fake.create_order, fake.fetch_order = create, fetch
    n = len(fake.calls)
    with pytest.raises(PerpBookError, match="filled without a price"):
        cycle(book, lv, ROLL + WEEK)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}   # no short left unprotected
    flat = [(c[1], c[3]) for c in fake.calls[n:] if c[0] == "order" and c[5]["clientOrderId"].endswith("-f0")]
    assert flat == [("CCC/USDT:USDT", "buy"), ("AAA/USDT:USDT", "sell"), ("EEE/USDT:USDT", "sell")]
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "rolling"
    assert "perp_lv_flatten_unfinished" in [e["event"] for e in events(book.audit_path.parent)]
    fake.create_order, fake.fetch_order = original, real_fetch
    book.clock = time.monotonic
    n = len(fake.calls)
    cycle(book, lv_reload(lv, picks), ROLL + WEEK + H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "unsettled" not in b
    assert b["legs"]["AAAUSDT"]["qty"] == 0.0 and b["legs"]["AAAUSDT"]["fills"] == [[f"lv{bid}-AAAUSDT-f0", 250.0, 1.0, "venue"]]
    assert all(leg["qty"] == 0.0 and leg["state"] == "closed" for leg in b["legs"].values())
    assert book.orders.unfinished_orders() == {}


def test_lv_roll_flatten_goes_on_past_a_lost_reply_and_raises_after(lvworld):
    """The week-2 roll fails at its first new stop. The flatten's buy-back of 1000PEPE fills but its reply is lost:
    the flatten still buys CCC back and sells the longs, then raises with the block rolling. The next run reads the
    lost reply back, records it on the leg and marks the block failed."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "PEPE"]))
    cycle(book, lv, ROLL)
    fake.fail_next = "stop"
    original = fake.create_order

    def lost(s, typ, side, amount, price=None, params=None):
        out = original(s, typ, side, amount, price, params)
        if str((params or {}).get("clientOrderId") or "").endswith("1000PEPEUSDT-f0"):
            raise ccxt.NetworkError("timeout")                 # the venue filled it; the reply never comes
        return out

    fake.create_order = lost
    with pytest.raises(PerpBookError, match="outcome unknown"):
        cycle(book, lv, ROLL + WEEK)
    fake.create_order, fake.fail_next = original, None
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "rolling"
    n = len(fake.calls)
    cycle(book, lv_reload(lv, picks), ROLL + WEEK + H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "unsettled" not in b
    assert b["legs"]["1000PEPEUSDT"]["fills"] == [[f"lv{bid}-1000PEPEUSDT-f0", 25_000.0, 0.01]]
    assert all(leg["qty"] == 0.0 and leg["state"] == "closed" for leg in b["legs"].values())
    assert book.orders.unfinished_orders() == {}


def test_lv_run_killed_while_its_flatten_sells_a_long_has_bought_the_shorts_back(lvworld):
    """The week-2 roll fails at its first new stop, after it cancelled CCC's old stop and opened EEE. The process
    dies just after the flatten's first sell: the shorts were bought back before it. The next run sells the rest."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "EEE"]))
    cycle(book, lv, ROLL)
    fake.fail_next = "stop"
    original = fake.create_order

    def dies(s, typ, side, amount, price=None, params=None):
        out = original(s, typ, side, amount, price, params)
        if str((params or {}).get("clientOrderId") or "").endswith("-f0") and side == "sell":
            raise Killed()                                     # the venue took the sell; the process died
        return out

    fake.create_order = dies
    with pytest.raises(Killed):
        cycle(book, lv, ROLL + WEEK)
    fake.create_order, fake.fail_next = original, None
    assert pos(fake, "CCCUSDT") == pytest.approx(0) and pos(fake, "EEEUSDT") == pytest.approx(0)
    assert pos(fake, "BBBUSDT") == pytest.approx(125) and fake.algo == {}      # a long waits for the next run
    b2, lv2 = lv_process(lv, picks)
    cycle(b2, lv2, ROLL + WEEK + H)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "failed" and "unsettled" not in blocks[bid]
    assert all(leg["qty"] == 0.0 and leg["state"] == "closed" for leg in blocks[bid]["legs"].values())
    assert b2.orders.unfinished_orders() == {}


def test_lv_roll_flatten_keeps_a_stop_it_could_not_cancel_until_a_later_run_does(lvworld):
    """Every cancel times out in the week-2 run (the adapter answers False): the roll fails at CCC's old stop, and
    the flatten cannot cancel it either. The flatten still buys CCC back, keeps the stop's intent open and lists the
    stop on the failed block. A run whose cancel still fails goes on; the next one cancels the stop and closes its
    intent. The week-3 roll shorts CCC under one stop."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]),
                (MON + 2 * WEEK, ["AAA", "BBB"], ["CCC"]))
    cycle(book, lv, ROLL)
    old_stop = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    real_cancel = fake.cancel_order

    def cancel(oid, sym, params=None):
        raise ccxt.NetworkError("cancel timeout")

    fake.cancel_order = cancel
    with pytest.raises(PerpBookError, match="could not be cancelled"):
        cycle(book, lv, ROLL + WEEK)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and set(fake.algo) == {old_stop}
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "failed" and blocks[bid]["stale_stops"] == {old_stop: "CCCUSDT"}
    assert book.orders.state.order_ledger[old_stop]["status"] == "open"      # the venue still lists it
    cycle(book, lv_reload(lv, picks), ROLL + WEEK + H)                        # its cancel still fails: the run goes on
    assert PairStore.load(lv.blocks.path, KEY).pairs[bid]["stale_stops"] == {old_stop: "CCCUSDT"}
    assert set(fake.algo) == {old_stop} and book.orders.state.order_ledger[old_stop]["status"] == "open"
    fake.cancel_order = real_cancel
    cycle(book, lv_reload(lv, picks), ROLL + WEEK + 2 * H)
    assert fake.algo == {} and "stale_stops" not in PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert book.orders.state.order_ledger[old_stop]["status"] == "cancelled"
    assert book.orders.unfinished_orders() == {}
    rep = cycle(book, lv_reload(lv, picks), ROLL + 2 * WEEK)
    assert rep["lv"]["rolled"] and pos(fake, "CCCUSDT") == pytest.approx(-100)
    assert [k for k, o in fake.algo.items() if o["symbol"] == "CCC/USDT:USDT"] == [f"lv{rep['lv']['rolled']}-CCCUSDT-st"]


@pytest.mark.parametrize("fired", ["before_the_run", "as_the_flatten_lists_it"])
def test_lv_flatten_records_an_old_stop_that_fired_as_a_stopped_leg(lvworld, fired):
    """The week-2 roll is killed right after its first save, before it cancels CCC's old stop. That stop then fires:
    before the next run, or just after the next run's flatten lists it, before it reads CCC's position (its cancel
    then finds no order). The flatten finds the venue flat on CCC and the leg still short: it marks the leg stopped
    and the stop's intent filled, as check() records a fired stop, and sends nothing on CCC."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]))
    cycle(book, lv, ROLL)
    old_stop = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    real_save = lv.blocks.save

    def save():
        real_save()
        if any(b.get("status") == "rolling" for b in lv.blocks.pairs.values()):
            raise Killed()

    lv.blocks.save = save
    with pytest.raises(Killed):
        cycle(book, lv, ROLL + WEEK)
    assert pos(fake, "CCCUSDT") == pytest.approx(-100) and set(fake.algo) == {old_stop}   # nothing sent yet
    if fired == "before_the_run":
        fake.fire_stop("CCCUSDT")
    else:
        real_open = fake.fetch_open_orders

        def open_orders(sym=None, since=None, limit=None, params=None):
            out = real_open(sym, since, limit, params)
            if (params or {}).get("trigger") and sym == "CCC/USDT:USDT":
                fake.fetch_open_orders = real_open
                fake.fire_stop("CCCUSDT")
            return out

        fake.fetch_open_orders = open_orders
    b2, lv2 = lv_process(lv, picks)
    n = len(fake.calls)
    cycle(b2, lv2, ROLL + WEEK + H)
    sent = [c for c in fake.calls[n:] if c[0] == "order"]
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    assert sent and all(c[5].get("reduceOnly") and c[1] != "CCC/USDT:USDT" for c in sent)
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    leg = blocks[bid]["legs"]["CCCUSDT"]
    assert leg["state"] == "stopped" and leg["stop_key"] == old_stop and leg["stop_price"] == pytest.approx(7.5)
    assert leg["qty"] == -100.0 and not leg.get("fills")                       # as check() leaves a stopped leg
    rec = b2.orders.state.order_ledger[old_stop]
    assert rec["status"] == "filled" and rec["exchange_status"] == "triggered"
    assert blocks[bid]["status"] == "failed" and "unsettled" not in blocks[bid]
    assert "perp_lv_stop" in [e["event"] for e in events(b2.audit_path.parent)]
    assert b2.orders.unfinished_orders() == {}


def test_lv_flatten_does_not_take_its_own_buy_back_for_a_fired_stop(lvworld):
    """The week-2 roll cancels CCC's old stop, then the next two stop reads time out: the roll's read-back (the
    stop's intent stays open) and the flatten's on CCC. The flatten's buy-back of CCC fills, but its reply is lost.
    The next run finds CCC flat and the old stop gone. The buy-back, read back by the reconcile, explains the flat
    venue: the leg is closed with that fill and the stop's intent cancelled. No stop fired."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]))
    cycle(book, lv, ROLL)
    old_stop = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    real_cancel, real_open, real_create = fake.cancel_order, fake.fetch_open_orders, fake.create_order
    to_fail = {"reads": 0}

    def cancel(oid, sym, params=None):
        out = real_cancel(oid, sym, params)
        to_fail["reads"] = 2
        return out

    def open_orders(sym=None, since=None, limit=None, params=None):
        if to_fail["reads"] > 0:
            to_fail["reads"] -= 1
            raise ccxt.NetworkError("read timeout")
        return real_open(sym, since, limit, params)

    def create(s, typ, side, amount, price=None, params=None):
        out = real_create(s, typ, side, amount, price, params)
        if str((params or {}).get("clientOrderId") or "").endswith("-CCCUSDT-f0"):
            raise ccxt.NetworkError("timeout")                 # the venue bought CCC back; the reply never comes
        return out

    fake.cancel_order, fake.fetch_open_orders, fake.create_order = cancel, open_orders, create
    with pytest.raises(Exception):
        cycle(book, lv, ROLL + WEEK)
    fake.cancel_order, fake.fetch_open_orders, fake.create_order = real_cancel, real_open, real_create
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "rolling" and book.orders.state.order_ledger[old_stop]["status"] == "open"
    b2, lv2 = lv_process(lv, picks)
    n = len(fake.calls)
    cycle(b2, lv2, ROLL + WEEK + H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    leg = b["legs"]["CCCUSDT"]
    assert leg["state"] == "closed" and "stopped_at" not in leg
    assert leg["qty"] == 0.0 and leg["fills"] == [[f"lv{bid}-CCCUSDT-f0", 100.0, 5.0]]
    assert b["status"] == "failed" and "unsettled" not in b
    rec = b2.orders.state.order_ledger[old_stop]
    assert rec["status"] == "cancelled" and rec.get("exchange_status") != "triggered"
    assert "perp_lv_stop" not in [e["event"] for e in events(b2.audit_path.parent)]
    assert b2.orders.unfinished_orders() == {}


def test_lv_flatten_records_a_stop_that_fired_after_its_buy_back_filled_in_part(lvworld):
    """The week-2 roll cannot cancel CCC's old stop (the cancel times out), and the flatten's read of CCC's stops
    times out too. Its buy-back of CCC fills 40 of 100 and expires; then the stop fires on the 60 left. The next run
    counts the 40 and still finds the leg short against a flat venue: the stop fired."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]))
    cycle(book, lv, ROLL)
    old_stop = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    real_cancel, real_open, original = fake.cancel_order, fake.fetch_open_orders, fake.create_order
    reads: dict[str, int | None] = {"after_cancel": None}

    def cancel(oid, sym, params=None):
        reads["after_cancel"] = 0
        raise ccxt.NetworkError("cancel timeout")              # the adapter answers False

    def open_orders(sym=None, since=None, limit=None, params=None):
        if (params or {}).get("trigger") and reads["after_cancel"] is not None:
            reads["after_cancel"] += 1
            if reads["after_cancel"] == 2:                     # the roll's read-back lists it; the flatten's fails
                raise ccxt.NetworkError("read timeout")
        return real_open(sym, since, limit, params)

    fake.cancel_order, fake.fetch_open_orders = cancel, open_orders
    cut = thin_book(fake, "CCCUSDT", 40.0)
    with pytest.raises(Exception):
        cycle(book, lv, ROLL + WEEK)
    fake.cancel_order, fake.fetch_open_orders, fake.create_order = real_cancel, real_open, original
    assert pos(fake, "CCCUSDT") == pytest.approx(-60) and set(fake.algo) == {old_stop}
    fake.fire_stop("CCCUSDT")
    b2, lv2 = lv_process(lv, picks)
    n = len(fake.calls)
    cycle(b2, lv2, ROLL + WEEK + H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    leg = blocks[bid]["legs"]["CCCUSDT"]
    assert leg["state"] == "stopped" and leg["stop_key"] == old_stop and leg["stop_price"] == pytest.approx(7.5)
    assert leg["qty"] == -60.0 and leg["fills"] == [[cut[0], 40.0, 5.0]]
    rec = b2.orders.state.order_ledger[old_stop]
    assert rec["status"] == "filled" and rec["exchange_status"] == "triggered"
    assert blocks[bid]["status"] == "failed" and "unsettled" not in blocks[bid]
    assert b2.orders.unfinished_orders() == {}


def test_lv_flatten_orders_by_the_legs_when_its_first_position_read_fails(lvworld):
    """The week-2 roll fails at its first new stop, after it bought part of CCC back and opened EEE short. The
    flatten's first read of all positions times out: the legs' quantities set the order, so the shorts are still
    bought back before the longs are sold. Each symbol reads its position again, and the same run ends flat."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "EEE"]))
    cycle(book, lv, ROLL)
    original, real_positions = fake.create_order, fake.fetch_positions
    armed = {"on": False}

    def create(s, typ, side, amount, price=None, params=None):
        if "stopLossPrice" in (params or {}):
            armed["on"] = True                                 # the next read of all positions is the flatten's
            raise ccxt.NetworkError("timeout")
        return original(s, typ, side, amount, price, params)

    def positions(symbols=None):
        if armed["on"]:
            armed["on"] = False
            raise ccxt.NetworkError("positions timeout")
        return real_positions(symbols)

    fake.create_order, fake.fetch_positions = create, positions
    n = len(fake.calls)
    with pytest.raises(PerpBookError, match="outcome unknown"):
        cycle(book, lv, ROLL + WEEK)
    fake.create_order, fake.fetch_positions = original, real_positions
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    flat = [(c[1], c[3]) for c in fake.calls[n:] if c[0] == "order" and c[5]["clientOrderId"].endswith("-f0")]
    assert flat == [("CCC/USDT:USDT", "buy"), ("EEE/USDT:USDT", "buy"), ("AAA/USDT:USDT", "sell"),
                    ("BBB/USDT:USDT", "sell")]
    bad = [e for e in events(book.audit_path.parent) if e["event"] == "perp_lv_flatten_error"]
    assert [e["details"]["step"] for e in bad] == ["order"]
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "failed" and "unsettled" not in blocks[bid]
    assert all(leg["qty"] == 0.0 and leg["state"] == "closed" for leg in blocks[bid]["legs"].values())
    assert book.orders.unfinished_orders() == {}


@pytest.mark.parametrize("fault", ["positions_down", "orders_down", "thin_book"])
def test_lv_flatten_cancels_a_shorts_stops_only_after_its_buy_back_filled_in_full(lvworld, fault):
    """The week-2 roll fails at EEE's new stop, after it placed CCC's and 1000PEPE's. For the rest of that run every
    position read fails, every market order fails before it acts, or every buy-back fills 40% and expires (a thin
    Demo book). The flatten tries each short's buy-back before its stops: none fills in full, so CCC and 1000PEPE
    keep stops that cover what the venue still holds. EEE, whose stop failed, is the one short without one. The block
    stays rolling. Once positions read again, or the books are deep again, the next run takes everything off."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "PEPE", "EEE"]))
    cycle(book, lv, ROLL)
    real_create, real_positions = fake.create_order, fake.fetch_positions
    down = {"on": False}
    reduce_only: list[tuple[str, str, float, float]] = []     # every reduce-only order: side, amount, venue before

    def create(s, typ, side, amount, price=None, params=None):
        p = params or {}
        cid = str(p.get("clientOrderId") or "")
        if "stopLossPrice" in p:
            if s == "EEE/USDT:USDT":
                down["on"] = True
                raise ccxt.NetworkError("stop timeout")
            return real_create(s, typ, side, amount, price, params)
        if p.get("reduceOnly"):
            reduce_only.append((s, side, float(amount), fake.positions.get(s, 0.0)))
        if down["on"] and fault == "orders_down":
            raise ccxt.NetworkError("orders endpoint down")
        if down["on"] and fault == "thin_book" and side == "buy" and p.get("reduceOnly"):
            q = round(float(amount) * 0.4, 3)
            fake.calls.append(("order", s, typ, side, amount, dict(p)))
            fake.positions[s] = round(fake.positions[s] + q, 3)
            px = fake.prices[fake.markets[s]["id"]]
            fake.orders[cid] = {"id": f"t{len(fake.orders)}", "clientOrderId": cid, "symbol": s, "type": "market",
                                "side": side, "amount": amount, "filled": q, "average": px, "cost": q * px,
                                "status": "expired", "reduceOnly": True}
            return dict(fake.orders[cid])
        return real_create(s, typ, side, amount, price, params)

    def positions(symbols=None):
        if down["on"] and fault == "positions_down":
            raise ccxt.NetworkError("positionRisk down")
        return real_positions(symbols)

    fake.create_order, fake.fetch_positions = create, positions
    with pytest.raises(Exception):
        cycle(book, lv, ROLL + WEEK)
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "rolling"
    kept = [f"lv{bid}-1000PEPEUSDT-st", f"lv{bid}-CCCUSDT-st"]
    assert sorted(fake.algo) == kept
    for key in kept:
        q = fake.positions[fake.algo[key]["symbol"]]
        assert q < 0 and float(fake.algo[key]["amount"]) >= -q - 1e-9     # still covers what the venue holds
        assert book.orders.state.order_ledger[key]["status"] == "open"
    shorts = sorted(s for s, q in fake.positions.items() if q < -1e-9)
    assert shorts == ["1000PEPE/USDT:USDT", "CCC/USDT:USDT", "EEE/USDT:USDT"]
    down["on"] = False
    b2, lv2 = lv_process(lv, picks)
    if fault == "orders_down":
        # The buy-backs raised before they acted: the reconcile cannot find them and stops every book for review,
        # with the shorts still under their stops.
        with pytest.raises(PerpBookError, match="not at the venue"):
            cycle(b2, lv2, ROLL + WEEK + H)
        assert sorted(fake.algo) == kept
        return
    cycle(b2, lv2, ROLL + WEEK + H)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "unsettled" not in b and "stale_stops" not in b
    assert all(leg["qty"] == 0.0 and leg["state"] == "closed" for leg in b["legs"].values())
    assert all(b2.orders.state.order_ledger[key]["status"] == "cancelled" for key in kept)
    assert b2.orders.unfinished_orders() == {}
    for s, side, amount, before in reduce_only:                      # never more than held, never the wrong side
        assert (before < 0) == (side == "buy") and amount <= abs(before) + 1e-9, (s, side, amount, before)


def test_lv_flatten_buys_back_a_short_without_a_stop_when_its_own_position_read_fails(lvworld):
    """The week-2 roll cancels 1000PEPE's old stop, then cannot cancel CCC's (the cancel times out once). The
    flatten's read of all positions works; 1000PEPE's own read then times out. 1000PEPE has no stop left and nothing
    but the flatten moves it, so it is bought back by the quantity of the first read. The block stays rolling for
    the read error, and the next run finds the venue flat and marks the block failed."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + WEEK, ["AAA", "CCC"], ["BBB", "EEE"]))
    cycle(book, lv, ROLL)
    real_cancel, real_positions = fake.cancel_order, fake.fetch_positions
    reads = {"after_cancel": None}

    def cancel(oid, sym, params=None):
        if sym == "CCC/USDT:USDT" and reads["after_cancel"] is None:
            reads["after_cancel"] = 0
            raise ccxt.NetworkError("cancel timeout")
        return real_cancel(oid, sym, params)

    def positions(symbols=None):
        if reads["after_cancel"] is not None:
            reads["after_cancel"] += 1
            if reads["after_cancel"] == 2:                    # the first read sets the order; 1000PEPE's own fails
                raise ccxt.NetworkError("positions timeout")
        return real_positions(symbols)

    fake.cancel_order, fake.fetch_positions = cancel, positions
    with pytest.raises(ccxt.NetworkError, match="positions timeout"):
        cycle(book, lv, ROLL + WEEK)
    fake.cancel_order, fake.fetch_positions = real_cancel, real_positions
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}   # nothing left without a stop
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "rolling"
    bad = [e["details"] for e in events(book.audit_path.parent) if e["event"] == "perp_lv_flatten_error"]
    assert [(e.get("symbol"), e["step"]) for e in bad] == [("1000PEPEUSDT", "position")]
    assert blocks[bid]["legs"]["1000PEPEUSDT"]["fills"] == [[f"lv{bid}-1000PEPEUSDT-f0", 25_000.0, 0.01]]
    b2, lv2 = lv_process(lv, picks)
    n = len(fake.calls)
    cycle(b2, lv2, ROLL + WEEK + H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "unsettled" not in b
    assert all(leg["qty"] == 0.0 and leg["state"] == "closed" for leg in b["legs"].values())
    assert b2.orders.unfinished_orders() == {}


def test_lv_flatten_does_not_buy_back_by_its_first_read_a_short_whose_taken_stop_is_no_longer_listed(lvworld):
    """The week-2 roll fails at EEE's new stop, after it placed CCC's. The flatten's read of all positions finds CCC
    short; CCC's new stop then fires, so the venue lists no stop on CCC, and CCC's own position read times out. The
    venue took that stop and its intent is still open: it may have fired since the first read, and a reduce-only buy
    on a flat symbol would be rejected and stop every book for review. So the flatten sends nothing on CCC, and the
    next run finds CCC flat and records the stop as fired."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "PEPE", "EEE"]))
    cycle(book, lv, ROLL)
    real_create, real_open, real_positions = fake.create_order, fake.fetch_open_orders, fake.fetch_positions
    st = {"down": False, "fired": False, "failed": False, "listed": None}

    def create(s, typ, side, amount, price=None, params=None):
        if "stopLossPrice" in (params or {}) and s == "EEE/USDT:USDT":
            st["down"] = True
            raise ccxt.NetworkError("stop timeout")
        return real_create(s, typ, side, amount, price, params)

    def open_orders(sym=None, since=None, limit=None, params=None):
        if (params or {}).get("trigger"):
            st["listed"] = sym
        return real_open(sym, since, limit, params)

    def positions(symbols=None):
        if st["down"] and not st["fired"]:            # the flatten's first read; CCC's stop fires right after it
            out = real_positions(symbols)
            st["fired"] = True
            fake.fire_stop("CCCUSDT")
            return out
        if st["fired"] and not st["failed"] and st["listed"] == "CCC/USDT:USDT":
            st["failed"] = True
            raise ccxt.NetworkError("positions timeout")
        return real_positions(symbols)

    fake.create_order, fake.fetch_open_orders, fake.fetch_positions = create, open_orders, positions
    with pytest.raises(ccxt.NetworkError, match="positions timeout"):
        cycle(book, lv, ROLL + WEEK)
    fake.create_order, fake.fetch_open_orders, fake.fetch_positions = real_create, real_open, real_positions
    assert st["fired"] and st["failed"]
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    mine = f"lv{bid}-CCCUSDT-st"
    assert blocks[bid]["status"] == "rolling"
    assert not [k for k in book.orders.state.order_ledger if k.startswith(f"lv{bid}-CCCUSDT-f")]
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    b2, lv2 = lv_process(lv, picks)
    n = len(fake.calls)
    cycle(b2, lv2, ROLL + WEEK + H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "unsettled" not in b
    assert b["legs"]["CCCUSDT"]["state"] == "stopped" and b["legs"]["CCCUSDT"]["stop_key"] == mine
    rec = b2.orders.state.order_ledger[mine]
    assert rec["status"] == "filled" and rec["exchange_status"] == "triggered"
    assert b2.orders.unfinished_orders() == {}


def test_lv_stop_whose_send_raised_but_an_earlier_pass_listed_is_closed_as_cancelled_once_dropped(lvworld):
    """The week-2 roll sends CCC's new stop: the venue takes it but the reply is lost, so its intent stays
    reconciling and the roll fails there. The flatten buys CCC back, but its cancel of that stop times out: the stop
    stays listed (stale). AAA's sell reply is lost, so the block stays rolling. Before the next run the venue drops
    the stop. The next pass closes its intent as cancelled, not rejected: an earlier pass found it listed, so the
    venue took it. EEE's stop, never sent, has no intent."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "EEE"]))
    cycle(book, lv, ROLL)
    real_create, real_cancel = fake.create_order, fake.cancel_order
    st = {"failed": False}

    def create(s, typ, side, amount, price=None, params=None):
        p = params or {}
        out = real_create(s, typ, side, amount, price, params)
        if "stopLossPrice" in p and s == "CCC/USDT:USDT":
            st["failed"] = True
            raise ccxt.NetworkError("reply lost")              # the venue took it
        if st["failed"] and s == "AAA/USDT:USDT" and str(p.get("clientOrderId", "")).endswith("-f0"):
            raise ccxt.NetworkError("reply lost")
        return out

    def cancel(oid, sym, params=None):
        if st["failed"] and sym == "CCC/USDT:USDT":
            raise ccxt.NetworkError("cancel timeout")
        return real_cancel(oid, sym, params)

    fake.create_order, fake.cancel_order = create, cancel
    with pytest.raises(PerpBookError):
        cycle(book, lv, ROLL + WEEK)
    fake.create_order, fake.cancel_order = real_create, real_cancel
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    mine = f"lv{bid}-CCCUSDT-st"
    assert blocks[bid]["status"] == "rolling" and blocks[bid]["stale_stops"] == {mine: "CCCUSDT"}
    assert book.orders.state.order_ledger[mine]["status"] == "reconciling"
    assert set(fake.algo) == {mine} and pos(fake, "CCCUSDT") == pytest.approx(0)
    del fake.algo[mine]                                       # the venue drops it before the next run
    b2, lv2 = lv_process(lv, picks)
    cycle(b2, lv2, ROLL + WEEK + H)
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "stale_stops" not in b
    led = b2.orders.state.order_ledger
    assert led[mine]["status"] == "cancelled" and led[mine].get("error") != "block_failed"
    assert f"lv{bid}-EEEUSDT-st" not in led
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and b2.orders.unfinished_orders() == {}


def test_lv_flatten_closes_its_own_acknowledged_stop_the_venue_dropped_as_cancelled(lvworld):
    """The week-2 roll places CCC's new stop (acknowledged), then fails at EEE's (its send times out; the venue never
    takes it). Just then the venue drops CCC's stop. The flatten finds CCC still short with no stop listed and buys
    it back. CCC's stop was taken by the venue, so its intent is closed as cancelled; EEE's, never acknowledged and
    never listed, as rejected."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "EEE"]))
    cycle(book, lv, ROLL)
    real_create = fake.create_order

    def create(s, typ, side, amount, price=None, params=None):
        if "stopLossPrice" in (params or {}) and s == "EEE/USDT:USDT":
            for k in [k for k, o in fake.algo.items() if o["symbol"] == "CCC/USDT:USDT"]:
                del fake.algo[k]                              # the venue drops CCC's new stop
            raise ccxt.NetworkError("stop timeout")
        return real_create(s, typ, side, amount, price, params)

    fake.create_order = create
    with pytest.raises(PerpBookError, match="outcome unknown"):
        cycle(book, lv, ROLL + WEEK)
    fake.create_order = real_create
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    mine, eee = f"lv{bid}-CCCUSDT-st", f"lv{bid}-EEEUSDT-st"
    assert blocks[bid]["status"] == "failed" and "stale_stops" not in blocks[bid]
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    led = book.orders.state.order_ledger
    assert led[mine]["status"] == "cancelled" and led[mine].get("error") != "block_failed"
    assert led[eee]["status"] == "rejected" and led[eee]["error"] == "block_failed"
    assert book.orders.unfinished_orders() == {}


def test_lv_flatten_sells_a_long_when_its_stop_list_cannot_be_read_again(lvworld):
    """CCC flips from short to long in week 2. The roll's cancel of CCC's old stop times out and a lagging list read
    shows it gone, so the venue keeps that reduce-only buy under the new long. The roll then fails at EEE's stop. The
    flatten cancels the old stop on CCC, and its read of CCC's stop list after the cancel times out: CCC is still
    sold. The block stays rolling for the read error, and the next run marks it failed."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "CCC"], ["EEE"]))
    cycle(book, lv, ROLL)
    old = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    real_create, real_cancel, real_open = fake.create_order, fake.cancel_order, fake.fetch_open_orders
    phase = {"n": 0}

    def create(s, typ, side, amount, price=None, params=None):
        if "stopLossPrice" in (params or {}) and s == "EEE/USDT:USDT":
            raise ccxt.NetworkError("stop timeout")
        return real_create(s, typ, side, amount, price, params)

    def cancel(oid, sym, params=None):
        if sym == "CCC/USDT:USDT":
            if phase["n"] == 0:                               # the roll's cancel: the venue keeps the stop
                phase["n"] = 1
                raise ccxt.NetworkError("cancel timeout")
            if phase["n"] == 2:                               # the flatten's cancel works
                phase["n"] = 3
        return real_cancel(oid, sym, params)

    def open_orders(sym=None, since=None, limit=None, params=None):
        if (params or {}).get("trigger") and sym == "CCC/USDT:USDT":
            if phase["n"] == 1:                               # the roll's read after its cancel lags
                phase["n"] = 2
                return []
            if phase["n"] == 3:                               # the flatten's read after its cancel
                phase["n"] = 4
                raise ccxt.NetworkError("open orders timeout")
        return real_open(sym, since, limit, params)

    fake.create_order, fake.cancel_order, fake.fetch_open_orders = create, cancel, open_orders
    with pytest.raises(ccxt.NetworkError, match="open orders timeout"):
        cycle(book, lv, ROLL + WEEK)
    fake.create_order, fake.cancel_order, fake.fetch_open_orders = real_create, real_cancel, real_open
    assert phase["n"] == 4
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    assert blocks[bid]["status"] == "rolling"
    bad = [e["details"] for e in events(book.audit_path.parent) if e["event"] == "perp_lv_flatten_error"]
    assert [(e.get("symbol"), e["step"]) for e in bad] == [("CCCUSDT", "stops")]
    assert [r[0] for r in blocks[bid]["legs"]["CCCUSDT"]["fills"]][-1] == f"lv{bid}-CCCUSDT-f0"
    b2, lv2 = lv_process(lv, picks)
    n = len(fake.calls)
    cycle(b2, lv2, ROLL + WEEK + H)
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "unsettled" not in b and "stale_stops" not in b
    assert all(leg["qty"] == 0.0 and leg["state"] == "closed" for leg in b["legs"].values())
    assert b2.orders.state.order_ledger[old]["status"] == "cancelled"
    assert b2.orders.unfinished_orders() == {}


@pytest.mark.parametrize("cut", ["after_the_intent", "before_the_intent"])
def test_lv_flatten_cut_off_while_it_records_a_fired_stop_keeps_the_leg_stopped(lvworld, monkeypatch, cut):
    """The week-2 roll is killed right after its first save; CCC's old stop then fires. The next run's flatten finds
    it fired and is killed while it records that: just after the stop's intent is closed as filled, or just before.
    The leg is saved stopped before the intent is closed, so the run after still records the stop as fired: the leg
    stopped, the intent filled and triggered."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]))
    cycle(book, lv, ROLL)
    old_stop = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    real_save = lv.blocks.save

    def save():
        real_save()
        if any(b.get("status") == "rolling" for b in lv.blocks.pairs.values()):
            raise Killed()

    lv.blocks.save = save
    with pytest.raises(Killed):
        cycle(book, lv, ROLL + WEEK)
    fake.fire_stop("CCCUSDT")
    real_close = lowvol.LowVolBook._close_stop_intent

    def close(self, key, final, **extra):
        if final == "filled" and cut == "before_the_intent":
            raise Killed()
        real_close(self, key, final, **extra)
        if final == "filled":
            raise Killed()

    monkeypatch.setattr(lowvol.LowVolBook, "_close_stop_intent", close)
    b2, lv2 = lv_process(lv, picks)
    with pytest.raises(Killed):
        cycle(b2, lv2, ROLL + WEEK + H)
    monkeypatch.setattr(lowvol.LowVolBook, "_close_stop_intent", real_close)
    b3, lv3 = lv_process(lv, picks)
    cycle(b3, lv3, ROLL + WEEK + 2 * H)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and fake.algo == {}
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    leg = blocks[bid]["legs"]["CCCUSDT"]
    assert leg["state"] == "stopped" and leg["stop_key"] == old_stop and leg["qty"] == -100.0
    rec = b3.orders.state.order_ledger[old_stop]
    assert rec["status"] == "filled" and rec["exchange_status"] == "triggered"
    assert blocks[bid]["status"] == "failed" and b3.orders.unfinished_orders() == {}


def test_lv_stale_stop_of_a_rolling_block_that_the_venue_drops_is_closed_as_cancelled(lvworld):
    """The week-2 roll fails at EEE's new stop (its send times out; the venue never takes it), after CCC's new stop
    was placed. The flatten buys CCC back, but its cancel of CCC's stop times out: the stop stays listed (stale).
    AAA's sell reply is lost, so the block stays rolling. Before the next run the venue drops CCC's stop. The next
    pass closes that intent as cancelled: the venue took the stop. EEE's stop, never acknowledged and never
    listed, is closed as rejected."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB"], ["CCC", "EEE"]))
    cycle(book, lv, ROLL)
    real_create, real_cancel = fake.create_order, fake.cancel_order
    st = {"failed": False}

    def create(s, typ, side, amount, price=None, params=None):
        p = params or {}
        if "stopLossPrice" in p and s == "EEE/USDT:USDT":
            st["failed"] = True
            raise ccxt.NetworkError("stop timeout")
        out = real_create(s, typ, side, amount, price, params)
        if st["failed"] and s == "AAA/USDT:USDT" and str(p.get("clientOrderId", "")).endswith("-f0"):
            raise ccxt.NetworkError("reply lost")
        return out

    def cancel(oid, sym, params=None):
        if st["failed"] and sym == "CCC/USDT:USDT":
            raise ccxt.NetworkError("cancel timeout")
        return real_cancel(oid, sym, params)

    fake.create_order, fake.cancel_order = create, cancel
    with pytest.raises(PerpBookError):
        cycle(book, lv, ROLL + WEEK)
    fake.create_order, fake.cancel_order = real_create, real_cancel
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    (bid,) = [k for k, b in blocks.items() if b.get("prev")]
    mine, eee = f"lv{bid}-CCCUSDT-st", f"lv{bid}-EEEUSDT-st"
    assert blocks[bid]["status"] == "rolling" and blocks[bid]["stale_stops"] == {mine: "CCCUSDT"}
    assert set(fake.algo) == {mine} and pos(fake, "CCCUSDT") == pytest.approx(0)
    del fake.algo[mine]                                       # the venue drops it before the next run
    b2, lv2 = lv_process(lv, picks)
    cycle(b2, lv2, ROLL + WEEK + H)
    b = PairStore.load(lv.blocks.path, KEY).pairs[bid]
    assert b["status"] == "failed" and "stale_stops" not in b
    led = b2.orders.state.order_ledger
    assert led[mine]["status"] == "cancelled" and led[mine].get("error") != "block_failed"
    assert led[eee]["status"] == "rejected" and led[eee]["error"] == "block_failed"
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and b2.orders.unfinished_orders() == {}


def test_lv_roll_skips_a_symbol_under_a_stop_a_failed_block_could_not_cancel(lvworld):
    """CCC's old stop cannot be cancelled from week 2 on, so the failed week-2 block keeps it in ``stale_stops``.
    The week-3 roll leaves CCC out (``stale_stop``) and shorts 1000PEPE: no LV short sits under that stale stop.
    Once a later run cancels it, the week-4 roll shorts CCC again under one stop."""

    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC"]), (MON + WEEK, ["AAA", "BBB", "EEE"], ["CCC"]),
                (MON + 2 * WEEK, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + 3 * WEEK, ["AAA", "BBB"], ["CCC", "PEPE"]))
    cycle(book, lv, ROLL)
    old = lv_block(lv)["legs"]["CCCUSDT"]["stop_key"]
    real_cancel = fake.cancel_order

    def cancel(oid, sym, params=None):
        if (params or {}).get("clientOrderId") == old:
            raise ccxt.NetworkError("cancel timeout")
        return real_cancel(oid, sym, params)

    fake.cancel_order = cancel
    with pytest.raises(PerpBookError, match="could not be cancelled"):
        cycle(book, lv, ROLL + WEEK)
    assert all(abs(q) < 1e-9 for q in fake.positions.values()) and set(fake.algo) == {old}
    plan = run_cycle(book, now_ms=ROLL + 2 * WEEK, mainnet_info={}, execute=False, lv=lv_reload(lv, picks))["lv"]
    assert plan["skipped"] == {"CCC": "stale_stop"} and plan["short"] == 1
    rep = cycle(book, lv_reload(lv, picks), ROLL + 2 * WEEK)
    assert rep["lv"]["rolled"] and rep["lv"]["short"] == 1 and rep["lv"]["skipped"] == 1
    blocks = PairStore.load(lv.blocks.path, KEY).pairs
    assert blocks[rep["lv"]["rolled"]]["skipped"] == {"CCC": "stale_stop"}
    assert pos(fake, "CCCUSDT") == 0.0 and pos(fake, "1000PEPEUSDT") == pytest.approx(-50_000)
    assert sorted(o["symbol"] for o in fake.algo.values()) == ["1000PEPE/USDT:USDT", "CCC/USDT:USDT"]
    fake.cancel_order = real_cancel
    cycle(book, lv_reload(lv, picks), ROLL + 2 * WEEK + H)    # the failed block's check cancels the stale stop
    assert [o["symbol"] for o in fake.algo.values()] == ["1000PEPE/USDT:USDT"]
    rep = cycle(book, lv_reload(lv, picks), ROLL + 3 * WEEK)
    assert rep["lv"]["rolled"] and rep["lv"]["short"] == 2 and rep["lv"]["skipped"] == 0
    assert pos(fake, "CCCUSDT") == pytest.approx(-50)
    assert [k for k, o in fake.algo.items() if o["symbol"] == "CCC/USDT:USDT"] == [f"lv{rep['lv']['rolled']}-CCCUSDT-st"]


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


# ── several books on one account: shared symbols and caps ──────────────────

from trading_agent.execution.perp import risk  # noqa: E402
from trading_agent.execution.perp.legs import LegTrader  # noqa: E402
from trading_agent.execution.perp.venue import PerpPosition  # noqa: E402


class FakeBook:
    """Another book on the account: holds what the test gives it and trades nothing."""

    def __init__(self, name: str, held: dict[str, float] | None = None):
        self.name = name
        self.qty = dict(held or {})
        self.calls: list[tuple] = []

    def check(self) -> None:
        self.calls.append(("check",))

    def held(self) -> dict[str, float]:
        return dict(self.qty)

    def plan(self, now_ms: int) -> dict:
        return {"held": len(self.qty)}

    def step(self, now_ms: int, *, entry_lock: str | None = None) -> dict:
        self.calls.append(("step", entry_lock))
        return {"held": len(self.qty)}


def hold(fake, ex_id: str, qty: float) -> None:
    """Put a position on the fake venue, as another book's fill would."""

    sym = f"{ex_id[:-4]}/USDT:USDT"
    fake.positions[sym] = fake.positions.get(sym, 0.0) + qty
    fake.entry[sym] = fake.prices[ex_id]


def events(tmp: Path) -> list[dict]:
    return [json.loads(x) for x in (tmp / "audit.jsonl").read_text().splitlines()]


def orders_sent(fake) -> list[tuple]:
    return [c for c in fake.calls if c[0] == "order"]


def test_other_books_check_before_the_position_check_and_step_after_the_exits(world):
    fake, book, _ = world
    a, b = FakeBook("learn"), FakeBook("flow02")
    rep = run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, others=[a, b], entry_lock="lock")
    assert a.calls == [("check",), ("step", "lock")] and b.calls == [("check",), ("step", "lock")]
    assert rep["learn"] == {"held": 0} and rep["flow02"] == {"held": 0}
    assert run_cycle(book, now_ms=T0, mainnet_info={}, execute=False, others=[a, b])["flow02"] == {"held": 0}
    with pytest.raises(PerpBookError, match="unique"):
        run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, others=[a, FakeBook("learn")])


def test_btc_must_equal_the_pair_hedges_plus_the_other_books_btc_legs(world):
    fake, book, tmp = world
    flow = FakeBook("flow02", {"BTCUSDT": 0.002})
    hold(fake, "BTCUSDT", 0.002)
    i = info(("NEWUSDT", T0, None, "COIN"))
    assert run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=i, execute=True, others=[flow])["entered"] == ["NEWUSDT"]
    assert pos(fake, "BTCUSDT") == pytest.approx(0.003)
    run_cycle(book, now_ms=T0 + 30 * H, mainnet_info=i, execute=True, others=[flow])   # 0.001 hedge + 0.002: explained
    # the pair's exit sells only its own hedge, reduce-only; the other book's BTC stays
    run_cycle(book, now_ms=T0 + (4 + rules.HOLD_H) * H, mainnet_info=i, execute=True, others=[flow])
    assert pos(fake, "BTCUSDT") == pytest.approx(0.002)
    (sell,) = [c for c in orders_sent(fake) if c[1] == "BTC/USDT:USDT" and c[3] == "sell"]
    assert sell[4] == pytest.approx(0.001) and sell[5]["reduceOnly"]
    # a BTC quantity no book explains stops the run
    hold(fake, "BTCUSDT", 0.001)
    n = len(fake.calls)
    with pytest.raises(PerpBookError, match="do not match"):
        run_cycle(book, now_ms=T0 + 200 * H, mainnet_info={}, execute=True, others=[flow])
    assert not [c for c in fake.calls[n:] if c[0] == "order"]
    assert events(tmp)[-1]["event"] == "perp_unexplained_position"
    # so does a book that thinks it is short BTC
    fake.positions["BTC/USDT:USDT"] = 0.0
    flow.qty["BTCUSDT"] = -0.002
    with pytest.raises(PerpBookError, match="negative"):
        run_cycle(book, now_ms=T0 + 201 * H, mainnet_info={}, execute=True, others=[flow])


def test_two_books_on_one_non_btc_symbol_stop_the_run(world):
    fake, book, tmp = world
    hold(fake, "OLDUSDT", -5.0)
    a, b = FakeBook("learn", {"OLDUSDT": -5.0}), FakeBook("flow01", {"OLDUSDT": -5.0})
    with pytest.raises(PerpBookError, match="held by learn and flow01"):
        run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, others=[a, b])
    assert not orders_sent(fake)
    assert events(tmp)[-1]["event"] == "perp_symbol_conflict"
    # the pair book counts as a holder too
    fake.positions.clear()
    run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, rehearse="OLDUSDT", rehearse_hold_h=2)
    with pytest.raises(PerpBookError, match="pair_book and learn"):
        run_cycle(book, now_ms=T0 + H, mainnet_info={}, execute=True, others=[FakeBook("learn", {"OLDUSDT": -1.0})])


def test_pair_book_skips_a_symbol_another_book_holds(world):
    fake, book, tmp = world
    hold(fake, "NEWUSDT", 10.0)
    learn = FakeBook("learn", {"NEWUSDT": 10.0})
    rep = run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=info(("NEWUSDT", T0, None, "COIN")), execute=True, others=[learn])
    assert rep["entered"] == [] and rep["skipped"] == [{"symbol": "NEWUSDT", "reason": "held_by_learn"}]
    assert not orders_sent(fake) and pos(fake, "NEWUSDT") == pytest.approx(10.0)
    with pytest.raises(PerpBookError, match="cannot rehearse"):
        run_cycle(book, now_ms=T0 + 5 * H, mainnet_info={}, execute=True, others=[learn], rehearse="NEWUSDT")
    assert book.taken("learn") == {} and book.taken("pair_book") == {"NEWUSDT": "learn"}


def test_room_measures_the_whole_account_at_mark(world):
    fake, book, _ = world
    hold(fake, "BTCUSDT", 0.01)      # 800 long BTC
    hold(fake, "OLDUSDT", -10.0)     # 100 short alt
    hold(fake, "NEWUSDT", 50.0)      # 100 long alt
    r = risk.room(book.venue)
    assert (r.equity, r.gross, r.alt_short) == pytest.approx((10_000, 1_000, 100))
    assert r.gross_room == pytest.approx(8_000) and r.alt_short_room == pytest.approx(3_900)
    assert r.binding(8_000, 3_900) is None
    assert r.binding(8_001, 0) == risk.EQUITY and r.binding(100, 3_901) == risk.ALT_SHORT
    assert r.scale(16_000, 0) == pytest.approx(0.5) and r.scale(100, 7_800) == pytest.approx(0.5)
    assert r.scale(10, 10) == 1.0
    released = risk.room(book.venue, release={"OLDUSDT": -10.0})
    assert (released.gross, released.alt_short) == pytest.approx((900, 0))
    over = risk.Room(1_000, 950, 500)
    assert over.scale(10, 10) == 0.0 and over.binding(0, 0) is None    # adding nothing is never blocked
    # a position the venue reports without a mark is priced from the ticker
    book.venue.positions = lambda: {"OLDUSDT": PerpPosition("OLDUSDT", -10.0, 10.0, 0.0)}
    assert risk.room(book.venue).alt_short == pytest.approx(100)


def test_gross_cap_counts_every_book_on_the_account(world):
    fake, book, tmp = world
    i = info(("NEWUSDT", T0, None, "COIN"))
    hold(fake, "OLDUSDT", 885.0)                 # 8,850 of 9,000 used: a 2 x 100 pair does not fit
    learn = FakeBook("learn", {"OLDUSDT": 885.0})
    rep = run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=i, execute=True, others=[learn])
    assert rep["entered"] == [] and pos(fake, "NEWUSDT") == 0.0
    assert events(tmp)[-1]["event"] == "perp_entry_skipped"
    assert events(tmp)[-1]["details"] == {"symbol": "NEWUSDT", "reason": "equity"}
    learn.qty["OLDUSDT"] = 870.0                 # 8,700: it fits
    hold(fake, "OLDUSDT", -15.0)
    assert run_cycle(book, now_ms=T0 + 5 * H, mainnet_info=i, execute=True, others=[learn])["entered"] == ["NEWUSDT"]


def test_alt_short_cap_binds_on_the_short_alt_group(world):
    fake, book, tmp = world
    i = info(("NEWUSDT", T0, None, "COIN"))
    hold(fake, "OLDUSDT", -395.0)                # 3,950 short of 4,000: a 100 short does not fit
    hold(fake, "BTCUSDT", 0.05)                  # 4,000 long BTC: not an alt short
    learn, flow = FakeBook("learn", {"OLDUSDT": -395.0}), FakeBook("flow02", {"BTCUSDT": 0.05})
    rep = run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=i, execute=True, others=[learn, flow])
    assert rep["entered"] == [] and pos(fake, "NEWUSDT") == 0.0
    assert events(tmp)[-1]["event"] == "perp_entry_skipped"
    assert events(tmp)[-1]["details"] == {"symbol": "NEWUSDT", "reason": "alt_short_cap"}
    learn.qty["OLDUSDT"] = -390.0                # 3,900 + 100: at the cap, which is allowed
    hold(fake, "OLDUSDT", 5.0)
    assert run_cycle(book, now_ms=T0 + 5 * H, mainnet_info=i, execute=True, others=[learn, flow])["entered"] == ["NEWUSDT"]


def test_exits_are_never_blocked_by_a_cap(world):
    fake, book, _ = world
    run_cycle(book, now_ms=T0, mainnet_info={}, execute=True, rehearse="OLDUSDT", rehearse_hold_h=2)
    fake.fetch_balance = lambda: {"info": {"totalMarginBalance": "10"}}    # far over both caps
    run_cycle(book, now_ms=T0 + 2 * H, mainnet_info={}, execute=True)
    assert pos(fake, "OLDUSDT") == pytest.approx(0) and pos(fake, "BTCUSDT") == pytest.approx(0)


def test_lv_skips_a_coin_another_book_holds(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]))
    hold(fake, "BBBUSDT", 50.0)
    learn = FakeBook("learn", {"BBBUSDT": 50.0})
    rep = cycle(book, lv, ROLL, others=[learn])
    assert rep["lv"]["rolled"] and rep["lv"]["long"] == 1 and rep["lv"]["skipped"] == 1
    assert pos(fake, "AAAUSDT") == pytest.approx(500) and pos(fake, "BBBUSDT") == pytest.approx(50)
    (block,) = PairStore.load(lv.blocks.path, KEY).pairs.values()
    assert block["skipped"] == {"BBB": "held_by_learn"}
    assert not [c for c in lv_orders(fake) if c[1] == "BBB/USDT:USDT"]
    cycle(book, lv, ROLL + H, others=[learn])     # both books explain their own legs
    trading = book.venue.trading_symbols()
    assert lowvol.pick_symbols(picks, MON + 15 * 60_000, trading) == {"AAAUSDT", "BBBUSDT", "CCCUSDT", "1000PEPEUSDT"}
    assert lowvol.pick_symbols(picks, MON + WEEK, trading) == set()


def test_lv_misses_the_week_when_the_alt_short_cap_binds(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]))
    hold(fake, "EEEUSDT", -950.0)                # 3,800 short: LV's 500 of shorts would pass 4,000
    rep = cycle(book, lv, ROLL, others=[FakeBook("learn", {"EEEUSDT": -950.0})])
    assert rep["lv"]["missed"] == "alt_short_cap" and not lv_orders(fake)


def test_lv_roll_counts_its_current_legs_as_gone(lvworld):
    fake, book, lv, picks = lvworld
    write_picks(picks, (MON, ["AAA", "BBB"], ["CCC", "PEPE"]), (MON + WEEK, ["AAA", "CCC"], ["BBB", "EEE"]))
    cycle(book, lv, ROLL)
    # 1,800 of gross room: the old book (1,000) and the new (1,000) together would not fit
    fake.fetch_balance = lambda: {"info": {"totalMarginBalance": "2000"}}
    rep = cycle(book, lv, ROLL + WEEK)
    assert rep["lv"]["rolled"] and pos(fake, "EEEUSDT") == pytest.approx(-62.5)


def test_leg_trader_moves_a_leg_toward_its_target(world):
    fake, book, tmp = world
    saved = []
    trader = LegTrader(book, lambda: saved.append(1), lambda s, kind: f"lrn-d1-{s[:18]}-{kind}", "perp_learn_delta_skipped")
    leg = {"qty": 0.0}
    trader.add_toward("OLDUSDT", leg, 5.0, price=10.0)
    assert leg["qty"] == pytest.approx(5.0) and pos(fake, "OLDUSDT") == pytest.approx(5.0)
    assert leg["fills"][0] == {"key": "lrn-d1-OLDUSDT-o0", "side": "buy", "qty": 5.0, "avg": 10.0, "fees": {}, "mark": 10.0}
    trader.add_toward("OLDUSDT", leg, 5.0004, price=10.0)          # below the 0.001 step: recorded, not sent
    assert leg["qty"] == pytest.approx(5.0) and events(tmp)[-1]["event"] == "perp_learn_delta_skipped"
    with pytest.raises(PerpBookError, match="flat"):
        trader.add_toward("OLDUSDT", leg, -3.0, price=10.0)         # a flip must go through flat
    trader.reduce_toward("OLDUSDT", leg, -3.0, price=10.0)
    trader.add_toward("OLDUSDT", leg, -3.0, price=10.0)
    assert leg["qty"] == pytest.approx(-3.0) and pos(fake, "OLDUSDT") == pytest.approx(-3.0)
    trader.reduce_toward("OLDUSDT", leg, -1.0, price=10.0)
    assert leg["qty"] == pytest.approx(-1.0) and leg["closing_key"] is None
    sent = [(c[5]["clientOrderId"], c[3], c[4], c[5].get("reduceOnly", False)) for c in orders_sent(fake)]
    assert sent == [("lrn-d1-OLDUSDT-o0", "buy", 5.0, False), ("lrn-d1-OLDUSDT-c0", "sell", 5.0, True),
                    ("lrn-d1-OLDUSDT-o1", "sell", 3.0, False), ("lrn-d1-OLDUSDT-c1", "buy", 2.0, True)]
    assert saved and book.orders.unfinished_orders() == {}
    with pytest.raises(PerpBookError, match="too long"):
        book.fresh_key("x" * 36)


def test_an_exact_close_after_a_cut_sends_the_whole_leg(world):
    """Summed fills drift (0.3 - 0.2 = 0.09999999999999998); ccxt would truncate that to 0.099 and leave dust."""

    fake, book, tmp = world
    trader = LegTrader(book, lambda: None, lambda s, kind: f"lv-d1-{s[:18]}-{kind}", "perp_lv_delta_skipped")
    leg = {"qty": 0.0}
    trader.add_toward("OLDUSDT", leg, 0.3, price=10.0)
    trader.reduce_toward("OLDUSDT", leg, 0.1, price=10.0)               # a cut of the same side
    assert leg["qty"] == 0.1                                           # rounded, not 0.09999999999999998
    trader.reduce_toward("OLDUSDT", leg, 0.0, price=10.0)               # the exact close
    assert leg["qty"] == 0.0 and pos(fake, "OLDUSDT") == pytest.approx(0.0, abs=1e-12)
    assert orders_sent(fake)[-1][4] == 0.1
    # a leg a store already holds with drift (an LV block saved before this rounding) closes whole too
    trader.add_toward("OLDUSDT", leg, 0.1, price=10.0)
    leg["qty"] = 0.3 - 0.2
    trader.reduce_toward("OLDUSDT", leg, 0.0, price=10.0)
    assert orders_sent(fake)[-1][4] == 0.1 and pos(fake, "OLDUSDT") == pytest.approx(0.0, abs=1e-12)
    # the fake venue truncates as ccxt does: the drifted quantity would have left one step behind
    fake.create_order("OLD/USDT:USDT", "market", "buy", 0.1, None, {"clientOrderId": "probe-o"})
    fake.create_order("OLD/USDT:USDT", "market", "sell", 0.3 - 0.2, None,
                      {"clientOrderId": "probe-c", "reduceOnly": True})
    assert pos(fake, "OLDUSDT") == pytest.approx(0.001)


def test_a_dry_run_with_two_holders_raises_and_writes_no_audit(world):
    fake, book, tmp = world
    books = [FakeBook("learn", {"XLMUSDT": 1.0}), Holder("flow02", {"XLMUSDT": 1.0}, book)]
    with pytest.raises(PerpBookError, match="operator review"):
        run_cycle(book, now_ms=T0 + 4 * H, mainnet_info={}, execute=False, others=books)
    assert not (tmp / "audit.jsonl").exists()
    with pytest.raises(PerpBookError, match="operator review"):          # the signed run audits it
        run_cycle(book, now_ms=T0 + 4 * H, mainnet_info={}, execute=True, others=books)
    assert [e["event"] for e in events(tmp)] == ["perp_symbol_conflict"]


class Holder(FakeBook):
    """A book whose dry-run plan reads the symbols other books hold, as LV and LEARN do."""

    def __init__(self, name: str, held: dict[str, float], book: Book):
        super().__init__(name, held)
        self.book = book

    def plan(self, now_ms: int) -> dict:
        return {"taken": len(self.book.taken(self.name))}

    def check(self) -> None:
        self.book.taken(self.name)


def test_a_run_of_one_book_checks_every_book_then_steps_that_book_alone(world):
    fake, book, tmp = world
    assert run_cycle(book, now_ms=T0 + 4 * H, mainnet_info=info(("NEWUSDT", T0, None, "COIN")), execute=True)["entered"] == ["NEWUSDT"]
    n = len(orders_sent(fake))
    learn, flow01 = FakeBook("learn"), FakeBook("flow01", {"BTCUSDT": 0.002})
    hold(fake, "BTCUSDT", 0.002)
    at = T0 + (4 + rules.HOLD_H) * H                      # the pair's exit is due, and OLDUSDT's entry
    due = info(("OLDUSDT", at - 4 * H, None, "COIN"))
    rep = run_cycle(book, now_ms=at, mainnet_info=due, execute=True, others=[learn, flow01], only="flow01")
    assert learn.calls == [("check",)] and flow01.calls == [("check",), ("step", None)]
    assert rep["flow01"] == {"held": 1} and "learn" not in rep and rep["only"] == "flow01" and rep["due"] == []
    assert len(orders_sent(fake)) == n and len(book.pairs.open_pairs()) == 1     # no pair exit, no entry
    assert "OLDUSDT" not in book.pairs.seen                                      # left to the hourly run
    # the position check still runs before the step: an unexplained BTC quantity stops the run
    hold(fake, "BTCUSDT", 0.001)
    with pytest.raises(PerpBookError, match="do not match"):
        run_cycle(book, now_ms=at, mainnet_info=None, execute=True, others=[learn, flow01], only="flow01")
    assert flow01.calls[-1] == ("check",)
    # a dry run plans that book alone; an unknown book or a rehearsal is refused
    assert run_cycle(book, now_ms=at, mainnet_info=None, execute=False, others=[learn, flow01], only="flow01")["flow01"] == {"held": 1}
    with pytest.raises(PerpBookError, match="not in this run"):
        run_cycle(book, now_ms=at, mainnet_info=None, execute=True, others=[learn], only="flow01")
    with pytest.raises(PerpBookError, match="rehearse"):
        run_cycle(book, now_ms=at, mainnet_info=None, execute=True, others=[flow01], only="flow01", rehearse="OLDUSDT")
    with pytest.raises(PerpBookError, match="report key"):
        run_cycle(book, now_ms=at, mainnet_info=None, execute=True, others=[FakeBook("only")])


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


# ── hourly and 5-minute wrappers and their tasks ────────────────────────────

REPO = Path(__file__).resolve().parents[1]
WRAPPER = REPO / "scripts" / "ops" / "run_perp_demo.sh"
FLUSH_WRAPPER = REPO / "scripts" / "ops" / "run_flow_flush.sh"
TASKS = REPO / "scripts" / "ops" / "windows" / "register_tasks.ps1"
BOOK_FLAGS = "--lv --learn --flow02 --flow01 --krw --pair-usd 500"


def wrapper_at(tmp_path: Path, hhmm: str, job: str, wrapper: Path = WRAPPER) -> subprocess.CompletedProcess:
    (tmp_path / "scripts" / "ops").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".venv" / "bin").mkdir(parents=True, exist_ok=True)
    script = tmp_path / "scripts" / "ops" / wrapper.name
    script.write_text(wrapper.read_text(encoding="utf8"), encoding="utf8")
    script.chmod(0o755)
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text("#!/usr/bin/env bash\n" + job, encoding="utf8")
    py.chmod(0o755)
    stand = tmp_path / "bin"
    stand.mkdir(exist_ok=True)
    (stand / "date").write_text(f'#!/usr/bin/env bash\ncase "$*" in *%H*) echo {hhmm[:2]};; *%M*) echo {hhmm[2:]};; *) /bin/date "$@";; esac\n', encoding="utf8")
    (stand / "date").chmod(0o755)
    return subprocess.run([str(script)], env={**os.environ, "PATH": f"{stand}:{os.environ['PATH']}"}, timeout=60, check=False)


def perp_calls(wrapper: Path) -> list[str]:
    """The arguments the wrapper gives scripts/live_perp_demo.py, each call as written."""

    text = wrapper.read_text(encoding="utf8").replace("\\\n", " ")
    lines = [x for x in text.splitlines() if "scripts/live_perp_demo.py" in x and not x.lstrip().startswith("#")]
    return [" ".join(x.split("scripts/live_perp_demo.py", 1)[1].split()).replace(' "$@" >> "$LOG" 2>&1', "") for x in lines]


@pytest.mark.parametrize("wrapper", [WRAPPER, FLUSH_WRAPPER], ids=["hourly", "5min"])
@pytest.mark.parametrize("hhmm", ["1050", "1100", "1105"])
def test_the_wrapper_keeps_out_of_the_testnet_window(tmp_path: Path, hhmm: str, wrapper: Path) -> None:
    assert wrapper_at(tmp_path, hhmm, "touch ran\n", wrapper).returncode == 0
    assert not (tmp_path / "ran").exists()


@pytest.mark.parametrize("hhmm", ["1015", "1115"])
def test_the_wrapper_runs_the_book_with_shadow_and_logs_its_end(tmp_path: Path, hhmm: str) -> None:
    assert wrapper_at(tmp_path, hhmm, 'echo "$TRADING_MODE $*"; exit 3\n').returncode == 3
    lines = (tmp_path / "logs" / "perp_demo.log").read_text(encoding="utf8").splitlines()
    assert lines[1] == f"testnet -u scripts/live_perp_demo.py --execute --shadow {BOOK_FLAGS}"
    assert lines[2] == "testnet -u scripts/live_spot_hold_demo.py --execute"
    assert lines[3] == " -u -m tools.learnpaper.run"
    assert re.fullmatch(r"=== \S+Z perp_demo end rc=3", lines[4])
    assert not re.search(r"^[^#]*\btimeout\b", WRAPPER.read_text(encoding="utf8"), re.M)


@pytest.mark.parametrize("hhmm", ["1014", "1159", "0004"])
def test_the_5_minute_wrapper_runs_flow01_then_krw_alone_with_every_book_and_logs_its_end(tmp_path: Path, hhmm: str) -> None:
    job = 'echo "$TRADING_MODE $OMP_NUM_THREADS $*"; case "$*" in *"only flow01"*) exit 3;; esac; exit 0\n'
    assert wrapper_at(tmp_path, hhmm, job, FLUSH_WRAPPER).returncode == 3          # the first failure is the exit code
    lines = (tmp_path / "logs" / "flow_flush.log").read_text(encoding="utf8").splitlines()
    assert re.fullmatch(r"=== \S+Z flow_flush start pid=\d+", lines[0])
    assert lines[1] == f"testnet 4 -u scripts/live_perp_demo.py --execute --only flow01 {BOOK_FLAGS}"
    assert lines[2] == f"testnet 4 -u scripts/live_perp_demo.py --execute --only krw {BOOK_FLAGS}"
    assert re.fullmatch(r"=== \S+Z flow_flush end rc=3", lines[3]) and len(lines) == 4
    assert not (tmp_path / "logs" / "perp_demo.log").exists()
    assert not re.search(r"^[^#]*\btimeout\b", FLUSH_WRAPPER.read_text(encoding="utf8"), re.M)
    assert "nice -n 19" in FLUSH_WRAPPER.read_text(encoding="utf8")
    # a failing KRW run is the exit code when FLOW01's went well
    job = 'case "$*" in *"only krw"*) exit 4;; esac; exit 0\n'
    assert wrapper_at(tmp_path, hhmm, job, FLUSH_WRAPPER).returncode == 4


def test_the_5_minute_wrapper_asks_the_testnet_window_again_before_the_krw_run(tmp_path: Path) -> None:
    """FLOW01's run started at :49 of an even hour and ended in the testnet window: the KRW run does not start."""

    (tmp_path / "bin").mkdir()
    clock = tmp_path / "minutes"
    clock.write_text("49 50\n", encoding="utf8")
    (tmp_path / "bin" / "date").write_text(
        '#!/usr/bin/env bash\nset -- "$@"\ncase "$*" in *%H*) echo 10;; *%M*) read -r m rest < "' + str(clock) + '"; '
        'echo "$m"; [ -n "$rest" ] && echo "$rest" > "' + str(clock) + '";; *) /bin/date "$@";; esac\n', encoding="utf8")
    (tmp_path / "bin" / "date").chmod(0o755)
    (tmp_path / "scripts" / "ops").mkdir(parents=True)
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    script = tmp_path / "scripts" / "ops" / FLUSH_WRAPPER.name
    script.write_text(FLUSH_WRAPPER.read_text(encoding="utf8"), encoding="utf8")
    script.chmod(0o755)
    py = tmp_path / ".venv" / "bin" / "python"
    py.write_text('#!/usr/bin/env bash\necho "$*"\n', encoding="utf8")
    py.chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}"}
    assert subprocess.run([str(script)], env=env, timeout=60, check=False).returncode == 0
    lines = (tmp_path / "logs" / "flow_flush.log").read_text(encoding="utf8").splitlines()
    assert [x for x in lines if "live_perp_demo" in x] == [f"-u scripts/live_perp_demo.py --execute --only flow01 {BOOK_FLAGS}"]


def test_the_5_minute_run_loads_the_same_books_and_sizes_as_the_hourly_run() -> None:
    (hourly,) = perp_calls(WRAPPER)
    flow01, krw = perp_calls(FLUSH_WRAPPER)
    assert hourly.split() == ["--execute", "--shadow"] + BOOK_FLAGS.split()
    assert flow01.split() == ["--execute", "--only", "flow01"] + BOOK_FLAGS.split()
    assert krw.split() == ["--execute", "--only", "krw"] + BOOK_FLAGS.split()


@pytest.mark.parametrize("wrapper", [WRAPPER, FLUSH_WRAPPER], ids=["hourly", "5min"])
def test_the_wrappers_share_one_lock_and_skip_while_it_is_held(tmp_path: Path, wrapper: Path) -> None:
    import fcntl

    (tmp_path / "logs").mkdir()
    with open(tmp_path / "logs" / ".perp_demo.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)     # the other wrapper's run is going
        assert wrapper_at(tmp_path, "1015", "touch ran\n", wrapper).returncode == 0
    assert not (tmp_path / "ran").exists()
    assert wrapper_at(tmp_path, "1015", "touch ran\n", wrapper).returncode == 0
    assert (tmp_path / "ran").exists()


def test_the_task_runs_the_wrapper_hourly_and_headless() -> None:
    text = TASKS.read_text(encoding="utf8")
    assert '-TaskName "TradingAgent-PerpDemo" -Action $perpdemo' in text
    assert "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_perp_demo.sh" in text
    assert re.search(r"perpdemoTrigger = .*?-RepetitionInterval \(New-TimeSpan -Hours 1\)", text, re.S)


def test_the_5_minute_task_is_registered_only_by_name() -> None:
    text = TASKS.read_text(encoding="utf8")
    valid = re.search(r"\[ValidateSet\(([^)]*)\)\]", text).group(1)
    default = re.search(r"\$Only = @\(([^)]*)\)", text).group(1)
    assert '"FlowFlush"' in valid and '"FlowFlush"' not in default
    assert '-TaskName "TradingAgent-FlowFlushDemo" -Action $flowflush' in text
    assert "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_flow_flush.sh" in text
    assert re.search(r"flowflushTrigger = New-ScheduledTaskTrigger -Once -At \$start\.AddMinutes\(1\) `\s*"
                     r"-RepetitionInterval \(New-TimeSpan -Minutes 5\)", text)
    assert 'if ($Only -contains "FlowFlush")' in text


# ── the runner script: flags and book FLOW01's 5-minute mode ─────────────────

import importlib.util  # noqa: E402


@pytest.fixture
def runner(monkeypatch):
    """scripts/live_perp_demo.py as a module, with .env never read."""

    spec = importlib.util.spec_from_file_location("live_perp_demo_under_test", REPO / "scripts" / "live_perp_demo.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(mod, "mainnet_listings", lambda: (_ for _ in ()).throw(AssertionError("no listings in --only")))
    # Book KRW's public data: a quiet notice list, no exchangeInfo and no shadow row (tests/test_perp_krwfade.py)
    monkeypatch.setattr(mod, "krw_sources", lambda box: (lambda: [], lambda: {}, lambda *a: {}))
    return mod


def roots(tmp_path: Path) -> list[str]:
    return ["--root", str(tmp_path / "perp_demo"), "--selection-root", str(tmp_path / "selection"),
            "--learn-root", str(tmp_path / "learn_paper"), "--flow02-root", str(tmp_path / "flowbtc")]


def test_runner_flags_refuse_sizes_over_each_books_cap_and_a_lone_only(runner, capsys):
    for flag, cap in (("--learn-usd", 4000), ("--flow02-usd", 3000), ("--flow01-usd", 3000), ("--krw-usd", 2000)):
        for bad in (str(cap + 1), "0", "-5", "nan"):
            with pytest.raises(SystemExit) as stop:
                runner.main([flag, bad])
            assert stop.value.code == 2
    with pytest.raises(SystemExit):
        runner.main(["--only", "lv"])                    # only books FLOW01 and KRW have a mode of their own
    assert runner.main(["--only", "krw"]) == 2           # needs --krw
    assert runner.main(["--only", "flow01"]) == 2        # needs --flow01
    assert runner.main(["--only", "flow01", "--flow01", "--shadow"]) == 2
    assert runner.main(["--only", "flow01", "--flow01", "--execute", "--rehearse", "OLDUSDT"]) == 2
    assert "needs --flow01" in capsys.readouterr().err


def test_only_flow01_with_a_quiet_pre_check_signs_nothing(runner, tmp_path, monkeypatch, capsys):
    from tests.test_perp_flowflush import E, FakeFeed

    feed = FakeFeed()                                    # no event
    monkeypatch.setattr(runner, "flow01_feed", lambda: feed)
    monkeypatch.setattr(runner, "now_ms", lambda: E + 5 * 60_000 + 20_000)

    def never(*a, **k):
        raise AssertionError("a quiet pre-check reached the signed path")

    for name in ("load_dotenv", "require_execution_authorization", "load_demo_credentials", "LiveExecutionLock",
                 "LiveRiskStateStore", "PerpDemoVenue", "append_live_audit_event"):
        monkeypatch.setattr(runner, name, never)
    argv = ["--execute", "--only", "flow01", *BOOK_FLAGS.split(), *roots(tmp_path)]
    assert runner.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {"only": "flow01", "signed_run": False}
    assert feed.looks and not (tmp_path / "perp_demo").exists()     # nothing written, not even the root
    # a pre-check that cannot read public data fails, still unsigned
    feed.fail = True
    assert runner.main(argv) == 1
    assert "pre-check failed" in capsys.readouterr().err and not (tmp_path / "perp_demo").exists()


def test_only_flow01_signed_run_checks_every_book_then_steps_flow01_alone(runner, tmp_path, monkeypatch, capsys):
    from tests.test_perp_flowflush import E, FakeFeed
    from trading_agent.execution.perp import flowflush, learnbook

    for k in ("TRADING_KILL_SWITCH", "TRADING_ENTRY_KILL_SWITCH", "BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("TRADING_MODE", "testnet")
    monkeypatch.setenv("TRADING_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("BINANCE_DEMO_FUTURES_API_KEY", "demo-key")
    monkeypatch.setenv("BINANCE_DEMO_FUTURES_API_SECRET", "demo-secret")
    monkeypatch.setenv("LIVE_SAFETY_HMAC_KEY", KEY)
    fake = FakeUsdm({"BTCUSDT": 80_000.0, "LTCUSDT": 100.0, "OLDUSDT": 10.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    feed = FakeFeed({E})
    monkeypatch.setattr(runner, "flow01_feed", lambda: feed)

    class NoSignal:   # book FLOW02's step must not run in this mode
        def update(self, now_ms):
            raise AssertionError("FLOW02 stepped")

        decide = update

    monkeypatch.setattr(runner, "flow02_signal", lambda root: NoSignal())
    root = tmp_path / "perp_demo"
    # book LEARN holds a leg: its check must find it at the venue, and the pair book must count it
    learn_state = {"kind": "book", "status": "ok", "base_usd": 2000.0, "high": 2000.0, "realized": 0.0, "fees": 0.0,
                   "since": "2026-11-05", "created_ms": E, "legs": {"LTCUSDT": {"qty": 1.0, "avg": 100.0, "fills": [],
                                                                                 "closing_key": None}}}
    PairStore(root / runner.LEARN_STORE, KEY, {learnbook.BOOK: learn_state}).save()
    learn_bytes = (root / runner.LEARN_STORE).read_bytes()
    hold(fake, "LTCUSDT", 1.0)
    argv = ["--execute", "--only", "flow01", *BOOK_FLAGS.split(), *roots(tmp_path)]

    def run(at: int) -> dict:
        monkeypatch.setattr(runner, "now_ms", lambda: at)
        assert runner.main(argv) == 0
        return json.loads(capsys.readouterr().out.splitlines()[-1])

    rep = run(E + 5 * 60_000 + 20_000)                   # a fresh event
    assert rep["only"] == "flow01" and rep["only_reason"] == "fresh_event" and rep["flow01"]["entered"] == 1
    assert not {"lv", "learn", "flow02", "krw"} & set(rep) and rep["entered"] == []
    (buy,) = orders_sent(fake)
    assert buy[5]["clientOrderId"].startswith("fb1-") and buy[3] == "buy"
    assert pos(fake, "BTCUSDT") == pytest.approx(0.012) and pos(fake, "LTCUSDT") == pytest.approx(1.0)
    assert (root / runner.LEARN_STORE).read_bytes() == learn_bytes        # LEARN checked, not stepped
    assert not any((root / f).exists() for f in ("pairs.json", "lv_blocks.json", runner.FLOW02_STORE, runner.KRW_STORE))
    status = json.loads((root / "status.json").read_text())
    assert status["run"] == "flow01" and status["flow01_open_legs"] == 1 and status["learn_legs"] == 1
    assert status["flow02_status"] == "ok" and status["lv_blocks"]["open"] == 0
    assert not [k for k, v in status.items() if isinstance(v, float)]    # counts only
    started = [e["details"] for e in events(root) if e["event"] == "perp_run_started"][-1]
    assert started["only"] == "flow01" and started["only_reason"] == "fresh_event" and started["learn_usd"] == 2000.0
    # a quiet moment while the leg is held: nothing is signed or written
    calls, audit = len(fake.calls), (root / "audit.jsonl").read_bytes()
    entry_ms = PairStore.load(root / flowflush.STORE, KEY).pairs[flowflush.event_id(E)]["entry_ms"]
    monkeypatch.setattr(runner, "now_ms", lambda: entry_ms + 3600_000)
    assert runner.main(argv) == 0
    assert json.loads(capsys.readouterr().out) == {"only": "flow01", "signed_run": False}
    assert len(fake.calls) == calls and (root / "audit.jsonl").read_bytes() == audit
    # the exit falls due: a signed run sells FLOW01's own quantity, reduce-only
    rep = run(entry_ms + flowflush.HOLD_MS + 60_000)
    assert rep["only_reason"] == "exit_due" and rep["flow01"]["closed"] == 1
    sell = orders_sent(fake)[-1]
    assert sell[3] == "sell" and sell[4] == pytest.approx(0.012) and sell[5]["reduceOnly"]
    assert pos(fake, "BTCUSDT") == pytest.approx(0.0) and pos(fake, "LTCUSDT") == pytest.approx(1.0)


def test_a_dry_run_plans_every_book_and_writes_no_store(runner, tmp_path, monkeypatch, capsys):
    from tests.test_perp_flowflush import E, FakeFeed

    fake = FakeUsdm({"BTCUSDT": 80_000.0, "OLDUSDT": 10.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    monkeypatch.setattr(runner, "flow01_feed", lambda: FakeFeed())
    monkeypatch.setattr(runner, "mainnet_listings", lambda: {})
    monkeypatch.setattr(runner, "now_ms", lambda: E + 60_000)

    class Waiting:   # the FLOW02 store is not seeded yet
        def update(self, now_ms):
            raise AssertionError("a dry run fetched FLOW02 data")

        def decide(self, day_ms, *, record):
            assert record is False
            return {"ok": False, "missing": ["store"]}

    monkeypatch.setattr(runner, "flow02_signal", lambda root: Waiting())
    monkeypatch.setattr(runner, "load_demo_credentials", lambda env: (_ for _ in ()).throw(AssertionError("signed")))
    assert runner.main([*BOOK_FLAGS.split(), *roots(tmp_path)]) == 0
    rep = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert rep["execute"] is False and {"lv", "learn", "flow02", "flow01", "krw"} <= set(rep)
    assert rep["learn"]["bars"] == "no_bar_store" and rep["flow01"]["state"] == "idle"

    def no_floats(x) -> bool:
        if isinstance(x, dict):
            return all(no_floats(v) for v in x.values())
        if isinstance(x, list):
            return all(no_floats(v) for v in x)
        return not isinstance(x, float)

    assert no_floats(rep)
    stores = ("pairs.json", "lv_blocks.json", "learn_book.json", "flow02.json", "flow01.json", "krw.json", "status.json",
              "audit.jsonl", "order_state.json")
    assert not any((tmp_path / "perp_demo" / f).exists() for f in stores)
    assert not orders_sent(fake)
