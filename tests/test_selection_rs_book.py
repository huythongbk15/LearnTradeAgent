"""The frozen paper book: its rank, its fills, its ledger. No network: bars are written to a store in tmp_path."""
from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest

from tools.selection import config, rs_book, status, universe
from tools.selection.config import hour_of

MONDAY = "2026-11-09"
NAMES = [f"C{i:02d}" for i in range(18)]


def write_bars(root, coin, hours, opens, closes=None) -> None:
    config.bars_dir(root).mkdir(parents=True, exist_ok=True)
    hours = np.asarray(hours, np.int64)
    closes = opens if closes is None else closes
    pl.DataFrame({"hour": hours, "o": np.asarray(opens, float), "c": np.asarray(closes, float), "q": np.ones(len(hours))},
                 schema={"hour": pl.Int32, "o": pl.Float64, "c": pl.Float64, "q": pl.Float32}).write_parquet(config.bars_dir(root) / f"{coin}.parquet")


def market(root, mondays=(MONDAY,), weeks=4) -> None:
    """Eighteen coins that fall from a high by 1% a step of rank, then stay flat, one long and one short leg moved in the first block."""
    start = hour_of(mondays[0]) - 800
    hours = np.arange(start, hour_of(mondays[-1]) + 168 * weeks)
    for i, c in enumerate(NAMES):
        p = np.full(len(hours), 100.0)
        p[hours >= hour_of(mondays[0]) - 24] = 100.0 * (1 - .01 * i)
        write_bars(root, c, hours, p)
    config.tier_path(root).write_text(json.dumps({c: 20.0 for c in NAMES}), encoding="utf8")
    for m in mondays:
        universe.store_list(m, NAMES, root)


def test_a_rank_reads_nothing_at_or_after_its_monday() -> None:
    monday = hour_of(MONDAY)
    hours = np.arange(monday - 800, monday + 100)
    closes = np.full(len(hours), 100.0)
    a = rs_book.dhi30(hours, closes, monday)
    closes[hours >= monday] = 1e6
    assert a == rs_book.dhi30(hours, closes, monday) == 0.0


def test_the_high_is_the_last_30_days_of_4_hour_closes() -> None:
    monday = hour_of(MONDAY)
    hours = np.arange(monday - 900, monday)
    closes = np.full(len(hours), 100.0)
    closes[hours == monday - 1 - 4 * 180] = 500.0          # one step past the window
    closes[hours == monday - 2] = 400.0                    # not a 4 hour close
    assert rs_book.dhi30(hours, closes, monday) == 0.0
    closes[hours == monday - 1 - 4 * 179] = 200.0
    assert rs_book.dhi30(hours, closes, monday) == pytest.approx(np.log(.5))


def test_a_coin_with_under_160_closes_has_no_rank() -> None:
    monday = hour_of(MONDAY)
    hours = np.arange(monday - 4 * 159, monday)
    assert np.isnan(rs_book.dhi30(hours, np.full(len(hours), 1.0), monday))
    hours = np.arange(monday - 4 * 160, monday)
    assert rs_book.dhi30(hours, np.full(len(hours), 1.0), monday) == 0.0


def test_legs_are_the_top_and_bottom_fifth_of_18_names() -> None:
    hi, lo = rs_book.legs({c: -float(i) for i, c in enumerate(NAMES)})
    assert hi == NAMES[:4] and lo == NAMES[-4:]


def test_too_few_names_have_no_legs() -> None:
    assert rs_book.legs({c: 0.0 for c in NAMES[:9]}) is None
    assert rs_book.legs({**{c: float("nan") for c in NAMES[:9]}, **{c: 0.0 for c in NAMES[9:18]}}) is None


def test_a_short_leaves_at_the_first_4_hour_open_past_the_stop() -> None:
    entry = 1000
    hours = np.arange(entry, entry + 169)
    opens = np.full(len(hours), 100.0)
    opens[hours == entry + 2] = 900.0                       # between two stamps: not seen
    opens[hours == entry + 8] = 170.0
    opens[hours >= entry + 12] = 400.0
    assert rs_book.hold(hours, opens, entry, .5) == (pytest.approx(1.7), True)
    assert rs_book.hold(hours, opens, entry)[0] == pytest.approx(4.0)


def test_a_coin_with_no_fresh_entry_open_is_not_held() -> None:
    assert rs_book.hold(np.array([10]), np.array([5.0]), 1000) is None
    assert rs_book.hold(np.array([2000]), np.array([5.0]), 1000) is None


def test_a_last_open_carried_past_a_day_is_marked() -> None:
    hours = np.arange(1000, 1100)
    assert rs_book.hold(hours, np.full(100, 2.0), 1000) == (1.0, False)


def test_a_block_pays_twice_the_tier_on_the_whole_book_the_first_time() -> None:
    entry = hour_of(MONDAY) + 4
    hours = np.arange(entry - 4, entry + 169)
    bars = {"A": (hours, np.full(len(hours), 10.0)), "B": (hours, np.full(len(hours), 10.0))}
    row = rs_book.settle({"monday": MONDAY, "long": ["A"], "short": ["B"]}, {}, {"A": 20.0, "B": 40.0}, bars)
    assert row["gross"] == 0.0
    assert row["cost"] == pytest.approx(.5 * 20 * 2 / 2e4 + .5 * 40 * 2 / 2e4)
    assert row["end_weights"] == {"A": .5, "B": -.5}


def test_a_held_position_pays_only_its_turnover() -> None:
    entry = hour_of(MONDAY) + 4
    hours = np.arange(entry - 4, entry + 169)
    bars = {"A": (hours, np.full(len(hours), 10.0)), "B": (hours, np.full(len(hours), 10.0))}
    row = rs_book.settle({"monday": MONDAY, "long": ["A"], "short": ["B"]}, {"A": .5, "B": -.5}, {}, bars)
    assert row["cost"] == 0.0


def test_a_short_that_rises_costs_the_book_and_a_long_earns_it() -> None:
    entry = hour_of(MONDAY) + 4
    hours = np.arange(entry - 4, entry + 169)
    up = np.where(hours >= entry + 164, 12.0, 10.0)
    row = rs_book.settle({"monday": MONDAY, "long": ["A"], "short": ["B"]}, {"A": .5, "B": -.5}, {}, {"A": (hours, up), "B": (hours, np.full(len(hours), 10.0))})
    assert row["net"] == pytest.approx(.1)
    row = rs_book.settle({"monday": MONDAY, "long": ["A"], "short": ["B"]}, {"A": .5, "B": -.5}, {}, {"B": (hours, up), "A": (hours, np.full(len(hours), 10.0))})
    assert row["gross"] == pytest.approx(-.1)


def test_a_pick_comes_on_its_monday_and_the_block_closes_two_mondays_on(tmp_path) -> None:
    mondays = (MONDAY, "2026-11-16", "2026-11-23")
    market(tmp_path, mondays)
    assert rs_book.update(tmp_path, MONDAY) == 0
    assert rs_book.update(tmp_path, "2026-11-16") == 0
    assert rs_book.update(tmp_path, "2026-11-23") == 1
    picks = [json.loads(x) for x in (rs_book.book_dir(tmp_path) / "picks.jsonl").read_text().splitlines()]
    assert [p["monday"] for p in picks] == list(mondays)
    assert picks[0]["long"] == NAMES[:4] and picks[0]["short"] == NAMES[-4:]


def test_a_run_again_writes_the_same_bytes(tmp_path) -> None:
    mondays = (MONDAY, "2026-11-16", "2026-11-23")
    market(tmp_path, mondays)
    rs_book.update(tmp_path, "2026-11-23")
    a = (rs_book.book_dir(tmp_path) / "ledger.jsonl").read_bytes(), (rs_book.book_dir(tmp_path) / "picks.jsonl").read_bytes()
    rs_book.update(tmp_path, "2026-11-23")
    assert a == ((rs_book.book_dir(tmp_path) / "ledger.jsonl").read_bytes(), (rs_book.book_dir(tmp_path) / "picks.jsonl").read_bytes())


def test_a_monday_with_no_list_is_filled_by_a_later_run_or_closes_as_missed(tmp_path) -> None:
    market(tmp_path, (MONDAY, "2026-11-23"))
    assert rs_book.update(tmp_path, "2026-11-30") == 2
    rows = [json.loads(x) for x in (rs_book.book_dir(tmp_path) / "ledger.jsonl").read_text().splitlines()]
    assert "net" in rows[0] and rows[1] == {"week": "2026-11-16", "missed": "no list"}


def test_nothing_before_the_first_monday_is_picked(tmp_path) -> None:
    market(tmp_path, ("2026-11-02", MONDAY))
    rs_book.update(tmp_path, MONDAY)
    assert [json.loads(x)["monday"] for x in (rs_book.book_dir(tmp_path) / "picks.jsonl").read_text().splitlines()] == [MONDAY]


def test_a_masked_coin_on_a_list_is_refused(tmp_path, monkeypatch) -> None:
    market(tmp_path)
    monkeypatch.setattr(universe, "load_lists", lambda root: {MONDAY: ["ETH", *NAMES[:17]]})
    with pytest.raises(config.SealedSymbolError):
        rs_book.update(tmp_path, MONDAY)


def test_the_page_gets_a_count_and_no_return(tmp_path) -> None:
    from tests.test_selection_status import COHORT, DECISION
    s = status.build(DECISION, "OK", COHORT, 1, 0, 7)
    assert s["rs_blocks"] == 7
    assert not {"net", "gross", "long", "short", "end_weights"} & set(s)


def test_the_frozen_terms() -> None:
    assert (config.RS_FIRST_MONDAY, config.RS_READ_WEEKS, config.RS_ENTRY_HOURS, config.RS_SIDE, config.RS_SHORT_STOP, config.RS_COST_TIMES) == ("2026-11-09", 104, 4, .5, .5, 2.0)
