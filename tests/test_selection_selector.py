"""The selector against plain Python loops, and truncation with planted leaks for weights, scores and lists. Synthetic market only."""
from __future__ import annotations

import hashlib
from dataclasses import replace

import numpy as np
import pytest

from tests.test_selection_fixture import market, panel_of
from tools.selection import config
from tools.selection import library as lib
from tools.selection import selector as sel
from tools.selection.config import hour_of

CAD, K, LOOK = config.PRIMARY_CADENCE_WEEKS, config.PRIMARY_K, config.PRIMARY_LOOK


def build(panel):
    d = lib.Data(panel)
    W = np.zeros((32, d.S, d.N))

    def sink(name, w):
        W[lib.NAMES.index(name)] = w

    return lib.build_series(d, on_weights=sink), W, d


@pytest.fixture(scope="module")
def run():
    s, W, d = build(panel_of(market()))
    bk = sel.Book(s.t, s.gross, s.cost, s.c, W)
    return s, W, d, bk, sel.score_book(s.net, s.t)


def test_block_is_six_stamps_from_20_to_16(run) -> None:
    s, _, _, _, _ = run
    x = s.net[:, :3]
    lab, D = sel.blocks(x, s.t)
    secs = s.t % 86400
    first = int(np.flatnonzero(secs == 72000)[0])          # the first 20:00
    assert s.t[first] // 86400 + 1 == lab[0]
    for i, label in enumerate(lab):
        a = first + 6 * i
        assert int(s.t[a] % 86400) == 72000 and (s.t[a] + 14400) // 86400 == label
        assert int(s.t[a + 5] % 86400) == 57600
        assert D[i] == pytest.approx(np.prod(1 + x[a:a + 6], axis=0) - 1, abs=1e-14)
    assert len(lab) == (s.t[-1] - s.t[first] + 14400) // 86400


def test_score_table_against_a_plain_loop(run) -> None:
    s, _, _, _, sb = run
    lab, D = sel.blocks(s.net[:, :31], s.t)
    Sc = sel.score_table(D, LOOK)
    assert (sb.primary[LOOK:] == Sc[LOOK:]).all() and np.isnan(Sc[:LOOK]).all() and Sc.shape[0] == D.shape[0] + 1
    for i in (LOOK, LOOK + 7, Sc.shape[0] - 1):
        for c in range(31):
            rets = D[i - LOOK:i, c].tolist()
            m = sum(rets) / LOOK
            sd = (sum((x - m) ** 2 for x in rets) / (LOOK - 1)) ** .5
            assert Sc[i, c] == pytest.approx(m / sd if sd > 1e-12 else 0.0, abs=1e-12)


def test_score_of_a_constant_series_is_zero() -> None:
    assert (sel.score_table(np.zeros((95, 2)), 90)[90:] == 0).all()
    x = np.full((95, 1), .01)
    assert (sel.score_table(x, 90)[90:] == 0).all()


def test_score_expanding_against_a_plain_loop(run) -> None:
    s, _, _, _, sb = run
    _, D = sel.blocks(s.net[:, :31], s.t)
    Sc = sb.best
    assert np.isnan(Sc[0]).all() and (Sc[1] == 0).all()
    for i in (2, 30, D.shape[0]):
        for c in (0, 12, 30):
            rets = D[:i, c].tolist()
            m = sum(rets) / i
            sd = (sum((x - m) ** 2 for x in rets) / (i - 1)) ** .5
            assert Sc[i, c] == pytest.approx(m / sd if sd > 1e-12 else 0.0, rel=1e-9, abs=1e-12)


def test_picks_break_ties_by_frozen_order_and_cash_a_non_positive_slot() -> None:
    row = np.zeros(31)
    row[[4, 9, 2]] = (.5, .5, .5)
    assert sel.picks(row, 3).tolist() == [2, 4, 9]
    row = np.zeros(31)
    row[7] = .1
    assert sel.picks(row, 3).tolist() == [7, sel.CASH, sel.CASH]
    assert sel.picks(-np.ones(31), 3).tolist() == [sel.CASH] * 3
    with pytest.raises(ValueError):
        sel.picks(np.full(31, np.nan), 3)
    assert sel.slot_names(np.array([7, sel.CASH, 0])) == [lib.NAMES[7], "cash", lib.NAMES[0]]


def test_picks_against_a_plain_sort() -> None:
    rng = np.random.default_rng(9)
    rows = np.round(rng.normal(size=(200, 31)), 1)
    got = sel.picks(rows, 3)
    for r, g in zip(rows, got):
        order = sorted(range(31), key=lambda i: (-r[i], i))[:3]
        assert g.tolist() == [i if r[i] > 0 else sel.CASH for i in order]


def test_first_selection_reads_exactly_90_blocks(run) -> None:
    s, _, _, bk, sb = run
    days = sel.sel_days(bk)
    assert days[0] == config.ts(config.FIRST_GRID_DAY) // 86400 and (np.diff(days) == 28).all()
    assert np.isfinite(sb.rows(days[:1])).all()
    assert np.isnan(sb.primary[days[0] - sb.lab0 - 1]).all()


def test_books_cover_every_monday_once(run) -> None:
    _, _, _, bk, _ = run
    all_days = np.concatenate([sel.sel_days(bk, phase=p) for p in range(CAD)])
    assert len(set(all_days.tolist())) == len(all_days)
    assert sorted(all_days.tolist()) == list(range(bk.D0, bk.last_day + 1, 7))
    for p in range(CAD):
        for day in sel.sel_days(bk, phase=p):
            assert config.phase_of(str(np.datetime64(int(day), "D"))) == p


def test_paths_against_a_plain_loop(run) -> None:
    s, W, _, bk, sb = run
    days, _, ch = sel.primary_lists(bk, sb)
    starts = bk.stamp_of_day(days)
    wp = sel.path(bk, ch, starts)
    g = sel.gross_path(bk, ch, starts)
    assert not wp[:bk.s0].any() and not g[:bk.s0].any()
    for t in (bk.s0, bk.s0 + 1, int(starts[1]) - 1, int(starts[1]), bk.S - 1):
        j = int(np.searchsorted(starts, t, side="right") - 1)
        want = sum(W[i, t] for i in ch[j] if i != sel.CASH) / K
        assert (wp[t] == pytest.approx(want))
        assert g[t] == pytest.approx(sum(s.gross[t, i] for i in ch[j]) / K)
        assert g[t] == pytest.approx(float((wp[t] * s.R[t]).sum()), abs=1e-14)


def test_ew_is_the_mean_of_the_32_from_the_first_selection(run) -> None:
    s, W, _, bk, _ = run
    g, c, wp = sel.ew_series(bk)
    assert not g[:bk.s0].any() and not c[:bk.s0].any() and not wp[:bk.s0].any()
    assert g[bk.s0:] == pytest.approx(s.gross[bk.s0:].mean(1))
    assert wp[bk.s0:] == pytest.approx(W[:, bk.s0:].mean(0))
    assert c == pytest.approx(lib.cost_on_weights(wp, s.c[:bk.S]))


def blocks_at(x: np.ndarray, t: np.ndarray, off: int) -> tuple[np.ndarray, np.ndarray]:
    """A block rule with its own offset. 14400 is the rule. 0 is the UTC day chain, which reads the open at the selection stamp. -14400 also reads the next stamp."""
    lab = (t + off) // 86400
    u, idx, cnt = np.unique(lab, return_index=True, return_counts=True)
    with np.errstate(invalid="ignore", divide="ignore"):        # an edit of 5 times on a short leg can pass -100% after the cut
        out = np.add.reduceat(np.log1p(x), idx, axis=0)
    keep = cnt == 6
    return u[keep], np.expm1(out[keep])


def outputs(panel, c: int, day: int, leak: int = 0) -> dict:
    """Everything a run at stamp c reads from the library, for each block rule."""
    d = lib.Data(panel)
    R, _ = lib.stamp_returns(d)
    net = np.zeros((d.Sr, 32))
    Wc = np.zeros((32, d.N))
    upto = hashlib.sha256()
    for i, (name, w) in enumerate(lib.candidates(d, leak=leak)):
        net[:, i] = lib.evaluate(w, R, d.cost)[1]
        Wc[i] = w[c]
        upto.update(w[:c + 1].tobytes())
    res = {"weights_hash": upto.hexdigest(), "w_c": Wc}
    for kind, off in (("clean", 14400), ("utc_chain", 0), ("next_stamp", -14400)):
        lab, D = blocks_at(net[:, :31], d.t, off)
        table = sel.score_table(D, LOOK)
        rows = table[np.arange(config.ts(config.FIRST_GRID_DAY) // 86400, day + 1, 28) - lab[0]]
        ch = sel.picks(rows, K)
        wp = sum(Wc[i] for i in ch[-1] if i != sel.CASH) / K
        res[kind] = dict(rows=rows, lists=ch, port=wp)
    return res


def edits(hc: int):
    rng = np.random.default_rng(hc)

    def rand(Op, C, Q):
        f = np.exp(rng.normal(0, .5, C[hc:].shape))
        C[hc:] *= f
        Op[hc:] *= f
        Q[hc:] *= rng.uniform(.01, 100, Q[hc:].shape)

    def up(Op, C, Q):
        C[hc:] *= 5
        Op[hc:] *= 5
        Q[hc:] *= 5

    def down(Op, C, Q):
        C[hc:] *= .2
        Op[hc:] *= .2
        Q[hc:] *= .2

    def remove(Op, C, Q):
        Op[hc:] = np.nan
        C[hc:] = np.nan
        Q[hc:] = np.nan

    return rand, up, down, remove


def edited(panel, fn):
    Op, C, Q = panel.opens.copy(), panel.closes.copy(), panel.quotes.copy()
    fn(Op, C, Q)
    return replace(panel, opens=Op, closes=C, quotes=Q)


@pytest.fixture(scope="module")
def truncation():
    """Cuts on Mondays of the 4 week grid, far from every planted gap, so a move can only be a leak."""
    panel = panel_of(market())
    d = lib.Data(panel)
    cuts = [int(np.searchsorted(d.h, hour_of(m))) for m in ("2020-08-10", "2020-09-07", "2020-10-05")]
    b_ma = next(w for _, w in lib.candidates(d, only=["B_ma4h_LS"]))[:, d.btc]
    flips = np.flatnonzero(np.diff(b_ma)) + 1
    cuts.append(int(flips[flips > cuts[0]][0]) - 1)       # the stamp before B_ma4h_LS flips: a leaked bar decides that rule
    res = []
    for c in cuts:
        h = int(d.h[c])
        day = int(d.t[c] // 86400)
        day -= (day + 3) % 7                               # the Monday at or before the stamp
        base = {leak: outputs(panel, c, day, leak) for leak in (0, 1)}
        moved = {leak: [outputs(edited(panel, fn), c, day, leak) for fn in edits(h)] for leak in (0, 1)}
        res.append((base, moved))
    return res


def test_truncation_moves_no_weight_at_or_before_the_stamp(truncation) -> None:
    for base, moved in truncation:
        for m in moved[0]:
            assert m["weights_hash"] == base[0]["weights_hash"]
            assert (m["w_c"] == base[0]["w_c"]).all()


def test_truncation_moves_no_score_list_or_portfolio_weight(truncation) -> None:
    for base, moved in truncation:
        for m in moved[0]:
            assert (m["clean"]["rows"] == base[0]["clean"]["rows"]).all()
            assert (m["clean"]["lists"] == base[0]["clean"]["lists"]).all()
            assert (m["clean"]["port"] == base[0]["clean"]["port"]).all()


def test_planted_next_bar_leak_moves_weights(truncation) -> None:
    per = np.zeros(32, int)
    for base, moved in truncation:
        for m in moved[1]:
            per += (m["w_c"] != base[1]["w_c"]).any(1)
    leaked = {n for n, x in zip(lib.NAMES, per) if x}
    assert "B_ma4h_LS" in leaked
    assert {f"L_{r}_LS" for r in ("ma4h", "rsi", "bb")} <= leaked
    assert {s[0] for s in lib.SPREADS if s[2] == 42} | {"rev1_1d"} <= leaked      # the spreads whose lists change on a Monday


def test_planted_block_leak_moves_scores_and_lists(truncation) -> None:
    score = {"utc_chain": 0, "next_stamp": 0}
    lists = {"utc_chain": 0, "next_stamp": 0}
    for base, moved in truncation:
        for m in moved[0]:
            for kind in score:
                score[kind] += int((m[kind]["rows"][-1] != base[0][kind]["rows"][-1]).sum())
                lists[kind] += int((m[kind]["lists"][-1] != base[0][kind]["lists"][-1]).any())
    assert score["utc_chain"] > 0 and score["next_stamp"] > 0
    assert lists["utc_chain"] > 0 and lists["next_stamp"] > 0


def test_a_cut_panel_scores_its_monday_like_the_full_run() -> None:
    mk = market()
    monday = "2020-09-07"
    day = config.ts(monday) // 86400
    s_full, _, _ = build(panel_of(mk))
    full = sel.score_book(s_full.net, s_full.t).rows([day])
    d = lib.Data(panel_of(mk, end=monday), live=True)
    s_cut = lib.build_series(d)
    assert s_cut.t[-1] == config.ts(monday) - 8 * 3600        # Sunday 16:00
    cut = sel.score_book(s_cut.net, s_cut.t).rows([day])
    assert (cut == full).all()
    assert (sel.picks(cut, K) == sel.picks(full, K)).all()


def test_select_reaches_the_monday_a_cut_build_ends_before() -> None:
    monday = "2020-09-07"
    day = config.ts(monday) // 86400
    d = lib.Data(panel_of(market(), end=monday), live=True)
    s = lib.build_series(d)
    bk = sel.Book(s.t, s.gross, s.cost, s.c)
    sb = sel.score_book(s.net, s.t)
    assert bk.last_day == day - 1 and day not in sel.sel_days(bk, phase=config.phase_of(monday)).tolist()
    row, got = sel.select(sb, day)
    assert row.shape == (31,) and got.shape == (K,)
    assert (got == sel.picks(sb.primary[-1], K)).all()


def test_a_day_outside_the_score_table_is_refused(run) -> None:
    s, _, _, _, sb = run
    with pytest.raises(ValueError, match="outside"):
        sb.rows([sb.lab0 - 1])
    with pytest.raises(ValueError, match="outside"):
        sel.select(sb, sb.lab0 + len(sb.primary))
