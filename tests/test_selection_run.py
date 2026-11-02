"""A weekly run end to end on the fake exchange: the records, every NO_TRADE reason from a fixture, the book on a blocked Monday, reruns byte for byte, failures in the middle of a run, and what the run may never touch."""
from __future__ import annotations

import ast
import copy
import fcntl
import hashlib
import json
import os
import re
import sys
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from tests.test_selection_exchange import FROZEN_LAST, MONDAYS, SEED_END, FakeExchange, World, blank, hour_ms, make_world, on_time, refuse_network, seed_market, without
from tests.test_selection_fixture import market
from tools.selection import config, decide, fetch, ledger, run_weekly, universe
from tools.selection import library as lib
from tools.selection import panel as pn
from tools.selection import selector as sel
from tools.selection.config import hour_of

M0, M1, M2, M3, M4 = MONDAYS[:5]
GOOD = ["NO_KEPT_SELECTOR"]


@pytest.fixture(autouse=True)
def no_network(monkeypatch) -> None:
    refuse_network(monkeypatch)


@pytest.fixture
def world(tmp_path, monkeypatch):
    return make_world(tmp_path, monkeypatch)


def listed_at(world, monday: str) -> list[str]:
    return universe.load_lists(world.root)[monday]


def fresh(world: World, tmp_path, monkeypatch) -> World:
    """A second root on the same seed and the same market."""
    other = tmp_path / "other"
    monkeypatch.setenv("SELECTION_ROOT", str(other))
    return World(other, world.seed, FakeExchange(world.exchange.mk))


def test_a_first_run_on_time_writes_every_record(world) -> None:
    out = world.run(M0)
    assert out.decision == "NO_TRADE" and out.reasons == tuple(GOOD) and not out.rerun
    assert sorted(p.name for p in world.folder(M0).iterdir()) == ["decision.json", "manifest.json", "policy_draft.json", "target.json"]
    d = world.decision(M0)
    assert d["reasons"] == GOOD and d["main_reason"] == "NO_KEPT_SELECTOR" and d["selected"] and not d["held"]
    assert d["due_book"] == d["phase"] == config.phase_of(M0) == 3
    assert len(d["list"]) == 18 and d["list_monday"] == M0 and len(d["computed_slots"]) == 3
    assert d["books"]["3"]["since"] == M0 and d["books"]["3"]["slots"] == d["computed_slots"] and d["books"]["0"] is None
    assert not d["errors"] and d["health"]["daily_pairs"] == 20 and d["health"]["checked"] == 19
    t = world.target(M0)
    assert t["paper_only"] is True and t["consumer"] == "none" and t["decision"] == "NO_TRADE" and t["complete"] is True
    assert t["books"] == d["books"] and t["weights"]
    assert (world.root / "status.json").exists() and config.bars_dir(world.root).exists()


def test_a_target_holds_a_quarter_of_the_book_until_the_others_have_selected(world) -> None:
    world.run(M0)
    one = world.target(M0)
    world.run(M1)
    two = world.target(M1)
    assert sum(abs(x) for x in one["weights"].values()) <= .25 + 1e-12
    assert sum(1 for b in two["books"].values() if b) == 2


def test_a_decision_holds_names_and_counts_and_a_target_stays_within_the_book(world) -> None:
    for monday in MONDAYS[:5]:
        world.run(monday)
    d = world.decision(MONDAYS[4])
    assert not [x for x in numbers_of(d) if isinstance(x, float)] and not [k for k in keys_of(d) if re.search("return|sharpe|pnl|gross|net$|^z$", k, re.I)]
    weights = world.target(MONDAYS[4])["weights"]
    assert all(b is not None for b in d["books"].values()) and 0 < sum(abs(x) for x in weights.values()) <= 1 + 1e-12


def test_the_cohort_starts_at_the_first_good_monday_and_reads_60_weeks_later(world) -> None:
    world.run(M0)
    assert decide.load_cohort(world.root) == {"start": M0, "read_date": "2021-10-25", "weeks": 60}
    assert world.status()["cohort_start"] == M0 and world.status()["read_date"] == "2021-10-25"


def test_the_cohort_is_written_once(world) -> None:
    world.run(M0)
    before = decide.cohort_path(world.root).read_bytes()
    world.run(M1)
    world.run(M2)
    assert decide.cohort_path(world.root).read_bytes() == before


def test_the_manifest_binds_the_records_by_hash(world) -> None:
    now = on_time(M0)
    world.run(M0, now=now)
    m = decide.read_json(world.folder(M0) / "manifest.json")
    for name in ("decision.json", "target.json", "policy_draft.json"):
        assert m["files"][name] == hashlib.sha256((world.folder(M0) / name).read_bytes()).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", m["code_sha256"]) and re.fullmatch(r"[0-9a-f]{64}", m["store_sha256"]) and re.fullmatch(r"[0-9a-f]{64}", m["seed_sha256"])
    assert m["started_at"] == now.isoformat() and m["as_of"] == M0 and m["errors"] == [] and m["lists_added"] == 1
    assert m["fetch"]["attempted"] and m["fetch"]["calls"] == len(world.exchange.calls)


def test_the_wall_clock_lives_only_in_the_manifest(world) -> None:
    world.run(M0, now=datetime(2031, 5, 6, 7, 8, tzinfo=UTC))
    for name, data in world.files().items():
        assert b"2031" not in data, name
    assert b"2031" in (world.folder(M0) / "manifest.json").read_bytes()


def test_a_rerun_of_a_finished_monday_changes_nothing(world) -> None:
    world.run(M0)
    before = world.files(skip_wall_clock=False)
    calls = len(world.exchange.calls)
    out = world.run(M0, now=on_time(M0, 90))
    assert out.rerun and out.reasons == tuple(GOOD)
    assert world.files(skip_wall_clock=False) == before and len(world.exchange.calls) == calls


def test_a_rerun_of_a_failed_monday_changes_nothing_either(world) -> None:
    world.exchange.hook = lambda n, path, q: (500, b"")
    first = world.run(M0)
    assert "LIST_INCOMPLETE" in first.reasons
    before = world.files(skip_wall_clock=False)
    world.exchange.hook = None
    assert world.run(M0).rerun and world.files(skip_wall_clock=False) == before


def test_a_rerun_after_later_mondays_changes_nothing(world) -> None:
    world.run(M0)
    world.run(M1)
    before = world.files(skip_wall_clock=False)
    assert world.run(M0).rerun and world.files(skip_wall_clock=False) == before


def test_a_run_cannot_go_back_before_the_last_finished_monday(world) -> None:
    world.run(M1)
    with pytest.raises(run_weekly.Refused):
        world.run(M0)


def test_the_same_inputs_give_the_same_bytes(world, tmp_path, monkeypatch) -> None:
    def chain(w):
        w.run(M0)
        w.exchange.calls.clear()
        w.exchange.hook = lambda n, path, q: (429, b"") if n == 30 else None
        w.run(M1)
        w.exchange.hook = None
        w.run(M2)
        w.run(M3, now=on_time(M3, 500))
        return w.files()

    a = chain(world)
    b = chain(fresh(world, tmp_path, monkeypatch))
    assert sorted(a) == sorted(b)
    assert [k for k in a if a[k] != b[k]] == []
    assert any(k.endswith("target.json") for k in a) and "ledger.jsonl" in a and any(k.endswith("policy_draft.json") for k in a)


def test_a_run_that_died_before_its_manifest_is_done_again_and_the_ledger_has_no_twin(world, tmp_path, monkeypatch) -> None:
    world.run(M0)
    real = run_weekly.manifest

    def dies(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(run_weekly, "manifest", dies)
    with pytest.raises(OSError):
        world.run(M1)
    assert not (world.folder(M1) / "manifest.json").exists() and [r["week"] for r in ledger.load_rows(world.root)] == [M1]
    monkeypatch.setattr(run_weekly, "manifest", real)
    assert not world.run(M1).rerun
    got = world.files()

    clean = fresh(world, tmp_path, monkeypatch)
    clean.run(M0)
    clean.run(M1)
    want = clean.files()
    assert [k for k in want if got.get(k) != want[k]] == [] and [r["week"] for r in ledger.load_rows(world.root)] == [M1]


def test_a_monday_finished_is_a_monday_on_the_page(world, monkeypatch) -> None:
    """The status goes before the manifest: once a rerun says finished, the page has that Monday."""
    world.run(M0)
    real = run_weekly.status.write

    def dies(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(run_weekly.status, "write", dies)
    with pytest.raises(OSError):
        world.run(M1)
    assert not (world.folder(M1) / "manifest.json").exists()
    monkeypatch.setattr(run_weekly.status, "write", real)
    assert not world.run(M1).rerun and world.status()["as_of"] == M1


def fire_the_tripwire(world) -> run_weekly.Outcome:
    world.run(M0)
    world.run(M1)
    rows = ledger.load_rows(world.root)
    main = (config.MAIN_FILL_CLOCK, config.MAIN_COST_BASIS, "drift")
    for i, monday in enumerate(MONDAYS[2:8], 2):
        r = copy.deepcopy(rows[0])
        r["week"], r["cohort_week"] = monday, i
        for name in ("paper", "BEST_SO_FAR", "hold_btc"):
            r["portfolios"][name]["same_exposure"][main[0]][main[1]][main[2]] = -.2
            r["portfolios"][name]["z"] = -.2
        ledger.append_rows(world.root, [r])
    return world.run(MONDAYS[8])


def test_a_ledger_that_cannot_be_read_keeps_a_fired_tripwire(world) -> None:
    """The rule says it stays fired. A torn file must not read as a tripwire that never armed."""
    fire_the_tripwire(world)
    assert world.status()["tripwire"] == "FIRED" and world.status()["weeks_done"] == 8
    path = ledger.ledger_path(world.root)
    path.write_bytes(path.read_bytes() + b'{"week": "2020')
    out = world.run("2020-11-02", fetching=False)
    assert "BOOK_TRIPWIRE" in out.reasons and world.status()["tripwire"] == "FIRED" and world.status()["weeks_done"] == 8
    assert "ledger_read: JSONDecodeError" in world.decision("2020-11-02")["errors"]


def test_a_ledger_that_cannot_be_read_gets_no_row(world) -> None:
    world.run(M0)
    world.run(M1)
    path = ledger.ledger_path(world.root)
    torn = path.read_bytes() + b'{"week": "2020'
    path.write_bytes(torn)
    world.run(M2)
    assert path.read_bytes() == torn and world.status()["weeks_done"] == 1
    path.write_bytes(torn.rsplit(b"\n", 1)[0] + b"\n")
    world.run(M3)
    assert [r["week"] for r in ledger.load_rows(world.root)] == [M1, M2, M3]


def test_a_cohort_file_that_cannot_be_read_leaves_a_record_and_no_row(world) -> None:
    """A week written without the cohort would lose its number for good, so it waits."""
    world.run(M0)
    path = decide.cohort_path(world.root)
    good = path.read_bytes()
    path.write_text('{"read_da', encoding="utf8")
    out = world.run(M1)
    d = world.decision(M1)
    assert list(out.reasons) == GOOD and d["errors"] == ["cohort_read: JSONDecodeError"] and ledger.load_rows(world.root) == []
    assert path.read_text(encoding="utf8") == '{"read_da' and world.status()["cohort_start"] is None
    path.write_bytes(good)
    world.run(M2)
    assert [(r["week"], r["cohort_week"]) for r in ledger.load_rows(world.root)] == [(M1, 1), (M2, 2)]


def test_stale_btc_is_a_reason_and_the_book_holds(tmp_path, monkeypatch) -> None:
    T = hour_of(M0)
    w = make_world(tmp_path, monkeypatch, exchange_mk=blank(market(), "BTC", T - 10, T))
    out = w.run(M0)
    d = w.decision(M0)
    assert list(out.reasons) == ["NO_KEPT_SELECTOR", "DATA_STALE_OR_GAP"] and d["held"] and not d["selected"]
    assert d["health"]["stale"] == 1 and d["health"]["gaps"] == 0 and d["books"] == {str(p): None for p in range(4)}
    assert decide.load_cohort(w.root) is None and w.status()["cohort_start"] is None


def test_a_stale_listed_name_is_a_reason(tmp_path, monkeypatch) -> None:
    T = hour_of(M0)
    w = make_world(tmp_path, monkeypatch, exchange_mk=blank(market(), "QQQ", T - 7, T))
    w.run(M0)
    assert "QQQ" in listed_at(w, M0) and w.decision(M0)["reasons"] == ["NO_KEPT_SELECTOR", "DATA_STALE_OR_GAP"] and w.decision(M0)["health"]["stale"] == 1


def test_six_hours_of_silence_is_not_stale(tmp_path, monkeypatch) -> None:
    T = hour_of(M0)
    w = make_world(tmp_path, monkeypatch, exchange_mk=blank(market(), "QQQ", T - 6, T))
    assert list(w.run(M0).reasons) == GOOD


def test_a_gap_in_the_last_week_of_a_listed_name_is_a_reason(tmp_path, monkeypatch) -> None:
    T = hour_of(M0)
    w = make_world(tmp_path, monkeypatch, exchange_mk=blank(market(), "QQQ", T - 40, T - 30))
    out = w.run(M0)
    d = w.decision(M0)
    assert "QQQ" in listed_at(w, M0) and list(out.reasons) == ["NO_KEPT_SELECTOR", "DATA_STALE_OR_GAP"]
    assert d["health"]["gaps"] == 1 and d["health"]["stale"] == 0 and d["held"]


def test_a_gap_of_a_coin_off_the_list_is_no_reason(tmp_path, monkeypatch) -> None:
    T = hour_of(M0)
    w = make_world(tmp_path, monkeypatch, exchange_mk=blank(market(), "EEE", T - 40, T - 30))
    w.run(M0)
    assert "EEE" not in listed_at(w, M0) and w.decision(M0)["reasons"] == GOOD


def test_too_few_names_is_list_incomplete_and_no_list_is_stored(world) -> None:
    world.exchange.listed = ("BTC", "AAA", "CCC", "DDD", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "MMM", "PPP", "QQQ", "RRR")
    out = world.run(M0)
    d = world.decision(M0)
    assert "LIST_INCOMPLETE" in out.reasons and d["held"] and d["list"] == [] and d["list_monday"] == FROZEN_LAST
    assert M0 not in universe.load_lists(world.root)


def symbols_failing(names):
    return lambda n, path, q: (500, b"") if q.get("symbol") in names and q.get("interval") == "1d" else None


def test_more_than_five_percent_of_pairs_failing_is_list_incomplete(world) -> None:
    world.exchange.hook = symbols_failing({"AAAUSDT", "CCCUSDT"})          # 2 of 20, 10%
    out = world.run(M0)
    d = world.decision(M0)
    assert "LIST_INCOMPLETE" in out.reasons and d["health"]["daily_failed"] == 2 and d["held"]
    assert M0 not in universe.load_lists(world.root)


def test_up_to_five_percent_of_pairs_failing_is_still_a_list(world) -> None:
    world.exchange.hook = symbols_failing({"AAAUSDT"})                     # 1 of 20, 5%
    out = world.run(M0)
    assert list(out.reasons) == GOOD and world.decision(M0)["health"]["daily_failed"] == 1 and len(listed_at(world, M0)) == 18


def test_a_late_run_says_so_still_selects_and_starts_no_cohort(world) -> None:
    out = world.run(M0, now=on_time(M0, 361))
    d = world.decision(M0)
    assert list(out.reasons) == ["NO_KEPT_SELECTOR", "LATE_RUN"] and d["selected"] and d["books"]["3"]["since"] == M0
    assert decide.load_cohort(world.root) is None
    world.run(M1)
    assert decide.load_cohort(world.root)["start"] == M1


def test_six_hours_after_the_stamp_is_on_time(world) -> None:
    assert list(world.run(M0, now=on_time(M0, 360)).reasons) == GOOD


def test_rows_before_the_cohort_stay_outside_it(world) -> None:
    world.run(M0, now=on_time(M0, 361))
    world.run(M1)
    world.run(M2)
    rows = ledger.load_rows(world.root)
    assert [r["cohort_week"] for r in rows] == [None, 1] and ledger.weeks_done(rows) == 1 and rows[0]["portfolios"]["paper"]["z"] is None


def test_a_masked_file_in_the_store_is_seal_scope_and_the_book_holds(world) -> None:
    world.run(M0)
    src = config.bars_dir(world.root) / "AAA.parquet"
    (config.bars_dir(world.root) / "ETH.parquet").write_bytes(src.read_bytes())
    out = world.run(M1)
    d = world.decision(M1)
    assert list(out.reasons) == ["NO_KEPT_SELECTOR", "SEAL_SCOPE"] and d["held"]
    assert d["books"] == world.decision(M0)["books"] and d["errors"] == ["build: SealedSymbolError"]
    assert b'"ETH"' not in (world.folder(M1) / "decision.json").read_bytes()
    assert world.target(M1)["complete"] is False and world.target(M1)["weights"] == {}


def test_a_masked_name_in_a_stored_list_is_seal_scope(world) -> None:
    world.run(M0)
    (config.lists_dir(world.root) / f"{M1}.json").write_text(json.dumps(["AAA", "SOL"]), encoding="utf8")
    out = world.run(M1)
    assert "SEAL_SCOPE" in out.reasons and world.decision(M1)["held"] and "SOL" not in world.decision(M1)["list"]


def test_a_masked_daily_file_is_seal_scope(world) -> None:
    config.daily_dir(world.root).mkdir(parents=True)
    pl.DataFrame({"day": [1]}).write_parquet(config.daily_dir(world.root) / "BNB.parquet")
    out = world.run(M0, fetching=False)
    assert "SEAL_SCOPE" in out.reasons and world.decision(M0)["held"]


def test_all_cash_slots_are_a_reason_and_the_book_takes_the_cash(world, monkeypatch) -> None:
    monkeypatch.setattr(run_weekly.sel, "select", lambda sb, day, table=None: (np.zeros(31), np.full(3, sel.CASH)))
    out = world.run(M0)
    d = world.decision(M0)
    assert list(out.reasons) == ["NO_KEPT_SELECTOR", "ALL_SLOTS_NON_POSITIVE"]
    assert d["selected"] and d["books"]["3"]["slots"] == ["cash"] * 3 and world.target(M0)["weights"] == {}


def test_negative_scores_give_all_cash_slots_through_the_real_rule(world, monkeypatch) -> None:
    real = run_weekly.sel.select
    monkeypatch.setattr(run_weekly.sel, "select", lambda sb, day, table=None: (-np.abs(real(sb, day, table)[0]), sel.picks(-np.abs(real(sb, day, table)[0]), 3)))
    assert "ALL_SLOTS_NON_POSITIVE" in world.run(M0).reasons


def test_a_week_with_no_run_expires_the_policy(world) -> None:
    world.run(M0)
    out = world.run(M2)
    assert list(out.reasons) == ["NO_KEPT_SELECTOR", "POLICY_EXPIRED"] and world.decision(M2)["selected"]


def test_a_held_week_is_a_week_with_no_valid_run(tmp_path, monkeypatch) -> None:
    T = hour_of(M1)
    w = make_world(tmp_path, monkeypatch, exchange_mk=blank(market(), "BTC", T - 10, T))
    w.run(M0)
    held = w.run(M1)
    assert "POLICY_EXPIRED" not in held.reasons and w.decision(M1)["held"]
    assert "POLICY_EXPIRED" in w.run(M2).reasons


def test_the_tripwire_is_a_reason_shows_on_the_page_and_the_book_keeps_writing(world) -> None:
    out = fire_the_tripwire(world)
    assert "BOOK_TRIPWIRE" in out.reasons and world.status()["tripwire"] == "FIRED" and world.status()["weeks_done"] == 8
    assert world.decision(MONDAYS[8])["selected"]


def test_the_tripwire_is_not_armed_before_week_8(world) -> None:
    world.run(M0)
    world.run(M1)
    assert world.status()["tripwire"] == "NOT_ARMED" and world.status()["weeks_done"] == 1


def test_a_blocked_monday_keeps_the_book_it_was_due_to_change(tmp_path, monkeypatch) -> None:
    due_again = MONDAYS[4]
    assert config.phase_of(due_again) == config.phase_of(M0) == 3
    T = hour_of(due_again)
    w = make_world(tmp_path, monkeypatch, exchange_mk=blank(market(), "BTC", T - 10, T))
    w.run(M0)
    before = w.decision(M0)["books"]["3"]
    out = w.run(due_again)
    d = w.decision(due_again)
    assert "DATA_STALE_OR_GAP" in out.reasons and d["held"] and not d["selected"]
    assert d["books"]["3"] == before and d["books"]["3"]["since"] == M0
    assert d["computed_slots"] is not None and w.target(due_again)["books"]["3"]["since"] == M0


@pytest.mark.parametrize("break_store", ["list_short", "seal"])
def test_every_blocking_reason_keeps_the_old_list(world, break_store: str) -> None:
    world.run(M0)
    before = world.decision(M0)["books"]["3"]
    due_again = MONDAYS[4]
    if break_store == "list_short":
        world.exchange.listed = ("BTC", "AAA", "CCC")
    else:
        (config.lists_dir(world.root) / f"{due_again}.json").write_text('["XRP"]', encoding="utf8")
    world.run(due_again)
    d = world.decision(due_again)
    assert d["held"] and d["books"]["3"] == before


def test_the_next_book_selects_as_usual_after_a_blocked_monday(tmp_path, monkeypatch) -> None:
    T = hour_of(M1)
    w = make_world(tmp_path, monkeypatch, exchange_mk=blank(market(), "BTC", T - 10, T))
    w.run(M0)
    w.run(M1)
    w.run(M2)
    d = w.decision(M2)
    assert d["selected"] and d["books"]["1"]["since"] == M2 and d["books"]["0"] is None and d["books"]["3"]["since"] == M0


def test_each_monday_exactly_one_book_moves(world) -> None:
    seen = {str(p): None for p in range(4)}
    for monday in MONDAYS[:6]:
        world.run(monday)
        d = world.decision(monday)
        moved = [p for p in seen if d["books"][p] != seen[p]]
        assert moved == [str(config.phase_of(monday))] and d["due_book"] == int(moved[0])
        seen = d["books"]


def test_best_so_far_reads_its_own_score_table(world) -> None:
    world.run(M0)
    s = lib.build_series(lib.Data(pn.load_panel(M0, world.root), live=True))
    sb = sel.score_book(s.net, s.t)
    day = config.ts(M0) // 86400
    book = world.decision(M0)["books"][str(config.phase_of(M0))]
    assert book["best_so_far"] == sel.slot_names(sel.select(sb, day, sb.best)[1]) and book["slots"] == sel.slot_names(sel.select(sb, day)[1])
    assert book["best_so_far"] != book["slots"]


def test_a_finished_week_closes_on_the_next_monday(world) -> None:
    world.run(M0)
    assert ledger.load_rows(world.root) == []
    world.run(M1)
    world.run(M2)
    rows = ledger.load_rows(world.root)
    assert [(r["week"], r["stamps"], r["cohort_week"]) for r in rows] == [(M1, 41, 1), (M2, 42, 2)]
    assert rows[0]["first_stamp"] == f"{M0}T00:00:00Z" and world.status()["weeks_done"] == 2


def test_rows_of_a_chain_equal_a_plain_rebuild_of_the_store(world) -> None:
    """The window of each run against one build of the final store, by hand and not through ledger.py: the same weights, returns and cost, stamp for stamp."""
    chain = MONDAYS[:6]
    for monday in chain:
        world.run(monday)
    d = lib.Data(pn.load_panel(chain[-1], world.root), live=True)
    t = d.t.copy()
    W: dict[str, np.ndarray] = {}
    s = lib.build_series(d, on_weights=lambda name, w: W.__setitem__(name, w.copy()))
    events = decide.events_of(decide.load_history(world.root))
    paper = np.zeros((len(t), len(d.coins)))
    for phase in range(4):
        own = [e for e in events if e.phase == phase]
        for i, e in enumerate(own):
            a = np.searchsorted(t, config.ts(e.monday))
            b = np.searchsorted(t, config.ts(own[i + 1].monday)) if i + 1 < len(own) else len(t)
            for name in e.slots:
                paper[a:b] += W[name][a:b] / 3 / 4
    n = len(s.R)
    w = paper[:n]
    gross, late = (w * s.R).sum(1), (w * s.R_late).sum(1)
    carried = w[:-1] * (1 + s.R_late[:-1]) / (1 + late[:-1])[:, None]
    cost = np.zeros(n)
    cost[1:] = (np.abs(w[1:] - carried) * (s.c[1:n] + 10)).sum(1) / 2e4
    rows = ledger.load_rows(world.root)
    assert [r["week"] for r in rows] == list(chain[1:])
    for r in rows:
        first, end = ledger.week_span(r["week"])
        keep = (t[:n] >= max(first, config.ts(M0))) & (t[:n] < end)
        q = r["portfolios"]["paper"]
        assert keep.sum() == r["stamps"]
        assert q["gross"]["stamp_open"] == pytest.approx(np.prod(1 + gross[keep]) - 1, abs=1e-12)
        assert q["gross"]["open_1h_later"] == pytest.approx(np.prod(1 + late[keep]) - 1, abs=1e-12)
        assert q["net"]["open_1h_later"]["tier_plus_10"]["drift"] == pytest.approx(np.prod(1 + (late - cost)[keep]) - 1, abs=1e-12)
        assert q["exposure"] == pytest.approx(np.abs(w[keep]).sum(1).mean(), abs=1e-12)


def test_a_failed_fetch_writes_no_row_and_the_next_run_catches_up(world) -> None:
    world.run(M0)
    world.exchange.hook = lambda n, path, q: (500, b"") if path == fetch.KLINES else None
    out = world.run(M1)
    assert "DATA_STALE_OR_GAP" in out.reasons and ledger.load_rows(world.root) == []
    world.exchange.hook = None
    world.run(M2)
    rows = ledger.load_rows(world.root)
    assert [r["week"] for r in rows] == [M1, M2] and [r["stamps"] for r in rows] == [41, 42]
    assert all(abs(r["portfolios"]["hold_btc"]["gross"]["stamp_open"]) > 1e-6 for r in rows)


def test_a_coin_we_failed_to_fetch_holds_the_week_back_and_says_so(world) -> None:
    world.run(M0)
    held = world.decision(M0)["list"][0]
    symbol = f"{universe.exchange_base(held)}USDT"
    world.exchange.hook = lambda n, path, q: (500, b"") if q.get("symbol") == symbol and q.get("interval") == "1h" else None
    world.run(M1)
    assert ledger.load_rows(world.root) == []
    assert world.decision(M1)["health"]["weeks_waiting"] == 1 and world.status()["data_health"]["weeks_waiting"] == 1
    assert decide.read_json(world.folder(M1) / "manifest.json")["fetch"]["hourly_failed"] == [held]
    world.exchange.hook = None
    world.run(M2)
    assert len(ledger.load_rows(world.root)) == 2 and world.status()["data_health"]["weeks_waiting"] == 0


def lagging(world, hours: int | None):
    """The mirror behind by some hours on every hourly bar, or None: every page empty. It answers 200 either way."""
    real = world.exchange.klines
    cut = hour_ms(hour_of(M1) - hours) if hours else 0

    def klines(q):
        status, body = real(q)
        if status == 200 and q["interval"] == "1h":
            return 200, json.dumps([r for r in json.loads(body) if r[0] < cut]).encode()
        return status, body

    world.exchange.klines = klines
    return real


@pytest.mark.parametrize("hours", [3, 5, 10, 30, None])
def test_a_mirror_that_lags_freezes_no_row(world, tmp_path, monkeypatch, hours: int | None) -> None:
    """A status of 200 with bars missing is no failure and no stop. The coin still trades, so the week waits, and the row that comes later is the row a clean run wrote."""
    world.run(M0)
    real = lagging(world, hours)
    world.run(M1)
    assert ledger.load_rows(world.root) == []
    world.exchange.klines = real
    world.run(M2)
    rows = ledger.load_rows(world.root)
    assert [r["week"] for r in rows] == [M1, M2]
    clean = fresh(world, tmp_path, monkeypatch)
    for monday in (M0, M1):
        clean.run(monday)
    want = ledger.load_rows(clean.root)[0]["portfolios"]
    for name in ledger.PORTFOLIOS:
        assert list(numbers_of(rows[0]["portfolios"][name])) == pytest.approx(list(numbers_of(want[name])), abs=1e-12), name


def test_a_listed_coin_with_no_bars_holds_its_week_back(world, tmp_path, monkeypatch) -> None:
    """On a list and new to the book, so it has no weight: stale, out of Mem, and the week would close on a universe without it for good."""
    others = ["AAA", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "MMM", "QQQ", "RRR", "FLAT", "GAPS", "HOLE", "LATE", "LUNA2"]
    on, off = others[:17] + ["PPP"], others
    plan = {M0: off, M1: off, M2: on, M3: on, M4: on}
    monkeypatch.setattr(universe, "rule_list", lambda daily, monday, n=18: plan[monday.isoformat()])
    world.run(M0)
    world.run(M1)
    world.exchange.hook = lambda n, path, q: (500, b"") if q.get("symbol") == "PPPUSDT" and q.get("interval") == "1h" else None
    world.run(M2)
    world.run(M3)
    assert [r["week"] for r in ledger.load_rows(world.root)] == [M1, M2]        # PPP is on the list in force from M2
    world.exchange.hook = None
    world.run(M4)
    rows = ledger.load_rows(world.root)
    clean = fresh(world, tmp_path, monkeypatch)
    for monday in (M0, M1, M2, M3, M4):
        clean.run(monday)
    want = ledger.load_rows(clean.root)
    assert [r["week"] for r in rows] == [r["week"] for r in want] == [M1, M2, M3, M4]
    for a, b in zip(rows, want):
        assert list(numbers_of(a["portfolios"]["EW"])) == pytest.approx(list(numbers_of(b["portfolios"]["EW"])), abs=1e-12), a["week"]


def test_a_bar_priced_zero_fails_its_coin_and_never_enters_the_store(world) -> None:
    world.run(M0)
    coin = world.decision(M0)["list"][0]
    symbol = f"{universe.exchange_base(coin)}USDT"
    bad = hour_ms(hour_of(M0) + 20)
    real = world.exchange.klines

    def klines(q):
        status, body = real(q)
        if status == 200 and q["interval"] == "1h" and q["symbol"] == symbol:
            rows = json.loads(body)
            for r in rows:
                if r[0] == bad:
                    r[1] = "0.00000000"
            return 200, json.dumps(rows).encode()
        return status, body

    world.exchange.klines = klines
    out = world.run(M1)
    d = world.decision(M1)
    assert "DATA_STALE_OR_GAP" in out.reasons and d["health"]["hourly_failed"] == 1 and d["errors"] == []
    assert (pl.read_parquet(config.bars_dir(world.root) / f"{coin}.parquet")["o"] > 0).all()
    world.exchange.klines = real
    assert not config.HOLD_REASONS & set(world.run(M2).reasons) and len(ledger.load_rows(world.root)) == 2 and world.decision(M2)["errors"] == []


def test_a_coin_the_exchange_stopped_mid_week_lets_the_week_close(tmp_path, monkeypatch) -> None:
    w = make_world(tmp_path, monkeypatch)
    w.run(M0)
    gone = w.decision(M0)["list"][0]
    w.exchange.mk = blank(market(), gone, hour_of(M0) + 30, market().opens.shape[0])
    out = w.run(M1)
    assert [r["week"] for r in ledger.load_rows(w.root)] == [M1]
    assert gone not in listed_at(w, M1) and "DATA_STALE_OR_GAP" not in out.reasons


@pytest.mark.parametrize("fail_at", [3, 30])
def test_a_rate_limit_in_the_middle_of_a_run_still_writes_a_record(world, fail_at: int) -> None:
    world.exchange.hook = lambda n, path, q: (429, b"") if n == fail_at else None
    out = world.run(M0)
    d = world.decision(M0)
    assert len(world.exchange.calls) == fail_at and d["held"] and d["errors"] == ["fetch: status 429"]
    assert "DATA_STALE_OR_GAP" in out.reasons and (world.folder(M0) / "manifest.json").exists() and world.status()["data_health"]["errors"] == 1


def test_a_rate_limit_in_the_daily_step_leaves_no_list(world) -> None:
    world.exchange.hook = lambda n, path, q: (418, b"") if n == 3 else None
    out = world.run(M0)
    assert "LIST_INCOMPLETE" in out.reasons and world.decision(M0)["health"]["daily_failed"] == 19 and M0 not in universe.load_lists(world.root)


def test_a_rate_limit_in_the_hourly_step_keeps_the_list_and_flags_the_bars(world) -> None:
    pairs = 21          # exchangeInfo and the 20 daily fetches go through
    world.exchange.hook = lambda n, path, q: (429, b"") if n == pairs + 2 else None
    out = world.run(M0)
    d = world.decision(M0)
    assert list(out.reasons) == ["NO_KEPT_SELECTOR", "DATA_STALE_OR_GAP"] and len(d["list"]) == 18
    assert d["health"]["hourly_failed"] > 10 and d["health"]["daily_failed"] == 0


def test_a_dead_network_stops_fast_and_still_writes_a_record(world) -> None:
    def dead(url, timeout):
        dead.calls += 1
        raise OSError("network down")

    dead.calls = 0
    out = world.run(M0, exchange=dead)
    d = world.decision(M0)
    assert dead.calls == (len(fetch.OUTAGE_WAITS) + 1 + fetch.MAX_FAILED_IN_A_ROW) * fetch.ATTEMPTS
    assert d["held"] and "LIST_INCOMPLETE" in out.reasons and "DATA_STALE_OR_GAP" in out.reasons
    assert d["errors"] == ["fetch: exchangeInfo: FetchError", "fetch: too many failures in a row"]


def test_no_fetch_runs_on_the_store_as_it_is(world) -> None:
    out = world.run(M0, fetching=False)
    assert world.exchange.calls == [] and "DATA_STALE_OR_GAP" in out.reasons and "LIST_INCOMPLETE" in out.reasons


def test_a_build_that_crashes_leaves_a_record_and_holds_the_book(world, monkeypatch) -> None:
    world.run(M0)

    def crash(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(run_weekly.lib, "build_series", crash)
    out = world.run(M1)
    d = world.decision(M1)
    assert d["held"] and d["errors"] == ["build: RuntimeError"] and d["computed_slots"] is None
    assert list(out.reasons) == GOOD and world.target(M1)["complete"] is False
    assert world.status()["held"] is True and world.status()["data_health"]["errors"] == 1
    assert ledger.load_rows(world.root) == []


def test_a_blip_at_the_start_of_a_run_costs_nothing(world) -> None:
    """Dead for a minute from the first call, then fine: the Monday is not burned for an outage it could wait out."""
    clock = [0.0]

    def transport(url, timeout):
        if clock[0] < 60.0:
            raise OSError("network down")
        return world.exchange(url, timeout)

    client = fetch.Client(transport=transport, pace=0.0, sleep=lambda s: clock.__setitem__(0, clock[0] + s))
    out = run_weekly.run(M0, fetching=True, client=client, now=on_time(M0))
    assert list(out.reasons) == GOOD and world.decision(M0)["selected"] and decide.load_cohort(world.root)["start"] == M0


def test_a_missing_seed_writes_a_record_and_fetches_nothing(world, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("SELECTION_SEED", str(tmp_path / "nowhere"))
    out = world.run(M0)
    d = world.decision(M0)
    assert world.exchange.calls == [] and d["errors"] == ["seed: FileNotFoundError", "build: FileNotFoundError"]
    assert list(out.reasons) == ["NO_KEPT_SELECTOR", "DATA_STALE_OR_GAP", "LIST_INCOMPLETE"] and d["held"]
    assert d["health"]["readable"] is False


def test_a_seed_that_died_before_its_last_file_is_done_again(world) -> None:
    """tier_cost.json goes last, so a store without it is a seed that did not finish."""
    pn.seed_store(world.seed, world.root)
    config.tier_path(world.root).unlink()
    assert list(world.run(M0).reasons) == GOOD and config.tier_path(world.root).exists()


def test_a_new_root_seeds_itself_before_any_fetch(world) -> None:
    world.run(M0)
    aaa = pl.read_parquet(config.bars_dir(world.root) / "AAA.parquet")
    assert aaa["hour"].to_list() == list(range(hour_of(M0)))
    assert int(world.exchange.kline_calls("AAAUSDT")[0]["startTime"]) == hour_ms(hour_of(SEED_END))


def test_the_first_run_backfills_the_rule_lists_as_runs_in_turn_would_have(world, tmp_path, monkeypatch) -> None:
    world.run(M2)
    got = universe.load_lists(world.root)
    assert decide.read_json(world.folder(M2) / "manifest.json")["lists_added"] == 3
    clean = fresh(world, tmp_path, monkeypatch)
    for monday in (M0, M1, M2):
        clean.run(monday)
    assert got == universe.load_lists(clean.root) and len(got[M0]) == 18


def test_a_pair_that_stopped_trading_is_not_ranked_even_with_bars_in_the_store(world) -> None:
    """exchangeInfo decides who may be ranked: bars left in the store from before do not."""
    world.run(M0)
    days = [date.fromisoformat(M1) - timedelta(days=k) for k in range(100, 0, -1)]
    pn.append_daily("ZZZ", days, [1.0 + k % 5 for k in range(100)], [1e15] * 100, M1, world.root)
    world.run(M1)
    assert "ZZZ" not in listed_at(world, M1) and len(listed_at(world, M1)) == 18


def test_without_a_fetch_the_pairs_are_those_in_the_store(world) -> None:
    world.run(M0)
    days = [date.fromisoformat(M1) - timedelta(days=k) for k in range(100, 0, -1)]
    pn.append_daily("ZZZ", days, [1.0 + k % 5 for k in range(100)], [1e15] * 100, M1, world.root)
    assert run_weekly.update_lists(M1, world.root, fetch.Report(), decide.load_history(world.root)) == 0      # a list needs 18 names
    assert M1 not in universe.load_lists(world.root)


def test_a_monday_with_a_finished_run_of_its_own_is_not_backfilled(world) -> None:
    world.exchange.hook = symbols_failing({"AAAUSDT", "CCCUSDT"})
    world.run(M0)
    world.exchange.hook = None
    world.run(M1)
    lists = universe.load_lists(world.root)
    assert M0 not in lists and M1 in lists


def rule_mondays(world) -> list[str]:
    return [k for k in universe.load_lists(world.root) if k >= config.RULE_FIRST_WEEK]


@pytest.mark.parametrize("failure", ["exchange_info", "dead_network", "no_seed"])
def test_a_first_run_that_failed_costs_its_own_monday_and_no_other(world, tmp_path, monkeypatch, failure: str) -> None:
    """The first run is the one that backfills, and the one most likely to fail. Its record stands, but the Mondays before it had no run: a good run fills them, and Mem does not stay on the frozen list for 90 days."""
    if failure == "exchange_info":
        world.exchange.hook = lambda n, path, q: (500, b"") if path == fetch.EXCHANGE_INFO else None
    elif failure == "dead_network":
        world.exchange.hook = lambda n, path, q: (503, b"")
    else:
        monkeypatch.setenv("SELECTION_SEED", str(tmp_path / "nowhere"))
    out = world.run(M3)
    assert "LIST_INCOMPLETE" in out.reasons and world.decision(M3)["held"] and rule_mondays(world) == []
    world.exchange.hook = None
    monkeypatch.setenv("SELECTION_SEED", str(world.seed))
    out = world.run(M4)
    assert list(out.reasons) == ["NO_KEPT_SELECTOR", "POLICY_EXPIRED"] and rule_mondays(world) == [M0, M1, M2, M4]
    assert world.decision(M4)["selected"] and decide.read_json(world.folder(M4) / "manifest.json")["lists_added"] == 4
    got = universe.load_lists(world.root)
    clean = fresh(world, tmp_path, monkeypatch)
    clean.run(M3)
    clean.run(M4)
    want = universe.load_lists(clean.root)
    assert {k: v for k, v in want.items() if k != M3} == got


def test_the_monday_a_failed_run_cost_leaves_the_scores_once_it_is_out_of_their_reach(tmp_path, monkeypatch) -> None:
    """The list of that Monday is the only thing lost. It differs from its neighbours here, so the scores feel it at first, and they stop feeling it when its week and the longest hold after it are out of the 90 days."""
    others = ["AAA", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "MMM", "QQQ", "RRR", "FLAT", "GAPS", "HOLE", "LATE", "LUNA2"]
    monkeypatch.setattr(universe, "rule_list", lambda daily, monday, n=18: [*others[:17], "PPP"] if monday.isoformat() == M3 else others)
    later = (date.fromisoformat(M3) + timedelta(weeks=16)).isoformat()
    mk = market("2021-01-18")
    world = make_world(tmp_path, monkeypatch, seed_mk=seed_market(mk), exchange_mk=mk)
    world.exchange.hook = lambda n, path, q: (503, b"")
    world.run(M3)
    world.exchange.hook = None
    world.run(M4)
    world.run(later)
    clean = fresh(world, tmp_path, monkeypatch)
    for monday in (M3, M4, later):
        clean.run(monday)
    assert M3 not in universe.load_lists(world.root) and M3 in universe.load_lists(clean.root)
    assert world.decision(M4)["scores_sha256"] != clean.decision(M4)["scores_sha256"]
    assert world.decision(later)["scores_sha256"] == clean.decision(later)["scores_sha256"]


def test_a_second_failed_run_costs_only_its_own_monday_too(world) -> None:
    world.exchange.hook = lambda n, path, q: (503, b"")
    world.run(M1)
    world.run(M2)
    world.exchange.hook = None
    world.run(M3)
    assert rule_mondays(world) == [M0, M3]


def test_a_monday_no_run_reached_is_backfilled(world) -> None:
    world.run(M0)
    world.run(M2)
    lists = universe.load_lists(world.root)
    assert M1 in lists and decide.read_json(world.folder(M2) / "manifest.json")["lists_added"] == 2


def test_a_coin_that_leaves_and_comes_back_fills_what_it_missed(world, monkeypatch) -> None:
    """The list is scripted. PPP is on it, then off it for three runs: it stops being asked for once no open week reaches its list, and comes back with no hole."""
    monkeypatch.setattr(run_weekly, "REFRESH_WEEKS", 0)
    others = ["AAA", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "MMM", "QQQ", "RRR", "FLAT", "GAPS", "HOLE", "LATE", "LUNA2"]
    on, off = others[:17] + ["PPP"], others
    plan = {M0: on, M1: off, M2: off, M3: off, M4: on}
    monkeypatch.setattr(universe, "rule_list", lambda daily, monday, n=18: plan[monday.isoformat()])
    for monday in (M0, M1, M2):
        world.run(monday)
    assert pl.read_parquet(config.bars_dir(world.root) / "PPP.parquet")["hour"].max() == hour_of(M2) - 1
    world.exchange.calls.clear()
    world.run(M3)
    assert world.exchange.kline_calls("PPPUSDT") == []
    world.exchange.calls.clear()
    out = world.run(M4)
    back = world.exchange.kline_calls("PPPUSDT")
    assert len(back) == 1 and int(back[0]["startTime"]) == hour_ms(hour_of(M2)) and list(out.reasons) == GOOD
    f = pl.read_parquet(config.bars_dir(world.root) / "PPP.parquet")
    mk = market()
    assert f["hour"].to_list() == list(range(hour_of(M4)))
    assert (f["c"].to_numpy() == mk.closes[:hour_of(M4), mk.coins.index("PPP")]).all()


def test_a_new_coin_fetches_its_whole_history_and_enters_the_run(tmp_path, monkeypatch) -> None:
    mk = market()
    w = make_world(tmp_path, monkeypatch, seed_mk=seed_market(without(mk, "OFFL")))
    names = ["AAA", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "MMM", "PPP", "QQQ", "RRR", "FLAT", "HOLE", "LATE", "OFFL"]
    monkeypatch.setattr(universe, "rule_list", lambda daily, monday, n=18: names)
    assert not (config.bars_dir(w.root) / "OFFL.parquet").exists()
    out = w.run(M0)
    calls = w.exchange.kline_calls("OFFLUSDT")
    T = hour_of(M0)
    assert len(calls) == T // 1000 + 1 and int(calls[0]["startTime"]) == config.ts(config.T0) * 1000
    f = pl.read_parquet(config.bars_dir(w.root) / "OFFL.parquet")
    assert f["hour"].to_list() == list(range(T)) and w.decision(M0)["health"]["new_coins"] == 1 and list(out.reasons) == GOOD


def test_only_closed_bars_enter_a_run(world) -> None:
    world.exchange.ignore_end = True
    world.run(M0)
    for p in config.bars_dir(world.root).glob("*.parquet"):
        assert pl.read_parquet(p)["hour"].max() < hour_of(M0), p.name
    for p in config.daily_dir(world.root).glob("*.parquet"):
        assert (pl.read_parquet(p)["day"].max() + 1) * 86400 <= config.ts(M0), p.name


def test_a_changed_future_bar_moves_no_byte_of_a_finished_monday(tmp_path, monkeypatch) -> None:
    """Truncation of the whole run: the Monday reads nothing from the exchange at or after its cutoff."""
    w = make_world(tmp_path, monkeypatch)
    w.exchange.ignore_end = True
    w.run(M0)
    a = w.files()
    mk = market()
    T = hour_of(M0)
    Op, C = mk.opens.copy(), mk.closes.copy()
    Op[T:] *= 3.0
    C[T:] *= .2
    other = tmp_path / "other"
    monkeypatch.setenv("SELECTION_ROOT", str(other))
    w2 = World(other, w.seed, FakeExchange(replace(mk, opens=Op, closes=C), ignore_end=True))
    w2.run(M0)
    b = w2.files()
    assert sorted(a) == sorted(b) and [k for k in a if a[k] != b[k]] == []


WRITES: list[str] = []
ARMED = [False]


def _audit(event: str, args: tuple) -> None:
    if not ARMED[0]:
        return
    if event == "open":
        path, mode, flags = args
        if isinstance(path, (str, os.PathLike)) and ((mode and set(mode) & set("wax+")) or (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT))):
            WRITES.append(str(path))
    elif event in ("os.rename", "os.mkdir", "os.remove", "os.rmdir", "os.link", "os.symlink", "shutil.move"):
        WRITES.append(str(args[1] if event == "os.rename" else args[0]))


sys.addaudithook(_audit)


def test_nothing_is_written_outside_the_selection_root(tmp_path, monkeypatch) -> None:
    w = make_world(tmp_path, monkeypatch)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "keep.txt").write_text("x", encoding="utf8")
    monkeypatch.chdir(cwd)

    def tree(path: Path) -> dict[str, bytes]:
        return {str(p): p.read_bytes() for p in sorted(path.rglob("*")) if p.is_file() and w.root not in p.parents}

    watched = [config.REPO / "data" / "promotion_store", config.REPO / "data" / "state"]
    other = {str(d): sorted(str(p) for p in d.rglob("*")) for d in watched if d.exists()}
    before = tree(tmp_path)
    WRITES.clear()
    ARMED[0] = True
    try:
        w.run(M0)
        w.run(M1)
    finally:
        ARMED[0] = False
    assert tree(tmp_path) == before
    assert other == {str(d): sorted(str(p) for p in d.rglob("*")) for d in watched if d.exists()}
    outside = sorted({p for p in WRITES if not p.startswith(str(w.root)) and "__pycache__" not in p})
    assert outside == [] and len(WRITES) > 20


def test_the_registry_sits_under_the_root_and_holds_only_drafts(world) -> None:
    world.run(M0)
    files = sorted(p.name for p in config.registry_dir(world.root).iterdir())
    pid = json.loads((world.folder(M0) / "policy_draft.json").read_text(encoding="utf8"))["policy_id"]
    assert files == sorted([f"{pid}.json", "index.json"])
    assert json.loads((config.registry_dir(world.root) / f"{pid}.json").read_text(encoding="utf8"))["status"] == "draft"


BANNED = re.compile(r"return|sharpe|pnl|p&l|profit|loss|gross|net|cost|equity|drawdown|score|bps|exposure|weight|^z$|^k$|edge|alpha|slot", re.I)


def keys_of(x):
    if isinstance(x, dict):
        for k, v in x.items():
            yield k
            yield from keys_of(v)
    elif isinstance(x, list):
        for v in x:
            yield from keys_of(v)


def numbers_of(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from numbers_of(v)
    elif isinstance(x, list):
        for v in x:
            yield from numbers_of(v)
    elif isinstance(x, (int, float)) and not isinstance(x, bool):
        yield x


def test_status_has_no_return_field_of_any_kind(world) -> None:
    for monday in MONDAYS[:4]:
        world.run(monday)
    s = world.status()
    assert not [k for k in keys_of(s) if BANNED.search(k)]
    assert all(isinstance(x, int) for x in numbers_of(s))
    assert set(s) == {"as_of", "phase", "due_book", "decision", "main_reason", "reasons", "held", "list", "list_monday", "data_health", "tripwire", "next_run", "cohort_start", "read_date", "weeks_done", "rs_blocks", "lv_blocks"}


def test_no_candidate_name_or_slot_reaches_the_page(world) -> None:
    world.run(M0)
    text = (world.root / "status.json").read_text(encoding="utf8")
    assert not [n for n in lib.NAMES if n in text]


def test_status_says_what_the_page_needs(world) -> None:
    world.run(M0)
    world.run(M1)
    s = world.status()
    assert s["as_of"] == M1 and s["phase"] == s["due_book"] == 0 and s["decision"] == "NO_TRADE"
    assert s["reasons"] == GOOD and s["list"] == listed_at(world, M1) and s["next_run"] == M2
    assert s["tripwire"] == "NOT_ARMED" and s["weeks_done"] == 1 and s["data_health"]["stale"] == 0


def test_the_lock_refuses_a_second_run(world) -> None:
    world.root.mkdir(parents=True)
    with open(world.root / ".lock", "w", encoding="utf8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(run_weekly.Refused):
            world.run(M0)
    assert not config.runs_dir(world.root).exists()


@pytest.mark.parametrize("day", ["2020-09-01", "2020-07-06", "2020-09-07T12:00", "monday", ""])
def test_a_day_that_is_no_monday_of_the_grid_is_refused(world, day: str) -> None:
    with pytest.raises(run_weekly.Refused):
        world.run(day, now=on_time(MONDAYS[-1]))
    assert not world.root.exists()


def test_a_monday_still_to_come_is_refused(world) -> None:
    with pytest.raises(run_weekly.Refused):
        world.run(M1, now=on_time(M0))
    assert not world.root.exists()


def test_the_last_monday_at_00_utc_is_the_default_day() -> None:
    assert run_weekly.last_monday(datetime(2026, 11, 1, 12, tzinfo=UTC)) == "2026-10-26"
    assert run_weekly.last_monday(datetime(2026, 11, 2, 0, 0, tzinfo=UTC)) == "2026-11-02"
    assert run_weekly.last_monday(datetime(2026, 11, 8, 23, 59, tzinfo=UTC)) == "2026-11-02"


def test_hourly_coins_reach_back_fourteen_weeks_and_to_the_oldest_open_week() -> None:
    lists = {"2026-07-20": ["OLD"], "2026-07-27": ["AAA"], "2026-11-02": ["CCC"], "2026-11-09": ["FUT"]}
    assert run_weekly.hourly_coins(lists, "2026-11-02", []) == ["AAA", "BTC", "CCC"]
    assert run_weekly.hourly_coins(lists, "2026-11-02", ["2026-08-03"]) == ["AAA", "BTC", "CCC", "OLD"]


def test_hourly_coins_ask_for_every_coin_an_open_week_waits_on() -> None:
    """A Monday with no list leaves the last list in force, however old."""
    lists = {"2026-07-20": ["OLD"], "2026-10-26": ["AAA"]}
    assert "OLD" not in run_weekly.hourly_coins(lists, "2026-11-02", [])
    assert "OLD" in run_weekly.hourly_coins(lists, "2026-11-02", ["2026-08-17"])


def test_cli_writes_a_record_and_exits_zero(world, capsys) -> None:
    assert run_weekly.main(["--as-of", M0, "--no-fetch"]) == 0
    assert (world.folder(M0) / "manifest.json").exists() and "record written" in capsys.readouterr().out
    assert run_weekly.main(["--as-of", M0, "--no-fetch"]) == 0
    assert "nothing changed" in capsys.readouterr().out


def test_cli_seed_only_is_refused_while_a_run_holds_the_root(world, capsys) -> None:
    world.root.mkdir(parents=True)
    with open(world.root / ".lock", "w", encoding="utf8") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert run_weekly.main(["--seed-only"]) == 2
    assert "another run" in capsys.readouterr().err and not config.tier_path(world.root).exists()


@pytest.mark.parametrize("args", [["--as-of", M0, "--no-fetch"], ["--seed-only"]])
def test_cli_refuses_a_root_in_a_runner_store_and_writes_nothing(world, tmp_path, monkeypatch, capsys, args: list[str]) -> None:
    bad = tmp_path / "data" / "execution"
    monkeypatch.setenv("SELECTION_ROOT", str(bad))
    assert run_weekly.main(args) == 2
    assert "runner store" in capsys.readouterr().err and not bad.exists()


def test_cli_refuses_a_day_that_is_not_a_monday_and_writes_nothing(world, capsys) -> None:
    assert run_weekly.main(["--as-of", "2020-09-01", "--no-fetch"]) == 2
    assert "refused" in capsys.readouterr().err and not world.root.exists()


def test_cli_seed_only_stops_after_the_seed(world) -> None:
    assert run_weekly.main(["--seed-only"]) == 0
    assert config.tier_path(world.root).exists() and not config.runs_dir(world.root).exists()


def test_cli_defaults_to_the_last_monday(world, monkeypatch) -> None:
    monkeypatch.setattr(run_weekly, "_now", lambda: on_time(M2) + timedelta(days=2))
    assert run_weekly.main(["--no-fetch"]) == 0
    assert world.decision(M2)["as_of"] == M2 and "LATE_RUN" in world.decision(M2)["reasons"]


STDLIB = sys.stdlib_module_names
PACKAGE = Path(config.__file__).parent


def test_the_package_imports_nothing_that_can_place_an_order() -> None:
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                names = [a.name for a in n.names]
            elif isinstance(n, ast.ImportFrom):
                names = [n.module or ""]
            else:
                continue
            for name in names:
                root = name.split(".")[0]
                assert root in STDLIB or root in {"numpy", "polars", "scipy", "tools"} or name == "trading_agent.research.selection_policy", (path.name, name)


FORBIDDEN = re.compile(r"X-MBX|apikey|api_key|api-key|secret|hmac|signature|/order|/account|ccxt|dotenv|\.env\b|testnet\.binance|api\.binance\.com", re.I)


def test_no_module_of_the_run_knows_a_key_or_a_private_endpoint() -> None:
    for name in ("fetch", "decide", "ledger", "policy", "status", "run_weekly"):
        text = (PACKAGE / f"{name}.py").read_text(encoding="utf8")
        assert not FORBIDDEN.findall(text), name


def test_the_run_never_reaches_for_the_promotion_store_or_the_runner_state() -> None:
    for name in ("fetch", "decide", "ledger", "status", "run_weekly"):
        text = (PACKAGE / f"{name}.py").read_text(encoding="utf8")
        assert "promotion_store" not in text and "data/state" not in text, name
