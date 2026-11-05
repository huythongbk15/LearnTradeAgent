"""Frozen features of H-FLOW-BTC-02 (data/flow_btc_20261104_02/PROTOCOL.md).

An exact port of scripts/combo.py: ``hourly()``, ``features()``, ``SETS`` and
``targets()``, on frames in memory instead of the packet path. The NaN rules
are the research ones:

- an hour built from 1 m klines with fewer than 55 minutes is unknown;
- a metric is the 5 m value stamped at the hour start + 55 min, carried
  forward at most 3 rows of the merged index; open interest at or below 0 is
  unknown;
- funding is the last rate settled at or before the bar close;
- an infinite feature (the log of a zero count or volume) is unknown.

Bars are indexed by the hour start; a feature row at index t uses data up to
t + 1 h, the bar close. Any change here changes the book; it needs the
operator's ruling in docs/operations/PERP_DEMO_RUNNER.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

H_MS = 3_600_000
MIN_MINUTES = 55                                     # an hour with 6+ missing minutes is unknown
HOLDOUT = pd.Timestamp("2026-10-26", tz="UTC")       # research bars and metrics stop before this
ORIGIN = pd.Timestamp("2020-01-01", tz="UTC")        # combo's hour 0: decision rows are hour_no % 24 == 0
BAR_COLS = ["open", "close", "vol", "qv", "cnt", "tbv", "n"]
METRIC_COLS = ["sum_open_interest", "sum_toptrader_long_short_ratio", "count_long_short_ratio",
               "sum_taker_long_short_vol_ratio"]

SETS = {
    "P": ["r1", "r4", "r24", "r72", "rv24", "rvratio", "dhi30", "dlo7", "hsin", "hcos", "wkend"],
}
SETS["P+OI"] = SETS["P"] + ["doi1", "doi4", "doi24", "oiz30"]
SETS["P+OI+TA"] = SETS["P+OI"] + ["tk1", "tk4", "tk24", "cnt1", "qv1", "size1"]
SETS["FULL"] = SETS["P+OI+TA"] + ["tt_lvl", "tt_d24", "ga_lvl", "ga_d24", "mtk", "fund", "fund3"]
FULL = SETS["FULL"]


def hourly_from_1m(k: pd.DataFrame) -> pd.DataFrame:
    """1 m klines to hourly rows by hour start, every hour kept with its minute count ``n``.

    ``k`` has open_time (ms), open, close, volume, quote_volume, count and
    taker_buy_volume. The aggregation is combo.hourly()'s; the < 55 minutes
    rule is applied later by ``bars``, so the store keeps every hour.
    """
    k = k.sort_values("open_time", kind="stable")      # build.py wrote it sorted; keep that order
    hs = k["open_time"] // H_MS * H_MS
    g = k.groupby(hs)
    b = pd.DataFrame({"open": g["open"].first(), "close": g["close"].last(), "vol": g["volume"].sum(),
                      "qv": g["quote_volume"].sum(), "cnt": g["count"].sum(), "tbv": g["taker_buy_volume"].sum(),
                      "n": g.size()})
    b.index.name = "open_time"
    return b.reset_index()


def bars(rows: pd.DataFrame, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """combo's ``b``: hours with 55+ minutes, on a full hourly grid from the first to the last, before ``end``."""
    b = rows.set_index(pd.to_datetime(rows["open_time"], unit="ms", utc=True))[BAR_COLS]
    b = b[b["n"] >= MIN_MINUTES]
    b = b.reindex(pd.date_range(b.index[0], b.index[-1], freq="h", tz="UTC"))
    if end is not None:
        b = b[b.index < end]
    return b


def metrics(rows: pd.DataFrame, end: pd.Timestamp | None = None) -> pd.DataFrame:
    """5 m metrics indexed by their stamp (vision create_time), before ``end``."""
    m = rows.set_index(pd.to_datetime(rows["t"], unit="ms", utc=True))[METRIC_COLS].sort_index()
    if end is not None:
        m = m[m.index < end]
    return m.copy()


def funding(rows: pd.DataFrame) -> pd.Series:
    """Settled funding rates indexed by settlement time (``t`` in seconds, as the packet)."""
    fu = pd.Series(rows["rate"].to_numpy(dtype=float), index=pd.to_datetime(rows["t"], unit="s", utc=True))
    return fu.sort_index()


def features(b: pd.DataFrame, m: pd.DataFrame, fund: pd.Series) -> pd.DataFrame:
    """combo.features(b) with the metrics ``m`` and the funding rates ``fund`` given, not read from the packet."""
    with np.errstate(divide="ignore", invalid="ignore"):     # log(0) is set to unknown at the end, as combo
        return _features(b, m, fund)


def _features(b: pd.DataFrame, m: pd.DataFrame, fund: pd.Series) -> pd.DataFrame:
    f = pd.DataFrame(index=b.index)
    c = b["close"]
    lc = np.log(c)
    r1 = lc.diff()
    for n in (1, 4, 24, 72):
        f[f"r{n}"] = lc.diff(n)
    f["rv24"] = r1.rolling(24, min_periods=20).std()
    f["rvratio"] = f["rv24"] / r1.rolling(168, min_periods=150).std()
    f["dhi30"] = lc - lc.rolling(720, min_periods=600).max()
    f["dlo7"] = lc - lc.rolling(168, min_periods=150).min()
    m = m.copy()
    m.loc[m["sum_open_interest"] <= 0, "sum_open_interest"] = np.nan
    # the value stamped at or before t - 5 min, t = hour start + 1 h
    at = b.index + pd.Timedelta(minutes=55)
    ms = m.reindex(m.index.union(at)).sort_index().ffill(limit=3).reindex(at)
    ms.index = b.index
    loi = np.log(ms["sum_open_interest"])
    for n in (1, 4, 24):
        f[f"doi{n}"] = loi.diff(n)
    f["oiz30"] = loi - loi.rolling(720, min_periods=600).mean()
    tk = 2 * b["tbv"] / b["vol"] - 1
    f["tk1"] = tk
    f["tk4"] = 2 * b["tbv"].rolling(4).sum() / b["vol"].rolling(4).sum() - 1
    f["tk24"] = 2 * b["tbv"].rolling(24).sum() / b["vol"].rolling(24).sum() - 1
    for name, s in (("cnt1", b["cnt"]), ("qv1", b["qv"]), ("size1", b["qv"] / b["cnt"])):
        f[name] = np.log(s / s.shift(1).rolling(168, min_periods=150).median())
    tt = np.log(ms["sum_toptrader_long_short_ratio"])
    ga = np.log(ms["count_long_short_ratio"])
    f["tt_lvl"], f["tt_d24"] = tt, tt.diff(24)
    f["ga_lvl"], f["ga_d24"] = ga, ga.diff(24)
    f["mtk"] = np.log(ms["sum_taker_long_short_vol_ratio"])
    fr = fund.sort_index()
    t_close = b.index + pd.Timedelta(hours=1)
    pos = fr.index.searchsorted(t_close, side="right") - 1
    rates = fr.to_numpy()
    last = np.where(pos >= 0, rates[np.clip(pos, 0, None)], np.nan)
    s3 = np.where(pos >= 2, rates[np.clip(pos, 0, None)] + rates[np.clip(pos - 1, 0, None)] + rates[np.clip(pos - 2, 0, None)], np.nan)
    f["fund"], f["fund3"] = last, s3
    hr = b.index.hour
    f["hsin"], f["hcos"] = np.sin(2 * np.pi * hr / 24), np.cos(2 * np.pi * hr / 24)
    f["wkend"] = (b.index.dayofweek >= 5).astype(float)
    return f.replace([np.inf, -np.inf], np.nan)   # log of a zero count or volume: unknown


def targets(b: pd.DataFrame, h: int) -> pd.Series:
    """Log return from the open of hour t + 1 to the open h hours later."""
    o = b["open"]
    return np.log(o.shift(-(1 + h)) / o.shift(-1))


def hour_no(idx: pd.DatetimeIndex) -> np.ndarray:
    """Hours since 2020-01-01 00:00, combo's count (its first bar)."""
    return ((idx - ORIGIN) / pd.Timedelta(hours=1)).astype(int)
