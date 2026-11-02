"""The reasons of a Monday, one at a time and all together, the books, the cohort and the phase rule. No market data: the reasons read facts, and the run tests build those facts from a store."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import numpy as np
import pytest

from tools.selection import config, decide
from tools.selection.decide import Event, Facts, Health

CLEAN = Health(19, 0, 0, 0)
SLOTS = ("B_ma1d_LS", "dhi30_3d", "cash")
NAMES = tuple(f"C{i:02d}" for i in range(18))
GOOD = Facts(CLEAN, NAMES, 0.0, False, 0, SLOTS, False, False)
AS_OF = "2026-11-02"


def closes(T: int = 400, coins=("BTC", "AAA")) -> np.ndarray:
    return np.ones((T, len(coins)))


def health(c: np.ndarray, names=("AAA",), coins=("BTC", "AAA")) -> Health:
    return decide.data_health(c, list(coins), list(names))


def test_clean_bars_have_no_mark() -> None:
    h = health(closes())
    assert h == Health(2, 0, 0, 0) and not h.bad


@pytest.mark.parametrize("hours,stale", [(6, 0), (7, 1), (30, 1)])
def test_a_last_bar_older_than_six_hours_is_stale(hours: int, stale: int) -> None:
    c = closes()
    c[-hours:, 1] = np.nan
    assert health(c).stale == stale and health(c).gaps == 0


def test_btc_is_checked_like_a_listed_name() -> None:
    c = closes()
    c[-7:, 0] = np.nan
    assert health(c).stale == 1 and health(c).bad


def test_only_btc_and_the_list_are_checked() -> None:
    c = closes(coins=("BTC", "AAA", "OFF"))
    c[:, 2] = np.nan
    assert not health(c, coins=("BTC", "AAA", "OFF")).bad


@pytest.mark.parametrize("hole,gaps", [(6, 0), (7, 1), (40, 1)])
def test_a_hole_longer_than_six_hours_inside_the_last_week_is_a_gap(hole: int, gaps: int) -> None:
    c = closes()
    c[-50:-50 + hole, 1] = np.nan
    assert health(c).gaps == gaps and health(c).stale == 0


def test_a_hole_that_ended_before_the_week_is_old_news() -> None:
    c = closes()
    c[-200:-168, 1] = np.nan            # the last missing hour is the one before the week starts
    assert health(c).gaps == 0
    c[-200:-167, 1] = np.nan            # now it is the first hour of the week
    assert health(c).gaps == 1


def test_a_coin_with_no_bars_or_no_column_is_missing() -> None:
    c = closes()
    c[:, 1] = np.nan
    assert health(c) == Health(2, 1, 0, 0) and health(c).bad
    assert health(closes(), names=("AAA", "NEWC")) == Health(3, 1, 0, 0)


def test_a_clean_monday_has_one_reason_and_it_is_the_ruling() -> None:
    assert decide.reasons_for(GOOD) == ["NO_KEPT_SELECTOR"]


@pytest.mark.parametrize("change,reason", [
    (dict(health=Health(19, 0, 1, 0)), "DATA_STALE_OR_GAP"),
    (dict(health=Health(19, 0, 0, 1)), "DATA_STALE_OR_GAP"),
    (dict(health=Health(19, 1, 0, 0)), "DATA_STALE_OR_GAP"),
    (dict(health=None), "DATA_STALE_OR_GAP"),
    (dict(list_names=NAMES[:17]), "LIST_INCOMPLETE"),
    (dict(list_names=()), "LIST_INCOMPLETE"),
    (dict(daily_fail_share=.0501), "LIST_INCOMPLETE"),
    (dict(late=True), "LATE_RUN"),
    (dict(seal_refusals=1), "SEAL_SCOPE"),
    (dict(slots=("cash", "cash", "cash")), "ALL_SLOTS_NON_POSITIVE"),
    (dict(policy_expired=True), "POLICY_EXPIRED"),
    (dict(tripwire_fired=True), "BOOK_TRIPWIRE"),
])
def test_each_reason_matches_alone(change: dict, reason: str) -> None:
    assert decide.reasons_for(replace(GOOD, **change)) == ["NO_KEPT_SELECTOR", reason]


def test_every_reason_together_keeps_the_order_of_the_page() -> None:
    f = Facts(Health(19, 0, 1, 0), (), .5, True, 2, ("cash",) * 3, True, True)
    assert decide.reasons_for(f) == list(config.REASONS)


def test_five_percent_of_pairs_failing_is_still_a_list() -> None:
    assert decide.reasons_for(replace(GOOD, daily_fail_share=.05)) == ["NO_KEPT_SELECTOR"]


def test_a_store_a_masked_coin_blocks_is_a_seal_matter_not_a_data_one() -> None:
    assert decide.reasons_for(replace(GOOD, health=None, seal_refusals=1)) == ["NO_KEPT_SELECTOR", "SEAL_SCOPE"]


def test_one_slot_of_cash_is_not_all_slots() -> None:
    assert "ALL_SLOTS_NON_POSITIVE" not in decide.reasons_for(replace(GOOD, slots=("cash", "cash", "B_ma1d_LS")))
    assert "ALL_SLOTS_NON_POSITIVE" not in decide.reasons_for(replace(GOOD, slots=None))


@pytest.mark.parametrize("reason", sorted(config.HOLD_REASONS))
def test_a_blocking_reason_holds_the_book(reason: str) -> None:
    assert not decide.selects(["NO_KEPT_SELECTOR", reason], SLOTS)


@pytest.mark.parametrize("reason", ["NO_KEPT_SELECTOR", "LATE_RUN", "ALL_SLOTS_NON_POSITIVE", "POLICY_EXPIRED", "BOOK_TRIPWIRE"])
def test_other_reasons_let_the_book_select(reason: str) -> None:
    assert decide.selects(["NO_KEPT_SELECTOR", reason], SLOTS)
    assert decide.selects([reason], ("cash",) * 3)


def test_a_book_cannot_select_what_was_not_computed() -> None:
    assert not decide.selects(["NO_KEPT_SELECTOR"], None)


def decision(as_of: str, selected: bool, slots=SLOTS, best=SLOTS) -> dict:
    phase = config.phase_of(as_of)
    books = {str(p): None for p in range(4)}
    books[str(phase)] = {"since": as_of, "slots": list(slots), "best_so_far": list(best)}
    return {"as_of": as_of, "selected": selected, "phase": phase, "books": books}


def test_events_come_from_selections_only_and_read_the_due_book() -> None:
    hist = [decision("2026-11-02", True), decision("2026-11-09", False), decision("2026-11-16", True, ("cash",) * 3, ("alt_btc", "cash", "cash"))]
    got = decide.events_of(hist)
    assert got == [Event("2026-11-02", 1, SLOTS, SLOTS), Event("2026-11-16", 3, ("cash",) * 3, ("alt_btc", "cash", "cash"))]


def test_books_hold_their_last_selection_and_none_before_the_first() -> None:
    a, b, c = (Event(d, config.phase_of(d), (d,), ("x",)) for d in ("2026-11-02", "2026-11-09", "2026-11-30"))
    assert a.phase == c.phase == 1 and b.phase == 2
    assert decide.books_after([a, b, c]) == {
        "0": None,
        "1": {"since": "2026-11-30", "slots": ["2026-11-30"], "best_so_far": ["x"]},
        "2": {"since": "2026-11-09", "slots": ["2026-11-09"], "best_so_far": ["x"]},
        "3": None,
    }


def test_there_is_no_policy_to_expire_before_the_first_run() -> None:
    assert not decide.policy_expired([], AS_OF)


def test_last_weeks_valid_run_keeps_the_policy_alive() -> None:
    assert not decide.policy_expired([decision("2026-10-26", True)], AS_OF)


def test_a_week_with_no_valid_run_expires_it() -> None:
    assert decide.policy_expired([decision("2026-10-19", True)], AS_OF)
    assert decide.policy_expired([decision("2026-10-19", True), decision("2026-10-26", False)], AS_OF)
    assert decide.policy_expired([decision("2026-10-26", False)], AS_OF)


def test_the_held_week_itself_is_not_expired() -> None:
    assert not decide.policy_expired([decision("2026-10-26", True)], "2026-11-02")


def test_validity_is_eight_days() -> None:
    assert decide.stamp_of("2026-10-26") + timedelta(days=8) >= decide.stamp_of(AS_OF)
    assert config.POLICY_VALIDITY_DAYS == 8


@pytest.mark.parametrize("delta,late", [(timedelta(minutes=7), False), (timedelta(hours=6), False), (timedelta(hours=6, seconds=1), True), (timedelta(days=3), True)])
def test_a_run_more_than_six_hours_after_the_stamp_is_late(delta: timedelta, late: bool) -> None:
    assert decide.is_late(AS_OF, datetime(2026, 11, 2, tzinfo=UTC) + delta) is late


def test_the_cohort_runs_60_weeks_from_the_first_good_monday(tmp_path) -> None:
    c = decide.start_cohort(tmp_path, AS_OF, ["NO_KEPT_SELECTOR"], True)
    assert c == {"start": "2026-11-02", "read_date": "2027-12-27", "weeks": 60}
    assert decide.load_cohort(tmp_path) == c


def test_a_cohort_is_written_whole_or_not_at_all(tmp_path, monkeypatch) -> None:
    def dies(a, b):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", dies)
    with pytest.raises(OSError):
        decide.start_cohort(tmp_path, AS_OF, ["NO_KEPT_SELECTOR"], True)
    assert not decide.cohort_path(tmp_path).exists()


def test_a_cohort_file_that_cannot_be_read_is_not_rewritten(tmp_path) -> None:
    decide.cohort_path(tmp_path).write_text('{"read_da', encoding="utf8")
    with pytest.raises(ValueError):
        decide.start_cohort(tmp_path, AS_OF, ["NO_KEPT_SELECTOR"], True)
    assert decide.cohort_path(tmp_path).read_text(encoding="utf8") == '{"read_da'


def test_a_cohort_is_never_rewritten(tmp_path) -> None:
    first = decide.start_cohort(tmp_path, AS_OF, ["NO_KEPT_SELECTOR"], True)
    before = decide.cohort_path(tmp_path).read_bytes()
    assert decide.start_cohort(tmp_path, "2026-11-09", ["NO_KEPT_SELECTOR"], True) == first
    assert decide.cohort_path(tmp_path).read_bytes() == before


@pytest.mark.parametrize("reasons,selected", [(["NO_KEPT_SELECTOR", "LATE_RUN"], True), (["NO_KEPT_SELECTOR", "DATA_STALE_OR_GAP"], False), (["NO_KEPT_SELECTOR"], False)])
def test_a_late_or_held_monday_starts_no_cohort(tmp_path, reasons: list, selected: bool) -> None:
    assert decide.start_cohort(tmp_path, AS_OF, reasons, selected) is None
    assert not decide.cohort_path(tmp_path).exists()


def test_exactly_one_book_is_due_each_monday() -> None:
    monday = date.fromisoformat(config.FIRST_GRID_DAY)
    seen = []
    while monday < date(2028, 1, 3):
        seen.append(config.phase_of(monday.isoformat()))
        monday += timedelta(days=7)
    assert set(seen) == {0, 1, 2, 3}
    assert seen == [i % 4 for i in range(len(seen))]            # each Monday one book, the next Monday the next book
    for a in range(len(seen) - 3):
        assert sorted(seen[a:a + 4]) == [0, 1, 2, 3]


def test_the_phase_of_the_first_forward_monday() -> None:
    assert config.phase_of("2026-11-02") == 1


def walk(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)
    else:
        yield x


def test_a_decision_holds_no_float_and_no_wall_clock() -> None:
    d = decide.record(AS_OF, GOOD, ["NO_KEPT_SELECTOR"], True, [Event(AS_OF, 1, SLOTS, SLOTS)], AS_OF, "ab", decide.health_counts(CLEAN, type("R", (), {"tradable": [], "daily_failed": [], "hourly_failed": [], "new_coins": []})), [])
    assert not [x for x in walk(d) if isinstance(x, float)]
    assert d["main_reason"] == "NO_KEPT_SELECTOR" and d["decision"] == "NO_TRADE" and d["due_book"] == d["phase"] == 1
    assert d["selected"] and not d["held"] and d["books"]["1"]["since"] == AS_OF
    assert set(json.loads(decide.dumps(d))) == set(d)


def test_the_main_reason_is_the_first_of_the_page() -> None:
    f = replace(GOOD, late=True, policy_expired=True)
    d = decide.record(AS_OF, f, decide.reasons_for(f), True, [], None, None, {}, [])
    assert d["reasons"] == ["NO_KEPT_SELECTOR", "LATE_RUN", "POLICY_EXPIRED"] and d["main_reason"] == "NO_KEPT_SELECTOR"


def test_a_record_is_replaced_whole_or_not_at_all(tmp_path, monkeypatch) -> None:
    path = tmp_path / "decision.json"
    decide.write_text(path, "old")

    def dies(a, b):
        raise OSError("disk full")

    monkeypatch.setattr(config.os, "replace", dies)
    with pytest.raises(OSError):
        decide.write_text(path, "new")
    assert path.read_text(encoding="utf8") == "old"


def test_the_real_side_stays_no_trade_whatever_the_facts_say() -> None:
    for f in (GOOD, replace(GOOD, slots=None), replace(GOOD, tripwire_fired=True)):
        d = decide.record(AS_OF, f, decide.reasons_for(f), False, [], None, None, {}, [])
        assert d["decision"] == decide.DECISION == "NO_TRADE"


def test_a_record_refuses_a_nan_rather_than_writing_it() -> None:
    with pytest.raises(ValueError):
        decide.dumps({"x": float("nan")})
