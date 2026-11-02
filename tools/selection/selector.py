"""Selector of the selection run: scores, picks and portfolio paths, a port of the study's seleng.py with the rules of sel_rules.py.

Clock. A selection stamp s is a Monday 00:00. A score may read only bars closed before s. The stamp return of s-1 ends at the open of hour h(s), a bar not closed before s, so a score stops at stamp s-2. A selection day is therefore a block of 6 stamps from 20:00 to 16:00, and a score at day x reads the blocks labelled x-L to x-1. A UTC day chain would read the open at s, which is a leak: keep the block.

Score: daily Sharpe, mean over sd with ddof 1 on the L blocks of candidate net. A candidate with sd at or below 1e-12 scores 0. cash scores 0 and is not ranked. Ties break by frozen order. A slot whose score is not positive becomes cash. Identical candidates in a window are not merged.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv

from tools.selection import config
from tools.selection.config import ts
from tools.selection.library import NAMES, cost_on_weights

CASH = 31
if NAMES[CASH] != "cash":
    raise ValueError("cash must close the frozen order")


def blocks(x: np.ndarray, t: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """x S by K per stamp. A block is six stamps 20:00 to 16:00, labelled by the day it ends in, so a score at day d reads labels up to d-1 and stops at stamp s-2 of the Monday stamp s. Only full blocks."""
    lab = (t + 14400) // 86400
    u, idx, cnt = np.unique(lab, return_index=True, return_counts=True)
    out = np.add.reduceat(np.log1p(x), idx, axis=0)
    keep = cnt == 6
    if not (np.diff(u[keep]) == 1).all():
        raise ValueError("a full block is missing inside the series")
    return u[keep], np.expm1(out[keep])


def score_table(D: np.ndarray, look: int, how: str = "sharpe") -> np.ndarray:
    """Row i is day lab0+i and reads blocks i-look to i-1. Rows before look are NaN, one extra row past the last label."""
    n, K = D.shape
    Sc = np.full((n + 1, K), np.nan)
    V = swv(D, look, axis=0)
    if how == "sharpe":
        m = V.mean(-1)
        s = V.std(-1, ddof=1)
        ok = s > 1e-12
        x = np.where(ok, m / np.where(ok, s, 1.0), 0.0)
    elif how == "return":
        x = np.expm1(np.log1p(V).sum(-1))
    else:
        raise ValueError(how)
    Sc[look:] = x
    return Sc


def score_expanding(D: np.ndarray) -> np.ndarray:
    """BEST_SO_FAR: Sharpe on all blocks before the day."""
    n, K = D.shape
    Sc = np.full((n + 1, K), np.nan)
    c1 = np.cumsum(D, 0)
    c2 = np.cumsum(D * D, 0)
    i = np.arange(1, n + 1)[:, None].astype(np.float64)
    m = c1 / i
    with np.errstate(all="ignore"):
        v = (c2 - i * m * m) / (i - 1)
    s = np.sqrt(np.maximum(v, 0))
    ok = s > 1e-12
    x = np.where(ok, m / np.where(ok, s, 1.0), 0.0)
    Sc[1:] = x
    Sc[1] = 0.0
    return Sc


def picks(rows: np.ndarray, k: int) -> np.ndarray:
    """rows ... by 31 scores. Returns ... by k candidate index, CASH where the score is not positive. Ties by frozen order."""
    if not np.isfinite(rows).all():
        raise ValueError("a score is not finite")
    o = np.argsort(-rows, axis=-1, kind="stable")[..., :k]
    v = np.take_along_axis(rows, o, -1)
    return np.where(v > 0, o, CASH)


def slot_names(row: np.ndarray) -> list[str]:
    """Candidate names of one list. CASH is the name cash."""
    return [NAMES[int(i)] for i in row]


class Book:
    """A library build with the calendar of the selector. W is anything that reads as W[i, a:b] for candidate i, stamps a to b: a float32 array in acceptance. gross and cost are Sr by 32, c is the stamp cost, S by N."""

    def __init__(self, t: np.ndarray, gross: np.ndarray, ccost: np.ndarray, c: np.ndarray, W=None):
        self.t = t
        self.gross = gross
        self.ccost = ccost
        self.cnet = gross - ccost
        self.c = c
        self.W = W
        self.S = len(t)
        self.N = c.shape[1]
        self.t0 = int(t[0])
        self.day = t // 86400
        self.mon = self.day - (self.day + 3) % 7
        self.D0 = ts(config.FIRST_GRID_DAY) // 86400
        self.s0 = int(self.stamp_of_day(self.D0))
        self.last_day = int(self.day[-1])

    def stamp_of_day(self, d) -> np.ndarray:
        return (np.asarray(d) * 86400 - self.t0) // 14400


def sel_days(bk: Book, cad: int = config.PRIMARY_CADENCE_WEEKS, phase: int = 0) -> np.ndarray:
    """Selection days of a book, as days since 1970. Phase p starts p weeks after the first grid day."""
    return np.arange(bk.D0 + 7 * phase, bk.last_day + 1, 7 * cad)


@dataclass
class ScoreBook:
    """Score tables of a net series, candidates without cash. rows(days) gives the score rows of those days, one row a day; the row of the Monday after the last block exists."""

    lab0: int
    primary: np.ndarray
    best: np.ndarray

    def rows(self, days, table: np.ndarray | None = None) -> np.ndarray:
        table = self.primary if table is None else table
        i = np.asarray(days) - self.lab0
        if (i < 0).any() or (i >= len(table)).any():
            raise ValueError("a day outside the score table")
        return table[i]


def score_book(net: np.ndarray, t: np.ndarray, look: int = config.PRIMARY_LOOK) -> ScoreBook:
    """Score tables from candidate net per stamp, Sr by 32 or 31. The stamps must end at the last stamp a cutoff can read: Sunday 16:00 for a Monday 00:00 cutoff."""
    lab, D = blocks(net[:, :CASH], t)
    return ScoreBook(int(lab[0]), score_table(D, look), score_expanding(D))


def path(bk: Book, ch: np.ndarray, starts: np.ndarray, k: int = config.PRIMARY_K) -> np.ndarray:
    """Direct weight path, S by N float64, flat before the first selection. ch: lists, one row a selection, starts: their stamps."""
    wp = np.zeros((bk.S, bk.N))
    ends = np.r_[starts[1:], bk.S]
    for j, (a, b) in enumerate(zip(starts, ends)):
        for i in ch[j]:
            if i != CASH:
                wp[a:b] += bk.W[i, a:b]
    return wp / k


def gross_path(bk: Book, ch: np.ndarray, starts: np.ndarray, k: int = config.PRIMARY_K) -> np.ndarray:
    """Gross per stamp from the float64 candidate series. Flat before the first selection."""
    g = np.zeros(bk.S)
    ends = np.r_[starts[1:], bk.S]
    for j, (a, b) in enumerate(zip(starts, ends)):
        for i in ch[j]:
            g[a:b] += bk.gross[a:b, i]
    return g / k


def ew_series(bk: Book) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """EW of the 32 from the first selection: gross, cost on the real path, weights. Flat before."""
    wp = np.zeros((bk.S, bk.N))
    for i in range(32):
        wp[bk.s0:] += bk.W[i, bk.s0:]
    wp /= 32
    g = np.zeros(bk.S)
    g[bk.s0:] = bk.gross[bk.s0:].mean(1)
    return g, cost_on_weights(wp, bk.c[:bk.S]), wp


def primary_lists(bk: Book, sb: ScoreBook, phase: int = 0, table: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Days, score rows and lists of one book over a whole build. The book's lists are the same rule at every phase, only the Mondays differ. A build cut at a Monday ends on the Sunday, so this stops at the Monday before it: use select for the Monday of a run."""
    days = sel_days(bk, phase=phase)
    rows = sb.rows(days, table)
    return days, rows, picks(rows, config.PRIMARY_K)


def select(sb: ScoreBook, day: int, table: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Score row and list of one Monday, day in days since 1970. On a build cut at that Monday it is the row past the last block."""
    row = sb.rows([day], table)[0]
    return row, picks(row, config.PRIMARY_K)
