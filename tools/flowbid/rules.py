"""The frozen flag, bid and settlement rules on 1m bars. Pure: arrays in, numbers out, no clock, no network.

Minutes count from 1970. A bar of minute m opens at m and closes before m + 1. A minute is traded when its bar exists and has quote volume; a minute with no trade repeats the last close and must not set a low.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tools.flowbid import config as cf


@dataclass
class Bars:
    """1m bars of one coin on a dense minute grid from minute g0. low is inf and close is nan where no trade."""

    g0: int
    low: np.ndarray
    close: np.ndarray
    traded: np.ndarray

    @property
    def g1(self) -> int:
        return self.g0 + len(self.low) - 1

    def last_traded(self, m: int) -> int | None:
        """Last traded minute at or before m inside the grid, or None."""
        i = min(m, self.g1) - self.g0
        if i < 0:
            return None
        hit = np.nonzero(self.traded[: i + 1])[0]
        return int(self.g0 + hit[-1]) if len(hit) else None

    def close_at(self, m: int) -> float:
        return float(self.close[m - self.g0])


def dense(rows: list, g0: int, g1: int) -> Bars:
    """Bars from kline rows (open time ms, open, high, low, close, volume, close time ms, quote volume, ...) on minutes g0..g1."""
    n = g1 - g0 + 1
    low = np.full(n, np.inf)
    close = np.full(n, np.nan)
    traded: np.ndarray = np.zeros(n, bool)
    for r in rows:
        i = int(r[0]) // 60_000 - g0
        if 0 <= i < n and float(r[7]) > 0:
            low[i] = float(r[3])
            close[i] = float(r[4])
            traded[i] = True
    return Bars(g0, low, close, traded)


def rolling_min(x: np.ndarray, w: int) -> np.ndarray:
    """out[i] = min of x[i - w + 1 .. i]; inf for the first w - 1 entries."""
    n = len(x)
    out = np.full(n, np.inf)
    if n < w:
        return out
    pad = (w - n % w) % w
    b = np.concatenate([x, np.full(pad, np.inf)]).reshape(-1, w)
    pre = np.minimum.accumulate(b, axis=1).ravel()
    suf = np.minimum.accumulate(b[:, ::-1], axis=1)[:, ::-1].ravel()
    out[w - 1:] = np.minimum(suf[: n - w + 1], pre[w - 1: n])
    return out


def counts(coins: dict[str, Bars], m0: int, m1: int) -> tuple[np.ndarray, np.ndarray]:
    """Down coins and counted coins at every minute m0..m1. Each grid must start at least WINDOW_MIN minutes before m0."""
    n = m1 - m0 + 1
    down: np.ndarray = np.zeros(n, int)
    den: np.ndarray = np.zeros(n, int)
    for b in coins.values():
        if m0 - b.g0 < cf.WINDOW_MIN or b.g1 < m1:
            raise ValueError("a grid does not cover the window of the minutes it judges")
        rm = rolling_min(b.low, cf.WINDOW_MIN)
        idx = np.arange(len(b.low))
        li = np.maximum.accumulate(np.where(b.traded, idx, -1))
        i = np.arange(m0 - b.g0, m1 - b.g0 + 1)
        lag = li[i - cf.WINDOW_MIN]
        ok = (lag >= 0) & (i - cf.WINDOW_MIN - lag <= cf.REF_MAX_AGE_MIN) & np.isfinite(rm[i])
        ref = np.where(ok, b.close[np.maximum(lag, 0)], np.nan)
        den += ok
        with np.errstate(invalid="ignore"):
            down += ok & (rm[i] <= (1 - cf.DOWN) * ref)
    return down, den


def flagged(down: np.ndarray, den: np.ndarray) -> np.ndarray:
    return (den >= cf.MIN_DENOMINATOR) & (down >= cf.SHARE * den)


def new_events(flag_minutes, last_event: int | None) -> list[int]:
    """Event minutes: a flag opens an event when none is active; an event stays active EVENT_MIN minutes."""
    out = []
    last = last_event
    for m in flag_minutes:
        if last is None or m - last >= cf.EVENT_MIN:
            out.append(int(m))
            last = int(m)
    return out


def day_start(m: int) -> int:
    return (m // 1440) * 1440


def bid_terms(ref_bars: Bars, flag_m: int, last_price: float | None, k: float) -> tuple[str, dict]:
    """("bid", terms) or (reason, terms). The reference is the last traded close before 00:00 UTC of the flag date, at most 48 h old."""
    d0 = day_start(flag_m)
    r = ref_bars.last_traded(d0 - 1)
    if r is None or d0 - 1 - r > cf.BID_REF_MAX_AGE_MIN:
        return "no_reference", {}
    ref = ref_bars.close_at(r)
    bid = ref * (1 - k)
    terms = {"k": k, "ref": ref, "ref_minute": r, "bid": bid, "last": last_price}
    if last_price is None or not last_price > bid:
        return "already_through", terms
    return "bid", terms


def fill_minute(b: Bars, bid: float, live_from: int, live_to: int) -> int | None:
    """First traded minute in [live_from, live_to) whose low is at or under the bid less THROUGH."""
    a, z = live_from - b.g0, live_to - b.g0
    if a < 0 or b.g1 < live_to - 1:
        raise ValueError("bars do not cover the live window")
    hit = np.nonzero(b.traded[a:z] & (b.low[a:z] <= bid * (1 - cf.THROUGH)))[0]
    return int(live_from + hit[0]) if len(hit) else None


def exit_terms(b: Bars, stamp: int) -> dict:
    """Last traded close at or before the stamp; gone when its minute is more than GONE_MIN before the stamp."""
    e = b.last_traded(stamp)
    if e is None:
        return {"minute": None, "close": None, "gone": True}
    return {"minute": e, "close": b.close_at(e), "gone": stamp - e > cf.GONE_MIN}
