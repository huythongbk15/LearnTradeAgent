"""status.json alone: the page's fields are an allowlist and write refuses anything else. test_selection_run.py asserts the same on the file of a real run."""
from __future__ import annotations

import json

import pytest

from tools.selection import status

DECISION = {
    "as_of": "2026-11-02", "phase": 1, "due_book": 1, "decision": "NO_TRADE", "main_reason": "NO_KEPT_SELECTOR", "reasons": ["NO_KEPT_SELECTOR"],
    "held": False, "list": ["AAA"], "list_monday": "2026-11-02",
    "health": {"checked": 19, "readable": True, "missing": 0, "stale": 0, "gaps": 0, "daily_pairs": 519, "daily_failed": 0, "hourly_failed": 0, "new_coins": 0, "weeks_waiting": 0},
    "selected": True, "computed_slots": ["B_ma1d_LS"], "books": {}, "scores_sha256": "ab", "errors": [],
}
COHORT = {"start": "2026-11-02", "read_date": "2027-12-27", "weeks": 60}


def test_status_carries_the_page_fields_and_nothing_of_the_book() -> None:
    s = status.build(DECISION, "NOT_ARMED", COHORT, 0, 0)
    assert set(s) == status.FIELDS and s["next_run"] == "2026-11-09" and s["read_date"] == "2027-12-27" and s["cohort_start"] == "2026-11-02"
    assert "computed_slots" not in s and "books" not in s and "scores_sha256" not in s


def test_status_before_a_cohort_has_no_dates() -> None:
    s = status.build(DECISION, "NOT_ARMED", None, 0, 3)
    assert s["cohort_start"] is None and s["read_date"] is None and s["data_health"]["errors"] == 3


@pytest.mark.parametrize("state", ["OK", "FIRED", "NOT_ARMED"])
def test_the_tripwire_has_three_states(state: str) -> None:
    assert status.build(DECISION, state, None, 0, 0)["tripwire"] == state


def test_an_unknown_tripwire_state_is_refused() -> None:
    with pytest.raises(ValueError):
        status.build(DECISION, "z -3.1", None, 0, 0)


@pytest.mark.parametrize("field", ["net_return", "sharpe", "pnl", "equity", "z"])
def test_write_refuses_any_field_outside_the_allowlist(tmp_path, field: str) -> None:
    s = status.build(DECISION, "OK", COHORT, 9, 0)
    with pytest.raises(ValueError, match="not a page field"):
        status.write(tmp_path, {**s, field: 0.12})
    with pytest.raises(ValueError, match="not a page field"):
        status.write(tmp_path, {**s, "data_health": {**s["data_health"], field: 0.12}})
    assert not status.status_path(tmp_path).exists()


def test_the_last_status_gives_the_tripwire_of_a_run_that_cannot_read_its_ledger(tmp_path) -> None:
    assert status.last_known(tmp_path) == ("NOT_ARMED", 0)
    status.write(tmp_path, status.build(DECISION, "FIRED", COHORT, 9, 0))
    assert status.last_known(tmp_path) == ("FIRED", 9)
    status.status_path(tmp_path).write_text("not json", encoding="utf8")
    assert status.last_known(tmp_path) == ("NOT_ARMED", 0)


def test_write_leaves_the_file_and_no_temporary(tmp_path) -> None:
    status.write(tmp_path, status.build(DECISION, "OK", COHORT, 9, 0))
    assert json.loads(status.status_path(tmp_path).read_text(encoding="utf8"))["weeks_done"] == 9
    assert [p.name for p in tmp_path.iterdir()] == ["status.json"]
