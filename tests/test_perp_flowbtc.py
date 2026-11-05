"""Book FLOW02 (H-FLOW-BTC-02) on a fake Binance USD-M demo venue: no network, no packet."""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import ccxt
import pytest

import trading_agent.exchanges.ccxt_adapter as ca
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
from trading_agent.execution.live_safety import LiveRiskStateStore
from trading_agent.execution.perp import flowbtc, rules
from trading_agent.execution.perp.book import Book, PairStore, PerpBookError, run_cycle
from trading_agent.execution.perp.venue import PerpDemoVenue, demo_config
from tests.test_perp_demo import KEY, FakeUsdm, info

H = rules.HOUR_MS
DAY = 24 * H
D = 1_789_948_800_000          # a Sunday, 00:00 UTC
RUN = D + H + 15 * 60_000      # the 01:15 run
# Keys a plan, a step, a status or an audit event may carry: statuses, counts, quantities, keys.
ALLOWED = {"day", "status", "in_window", "decided", "held", "target", "model", "missing", "missed", "waiting", "action",
           "reason", "qty", "key", "error", "closed", "size_changed"}


class FakeSignal:
    """tools.flowbtc.signal.Signal's surface: a call per day, set by the test."""

    def __init__(self):
        self.calls: dict[int, dict] = {}
        self.default = {"ok": True, "long": True, "pred": 0.01, "threshold": 0.005, "model": "2026Q3", "nan": 0}
        self.updates: list[int] = []
        self.fail_update = False
        self.records: list[bool] = []

    def update(self, now_ms):
        self.updates.append(now_ms)
        if self.fail_update:
            raise OSError("down")
        return {}

    def decide(self, day_ms, *, record):
        self.records.append(record)
        return dict(self.calls.get(day_ms, self.default))

    def call(self, day_ms, long=None, missing=None):
        self.calls[day_ms] = {"ok": False, "missing": missing} if missing else {**self.default, "long": long, "pred": 0.01 if long else -0.01}


@pytest.fixture
def fworld(tmp_path, monkeypatch):
    fake = FakeUsdm({"BTCUSDT": 80_000.0, "NEWUSDT": 2.0, "OLDUSDT": 10.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    venue = PerpDemoVenue(CCXTAdapter(demo_config("demo-key", "demo-secret")))
    venue.connect()
    orders = LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY)
    book = Book(venue, orders, PairStore.load(tmp_path / "pairs.json", KEY), tmp_path / "audit.jsonl", 100.0, sleep=lambda s: None)
    sig = FakeSignal()
    flow = flowbtc.FlowBtcBook(book, PairStore.load(tmp_path / "flow02.json", KEY), 1000.0, sig)
    return fake, book, flow, sig, tmp_path


def cycle(book, flow, at, **kw):
    return run_cycle(book, now_ms=at, mainnet_info=kw.pop("mainnet_info", {}), execute=True, others=[flow], **kw)


def btc(fake) -> float:
    return fake.positions.get("BTC/USDT:USDT", 0.0)


def flow_orders(fake) -> list[tuple]:
    return [c for c in fake.calls if c[0] == "order" and c[5]["clientOrderId"].startswith("fb2-")]


def events(tmp: Path, prefix="perp_flow02") -> list[dict]:
    return [e for e in (json.loads(x) for x in (tmp / "audit.jsonl").read_text().splitlines()) if e["event"].startswith(prefix)]


def store(tmp: Path) -> PairStore:
    return PairStore.load(tmp / "flow02.json", KEY)


def test_long_call_buys_once_holds_on_long_days_and_a_flat_call_sells_its_own_qty(fworld):
    fake, book, flow, sig, tmp = fworld
    rep = cycle(book, flow, RUN)
    assert rep["flow02"]["target"] == "long" and rep["flow02"]["action"] == "buy"
    assert btc(fake) == pytest.approx(0.012)          # 1,000 / 80,000 = 0.0125, cut to the 0.001 step
    (o,) = flow_orders(fake)
    assert o[3] == "buy" and not o[5].get("reduceOnly") and len(o[5]["clientOrderId"]) <= 36
    assert fake.leverage["BTC/USDT:USDT"] == 1
    cycle(book, flow, RUN + H)                        # the 02:15 run: the day is decided, nothing more
    cycle(book, flow, RUN + DAY)                      # next day long again: keep the position
    assert len(flow_orders(fake)) == 1
    sig.call(D + 2 * DAY, long=False)
    rep = cycle(book, flow, RUN + 2 * DAY)
    assert rep["flow02"]["action"] == "sell" and btc(fake) == 0.0
    sell = flow_orders(fake)[-1]
    assert sell[3] == "sell" and sell[4] == pytest.approx(0.012) and sell[5]["reduceOnly"]
    days = {k: r for k, r in store(tmp).pairs.items() if k.startswith("d")}
    assert [r["action"] for _, r in sorted(days.items())] == ["buy", "hold", "sell"]
    assert book.orders.unfinished_orders() == {}


def test_nothing_happens_outside_the_window(fworld):
    fake, book, flow, sig, tmp = fworld
    for at in (D + 15 * 60_000, D + 3 * H + 15 * 60_000, D + 23 * H):
        cycle(book, flow, at)
    assert flow_orders(fake) == [] and sig.updates == []


def test_btc_is_shared_with_the_pair_hedge_and_each_book_sells_only_its_own(fworld):
    fake, book, flow, sig, tmp = fworld
    i = info(("NEWUSDT", RUN - 4 * H, None, "COIN"))
    rep = cycle(book, flow, RUN, mainnet_info=i)
    assert rep["entered"] == ["NEWUSDT"] and btc(fake) == pytest.approx(0.013)   # hedge 0.001 + FLOW02 0.012
    sig.call(D + DAY, long=False)
    cycle(book, flow, RUN + DAY, mainnet_info=i)       # explained: 0.001 + 0.012; FLOW02 sells its 0.012
    assert btc(fake) == pytest.approx(0.001)
    fake.positions["BTC/USDT:USDT"] += 0.005           # BTC no book explains
    with pytest.raises(PerpBookError, match="do not match"):
        cycle(book, flow, RUN + 2 * DAY, mainnet_info=i)


def test_a_missed_day_sells_at_the_window_end_and_records_why(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)
    sig.call(D + DAY, missing=["metrics"])
    rep = cycle(book, flow, RUN + DAY)
    assert rep["flow02"]["waiting"] == "missing" and rep["flow02"]["missing"] == ["metrics"] and btc(fake) == pytest.approx(0.012)
    cycle(book, flow, RUN + DAY + H)
    rep = cycle(book, flow, RUN + DAY + 2 * H)        # 03:15: the window has passed
    assert rep["flow02"]["missed"] == 1 and btc(fake) == 0.0
    assert flow_orders(fake)[-1][5]["reduceOnly"]
    rec = store(tmp).pairs[f"d{flowbtc.day_iso(D + DAY)}"]
    assert rec["status"] == "missed" and rec["reason"] == "missing:metrics" and len(rec["attempts"]) == 2
    assert rec["close_key"].startswith("fb2-")
    assert events(tmp)[-1]["details"] == {"day": flowbtc.day_iso(D + DAY), "reason": "missing:metrics", "closed": True}


def test_days_before_the_first_run_are_not_missed_and_a_day_without_a_run_is(fworld):
    fake, book, flow, sig, tmp = fworld
    assert cycle(book, flow, D + 5 * H)["flow02"]["missed"] == 0
    rep = cycle(book, flow, D + DAY + 6 * H)           # no run in the next day's window
    assert rep["flow02"]["missed"] == 1
    assert store(tmp).pairs[f"d{flowbtc.day_iso(D + DAY)}"]["reason"] == "no_run"
    rep = cycle(book, flow, D + 4 * DAY + 6 * H)       # three days down: three more missed, once each
    assert rep["flow02"]["missed"] == 3 and cycle(book, flow, D + 4 * DAY + 7 * H)["flow02"]["missed"] == 0


def test_entry_lock_holds_back_a_buy_but_not_a_sell(fworld):
    fake, book, flow, sig, tmp = fworld
    rep = cycle(book, flow, RUN, entry_lock="kill switch")
    assert rep["flow02"]["waiting"] == "entry_lock" and flow_orders(fake) == []
    rep = cycle(book, flow, RUN + H)                    # the lock lifts inside the window: it buys
    assert rep["flow02"]["action"] == "buy" and btc(fake) == pytest.approx(0.012)
    sig.call(D + DAY, long=False)
    rep = cycle(book, flow, RUN + DAY, entry_lock="kill switch")
    assert rep["flow02"]["action"] == "sell" and btc(fake) == 0.0
    rep = cycle(book, flow, RUN + 2 * DAY, entry_lock="kill switch")
    rep = cycle(book, flow, RUN + 2 * DAY + 2 * H, entry_lock="kill switch")
    assert rep["flow02"]["missed"] == 1
    assert store(tmp).pairs[f"d{flowbtc.day_iso(D + 2 * DAY)}"]["reason"] == "entry_lock"


def test_the_gross_cap_skips_the_buy_and_records_it(fworld):
    fake, book, flow, sig, tmp = fworld
    fake.fetch_balance = lambda: {"info": {"totalMarginBalance": "1000"}}    # 900 of room: a 960 buy does not fit
    rep = cycle(book, flow, RUN)
    assert rep["flow02"]["action"] == "skipped" and rep["flow02"]["reason"] == "equity" and flow_orders(fake) == []
    rec = store(tmp).pairs[f"d{flowbtc.day_iso(D)}"]
    assert rec["status"] == "decided" and rec["action"] == "skipped" and rec["reason"] == "equity"
    assert [e["event"] for e in events(tmp)][-2:] == ["perp_flow02_entry_skipped", "perp_flow02_decided"]
    assert cycle(book, flow, RUN + H)["flow02"].get("action") is None          # skipped is final for the day


def test_drawdown_of_35_percent_halts_sells_and_trades_nothing_more(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)
    fake.prices["BTCUSDT"] = 51_000.0                  # sleeve 1,000 - 960 + 0.012 x 51,000 = 652: above 650
    assert cycle(book, flow, RUN + 5 * H)["flow02"]["status"] == "ok" and btc(fake) == pytest.approx(0.012)
    fake.prices["BTCUSDT"] = 50_000.0                  # 640: below 65% of the 1,000 high
    rep = cycle(book, flow, RUN + 6 * H)
    assert rep["flow02"] == {"day": flowbtc.day_iso(D), "status": "halted", "held": 0} and btc(fake) == 0.0
    assert flow_orders(fake)[-1][5]["reduceOnly"]
    st = store(tmp).pairs["book"]
    assert st["status"] == "halted" and st["halt_reason"] == "drawdown"
    fake.prices["BTCUSDT"] = 80_000.0
    rep = cycle(book, flow, RUN + DAY)                  # a long call the next day: nothing
    assert rep["flow02"]["status"] == "halted" and len(flow_orders(fake)) == 2
    assert flowbtc.status(store(tmp))["flow02_status"] == "halted"
    assert {e["event"] for e in events(tmp)} >= {"perp_flow02_halted"}


def test_the_high_follows_the_sleeve_up(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)
    fake.prices["BTCUSDT"] = 120_000.0                 # 1,000 - 960 + 1,440 = 480 gain: high 1,480
    cycle(book, flow, RUN + H)
    fake.prices["BTCUSDT"] = 82_000.0                  # 1,024: below 65% of 1,480 (962)? no, above
    assert cycle(book, flow, RUN + 2 * H)["flow02"]["status"] == "ok"
    fake.prices["BTCUSDT"] = 76_000.0                  # 952 < 962
    assert cycle(book, flow, RUN + 3 * H)["flow02"]["status"] == "halted"


def test_a_fill_the_run_did_not_record_is_settled_from_the_ledger(fworld, monkeypatch):
    fake, book, flow, sig, tmp = fworld
    real = fake.create_order

    def lost(*a, **kw):                                  # the order fills, the answer never comes back
        real(*a, **kw)
        raise ccxt.NetworkError("timeout")

    monkeypatch.setattr(fake, "create_order", lost)
    with pytest.raises(PerpBookError, match="outcome unknown"):
        cycle(book, flow, RUN)
    assert btc(fake) == pytest.approx(0.012) and store(tmp).pairs["book"]["leg"]["qty"] == 0.0
    monkeypatch.setattr(fake, "create_order", real)
    rep = cycle(book, flow, RUN + H)                     # reconcile, then FLOW02 settles its leg, then the check passes
    st = store(tmp).pairs["book"]
    assert st["leg"]["qty"] == pytest.approx(0.012) and st["leg"]["pending"] is None
    assert rep["flow02"]["held"] == 1 and len(flow_orders(fake)) == 1    # the day is not traded twice
    assert st["leg"]["fills"][0]["key"] == store(tmp).pairs[f"d{flowbtc.day_iso(D)}"]["key"]


def fail_btc_ticker(fake, nth: int):
    """The nth BTCUSDT ticker read from now on times out; returns the real reader."""

    orig, n = fake.fetch_ticker, {"i": 0}

    def flaky(sym):
        if sym.startswith("BTC/"):
            n["i"] += 1
            if n["i"] == nth:
                raise ConnectionError("ticker timeout")
        return orig(sym)

    fake.fetch_ticker = flaky
    return orig


def test_a_flat_call_sell_that_fails_before_it_is_sent_is_sent_at_the_next_run(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)                                # day 1: long, buys 0.012
    sig.call(D + DAY, long=False)                         # day 2: flat call
    orig = fail_btc_ticker(fake, 2)                       # 1st BTC ticker: the halt check; 2nd: the sell's mark
    with pytest.raises(Exception):
        cycle(book, flow, RUN + DAY)
    fake.fetch_ticker = orig
    assert btc(fake) == pytest.approx(0.012) and store(tmp).pairs["book"]["close_due"]["day"] == flowbtc.day_iso(D + DAY)
    rep = cycle(book, flow, RUN + DAY + H)                # 02:15: the day is decided; its sell is sent now
    assert rep["flow02"]["closed"] is True and btc(fake) == 0.0
    sell = flow_orders(fake)[-1]
    assert sell[3] == "sell" and sell[4] == pytest.approx(0.012) and sell[5]["reduceOnly"]
    st = store(tmp)
    rec = st.pairs[f"d{flowbtc.day_iso(D + DAY)}"]
    assert rec["action"] == "sell" and rec["key"] == sell[5]["clientOrderId"] and "close_due" not in st.pairs["book"]
    assert "closed" not in cycle(book, flow, RUN + DAY + 2 * H)["flow02"] and len(flow_orders(fake)) == 2


def test_a_missed_day_sell_that_fails_is_sent_at_the_next_run(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)                                # day 1: long, buys 0.012
    sig.call(D + DAY, missing=["metrics"])                # day 2: no call in its window
    cycle(book, flow, RUN + DAY)
    cycle(book, flow, RUN + DAY + H)
    orig = fail_btc_ticker(fake, 2)                       # 03:15: the missed day's sell cannot read its mark
    with pytest.raises(Exception):
        cycle(book, flow, RUN + DAY + 2 * H)
    fake.fetch_ticker = orig
    rep = cycle(book, flow, RUN + DAY + 3 * H)            # 04:15: the day is missed, and its sell goes out now
    assert rep["flow02"]["closed"] is True and btc(fake) == 0.0
    rec = store(tmp).pairs[f"d{flowbtc.day_iso(D + DAY)}"]
    assert rec["status"] == "missed" and rec["close_key"] == flow_orders(fake)[-1][5]["clientOrderId"]


def no_price(fake):
    """Demo answers a filled market order with average 0, and the read-back has none either."""

    orig = fake.fetch_order
    fake.ack_without_price = True
    fake.fetch_order = lambda oid, sym, params=None: {**orig(oid, sym, params), "average": None, "cost": 0}
    return orig


def test_a_fill_the_ledger_holds_without_a_price_is_priced_and_the_halt_still_works(fworld):
    fake, book, flow, sig, tmp = fworld
    orig = no_price(fake)
    ticks = itertools.count(0, 5)
    book.clock = lambda: next(ticks)
    with pytest.raises(PerpBookError, match="without a price"):
        cycle(book, flow, RUN)
    fake.ack_without_price = False
    fake.fetch_order = orig                               # the venue has the price by the next run
    cycle(book, flow, RUN + H)
    (fill,) = store(tmp).pairs["book"]["leg"]["fills"]
    assert fill["qty"] == pytest.approx(0.012) and fill["avg"] == 80_000.0 and fill["avg_from"] == "venue"
    assert store(tmp).pairs["book"]["high"] == pytest.approx(1000.0)
    fake.prices["BTCUSDT"] = 48_000.0                     # 1,000 - 960 + 576 = 616: below 650
    assert cycle(book, flow, RUN + 3 * H)["flow02"]["status"] == "halted" and btc(fake) == 0.0


def test_without_any_price_the_fill_takes_the_mark_at_send(fworld):
    fake, book, flow, sig, tmp = fworld
    no_price(fake)
    ticks = itertools.count(0, 5)
    book.clock = lambda: next(ticks)
    with pytest.raises(PerpBookError, match="without a price"):
        cycle(book, flow, RUN)
    cycle(book, flow, RUN + H)
    (fill,) = store(tmp).pairs["book"]["leg"]["fills"]
    assert fill["avg"] == fill["mark"] == 80_000.0 and fill["avg_from"] == "mark"


def test_a_new_size_buys_nothing_until_the_store_is_moved_aside(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, D + 5 * H)                          # the book starts at 1,000, flat
    big = flowbtc.FlowBtcBook(book, store(tmp), 3000.0, sig)
    plan = run_cycle(book, now_ms=RUN + DAY, mainnet_info={}, execute=False, others=[big])["flow02"]
    assert plan["size_changed"] is True
    rep = cycle(book, big, RUN + DAY)
    assert rep["flow02"]["action"] == "skipped" and rep["flow02"]["reason"] == "size_changed" and flow_orders(fake) == []
    # with the old size the book buys; a later flat call still sells under a new size
    old = flowbtc.FlowBtcBook(book, store(tmp), 1000.0, sig)
    assert cycle(book, old, RUN + 2 * DAY)["flow02"]["action"] == "buy"
    sig.call(D + 3 * DAY, long=False)
    small = flowbtc.FlowBtcBook(book, store(tmp), 500.0, sig)
    assert cycle(book, small, RUN + 3 * DAY)["flow02"]["action"] == "sell" and btc(fake) == 0.0


def test_a_partial_sell_leaves_no_step_the_book_cannot_sell(fworld, tmp_path):
    """0.013 - 0.010 is 0.0029999999999999996 in floats; ccxt would truncate that to 0.002 and leave a step."""

    fake, book, _, sig, tmp = fworld
    flow = flowbtc.FlowBtcBook(book, PairStore.load(tmp_path / "flow02b.json", KEY), 1050.0, sig)
    cycle(book, flow, RUN)
    assert btc(fake) == pytest.approx(0.013)              # 1,050 / 80,000 = 0.013125, down to the step
    sig.call(D + DAY, long=False)
    original = fake.create_order

    def partial(sym, typ, side, amount, price=None, params=None):    # the sell fills 0.010, then expires
        cid = (params or {}).get("clientOrderId", "")
        if cid.endswith("-c0"):
            fake.calls.append(("order", sym, typ, side, amount, dict(params)))
            fake.positions[sym] -= 0.010
            fake.orders[cid] = {"id": "x" + cid, "clientOrderId": cid, "symbol": sym, "type": "market", "side": side,
                                "amount": amount, "filled": 0.010, "average": 80_000.0, "cost": 800.0,
                                "status": "expired"}
            return dict(fake.orders[cid])
        return original(sym, typ, side, amount, price, params)

    fake.create_order = partial
    with pytest.raises(PerpBookError, match="expired"):
        cycle(book, flow, RUN + DAY)
    fake.create_order = original
    rep = cycle(book, flow, RUN + DAY + H)                # settles the 0.010, then sells what is left
    assert rep["flow02"]["closed"] is True and rep["flow02"]["held"] == 0
    assert btc(fake) == pytest.approx(0.0, abs=1e-12) and flow.store.pairs["book"]["leg"]["qty"] == 0.0
    assert [c[4] for c in flow_orders(fake)][-1] == 0.003
    assert book.orders.unfinished_orders() == {}
    cycle(book, flow, RUN + DAY + 2 * H)                  # nothing is left over to sell, and nothing stops
    assert len(flow_orders(fake)) == 3


def test_a_sell_the_venue_refuses_stops_for_the_operator_not_retried(fworld):
    """The behaviour FlowBtcBook._close's docstring states: a venue error is an unknown outcome, not a retry."""

    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)
    sig.call(D + DAY, long=False)
    original = fake.create_order

    def refuse(sym, typ, side, amount, price=None, params=None):
        if (params or {}).get("clientOrderId", "").startswith("fb2-") and side == "sell":
            raise ccxt.InvalidOrder("binanceusdm -2022 ReduceOnly Order is rejected")
        return original(sym, typ, side, amount, price, params)

    fake.create_order = refuse
    with pytest.raises(PerpBookError, match="outcome unknown"):
        cycle(book, flow, RUN + DAY)
    fake.create_order = original
    for at in (RUN + DAY + H, RUN + DAY + 2 * H):
        with pytest.raises(PerpBookError, match="operator review"):
            cycle(book, flow, at)
    assert btc(fake) == pytest.approx(0.012) and len(flow_orders(fake)) == 1


def test_a_halted_book_still_updates_its_data_store_once_a_day(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)
    fake.prices["BTCUSDT"] = 50_000.0
    assert cycle(book, flow, RUN + 6 * H)["flow02"]["status"] == "halted"
    n, orders = len(sig.updates), len(flow_orders(fake))
    sig.fail_update = True
    cycle(book, flow, RUN + DAY)                          # 01:15: the update fails, the 02:15 run tries again
    sig.fail_update = False
    cycle(book, flow, RUN + DAY + H)
    cycle(book, flow, RUN + DAY + 5 * H)                  # outside the window: no update
    for k in range(2, 5):
        cycle(book, flow, RUN + k * DAY)
        cycle(book, flow, RUN + k * DAY + H)              # once a day
    assert sig.updates[n:] == [RUN + DAY, RUN + DAY + H] + [RUN + k * DAY for k in range(2, 5)]
    assert len(flow_orders(fake)) == orders and sig.records.count(True) == 1     # never a call, never an order


def test_an_order_with_an_unknown_outcome_stops_for_the_operator(fworld):
    fake, book, flow, sig, tmp = fworld
    fake.fail_next = "fb2-" + flowbtc.day_iso(D)[2:].replace("-", "") + "-o0"
    with pytest.raises(PerpBookError):
        cycle(book, flow, RUN)
    assert btc(fake) == 0.0
    # sent, not at the venue: reconcile stops for the operator, as for every book
    with pytest.raises(PerpBookError, match="operator review"):
        cycle(book, flow, RUN + H)
    assert store(tmp).pairs["book"]["leg"]["pending"]["key"] == fake.fail_next


def test_an_order_reserved_but_never_sent_is_cleared(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, D + 5 * H)                       # the book exists, flat
    key = "fb2-" + flowbtc.day_iso(D)[2:].replace("-", "") + "-o0"
    book.orders.reserve_order(key, symbol="BTCUSDT", side="buy", quantity=0.012, venue_client_order_id=key)
    s = flow.store
    s.pairs["book"]["leg"]["pending"] = {"key": key, "side": "buy", "mark": 80_000.0}
    s.save()
    cycle(book, flow, D + 6 * H)                       # reconcile: rejected, never submitted; FLOW02 clears it
    leg = store(tmp).pairs["book"]["leg"]
    assert leg["pending"] is None and leg["qty"] == 0.0 and leg["fills"] == []
    assert events(tmp)[-1]["details"] == {"key": key, "status": "rejected"}


def test_records_hold_what_the_cost_and_futility_reads_need(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)
    s = store(tmp)
    (fill,) = s.pairs["book"]["leg"]["fills"]
    assert set(fill) == {"key", "side", "qty", "avg", "fees", "mark"}
    assert fill["side"] == "buy" and fill["qty"] == pytest.approx(0.012) and fill["mark"] == 80_000.0 and fill["avg"] == 80_000.0
    rec = s.pairs[f"d{flowbtc.day_iso(D)}"]
    assert {"pred", "threshold", "model", "target", "action", "key", "decided_at", "bar_ms", "nan"} <= set(rec)
    assert rec["bar_ms"] == D and rec["key"] == fill["key"]


def test_plan_and_step_and_audit_carry_no_amounts(fworld):
    fake, book, flow, sig, tmp = fworld
    plan = run_cycle(book, now_ms=RUN, mainnet_info={}, execute=False, others=[flow])["flow02"]
    assert set(plan) <= ALLOWED and plan["target"] == "long" and sig.records == [False]
    assert not (tmp / "flow02.json").exists() and flow_orders(fake) == [] and sig.updates == []
    sig.call(D + DAY, missing=["bars"])
    for at in (RUN, RUN + DAY, RUN + DAY + 2 * H):
        rep = cycle(book, flow, at)
        assert set(rep["flow02"]) <= ALLOWED
    for e in events(tmp):
        assert set(e["details"]) <= ALLOWED, e
    st = flowbtc.status(store(tmp))
    assert all(isinstance(v, (int, str, dict)) for v in st.values())


def test_a_failed_update_is_audited_and_the_call_uses_the_store(fworld):
    fake, book, flow, sig, tmp = fworld
    sig.fail_update = True
    rep = cycle(book, flow, RUN)
    assert rep["flow02"]["action"] == "buy"
    assert events(tmp)[0]["event"] == "perp_flow02_update_failed" and events(tmp)[0]["details"] == {"error": "OSError"}


def test_a_signal_fault_stays_in_the_book_and_the_day_is_missed(fworld):
    fake, book, flow, sig, tmp = fworld
    cycle(book, flow, RUN)
    sig.decide = lambda day_ms, *, record: (_ for _ in ()).throw(ValueError("bad parquet"))
    i = info(("NEWUSDT", RUN + DAY - 4 * H, None, "COIN"))
    rep = cycle(book, flow, RUN + DAY, mainnet_info=i)          # the pair book still enters after it
    assert rep["flow02"]["missing"] == ["error_ValueError"] and rep["entered"] == ["NEWUSDT"]
    rep = cycle(book, flow, RUN + DAY + 2 * H, mainnet_info=i)
    assert rep["flow02"]["missed"] == 1 and btc(fake) == pytest.approx(0.001)    # FLOW02 sold; the hedge stays
    assert store(tmp).pairs[f"d{flowbtc.day_iso(D + DAY)}"]["reason"] == "missing:error_ValueError"


def test_a_sleeve_above_its_cap_is_refused(fworld):
    fake, book, flow, sig, tmp = fworld
    flow.usd = flowbtc.USD_CAP + 1
    with pytest.raises(PerpBookError, match="outside"):
        cycle(book, flow, RUN)


def test_with_the_real_signal_on_a_synthetic_store(fworld, tmp_path):
    from tests.test_flowbtc import Market, Venue, client, ms
    from tools.flowbtc.signal import Signal

    fake, book, flow, _, tmp = fworld
    mk = Market(end="2021-01-05")
    mk.seed(tmp_path / "flowbtc", ms("2021-01-03"))
    v = Venue(mk)
    flow.signal = Signal(tmp_path / "flowbtc", client=lambda: client(v))
    day = ms("2021-01-04")
    v.now = day + H + 15 * 60_000
    rep = cycle(book, flow, v.now)
    assert rep["flow02"]["target"] in ("long", "flat") and rep["flow02"]["action"] in ("buy", "none")
    rec = store(tmp).pairs["d2021-01-04"]
    assert rec["model"] == "2021Q1" and (tmp_path / "flowbtc" / "models" / "2021Q1.json").exists()
    assert (btc(fake) > 0) == (rec["target"] == "long")
