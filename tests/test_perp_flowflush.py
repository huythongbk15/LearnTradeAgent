"""Book FLOW01 (H-FLOW-BTC-01) on the fake Binance USD-M demo venue, with a fake public feed. No network."""

from __future__ import annotations

import itertools
import json

import ccxt
import pytest

import trading_agent.exchanges.ccxt_adapter as ca
from tests.test_perp_demo import KEY, T0, FakeBook, FakeUsdm, events, hold, info, orders_sent, pos
from tools.flowflush import feed as F
from tools.flowflush import poll
from tools.flowflush import rules as R
from trading_agent.exchanges.ccxt_adapter import CCXTAdapter
from trading_agent.execution.live_safety import LiveRiskStateStore
from trading_agent.execution.perp import flowflush as fx
from trading_agent.execution.perp import rules as prules
from trading_agent.execution.perp.book import KEY_MAX, Book, PairStore, PerpBookError, run_cycle
from trading_agent.execution.perp.venue import PerpDemoVenue, demo_config

MIN = 60_000
H = 60 * MIN
E = T0 // R.STAMP_MS * R.STAMP_MS + 10 * R.STAMP_MS     # a research stamp
CLUSTER = R.CLUSTER_MS


@pytest.fixture
def world(tmp_path, monkeypatch):
    """test_perp_demo's world: the fake demo venue (BTC at 80,000, equity 10,000) and the pair book."""

    fake = FakeUsdm({"BTCUSDT": 80_000.0, "NEWUSDT": 2.0, "OLDUSDT": 10.0})
    monkeypatch.setattr(ca.ccxt, "binanceusdm", lambda cfg: fake)
    venue = PerpDemoVenue(CCXTAdapter(demo_config("demo-key", "demo-secret")))
    venue.connect()
    orders = LiveRiskStateStore(tmp_path / "order_state.json", integrity_key=KEY)
    book = Book(venue, orders, PairStore.load(tmp_path / "pairs.json", KEY), tmp_path / "audit.jsonl", 100.0,
                sleep=lambda s: None)
    return fake, book, tmp_path


class FakeFeed:
    """Research stamps every 5 min; the endpoint publishes stamp t at t + ``publish`` (5 min, see feed.py)."""

    def __init__(self, events: set[int] = frozenset(), lo: int = E - 12 * H, hi: int = E + 24 * H, publish: int = 5 * MIN):
        self.events = set(events)
        self.lo, self.hi, self.publish = lo, hi, publish
        self.opens: dict[int, float] = {}
        self.looks: list[int] = []
        self.fail = False
        self.last: F.Look | None = None

    def look(self, now_ms: int) -> F.Look:
        self.looks.append(now_ms)
        if self.fail:
            raise RuntimeError("feed down")
        start = F.grid(now_ms - F.LOOKBACK_MS)
        stamps = tuple(F.Stamp(t, -0.03 if t in self.events else 0.0, -0.05 if t in self.events else 0.0, t in self.events)
                       for t in range(start + R.W_MS, self.hi, R.STAMP_MS) if self.lo <= t and t + self.publish <= now_ms)
        self.last = F.Look(now_ms, stamps, start + R.W_MS, now_ms // MIN * MIN)
        return self.last

    def open_at(self, t_ms: int) -> float | None:
        return self.opens.get(t_ms)


def flow(book, tmp, feed, usd: float = 1000.0) -> fx.FlowFlushBook:
    return fx.FlowFlushBook(book, PairStore.load(tmp / fx.STORE, KEY), usd, feed)


def cycle(book, fl, at, **kw):
    return run_cycle(book, now_ms=at, mainnet_info=kw.pop("mainnet_info", {}), execute=True, others=[fl], **kw)


def stored(tmp) -> PairStore:
    return PairStore.load(tmp / fx.STORE, KEY)


def flow_events(tmp) -> list[dict]:
    return [e for e in events(tmp) if e["event"].startswith("perp_flow01")]


def no_floats(x) -> bool:
    """Counts, statuses, quantities as ints and keys only: no float anywhere."""

    if isinstance(x, dict):
        return all(no_floats(v) for v in x.values())
    if isinstance(x, (list, tuple)):
        return all(no_floats(v) for v in x)
    return not isinstance(x, float)


def test_constants_follow_the_frozen_rule_and_keys_fit():
    assert (fx.CLUSTER_MS, fx.ENTRY_DELAY_MS, fx.HOLD_MS, fx.STAMP_MS) == (R.CLUSTER_MS, R.ENTRY_DELAY_MS, R.HOLD_MS, R.STAMP_MS)
    assert fx.ENTRY_WINDOW_MS == R.ENTRY_WINDOW_MS and fx.SYMBOL == R.SYMBOL == prules.HEDGE
    key = fx.key_stem(fx.event_id(E), "o") + "99"
    assert key.startswith("fb1-") and len(key) <= KEY_MAX and not key.endswith("-st")
    assert fx.key_stem(f"r{E // MIN:x}", "c").startswith("fb1-r")


def test_a_fresh_event_buys_the_sleeve_and_sells_its_own_quantity_after_four_hours(world):
    fake, book, tmp = world
    feed = FakeFeed({E})
    feed.opens = {E + 5 * MIN: 79_900.0, E + 5 * MIN + 4 * H: 81_000.0}
    fl = flow(book, tmp, feed)
    now = E + 5 * MIN + 20_000
    fl.wall = lambda: now / 1000                                 # the order goes out the moment the run starts
    rep = cycle(book, fl, now)["flow01"]
    assert rep == {"entered": 1, "closed": 0, "stale": 0, "skipped": 0, "gap_stamps": 0, "state": "open", "halted": False}
    assert pos(fake, "BTCUSDT") == pytest.approx(0.012)          # 1,000 / 80,000, down to the 0.001 step
    (buy,) = orders_sent(fake)
    key = f"fb1-{E // MIN:x}-o0"
    assert buy[1:5] == ("BTC/USDT:USDT", "market", "buy", pytest.approx(0.012)) and buy[5]["clientOrderId"] == key
    assert not buy[5].get("reduceOnly")
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert ev["status"] == "open" and ev["t"] == E and ev["ret60"] == -0.03 and ev["doi60"] == -0.05
    assert ev["delay_s"] == (now - E) / 1000 and ev["lag_s"] == 20.0
    assert ev["fills"] == [{"key": key, "side": "buy", "qty": 0.012, "avg": 80_000.0, "fees": {}, "mark": 80_000.0, "ms": now,
                            "sent_ms": now}]
    assert ev["ref_entry_open"] == 79_900.0 and ev["exit_due_ms"] == now + 4 * H
    assert fake.leverage["BTC/USDT:USDT"] == 1
    # inside the hold: the pair book counts the leg as explained, nothing is sent
    assert cycle(book, fl, now + 2 * H)["flow01"]["state"] == "open" and len(orders_sent(fake)) == 1
    assert cycle(book, fl, now + 4 * H - fx.EXIT_EARLY_MS - 1)["flow01"]["closed"] == 0
    rep = cycle(book, fl, now + 4 * H - fx.EXIT_EARLY_MS)["flow01"]
    assert rep["closed"] == 1 and rep["state"] == "idle" and pos(fake, "BTCUSDT") == pytest.approx(0)
    sell = orders_sent(fake)[-1]
    assert sell[3:5] == ("sell", pytest.approx(0.012)) and sell[5]["reduceOnly"] and sell[5]["clientOrderId"] == f"fb1-{E // MIN:x}-c0"
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert ev["status"] == "closed" and ev["exit_reason"] == "hold" and ev["ref_exit_open"] == 81_000.0
    assert [f["side"] for f in ev["fills"]] == ["buy", "sell"]
    assert book.orders.unfinished_orders() == {}
    found = [e for e in events(tmp) if e["event"].startswith("perp_flow01")]
    assert [e["event"] for e in found] == ["perp_flow01_entered", "perp_flow01_closed"]
    assert all(no_floats({k: v for k, v in e["details"].items() if k != "qty"}) for e in found)


def test_stale_clustered_and_before_start_events_are_recorded_not_traded(world):
    fake, book, tmp = world
    B = E - 100 * MIN
    S = B + CLUSTER            # at the edge of B's cluster: clustered
    S2 = S + R.STAMP_MS        # past it, seen 21 min late: stale
    F_ = S2 + CLUSTER + R.STAMP_MS
    feed = FakeFeed({B, S, S2, F_})
    fl = flow(book, tmp, feed)
    assert cycle(book, fl, B + 30 * MIN)["flow01"]["stale"] == 1     # the book's first run: before its start
    assert cycle(book, fl, S2 + 21 * MIN)["flow01"]["stale"] == 1
    assert not orders_sent(fake)
    assert cycle(book, fl, F_ + 6 * MIN)["flow01"]["entered"] == 1
    st = stored(tmp)
    got = {v["t"]: v["status"] for k, v in st.pairs.items() if k.startswith("e")}
    assert got == {B: "before_start", S2: "stale", F_: "open"}
    assert st.pairs[fx.STATE]["last_event_t"] == F_ and not any(k.startswith("g") for k in st.pairs)


def test_entry_waits_for_t_plus_five_minutes_and_ends_twenty_minutes_after_t(world):
    fake, book, tmp = world
    feed = FakeFeed({E, E + CLUSTER + R.STAMP_MS}, publish=MIN)      # a feed that shows a stamp early
    fl = flow(book, tmp, feed)
    rep = cycle(book, fl, E + 2 * MIN)["flow01"]
    assert rep["entered"] == 0 and fx.event_id(E) not in stored(tmp).pairs
    assert stored(tmp).pairs[fx.STATE]["cursor"] == E - R.STAMP_MS
    assert cycle(book, fl, E + 5 * MIN)["flow01"]["entered"] == 1
    cycle(book, fl, E + 5 * MIN + 4 * H)
    E2 = E + CLUSTER + R.STAMP_MS
    assert cycle(book, fl, E2 + 20 * MIN)["flow01"]["entered"] == 1      # now - t = 20 min is still fresh
    assert len([c for c in orders_sent(fake) if c[3] == "buy"]) == 2


def test_a_binding_cap_skips_the_event_and_anchors_the_cluster(world):
    fake, book, tmp = world
    feed = FakeFeed({E, E + 30 * MIN})
    fl = flow(book, tmp, feed)
    fake.fetch_balance = lambda: {"info": {"totalMarginBalance": "1000"}}     # 900 of room; 960 would not fit
    rep = cycle(book, fl, E + 6 * MIN)["flow01"]
    assert rep["skipped"] == 1 and not orders_sent(fake)
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert (ev["status"], ev["reason"]) == ("skipped", "equity")
    assert flow_events(tmp)[-1]["details"] == {"id": fx.event_id(E), "status": "skipped", "reason": "equity"}
    fake.fetch_balance = lambda: {"info": {"totalMarginBalance": "10000"}}
    assert cycle(book, fl, E + 36 * MIN)["flow01"]["entered"] == 0         # E + 30 min sits in E's cluster
    assert fx.event_id(E + 30 * MIN) not in stored(tmp).pairs


def test_the_entry_lock_still_exits_but_does_not_scan(world):
    fake, book, tmp = world
    feed = FakeFeed({E, E + CLUSTER + R.STAMP_MS})
    fl = flow(book, tmp, feed)
    cycle(book, fl, E + 6 * MIN)
    n = len(feed.looks)
    rep = cycle(book, fl, E + 6 * MIN + 4 * H, entry_lock="TRADING_ENTRY_KILL_SWITCH is active")["flow01"]
    assert rep["closed"] == 1 and pos(fake, "BTCUSDT") == pytest.approx(0) and len(feed.looks) == n
    E2 = E + CLUSTER + R.STAMP_MS
    cursor = stored(tmp).pairs[fx.STATE]["cursor"]
    rep = cycle(book, fl, E2 + 6 * MIN, entry_lock="lock")["flow01"]
    assert rep["entered"] == 0 and len(feed.looks) == n and stored(tmp).pairs[fx.STATE]["cursor"] == cursor


def test_btc_is_shared_with_the_pair_hedges_and_each_book_sells_its_own(world):
    fake, book, tmp = world
    feed = FakeFeed({E})
    fl = flow(book, tmp, feed)
    now = E + 6 * MIN
    rep = cycle(book, fl, now, rehearse="OLDUSDT", rehearse_hold_h=2)
    assert rep["entered"] == ["OLDUSDT"] and rep["flow01"]["entered"] == 1
    assert pos(fake, "BTCUSDT") == pytest.approx(0.013)       # 0.012 FLOW01 + 0.001 pair hedge
    cycle(book, fl, now + 2 * H)                                # the pair closes: its own 0.001 only
    assert pos(fake, "BTCUSDT") == pytest.approx(0.012)
    cycle(book, fl, now + 4 * H)                                # FLOW01 closes: its own 0.012 only
    assert pos(fake, "BTCUSDT") == pytest.approx(0)
    btc_sells = [(c[4], c[5]["reduceOnly"]) for c in orders_sent(fake) if c[1] == "BTC/USDT:USDT" and c[3] == "sell"]
    assert btc_sells == [(pytest.approx(0.001), True), (pytest.approx(0.012), True)]
    # a BTC quantity no book explains still stops the run
    hold(fake, "BTCUSDT", 0.002)
    with pytest.raises(PerpBookError, match="do not match"):
        cycle(book, fl, now + 5 * H)


def test_losses_past_ten_percent_of_the_sleeve_halt_the_book(world):
    fake, book, tmp = world
    E2 = E + CLUSTER + R.STAMP_MS
    E3 = E2 + CLUSTER + R.STAMP_MS
    feed = FakeFeed({E, E2, E3})
    fl = flow(book, tmp, feed)
    cycle(book, fl, E + 6 * MIN)
    fake.prices["BTCUSDT"] = 79_500.0           # 0.012 x 500 = 6 USDT lost, fees aside: not past 100
    assert cycle(book, fl, E + 6 * MIN + 4 * H)["flow01"]["halted"] is False
    fake.prices["BTCUSDT"] = 80_000.0
    cycle(book, fl, E2 + 6 * MIN)
    fake.prices["BTCUSDT"] = 72_000.0           # 96 more: past 100 with the fees
    rep = cycle(book, fl, E2 + 6 * MIN + 4 * H)["flow01"]
    assert rep["closed"] == 1 and rep["halted"] is True and rep["state"] == "idle"
    assert flow_events(tmp)[-1]["event"] == "perp_flow01_halted"
    assert flow_events(tmp)[-1]["details"] == {"reason": "realized_net_below_limit"}   # no value leaves the book
    n = len(orders_sent(fake))
    rep = cycle(book, fl, E3 + 6 * MIN)["flow01"]
    assert rep["entered"] == 0 and rep["halted"] is True and len(orders_sent(fake)) == n
    assert fx.event_id(E3) not in stored(tmp).pairs
    assert fx.needs_signed_run(E3 + 6 * MIN, PairStore.peek(tmp / fx.STORE), feed) is False
    assert no_floats(rep)


def lose_reply(fake, key_part: str):
    """The venue fills the order but the reply is lost."""

    real = fake.create_order

    def create(sym, typ, side, amount, price=None, params=None):
        out = real(sym, typ, side, amount, price, params)
        if key_part in (params or {}).get("clientOrderId", ""):
            raise ccxt.NetworkError("reply lost")
        return out

    fake.create_order = create
    return real


def test_an_entry_or_exit_cut_off_is_settled_from_the_ledger(world):
    fake, book, tmp = world
    feed = FakeFeed({E})
    fl = flow(book, tmp, feed)
    real = lose_reply(fake, "-o0")
    now = E + 6 * MIN
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, fl, now)
    assert stored(tmp).pairs[fx.event_id(E)]["status"] == "opening" and pos(fake, "BTCUSDT") == pytest.approx(0.012)
    fake.create_order = real
    fl = flow(book, tmp, feed)                  # a new run reads the store again
    rep = cycle(book, fl, now + 5 * MIN)["flow01"]
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert ev["status"] == "open" and ev["qty"] == pytest.approx(0.012) and ev["fills"][0]["from_ledger"]
    assert rep["state"] == "open" and flow_events(tmp)[-1]["details"]["status"] == "open"
    lose_reply(fake, "-c0")
    with pytest.raises(PerpBookError, match="unknown"):
        cycle(book, fl, now + 4 * H)
    assert stored(tmp).pairs[fx.event_id(E)]["status"] == "closing" and pos(fake, "BTCUSDT") == pytest.approx(0)
    fake.create_order = real
    fl = flow(book, tmp, feed)
    assert cycle(book, fl, now + 4 * H + 5 * MIN)["flow01"]["state"] == "idle"
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert ev["status"] == "closed" and ev["qty"] == 0.0 and [f["side"] for f in ev["fills"]] == ["buy", "sell"]


def test_an_entry_the_ledger_holds_without_a_price_is_priced_for_the_halt(world):
    fake, book, tmp = world
    feed = FakeFeed({E})
    fl = flow(book, tmp, feed)
    orig = fake.fetch_order
    fake.ack_without_price = True              # demo answers without a price, and the read-back has none either
    fake.fetch_order = lambda oid, sym, params=None: {**orig(oid, sym, params), "average": None, "cost": 0}
    ticks = itertools.count(0, 5)
    book.clock = lambda: next(ticks)
    now = E + 5 * MIN + 20_000
    with pytest.raises(PerpBookError, match="without a price"):
        cycle(book, fl, now)
    fake.ack_without_price = False
    cycle(book, flow(book, tmp, feed), now + MIN)
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert ev["status"] == "open" and ev["fills"][0]["avg"] == ev["entry_mark"] == 80_000.0
    assert ev["fills"][0]["avg_from"] == "mark"
    fake.fetch_order = orig                    # the venue has the price later: a read-back wins over the mark
    rec = book.orders.state.order_ledger[ev["entry_key"]]
    assert fl._ledger_fill(ev["entry_key"], "buy", rec, None)["avg_from"] == "venue"


def test_the_exit_is_due_at_the_run_four_hours_later_despite_its_start_up(world):
    """The 5-minute pre-check reads its clock before the signed start-up that set the entry time."""

    fake, book, tmp = world
    feed = FakeFeed({E})
    fl = flow(book, tmp, feed)
    phase = E + 9 * MIN                                   # the 5-minute task's run that first sees the stamp
    entry_at = phase + 15_000                             # its signed ``at``: after dotenv, connect and the stores
    assert cycle(book, fl, entry_at)["flow01"]["entered"] == 1
    peek = lambda: PairStore.peek(tmp / fx.STORE)        # noqa: E731
    assert fx.signed_run_reason(phase + 4 * H - 5 * MIN + 2_000, peek(), feed) is None    # 3 h 55: not yet
    assert fx.signed_run_reason(phase + 4 * H + 2_000, peek(), feed) == "exit_due"        # the run at 4 h
    assert cycle(book, flow(book, tmp, feed), phase + 4 * H + 15_000)["flow01"]["closed"] == 1
    # hourly runs alone: the run four hours later reaches FLOW01 10 s sooner in its start-up, and still exits
    E2 = E + CLUSTER + R.STAMP_MS
    feed.events.add(E2)
    hourly = E2 + 15 * MIN + 12_000
    assert cycle(book, flow(book, tmp, feed), hourly)["flow01"]["entered"] == 1
    assert cycle(book, flow(book, tmp, feed), hourly + 4 * H - 10_000)["flow01"]["closed"] == 1
    held = [v["exit_ms"] - v["entry_ms"] for k, v in stored(tmp).pairs.items() if k.startswith("e")]
    assert held == [4 * H, 4 * H - 10_000]


def test_each_fill_records_the_minute_its_order_went_out(world):
    """The run's ``at`` comes before the reconcile and the other books' steps; the send minute can be later."""

    fake, book, tmp = world
    feed = FakeFeed({E})
    at = E + 15 * MIN + 5_000                             # an hourly run's start
    sent = at + 70_000                                    # the other books stepped first: the order goes out a minute later
    feed.opens = {at // MIN * MIN: 79_000.0, sent // MIN * MIN: 79_500.0, (sent + 4 * H) // MIN * MIN: 81_500.0}
    fl = flow(book, tmp, feed)
    fl.wall = lambda: sent / 1000
    cycle(book, fl, at)
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert ev["fills"][0]["ms"] == at and ev["fills"][0]["sent_ms"] == sent
    assert ev["ref_send_minute_open"] == 79_500.0
    fl.wall = lambda: (sent + 4 * H) / 1000
    cycle(book, fl, at + 4 * H)
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert ev["fills"][1]["sent_ms"] == sent + 4 * H and ev["ref_exit_send_minute_open"] == 81_500.0


def test_a_partial_exit_leaves_no_step_the_book_cannot_sell(world):
    """0.013 - 0.010 is 0.0029999999999999996 in floats; ccxt would truncate that to 0.002 and leave a step."""

    fake, book, tmp = world
    feed = FakeFeed({E})
    fl = flow(book, tmp, feed, usd=1050.0)
    cycle(book, fl, E + 6 * MIN)
    assert pos(fake, "BTCUSDT") == pytest.approx(0.013)
    original = fake.create_order

    def partial(sym, typ, side, amount, price=None, params=None):    # the exit fills 0.010, then expires
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
        cycle(book, fl, E + 6 * MIN + 4 * H)
    fake.create_order = original
    rep = cycle(book, flow(book, tmp, feed, usd=1050.0), E + 6 * MIN + 4 * H + 5 * MIN)["flow01"]
    assert rep["closed"] == 1 and rep["state"] == "idle" and pos(fake, "BTCUSDT") == pytest.approx(0.0, abs=1e-12)
    assert orders_sent(fake)[-1][4] == pytest.approx(0.003) and book.orders.unfinished_orders() == {}
    ev = stored(tmp).pairs[fx.event_id(E)]
    assert ev["status"] == "closed" and ev["qty"] == 0.0 and [f["qty"] for f in ev["fills"]] == [0.013, 0.010, 0.003]


def test_a_new_size_skips_events_until_the_store_is_moved_aside(world):
    fake, book, tmp = world
    E2 = E + CLUSTER + R.STAMP_MS
    feed = FakeFeed({E2})
    cycle(book, flow(book, tmp, feed), E + 6 * MIN)          # the book starts at 1,000, no event yet
    big = flow(book, tmp, feed, usd=2000.0)
    assert run_cycle(book, now_ms=E2 + 6 * MIN, mainnet_info={}, execute=False, others=[big])["flow01"]["size_changed"]
    rep = cycle(book, big, E2 + 6 * MIN)["flow01"]
    assert rep["skipped"] == 1 and rep["entered"] == 0 and orders_sent(fake) == []
    ev = stored(tmp).pairs[fx.event_id(E2)]
    assert ev["status"] == "skipped" and ev["reason"] == "size_changed"
    assert stored(tmp).pairs[fx.STATE]["usd"] == 1000.0


def test_the_five_minute_poll_signs_only_when_needed(world, monkeypatch, capsys):
    fake, book, tmp = world
    feed = FakeFeed({E + 2 * H})
    peek = lambda: PairStore.peek(tmp / fx.STORE)   # noqa: E731  (no key)
    assert fx.signed_run_reason(E, peek(), feed) is None                          # quiet
    assert fx.signed_run_reason(E + 2 * H + 6 * MIN, peek(), feed) == "fresh_event"
    assert fx.signed_run_reason(E + 2 * H + 21 * MIN, peek(), feed) is None       # stale: the hourly run records it
    fl = flow(book, tmp, feed)
    cycle(book, fl, E + 2 * H + 6 * MIN)
    feed.fail = True                                                              # held: no public call needed
    assert fx.signed_run_reason(E + 3 * H, peek(), feed) is None
    assert fx.signed_run_reason(E + 2 * H + 6 * MIN + 4 * H, peek(), feed) == "exit_due"
    # the CLI: exit 0 to run, 1 to skip, 2 when the poll failed; one JSON line without a price
    monkeypatch.setattr(F, "Feed", lambda: feed)
    monkeypatch.setattr(poll.time, "time", lambda: (E + 6 * H + 7 * MIN) / 1000)
    assert poll.main(["--root", str(tmp)]) == 0
    assert json.loads(capsys.readouterr().out) == {"signed_run": True, "reason": "exit_due", "tries": 1}
    rep = cycle(book, fl, E + 6 * H + 7 * MIN)["flow01"]          # the exit is made; the failed look adds nothing
    assert rep["closed"] == 1 and rep["feed_failed"] == 1 and flow_events(tmp)[-1]["event"] == "perp_flow01_feed_failed"
    assert poll.main(["--root", str(tmp)]) == 2 and json.loads(capsys.readouterr().out)["error"] == "RuntimeError"
    feed.fail = False
    assert poll.main(["--root", str(tmp)]) == 1


def test_the_dry_run_reads_and_writes_nothing(world):
    fake, book, tmp = world
    feed = FakeFeed({E})
    fl = fx.FlowFlushBook(book, PairStore.peek(tmp / fx.STORE), 1000.0, feed)
    rep = run_cycle(book, now_ms=E + 6 * MIN, mainnet_info={}, execute=False, others=[fl])
    assert rep["flow01"] == {"state": "idle", "halted": False, "events": {}, "fresh_event": True}
    assert not (tmp / fx.STORE).exists() and not orders_sent(fake)
    feed.fail = True
    assert run_cycle(book, now_ms=E + 6 * MIN, mainnet_info={}, execute=False, others=[fl])["flow01"]["feed"] == "RuntimeError"


def test_a_scan_that_cannot_see_back_to_its_cursor_records_the_gap(world):
    fake, book, tmp = world
    feed = FakeFeed()
    fl = flow(book, tmp, feed)
    cycle(book, fl, E)
    cursor = stored(tmp).pairs[fx.STATE]["cursor"]
    assert cursor == E - R.STAMP_MS
    rep = cycle(book, fl, E + 6 * H)["flow01"]
    covered = F.grid(E + 6 * H - F.LOOKBACK_MS) + R.W_MS
    assert rep["gap_stamps"] == (covered - cursor) // R.STAMP_MS - 1 > 0
    (gap,) = [v for k, v in stored(tmp).pairs.items() if k.startswith("g")]
    assert gap == {"kind": "gap", "from": cursor + R.STAMP_MS, "to": covered - R.STAMP_MS, "stamps": rep["gap_stamps"]}
    assert flow_events(tmp)[-1]["event"] == "perp_flow01_gap"


def test_a_rehearsal_tests_the_mechanics_outside_the_record(world):
    fake, book, tmp = world
    feed = FakeFeed()
    fl = flow(book, tmp, feed)
    run_cycle(book, now_ms=E, mainnet_info={}, execute=True, others=[fl])
    assert fl.rehearse(E, hold_min=10)["qty"] == pytest.approx(0.012)
    assert fl.held() == {"BTCUSDT": pytest.approx(0.012)}
    with pytest.raises(PerpBookError, match="idle"):
        fl.rehearse(E + MIN)
    fake.prices["BTCUSDT"] = 60_000.0          # a large loss: a rehearsal never halts the book
    rep = cycle(book, fl, E + 10 * MIN)["flow01"]
    assert rep["closed"] == 1 and rep["halted"] is False and pos(fake, "BTCUSDT") == pytest.approx(0)
    assert fl.counts() == {"flow01_events": {}, "flow01_open_legs": 0, "flow01_halted": False, "flow01_gaps": 0}
    keys = [c[5]["clientOrderId"] for c in orders_sent(fake)]
    assert keys == [f"fb1-r{E // MIN:x}-o0", f"fb1-r{E // MIN:x}-c0"]


def test_flow01_beside_other_books_and_the_symbol_rules(world):
    fake, book, tmp = world
    feed = FakeFeed({E})
    fl = flow(book, tmp, feed)
    other = FakeBook("flow02", {"BTCUSDT": 0.002})
    hold(fake, "BTCUSDT", 0.002)
    run_cycle(book, now_ms=E + 6 * MIN, mainnet_info=info(), execute=True, others=[other, fl])
    assert pos(fake, "BTCUSDT") == pytest.approx(0.014) and book.held_elsewhere() == {"BTCUSDT": pytest.approx(0.014)}
    with pytest.raises(PerpBookError, match="unique"):
        run_cycle(book, now_ms=E + 7 * MIN, mainnet_info={}, execute=True, others=[fl, flow(book, tmp, feed)])


def test_the_poll_waits_for_the_current_stamp_row(world, monkeypatch, capsys):
    fake, book, tmp = world
    now = [E + 5 * MIN + 10_000]                         # 10 s after the endpoint's stamp E + 5 min
    feed = FakeFeed({E}, publish=5 * MIN + 15_000)       # published 15 s after it
    monkeypatch.setattr(F, "Feed", lambda: feed)
    monkeypatch.setattr(poll.time, "time", lambda: now[0] / 1000)
    monkeypatch.setattr(poll.time, "sleep", lambda s: now.__setitem__(0, now[0] + int(s * 1000)))
    assert not F.published_now(feed.look(now[0]))
    assert poll.main(["--root", str(tmp), "--wait-s", "60"]) == 0
    assert json.loads(capsys.readouterr().out) == {"signed_run": True, "reason": "fresh_event", "tries": 2}
    now[0] = E + 5 * MIN + 10_000
    assert poll.main(["--root", str(tmp)]) == 1                                # without a wait: the next poll
    assert json.loads(capsys.readouterr().out)["tries"] == 1
