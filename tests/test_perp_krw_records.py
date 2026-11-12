"""Book KRW's records for perp tier R0 (docs/operations/PERP_LIVE_R0.md section 5 item 6): per order the mark at
send, the fill, the fee and its asset; per event the run's delay against E and funding at the real settlement
interval. On the fake venue, Demo and mainnet; no network."""

from __future__ import annotations

import pytest

from tests.test_perp_demo import KEY, orders_sent
from tests import test_perp_krwfade as krwfade_tests
from tests.test_perp_krwfade import (
    DAY,
    E0,
    H,
    MIN,
    Public,
    cycle,
    enter,
    event,
    krw,
    krw_events,
    note,
)
from trading_agent.execution.perp import krwfade as kx
from trading_agent.execution.perp.book import PairStore

world = krwfade_tests.world        # the fake venue, Demo and mainnet (a fixture)
BTC = kx.BTC
HOLD_END = E0 + 72 * H


class Funding:
    """Public mainnet funding: settlements every ``every`` hours at ``rate``, by symbol; counts the reads."""

    def __init__(self, rates: dict[str, float], every: dict[str, int] | None = None):
        self.rates = rates
        self.every = every or {}
        self.calls: list[tuple] = []
        self.fail: Exception | None = None

    def __call__(self, symbol: str, lo: int, hi: int):
        self.calls.append((symbol, lo, hi))
        if self.fail is not None:
            raise self.fail
        step = self.every.get(symbol, 8) * H
        t = (lo // step) * step
        out = []
        while t < hi:
            if t >= lo:
                out.append((t, self.rates[symbol]))
            t += step
        return out


def test_every_fill_keeps_its_mark_its_order_id_and_its_fee_read_from_its_trades(world):
    fake, book, tmp = world
    enter(world)
    ev = event(tmp)
    assert ev["entry_delay_ms"] == 1_000 - E0               # the test clock's send (wall 1.0 s) against E
    sell, buy = ev["fills"]
    assert (sell["mark"], buy["mark"]) == (2.0, 80_000.0) and sell["order_id"] and buy["order_id"]
    assert sell["fees"] == {"USDT": pytest.approx(250.0 * 2.0 * 0.0005)} and sell["fees_from"] == "trades"
    assert buy["fees"] == {"USDT": pytest.approx(0.006 * 80_000.0 * 0.0005)} and buy["fees_from"] == "trades"
    trades = [c for c in fake.calls if c[0] == "trades"]
    assert [c[2] for c in trades] == [sell["order_id"], buy["order_id"]]
    # the reads come after every order of the run: none stands between the alt sell and its stop
    order_at = [i for i, c in enumerate(fake.calls) if c[0] == "order"]
    trade_at = [i for i, c in enumerate(fake.calls) if c[0] == "trades"]
    assert max(order_at) < min(trade_at)


def test_a_fee_the_venue_does_not_answer_yet_is_read_by_a_later_run_then_given_up(world, monkeypatch):
    fake, book, tmp = world
    asked: list[int] = []

    def no_trade_yet(*a, **k):                               # the venue lists no trade for any order yet
        asked.append(1)
        return []

    monkeypatch.setattr(fake, "fetch_my_trades", no_trade_yet)
    _pub, _kb = enter(world)
    for f in event(tmp)["fills"]:
        assert f["fees"] == {} and f["fee_tries"] == 1
    for i in range(kx.FEE_TRIES - 1):                        # the next runs inside the hold ask again
        cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + (10 + 5 * i) * MIN)
    ev = event(tmp)
    assert all(f["fee_read"] == "failed" and f["fees"] == {} for f in ev["fills"])
    assert [e["event"] for e in krw_events(tmp)].count("perp_krw_fee_unread") == 2
    n = len(asked)
    assert n == 2 * kx.FEE_TRIES
    cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 2 * H)
    assert len(asked) == n                                   # given up: asked no more


def test_the_take_off_after_a_failed_entry_keeps_the_marks_at_send(world):
    fake, book, tmp = world
    fake.fail_next = "stop"
    with pytest.raises(Exception):
        enter(world)
    ev = event(tmp)
    assert ev["status"] == "failed"
    back = [f for f in ev["fills"] if f["side"] == "buy" and f["symbol"] == "AAAUSDT"]
    assert back and all(f["mark"] == 2.0 for f in back)


def test_a_closed_event_records_funding_settlement_by_settlement_on_each_leg(world):
    fake, book, tmp = world
    enter(world)
    funding = Funding({"AAAUSDT": 0.0005, BTC: 0.0001}, every={"AAAUSDT": 1})   # a capped alt: hourly
    kb = krw(book, tmp, Public(note(101, "AAA")), funding_rows=funding)
    cycle(book, kb, E0 + 30 * MIN)
    assert "funding" not in event(tmp) and funding.calls == []                 # open: nothing yet
    rep = cycle(book, krw(book, tmp, Public(note(101, "AAA")), funding_rows=funding), HOLD_END)["krw"]
    assert rep["closed"] == 1 and rep["funding_rows"] == 1
    fd = event(tmp)["funding"]
    assert fd["status"] == "done" and fd["source"] == "mainnet_rates"
    alt, btc = fd["alt"], fd["btc"]
    # the fake clock sends every order at wall 1.0 s: each leg's window is (1000, 1000], so no settlement
    assert (alt["from_ms"], alt["to_ms"], alt["end_from"]) == (1_000, 1_000, "sent")
    assert alt["n"] == 0 and btc["n"] == 0
    cycle(book, krw(book, tmp, Public(note(101, "AAA")), funding_rows=funding), HOLD_END + 5 * MIN)
    assert len(funding.calls) == 2                                              # done once, never again


def test_funding_sums_the_settlements_inside_each_legs_hold(world):
    """With real send times: an hourly alt counts 72 settlements, an 8-hourly BTC 9; the short alt receives."""

    fake, book, tmp = world
    pub = Public(note(101, "AAA"))
    clock = {"t": (E0 + 2 * MIN) / 1000}

    def at_clock(**kw) -> kx.KrwFadeBook:
        return kx.KrwFadeBook(book, PairStore.load(tmp / kx.STORE, KEY), 500.0, pub.notices, pub.symbols,
                              pub.shadow_row, wall=lambda: clock["t"], **kw)

    cycle(book, at_clock(), E0 + 2 * MIN)
    assert event(tmp)["entry_delay_ms"] == 2 * MIN
    clock["t"] = (HOLD_END + 1 * MIN) / 1000
    funding = Funding({"AAAUSDT": 0.0005, BTC: 0.0001}, every={"AAAUSDT": 1, BTC: 8})
    cycle(book, at_clock(funding_rows=funding), HOLD_END)
    fd = event(tmp)["funding"]
    assert (fd["alt"]["n"], fd["alt"]["gaps_h"]) == (72, [1.0])
    assert fd["alt"]["bps"] == pytest.approx(72 * 5.0)                         # received by the short
    assert (fd["btc"]["n"], fd["btc"]["gaps_h"]) == (9, [8.0])
    assert fd["btc"]["bps"] == pytest.approx(-9 * 1.0)                          # paid by the long
    assert fd["alt"]["from_ms"] == E0 + 2 * MIN and fd["alt"]["to_ms"] == HOLD_END + MIN


def test_a_fired_stops_funding_ends_when_a_run_saw_it(world):
    fake, book, tmp = world
    enter(world)
    fake.fire_stop("AAAUSDT")
    funding = Funding({"AAAUSDT": 0.0001, BTC: 0.0001})
    cycle(book, krw(book, tmp, Public(note(101, "AAA")), funding_rows=funding), E0 + 5 * H)
    fd = event(tmp)["funding"]
    assert fd["status"] == "done" and fd["alt"]["end_from"] == "detected"


def test_a_failed_funding_read_is_tried_again_and_given_up_after_7_days(world):
    fake, book, tmp = world
    enter(world)
    funding = Funding({"AAAUSDT": 0.0001, BTC: 0.0001})
    funding.fail = OSError("down")
    cycle(book, krw(book, tmp, Public(note(101, "AAA")), funding_rows=funding), HOLD_END)
    fd = event(tmp)["funding"]
    assert (fd["status"], fd["tries"], fd["first_fail_ms"]) == ("due", 1, HOLD_END)
    cycle(book, krw(book, tmp, Public(note(101, "AAA")), funding_rows=funding), HOLD_END + 7 * DAY)
    assert event(tmp)["funding"]["status"] == "failed"
    assert "perp_krw_funding_failed" in [e["event"] for e in krw_events(tmp)]


def test_without_a_funding_read_nothing_is_recorded(world):
    fake, book, tmp = world
    enter(world)
    cycle(book, krw(book, tmp, Public(note(101, "AAA"))), HOLD_END)
    assert event(tmp)["status"] == "closed" and "funding" not in event(tmp)
    assert len(orders_sent(fake)) == 5
