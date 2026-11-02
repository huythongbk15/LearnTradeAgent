"""The library against plain Python loops written differently from it: scalar, no shared helper. Synthetic market only."""
from __future__ import annotations

import hashlib
import math
import statistics
from dataclasses import replace

import numpy as np
import pytest

from tests.test_selection_fixture import END, market, panel_of
from tools.selection import config
from tools.selection import library as lib
from tools.selection.config import hour_of


def isnan(x: float) -> bool:
    return x != x


def filled(col: np.ndarray) -> list[float]:
    """Forward fill with the 1000 hour limit, one cell at a time."""
    out, last, at = [], None, None
    for i, x in enumerate(col.tolist()):
        if x == x:
            last, at = x, i
        out.append(float("nan") if last is None or i - at > 1000 else last)
    return out


def bars(cf: list[float], bh: int) -> list[float]:
    return cf[bh - 1::bh]


def ma_state(x: list[float], fast: int, slow: int) -> list[int]:
    out = []
    for i in range(len(x)):
        if i < slow - 1 or any(isnan(v) for v in x[i - slow + 1:i + 1]):
            out.append(0)
            continue
        p, q = sum(x[i - fast + 1:i + 1]) / fast, sum(x[i - slow + 1:i + 1]) / slow
        out.append(0 if abs(p - q) <= 1e-12 * abs(q) else (1 if p > q else -1))
    return out


def don_state(x: list[float]) -> list[int]:
    out, st = [], 0
    for i in range(len(x)):
        if i < 20 or isnan(x[i]) or any(isnan(v) for v in x[i - 20:i]):
            st = 0
            out.append(0)
            continue
        if (st == 1 and x[i] < min(x[i - 10:i])) or (st == -1 and x[i] > max(x[i - 10:i])):
            st = 0
        if st == 0:
            st = 1 if x[i] > max(x[i - 20:i]) else (-1 if x[i] < min(x[i - 20:i]) else 0)
        out.append(st)
    return out


def rsi_state(x: list[float], n: int = 14) -> list[int]:
    out, st, changes, ag, al = [], 0, [], None, None
    for i in range(len(x)):
        if i == 0 or isnan(x[i]) or isnan(x[i - 1]):
            changes, ag, al, st = [], None, None, 0
            out.append(0)
            continue
        ch = x[i] - x[i - 1]
        if ag is None:
            changes.append(ch)
            if len(changes) < n:
                st = 0
                out.append(0)
                continue
            ag = sum(max(v, 0) for v in changes) / n
            al = sum(max(-v, 0) for v in changes) / n
        else:
            ag = (ag * (n - 1) + max(ch, 0)) / n
            al = (al * (n - 1) + max(-ch, 0)) / n
        r = 50.0 if ag + al == 0 else 100 * ag / (ag + al)
        if (st == 1 and r > 50) or (st == -1 and r < 50):
            st = 0
        if st == 0:
            st = 1 if r < 30 else (-1 if r > 70 else 0)
        out.append(st)
    return out


def bb_state(x: list[float], n: int = 20) -> list[int]:
    out, st = [], 0
    for i in range(len(x)):
        win = x[i - n + 1:i + 1]
        if i < n - 1 or any(isnan(v) for v in win):
            st = 0
            out.append(0)
            continue
        m, sd = sum(win) / n, statistics.pstdev(win)
        tol = 1e-12 * abs(m)
        if sd <= tol:
            st = 0
            out.append(0)
            continue
        if (st == 1 and x[i] >= m - tol) or (st == -1 and x[i] <= m + tol):
            st = 0
        if st == 0:
            st = 1 if x[i] < m - 2 * sd - tol else (-1 if x[i] > m + 2 * sd + tol else 0)
        out.append(st)
    return out


@pytest.fixture(scope="module")
def built():
    p = panel_of(market())
    d = lib.Data(p)
    return p, d


def test_frozen_tables_are_the_protocol() -> None:
    assert lib.DIRECTIONAL == ("ma4h", "ma1d", "don", "rsi", "bb")
    assert lib.SPREADS == (
        ("dhi30_3d", "dhi30", 18, 1), ("dhi30_7d", "dhi30", 42, 1), ("mom7_3d", "mom7", 18, 1), ("mom30_7d", "mom30", 42, 1),
        ("rev1_1d", "mom1", 6, -1), ("rv30_7d", "rv30", 42, -1), ("vtr7_3d", "vtr7", 18, 1),
    )
    assert lib.MARKET == ("hold_btc", "hold_list", "alt_btc", "btc_alt", "cash")
    assert hashlib.sha256(",".join(lib.NAMES).encode()).hexdigest() == "eae658321a895fe447c2b277457df4995be9940ec9ca5ad702da56ba8cc55c3d"


def test_stamps_keep_the_study_grid(built) -> None:
    p, d = built
    assert d.h[0] == hour_of(config.CANDIDATE_START) and (d.h % 4 == 0).all() and (np.diff(d.h) == 4).all()
    assert d.h[-1] + 4 < d.T and d.h[-1] + 8 >= d.T and d.S == d.Sr
    assert (d.t == config.ts(config.T0) + d.h * 3600).all()


@pytest.mark.parametrize("rule", lib.DIRECTIONAL)
def test_rule_state_against_a_plain_loop(built, rule: str) -> None:
    p, d = built
    sig = lib.rule_states(d)[rule]
    moved = 0
    for j in range(d.N):
        cf = filled(p.closes[:, j])
        x4, xd = bars(cf, 4), bars(cf, 24)
        plain, ix = {
            "ma4h": (ma_state(x4, 20, 50), d.h // 4 - 1),
            "ma1d": (ma_state(xd, 20, 100), d.h // 24 - 1),
            "don": (don_state(xd), d.h // 24 - 1),
            "rsi": (rsi_state(x4), d.h // 4 - 1),
            "bb": (bb_state(x4), d.h // 4 - 1),
        }[rule]
        want = np.array([plain[i] for i in ix], np.int8)
        assert (want == sig[:, j]).all(), f"{rule} on {d.coins[j]}"
        moved += int((want != 0).sum())
    assert (sig == 1).any() and (sig == -1).any() and moved > 0


def test_flat_closes_take_no_sign(built) -> None:
    _, d = built
    j = d.coins.index("FLAT")
    s = int(np.searchsorted(d.h, 3800))          # the 50 bars of ma4h and the 20 of bb all sit inside the flat stretch
    sig = lib.rule_states(d)
    assert sig["ma4h"][s, j] == 0 and sig["bb"][s, j] == 0


def feature_plain(p, d, fe: str, s: int, j: int) -> float:
    idx = int(d.h[s]) - 1
    cf = filled(p.closes[:, j])
    if fe in ("mom1", "mom7", "mom30"):
        w = {"mom1": 24, "mom7": 168, "mom30": 720}[fe]
        return math.log(cf[idx] / cf[idx - w]) if not (isnan(cf[idx]) or isnan(cf[idx - w])) else float("nan")
    if fe == "dhi30":
        win = cf[idx - 719:idx + 1]
        return float("nan") if any(isnan(v) for v in win) else math.log(cf[idx] / max(win))
    if fe == "rv30":
        win = cf[idx - 720:idx + 1]
        if any(isnan(v) for v in win):
            return float("nan")
        return statistics.pstdev([math.log(win[q + 1] / win[q]) for q in range(720)])
    q = p.quotes[:idx + 1, j]
    a, b = q[idx - 167:idx + 1], q[idx - 671:idx + 1]
    na, nb = int((~np.isnan(a)).sum()), int((~np.isnan(b)).sum())
    if na < 126 or nb < 504 or idx - 672 < d.first[j]:
        return float("nan")
    ma, mb = float(np.nansum(a)) / na, float(np.nansum(b)) / nb
    return math.log(ma / mb) if ma > 0 and mb > 0 else float("nan")


def legs_plain(values: dict[int, float]) -> tuple[set[int], set[int]]:
    """Q5 and Q1 by hand: average ranks, the frozen floor formula, at least 10 names."""
    n = len(values)
    if n < 10:
        return set(), set()
    order = sorted(values.items(), key=lambda kv: kv[1])
    rank, p = {}, 0
    while p < n:
        e = p
        while e + 1 < n and order[e + 1][1] == order[p][1]:
            e += 1
        for q in range(p, e + 1):
            rank[order[q][0]] = (p + e) / 2 + 1
        p = e + 1
    return {j for j, r in rank.items() if math.floor(5 * (r - .5) / n) == 4}, {j for j, r in rank.items() if math.floor(5 * (r - .5) / n) == 0}


@pytest.mark.parametrize("name,fe,k,sg", lib.SPREADS)
def test_spread_lists_against_a_plain_loop(built, name: str, fe: str, k: int, sg: int) -> None:
    p, d = built
    w = lib.spread_weights(d, lib.features(d, need=(fe,))[fe], k, sg)
    rounds = 0
    for r in range(0, d.S, k):
        vals = {}
        for j in np.flatnonzero(d.Mem[r]):
            v = feature_plain(p, d, fe, r, int(j))
            if not isnan(v):
                vals[int(j)] = v
        q5, q1 = legs_plain(vals)
        lg, sh = (q5, q1) if sg > 0 else (q1, q5)
        for s in range(r, min(r + k, d.S)):
            live_l = {j for j in lg if not d.stale[s, j]}
            live_s = {j for j in sh if not d.stale[s, j]}
            if lg and sh:
                assert set(np.flatnonzero(w[s] > 0).tolist()) == live_l and set(np.flatnonzero(w[s] < 0).tolist()) == live_s
                if live_l:
                    assert w[s, sorted(live_l)] == pytest.approx(0.5 / len(lg))
            else:
                assert not w[s].any()
        rounds += bool(lg and sh)
    assert rounds > 10


def return_plain(p, h: int, j: int, T: int, exit_at: str = "close") -> float:
    """Open at the first traded hour from h, to the open at the first traded hour from h + 4, or a stop."""
    C, Op = p.closes[:, j], p.opens[:, j]
    traded = np.flatnonzero(~np.isnan(C[:T]))
    later = traded[traded >= h]
    i0 = int(later[0]) if len(later) else T
    tg = h + 4
    after = traded[traded >= tg]
    i1 = int(after[0]) if len(after) else T
    before = traded[traded <= tg]
    lb = int(before[-1]) if len(before) else -1
    stop = i1 >= T or i1 - tg > 1000
    if i0 >= T or (stop and i0 > lb):
        return 0.0
    px = (C if exit_at == "close" else Op)[lb] if stop else Op[i1]
    return float(px / Op[i0] - 1)


def test_stamp_returns_against_a_plain_loop(built) -> None:
    p, d = built
    R, _ = lib.stamp_returns(d, "close")
    Ro, _ = lib.stamp_returns(d, "open")
    for j in range(d.N):
        for s in range(0, d.S, 7):
            assert R[s, j] == pytest.approx(return_plain(p, int(d.h[s]), j, d.T), abs=1e-12)
            assert Ro[s, j] == pytest.approx(return_plain(p, int(d.h[s]), j, d.T, "open"), abs=1e-12)


def test_late_clock_runs_one_hour_later_with_the_same_stop(built) -> None:
    p, d = built
    R, _ = lib.stamp_returns(d, "close", lag=1)
    assert len(R) == d.Sr
    for j in range(d.N):
        for s in range(0, d.Sr, 11):
            assert R[s, j] == pytest.approx(return_plain(p, int(d.h[s]) + 1, j, d.T), abs=1e-12)


def test_stop_leaves_at_the_last_close_and_a_hole_stops(built) -> None:
    p, d = built
    R, stop = lib.stamp_returns(d)
    j = d.coins.index("GONE")
    s = int(np.searchsorted(d.h, 5296))       # the stamp whose next open does not exist
    assert stop[s, j] and d.h[s] < 5300 <= d.h[s] + 4
    assert R[s, j] == pytest.approx(p.closes[5299, j] / p.opens[int(d.h[s]), j] - 1)
    assert (R[s + 1:, j] == 0).all()
    j = d.coins.index("HOLE")
    s = int(np.searchsorted(d.h, 2996))
    assert stop[s, j]
    assert R[s, j] == pytest.approx(p.closes[2999, j] / p.opens[2996, j] - 1)


def test_short_gap_is_held_through(built) -> None:
    p, d = built
    R, stop = lib.stamp_returns(d)
    j = d.coins.index("GAPS")
    s = int(np.searchsorted(d.h, 3996))
    assert not stop[s, j]
    assert np.prod(1 + R[s:s + 3, j]) - 1 == pytest.approx(p.opens[4008, j] / p.opens[3996, j] - 1)


def test_a_stale_coin_carries_no_weight(built) -> None:
    _, d = built
    j = d.coins.index("GAPS")
    s = int(np.searchsorted(d.h, 4832))
    assert d.age_at[s, j] > 6 and d.stale[s, j] and not d.Mem[s, j]
    for name, w in lib.candidates(d, only=[x for x in lib.NAMES if x.startswith("L_") or x in ("dhi30_3d", "hold_list", "alt_btc")]):
        assert (w[d.stale] == 0).all(), name


def test_a_short_gap_keeps_a_coin_on_the_list(built) -> None:
    _, d = built
    j = d.coins.index("GAPS")
    s = int(np.searchsorted(d.h, 4004))
    assert d.age_at[s, j] <= 6


def blanked(p, spans: dict[str, tuple[int, int]]):
    """A copy of the panel with these coins missing over a span of rows."""
    Op, C, Q = p.opens.copy(), p.closes.copy(), p.quotes.copy()
    for coin, (a, b) in spans.items():
        j = p.coins.index(coin)
        Op[a:b, j] = C[a:b, j] = Q[a:b, j] = np.nan
    return replace(p, opens=Op, closes=C, quotes=Q)


def test_stale_starts_above_six_hours() -> None:
    p = panel_of(market())
    h = hour_of("2020-06-01")
    d = lib.Data(blanked(p, {"DDD": (h - 6, h + 12), "EEE": (h - 7, h + 12)}))
    s = int(np.searchsorted(d.h, h))
    ok, old = d.coins.index("DDD"), d.coins.index("EEE")
    assert d.age_at[s, ok] == 6 and not d.stale[s, ok] and d.Mem[s, ok]
    assert d.age_at[s, old] == 7 and d.stale[s, old] and not d.Mem[s, old]


def test_a_filled_close_lives_1000_hours() -> None:
    p = panel_of(market())
    last = hour_of("2020-04-20")
    d = lib.Data(blanked(p, {"DDD": (last + 1, last + 1100)}))
    j = d.coins.index("DDD")
    assert d.Cf[last + 1000, j] == p.closes[last, j]
    assert np.isnan(d.Cf[last + 1001, j])


def test_a_hole_stops_a_coin_above_1000_hours() -> None:
    p = panel_of(market())
    h = hour_of("2020-04-20")
    tg = h + 4
    q = blanked(p, {"DDD": (tg - 2, tg + 1000), "EEE": (tg - 2, tg + 1001)})
    d = lib.Data(q)
    R, stop = lib.stamp_returns(d)
    s = int(np.searchsorted(d.h, h))
    held, gone = d.coins.index("DDD"), d.coins.index("EEE")
    assert not stop[s, held] and R[s, held] == pytest.approx(q.opens[tg + 1000, held] / q.opens[h, held] - 1)
    assert stop[s, gone] and R[s, gone] == pytest.approx(q.closes[tg - 3, gone] / q.opens[h, gone] - 1)


@pytest.mark.parametrize("back,missing,valid", [(100, 42, True), (100, 43, False), (400, 168, True), (400, 169, False)])
def test_volume_trend_needs_three_quarters_of_each_window(back: int, missing: int, valid: bool) -> None:
    """42 missing of the 168 hours leave 126, 168 missing of the 672 leave 504: the last counts that still read."""
    p = panel_of(market())
    h = hour_of("2020-06-01")
    d = lib.Data(blanked(p, {"DDD": (h - back, h - back + missing)}))
    s = int(np.searchsorted(d.h, h))
    assert np.isfinite(lib.features(d, need=("vtr7",))["vtr7"][s, d.coins.index("DDD")]) == valid


def test_hold_btc_equals_btc_open_to_open(built) -> None:
    p, d = built
    R, _ = lib.stamp_returns(d)
    g, net, cost, turn = lib.evaluate(next(w for _, w in lib.candidates(d, only=["hold_btc"])), R, d.cost)
    b = d.btc
    a_, z_ = hour_of("2020-05-04"), hour_of("2020-10-12")
    a, z = int(np.searchsorted(d.h, a_)), int(np.searchsorted(d.h, z_))
    assert np.prod(1 + g[a:z]) - 1 == pytest.approx(p.opens[z_, b] / p.opens[a_, b] - 1, rel=1e-10)
    ok = ~np.isnan(p.opens[d.h, b]) & ~np.isnan(p.opens[d.h + 4, b])
    assert g[ok] == pytest.approx(p.opens[d.h + 4, b][ok] / p.opens[d.h, b][ok] - 1, abs=1e-14)
    assert not ok.all()                        # the BTC gap sits on a stamp
    hole = np.flatnonzero(~ok)
    span = (hole[0], hole[-1] + 1)
    assert np.prod(1 + g[span[0]:span[1]]) - 1 == pytest.approx(p.opens[d.h[span[1]], b] / p.opens[d.h[span[0]], b] - 1)
    assert cost[0] == pytest.approx(11.0 / 2e4) and (cost[1:] == 0).all()


def test_candidates_keep_their_names_order_and_exposure(built) -> None:
    _, d = built
    seen = []
    for name, w in lib.candidates(d):
        seen.append(name)
        assert w.shape == (d.S, d.N) and np.abs(w).sum(1).max() <= 1 + 1e-9
        if name.startswith("B_"):
            assert not np.delete(w, d.btc, axis=1).any()
        if name.startswith("L_") or name in ("hold_list",):
            assert not w[:, d.btc].any()
    assert seen == lib.NAMES and len(seen) == 32 and lib.NAMES[-1] == "cash"
    assert not next(w for n, w in lib.candidates(d, only=["cash"]) if n == "cash").any()


@pytest.mark.parametrize("rule", lib.DIRECTIONAL)
def test_directional_candidates_follow_their_rule(built, rule: str) -> None:
    _, d = built
    sig = lib.rule_states(d)[rule]
    n = np.maximum(d.Mem.sum(1), 1)
    got = dict(lib.candidates(d, only=[f"{u}_{rule}_{f}" for u in "BL" for f in ("LS", "LF")]))
    for s in range(0, d.S, 13):
        ls, lf = sig[s], np.maximum(sig[s], 0)
        assert got[f"B_{rule}_LS"][s, d.btc] == ls[d.btc] and got[f"B_{rule}_LF"][s, d.btc] == lf[d.btc]
        for j in range(d.N):
            on = d.Mem[s, j]
            assert got[f"L_{rule}_LS"][s, j] == (ls[j] / n[s] if on else 0)
            assert got[f"L_{rule}_LF"][s, j] == (lf[j] / n[s] if on else 0)
    assert (sig == -1).any() and (got[f"B_{rule}_LF"] >= 0).all() and (got[f"L_{rule}_LF"] >= 0).all()


def test_market_stances_mirror_each_other(built) -> None:
    _, d = built
    w = dict(lib.candidates(d, only=["alt_btc", "btc_alt", "hold_list"]))
    assert (w["alt_btc"] == -w["btc_alt"]).all()
    assert (w["alt_btc"][:, d.btc] == -.5).all()
    assert w["hold_list"].sum(1) == pytest.approx(np.where(d.Mem.sum(1) > 0, 1.0, 0.0))


def test_evaluate_against_a_plain_loop() -> None:
    rng = np.random.default_rng(3)
    w, R, c = rng.normal(size=(30, 6)), rng.normal(size=(30, 6)) / 100, rng.uniform(10, 40, (30, 6))
    g, n, cost, turn = lib.evaluate(w, R, c)
    prev = np.zeros(6)
    for s in range(30):
        dw = np.abs(w[s] - prev)
        assert g[s] == pytest.approx(float((w[s] * R[s]).sum()))
        assert cost[s] == pytest.approx(float((dw * c[s]).sum() / 2e4))
        assert turn[s] == pytest.approx(float(dw.sum()))
        prev = w[s]
    assert n == pytest.approx(g - cost)
    assert lib.cost_on_weights(w, c) == pytest.approx(cost)


def test_tier_cost_against_a_plain_loop(built) -> None:
    p, d = built
    tier = dict(p.tier)
    c = lib.tier_cost(d, tier)
    assert (c[:, d.coins.index("AAA")] == 20.0).all()
    assert (c[:, d.coins.index("LUNA2")] == 31.0).all()         # LUNA2 reads LUNA's tier
    j = d.coins.index("JJJ")
    assert "JJJ" not in tier
    q = p.quotes[:, j]
    for s in range(0, d.S, 37):
        idx = int(d.h[s]) - 1
        win = [v for v in q[max(idx - 719, 0):idx + 1].tolist() if v == v]
        want = 37.0
        if len(win) >= 240 and not d.stale[s, j]:
            liq = math.log10(24 * statistics.median(win))
            want = 20.0 if liq >= 7 else (31.0 if liq >= math.log10(3e6) else 37.0)
        assert c[s, j] == want


def test_luna2_reads_the_tier_of_luna() -> None:
    p = panel_of(market())
    d = lib.Data(p)
    assert (lib.tier_cost(d, {"LUNA": 24.0})[:, d.coins.index("LUNA2")] == 24.0).all()
    assert not (lib.tier_cost(d, {"LUNA2": 24.0})[:, d.coins.index("LUNA2")] == 24.0).all()


@pytest.mark.parametrize("day_volume,want", [(1.05e7, 20.0), (10 ** 6.95, 31.0), (3.1e6, 31.0), (2.9e6, 37.0)])
def test_liquidity_decides_the_cost_of_a_coin_with_no_tier(day_volume: float, want: float) -> None:
    p = panel_of(market())
    Q = p.quotes.copy()
    j = p.coins.index("JJJ")
    Q[:, j] = day_volume / 24
    d = lib.Data(replace(p, quotes=Q))
    c = lib.tier_cost(d, p.tier)
    assert (c[:, j] == want).all()


def test_cost_is_filled_with_37_where_nothing_is_known(built) -> None:
    p, d = built
    assert not np.isnan(d.cost).any()
    assert set(np.unique(d.cost)) <= {11.0, 13.0, 20.0, 24.0, 31.0, 37.0}
    j = d.coins.index("LATE")          # no tier, no bars at the start
    assert d.cost[0, j] == 37.0


def test_replay_cost_is_read_as_given(built) -> None:
    p, d0 = built
    fake = {"t": d0.t.copy(), "cost": np.full((d0.S, d0.N), 12.0, np.float32)}
    d = lib.Data(replace(p, replay_cost=fake))
    assert (d.cost == 12.0).all()
    with pytest.raises(ValueError, match="does not cover"):
        lib.Data(replace(p, replay_cost={"t": d0.t[:-3].copy(), "cost": fake["cost"][:-3]}))


def test_live_stamp_needs_no_later_bar() -> None:
    """A panel cut at a Monday holds a weight for that Monday, equal to the build that goes on past it, and no return for the last two stamps."""
    mk = market()
    monday = "2020-09-07"
    d_cut = lib.Data(panel_of(mk, end=monday), live=True)
    d_all = lib.Data(panel_of(mk))
    h = hour_of(monday)
    assert d_cut.h[-1] == h and d_cut.Sr == d_cut.S - 2 and d_cut.h[d_cut.Sr - 1] == h - 8
    s = int(np.searchsorted(d_all.h, h))
    assert s == d_cut.S - 1
    full = dict(lib.candidates(d_all))
    for name, w in lib.candidates(d_cut):
        assert (w == full[name][:s + 1]).all(), name
    assert (d_cut.Mem == d_all.Mem[:s + 1]).all()
    assert (d_cut.cost == d_all.cost[:s + 1]).all()


def test_series_match_one_candidate_at_a_time(built) -> None:
    p, d0 = built
    R, _ = lib.stamp_returns(d0)
    w0 = dict(lib.candidates(d0))
    d = lib.Data(p)
    s = lib.build_series(d, keep_from=d.S - 5)
    for i, name in enumerate(lib.NAMES):
        g, n, c, tr = lib.evaluate(w0[name], R, d0.cost)
        assert (s.gross[:, i] == g).all() and (s.net[:, i] == n).all() and (s.cost[:, i] == c).all() and (s.turn[:, i] == tr).all()
        assert (s.recent[name] == w0[name][-5:]).all()
    assert s.R.shape == (d.Sr, d.N) and s.R_late.shape == (d.Sr, d.N)


def test_end_argument_cuts_the_panel() -> None:
    p = panel_of(market())
    d = lib.Data(p, end="2020-09-07")
    assert d.T == hour_of("2020-09-07") and d.T < p.opens.shape[0] and d.h[-1] + 4 < d.T
    assert END > "2020-09-07"
