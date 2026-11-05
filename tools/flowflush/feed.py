"""Public mainnet data of book FLOW01, through the NL-P allowlisted client (tools/newperp/fetch.py).

Two calls a look, both on fapi.binance.com, no key:

- ``/fapi/v1/klines`` BTCUSDT 1m over the last ``LOOKBACK_MS``;
- ``/futures/data/openInterestHist`` BTCUSDT period 5m over the same span.

Stamps here are the research panel's: the archive's ``create_time`` (``m5.parquet`` column ``t``). The
endpoint stamps the same snapshot 5 minutes later (``OI_LABEL_SHIFT_MS``): its row stamped T equals the
archive row with create_time T - 5 min, and its implied price (value / OI) matches the 1m close ending at T,
so T is when the snapshot was taken (the archive's create_time matches the close 5 minutes later). Checked on
2026-11-05 by ``parity.py --align`` over 2026-10-07 to 2026-10-26: 5,448 stamps matched, 5,370 equal at this
shift and none at any other shift; the 78 unequal rows are all on 2026-10-15 (up to 3.0%). So the OI of
research stamp t is public only after t + 5 min, when the endpoint publishes row t + 5 min (10 s, 18 s,
22 s and 84 s after it on four stamps observed on 2026-11-05, two by this book's poll and two by book
FLOW02's probe). Research enters at the 1m open at t + 5 min, the minute the
snapshot is taken, so the demo cannot enter at the research price.

A research stamp t is a candidate when the endpoint has published its OI (row t + 5 min) and the 1m bar
ending at t is final (the venue has started a later bar). ret60 and doi60 need data 60 min back, so a look
covers the stamps of the last ``LOOKBACK_MS - 60 min``.

Each look is kept, so ``open_at`` answers the research entry price (the 1m open at t + 5 min) without a
second call while the bar is in it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from tools.flowflush import rules as R
from tools.newperp import fetch as nf

LOOKBACK_MS = 3 * 60 * R.MIN_MS          # stamps of the last 2 h, with 60 min of history each
OI_LABEL_SHIFT_MS = 5 * R.MIN_MS         # endpoint stamp T = archive create_time T - 5 min (parity.py --align)


@dataclass(frozen=True)
class Stamp:
    t: int
    ret60: float
    doi60: float
    event: bool


@dataclass(frozen=True)
class Look:
    """One look at the public data: the candidate stamps, oldest first, and the first stamp it can judge."""

    now_ms: int
    stamps: tuple[Stamp, ...]
    covered_from: int
    newest_bar: int | None        # open time of the newest 1m bar the venue returned (it may still be forming)


def grid(ms: int) -> int:
    return ms // R.STAMP_MS * R.STAMP_MS


def published_now(look: Look) -> bool:
    """True when the look holds the endpoint's row of the current 5-minute stamp (research stamp grid - 5 min)."""

    return bool(look.stamps) and look.stamps[-1].t + OI_LABEL_SHIFT_MS >= grid(look.now_ms)


def api_to_research(api_ms: int) -> int:
    """The research stamp (archive create_time) of an openInterestHist row stamped ``api_ms``."""

    return api_ms - OI_LABEL_SHIFT_MS


def look_from_rows(now_ms: int, k_rows: list, oi_rows: list[dict], start_ms: int) -> tuple[Look, dict[int, float]]:
    """Build a look from raw rows: klines ``[open_time, open, high, low, close, ...]`` and endpoint OI rows
    ``{"timestamp", "sumOpenInterest"}``, relabelled to research stamps. Also returns the 1m opens by open time."""

    bars = {int(r[0]): (float(r[1]), float(r[4])) for r in k_rows}
    opens = {t: o for t, (o, _) in bars.items()}
    if not bars:
        return Look(now_ms, (), start_ms + R.W_MS, None), opens
    newest = max(bars)
    final = sorted(t for t in bars if t < newest)          # the newest bar may still be forming
    k_open = np.array(final, dtype=np.int64)
    k_close = np.array([bars[t][1] for t in final], dtype=float)
    oi = {api_to_research(int(r["timestamp"])): float(r["sumOpenInterest"]) for r in oi_rows}
    oi_t = np.array(sorted(oi), dtype=np.int64)
    oi_v = np.array([oi[t] for t in oi_t], dtype=float)
    covered_from = start_ms + R.W_MS
    cand = np.array([t for t in oi_t if t >= covered_from and t % R.STAMP_MS == 0 and t <= newest], dtype=np.int64)
    ret, doi = R.features(cand, k_open, k_close, oi_t, oi_v)
    ev = R.is_event(ret, doi)
    stamps = tuple(Stamp(int(t), float(r), float(d), bool(e)) for t, r, d, e in zip(cand, ret, doi, ev))
    return Look(now_ms, stamps, covered_from, newest), opens


@dataclass
class Feed:
    """The live look. ``client`` is an ``nf.Client`` (tests give one on a fake transport)."""

    client: nf.Client = field(default_factory=nf.Client)
    lookback_ms: int = LOOKBACK_MS
    _opens: dict[int, float] = field(default_factory=dict, repr=False)
    last: Look | None = field(default=None, repr=False)

    def look(self, now_ms: int) -> Look:
        start = grid(now_ms - self.lookback_ms)
        k_rows = nf.klines(self.client, R.SYMBOL, "1m", start - R.MIN_MS, now_ms + R.MIN_MS)
        oi_rows = nf.stats(self.client, nf.OPEN_INTEREST_HIST, R.SYMBOL, "5m", start, now_ms + 1)
        out, opens = look_from_rows(now_ms, k_rows, oi_rows, start)
        self._opens.update(opens)
        self.last = out
        return out

    def open_at(self, t_ms: int) -> float | None:
        """Mainnet 1m open of the bar opening at ``t_ms``; None when it is not out yet or not served."""

        t_ms = t_ms // R.MIN_MS * R.MIN_MS
        if t_ms in self._opens:
            return self._opens[t_ms]
        rows = nf.klines(self.client, R.SYMBOL, "1m", t_ms, t_ms + R.MIN_MS)
        for r in rows:
            if int(r[0]) == t_ms:
                self._opens[t_ms] = float(r[1])
                return self._opens[t_ms]
        return None
