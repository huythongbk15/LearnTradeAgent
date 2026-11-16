"""K-pause on the live root (PERP_LIVE_R0.md section 7): after any fired stop book KRW enters nothing until the
operator signs with --resume; a rehearsal's stop does not count, and Demo keeps K3 alone. On the fake venue, Demo and
mainnet; no network."""

from __future__ import annotations

from tests import test_perp_krwfade as krwfade_tests
from tests.test_perp_krwfade import DAY, E0, MIN, N0, Public, cycle, enter, event, krw, krw_events, note
from trading_agent.execution.perp import krwfade as kx

world = krwfade_tests.world        # the fake venue, Demo and mainnet (a fixture)
LATER = N0 + 5 * DAY               # a second notice, after the first event's stop
E1 = E0 + 5 * DAY


def stopped_then_new(world, *, pause: bool) -> Public:
    fake, book, tmp = world
    enter(world)
    fake.fire_stop("AAAUSDT", fill=3.0)             # at its 1.5x trigger: 12.5% of the sleeve, K3 stays off
    pub = Public(note(101, "AAA"), note(102, "BBB", at=LATER))
    cycle(book, krw(book, tmp, pub, stop_pause=pause), E0 + 3 * 3_600_000)     # the stop is seen, the BTC sold
    assert event(tmp)["status"] == "closed" and event(tmp)["close_reason"] == "stop"
    assert not krw(book, tmp, pub).counts()["krw_halted"]          # one stop at its trigger does not trip K3
    return pub


def test_a_fired_stop_holds_live_entries_until_the_operator_signs(world):
    fake, book, tmp = world
    pub = stopped_then_new(world, pause=True)
    assert krw(book, tmp, pub, stop_pause=True).counts()["krw_paused"] is True
    cycle(book, krw(book, tmp, pub, stop_pause=True), E1 + 2 * MIN)
    bbb = event(tmp, 102, "BBB")
    assert (bbb["status"], bbb["reason"]) == ("shadow_only", "k_pause")


def test_resume_signs_the_pause_and_the_next_event_enters(world):
    fake, book, tmp = world
    pub = stopped_then_new(world, pause=True)
    rep = cycle(book, krw(book, tmp, pub, stop_pause=True, resume=True), E1 - 30 * MIN)["krw"]
    assert rep["resume"] == "pause_signed"
    assert "perp_krw_pause_signed" in [e["event"] for e in krw_events(tmp)]
    kb = krw(book, tmp, pub, stop_pause=True)
    assert kb.counts()["krw_paused"] is False and kb.store.pairs[kx.STATE]["pause_signed_stops"] == 1
    cycle(book, kb, E1 + 2 * MIN)
    assert event(tmp, 102, "BBB")["status"] == "open"


def test_resume_with_nothing_fired_is_still_nothing_to_resume(world):
    fake, book, tmp = world
    enter(world)
    rep = cycle(book, krw(book, tmp, Public(note(101, "AAA")), stop_pause=True, resume=True), E0 + 5 * MIN)["krw"]
    assert rep["resume"] == "nothing_to_resume"


def test_demo_keeps_k3_alone_after_a_fired_stop(world):
    fake, book, tmp = world
    pub = stopped_then_new(world, pause=False)
    assert krw(book, tmp, pub).counts()["krw_paused"] is False
    cycle(book, krw(book, tmp, pub), E1 + 2 * MIN)
    assert event(tmp, 102, "BBB")["status"] == "open"


def test_a_rehearsals_stop_never_pauses(world):
    fake, book, tmp = world
    cycle(book, krw(book, tmp, Public(), stop_pause=True,
                    rehearse={"perp": "CCCUSDT", "stop_x": 1.01, "hold_ms": 30 * 60_000}), E0 + 7 * MIN)
    fake.fire_stop("CCCUSDT", fill=5.05)
    cycle(book, krw(book, tmp, Public(), stop_pause=True), E0 + 12 * MIN)
    counts = krw(book, tmp, Public(), stop_pause=True).counts()
    assert counts["krw_rehearsal_stops"] == 1 and counts["krw_stops_fired"] == 0 and counts["krw_paused"] is False


def test_the_live_runner_turns_the_pause_on():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "scripts" / "live_perp_r0.py").read_text()
    assert "stop_pause=True" in text
    demo = (Path(__file__).resolve().parents[1] / "scripts" / "live_perp_demo.py").read_text()
    assert "stop_pause" not in demo
