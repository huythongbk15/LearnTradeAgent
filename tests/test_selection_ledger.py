"""The paper ledger against plain Python loops and against the selector on a real library build. Synthetic numbers only."""
from __future__ import annotations

import json
import math

import numpy as np
import pytest

from tests.test_selection_fixture import market, panel_of
from tools.selection import config, ledger
from tools.selection import library as lib
from tools.selection import selector as sel
from tools.selection.config import ts
from tools.selection.decide import Event
from tools.selection.ledger import Window

END = "2026-11-30"          # a Monday, the stamp at the cutoff
CASH, BTC_I = lib.NAMES.index("cash"), lib.NAMES.index("hold_btc")
NAME_A, NAME_B, NAME_C = lib.NAMES[0], lib.NAMES[11], lib.NAMES[24]
COINS = ["BTC", "AAA", "CCC", "DDD", "EEE"]
SD = 0.051
MAIN = (config.MAIN_FILL_CLOCK, config.MAIN_COST_BASIS, "drift")
assert MAIN == ("open_1h_later", "tier_plus_10", "drift")


def window(weeks: int = 6, seed: int = 3) -> Window:
    """Random weights and returns on the real clock, ending at a Monday's stamp."""
    S = 42 * weeks + 2
    rng = np.random.default_rng(seed)
    t = ts(END) - 14400 * np.arange(S - 1, -1, -1)
    W = rng.uniform(-.1, .1, (32, S, len(COINS)))
    W[CASH] = 0.0
    W[BTC_I] = 0.0
    W[BTC_I, :, 0] = 1.0
    R = rng.normal(0, .02, (S - 2, len(COINS)))
    R_late = rng.normal(0, .02, (S - 2, len(COINS)))
    c = rng.uniform(10, 40, (S, len(COINS)))
    return Window(t, W, R, R_late, c, COINS)


EVENTS = [
    Event("2026-10-19", config.phase_of("2026-10-19"), (NAME_A, NAME_B, "cash"), (NAME_C, "cash", "cash")),
    Event("2026-10-26", config.phase_of("2026-10-26"), (NAME_B, NAME_C, "cash"), (NAME_A, NAME_B, "cash")),
    Event("2026-11-16", config.phase_of("2026-11-16"), ("cash", "cash", "cash"), (NAME_A, NAME_B, NAME_C)),
]
START = EVENTS[0].monday


def plain_path(win: Window, events: list[Event], field: str) -> np.ndarray:
    """A book at a stamp holds its last selection at or before the stamp, a slot holds a third, four books share the portfolio."""
    S, N = win.W.shape[1:]
    out = np.zeros((S, N))
    for s in range(S):
        for phase in range(4):
            mine = [e for e in events if e.phase == phase and ts(e.monday) <= win.t[s]]
            if mine:
                for name in getattr(mine[-1], field):
                    if name != "cash":
                        out[s] += win.W[lib.NAMES.index(name), s] / 3 / 4
    return out


def plain_paths(win: Window) -> dict[str, np.ndarray]:
    out = {"paper": plain_path(win, EVENTS, "slots"), "BEST_SO_FAR": plain_path(win, EVENTS, "best"), "EW": win.W.mean(0), "hold_btc": win.W[BTC_I].copy()}
    for w in out.values():
        w[win.t < ts(START)] = 0
    return out


def study_drift_cost(wp: np.ndarray, R: np.ndarray, c: np.ndarray, s0: int) -> np.ndarray:
    """drift_cost of the study's report_sens.py, line for line."""
    g = (wp * R).sum(1)
    den = 1 + g
    wd = wp[:-1] * (1 + R[:-1]) / den[:-1, None]
    dw = np.abs(wp[1:] - wd)
    out = np.zeros(len(wp))
    out[1:] = (dw * c[1:]).sum(1) / 2e4
    out[s0] = (np.abs(wp[s0]) * c[s0]).sum() / 2e4
    return out


def test_a_week_is_named_after_the_monday_that_closes_it() -> None:
    first, end = ledger.week_span("2026-11-09")
    assert first == ts("2026-11-01T20:00:00") and end == ts("2026-11-08T20:00:00") and (end - first) // 14400 == 42


def test_weeks_chain_with_no_stamp_left_out() -> None:
    assert ledger.week_span("2026-11-09")[1] == ledger.week_span("2026-11-16")[0]


def test_the_last_stamp_of_a_week_ends_before_the_cutoff_on_both_clocks() -> None:
    last = (ledger.week_span("2026-11-09")[1] - 14400) // 3600 - ts(config.T0) // 3600
    T = config.hour_of("2026-11-09")
    assert last + 4 < T and last + 1 + 4 < T          # the open of h+4 and of h+5 are on bars closed before the Monday
    assert last + 4 + 4 == T                           # one stamp later needs the open of the Monday itself


def test_open_weeks_start_a_week_after_the_first_selection() -> None:
    assert ledger.open_weeks([], [], "2026-11-09") == []
    assert ledger.open_weeks([], EVENTS, "2026-10-19") == []
    assert ledger.open_weeks([], EVENTS, "2026-11-02") == ["2026-10-26", "2026-11-02"]


def test_open_weeks_go_on_after_the_last_row_and_catch_up() -> None:
    assert ledger.open_weeks([{"week": "2026-10-26"}], EVENTS, "2026-10-26") == []
    assert ledger.open_weeks([{"week": "2026-10-26"}], EVENTS, "2026-11-16") == ["2026-11-02", "2026-11-09", "2026-11-16"]


def test_keep_from_is_the_stamp_before_the_oldest_open_week_or_the_last() -> None:
    t = window().t
    assert ledger.keep_from(t, []) == len(t) - 1
    a = ledger.keep_from(t, ["2026-11-23"])
    assert t[a] == ts("2026-11-15T16:00:00") and t[a + 1] == ledger.week_span("2026-11-23")[0]
    assert ledger.keep_from(t, ["2020-01-06"]) == 0


def test_weight_paths_against_plain_loops() -> None:
    win = window()
    got = ledger.weight_paths(win, EVENTS, START)
    for name, want in plain_paths(win).items():
        assert got[name] == pytest.approx(want, abs=1e-15), name


def test_every_portfolio_starts_flat_at_the_first_selection() -> None:
    win = window()
    got = ledger.weight_paths(win, EVENTS, START)
    i = int(np.searchsorted(win.t, ts(START)))
    for name, w in got.items():
        assert not w[:i].any() and w[i].any(), name


def test_a_selection_acts_from_its_own_stamp_and_not_before() -> None:
    win = window()
    w = ledger.four_books(win, [EVENTS[0]], "slots")
    i = int(np.searchsorted(win.t, ts(EVENTS[0].monday)))
    assert not w[i - 1].any()
    assert w[i] == pytest.approx((win.W[lib.NAMES.index(NAME_A), i] + win.W[lib.NAMES.index(NAME_B), i]) / 3 / 4)


def test_a_book_of_all_cash_holds_nothing_and_a_cash_slot_is_a_third_of_cash() -> None:
    win = window()
    e = Event("2026-10-19", 0, ("cash", "cash", "cash"), ("cash", "cash", NAME_A))
    assert not ledger.four_books(win, [e], "slots").any()
    a = np.abs(ledger.four_books(win, [e], "best")).sum(1)
    assert a.max() <= .1 * 5 / 12 + 1e-12


def test_gross_on_each_clock_against_a_plain_loop() -> None:
    win = window()
    w = plain_paths(win)["paper"]
    gross, _ = ledger.stamp_series(w, win)
    n = len(win.R)
    assert set(gross) == {"stamp_open", "open_1h_later"}
    for s in (5, 60, n - 1):
        assert gross["stamp_open"][s] == pytest.approx(sum(w[s, j] * win.R[s, j] for j in range(5)), abs=1e-16)
        assert gross["open_1h_later"][s] == pytest.approx(sum(w[s, j] * win.R_late[s, j] for j in range(5)), abs=1e-16)


def test_cost_without_drift_is_the_frozen_rule() -> None:
    win = window()
    w = plain_paths(win)["paper"]
    n = len(win.R)
    got = ledger.cost_path(w[:n], win.R, win.c[:n], False)
    for s in range(1, n, 17):
        assert got[s] == pytest.approx(sum(abs(w[s, j] - w[s - 1, j]) * win.c[s, j] for j in range(5)) / 2e4, abs=1e-15)


def test_cost_with_drift_is_the_studys_drift_cost() -> None:
    win = window()
    for name, w in plain_paths(win).items():
        n = len(win.R)
        s0 = int(np.searchsorted(win.t, ts(START)))
        got = ledger.cost_path(w[:n], win.R, win.c[:n], True)
        assert got[s0:] == pytest.approx(study_drift_cost(w[:n], win.R, win.c[:n], s0)[s0:], abs=1e-14), name


def test_drift_is_a_real_rebalance_cost() -> None:
    w = np.array([[.5, .5], [.5, .5]])
    R = np.array([[.1, -.1], [0.0, 0.0]])
    c = np.full((2, 2), 20.0)
    assert ledger.cost_path(w, R, c, False)[1] == 0.0
    # 0.55 and 0.45 of a capital that did not move, back to 0.5 and 0.5: 0.10 traded at 10 bps a side
    assert ledger.cost_path(w, R, c, True)[1] == pytest.approx(.1 * 20 / 2e4)


def test_the_cost_bases_are_tier_tier_plus_ten_and_flat_thirty() -> None:
    c = np.array([[11.0, 37.0]])
    assert ledger.basis_cost(c, "tier").tolist() == [[11.0, 37.0]]
    assert ledger.basis_cost(c, "tier_plus_10").tolist() == [[21.0, 47.0]]
    assert ledger.basis_cost(c, "flat_30").tolist() == [[30.0, 30.0]]


def test_net_is_gross_less_cost_on_every_cell() -> None:
    win = window()
    w = plain_paths(win)["EW"]
    gross, net = ledger.stamp_series(w, win)
    n = len(win.R)
    assert len(net) == 2 * 3 * 2
    for (clock, basis, drift), x in net.items():
        R = win.R if clock == "stamp_open" else win.R_late
        c = {"tier": win.c[:n], "tier_plus_10": win.c[:n] + 10, "flat_30": np.full((n, 5), 30.0)}[basis]
        cost = study_drift_cost(w[:n], R, c, 0) if drift == "drift" else lib.cost_on_weights(w[:n], c)
        assert x[1:] == pytest.approx((gross[clock] - cost)[1:], abs=1e-14)


def row_set(win: Window, weeks: list[str]) -> list[dict]:
    paths = ledger.weight_paths(win, EVENTS, START)
    stamps = {k: ledger.stamp_series(v, win) for k, v in paths.items()}
    rows: list[dict] = []
    cohort = {"start": "2026-10-19"}
    for m in weeks:
        rows.append(ledger.week_row(m, START, cohort, win, paths, stamps, rows))
    return rows


WEEKS = ["2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16", "2026-11-23", "2026-11-30"]


def test_the_first_week_has_41_stamps_and_the_others_42() -> None:
    rows = row_set(window(), WEEKS)
    assert [r["stamps"] for r in rows] == [41] + [42] * 5
    assert rows[0]["first_stamp"] == "2026-10-19T00:00:00Z" and rows[0]["last_stamp"] == "2026-10-25T16:00:00Z"
    assert rows[1]["first_stamp"] == "2026-10-25T20:00:00Z" and rows[1]["last_stamp"] == "2026-11-01T16:00:00Z"


def test_a_row_compounds_exactly_the_stamps_of_its_week() -> None:
    win = window()
    rows = row_set(win, WEEKS)
    w = plain_paths(win)["paper"]
    for r in rows:
        keep = (win.t[:len(win.R)] >= ts(r["first_stamp"][:-1])) & (win.t[:len(win.R)] <= ts(r["last_stamp"][:-1]))
        assert keep.sum() == r["stamps"]
        q = r["portfolios"]["paper"]
        assert q["gross"]["stamp_open"] == pytest.approx(np.prod(1 + (w[:len(win.R)] * win.R).sum(1)[keep]) - 1, abs=1e-14)
        assert q["gross"]["open_1h_later"] == pytest.approx(np.prod(1 + (w[:len(win.R)] * win.R_late).sum(1)[keep]) - 1, abs=1e-14)
        assert q["exposure"] == pytest.approx(np.abs(w[:len(win.R)][keep]).sum(1).mean(), abs=1e-14)


def test_weeks_chain_to_the_whole_span() -> None:
    win = window()
    rows = row_set(win, WEEKS)
    paths = ledger.weight_paths(win, EVENTS, START)
    _, net = ledger.stamp_series(paths["paper"], win)
    a = int(np.searchsorted(win.t, ts(START)))
    total = np.prod([1 + r["portfolios"]["paper"]["net"]["open_1h_later"]["tier_plus_10"]["drift"] for r in rows])
    assert total == pytest.approx(np.prod(1 + net[MAIN][a:]))


def test_every_cell_of_the_grid_is_there_for_every_portfolio() -> None:
    r = row_set(window(), WEEKS[:1])[0]
    assert set(r["portfolios"]) == {"paper", "EW", "BEST_SO_FAR", "hold_btc"}
    for name, q in r["portfolios"].items():
        assert set(q["net"]) == {"stamp_open", "open_1h_later"}
        for clock in q["net"]:
            assert set(q["net"][clock]) == {"tier", "tier_plus_10", "flat_30"}
            for basis in q["net"][clock]:
                assert set(q["net"][clock][basis]) == {"no_drift", "drift"}
        assert ("z" in q) == (name != "EW") and ("same_exposure" in q) == (name != "EW") and ("k" in q) == (name != "EW")


def test_same_exposure_is_the_week_minus_k_times_ew() -> None:
    rows = row_set(window(), WEEKS)
    for r in rows:
        ew = r["portfolios"]["EW"]
        for name in ("paper", "BEST_SO_FAR", "hold_btc"):
            q = r["portfolios"][name]
            k = q["exposure"] / ew["exposure"]
            assert q["k"] == pytest.approx(k)
            for clock in q["net"]:
                for basis in q["net"][clock]:
                    for d in ("no_drift", "drift"):
                        assert q["same_exposure"][clock][basis][d] == pytest.approx(q["net"][clock][basis][d] - k * ew["net"][clock][basis][d], abs=1e-15)


def test_k_of_a_week_with_no_ew_exposure_is_zero_not_a_crash() -> None:
    win = window()
    empty = Window(win.t, np.zeros_like(win.W), win.R, win.R_late, win.c, COINS)
    r = ledger.week_row("2026-11-02", START, None, empty, ledger.weight_paths(empty, EVENTS, START), {k: ledger.stamp_series(v, empty) for k, v in ledger.weight_paths(empty, EVENTS, START).items()}, [])
    assert r["portfolios"]["paper"]["k"] == 0.0


def test_z_is_the_sum_of_the_main_cell_over_510_bps_and_the_root_of_the_weeks() -> None:
    rows = row_set(window(), WEEKS)
    total = 0.0
    for n, r in enumerate(rows, 1):
        q = r["portfolios"]["paper"]
        total += q["same_exposure"]["open_1h_later"]["tier_plus_10"]["drift"]
        assert r["cohort_week"] == n and q["z"] == pytest.approx(total / (SD * math.sqrt(n)), abs=1e-12)


def test_the_cohort_counts_from_the_week_after_its_first_monday() -> None:
    assert ledger.cohort_week("2026-11-09", {"start": "2026-11-02"}) == 1
    assert ledger.cohort_week("2027-12-27", {"start": "2026-11-02"}) == 60
    assert ledger.cohort_week("2026-11-02", {"start": "2026-11-02"}) is None
    assert ledger.cohort_week("2026-10-26", {"start": "2026-11-02"}) is None
    assert ledger.cohort_week("2026-11-09", None) is None


def test_weeks_before_the_cohort_carry_no_z_and_do_not_count() -> None:
    win = window()
    paths = ledger.weight_paths(win, EVENTS, START)
    stamps = {k: ledger.stamp_series(v, win) for k, v in paths.items()}
    rows: list[dict] = []
    for m in WEEKS[:3]:
        rows.append(ledger.week_row(m, START, {"start": "2026-10-26"}, win, paths, stamps, rows))
    assert rows[0]["cohort_week"] is None and rows[0]["portfolios"]["paper"]["z"] is None
    d = rows[1]["portfolios"]["paper"]["same_exposure"]["open_1h_later"]["tier_plus_10"]["drift"]
    assert rows[1]["portfolios"]["paper"]["z"] == pytest.approx(d / SD)


def rows_of(zs: list[float | None]) -> list[dict]:
    return [{"week": f"w{i}", "cohort_week": None if z is None else i, "portfolios": {"paper": {"z": z}}} for i, z in enumerate(zs, 1)]


def test_the_tripwire_is_not_armed_before_week_8() -> None:
    assert ledger.tripwire([]) == "NOT_ARMED"
    assert ledger.tripwire(rows_of([-9.0] * 7)) == "NOT_ARMED"


def test_the_tripwire_fires_at_week_8_under_the_limit() -> None:
    assert ledger.tripwire(rows_of([0.0] * 7 + [-2.51])) == "FIRED"


def test_the_limit_itself_does_not_fire_it() -> None:
    assert ledger.tripwire(rows_of([0.0] * 7 + [-2.5])) == "OK"


def test_a_z_under_the_limit_before_week_8_does_not_fire_it() -> None:
    assert ledger.tripwire(rows_of([-9.0] * 7 + [0.0])) == "OK"


def test_a_fired_tripwire_stays_fired() -> None:
    assert ledger.tripwire(rows_of([0.0] * 7 + [-3.0] + [1.0] * 9)) == "FIRED"


def test_rows_before_the_cohort_do_not_arm_it() -> None:
    assert ledger.tripwire(rows_of([None] * 9)) == "NOT_ARMED"
    assert ledger.weeks_done(rows_of([None, None, 0.1])) == 1


def row(week: str) -> dict:
    return {"week": week, "cohort_week": None, "portfolios": {}}


def test_rows_append_and_read_back_one_a_line(tmp_path) -> None:
    ledger.append_rows(tmp_path, [row("2026-11-09")])
    ledger.append_rows(tmp_path, [row("2026-11-16"), row("2026-11-23")])
    assert [r["week"] for r in ledger.load_rows(tmp_path)] == ["2026-11-09", "2026-11-16", "2026-11-23"]
    assert len(ledger.ledger_path(tmp_path).read_text().splitlines()) == 3


@pytest.mark.parametrize("weeks", [["2026-11-09"], ["2026-11-02"], ["2026-11-16", "2026-11-16"], ["2026-11-16", "2026-11-09"]])
def test_a_row_is_final(tmp_path, weeks: list[str]) -> None:
    ledger.append_rows(tmp_path, [row("2026-11-09")])
    before = ledger.ledger_path(tmp_path).read_bytes()
    with pytest.raises(ValueError):
        ledger.append_rows(tmp_path, [row(w) for w in weeks])
    assert ledger.ledger_path(tmp_path).read_bytes() == before


def test_a_write_keeps_the_old_bytes_under_the_new_rows(tmp_path) -> None:
    ledger.append_rows(tmp_path, [row("2026-11-09")])
    before = ledger.ledger_path(tmp_path).read_bytes()
    ledger.append_rows(tmp_path, [row("2026-11-16")])
    assert ledger.ledger_path(tmp_path).read_bytes().startswith(before)


def test_a_write_that_dies_leaves_the_ledger_as_it_was(tmp_path, monkeypatch) -> None:
    ledger.append_rows(tmp_path, [row("2026-11-09")])
    before = ledger.ledger_path(tmp_path).read_bytes()

    def dies(a, b):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(config.os, "replace", dies)
    with pytest.raises(OSError):
        ledger.append_rows(tmp_path, [row("2026-11-16")])
    assert ledger.ledger_path(tmp_path).read_bytes() == before and [r["week"] for r in ledger.load_rows(tmp_path)] == ["2026-11-09"]


def test_an_empty_ledger_reads_empty_and_writes_no_file(tmp_path) -> None:
    assert ledger.load_rows(tmp_path) == []
    ledger.append_rows(tmp_path, [])
    assert not ledger.ledger_path(tmp_path).exists()


def bars_to(win: Window, monday: str) -> np.ndarray:
    return np.full(len(COINS), config.hour_of(monday) - 1)


def test_a_week_is_ready_when_every_held_coin_has_its_bars() -> None:
    win = window()
    paths = ledger.weight_paths(win, EVENTS, START)
    assert ledger.week_ready("2026-11-02", win, paths, bars_to(win, "2026-11-02"), set())


def test_the_last_hour_a_week_reads_is_the_late_fill_of_its_last_stamp() -> None:
    assert ledger.last_hour_read("2026-11-09") == config.hour_of("2026-11-08T21:00:00")
    last = (ledger.week_span("2026-11-09")[1] - 14400) // 3600 - ts(config.T0) // 3600
    assert ledger.last_hour_read("2026-11-09") == last + 1 + 4


@pytest.mark.parametrize("age,ready", [(2, True), (3, False), (7, False)])
def test_a_held_coin_short_of_the_last_hour_of_the_week_holds_it_back(age: int, ready: bool) -> None:
    """A bar short of that hour reads as a stop, so even a lag inside the stale limit would move the row."""
    win = window()
    paths = ledger.weight_paths(win, EVENTS, START)
    last = bars_to(win, "2026-11-02")
    last[2] -= age
    assert ledger.week_ready("2026-11-02", win, paths, last, set()) is ready


@pytest.mark.parametrize("lag,stop", [(2, False), (3, True)])
def test_a_lag_of_three_hours_reads_as_a_stop_on_the_last_stamp_of_the_week(lag: int, stop: bool) -> None:
    """Why the limit is where it is, on the rule itself: with bars short of that hour the late fill of the last stamp has no open to reach, and the coin leaves at its last close."""
    p = panel_of(market(), end="2020-09-07")
    j = p.coins.index("AAA")
    p.opens[-lag:, j] = p.closes[-lag:, j] = p.quotes[-lag:, j] = np.nan
    d = lib.Data(p, live=True)
    last = int(np.searchsorted(d.t, ledger.week_span("2020-09-07")[1])) - 1
    assert bool(lib.stamp_returns(d, lag=1)[1][last, j]) is stop


def test_a_coin_on_a_list_of_the_week_counts_even_with_no_weight() -> None:
    win = window()
    W = win.W.copy()
    W[:, :, 3] = 0.0
    win = Window(win.t, W, win.R, win.R_late, win.c, COINS)
    paths = ledger.weight_paths(win, EVENTS, START)
    last = bars_to(win, "2026-11-02")
    last[3] = -1
    assert not ledger.week_ready("2026-11-02", win, paths, last, set(), {"DDD"})
    assert ledger.week_ready("2026-11-02", win, paths, last, {"DDD"}, {"DDD"})


def test_a_week_reads_the_list_of_the_monday_before_it_and_the_list_inside_it() -> None:
    lists = {"2026-10-12": ["OLD"], "2026-10-19": ["SUN"], "2026-10-26": ["MON"], "2026-11-02": ["NEXT"]}
    assert ledger.listed_in("2026-11-02", lists) == {"SUN", "MON"}      # the week starts on Sunday 25 October at 20:00
    assert ledger.listed_in("2026-11-02", {k: v for k, v in lists.items() if k != "2026-10-26"}) == {"SUN"}     # no list that Monday, Mem keeps the last
    assert ledger.listed_in("2026-11-02", {}) == set()


def test_update_waits_for_a_listed_coin_with_no_bars(tmp_path) -> None:
    win = window()
    W = win.W.copy()
    W[:, :, 3] = 0.0
    win = Window(win.t, W, win.R, win.R_late, win.c, COINS)
    last = bars_to(win, "2026-11-30")
    last[3] = config.hour_of("2026-11-09") - 1
    lists = {"2026-10-19": ["AAA"], "2026-11-02": ["AAA", "DDD"]}
    assert [r["week"] for r in ledger.update(tmp_path, "2026-11-30", win, EVENTS, None, last, set(), lists)] == WEEKS[:3]


def test_a_coin_the_exchange_says_stopped_lets_the_week_close() -> None:
    win = window()
    paths = ledger.weight_paths(win, EVENTS, START)
    last = bars_to(win, "2026-11-02")
    last[2] -= 40
    assert ledger.week_ready("2026-11-02", win, paths, last, {"CCC"})


def test_a_coin_with_no_weight_in_the_week_does_not_hold_it_back() -> None:
    win = window()
    W = win.W.copy()
    W[:, :, 3] = 0.0
    win = Window(win.t, W, win.R, win.R_late, win.c, COINS)
    paths = ledger.weight_paths(win, EVENTS, START)
    last = bars_to(win, "2026-11-02")
    last[3] = -1
    assert ledger.week_ready("2026-11-02", win, paths, last, set())


def test_update_writes_open_weeks_in_order_then_waits_at_the_first_that_is_not_ready(tmp_path) -> None:
    win = window()
    last = bars_to(win, "2026-11-30")
    last[2] = config.hour_of("2026-11-09") - 1          # CCC has its bars for two open weeks more, then none
    got = ledger.update(tmp_path, "2026-11-30", win, EVENTS, None, last, set())
    assert [r["week"] for r in got] == ["2026-10-26", "2026-11-02", "2026-11-09"]
    assert [r["week"] for r in ledger.load_rows(tmp_path)] == ["2026-10-26", "2026-11-02", "2026-11-09"]
    assert [r["week"] for r in ledger.update(tmp_path, "2026-11-30", win, EVENTS, None, bars_to(win, "2026-11-30"), set())] == WEEKS[3:]


def test_a_ready_week_waits_behind_one_that_is_not(tmp_path) -> None:
    """A hole in an append only file could never be filled, so the order holds even if a later week could be written."""
    win = window()
    W = win.W.copy()
    W[:, win.t >= ledger.week_span("2026-11-09")[0], 2] = 0.0       # CCC is held up to the week that closes on 2 November and no later
    win = Window(win.t, W, win.R, win.R_late, win.c, COINS)
    last = bars_to(win, "2026-11-30")
    last[2] = config.hour_of("2026-10-26") - 1
    paths = ledger.weight_paths(win, EVENTS, START)
    assert not ledger.week_ready("2026-11-02", win, paths, last, set()) and ledger.week_ready("2026-11-09", win, paths, last, set())
    assert [r["week"] for r in ledger.update(tmp_path, "2026-11-30", win, EVENTS, None, last, set())] == ["2026-10-26"]


def test_update_with_nothing_selected_writes_nothing(tmp_path) -> None:
    win = window()
    assert ledger.update(tmp_path, "2026-11-30", win, [], None, bars_to(win, "2026-11-30"), set()) == []
    assert not ledger.ledger_path(tmp_path).exists()


def test_a_written_row_is_plain_json_with_no_nan(tmp_path) -> None:
    win = window()
    ledger.update(tmp_path, "2026-11-02", win, EVENTS, {"start": "2026-10-19"}, bars_to(win, "2026-11-02"), set())
    for line in ledger.ledger_path(tmp_path).read_text().splitlines():
        json.loads(line, parse_constant=lambda x: pytest.fail(x))


def test_the_target_is_the_paper_book_at_the_stamp_of_the_monday() -> None:
    win = window()
    got = ledger.target_weights(win, EVENTS, END)
    w = plain_paths(win)["paper"][-1]
    assert got == pytest.approx({c: x for c, x in zip(COINS, w)})
    assert ledger.target_weights(win, [], END) == {}


def test_a_target_leaves_out_a_coin_with_no_weight() -> None:
    win = window()
    W = win.W.copy()
    W[:, :, 3] = 0.0
    got = ledger.target_weights(Window(win.t, W, win.R, win.R_late, win.c, COINS), EVENTS, END)
    assert "DDD" not in got and "AAA" in got


def test_paths_and_series_match_the_selector_on_a_library_build() -> None:
    """Four books from the selector's own lists on a real build: same weights, same gross, same frozen cost as the ledger's."""
    panel = panel_of(market())
    d = lib.Data(panel)
    s = lib.build_series(d, keep_from=0)
    W = np.stack([s.recent[n] for n in lib.NAMES])
    t = d.t
    bk = sel.Book(s.t, s.gross, s.cost, s.c, W)
    sb = sel.score_book(s.net, s.t)
    events = []
    wp = np.zeros((bk.S, bk.N))
    g = np.zeros(bk.S)
    for phase in range(4):
        days, _, ch = sel.primary_lists(bk, sb, phase=phase)
        wp += sel.path(bk, ch, bk.stamp_of_day(days))
        g += sel.gross_path(bk, ch, bk.stamp_of_day(days))
        for day, row_ in zip(days, ch):
            names = tuple(sel.slot_names(row_))
            events.append(Event(str(np.datetime64(int(day), "D")), phase, names, names))
    wp /= 4
    g /= 4
    events.sort(key=lambda e: e.monday)
    win = ledger.window_of(s, t, d.coins)
    paths = ledger.weight_paths(win, events, config.FIRST_GRID_DAY)
    assert (paths["paper"] == wp).all()
    ew_g, ew_c, ew_w = sel.ew_series(bk)
    assert paths["EW"] == pytest.approx(ew_w, abs=1e-15)
    gross, net = ledger.stamp_series(paths["paper"], win)
    assert gross["stamp_open"] == pytest.approx(g, abs=1e-13)
    cost = lib.cost_on_weights(wp, bk.c[:bk.S])
    assert net["stamp_open", "tier", "no_drift"] == pytest.approx(g - cost, abs=1e-13)
    gross_ew, net_ew = ledger.stamp_series(paths["EW"], win)
    assert net_ew["stamp_open", "tier", "no_drift"] == pytest.approx(ew_g - ew_c, abs=1e-13)
    assert (win.R_late == s.R_late).all() and len(win.t) == d.S
