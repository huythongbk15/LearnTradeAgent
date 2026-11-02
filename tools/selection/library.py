"""Candidate library of the selection run: the 32 frozen candidates of selector_20261101_01 as weights w(s) on the 4 hour stamps.

A port of the study's lib.py with no change of behaviour: same candidates, same names and order, same stale, hole, stop, tie and float rules. acceptance.py proves it cell for cell. Data comes from a panel, not from paths. On a masked panel the same rules run on the masked universe, which is a new variant, not the study's book.

Clock: a weight at stamp s reads only hourly bars closed before hour h(s). The position runs from the open at s to the open at s+1.
Sum of |w| is at most 1 in every candidate. Fractions, not bps, except cost, which is bps a round trip.
Returns follow one frozen rule of the protocol: a stopped coin leaves at its last close.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import polars as pl
from numpy.lib.stride_tricks import sliding_window_view as swv
from scipy.stats import rankdata

from tools.selection import config
from tools.selection.config import BTC, hour_of, ts
from tools.selection.panel import Panel
from tools.selection.universe import mem_matrix

STALE_H = 6                   # age above this at h-1 means no weight, the rule inside Mem
HOLE_H = 1000                 # a hole above this is treated like a stop
MINN = 10                     # fewer ranked coins than this give no quintile
MISSING_COST = 37.0           # round trip bps of a coin with no cost
CHUNK_COLS = 32               # columns of a running sum held at once
DIRECTIONAL = ("ma4h", "ma1d", "don", "rsi", "bb")
SPREADS = (                   # name, feature, stamps a hold, sign: +1 long Q5 short Q1, -1 long Q1 short Q5
    ("dhi30_3d", "dhi30", 18, 1), ("dhi30_7d", "dhi30", 42, 1), ("mom7_3d", "mom7", 18, 1), ("mom30_7d", "mom30", 42, 1),
    ("rev1_1d", "mom1", 6, -1), ("rv30_7d", "rv30", 42, -1), ("vtr7_3d", "vtr7", 18, 1),
)
MARKET = ("hold_btc", "hold_list", "alt_btc", "btc_alt", "cash")
NAMES = [f"B_{r}_{f}" for r in DIRECTIONAL for f in ("LS", "LF")] + [f"L_{r}_{f}" for r in DIRECTIONAL for f in ("LS", "LF")] + [s[0] for s in SPREADS] + list(MARKET)
if len(NAMES) != 32:
    raise ValueError("the library has 32 candidates")
WINDOWS = (("W1", "2020-07-13", "2022-01-01"), ("W2", "2022-01-01", "2023-01-01"), ("W3", "2023-01-01", "2024-07-01"), ("W4", "2024-07-01", "2025-07-01"))


def quint(A: np.ndarray, minn: int = MINN) -> np.ndarray:
    """Frozen quintile rule of the study's core.py, row by row. -1 where a value is missing or the row has too few."""
    R = rankdata(A, axis=1, nan_policy="omit")
    n = (~np.isnan(A)).sum(1, keepdims=True)
    q = np.floor(5 * (R - 0.5) / np.maximum(n, 1))
    q[np.isnan(A)] = -1
    q[(n < minn).ravel()] = -1
    return q.astype(np.int8)


class Data:
    """Bars before end and the stamps of a run.

    With live False the stamps are those whose return can be read, as the study: h + 4 inside the panel. With live True the stamps go on to the one at the cutoff itself, so a weight exists for the Monday being decided; Sr counts the stamps that have a return, S all of them, and every weight array is S rows.
    """

    def __init__(self, panel: Panel, end=None, start: str = config.CANDIDATE_START, live: bool = False):
        T = panel.opens.shape[0] if end is None else min(hour_of(end), panel.opens.shape[0])
        self.T = T
        self.h = np.arange(hour_of(start), (T if live else T - 5) + 1, 4, dtype=np.int64)
        self.t = ts(config.T0) + self.h * 3600
        self.S = len(self.h)
        self.Sr = int((self.h + 4 < T).sum())
        self.coins = panel.coins
        self.N = len(panel.coins)
        self.btc = panel.coins.index(BTC)
        self.O, self.C, self.Q = panel.opens[:T], panel.closes[:T], panel.quotes[:T]
        P = ~np.isnan(self.C)
        hr = np.arange(T, dtype=np.int32)[:, None]
        self.ff = np.maximum.accumulate(np.where(P, hr, -1), axis=0).astype(np.int32)     # last traded hour up to the row
        self.nx = np.minimum.accumulate(np.where(P, hr, T)[::-1], axis=0)[::-1].astype(np.int32)   # next traded hour from the row, used for returns only
        has = self.ff >= 0
        age = np.where(has, hr - self.ff, 1 << 30).astype(np.int32)
        col = np.arange(self.N)[None, :]
        Cf = self.C[self.ff, col]
        Cf[~has] = np.nan
        Cf[age > HOLE_H] = np.nan
        self.Cf = Cf
        self.first = np.where(P.any(0), P.argmax(0), T)
        self.age_at = age[self.h - 1]
        self.stale = self.age_at > STALE_H
        self.Mem = mem_matrix(panel.lists, panel.coins, self.t, self.stale)
        if self.Mem[:, self.btc].any():
            raise ValueError("BTC is on a list")
        c = _replay_cost(self, panel.replay_cost) if panel.replay_cost is not None else tier_cost(self, panel.tier)
        self.cost = np.where(np.isnan(c), MISSING_COST, c).astype(np.float64)

    def release(self) -> None:
        """Drop what only the returns read. The rules read Cf and Q."""
        self.O = self.C = self.nx = self.ff = None


def _replay_cost(d: Data, replay: dict[str, np.ndarray]) -> np.ndarray:
    """The study's stamp cost at the stamps of this run."""
    pos = np.searchsorted(replay["t"], d.t)
    if pos.max() >= len(replay["t"]) or (replay["t"][pos] != d.t).any():
        raise ValueError("the replay cost does not cover these stamps")
    return replay["cost"][pos]


def tier_cost(d: Data, tier: dict[str, float]) -> np.ndarray:
    """Round trip bps per stamp and coin, seed/stamps.py.txt: the coin's tier, else by liquidity, else NaN for the 37 fill. liq is log10 of 24 times the rolling median hourly quote volume, 720 hours, at least 240, read at h-1, NaN when stale."""
    cost = np.full((d.S, d.N), np.nan, np.float32)
    need = []
    for j, c in enumerate(d.coins):
        t_ = tier.get("LUNA" if c == "LUNA2" else c)
        if t_ is not None:
            cost[:, j] = t_
        else:
            need.append(j)
    if need:
        qm = pl.DataFrame({str(j): d.Q[:, j] for j in need}).fill_nan(None).select(pl.all().rolling_median(720, min_samples=240)).to_numpy().astype(float)
        with np.errstate(all="ignore"):
            liq = np.log10(24 * qm)
        liq[~np.isfinite(liq)] = np.nan
        lq = np.asarray(liq[d.h - 1], np.float32).copy()
        lq[d.stale[:, need]] = np.nan
        cost[:, need] = np.where(np.isnan(lq), 37.0, np.where(lq >= 7, 20.0, np.where(lq >= np.log10(3e6), 31.0, 37.0)))
    return cost


def _by_chunk(d: Data, fn) -> np.ndarray:
    """Build an S by N feature a block of columns at a time. Running sums go down a column, so the floats do not depend on the block, and a weekly run stays under its memory limit."""
    out = np.empty((d.S, d.N))
    for a in range(0, d.N, CHUNK_COLS):
        c = np.arange(a, min(a + CHUNK_COLS, d.N))
        out[:, c] = fn(c)
    return out


def _rv30(L: np.ndarray, idx: np.ndarray) -> np.ndarray:
    r1 = np.full(L.shape, np.nan)
    r1[1:] = L[1:] - L[:-1]
    ok = ~np.isnan(r1)
    r0 = np.where(ok, r1, 0.0)
    c1 = np.cumsum(r0, 0)
    c2 = np.cumsum(r0 * r0, 0)
    cn = np.cumsum(ok.astype(np.int32), 0)
    w = 720
    s1 = c1[idx] - c1[idx - w]
    s2 = c2[idx] - c2[idx - w]
    n = cn[idx] - cn[idx - w]
    v = np.sqrt(np.maximum(s2 / w - (s1 / w) ** 2, 0))
    v[~(n >= w)] = np.nan
    return v


def _vtr7(Q: np.ndarray, idx: np.ndarray, first: np.ndarray) -> np.ndarray:
    Qok = ~np.isnan(Q)
    cq = np.cumsum(np.where(Qok, Q, 0.0), 0)
    cc = np.cumsum(Qok.astype(np.int32), 0)
    w, w4 = 168, 672
    nS = cc[idx] - cc[idx - w]
    nL = cc[idx] - cc[idx - w4]
    with np.errstate(all="ignore"):
        mS = (cq[idx] - cq[idx - w]) / nS
        mL = (cq[idx] - cq[idx - w4]) / nL
        v = np.log(mS / mL)
    v[~np.isfinite(v)] = np.nan
    v[~((nS >= np.ceil(.75 * w)) & (nL >= np.ceil(.75 * w4)) & (idx[:, None] - w4 >= first[None, :]))] = np.nan
    return v


def features(d: Data, leak: int = 0, need=("dhi30", "mom1", "mom7", "mom30", "rv30", "vtr7"), dhi_rows=None) -> dict[str, np.ndarray]:
    """Features of the spreads, S by N, NaN where stale, each at bars closed before the stamp. leak=1 plants a next hour leak, for the truncation test. dhi_rows: stamps to compute dhi at, default all."""
    idx = d.h - 1 + leak
    out = {}
    L = np.log(d.Cf)
    for k, w in (("mom1", 24), ("mom7", 168), ("mom30", 720)):
        if k in need:
            out[k] = L[idx] - L[idx - w]
    if "rv30" in need:
        out["rv30"] = _by_chunk(d, lambda c: _rv30(L[:, c], idx))
    if "vtr7" in need:
        out["vtr7"] = _by_chunk(d, lambda c: _vtr7(d.Q[:, c], idx, d.first[c]))
    if "dhi30" in need:
        rows = range(d.S) if dhi_rows is None else dhi_rows
        v = np.full((d.S, d.N), np.nan)
        for s in rows:
            i = idx[s]
            v[s] = L[i] - np.log(d.Cf[i - 719:i + 1].max(0))       # NaN in the window gives NaN, as rolling_max with a full window
        out["dhi30"] = v
    for k in out:
        out[k][d.stale] = np.nan
    return out


def closes(d: Data, bh: int, leak: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Close of each bar on the UTC grid (hour 0 of the matrix is 00:00 UTC) and, per stamp, the index of the last closed bar. A bar of bh hours closes at row bh*k-1."""
    rows = np.arange(bh - 1, d.T, bh)
    return d.Cf[rows], np.minimum(d.h // bh - 1 + leak, len(rows) - 1)


def roll(X: np.ndarray, n: int, fn) -> np.ndarray:
    """fn over a trailing window of n bars, NaN inside the window gives NaN."""
    out = np.full(X.shape, np.nan)
    v = swv(X, n, axis=0)
    for a in range(0, len(v), 1000):
        out[n - 1 + a:n - 1 + a + 1000] = fn(v[a:a + 1000], axis=-1)
    return out


def shift1(X: np.ndarray) -> np.ndarray:
    out = np.full(X.shape, np.nan)
    out[1:] = X[:-1]
    return out


def sign_state(F: np.ndarray, S_: np.ndarray) -> np.ndarray:
    """+1 fast above slow, -1 below. Equal within rounding is 0: a window of equal closes, a coin after its last bar, must not take a sign from float noise."""
    with np.errstate(invalid="ignore"):
        x = np.where(np.abs(F - S_) <= 1e-12 * np.abs(S_), 0.0, F - S_)
    return np.nan_to_num(np.sign(x)).astype(np.int8)


def state_don(X: np.ndarray) -> np.ndarray:
    """+1 from a close above the highest of the 20 closes before it, until a close below the lowest of the 10 closes before it; mirror for -1. A bar that ends one state may open the other."""
    hi20 = shift1(roll(X, 20, np.max))
    lo20 = shift1(roll(X, 20, np.min))
    hi10 = shift1(roll(X, 10, np.max))
    lo10 = shift1(roll(X, 10, np.min))
    st = np.zeros(X.shape[1], np.int8)
    out = np.zeros(X.shape, np.int8)
    for b in range(X.shape[0]):
        x = X[b]
        with np.errstate(invalid="ignore"):
            st[(st == 1) & (x < lo10[b])] = 0
            st[(st == -1) & (x > hi10[b])] = 0
            st[(st == 0) & (x > hi20[b])] = 1
            st[(st == 0) & (x < lo20[b])] = -1
        st[np.isnan(x) | np.isnan(hi20[b])] = 0
        out[b] = st
    return out


def rsi_wilder(X: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder RSI, first value the simple mean of n changes. No move at all reads 50. A missing close restarts the count."""
    B, K = X.shape
    out = np.full((B, K), np.nan)
    ag = np.zeros(K)
    al = np.zeros(K)
    cnt = np.zeros(K, np.int64)
    for b in range(1, B):
        d_ = X[b] - X[b - 1]
        ok = ~np.isnan(d_)
        up = np.where(ok, np.maximum(d_, 0), 0.0)
        dn = np.where(ok, np.maximum(-d_, 0), 0.0)
        cnt = np.where(ok, cnt + 1, 0)
        ag = np.where(cnt == 0, 0.0, np.where(cnt <= n, ag + up, (ag * (n - 1) + up) / n))
        al = np.where(cnt == 0, 0.0, np.where(cnt <= n, al + dn, (al * (n - 1) + dn) / n))
        at = cnt == n
        ag = np.where(at, ag / n, ag)
        al = np.where(at, al / n, al)
        full = cnt >= n
        den = ag + al
        with np.errstate(all="ignore"):
            r = np.where(den > 0, 100 * ag / den, 50.0)
        out[b, full] = r[full]
    return out


def state_rsi(R: np.ndarray) -> np.ndarray:
    """RSI below 30 is +1 until above 50, above 70 is -1 until below 50."""
    st = np.zeros(R.shape[1], np.int8)
    out = np.zeros(R.shape, np.int8)
    for b in range(R.shape[0]):
        x = R[b]
        with np.errstate(invalid="ignore"):
            st[(st == 1) & (x > 50)] = 0
            st[(st == -1) & (x < 50)] = 0
            st[(st == 0) & (x < 30)] = 1
            st[(st == 0) & (x > 70)] = -1
        st[np.isnan(x)] = 0
        out[b] = st
    return out


def state_bb(X: np.ndarray, n: int = 20, k: float = 2.0) -> np.ndarray:
    """Close under the lower band is +1 until back to the middle line, mirror for -1. Population standard deviation."""
    m = roll(X, n, np.mean)
    s_ = roll(X, n, np.std)
    flat = s_ <= 1e-12 * np.abs(m)            # no width, no band: float noise must not open a state on equal closes
    up = np.where(flat, np.inf, m + k * s_)
    lo = np.where(flat, -np.inf, m - k * s_)
    tol = 1e-12 * np.abs(m)                   # closes sit on a tick grid, so a close exactly on a line is a real tie: on the middle line it ends the state, on a band it does not open one
    st = np.zeros(X.shape[1], np.int8)
    out = np.zeros(X.shape, np.int8)
    for b in range(X.shape[0]):
        x = X[b]
        with np.errstate(invalid="ignore"):
            st[(st == 1) & (x >= m[b] - tol[b])] = 0
            st[(st == -1) & (x <= m[b] + tol[b])] = 0
            st[(st == 0) & (x < lo[b] - tol[b])] = 1
            st[(st == 0) & (x > up[b] + tol[b])] = -1
        st[np.isnan(x) | np.isnan(m[b]) | flat[b]] = 0        # equal closes sit on the middle line
        out[b] = st
    return out


def rule_states(d: Data, leak: int = 0) -> dict[str, np.ndarray]:
    """Signal per rule, S by N int8, from the full history of every coin, on or off the list. leak=1 plants a next bar leak in every rule."""
    X4, i4 = closes(d, 4, leak)
    Xd, id_ = closes(d, 24, leak)
    return {
        "ma4h": sign_state(roll(X4, 20, np.mean), roll(X4, 50, np.mean))[i4],
        "ma1d": sign_state(roll(Xd, 20, np.mean), roll(Xd, 100, np.mean))[id_],
        "don": state_don(Xd)[id_],
        "rsi": state_rsi(rsi_wilder(X4))[i4],
        "bb": state_bb(X4)[i4],
    }


def spread_weights(d: Data, F: np.ndarray, k: int, sign: int, phase: int = 0) -> np.ndarray:
    """Lists change every k stamps from phase. 0.5 a leg, equal inside a leg. A leg left empty by ties leaves the round flat. A coin that went stale inside a hold carries no weight from there."""
    w = np.zeros((d.S, d.N))
    r = np.arange(phase, d.S, k)
    X = F[r].copy()
    X[~d.Mem[r]] = np.nan
    q = quint(X)
    hi, lo = q == 4, q == 0
    lg, sh = (hi, lo) if sign > 0 else (lo, hi)
    nl = lg.sum(1)
    ns = sh.sum(1)
    for i, s in enumerate(r):
        if nl[i] and ns[i]:
            w[s:s + k] = 0.5 * lg[i] / nl[i] - 0.5 * sh[i] / ns[i]
    w[d.stale] = 0.0
    return w


def candidates(d: Data, leak: int = 0, only=None):
    """Yield (name, w) in the frozen order, w S by N float64, built one at a time. only: names to build."""
    want = (lambda n: True) if only is None else (lambda n: n in only)
    n = np.maximum(d.Mem.sum(1), 1).astype(np.float64)
    M = d.Mem / n[:, None]
    if any(want(x) for x in NAMES[:20]):
        sig = rule_states(d, leak)
        for r in DIRECTIONAL:
            for f in ("LS", "LF"):
                x = sig[r] if f == "LS" else np.maximum(sig[r], 0)
                if want(f"B_{r}_{f}"):
                    w = np.zeros((d.S, d.N))
                    w[:, d.btc] = x[:, d.btc]
                    yield f"B_{r}_{f}", w
        for r in DIRECTIONAL:
            for f in ("LS", "LF"):
                x = sig[r] if f == "LS" else np.maximum(sig[r], 0)
                if want(f"L_{r}_{f}"):
                    yield f"L_{r}_{f}", x * M
        del sig
    if any(want(x[0]) for x in SPREADS):
        rows = sorted({s for k in (18, 42) for s in range(0, d.S, k)})
        F = features(d, leak, dhi_rows=rows)
        for name, fe, k, sg in SPREADS:
            if want(name):
                yield name, spread_weights(d, F[fe], k, sg)
        del F
    for name in MARKET:
        if want(name):
            w = np.zeros((d.S, d.N))
            if name == "hold_btc":
                w[:, d.btc] = 1.0
            elif name == "hold_list":
                w = M.copy()
            elif name == "alt_btc":
                w = 0.5 * M
                w[:, d.btc] = -0.5
            elif name == "btc_alt":
                w = -0.5 * M
                w[:, d.btc] = 0.5
            yield name, w


def stamp_returns(d: Data, exit_at: str = "close", lag: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """Return of a coin from the open at s to the open at s+1, one row per stamp whose return can be read, no clip. Open means the first traded hour at or after the stamp, so a short gap is held through and the chain of stamps compounds to the open to open return of the whole span.

    A coin with no bar from the next stamp on, or with a hole above 1000 hours next, has no open to exit at: it leaves at its last close (exit_at='open' gives the last open). Nothing traded after the stamp means 0. lag=1 is the late fill clock: the open at h+1 to the open at h+5, same stop rule.
    """
    T = d.T
    n = int((d.h + lag + 4 < T).sum())
    h = d.h[:n] + lag
    col = np.arange(d.N)[None, :]
    tg = h + 4
    i0 = d.nx[h]
    i1 = d.nx[tg]
    lb = d.ff[tg]
    stop = (i1 >= T) | (i1 - tg[:, None] > HOLE_H)
    P0 = d.O[np.minimum(i0, T - 1), col]
    P1 = d.O[np.minimum(i1, T - 1), col]
    Pe = (d.C if exit_at == "close" else d.O)[np.maximum(lb, 0), col]
    with np.errstate(all="ignore"):
        r = np.where(stop, Pe, P1) / P0 - 1
    r[(i0 >= T) | (stop & (i0 > lb))] = 0.0
    r[np.isnan(r)] = 0.0
    return r, stop


def evaluate(w: np.ndarray, R: np.ndarray, c: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Gross and net per stamp, cost on |w(s)-w(s-1)| times c/2, turnover sum of |dw|. The series starts flat."""
    gross = (w * R).sum(1)
    dw = np.abs(np.diff(w, axis=0, prepend=np.zeros((1, w.shape[1]))))
    cost = (dw * c).sum(1) / 2e4
    return gross, gross - cost, cost, dw.sum(1)


def cost_on_weights(w: np.ndarray, c: np.ndarray) -> np.ndarray:
    """Cost per stamp as a fraction for any weight path w, S by N, for example a portfolio: this is the rule, on real |dw|."""
    dw = np.abs(np.diff(np.asarray(w, np.float64), axis=0, prepend=np.zeros((1, w.shape[1]))))
    return (dw * c).sum(1) / 2e4


@dataclass
class Series:
    """What a run keeps of the library: one row a stamp with a return, except c, one row a stamp of the run.

    gross, net, cost, turn, expo, nexp, gross_open are Sr by 32. R and R_late are the coin returns on the two fill clocks, one row a stamp whose return can be read. recent holds, per candidate, the weights from stamp keep_from on, live stamps included.
    """

    t: np.ndarray
    h: np.ndarray
    gross: np.ndarray
    net: np.ndarray
    cost: np.ndarray
    turn: np.ndarray
    expo: np.ndarray
    nexp: np.ndarray
    gross_open: np.ndarray
    R: np.ndarray
    R_late: np.ndarray
    c: np.ndarray
    keep_from: int | None = None
    recent: dict[str, np.ndarray] = field(default_factory=dict)


def build_series(d: Data, keep_from: int | None = None, on_weights=None) -> Series:
    """Build the candidates one at a time and keep the series, so memory stays flat if the caller holds no reference to the panel. keep_from: first stamp whose weights are kept in Series.recent. on_weights(name, w) sees each full S by N block."""
    R, _ = stamp_returns(d, "close")
    R_o, _ = stamp_returns(d, "open")
    R_late, _ = stamp_returns(d, "close", lag=1)
    d.release()
    S, K = d.Sr, len(NAMES)
    c = d.cost[:S]
    gross = np.zeros((S, K))
    net, cost, turn, expo, nexp, gross_open = (gross.copy() for _ in range(6))
    recent = {}
    seen = []
    for name, w in candidates(d):
        i = len(seen)
        if name != NAMES[i]:
            raise ValueError(f"candidate order: {name} at {i}")
        seen.append(name)
        if np.abs(w).sum(1).max() > 1 + 1e-9:
            raise ValueError(f"{name}: sum of |w| above 1")
        wr = w[:S]
        gross[:, i], net[:, i], cost[:, i], turn[:, i] = evaluate(wr, R, c)
        expo[:, i] = np.abs(wr).sum(1)
        nexp[:, i] = wr.sum(1)
        gross_open[:, i] = (wr * R_o).sum(1)
        if on_weights is not None:
            on_weights(name, w)
        if keep_from is not None:
            recent[name] = w[keep_from:].copy()
    return Series(d.t[:S], d.h[:S], gross, net, cost, turn, expo, nexp, gross_open, R, R_late, d.cost, keep_from, recent)
