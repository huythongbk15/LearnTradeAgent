"""Book LEARN on a fake Binance USD-M demo venue: the H-LEARN-11 daily decisions as orders. No network."""

from __future__ import annotations

import json
import os
from pathlib import Path

import ccxt
import pandas as pd
import pytest

import trading_agent.exchanges.ccxt_adapter as ca
from tests.test_learn_paper import ConstModel, fake_transport, seed_store, synth
from tests.test_perp_demo import KEY, FakeBook, FakeUsdm, Killed, events, hold, orders_sent, pos, write_picks
from tools.learnpaper import model as M
from tools.learnpaper import run as R
from tools.newperp import fetch as fx
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
from trading_agent.execution.live_safety import LiveRiskStateStore
from trading_agent.execution.perp import learnbook as lb
from trading_agent.execution.perp.book import Book, PairStore, PerpBookError, run_cycle
from trading_agent.execution.perp.learnbook import LearnBook
from trading_agent.execution.perp.lowvol import LowVolBook
from trading_agent.execution.perp.venue import PerpDemoVenue, demo_config

H = lb.HOUR_MS
DAY = lb.DAY_MS
MON = int(pd.Timestamp("2026-11-09", tz="UTC").timestamp() * 1000)   # a Monday 00:00 UTC
D = MON + DAY                                                         # Tuesday 2026-11-10
RUN = 15 * 60_000                                                     # the hourly run at :15
REAL_PREDICT = lb.predict
STEPS = [4.0, 5.0, 8.0, 10.0, 20.0, 25.0, 40.0, 50.0, 2.0, 0.5]
# Every list coin trades on demo but HBAR; AAA is another book's coin.
PRICES = {"BTCUSDT": 80_000.0, "AAAUSDT": 1.0,
          **{f"{c}USDT": STEPS[i % 10] for i, c in enumerate(M.COINS) if c != "HBAR"}}


def size(coin: str, usd: float = 2000.0) -> float:
    return usd / 20 / PRICES[f"{coin}USDT"]


class World:
    """The demo account, the paper's bar store and a stubbed model; each run is a new process."""

    def __init__(self, tmp: Path, fake: FakeUsdm, venue: PerpDemoVenue):
        self.tmp, self.fake, self.venue = tmp, fake, venue
        self.paper = tmp / "learn_paper"
        self.picks = tmp / "selection" / "lv_book" / "picks.jsonl"
        self.store = tmp / "learn_book.json"
        self.bars = synth(start="2026-09-20", end="2026-11-20")
        self.preds: dict[str, float] = {}
        self.fits: list = []
        self.const = 0.0
        self.down = False
        self.dead: set[str] = set()     # coins whose mainnet bars no longer come

    def transport(self, url, timeout):
        if self.down:
            raise OSError("network down")
        if any(f"symbol={c}USDT" in url for c in self.dead):
            return 200, b"[]"
        return fake_transport(self.bars)(url, timeout)

    def client(self):
        return fx.Client(self.transport, pace=0, sleep=lambda s: None)

    def book(self) -> Book:
        orders = LiveRiskStateStore(self.tmp / "order_state.json", integrity_key=KEY)
        return Book(self.venue, orders, PairStore.load(self.tmp / "pairs.json", KEY), self.tmp / "audit.jsonl", 100.0,
                    sleep=lambda s: None)

    def learn(self, book: Book, usd: float = 2000.0, *, peek: bool = False) -> LearnBook:
        store = PairStore.peek(self.store) if peek else PairStore.load(self.store, KEY)
        return LearnBook(book, store, usd, self.paper, self.picks, self.client, lambda c: c in {"ETH", "SOL"})

    def run(self, at: int, *, usd: float = 2000.0, others=(), entry_lock: str | None = None, lv: bool = False) -> dict:
        book = self.book()
        books = list(others)
        if lv:
            books.insert(0, LowVolBook(book, PairStore.load(self.tmp / "lv_blocks.json", KEY), 1000.0, self.picks,
                                       lambda c: False))
        return run_cycle(book, now_ms=at, mainnet_info={}, execute=True, entry_lock=entry_lock,
                         others=[*books, self.learn(book, usd)])

    def saved(self) -> PairStore:
        return PairStore.load(self.store, KEY)


@pytest.fixture
def w(tmp_path, monkeypatch) -> World:
    fake = FakeUsdm(PRICES)
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    monkeypatch.setattr(os, "fsync", lambda fd: None)     # the signed stores' durability is not under test; it costs seconds
    venue = PerpDemoVenue(CCXTAdapter(demo_config("demo-key", "demo-secret")))
    venue.connect()
    world = World(tmp_path, fake, venue)
    # the paper's store, seeded before Sunday; each decision appends the bars it needs
    seed_store(world.paper, {c: b[b.index < pd.Timestamp("2026-11-08", tz="UTC")] for c, b in world.bars.items()})
    monkeypatch.setattr(M, "fit", lambda bars, q: (world.fits.append(q), ConstModel(world.const), ["r1"], 7)[1:])
    monkeypatch.setattr(lb, "predict", lambda model, feats, cols, t, coins: {c: world.preds.get(c, 0.0) for c in coins})
    return world


def learn_orders(fake, since: int = 0) -> list[tuple[str, str, float, bool]]:
    return [(c[5]["clientOrderId"], c[3], c[4], bool(c[5].get("reduceOnly"))) for c in orders_sent(fake)[since:]
            if c[5]["clientOrderId"].startswith("lrn-")]


def counts_only(obj) -> None:
    """A report or audit detail of this book: counts, statuses, quantities' counts and keys; never a float."""

    if isinstance(obj, dict):
        for v in obj.values():
            counts_only(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            counts_only(v)
    else:
        assert obj is None or isinstance(obj, (bool, int, str)), obj


def learn_events(tmp: Path) -> list[dict]:
    return [e for e in events(tmp) if e["event"].startswith("perp_learn")]


def test_slots_match_the_paper_list():
    assert lb.SLOTS == len(M.COINS) == 20
    assert lb.decision_bar(D // DAY) == pd.Timestamp("2026-11-09 23:00", tz="UTC")
    assert lb.window(D // DAY) == (D, D + 2 * H)


def test_one_decision_a_day_inside_its_window(w):
    w.preds.update(LTC=0.01, BCH=-0.01, XLM=0.001)       # XLM is inside the round trip: flat
    rep = w.run(D - H + RUN)                              # Monday 23:15: the book starts; Monday's window is gone
    assert rep["learn"] == {"day": "2026-11-09", "status": "ok", "held": 0}
    assert not orders_sent(w.fake)
    rep = w.run(D + RUN)
    assert rep["learn"] == {"day": "2026-11-10", "decided": "2026-11-10", "long": 1, "short": 1, "flat": 18,
                            "skipped": 0, "missing": 0, "capped": False, "orders": 2, "status": "ok", "held": 2}
    assert learn_orders(w.fake) == [("lrn-261110-BCHUSDT-o0", "sell", size("BCH"), False),
                                    ("lrn-261110-LTCUSDT-o0", "buy", size("LTC"), False)]
    assert pos(w.fake, "LTCUSDT") == pytest.approx(25.0) and pos(w.fake, "BCHUSDT") == pytest.approx(-20.0)
    assert w.fake.leverage["LTC/USDT:USDT"] == 1
    # the decision record: bar, model, predictions, targets, orders with keys, marks at send and fills
    store = w.saved()
    rec = store.pairs["261110"]
    assert rec["status"] == "done" and rec["bar"] == "2026-11-09T23:00Z" and rec["model"] == "2026Q4"
    assert rec["pred"]["LTC"] == 0.01 and rec["pos"]["XLM"] == 0
    assert rec["target"] == {"LTCUSDT": pytest.approx(25.0), "BCHUSDT": pytest.approx(-20.0)}
    (fill,) = [f for f in rec["fills"] if f["symbol"] == "LTCUSDT"]
    assert fill == {"key": "lrn-261110-LTCUSDT-o0", "side": "buy", "qty": 25.0, "avg": 4.0, "fees": {}, "mark": 4.0,
                    "symbol": "LTCUSDT"}
    assert "2026-11-10" in store.seen
    # the paper's bar store now holds the decision bar; its ledger is never written here
    assert R.load_bars(w.paper)["LTC"].index[-1] == pd.Timestamp("2026-11-09 23:00", tz="UTC")
    assert not (w.paper / "ledger.jsonl").exists()
    # a second run inside the window, and one after it, trade nothing
    n = len(orders_sent(w.fake))
    assert "decided" not in w.run(D + H + RUN)["learn"]
    w.run(D + 5 * H + RUN)
    assert len(orders_sent(w.fake)) == n
    # the next day trades only the difference: BCH off, XLM on, LTC kept
    w.preds.update(BCH=0.0, XLM=-0.01)
    rep = w.run(D + DAY + RUN)
    assert rep["learn"]["orders"] == 2
    assert learn_orders(w.fake, n) == [("lrn-261111-BCHUSDT-c0", "buy", 20.0, True),
                                       ("lrn-261111-XLMUSDT-o0", "sell", size("XLM"), False)]
    assert pos(w.fake, "LTCUSDT") == pytest.approx(25.0)
    for e in learn_events(w.tmp):
        counts_only(e["details"])
    counts_only(rep["learn"])


def test_a_missed_day_closes_every_leg_at_the_window_end(w):
    w.preds.update(LTC=0.01, BCH=-0.01)
    w.run(D + RUN)
    n = len(orders_sent(w.fake))
    rep = w.run(D + DAY + 2 * H + RUN)                    # no run inside Wednesday's window
    assert rep["learn"]["missed"] == 1 and rep["learn"]["closed"] == 2 and rep["learn"]["held"] == 0
    assert learn_orders(w.fake, n) == [("lrn-261111m-BCHUSDT-c0", "buy", 20.0, True),
                                       ("lrn-261111m-LTCUSDT-c0", "sell", 25.0, True)]
    assert pos(w.fake, "LTCUSDT") == 0 and pos(w.fake, "BCHUSDT") == 0
    store = w.saved()
    assert store.pairs["261111"]["status"] == "missed" and store.pairs["261111"]["reason"] == "no_run"
    assert learn_events(w.tmp)[-1]["details"] == {"days": ["2026-11-11"], "reasons": ["no_run"], "closed": 2}
    # a fetch that fails leaves the day to a later run in the window, then misses it with that reason
    w.down = True
    assert w.run(D + 2 * DAY + RUN)["learn"]["wait"] == "bars_failed"
    rep = w.run(D + 2 * DAY + 2 * H + RUN)
    assert rep["learn"]["missed"] == 1 and rep["learn"]["closed"] == 0
    assert w.saved().pairs["261112"]["reason"] == "bars_failed"
    w.down = False
    assert w.run(D + 3 * DAY + RUN)["learn"]["decided"] == "2026-11-13"


def test_skips_another_books_coin_this_weeks_lv_pick_and_a_coin_not_on_demo(w):
    write_picks(w.picks, (MON, ["XLM"], ["FIL"]))
    hold(w.fake, "AAVEUSDT", 3.0)
    other = FakeBook("flow02", {"AAVEUSDT": 3.0})
    w.preds.update(LTC=0.01, XLM=0.01, FIL=-0.01, AAVE=-0.01, HBAR=0.01)
    rep = w.run(D + RUN, others=[other])
    assert rep["learn"]["long"] == 3 and rep["learn"]["short"] == 2 and rep["learn"]["skipped"] == 4
    assert w.saved().pairs["261110"]["skipped"] == {"XLM": "lv_pick", "FIL": "lv_pick", "AAVE": "held_by_flow02",
                                                    "HBAR": "not_on_demo"}
    assert [o[0] for o in learn_orders(w.fake)] == ["lrn-261110-LTCUSDT-o0"]
    assert pos(w.fake, "AAVEUSDT") == pytest.approx(3.0)


def test_a_coin_in_the_new_lv_pick_is_closed_at_the_monday_decision_and_lv_rolls_into_it(w):
    w.preds.update(XLM=0.01, FIL=-0.01, LTC=0.01)
    w.run(MON - DAY + RUN, lv=True)                       # Sunday: no pick yet, so XLM and FIL are traded
    assert pos(w.fake, "XLMUSDT") == pytest.approx(size("XLM")) and pos(w.fake, "FILUSDT") == pytest.approx(-size("FIL"))
    write_picks(w.picks, (MON, ["XLM"], ["FIL"]))        # the selection run, Monday 00:07
    n = len(orders_sent(w.fake))
    rep = w.run(MON + RUN, lv=True)
    assert rep["learn"]["skipped"] == 2
    assert learn_orders(w.fake, n) == [("lrn-261109-FILUSDT-c0", "buy", size("FIL"), True),
                                       ("lrn-261109-XLMUSDT-c0", "sell", size("XLM"), True)]
    # LV's 04:00 roll finds them free; both books explain their own legs to the pair book
    rep = w.run(MON + 4 * H + RUN, lv=True)
    assert rep["lv"]["rolled"] and rep["lv"]["skipped"] == 0
    assert pos(w.fake, "XLMUSDT") == pytest.approx(500 / PRICES["XLMUSDT"])
    rep = w.run(D + RUN, lv=True)                         # Tuesday: LV holds them now
    assert w.saved().pairs["261110"]["skipped"] == {"XLM": "held_by_lv", "FIL": "held_by_lv"}
    assert pos(w.fake, "LTCUSDT") == pytest.approx(25.0)


def test_the_alt_short_cap_scales_every_adding_leg_pro_rata(w):
    coins = M.COINS[:10]
    w.preds.update({c: 0.01 if i < 5 else -0.01 for i, c in enumerate(coins)})   # 500 long, 500 short
    hold(w.fake, "AAAUSDT", -3800.0)                     # 3,800 of the 4,000 alt-short cap: 200 left for 500
    other = FakeBook("flow01", {"AAAUSDT": -3800.0})
    rep = w.run(D + RUN, others=[other])
    assert rep["learn"]["capped"] is True and rep["learn"]["orders"] == 10
    for i, c in enumerate(coins):
        assert pos(w.fake, f"{c}USDT") == pytest.approx((1 if i < 5 else -1) * 0.4 * size(c), abs=1e-3)
    rec = w.saved().pairs["261110"]
    assert rec["capped"] is True and rec["scale"] == pytest.approx(0.4)
    assert rec["target"][f"{coins[0]}USDT"] == pytest.approx(size(coins[0]))     # the target is recorded unscaled
    # a cap never blocks a reduction: far over both caps, the next day still goes flat
    w.fake.fetch_balance = lambda: {"info": {"totalMarginBalance": "10"}}
    w.preds.clear()
    w.run(D + DAY + RUN, others=[other])
    assert all(pos(w.fake, f"{c}USDT") == 0 for c in coins)


def test_the_gross_cap_scales_a_long_book(w):
    w.preds.update({c: 0.01 for c in M.COINS[:10]})      # 1,000 long
    w.fake.fetch_balance = lambda: {"info": {"totalMarginBalance": "1000"}}       # 900 of gross room
    rep = w.run(D + RUN)
    assert rep["learn"]["capped"] is True
    assert w.saved().pairs["261110"]["scale"] == pytest.approx(0.9)
    assert pos(w.fake, "LTCUSDT") == pytest.approx(0.9 * 25.0)


def test_a_flip_goes_through_flat(w):
    w.preds.update(LTC=0.01)
    w.run(D + RUN)
    n = len(orders_sent(w.fake))
    w.preds.update(LTC=-0.01)
    w.run(D + DAY + RUN)
    assert learn_orders(w.fake, n) == [("lrn-261111-LTCUSDT-c0", "sell", 25.0, True),
                                       ("lrn-261111-LTCUSDT-o0", "sell", 25.0, False)]
    assert pos(w.fake, "LTCUSDT") == pytest.approx(-25.0)


def test_a_halt_flattens_the_book_and_it_trades_nothing_more(w):
    demo = [c for c in M.COINS if c != "HBAR"]
    w.preds.update({c: 0.01 for c in demo})                # 19 longs of 100
    w.run(D + RUN)
    base = dict(w.fake.prices)

    def move(x: float) -> None:
        w.fake.prices.update({f"{c}USDT": base[f"{c}USDT"] * x for c in demo})

    move(0.76)                                            # 1,544 of 2,000: above 75% of the high
    assert w.run(D + 3 * H + RUN)["learn"]["status"] == "ok"
    move(1.10)                                            # 2,190: a new high
    assert w.run(D + 4 * H + RUN)["learn"]["status"] == "ok"
    move(0.76)                                            # 1,544 of 2,190: below 75%
    n = len(orders_sent(w.fake))
    rep = w.run(D + 5 * H + RUN)
    assert rep["learn"] == {"day": "2026-11-10", "status": "halted", "closed": 19, "held": 0}
    sent = learn_orders(w.fake, n)
    assert len(sent) == 19 and all(o[0].startswith("lrn-261110h-") and o[3] for o in sent)
    assert all(pos(w.fake, f"{c}USDT") == 0 for c in demo)
    last = learn_events(w.tmp)[-1]
    assert last["event"] == "perp_learn_halted" and last["details"] == {"reason": "drawdown", "closed": 19}
    assert w.saved().pairs[lb.BOOK]["status"] == "halted"
    # it trades nothing more, not even a new day's decision
    n = len(orders_sent(w.fake))
    move(1.0)
    assert w.run(D + DAY + RUN)["learn"] == {"day": "2026-11-11", "status": "halted", "held": 0}
    assert len(orders_sent(w.fake)) == n


def test_a_halt_cut_off_midway_stays_halted_and_finishes_its_close(w):
    demo = [c for c in M.COINS if c != "HBAR"]
    w.preds.update({c: 0.01 for c in demo})                # 19 longs of 100
    w.run(D + RUN)
    base = dict(w.fake.prices)

    def move(x: float) -> None:
        w.fake.prices.update({f"{c}USDT": base[f"{c}USDT"] * x for c in demo})

    move(0.70)                                            # 1,430 of 2,000: below 75%, the halt fires
    original, n0 = w.fake.create_order, len(orders_sent(w.fake))

    def create(sym, typ, side, amount, price=None, params=None):
        out = original(sym, typ, side, amount, price, params)
        if len(orders_sent(w.fake)) == n0 + 3:            # the third halt close reached the venue; the process dies
            raise Killed()
        return out

    w.fake.create_order = create
    with pytest.raises(Killed):
        w.run(D + 3 * H + RUN)
    w.fake.create_order = original
    assert w.saved().pairs[lb.BOOK]["status"] == "halted"  # saved before the first close
    move(0.95)                                            # a rebound does not undo the halt
    rep = w.run(D + 4 * H + RUN)
    assert rep["learn"] == {"day": "2026-11-10", "status": "halted", "closed": 16, "held": 0}
    assert all(pos(w.fake, f"{c}USDT") == 0 for c in demo)
    n = len(orders_sent(w.fake))
    assert w.run(D + DAY + RUN)["learn"] == {"day": "2026-11-11", "status": "halted", "held": 0}
    assert len(orders_sent(w.fake)) == n                  # nothing more, not even a new day's decision


def test_a_halt_close_does_not_stop_on_a_mark_it_cannot_read(w):
    demo = [c for c in M.COINS if c != "HBAR"]
    w.preds.update({c: 0.01 for c in demo})
    w.run(D + RUN)
    w.fake.prices.update({f"{c}USDT": w.fake.prices[f"{c}USDT"] * 0.70 for c in demo})
    fourth = sorted(f"{c}USDT" for c in demo)[3]          # the close walks the legs; this leg's ticker times out
    orig = w.fake.fetch_ticker
    w.fake.fetch_ticker = lambda sym: (_ for _ in ()).throw(ConnectionError("timeout")) \
        if w.fake.markets[sym]["id"] == fourth else orig(sym)
    rep = w.run(D + 3 * H + RUN)
    assert rep["learn"] == {"day": "2026-11-10", "status": "halted", "closed": 19, "held": 0}
    assert all(pos(w.fake, f"{c}USDT") == 0 for c in demo)
    rows = [r for r in w.saved().pairs["261110h"]["fills"] if r["symbol"] == fourth]
    assert len(rows) == 1 and rows[0]["mark"] is None and rows[0]["avg"] > 0


def test_the_entry_lock_lets_only_reductions_through(w):
    w.preds.update(LTC=0.01, BCH=0.01)
    w.run(D + RUN)
    n = len(orders_sent(w.fake))
    w.preds.update(LTC=-0.01, XLM=0.01)                   # LTC flips, BCH stays, XLM is new
    rep = w.run(D + DAY + RUN, entry_lock="operator")
    assert rep["learn"]["decided"] == "2026-11-11" and rep["learn"]["held_back"] == 2
    assert learn_orders(w.fake, n) == [("lrn-261111-LTCUSDT-c0", "sell", 25.0, True)]
    assert pos(w.fake, "LTCUSDT") == 0 and pos(w.fake, "BCHUSDT") == pytest.approx(20.0) and pos(w.fake, "XLMUSDT") == 0
    rec = w.saved().pairs["261111"]
    assert rec["entry_lock"] == "operator" and rec["held_back"] == ["LTCUSDT", "XLMUSDT"]
    # a missed day still closes under the lock
    rep = w.run(D + 2 * DAY + 2 * H + RUN, entry_lock="operator")
    assert rep["learn"]["closed"] == 1 and pos(w.fake, "BCHUSDT") == 0


def test_a_dry_run_sends_signs_and_writes_nothing(w):
    def snapshot() -> dict:
        return {p.relative_to(w.tmp): p.read_bytes() for p in w.tmp.rglob("*") if p.is_file()}

    def plan(at: int) -> dict:
        book = w.book()
        return run_cycle(book, now_ms=at, mainnet_info={}, execute=False, others=[w.learn(book, peek=True)])["learn"]

    before = snapshot()
    rep = plan(D + RUN)
    assert rep == {"day": "2026-11-10", "in_window": True, "status": "new", "decided": False, "held": 0,
                   "bar": "2026-11-09T23:00Z", "missing": 20, "model": "2026Q4"}    # bars are never fetched
    assert snapshot() == before
    seed_store(w.paper, {c: b[b.index < pd.Timestamp(D, unit="ms", tz="UTC")] for c, b in w.bars.items()})
    before = snapshot()
    rep = plan(D + RUN)
    assert rep["missing"] == 0 and rep["model_state"] == "model_missing" and w.fits == []   # never fitted
    R.model_for(w.paper, R.load_bars(w.paper), pd.Timestamp("2026-10-01", tz="UTC"), log=lambda *_: None)
    write_picks(w.picks, (MON, ["XLM"], []))
    w.preds.update(LTC=0.01, BCH=-0.01, XLM=0.01, HBAR=0.01)
    before = snapshot()
    rep = plan(D + RUN)
    assert rep["model_state"] == "ok" and (rep["long"], rep["short"], rep["flat"]) == (1, 1, 16)
    assert rep["skipped"] == {"lv_pick": 1, "not_on_demo": 1}
    assert snapshot() == before and w.fits == [pd.Timestamp("2026-10-01", tz="UTC")]
    assert not orders_sent(w.fake) and not w.store.exists()
    counts_only(rep)


def test_the_quarter_model_is_fitted_once_through_the_paper_function(w, monkeypatch):
    monkeypatch.setattr(lb, "predict", REAL_PREDICT)
    w.const = 0.003                                       # every coin long, through the real features and predict
    rep = w.run(D + RUN)
    assert rep["learn"]["long"] == 20 and rep["learn"]["skipped"] == 1           # HBAR is not on demo
    assert w.fits == [pd.Timestamp("2026-10-01", tz="UTC")]
    assert set(json.loads((w.paper / "models" / "manifest.json").read_text())) == {"2026Q4"}
    assert learn_events(w.tmp)[1]["event"] == "perp_learn_model_fitted"
    w.run(D + DAY + RUN)
    assert len(w.fits) == 1


def test_no_fit_while_a_coins_bars_end_before_the_quarter_start(w):
    w.dead = {"CHZ"}
    old = w.bars["CHZ"]
    seed_store(w.paper, {"CHZ": old[old.index < pd.Timestamp("2026-09-30", tz="UTC")]})
    w.preds.update(LTC=0.01)
    rep = w.run(D + RUN)
    assert rep["learn"]["wait"] == "model_wait" and w.fits == [] and not orders_sent(w.fake)
    assert not (w.paper / "models").exists()


def test_a_run_killed_midway_is_settled_from_the_order_ledger(w):
    coins = M.COINS[:5]
    w.preds.update({c: 0.01 for c in coins})
    original = w.fake.create_order

    def dies_on(k):
        def create(sym, typ, side, amount, price=None, params=None):
            out = original(sym, typ, side, amount, price, params)
            if len(orders_sent(w.fake)) == k:
                raise Killed()        # the venue took the order; the process died before recording it
            return out
        return create

    w.fake.create_order = dies_on(3)
    with pytest.raises(Killed):
        w.run(D + RUN)
    w.fake.create_order = original
    assert w.saved().pairs["261110"]["status"] == "running"
    rep = w.run(D + H + RUN)                              # a new process, still inside the window
    assert rep["learn"]["decided"] == "2026-11-10" and rep["learn"]["orders"] == 2
    for c in coins:
        assert pos(w.fake, f"{c}USDT") == pytest.approx(size(c))
    opened = [o[0] for o in learn_orders(w.fake)]
    assert len(opened) == 5 and len({k.split("-")[2] for k in opened}) == 5    # one opening order a coin
    rec = w.saved().pairs["261110"]
    assert rec["status"] == "done" and rec["attempts"] == 2 and len(rec["fills"]) == 5
    assert [f["key"] for f in rec["fills"] if f.get("from") == "ledger"] == [opened[2]]
    # a fill settled from the ledger has no mark at send: None, not a price of 0
    assert [f["mark"] for f in rec["fills"] if f.get("from") == "ledger"] == [None]
    assert any(e["event"] == "perp_learn_interrupted" for e in learn_events(w.tmp))
    # killed mid-close: the next run records the close from the ledger and finishes the day's flat book
    w.preds.clear()
    n = len(orders_sent(w.fake))
    w.fake.create_order = dies_on(n + 2)
    with pytest.raises(Killed):
        w.run(D + DAY + RUN)
    w.fake.create_order = original
    w.run(D + DAY + H + RUN)
    assert all(pos(w.fake, f"{c}USDT") == 0 for c in coins)
    assert len(learn_orders(w.fake, n)) == 5 and w.saved().pairs[lb.BOOK]["legs"] == {}
    assert w.book().orders.unfinished_orders() == {}


def lose_reply(fake, key: str):
    """The venue fills the order with this key, and its reply is lost (a timeout after the fill)."""

    original = fake.create_order

    def create(sym, typ, side, amount, price=None, params=None):
        out = original(sym, typ, side, amount, price, params)
        if (params or {}).get("clientOrderId") == key:
            raise ccxt.RequestTimeout("read timeout")
        return out

    fake.create_order = create
    return original


@pytest.mark.parametrize("lost", ["lrn-261111-BCHUSDT-c0", "lrn-261111-BCHUSDT-o0"], ids=["reduction", "addition"])
def test_a_decision_that_fails_on_a_lost_reply_goes_flat_at_the_next_run(w, lost):
    """No order from the failure itself: the next run closes every leg after the reconcile settled the lost one."""

    if lost.endswith("c0"):
        w.preds.update(BCH=-0.01, LTC=0.01, XLM=0.01)    # day 1: BCH short, LTC and XLM long
        w.run(D + RUN)
        w.preds.update(BCH=0.0)                           # day 2: BCH off (its reply is lost), LTC and XLM kept
    else:
        w.preds.update(LTC=0.01)                          # day 1: LTC long
        w.run(D + RUN)
        w.preds.update(BCH=-0.01, XLM=0.01)               # day 2: BCH short added first (its reply is lost)
    n = len(orders_sent(w.fake))
    original = lose_reply(w.fake, lost)
    with pytest.raises(PerpBookError, match="outcome unknown"):
        w.run(D + DAY + RUN)
    w.fake.create_order = original
    assert [o[0] for o in learn_orders(w.fake, n)] == [lost]          # nothing is closed from stale records
    st = w.saved()
    assert st.pairs["261111"]["status"] == "failed" and "2026-11-11" in st.seen
    assert st.pairs[lb.BOOK]["flat_due"] == "261111f"
    m = len(orders_sent(w.fake))
    rep = w.run(D + DAY + H + RUN)                        # the next run: reconcile, settle, then the close
    assert rep["learn"]["closed"] >= 1 and rep["learn"]["held"] == 0
    sent = learn_orders(w.fake, m)
    assert sent and all(k.startswith("lrn-261111f-") and ro for k, _, _, ro in sent)
    assert all(pos(w.fake, s) == 0 for s in ("BCHUSDT", "LTCUSDT", "XLMUSDT"))
    assert w.book().orders.unfinished_orders() == {}
    assert not [e for e in events(w.tmp) if e["event"] == "perp_manual_intervention"]
    st = w.saved()
    assert st.pairs[lb.BOOK]["legs"] == {} and "flat_due" not in st.pairs[lb.BOOK]
    assert [f["key"] for f in st.pairs["261111"]["fills"] if f.get("from") == "ledger"] == [lost]
    assert w.run(D + DAY + 5 * H + RUN)["learn"]["held"] == 0           # the day is not retried
    assert w.run(D + 2 * DAY + RUN)["learn"]["decided"] == "2026-11-12"
    for e in learn_events(w.tmp):
        counts_only(e["details"])


def test_a_failed_days_close_that_fails_is_sent_again_until_the_book_is_flat(w):
    w.preds.update(LTC=0.01, BCH=0.01)
    w.run(D + RUN)
    w.preds.update(LTC=-0.01)                             # day 2: LTC flips; BCH stays
    lev = w.fake.set_leverage

    def busy(n, sym):                                     # prepare() before the short add: no order in flight
        if sym.startswith("LTC/"):
            raise ccxt.ExchangeError("leverage service busy")
        return lev(n, sym)

    w.fake.set_leverage = busy
    with pytest.raises(ccxt.ExchangeError):
        w.run(D + DAY + RUN)
    w.fake.set_leverage = lev
    assert pos(w.fake, "LTCUSDT") == 0 and pos(w.fake, "BCHUSDT") == pytest.approx(20.0)
    original = w.fake.create_order

    def expired(sym, typ, side, amount, price=None, params=None):    # a thin demo book: the close expires unfilled
        cid = (params or {}).get("clientOrderId", "")
        if cid.startswith("lrn-261111f-"):
            w.fake.calls.append(("order", sym, typ, side, amount, dict(params)))
            return {"id": "x" + cid, "clientOrderId": cid, "symbol": sym, "type": "market", "side": side,
                    "amount": amount, "filled": 0, "average": None, "cost": 0, "status": "expired"}
        return original(sym, typ, side, amount, price, params)

    w.fake.create_order = expired
    with pytest.raises(PerpBookError, match="expired"):
        w.run(D + DAY + H + RUN)
    w.fake.create_order = original
    assert pos(w.fake, "BCHUSDT") == pytest.approx(20.0) and w.saved().pairs[lb.BOOK]["flat_due"] == "261111f"
    rep = w.run(D + DAY + 3 * H + RUN)                    # the duty is kept: the close goes out again
    assert rep["learn"]["closed"] == 1 and rep["learn"]["held"] == 0 and pos(w.fake, "BCHUSDT") == 0
    keys = [o[0] for o in learn_orders(w.fake) if o[0].startswith("lrn-261111f-")]
    assert keys == ["lrn-261111f-BCHUSDT-c0", "lrn-261111f-BCHUSDT-c1"]
    assert "flat_due" not in w.saved().pairs[lb.BOOK] and w.book().orders.unfinished_orders() == {}


def test_a_leg_that_differs_from_the_venue_stops_the_run(w):
    w.preds.update(LTC=0.01)
    w.run(D + RUN)
    hold(w.fake, "LTCUSDT", -5.0)
    n = len(orders_sent(w.fake))
    with pytest.raises(PerpBookError, match="LEARN LTCUSDT"):
        w.run(D + 3 * H + RUN)
    assert len(orders_sent(w.fake)) == n and events(w.tmp)[-1]["event"] == "perp_learn_mismatch"


def test_a_new_size_waits_for_a_fresh_store(w):
    w.preds.update(LTC=0.01)
    w.run(D + RUN)
    assert w.run(D + DAY + RUN, usd=1000.0)["learn"]["wait"] == "size_changed"
    rep = w.run(D + DAY + 2 * H + RUN, usd=1000.0)        # the window passes: the book goes flat
    assert rep["learn"]["closed"] == 1 and pos(w.fake, "LTCUSDT") == 0
    with pytest.raises(PerpBookError, match="outside"):
        w.run(D + 2 * DAY + RUN, usd=5000.0)


def test_book_value_folds_fills_into_entry_realized_and_fees():
    st = {"base_usd": 2000.0, "realized": 0.0, "fees": 0.0, "high": 2000.0, "legs": {}}
    leg = {"qty": 15.0, "avg": 0.0, "fills": [
        {"key": "a", "side": "buy", "qty": 10.0, "avg": 4.0, "fees": {"USDT": 0.02}},
        {"key": "b", "side": "buy", "qty": 5.0, "avg": 7.0, "fees": {"USDT": 0.01, "BNB": 1.0}}]}
    st["legs"]["X"] = leg
    lb.fold_leg(leg, st)
    assert leg["avg"] == pytest.approx(5.0) and st["fees"] == pytest.approx(0.03) and leg["fills"] == []
    leg["qty"] -= 6.0
    leg["fills"] = [{"key": "c", "side": "sell", "qty": 6.0, "avg": 6.0, "fees": {}}]
    lb.fold_leg(leg, st)
    assert st["realized"] == pytest.approx(6.0) and leg["avg"] == pytest.approx(5.0)
    leg["qty"] -= 15.0                                    # through flat in one fill: 9 closed, 6 opened short at 4
    leg["fills"] = [{"key": "d", "side": "sell", "qty": 15.0, "avg": 4.0, "fees": {}}]
    lb.fold_leg(leg, st)
    assert st["realized"] == pytest.approx(6.0 - 9.0) and leg["avg"] == pytest.approx(4.0) and leg["qty"] == -6.0
    assert lb.book_value(st, {"X": 3.0}) == pytest.approx(2000.0 - 3.0 - 0.03 + 6.0)
