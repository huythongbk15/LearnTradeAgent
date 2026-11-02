"""The second frozen paper book: its rank and its ledger. Fills and cost are rs_book's and are tested there. No network: bars are written to a store in tmp_path."""
from __future__ import annotations

import json

import numpy as np
import pytest

from tests.test_selection_rs_book import MONDAY, NAMES, write_bars
from tools.selection import config, lv_book, rs_book, status, universe
from tools.selection.config import hour_of

MONDAYS = (MONDAY, "2026-11-16", "2026-11-23")


def market(root, mondays=(MONDAY,), weeks=4) -> None:
    """Eighteen coins that swing by .1% a step of rank every 4 hours until the Monday, then stay flat. C00 is the calmest."""
    hours = np.arange(hour_of(mondays[0]) - 800, hour_of(mondays[-1]) + 168 * weeks)
    for i, c in enumerate(NAMES):
        wiggle = np.where((hours // 4) % 2 == 0, 1.0, 1 + .001 * (i + 1))
        p = np.where(hours < hour_of(mondays[0]), 100.0 * wiggle, 100.0)
        write_bars(root, c, hours, p)
    config.tier_path(root).write_text(json.dumps({c: 20.0 for c in NAMES}), encoding="utf8")
    for m in mondays:
        universe.store_list(m, NAMES, root)


def test_the_rank_reads_nothing_at_or_after_its_monday() -> None:
    monday = hour_of(MONDAY)
    hours = np.arange(monday - 800, monday + 200)
    closes = 100.0 * np.where((hours // 4) % 2 == 0, 1.0, 1.01)
    a = lv_book.calm(hours, closes, monday)
    late = closes.copy()
    late[hours >= monday] = 1e6
    assert a == lv_book.calm(hours, late, monday) < 0


def test_a_wilder_coin_ranks_lower() -> None:
    monday = hour_of(MONDAY)
    hours = np.arange(monday - 800, monday)

    def swing(s: float) -> np.ndarray:
        return 100.0 * np.where((hours // 4) % 2 == 0, 1.0, 1 + s)

    assert lv_book.calm(hours, swing(.01), monday) < lv_book.calm(hours, swing(.001), monday) < 0
    assert lv_book.calm(hours, np.full(len(hours), 5.0), monday) == 0.0


def test_the_rank_uses_180_changes_of_4_hour_closes() -> None:
    monday = hour_of(MONDAY)
    hours = np.arange(monday - 800, monday)
    closes = np.full(len(hours), 100.0)
    closes[hours <= monday - 1 - 4 * 180] = 50.0           # the step falls in the first change of the window
    change = np.log(2.0) * np.r_[1.0, np.zeros(179)]
    assert lv_book.calm(hours, closes, monday) == pytest.approx(-float(np.std(change, ddof=1)))
    closes[hours > monday - 1 - 4 * 180] = 50.0            # the step is gone from the window
    assert lv_book.calm(hours, closes, monday) == 0.0


def test_too_few_changes_or_no_last_close_give_no_rank() -> None:
    monday = hour_of(MONDAY)
    hours = np.arange(monday - 4 * 150, monday)
    assert np.isnan(lv_book.calm(hours, np.full(len(hours), 1.0), monday))
    hours = np.arange(monday - 800, monday)
    closes = np.full(len(hours), 1.0)
    closes[-1] = np.nan
    assert np.isnan(lv_book.calm(hours, closes, monday))


def test_a_coin_whose_bars_stopped_gets_no_rank() -> None:
    monday = hour_of(MONDAY)
    hours = np.arange(monday - 800, monday)
    closes = 100.0 * np.where((hours // 4) % 2 == 0, 1.0, 1.01)
    keep = hours <= monday - 1 - config.LV_FRESH_HOURS
    assert np.isfinite(lv_book.calm(hours[keep], closes[keep], monday))
    keep = hours <= monday - 2 - config.LV_FRESH_HOURS
    assert np.isnan(lv_book.calm(hours[keep], closes[keep], monday))
    assert np.isnan(lv_book.calm(hours + 2000, closes, monday))


def test_a_pick_comes_on_its_monday_and_the_block_closes_two_mondays_on(tmp_path) -> None:
    market(tmp_path, MONDAYS)
    assert lv_book.update(tmp_path, MONDAY) == 0
    assert lv_book.update(tmp_path, "2026-11-16") == 0
    assert lv_book.update(tmp_path, "2026-11-23") == 1
    picks = [json.loads(x) for x in (lv_book.book_dir(tmp_path) / "picks.jsonl").read_text().splitlines()]
    assert [p["monday"] for p in picks] == list(MONDAYS)
    assert picks[0]["long"] == NAMES[:4] and picks[0]["short"] == NAMES[-4:]


def test_its_records_stay_apart_from_book_rs(tmp_path) -> None:
    market(tmp_path, MONDAYS)
    lv_book.update(tmp_path, "2026-11-23")
    assert not rs_book.book_dir(tmp_path).exists()
    a = (lv_book.book_dir(tmp_path) / "picks.jsonl").read_bytes()
    rs_book.update(tmp_path, "2026-11-23")
    assert a == (lv_book.book_dir(tmp_path) / "picks.jsonl").read_bytes() != (rs_book.book_dir(tmp_path) / "picks.jsonl").read_bytes()


def test_a_run_again_writes_the_same_bytes(tmp_path) -> None:
    market(tmp_path, MONDAYS)
    lv_book.update(tmp_path, "2026-11-23")
    a = [(lv_book.book_dir(tmp_path) / f).read_bytes() for f in ("ledger.jsonl", "picks.jsonl")]
    lv_book.update(tmp_path, "2026-11-23")
    assert a == [(lv_book.book_dir(tmp_path) / f).read_bytes() for f in ("ledger.jsonl", "picks.jsonl")]


def test_nothing_before_the_first_monday_is_picked(tmp_path) -> None:
    market(tmp_path, ("2026-11-02", MONDAY))
    lv_book.update(tmp_path, MONDAY)
    assert [json.loads(x)["monday"] for x in (lv_book.book_dir(tmp_path) / "picks.jsonl").read_text().splitlines()] == [MONDAY]


def test_a_masked_coin_on_a_list_is_refused(tmp_path, monkeypatch) -> None:
    market(tmp_path)
    monkeypatch.setattr(universe, "load_lists", lambda root: {MONDAY: ["ETH", *NAMES[:17]]})
    with pytest.raises(config.SealedSymbolError):
        lv_book.update(tmp_path, MONDAY)


def test_a_store_with_no_seed_writes_nothing(tmp_path) -> None:
    assert lv_book.update(tmp_path, MONDAY) == 0
    assert not lv_book.book_dir(tmp_path).exists()


def test_the_page_gets_a_count_and_no_return() -> None:
    from tests.test_selection_status import COHORT, DECISION
    s = status.build(DECISION, "OK", COHORT, 1, 0, 7, 5)
    assert (s["rs_blocks"], s["lv_blocks"]) == (7, 5)
    assert not {"net", "gross", "long", "short", "end_weights"} & set(s)


def test_the_frozen_terms() -> None:
    assert (config.LV_FIRST_MONDAY, config.LV_READ_WEEKS, config.LV_FRESH_HOURS) == ("2026-11-09", 104, 6)
