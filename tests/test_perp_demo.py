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
    assert lines[1] == "testnet -u scripts/live_perp_demo.py --execute --shadow"
    assert re.fullmatch(r"=== \S+Z perp_demo end rc=3", lines[2])
    assert not re.search(r"^[^#]*\btimeout\b", WRAPPER.read_text(encoding="utf8"), re.M)


def test_the_task_runs_the_wrapper_hourly_and_headless() -> None:
    text = TASKS.read_text(encoding="utf8")
    assert '-TaskName "TradingAgent-PerpDemo" -Action $perpdemo' in text
    assert "--headless wsl.exe -d $Distro -u $User --exec $Repo/scripts/ops/run_perp_demo.sh" in text
    assert re.search(r"perpdemoTrigger = .*?-RepetitionInterval \(New-TimeSpan -Hours 1\)", text, re.S)
