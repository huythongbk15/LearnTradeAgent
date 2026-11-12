"""Book KRW's stop path, the parts that do not wait for the first Demo exit (PERP_LIVE_R0.md section 5 item 3): the
D12 trigger type set on every stop, and a fired stop read back from its algo order. On the fake venue, Demo and
mainnet; no network."""

from __future__ import annotations

import ccxt
import pytest

from tests import test_perp_krwfade as krwfade_tests
from tests.test_perp_krwfade import E0, H, MIN, Public, cycle, enter, event, krw, note
from trading_agent.execution.perp import krwfade as kx

world = krwfade_tests.world        # the fake venue, Demo and mainnet (a fixture)
FIRED = E0 + 3 * H + 17 * MIN


def stop_calls(fake) -> list[dict]:
    return [c[5] for c in fake.calls if c[0] == "order" and "stopLossPrice" in c[5]]


def test_every_stop_is_sent_to_trigger_on_the_last_price(world):
    fake, book, tmp = world
    enter(world)
    (params,) = stop_calls(fake)
    assert params["workingType"] == "CONTRACT_PRICE" and params["reduceOnly"] is True


def test_a_fired_stop_is_read_back_from_its_algo_order(world):
    fake, book, tmp = world
    enter(world)
    fake.fire_stop("AAAUSDT", fill=3.4, at=FIRED)
    rep = cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 5 * H)["krw"]
    assert rep["closed"] == 1
    ev = event(tmp)
    (row,) = [f for f in ev["fills"] if f.get("from_stop")]
    assert (row["avg"], row["avg_from"], row["fired_ms"]) == (pytest.approx(3.4), "algo", FIRED)
    assert row["algo_status"] == "FINISHED" and row["working_type"] == "CONTRACT_PRICE"
    assert row["actual_qty"] == pytest.approx(250.0) and row["order_id"] == "t2"
    # the records pass reads the fee of the market order Binance filled the stop with
    assert row["fees"] == {"USDT": pytest.approx(250.0 * 3.4 * 0.0005)} and row["fees_from"] == "trades"


def test_a_stop_the_venue_does_not_answer_falls_back_to_the_trigger_as_before(world):
    fake, book, tmp = world
    enter(world)
    fake.fire_stop("AAAUSDT")
    cycle(book, krw(book, tmp, Public(note(101, "AAA"))), E0 + 5 * H)
    (row,) = [f for f in event(tmp)["fills"] if f.get("from_stop")]
    assert (row["avg"], row["avg_from"]) == (pytest.approx(kx.STOP_FILL_X * 3.0), "stop_trigger")
    assert "fired_ms" not in row and "order_id" not in row


def test_a_fired_stops_funding_ends_when_it_fired(world):
    fake, book, tmp = world
    enter(world)
    fake.fire_stop("AAAUSDT", fill=3.4, at=FIRED)
    rows = []

    def funding(symbol, lo, hi):
        rows.append((symbol, lo, hi))
        return []

    cycle(book, krw(book, tmp, Public(note(101, "AAA")), funding_rows=funding), E0 + 5 * H)
    alt = event(tmp)["funding"]["alt"]
    assert (alt["to_ms"], alt["end_from"]) == (FIRED, "fired")


def test_a_stop_whose_send_raised_and_that_fired_is_found_by_its_algo_order(world):
    """The take-off after a failed entry: the venue took the stop and it fired before the positions were read."""

    fake, book, tmp = world
    original = fake.create_order

    def took_then_lost(sym, typ, side, amount, price=None, params=None):
        got = original(sym, typ, side, amount, price, params)
        if params and "stopLossPrice" in params:
            fake.fire_stop("AAAUSDT", fill=3.2, at=FIRED)     # it fires at once, and the reply is lost
            raise ccxt.NetworkError("timeout")
        return got

    fake.create_order = took_then_lost
    with pytest.raises(Exception):
        enter(world)
    ev = event(tmp)
    assert ev["status"] == "failed"
    (row,) = [f for f in ev["fills"] if f.get("from_stop")]
    assert (row["avg"], row["avg_from"]) == (pytest.approx(3.2), "algo")
