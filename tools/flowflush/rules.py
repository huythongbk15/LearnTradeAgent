"""The frozen H-FLOW-BTC-01 primary cell as a live rule. Pure: arrays in, arrays out, no clock, no network.

The cell: LF (long flush), w = 60 min, q = 1% of train, no burst filter, hold 4 h, long BTCUSDT.

At a 5-minute metric stamp t (ms since 1970, on the 5-minute grid):

- ``ret60 = log(close of the 1m bar ending at t / close of the 1m bar ending at t - 60 min)``;
- ``doi60 = log(OI stamped t / OI stamped t - 60 min)``; an OI at or below 0 is unknown, not zero;
- t is an event when both are finite, ``ret60 <= RLO`` and ``doi60 <= DLO``;
- after an event, the next one needs ``t > previous t + 5 min + 4 h`` (``decluster``);
- research enters at the 1m open at t + 5 min and exits at the 1m open 4 h later.

Provenance, the git-ignored research packet ``data/flow_btc_20261104_01``:

- PROTOCOL.md, frozen 2026-11-04 before the download (sha256 a2820b0c518cb1d3..., FROZEN.sha256).
- scripts/measure.py (sha256 ac1d92739337dec7..., FROZEN.sha256): the features and ``decluster`` copied here.
- train.json (sha256 4611874baf726dff...), ``thresholds["60|0.01"]``: ``rlo`` and ``dlo``, the 1% quantiles of
  ret60 and doi60 over the finite train pairs, 2020-09-01 to 2024-01-01. Copied as exact floats.
- RESULTS.md: train n 125, net +19.9 bps; test (2024-01-01 to 2026-10-26) n 29, net +24.2 bps, t 0.59.
  The primary cell FAILED its t >= 2 bar. The demo runs on the operator's decision of 2026-11-05.

A change of a constant needs the operator's ruling in docs/operations/PERP_DEMO_RUNNER.md. Research
description, not advice.
"""
from __future__ import annotations

import numpy as np

SYMBOL = "BTCUSDT"
MIN_MS = 60_000
STAMP_MS = 5 * MIN_MS                     # the metric grid
W_MS = 60 * MIN_MS                        # w = 60 min
HOLD_MS = 4 * 60 * MIN_MS                 # h = 4 h
ENTRY_DELAY_MS = 5 * MIN_MS               # research entry: the 1m open at t + 5 min
CLUSTER_MS = STAMP_MS + HOLD_MS           # measure.decluster: next event needs t > previous t + 5 min + 4 h
RLO = -0.021138149153270704               # train.json thresholds["60|0.01"]["rlo"]
DLO = -0.03324631085383747                # train.json thresholds["60|0.01"]["dlo"]
COST_BPS = 11.0                           # the research round trip (data/small_bars_20261101), for parity only
ENTRY_WINDOW_MS = 20 * MIN_MS             # demo: enter at the first run with now - t <= 20 min, else stale


def _at(keys: np.ndarray, values: np.ndarray, want: np.ndarray) -> np.ndarray:
    """values[keys == want] for each want (keys sorted, unique); NaN where absent."""

    keys = np.asarray(keys, dtype=np.int64)
    want = np.asarray(want, dtype=np.int64)
    out = np.full(len(want), np.nan)
    if len(keys) == 0:
        return out
    i = np.searchsorted(keys, want)
    ok = i < len(keys)
    ok[ok] = keys[i[ok]] == want[ok]
    out[ok] = np.asarray(values, dtype=float)[i[ok]]
    return out


def close_ending_at(tt: np.ndarray, k_open: np.ndarray, k_close: np.ndarray) -> np.ndarray:
    """Close of the 1m bar that ends at each tt: the bar opening at the minute of tt less one minute.

    measure.py indexes minutes from the first bar, so an off-minute tt floors; metric stamps sit on the
    grid, so for them this is the bar opening at tt - 1 min.
    """

    tt = np.asarray(tt, dtype=np.int64)
    return _at(k_open, k_close, tt // MIN_MS * MIN_MS - MIN_MS)


def open_at(tt: np.ndarray, k_open: np.ndarray, k_o: np.ndarray) -> np.ndarray:
    """Open of the 1m bar holding each tt (the bar opening at the minute of tt)."""

    tt = np.asarray(tt, dtype=np.int64)
    return _at(k_open, k_o, tt // MIN_MS * MIN_MS)


def oi_at(tt: np.ndarray, oi_t: np.ndarray, oi_v: np.ndarray) -> np.ndarray:
    """Open interest stamped exactly at each tt; an OI at or below 0 is unknown (NaN)."""

    v = np.asarray(oi_v, dtype=float).copy()
    v[~(v > 0)] = np.nan
    return _at(oi_t, v, tt)


def features(stamps: np.ndarray, k_open: np.ndarray, k_close: np.ndarray,
             oi_t: np.ndarray, oi_v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """ret60 and doi60 at each stamp, with measure.py's arithmetic (log of a ratio). NaN where data are missing.

    ``k_open``/``k_close``: 1m bars by open time, sorted and unique; only closed bars. ``oi_t``/``oi_v``:
    the 5-minute open interest by stamp, sorted and unique.
    """

    stamps = np.asarray(stamps, dtype=np.int64)
    prev = stamps - W_MS
    with np.errstate(divide="ignore", invalid="ignore"):
        ret = np.log(close_ending_at(stamps, k_open, k_close) / close_ending_at(prev, k_open, k_close))
        doi = np.log(oi_at(stamps, oi_t, oi_v) / oi_at(prev, oi_t, oi_v))
    return ret, doi


def is_event(ret60: np.ndarray, doi60: np.ndarray) -> np.ndarray:
    """LF at w 60, q 1%: both finite, ret60 <= RLO and doi60 <= DLO."""

    r = np.asarray(ret60, dtype=float)
    d = np.asarray(doi60, dtype=float)
    ok = np.isfinite(r) & np.isfinite(d)
    out = np.zeros(len(r), bool)
    out[ok] = (r[ok] <= RLO) & (d[ok] <= DLO)
    return out


def decluster(ts: np.ndarray, cluster_ms: int = CLUSTER_MS) -> np.ndarray:
    """measure.decluster: keep an event when t > the last kept t + 5 min + 4 h. ``ts`` ascending."""

    keep = np.zeros(len(ts), bool)
    last = -(10 ** 18)
    for i, x in enumerate(np.asarray(ts, dtype=np.int64)):
        if x > last:
            keep[i] = True
            last = int(x) + cluster_ms
    return keep


def clustered(t: int, last_event_t: int | None) -> bool:
    """True when an event at t falls inside the cluster of the previous event."""

    return last_event_t is not None and t <= last_event_t + CLUSTER_MS
