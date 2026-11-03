"""The new perp pair book on a fake Binance USD-M demo venue: no network."""

from __future__ import annotations

import json

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
                                 "type": "swap", "swap": True, "linear": True, "active": True, "limits": {}}
        self.markets_by_id = {m["id"]: [m] for m in self.markets.values()}
        self.positions: dict[str, float] = {}     # ccxt symbol -> signed qty
        self.entry: dict[str, float] = {}
        self.orders: dict[str, dict] = {}         # client id -> order
        self.algo: dict[str, dict] = {}           # open stop orders by client id
        self.hedged = False
        self.calls: list[tuple] = []
        self.fail_next: str | None = None
        self.leverage: dict[str, int] = {}

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

    def amount_to_precision(self, sym, amt):
        return f"{float(amt):.3f}"

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
    # The next run finds the fill by key, then refuses the short the book never opened.
    book2 = Book(book.venue, LiveRiskStateStore(tmp / "order_state.json", integrity_key=KEY),
                 PairStore.load(tmp / "pairs.json", KEY), tmp / "audit.jsonl", 100.0, sleep=lambda s: None)
    with pytest.raises(PerpBookError, match="do not match"):
        run_cycle(book2, now_ms=T0 + 5 * H, mainnet_info=i, execute=True)
    assert book2.orders.state.order_ledger[short_key]["status"] == "filled"


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
