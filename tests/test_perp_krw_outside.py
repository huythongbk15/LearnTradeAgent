"""Book KRW's close it did not order (PERP_LIVE_R0.md section 5 items 3 and 4): a liquidation, an ADL or a hand close
kept as the event's own state ``outside``, never booked as a fired stop, and the operator's settle command that
records what closed it from the venue's trades. On the fake venue, Demo and mainnet; no network."""

from __future__ import annotations

import json

import pytest

from tests import test_perp_krwfade as krwfade_tests
from tests.test_perp_demo import KEY, orders_sent, pos
from tests.test_perp_krwfade import DAY, E0, H, MIN, Public, cycle, enter, event, krw, krw_events, note
from trading_agent.execution.perp import alerts
from trading_agent.execution.perp import krwfade as kx
from trading_agent.execution.perp.book import PerpBookError

world = krwfade_tests.world        # the fake venue, Demo and mainnet (a fixture)
SEEN = E0 + 5 * H                  # the run that finds the close
CLOSED = E0 + 4 * H                # when the close happened at the venue


def outside(world, **close) -> dict:
    """Enter, close the alt at the venue outside the book, and let a signed run find it."""

    fake, book, tmp = world
    enter(world)
    fake.close_outside("AAAUSDT", at=CLOSED, **close)
    cycle(book, krw(book, tmp, Public(note(101, "AAA"))), SEEN)
    return event(tmp)


def settle(world, at: int = SEEN + 10 * MIN, *, apply: bool) -> dict:
    fake, book, tmp = world
    return krw(book, tmp, Public()).settle_outside(kx.event_id(101, "AAA"), at, apply=apply)


@pytest.mark.parametrize("stop", ["kept", "cancelled", "unknown"])
def test_a_close_the_book_did_not_order_is_outside_never_a_fired_stop(world, stop):
    fake, book, tmp = world
    enter(world)
    n = len(orders_sent(fake))
    fake.close_outside("AAAUSDT", at=CLOSED, stop=stop)     # a hand close; the venue's liquidation cancels the stop
    rep = cycle(book, krw(book, tmp, Public(note(101, "AAA"))), SEEN)      # no review stop: the run goes on
    ev = event(tmp)
    assert ev["status"] == "outside" and orders_sent(fake)[n:] == []        # nothing sent, the BTC leg kept
    assert ev["outside"]["was"] == "open" and ev["outside"]["venue_qty"] == 0.0
    assert ev["outside"]["record_qty"] == -250.0 and ev["outside"]["stop_listed"] is (stop == "kept")
    assert ev["outside"]["algo_status"] == {"kept": "NEW", "cancelled": "CANCELED", "unknown": "not_found"}[stop]
    assert not [f for f in ev["fills"] if f.get("from_stop")] and pos(fake, "BTCUSDT") > 0
    counts = krw(book, tmp, Public()).counts()
    assert (counts["krw_outside"], counts["krw_stops_fired"], counts["krw_open"]) == (1, 0, 1)
    assert rep["krw"]["closed"] == 0
    assert [e["event"] for e in krw_events(tmp)][-1] == "perp_krw_outside_close"
    held = krw(book, tmp, Public()).held()
    assert "AAAUSDT" not in held and held[kx.BTC] > 0                          # the venue's alt, the record's BTC


def test_an_outside_event_holds_entries_and_is_never_exited_by_the_book(world):
    fake, book, tmp = world
    outside(world)
    n = len(orders_sent(fake))
    pub = Public(note(101, "AAA"), note(102, "BBB", at=E0 + 3 * DAY - 10 * MIN))
    cycle(book, krw(book, tmp, pub), E0 + 3 * DAY + 2 * MIN)       # BBB's window and AAA's hold end
    assert event(tmp)["status"] == "outside" and orders_sent(fake)[n:] == []
    bbb = event(tmp, 102, "BBB")
    assert (bbb["status"], bbb["reason"]) == ("shadow_only", "outside_close")


def test_an_outside_event_follows_the_venue_down_never_up(world):
    fake, book, tmp = world
    ev = outside(world, qty=100.0)                  # a part closed: 150 left, the stop still listed
    assert ev["status"] == "outside" and ev["outside"]["venue_qty"] == -150.0
    assert krw(book, tmp, Public()).held()["AAAUSDT"] == -150.0
    fake.close_outside("AAAUSDT", qty=50.0, at=CLOSED + H)          # the operator closes more in the app
    cycle(book, krw(book, tmp, Public(note(101, "AAA"))), SEEN + H)
    assert event(tmp)["outside"]["venue_qty"] == -100.0
    fake.positions["AAA/USDT:USDT"] = -120.0        # more short than seen after the close: a foreign order
    with pytest.raises(PerpBookError, match="seen after its outside close"):
        cycle(book, krw(book, tmp, Public(note(101, "AAA"))), SEEN + 2 * H)


def test_the_settle_reports_what_closed_it_and_writes_nothing(world):
    fake, book, tmp = world
    outside(world)
    before = (tmp / kx.STORE).read_text()
    rep = settle(world, apply=False)
    assert rep["refusals"] == [] and not rep["applied"]
    assert (rep["then"], rep["close_reason"], rep["stop_listed"], rep["algo_status"]) == ("closing", "outside", True,
                                                                                          "NEW")
    assert rep["alt"] == {"record": -250.0, "venue": 0.0, "orders": 1, "bought": 250.0, "stop_order": False}
    assert rep["btc"] == {"record": pytest.approx(0.006), "orders": 0, "sold": 0.0}
    assert (tmp / kx.STORE).read_text() == before


def test_a_settled_close_is_recorded_from_the_trades_then_the_book_sells_its_btc_and_closes_it(world):
    fake, book, tmp = world
    ev = outside(world, price=3.6)
    rep = settle(world, apply=True)
    assert rep["applied"] and rep["refusals"] == []
    ev = event(tmp)
    assert (ev["status"], ev["close_reason"]) == ("closing", "outside") and kx.alt_short(ev) == 0.0
    (row,) = [f for f in ev["fills"] if f.get("from_outside")]
    assert (row["side"], row["qty"], row["avg"], row["symbol"], row["ms"]) == ("buy", 250.0, pytest.approx(3.6),
                                                                              "AAAUSDT", CLOSED)
    assert row["fees"] == {"USDT": pytest.approx(250.0 * 3.6 * 0.0005)} and row["fees_from"] == "trades"
    assert row["key"].startswith("krw-101-AAA-out-") and ev["outside"]["alt_orders"] == 1
    assert kx.signed_run_reason(SEEN + 15 * MIN, kx.PairStore.peek(tmp / kx.STORE), Public().notices,
                                Public().symbols) == "settle"
    n = len(orders_sent(fake))
    out = cycle(book, krw(book, tmp, Public(note(101, "AAA"))), SEEN + 15 * MIN)["krw"]
    (sold,) = orders_sent(fake)[n:]
    assert sold[1] == "BTC/USDT:USDT" and sold[3] == "sell" and sold[5]["reduceOnly"]
    assert out["closed"] == 1 and fake.algo == {} and pos(fake, "BTCUSDT") == pytest.approx(0)
    ev = event(tmp)
    assert (ev["status"], ev["close_reason"]) == ("closed", "outside") and not kx.has_legs(ev)
    assert book.orders.state.order_ledger["krw-101-AAA-st"]["status"] == "cancelled"
    counts = krw(book, tmp, Public()).counts()
    assert (counts["krw_outside"], counts["krw_outside_closes"], counts["krw_stops_fired"]) == (0, 1, 0)
    assert "perp_krw_outside_settled" in [e["event"] for e in krw_events(tmp)]


def test_a_part_settled_goes_back_to_open_and_the_book_exits_the_rest_at_its_hold(world):
    fake, book, tmp = world
    outside(world, qty=100.0)
    rep = settle(world, apply=True)
    assert (rep["then"], rep["close_reason"]) == ("open", None)
    ev = event(tmp)
    assert ev["status"] == "open" and kx.alt_short(ev) == pytest.approx(150.0)
    n = len(orders_sent(fake))
    cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 3 * DAY)
    alt_buy = [c for c in orders_sent(fake)[n:] if c[1] == "AAA/USDT:USDT"]
    assert [(c[3], c[4]) for c in alt_buy] == [("buy", 150.0)]
    assert (event(tmp)["status"], event(tmp)["close_reason"]) == ("closed", "hold")


def test_a_stop_order_among_the_closes_is_the_stops_fill(world):
    """A part closed by hand, then the stop fired for the rest: its order is the stop's fill, the rest outside."""

    fake, book, tmp = world
    outside(world, qty=100.0)
    fake.fire_stop("AAAUSDT", fill=4.6, at=CLOSED + H)
    cycle(book, krw(book, tmp, Public(note(101, "AAA"))), SEEN + H)
    assert event(tmp)["outside"]["venue_qty"] == 0.0
    rep = settle(world, SEEN + 2 * H, apply=True)
    assert rep["applied"] and rep["alt"]["stop_order"] and rep["close_reason"] == "outside"
    ev = event(tmp)
    (stop,) = [f for f in ev["fills"] if f.get("from_stop")]
    assert (stop["qty"], stop["avg"], stop["avg_from"], stop["fired_ms"]) == (150.0, pytest.approx(4.6), "trades",
                                                                              CLOSED + H)
    assert book.orders.state.order_ledger["krw-101-AAA-st"]["status"] == "filled"


def test_the_settle_refuses_what_the_trades_do_not_explain(world):
    fake, book, tmp = world
    outside(world)
    real = fake.trades
    fake.trades = [t for t in real if not t["order"].startswith("h")]       # the close's trade not found
    rep = settle(world, apply=True)
    assert not rep["applied"] and any("the record less the closes found" in r for r in rep["refusals"])
    fake.trades = real + [{"order": "z1", "symbol": "AAA/USDT:USDT", "side": "sell", "amount": 10.0, "price": 3.0,
                           "timestamp": CLOSED, "fee": {"cost": 0.01, "currency": "USDT"}}]
    rep = settle(world, apply=True)
    assert any("sold AAAUSDT" in r for r in rep["refusals"])
    fake.trades = real + [{"order": "z2", "symbol": "BTC/USDT:USDT", "side": "sell", "amount": 0.5,
                           "price": 80_000.0, "timestamp": CLOSED, "fee": {"cost": 1.0, "currency": "USDT"}}]
    assert any("sold by orders the ledger does not know" in r for r in settle(world, apply=True)["refusals"])
    assert event(tmp)["status"] == "outside"
    fake.trades = real
    other = krw(book, tmp, Public()).settle_outside("e999-ZZZ", SEEN, apply=True)
    assert other["refusals"] == ["no such event in book KRW's store"]


def test_the_settle_refuses_an_event_that_is_not_outside(world):
    fake, book, tmp = world
    enter(world)
    rep = settle(world, apply=True)
    assert rep["status"] == "open" and "not outside" in rep["refusals"][0] and not rep["applied"]


def test_the_settle_command_needs_the_operators_statement_to_apply(world):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("perp_krw_settle_test",
                                                  Path(__file__).resolve().parents[1] / "scripts" / "perp_krw_settle.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake, book, tmp = world
    outside(world)
    eid = kx.event_id(101, "AAA")
    rep = mod.settle(tmp, book.venue, KEY, eid, apply=True, operator_checked=False, now_ms=SEEN + 10 * MIN)
    assert not rep["applied"] and any("--operator-checked-app" in r for r in rep["refusals"])
    assert event(tmp)["status"] == "outside"
    n = len(orders_sent(fake))
    rep = mod.settle(tmp, book.venue, KEY, eid, apply=True, operator_checked=True, now_ms=SEEN + 10 * MIN)
    assert rep["applied"] and event(tmp)["status"] == "closing" and orders_sent(fake)[n:] == []   # reads only
    assert "avg" not in json.dumps(rep) and "price" not in json.dumps(rep)      # no price in the report
    assert mod.main(["--venue", "demo", "--event", eid, "--root", str(tmp / "missing")]) == 1


def test_the_alert_pages_an_outside_close_and_its_settle():
    msgs, ping, st = alerts.decide({}, rc=0, outcome={}, status={"krw_outside": 1}, unsettled=0, now_ms=E0)
    assert ping and any("did not order" in m and "settle" in m for m in msgs)
    again, _ping, st = alerts.decide(st, rc=0, outcome={}, status={"krw_outside": 1}, unsettled=0, now_ms=E0 + H)
    assert not [m for m in again if "did not order" in m]                    # once per new outside close
    done, _ping, st = alerts.decide(st, rc=0, outcome={}, status={"krw_outside": 0}, unsettled=0, now_ms=E0 + 2 * H)
    assert any("outside close is settled" in m for m in done)
