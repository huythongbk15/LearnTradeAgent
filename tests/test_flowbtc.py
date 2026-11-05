"""H-FLOW-BTC-02 data and model (tools/flowbtc): synthetic frames and a fake venue, no network, no packet.

The parity test runs only when FLOWBTC_PARITY_STORE names a store seeded from
the packet (python -I tools/flowbtc/run.py --init-from ...).
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import Ridge

from tools.flowbtc import features as F
from tools.flowbtc import model as M
from tools.flowbtc import run as R
from tools.flowbtc import store as S
from tools.flowbtc.signal import Signal
from tools.newperp import fetch as fx

H = 3_600_000
M5 = 300_000
DAY = 24 * H
LOCK_AS_SHIPPED = R.LOCK      # before runners_lock_in_tmp points it into each test's tmp_path


def ms(ts) -> int:
    return int(pd.Timestamp(ts).tz_localize("UTC").timestamp() * 1000) if pd.Timestamp(ts).tzinfo is None \
        else int(pd.Timestamp(ts).timestamp() * 1000)


# ── synthetic market ───────────────────────────────────────────────────────

class Market:
    """A synthetic BTCUSDT perp: hourly bars, 5 m metrics and 8 h funding over [start, end)."""

    def __init__(self, start="2020-08-01", end="2021-01-10", seed=0):
        rng = np.random.default_rng(seed)
        hours = np.arange(ms(start), ms(end), H, dtype="int64")
        close = 10_000 * np.exp(np.cumsum(rng.normal(0, 0.005, len(hours))))
        vol = rng.uniform(100, 200, len(hours))
        self.bars = pd.DataFrame({"open_time": hours, "open": np.r_[10_000.0, close[:-1]], "close": close, "vol": vol,
                                  "qv": vol * close, "cnt": rng.integers(1_000, 2_000, len(hours)).astype(float),
                                  "tbv": vol * rng.uniform(0.4, 0.6, len(hours)), "n": np.int64(60), "src": np.int8(0)})
        stamps = np.arange(ms(start), ms(end), M5, dtype="int64")
        self.metrics = pd.DataFrame({
            "t": stamps,
            "sum_open_interest": 50_000 * np.exp(np.cumsum(rng.normal(0, 0.001, len(stamps)))),
            "sum_toptrader_long_short_ratio": np.exp(rng.normal(0.1, 0.1, len(stamps))),
            "count_long_short_ratio": np.exp(rng.normal(0.2, 0.1, len(stamps))),
            "sum_taker_long_short_vol_ratio": np.exp(rng.normal(0, 0.2, len(stamps)))})
        ft = np.arange(ms(start), ms(end), 8 * H, dtype="int64") // 1000
        self.funding = pd.DataFrame({"t": ft, "rate": rng.normal(1e-4, 5e-5, len(ft))})

    def seed(self, root: Path, upto_ms: int) -> S.Store:
        """A store holding everything before ``upto_ms``, as if seeded then."""
        root.mkdir(parents=True, exist_ok=True)
        self.bars[self.bars["open_time"] < upto_ms].to_parquet(root / "bars.parquet", index=False)
        self.metrics[self.metrics["t"] < upto_ms].to_parquet(root / "metrics.parquet", index=False)
        self.funding[self.funding["t"] * 1000 < upto_ms].to_parquet(root / "funding.parquet", index=False)
        (root / "meta.json").write_text(json.dumps({"gaps": [], "updates": 0}))
        return S.Store(root)


class Venue:
    """Public endpoints over a Market: klines, the four series and funding, each published only up to ``now``.

    A series stamps a value at the vision stamp less its offset in store.SERIES,
    and serves vision stamps up to now - 5 min, as the live endpoints did on 2026-11-05.
    """

    def __init__(self, mk: Market):
        self.mk, self.calls = mk, []
        self.now = 0
        self.behind: dict[str, int] = {}     # path -> stamps a series is late by
        self.hole: tuple[int, int] | None = None   # vision stamps no series serves
        self.fail = None                     # (path, query) -> an HTTP status to answer instead, or None

    def __call__(self, url, timeout):
        fx.check_url(url)
        p = urlsplit(url).path
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        self.calls.append((p, q))
        if self.fail and (status := self.fail(p, q)):
            return status, b"{}"
        a, b = int(q.get("startTime", 0)), int(q.get("endTime", 2**62))
        if p == fx.KLINES:
            d = self.mk.bars[(self.mk.bars["open_time"] >= a) & (self.mk.bars["open_time"] <= b)
                             & (self.mk.bars["open_time"] + H <= self.now)]
            rows = [[int(r.open_time), str(r.open), "0", "0", str(r.close), str(r.vol), int(r.open_time) + H - 1, str(r.qv),
                     int(r.cnt), str(r.tbv), "0", "0"] for r in d.itertuples()][: int(q["limit"])]
            return 200, json.dumps(rows).encode()
        if p in S.SERIES:
            field, col, off = S.SERIES[p]
            last = self.now - M5 - self.behind.get(p, 0) * M5
            m = self.mk.metrics
            api_t = m["t"] - off
            sel = (api_t >= a) & (api_t <= b) & (m["t"] <= last)
            if self.hole:
                sel &= ~((m["t"] >= self.hole[0]) & (m["t"] <= self.hole[1]))
            d = m[sel].tail(int(q["limit"]))
            rows = [{"symbol": "BTCUSDT", "timestamp": int(t) - off, field: str(v)} for t, v in zip(d["t"], d[col])]
            return 200, json.dumps(rows[::-1]).encode()
        if p == fx.FUNDING:
            f = self.mk.funding
            t = f["t"] * 1000 + 3              # the API's settlement time carries a few ms
            d = f[(t >= a) & (t <= b) & (t <= self.now)].head(int(q["limit"]))
            rows = [{"symbol": "BTCUSDT", "fundingTime": int(x) * 1000 + 3, "fundingRate": str(r)} for x, r in zip(d["t"], d["rate"])]
            return 200, json.dumps(rows).encode()
        return 404, b"{}"


def client(v: Venue) -> fx.Client:
    return fx.Client(v, pace=0, sleep=lambda s: None)


@pytest.fixture(scope="module")
def market() -> Market:
    return Market()


@pytest.fixture(autouse=True)
def runners_lock_in_tmp(tmp_path, monkeypatch):
    """run.py's writing modes take the runners' lock: here one under the test's tmp_path, never the checkout's."""
    from tools.flowbtc import run as R
    monkeypatch.setattr(R, "LOCK", tmp_path / "logs" / ".perp_demo.lock")


# ── features ───────────────────────────────────────────────────────────────

def test_hourly_from_1m_keeps_every_hour_and_bars_drop_short_ones():
    t = np.arange(ms("2024-01-01"), ms("2024-01-01 03:00"), 60_000, dtype="int64")
    t = t[(t < ms("2024-01-01 01:00")) | (t >= ms("2024-01-01 01:06"))]          # hour 1 loses 6 minutes
    k = pd.DataFrame({"open_time": t, "open": np.arange(len(t), dtype=float) + 1, "close": np.arange(len(t), dtype=float) + 2,
                      "volume": 1.0, "quote_volume": 2.0, "count": 3, "taker_buy_volume": 0.5}).iloc[::-1]
    h = F.hourly_from_1m(k)
    assert h["n"].tolist() == [60, 54, 60]
    assert h.loc[0, "open"] == 1 and h.loc[0, "close"] == 61 and h.loc[0, "vol"] == 60 and h.loc[0, "cnt"] == 180
    b = F.bars(h)
    assert len(b) == 3 and b.iloc[1].isna().all() and b.iloc[2]["n"] == 60


def test_features_use_only_data_to_the_bar_close(market):
    b, m, fu = F.bars(market.bars), F.metrics(market.metrics), F.funding(market.funding)
    full = F.features(b, m, fu)
    t = pd.Timestamp("2020-12-20 07:00", tz="UTC")
    close = t + pd.Timedelta(hours=1)
    cut = F.features(b[b.index <= t], m[m.index < close], fu[fu.index <= close])
    assert list(full.columns[: len(F.FULL)]) and set(F.FULL) <= set(full.columns)
    np.testing.assert_array_equal(full.loc[t, F.FULL].to_numpy(float), cut.loc[t, F.FULL].to_numpy(float))
    assert full.loc[t, F.FULL].notna().all()


def test_metric_is_the_value_at_55_min_carried_three_rows_and_bad_values_are_unknown(market):
    b = F.bars(market.bars)
    m = F.metrics(market.metrics)
    fu = F.funding(market.funding)
    t = pd.Timestamp("2020-12-20 07:00", tz="UTC")
    at = t + pd.Timedelta(minutes=55)
    base = F.features(b, m, fu)
    assert base.loc[t, "ga_lvl"] == pytest.approx(np.log(m.loc[at, "count_long_short_ratio"]))
    # the 00:55 row missing: the 00:50 value carries; four rows missing (00:40 to 00:55): unknown
    m1 = m.drop(index=[at])
    assert F.features(b, m1, fu).loc[t, "ga_lvl"] == pytest.approx(np.log(m.loc[at - pd.Timedelta(minutes=5), "count_long_short_ratio"]))
    # open interest at 0 is unknown before the carry, as in combo: the 00:50 value stands in
    m0 = m.copy()
    m0.loc[at, "sum_open_interest"] = 0.0
    prev = at - pd.Timedelta(minutes=5)
    assert F.features(b, m0, fu).loc[t, "doi1"] == pytest.approx(
        np.log(m.loc[prev, "sum_open_interest"]) - np.log(m.loc[at - pd.Timedelta(hours=1), "sum_open_interest"]))
    # funding: the last rate at or before the bar close, a rate stamped exactly at the close included
    t8 = pd.Timestamp("2020-12-20 07:00", tz="UTC")       # closes 08:00, a settlement
    assert base.loc[t8, "fund"] == pytest.approx(fu.loc[pd.Timestamp("2020-12-20 08:00", tz="UTC")])
    assert base.loc[t8 - pd.Timedelta(hours=1), "fund"] == pytest.approx(fu.loc[pd.Timestamp("2020-12-20 00:00", tz="UTC")])
    # a zero count gives log(0): unknown, not infinite
    bz = b.copy()
    bz.loc[t, "cnt"] = 0.0
    fz = F.features(bz, m, fu)
    assert np.isnan(fz.loc[t, "cnt1"]) and not np.isinf(fz.to_numpy(float)).any()


def test_a_missing_metric_row_carries_along_the_merged_index_as_combo():
    idx = pd.date_range("2024-01-01", periods=10, freq="h", tz="UTC")
    b = pd.DataFrame({"open": 1.0, "close": np.linspace(1, 2, 10), "vol": 1.0, "qv": 1.0, "cnt": 1.0, "tbv": 0.5, "n": 60}, index=idx)
    stamps = pd.date_range("2024-01-01", periods=12 * 10, freq="5min", tz="UTC")
    m = pd.DataFrame({c: np.arange(len(stamps), dtype=float) + 1 for c in F.METRIC_COLS}, index=stamps)
    m = m[(m.index < idx[2]) | (m.index >= idx[7])]               # five hours of metrics missing
    fu = pd.Series([1e-4], index=[idx[0]])
    ga = F.features(b, m, fu)["ga_lvl"]
    last = np.log(m.loc[idx[2] - pd.Timedelta(minutes=5), "count_long_short_ratio"])
    # 01:55 is on file; 02:55, 03:55, 04:55 carry the 01:55 row (three rows of the merged index); 05:55 is unknown
    assert [round(float(x), 9) if np.isfinite(x) else None for x in ga.iloc[1:6]] == [round(float(last), 9)] * 4 + [None]


# ── model ──────────────────────────────────────────────────────────────────

def frames(mk: Market):
    b, m, fu = F.bars(mk.bars), F.metrics(mk.metrics), F.funding(mk.funding)
    return b, F.features(b, m, fu), F.targets(b, M.H)


def test_fit_is_combo_ridge_on_combo_standardization(market):
    b, f, y = frames(market)
    q = pd.Timestamp("2021-01-01", tz="UTC")
    mdl = M.fit(f, y, q)
    # combo.walk's ridge branch, written out
    idx = f.index
    tr = (idx >= pd.Timestamp("2020-09-01", tz="UTC")) & (idx + pd.Timedelta(hours=25) <= q) & y.notna().to_numpy()
    Xtr, ytr = f.loc[tr, F.FULL], y[tr]
    med = Xtr.median()
    sd = Xtr.std().replace(0, 1)
    z = lambda A: ((A.fillna(med) - Xtr.fillna(med).mean()) / sd).to_numpy()  # noqa: E731
    ref = Ridge(alpha=10.0).fit(z(Xtr), ytr.to_numpy())
    thr = np.quantile(np.abs(ref.predict(z(Xtr))), 0.8)
    assert mdl["threshold"] == thr and mdl["train_rows"] == int(tr.sum())
    te = (idx >= q) & (idx.hour == 0)
    np.testing.assert_array_equal(M.predict(mdl, f.loc[te]), ref.predict(z(f.loc[te, F.FULL])))
    # the label of the last training row ends by the quarter start
    assert pd.Timestamp(mdl["train_last"]) + pd.Timedelta(hours=25) <= q
    assert M.is_long(mdl, mdl["threshold"] * 1.01) and not M.is_long(mdl, mdl["threshold"])
    assert not M.is_long(mdl, -mdl["threshold"] * 2)          # long-only: a strong negative call is flat


def test_quarters_and_tags():
    assert M.quarter_of(pd.Timestamp("2026-11-05 00:00", tz="UTC")) == pd.Timestamp("2026-10-01", tz="UTC")
    assert M.quarter_tag(pd.Timestamp("2027-01-01", tz="UTC")) == "2027Q1"


def test_model_is_recorded_once_and_a_changed_file_is_refused(market, tmp_path):
    b, f, y = frames(market)
    mdl = M.fit(f, y, pd.Timestamp("2021-01-01", tz="UTC"))
    assert M.load(tmp_path, "2021Q1") is None
    M.record(tmp_path, mdl)
    assert M.load(tmp_path, "2021Q1") == json.loads(json.dumps(mdl))
    with pytest.raises(M.ModelError, match="already"):
        M.record(tmp_path, mdl)
    p = tmp_path / "models" / "2021Q1.json"
    p.write_text(p.read_text().replace('"alpha": 10.0', '"alpha": 1.0'))
    with pytest.raises(M.ModelError, match="hash"):
        M.load(tmp_path, "2021Q1")


def test_walk_predicts_each_quarter_with_its_own_fit_on_the_00_bar(market):
    b, f, y = frames(market)
    d = M.walk(f, y, first=pd.Timestamp("2020-10-01", tz="UTC"), end=pd.Timestamp("2021-01-08", tz="UTC"))
    assert (d.index.hour == 0).all() and d.index[0] == pd.Timestamp("2020-10-01", tz="UTC")
    assert d.index[-1] + pd.Timedelta(hours=25) < pd.Timestamp("2021-01-08", tz="UTC")
    q4 = M.fit(f, y, pd.Timestamp("2020-10-01", tz="UTC"))
    assert (d.loc[d.index < pd.Timestamp("2021-01-01", tz="UTC"), "thr"] == q4["threshold"]).all()
    s = M.score_long_only(d)
    pos = ((d["pred"].abs() > d["thr"]) & (d["pred"] > 0)).astype(float).to_numpy()
    prev = np.r_[0.0, pos[:-1]]
    assert s["n"] == len(d) and s["net"] == pytest.approx(((pos * d["fwd"] - 5.5e-4 * np.abs(pos - prev)) * 1e4).mean())


# ── store ──────────────────────────────────────────────────────────────────

def test_store_seeds_once_from_a_packet_and_refuses_to_reseed(tmp_path):
    panel = tmp_path / "panel"
    panel.mkdir()
    t = np.arange(ms("2024-01-01"), ms("2024-01-01 02:00"), 60_000, dtype="int64")
    pd.DataFrame({"open_time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "quote_volume": 1.0,
                  "count": np.int64(2), "taker_buy_volume": 0.5}).to_parquet(panel / "k1m.parquet")
    st = np.arange(ms("2024-01-01"), ms("2024-01-01 02:00"), M5, dtype="int64")
    pd.DataFrame({"sum_open_interest": 1.0, "sum_open_interest_value": 1.0, "count_toptrader_long_short_ratio": 1.0,
                  "sum_toptrader_long_short_ratio": 1.0, "count_long_short_ratio": 1.0, "sum_taker_long_short_vol_ratio": 1.0,
                  "t": st}).to_parquet(panel / "m5.parquet")
    fund = tmp_path / "BTC.parquet"
    pd.DataFrame({"t": [ms("2024-01-01") // 1000], "rate": [1e-4]}).to_parquet(fund)
    store = S.Store(tmp_path / "store")
    got = store.init_from(panel, fund)
    assert got["bars"] == 2 and got["metrics"] == 24 and got["funding"] == 1
    assert store.load_bars()["n"].tolist() == [60, 60] and set(store.load_metrics().columns) == {"t", *F.METRIC_COLS}
    assert store.meta()["seeded"]["bars"] == 2
    with pytest.raises(S.StoreError, match="refusing"):
        store.init_from(panel, fund)


def test_update_appends_closed_rows_on_vision_stamps_and_records_gaps(market, tmp_path):
    assert {off for _, _, off in S.SERIES.values()} == {-M5, 0}   # three series stamp 5 min after vision, taker on it
    store = market.seed(tmp_path, ms("2021-01-01"))
    v = Venue(market)
    v.now = ms("2021-01-02 01:15")
    v.behind[fx.TOP_POSITION_RATIO] = 2                       # one series is two stamps late
    v.hole = (ms("2021-01-01 12:00"), ms("2021-01-01 12:10"))
    got = store.update(client(v), v.now)
    bars, m, fu = store.load_bars(), store.load_metrics(), store.load_funding()
    assert bars["open_time"].max() == ms("2021-01-02 00:00") and got["bars"] == 25
    assert (bars.loc[bars["open_time"] >= ms("2021-01-01"), "src"] == 1).all()
    assert (bars.loc[bars["open_time"] >= ms("2021-01-01"), "n"] == 60).all()
    # every series on vision stamps, through the stamp the late series has reached
    assert m["t"].max() == ms("2021-01-02 01:00")
    new = m[m["t"] >= ms("2021-01-01")].set_index("t")
    ref = market.metrics.set_index("t").loc[new.index]
    np.testing.assert_allclose(new[F.METRIC_COLS].to_numpy(), ref[F.METRIC_COLS].to_numpy(), rtol=1e-12)
    assert not set(range(ms("2021-01-01 12:00"), ms("2021-01-01 12:15"), M5)) & set(m["t"])
    assert fu["t"].max() == ms("2021-01-02 00:00") // 1000              # ms offsets dropped, as the packet
    assert {"series": "metrics", "from": "2021-01-01T12:00Z", "to": "2021-01-01T12:10Z"} in store.meta()["gaps"]
    # the late series catches up at the next update; nothing is lost or doubled
    v.behind.clear()
    v.now = ms("2021-01-02 02:15")
    store.update(client(v), v.now)
    m = store.load_metrics()
    assert m["t"].is_unique and m["t"].max() == ms("2021-01-02 02:10")
    assert m.set_index("t").loc[ms("2021-01-02 01:05"), F.METRIC_COLS].notna().all()


# ── holes after the research end (features.HOLDOUT, 2026-10-26) ────────────

HOLD = ms("2026-10-26")
BAR_VALUES = ["open", "close", "vol", "qv", "cnt", "tbv"]


@pytest.fixture(scope="module")
def late() -> Market:
    return Market(start="2026-10-01", end="2026-12-06", seed=1)


def holey(mk: Market, root: Path, upto_ms: int) -> S.Store:
    """A store seeded before ``upto_ms`` with bar holes on both sides of the research end, as the packet's 2026-10-28."""
    store = mk.seed(root, upto_ms)
    b = store.load_bars()
    gone = (b["open_time"] >= ms("2026-10-28")) & (b["open_time"] < ms("2026-10-29"))           # a day after the end
    gone |= (b["open_time"] >= ms("2026-10-20 05:00")) & (b["open_time"] < ms("2026-10-20 08:00"))   # three hours before
    b = b[~gone].copy()
    b.loc[b["open_time"].isin([ms("2026-10-29 05:00"), ms("2026-10-21 03:00")]), "n"] = 50    # short: one after, one before
    b.to_parquet(root / "bars.parquet", index=False)
    m = store.load_metrics()                                   # metrics holes before the end, inside the 29 days: kept
    m = m[~((m["t"] >= ms("2026-10-20 10:00")) & (m["t"] < ms("2026-10-20 10:30")))].copy()
    m.loc[m["t"] == ms("2026-10-22 07:35"), "count_long_short_ratio"] = np.nan
    m.to_parquet(root / "metrics.parquet", index=False)
    return store


def parity_frames(store: S.Store):
    """What --parity reads: bars and metrics before the end, funding settled by the end."""
    b, m, fu = store.frames(end=F.HOLDOUT)
    return b, m, fu[fu.index <= F.HOLDOUT]


def assert_same_frames(a, b):
    pd.testing.assert_frame_equal(a[0], b[0])
    pd.testing.assert_frame_equal(a[1], b[1])
    pd.testing.assert_series_equal(a[2], b[2])


def test_update_fills_bar_holes_after_the_research_end_once_and_never_before(late, tmp_path):
    store = holey(late, tmp_path, ms("2026-11-01"))
    old, before = store.load_bars(), parity_frames(store)
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    got = store.update(client(v), v.now)
    assert got["filled_bars"] == 25 and got["bars"] == 1 and got["filled_metrics"] == got["filled_funding"] == 0
    assert "fill_failed" not in got
    b = store.load_bars()
    assert b["open_time"].is_unique and b["open_time"].is_monotonic_increasing
    b = b.set_index("open_time")
    hole = list(range(ms("2026-10-28"), ms("2026-10-29"), H)) + [ms("2026-10-29 05:00")]
    assert (b.loc[hole, "src"] == 1).all() and (b.loc[hole, "n"] == 60).all()      # marked as any API bar
    np.testing.assert_allclose(b.loc[hole, BAR_VALUES].to_numpy(), late.bars.set_index("open_time").loc[hole, BAR_VALUES].to_numpy(),
                               rtol=1e-12)
    assert len(F.bars(store.load_bars()).loc[pd.Timestamp(HOLD, unit="ms", tz="UTC"):].dropna()) == len(b[b.index >= HOLD])
    # research rows untouched: the hole and the short hour before the end stay as they were
    now = store.load_bars()
    pd.testing.assert_frame_equal(now[now["open_time"] < HOLD].reset_index(drop=True), old[old["open_time"] < HOLD].reset_index(drop=True))
    assert not set(range(ms("2026-10-20 05:00"), ms("2026-10-20 08:00"), H)) & set(b.index)
    assert b.loc[ms("2026-10-21 03:00"), "n"] == 50
    assert_same_frames(before, parity_frames(store))
    filled = store.meta()["filled"]
    assert filled == [{"series": "bars", "from": "2026-10-28T00:00Z", "to": "2026-10-28T23:00Z", "count": 24, "at": "2026-11-01T01:15Z"},
                      {"series": "bars", "from": "2026-10-29T05:00Z", "to": "2026-10-29T05:00Z", "count": 1, "at": "2026-11-01T01:15Z"}]
    # once: the next update asks nothing before its own new bar, fills nothing and records nothing more
    n = len(v.calls)
    v.now += H
    again = store.update(client(v), v.now)
    assert again["filled_bars"] == 0 and again["bars"] == 1 and store.meta()["filled"] == filled
    assert [int(q["startTime"]) for p, q in v.calls[n:] if p == fx.KLINES] == [ms("2026-11-01 01:00")]


def more_holes(store: S.Store) -> None:
    """Add to a ``holey`` store six 5 m stamps (2026-10-30 10:00 to 10:25) and one settlement (10-27 08:00) after the end."""
    m = store.load_metrics()
    m[~((m["t"] >= ms("2026-10-30 10:00")) & (m["t"] < ms("2026-10-30 10:30")))].to_parquet(store.metrics_path, index=False)
    fu = store.load_funding()
    fu[fu["t"] != ms("2026-10-27 08:00") // 1000].to_parquet(store.funding_path, index=False)


def old_klines_fail(status: int):
    """The venue answers ``status`` to every klines call before 2026-11-01: the bars fill's, not the append's."""
    return lambda p, q: status if p == fx.KLINES and int(q["startTime"]) < ms("2026-11-01") else None


def test_a_failed_fill_leaves_its_file_as_it_was_the_other_fills_run_and_it_is_asked_again(late, tmp_path):
    store = holey(late, tmp_path, ms("2026-11-01"))
    more_holes(store)
    old = store.load_bars()
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    v.fail = old_klines_fail(503)
    got = store.update(client(v), v.now)
    assert got["fill_failed"] == ["bars"] and got["filled_bars"] == 0 and got["bars"] == 1   # the append still ran
    assert got["filled_metrics"] == 6 and got["filled_funding"] == 1                        # and so did the other fills
    b = store.load_bars()
    pd.testing.assert_frame_equal(b[b["open_time"] < ms("2026-11-01")].reset_index(drop=True), old)
    meta = store.meta()
    assert meta["last_fill_failure"] == {"at": "2026-11-01T01:15Z", "errors": {"bars": "FetchError"}}
    assert meta["updates"] == 1 and [r["series"] for r in meta["filled"]] == ["metrics", "funding"]
    assert not list(tmp_path.glob("*.tmp"))
    v.fail = None
    v.now += H
    got = store.update(client(v), v.now)
    assert got["filled_bars"] == 25 and "fill_failed" not in got
    assert "last_fill_failure" not in store.meta()                    # dropped once every fill ran
    assert (store.load_bars().set_index("open_time").loc[ms("2026-10-28"):ms("2026-10-28 23:00"), "src"] == 1).sum() == 24


def test_a_418_or_429_in_a_fill_stops_asking_and_is_raised_once_meta_is_saved(late, tmp_path):
    store = holey(late, tmp_path, ms("2026-11-01"))
    more_holes(store)
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    v.fail = old_klines_fail(429)
    with pytest.raises(S.FetchStop):
        store.update(client(v), v.now)
    assert v.calls[-1][0] == fx.KLINES and int(v.calls[-1][1]["startTime"]) == ms("2026-10-28")   # nothing asked after it
    assert store.load_bars()["open_time"].max() == ms("2026-11-01")                            # the append landed
    t = store.load_metrics()["t"]
    assert t.max() == ms("2026-11-01 01:10") and not set(range(ms("2026-10-30 10:00"), ms("2026-10-30 10:30"), M5)) & set(t)
    meta = store.meta()
    assert meta["last_fill_failure"] == {"at": "2026-11-01T01:15Z", "errors": {"bars": "FetchStop"}}
    assert meta["updates"] == 1 and "filled" not in meta
    v.fail = None
    v.now += H
    got = store.update(client(v), v.now)
    assert (got["filled_bars"], got["filled_metrics"], got["filled_funding"]) == (25, 6, 1)
    assert "last_fill_failure" not in store.meta()


def test_a_run_killed_in_the_fills_keeps_the_records_of_what_landed(late, tmp_path, monkeypatch):
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    v.hole = (ms("2026-11-01 00:30"), ms("2026-11-01 00:40"))           # the append finds a metrics gap
    gap = {"series": "metrics", "from": "2026-11-01T00:30Z", "to": "2026-11-01T00:40Z"}
    real, writes = S._write, []
    # killed at the append's metrics write: the gap it found is on file, and found again it is not recorded twice
    store = holey(late, tmp_path / "g", ms("2026-11-01"))

    def append_killed(df, path):
        if path.name == "metrics.parquet":
            raise Killed()
        real(df, path)

    monkeypatch.setattr(S, "_write", append_killed)
    with pytest.raises(Killed):
        store.update(client(v), v.now)
    monkeypatch.undo()
    assert store.meta()["gaps"] == [gap] and store.load_metrics()["t"].max() == ms("2026-10-31 23:55")
    got = store.update(client(v), v.now)
    assert got["gaps"] == 0 and store.meta()["gaps"] == [gap] and store.load_metrics()["t"].max() == ms("2026-11-01 01:10")
    # killed after the bars fill landed: its record and the append's gap are on file
    store = holey(late, tmp_path / "a", ms("2026-11-01"))

    def killed(*a, **k):
        raise Killed()

    monkeypatch.setattr(S.Store, "_fill_metrics", killed)
    with pytest.raises(Killed):
        store.update(client(v), v.now)
    monkeypatch.undo()
    meta = store.meta()
    assert not len(S.bar_holes(store.load_bars()))
    assert [(r["series"], r["from"], r["count"]) for r in meta["filled"]] == [("bars", "2026-10-28T00:00Z", 24),
                                                                              ("bars", "2026-10-29T05:00Z", 1)]
    assert meta["gaps"] == [gap]
    got = store.update(client(v), v.now)
    assert got["filled_bars"] == got["gaps"] == 0 and len(store.meta()["filled"]) == 2
    # killed between a fill's record and its rows: the record is on file, and the next update asks and records again
    store = holey(late, tmp_path / "b", ms("2026-11-01"))

    def write(df, path):
        writes.append(path.name)
        if writes.count("bars.parquet") == 2:          # the append's write, then the fill's
            raise Killed()
        real(df, path)

    monkeypatch.setattr(S, "_write", write)
    with pytest.raises(Killed):
        store.update(client(v), v.now)
    monkeypatch.undo()
    assert len(S.bar_holes(store.load_bars())) == 25
    assert [(r["series"], r["count"]) for r in store.meta()["filled"]] == [("bars", 24), ("bars", 1)]
    got = store.update(client(v), v.now)
    assert got["filled_bars"] == 25 and not len(S.bar_holes(store.load_bars()))
    assert [(r["series"], r["count"]) for r in store.meta()["filled"]] == [("bars", 24), ("bars", 1)] * 2
    # a write that fails takes its record back out
    store = holey(late, tmp_path / "c", ms("2026-11-01"))
    writes.clear()

    def broken(df, path):
        writes.append(path.name)
        if writes.count("bars.parquet") == 2:
            raise OSError("disk")
        real(df, path)

    monkeypatch.setattr(S, "_write", broken)
    got = store.update(client(v), v.now)
    assert got["fill_failed"] == ["bars"] and not store.meta().get("filled") and len(S.bar_holes(store.load_bars())) == 25
    assert store.meta()["last_fill_failure"]["errors"] == {"bars": "OSError"}


def test_a_fill_whose_record_cannot_be_saved_leaves_no_record_and_fails(late, tmp_path, monkeypatch):
    """The record save inside the fill fails once: no rows, no record in the update's last save of meta.json."""
    store = holey(late, tmp_path, ms("2026-11-01"))
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    real, refused = S.Store._save_meta, []

    def full_once(self, meta):
        if not refused and any(r["series"] == "bars" for r in meta.get("filled", [])):
            refused.append(1)
            raise OSError("disk")
        real(self, meta)

    monkeypatch.setattr(S.Store, "_save_meta", full_once)
    got = store.update(client(v), v.now)
    monkeypatch.undo()
    assert refused and got["fill_failed"] == ["bars"] and got["filled_bars"] == 0 and got["bars"] == 1
    assert len(S.bar_holes(store.load_bars())) == 25
    meta = store.meta()
    assert not meta.get("filled") and meta["last_fill_failure"]["errors"] == {"bars": "OSError"} and meta["updates"] == 1
    v.now += H
    got = store.update(client(v), v.now)
    assert got["filled_bars"] == 25 and "fill_failed" not in got
    assert [(r["series"], r["count"]) for r in store.meta()["filled"]] == [("bars", 24), ("bars", 1)]   # once


def test_a_failed_fill_write_takes_out_only_its_own_newest_records(late, tmp_path, monkeypatch):
    store = holey(late, tmp_path, ms("2026-11-01"))
    more_holes(store)
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    v.fail = old_klines_fail(503)                     # the bars fill fails; the metrics and funding fills land
    store.update(client(v), v.now)
    older = store.meta()["filled"]
    assert [(r["series"], r["count"]) for r in older] == [("metrics", 6), ("funding", 1)]
    v.fail = None
    v.now += H
    real, writes = S._write, []

    def broken(df, path):
        writes.append(path.name)
        if writes.count("bars.parquet") == 2:          # the append's write, then the bars fill's
            raise OSError("disk")
        real(df, path)

    monkeypatch.setattr(S, "_write", broken)
    got = store.update(client(v), v.now)
    monkeypatch.undo()
    assert got["fill_failed"] == ["bars"] and len(S.bar_holes(store.load_bars())) == 25
    assert store.meta()["filled"] == older            # the two older records stay; the bars fill's two are gone
    v.now += H
    assert store.update(client(v), v.now)["filled_bars"] == 25
    filled = store.meta()["filled"]
    assert filled[:2] == older and [(r["series"], r["count"]) for r in filled[2:]] == [("bars", 24), ("bars", 1)]


def test_a_gap_at_the_end_is_recorded_again_with_a_later_end_until_rows_arrive_after_it(late, tmp_path):
    store = late.seed(tmp_path, ms("2026-11-01"))
    v = Venue(late)
    v.hole = (ms("2026-11-01"), ms("2026-11-01 02:55"))     # no series serves these stamps, at any time
    for k in range(4):
        v.now = ms("2026-11-01 01:15") + k * H
        store.update(client(v), v.now)
    gaps = store.meta()["gaps"]
    # no row after it: each update records it again with the same start; the last record has its true end
    assert [(g["series"], g["from"], g["to"]) for g in gaps] == [("metrics", "2026-11-01T00:00Z", "2026-11-01T00:15Z"),
                                                                ("metrics", "2026-11-01T00:00Z", "2026-11-01T01:15Z"),
                                                                ("metrics", "2026-11-01T00:00Z", "2026-11-01T02:55Z")]
    assert store.load_metrics()["t"].max() == ms("2026-11-01 04:10")


def test_a_gap_found_changed_after_a_killed_run_is_recorded_again_and_the_first_record_stays(late, tmp_path, monkeypatch):
    """Killed after note() saved a partial record, before the rows: by the next update the late series served more."""
    taker = "sum_taker_long_short_vol_ratio"
    store = late.seed(tmp_path, ms("2026-11-01"))
    v = Venue(late)
    v.behind[fx.TAKER_RATIO] = 24                     # two hours late: more than PUBLISH_LAG_MS
    v.now = ms("2026-11-01 03:15")
    real = S._write

    def append_killed(df, path):
        if path.name == "metrics.parquet":
            raise Killed()
        real(df, path)

    monkeypatch.setattr(S, "_write", append_killed)
    with pytest.raises(Killed):
        store.update(client(v), v.now)
    monkeypatch.undo()
    first = {"series": "metrics_partial", "rows": 13, "from": "2026-11-01T01:15Z", "to": "2026-11-01T02:15Z"}
    assert store.meta()["gaps"] == [first] and store.load_metrics()["t"].max() == ms("2026-10-31 23:55")
    v.now += H
    got = store.update(client(v), v.now)
    later = {"series": "metrics_partial", "rows": 13, "from": "2026-11-01T02:15Z", "to": "2026-11-01T03:15Z"}
    assert got["gaps"] == 1 and store.meta()["gaps"] == [first, later]
    m = store.load_metrics().set_index("t")[taker]
    assert m.loc[ms("2026-11-01 01:15"):ms("2026-11-01 02:10")].notna().all()   # the first record's stamps but one, in full
    assert m.loc[ms("2026-11-01 02:15"):ms("2026-11-01 03:15")].isna().all()


def test_run_update_exits_1_when_a_fill_failed(late, tmp_path, monkeypatch, capsys):
    import types

    from tools.flowbtc import run as R
    holey(late, tmp_path, ms("2026-11-01"))
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    v.fail = old_klines_fail(503)
    real = fx.Client
    monkeypatch.setattr(fx, "Client", lambda: real(v, pace=0, sleep=lambda s: None))
    monkeypatch.setattr(R, "time", types.SimpleNamespace(time=lambda: v.now / 1000))
    assert R.main(["--root", str(tmp_path), "--update"]) == 1
    out = capsys.readouterr()
    assert json.loads(out.out)["update"]["fill_failed"] == ["bars"] and "fill failed for bars" in out.err
    v.fail = None
    v.now += H
    assert R.main(["--root", str(tmp_path), "--update"]) == 0
    assert json.loads(capsys.readouterr().out)["update"]["filled_bars"] == 25


def hold(path: Path):
    """Another open file holding ``path``'s lock, as a wrapper's ``exec 9>...; flock -n 9``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "w")
    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fh


def test_run_writes_the_store_only_under_the_runners_lock(late, tmp_path, monkeypatch, capsys):
    import types

    from tools.flowbtc import run as R
    lock = tmp_path / "logs" / ".perp_demo.lock"
    assert R.LOCK == lock and not lock.parent.exists()
    root = tmp_path / "store"
    holey(late, root, ms("2026-11-01"))
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    held = []

    def probe(p, q):                                   # at each venue call: can another open file take the lock?
        if not lock.exists():
            held.append(False)
            return None
        with open(lock, "a") as fh:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except BlockingIOError:
                held.append(True)
        return None

    v.fail = probe
    real = fx.Client
    monkeypatch.setattr(fx, "Client", lambda: real(v, pace=0, sleep=lambda s: None))
    monkeypatch.setattr(R, "time", types.SimpleNamespace(time=lambda: v.now / 1000))
    # nothing holds it: --update makes logs/, holds the lock through every call, and lets it go at the end
    assert R.main(["--root", str(root), "--update"]) == 0
    assert held and all(held) and lock.exists()
    hold(lock).close()
    capsys.readouterr()
    # a wrapper's run holds it: --update and --init-from write nothing and exit 3
    wrapper = hold(lock)
    files = {p.name: p.read_bytes() for p in root.iterdir()}
    n = len(v.calls)
    v.now += H
    assert R.main(["--root", str(root), "--update"]) == R.LOCK_HELD == 3
    out = capsys.readouterr()
    assert out.out == "" and f"{lock} is held" in out.err and "Nothing was written" in out.err
    assert len(v.calls) == n and {p.name: p.read_bytes() for p in root.iterdir()} == files
    seed = tmp_path / "seed"
    assert R.main(["--root", str(seed), "--init-from", str(tmp_path / "panel"), "--funding", str(tmp_path / "f.parquet")]) == 3
    assert not seed.exists()
    # the read-only modes take no lock
    monkeypatch.setattr(R, "parity", lambda store: {"n": 0})
    assert R.main(["--root", str(root), "--parity"]) == 0
    assert json.loads(capsys.readouterr().out) == {"parity": {"n": 0}}
    wrapper.close()
    assert R.main(["--root", str(root), "--update"]) == 0
    assert json.loads(capsys.readouterr().out)["update"]["bars"] == 1


def test_the_default_store_takes_the_wrappers_lock_file():
    """run.py and both wrappers lock the same file: <checkout>/logs/.perp_demo.lock, never one relative to the cwd."""
    repo = Path(__file__).resolve().parents[1]
    want = repo / "logs" / ".perp_demo.lock"
    assert S.ROOT == repo / "data" / "flowbtc"
    assert R.lock_for(S.ROOT) == want and LOCK_AS_SHIPPED == want and LOCK_AS_SHIPPED.is_absolute()
    for wrapper in ("run_perp_demo.sh", "run_flow_flush.sh"):
        text = (repo / "scripts" / "ops" / wrapper).read_text()
        assert 'ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"' in text      # the checkout root
        assert 'exec 9>"$ROOT/logs/.perp_demo.lock"\nflock -n 9 || exit 0' in text


def test_a_second_run_py_writer_is_refused_while_one_writes(late, tmp_path, monkeypatch, capsys):
    """The lock is exclusive: a second --update started while one is at the venue writes nothing and exits 3."""
    import types

    lock = tmp_path / "logs" / ".perp_demo.lock"
    first = R.take_lock(lock)
    assert first is not None and R.take_lock(lock) is None
    first.close()
    again = R.take_lock(lock)
    assert again is not None
    again.close()
    root = tmp_path / "store"
    holey(late, root, ms("2026-11-01"))
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    second: list[int] = []

    def start_a_second_writer(p, q):                   # once, at the first run's first venue call
        if not second:
            second.append(-1)
            second[0] = R.main(["--root", str(root), "--update"])
        return None

    v.fail = start_a_second_writer
    real = fx.Client
    monkeypatch.setattr(fx, "Client", lambda: real(v, pace=0, sleep=lambda s: None))
    monkeypatch.setattr(R, "time", types.SimpleNamespace(time=lambda: v.now / 1000))
    assert R.main(["--root", str(root), "--update"]) == 0
    out = capsys.readouterr()
    assert second == [R.LOCK_HELD] and f"{lock} is held" in out.err
    assert len(out.out.splitlines()) == 1 and json.loads(out.out)["update"]["filled_bars"] == 25   # only the first wrote


def test_a_store_in_another_checkout_takes_that_checkouts_lock(late, tmp_path, monkeypatch, capsys):
    """--root <checkout>/data/flowbtc locks <checkout>/logs/.perp_demo.lock, the file that checkout's wrappers lock."""
    import types

    main = tmp_path / "main"
    root = main / "data" / "flowbtc"
    lock = main / "logs" / ".perp_demo.lock"
    holey(late, root, ms("2026-11-01"))
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    real = fx.Client
    monkeypatch.setattr(fx, "Client", lambda: real(v, pace=0, sleep=lambda s: None))
    monkeypatch.setattr(R, "time", types.SimpleNamespace(time=lambda: v.now / 1000))
    # that checkout's hourly run is going: run.py from this checkout writes nothing there
    wrapper = hold(lock)
    files = {p.name: p.read_bytes() for p in root.iterdir()}
    assert R.main(["--root", str(root), "--update"]) == R.LOCK_HELD
    out = capsys.readouterr()
    assert out.out == "" and f"{lock} is held" in out.err
    assert not v.calls and {p.name: p.read_bytes() for p in root.iterdir()} == files and not R.LOCK.exists()
    fresh = tmp_path / "fresh"
    hold_fresh = hold(fresh / "logs" / ".perp_demo.lock")
    assert R.main(["--root", str(fresh / "data" / "flowbtc"), "--init-from", str(tmp_path / "panel"),
                   "--funding", str(tmp_path / "f.parquet")]) == R.LOCK_HELD
    assert not (fresh / "data").exists()
    hold_fresh.close()
    wrapper.close()
    assert R.main(["--root", str(root), "--update"]) == 0 and not R.LOCK.exists()
    assert json.loads(capsys.readouterr().out)["update"]["filled_bars"] == 25
    # which lock guards which root
    assert R.lock_for(root) == lock and R.lock_for(tmp_path / "store") == R.LOCK
    monkeypatch.chdir(main)
    assert R.lock_for(Path("data/flowbtc")) == lock


def test_fills_start_exactly_at_the_research_end(late, tmp_path):
    """The hour and the 5 m stamp at 2026-10-26 00:00 are filled; the hour 10-25 23:00 and the stamp 23:55 are research's."""
    store = late.seed(tmp_path, ms("2026-11-01"))
    b = store.load_bars()
    b = b[b["open_time"] != HOLD].copy()                                        # the first hour after the end: missing
    b.loc[b["open_time"] == HOLD - H, "n"] = 50                                 # the last research hour: short
    b.to_parquet(store.bars_path, index=False)
    m = store.load_metrics()
    m = m[~m["t"].isin([HOLD - M5, HOLD])].copy()                               # the stamps either side of the end: missing
    m.loc[m["t"] == HOLD - 2 * M5, "sum_taker_long_short_vol_ratio"] = np.nan   # a research cell: stays empty
    m.loc[m["t"] == HOLD + M5, "sum_taker_long_short_vol_ratio"] = np.nan       # the first cell after the end: filled
    m.to_parquet(store.metrics_path, index=False)
    before = parity_frames(store)
    v = Venue(late)
    v.now = ms("2026-11-01 01:15")
    got = store.update(client(v), v.now)
    assert (got["filled_bars"], got["filled_metrics"], got["filled_funding"]) == (1, 2, 0)
    b = store.load_bars().set_index("open_time")
    assert (b.loc[HOLD, "src"], b.loc[HOLD, "n"]) == (1, 60) and (b.loc[HOLD - H, "src"], b.loc[HOLD - H, "n"]) == (0, 50)
    m = store.load_metrics().set_index("t")
    ref = late.metrics.set_index("t")
    assert HOLD - M5 not in m.index and np.isnan(m.loc[HOLD - 2 * M5, "sum_taker_long_short_vol_ratio"])
    np.testing.assert_allclose(m.loc[[HOLD, HOLD + M5], F.METRIC_COLS].to_numpy(),
                               ref.loc[[HOLD, HOLD + M5], F.METRIC_COLS].to_numpy(), rtol=1e-12)
    assert_same_frames(before, parity_frames(store))
    # nothing before the end is even asked
    assert min(int(q["startTime"]) for p, q in v.calls if p == fx.KLINES) == HOLD
    assert min(int(q["startTime"]) + S.SERIES[p][2] for p, q in v.calls if p in S.SERIES) == HOLD
    assert [(r["series"], r["from"], r["count"]) for r in store.meta()["filled"]] == [("bars", "2026-10-26T00:00Z", 1),
                                                                                     ("metrics", "2026-10-26T00:00Z", 2)]


def test_hole_report_splits_at_the_research_end():
    hours = np.arange(HOLD - 4 * H, HOLD + 4 * H, H, dtype="int64")
    stamps = np.arange(HOLD - 4 * H, HOLD + 4 * H, M5, dtype="int64")
    fu = pd.DataFrame({"t": [ms(x) // 1000 for x in ("2026-10-25 08:00", "2026-10-26 16:00")]})   # 10-25 16:00, 10-26 00:00, 08:00

    def report(bar_gone=(), bar_short=(), m_gone=(), m_part=()):
        b = pd.DataFrame({"open_time": hours, "n": 60})
        b = b[~b["open_time"].isin(bar_gone)].copy()
        b.loc[b["open_time"].isin(bar_short), "n"] = 50
        m = pd.DataFrame({"t": stamps, **{c: 1.0 for c in F.METRIC_COLS}})
        m = m[~m["t"].isin(m_gone)].copy()
        m.loc[m["t"].isin(m_part), "count_long_short_ratio"] = np.nan
        return S.hole_report(b, m, fu)

    at_end = [{"from": "2026-10-26T00:00Z", "to": "2026-10-26T00:00Z", "hours": 1}]
    got = report(bar_gone=[HOLD - H, HOLD], m_gone=[HOLD - M5, HOLD], m_part=[HOLD - 2 * M5, HOLD + M5])
    assert (got["bars_missing_before_holdout"], got["bars_missing_after_holdout"]) == (1, 1)
    assert (got["bars_under_55_min_before_holdout"], got["bars_under_55_min_after_holdout"]) == (0, 0)
    assert got["bars_holes_after_holdout"] == at_end
    assert (got["metrics_missing_before_holdout"], got["metrics_missing_after_holdout"], got["metrics_partial_after_holdout"]) == (1, 1, 1)
    assert (got["funding_missing_before_holdout"], got["funding_missing_after_holdout"]) == (2, 1)
    got = report(bar_short=[HOLD - H, HOLD], m_part=[HOLD - M5, HOLD])
    assert (got["bars_missing_before_holdout"], got["bars_missing_after_holdout"]) == (0, 0)
    assert (got["bars_under_55_min_before_holdout"], got["bars_under_55_min_after_holdout"]) == (1, 1)
    assert got["bars_holes_after_holdout"] == at_end
    assert (got["metrics_missing_before_holdout"], got["metrics_missing_after_holdout"], got["metrics_partial_after_holdout"]) == (0, 0, 1)


def test_metric_and_funding_holes_after_the_research_end_are_filled_inside_the_reach(late, tmp_path):
    store = late.seed(tmp_path, ms("2026-12-05"))
    m = store.load_metrics()
    t = m["t"]
    drop = (t >= ms("2026-11-20 10:00")) & (t < ms("2026-11-20 12:00"))        # 24 stamps inside the reach
    drop |= (t >= ms("2026-11-20 13:00")) & (t < ms("2026-11-20 13:15"))       # 3 more, one call with the 24
    drop |= (t >= ms("2026-10-30 10:00")) & (t < ms("2026-10-30 11:00"))       # after the end, beyond the 29 days
    drop |= (t >= ms("2026-10-20 10:00")) & (t < ms("2026-10-20 11:00"))       # before the end
    m = m[~drop].copy()
    m.loc[m["t"] == ms("2026-11-25 07:35"), "count_long_short_ratio"] = np.nan    # one series empty on a stamp
    m.loc[m["t"] == ms("2026-10-21 07:35"), "count_long_short_ratio"] = np.nan    # before the end: stays empty
    m.loc[m["t"] == ms("2026-11-20 12:30"), "sum_open_interest"] *= 1.5           # a value on file inside the asked window
    m.to_parquet(tmp_path / "metrics.parquet", index=False)
    fu = store.load_funding()
    gone = [ms(x) // 1000 for x in ("2026-11-10 08:00",                                  # after the end
                                    "2026-10-25 16:00", "2026-10-26", "2026-10-26 08:00",  # one gap across the end
                                    "2026-10-20 08:00")]                                   # before
    fu[~fu["t"].isin(gone)].to_parquet(tmp_path / "funding.parquet", index=False)
    before = parity_frames(store)
    v = Venue(late)
    v.now = ms("2026-12-05 00:20")
    got = store.update(client(v), v.now)
    assert got["filled_metrics"] == 28 and got["filled_funding"] == 2 and got["filled_bars"] == 0
    new = store.load_metrics()
    assert new["t"].is_unique and new["t"].is_monotonic_increasing and new["t"].dtype == "int64"
    new = new.set_index("t")
    ref = late.metrics.set_index("t")
    filled = list(range(ms("2026-11-20 10:00"), ms("2026-11-20 12:00"), M5)) + list(range(ms("2026-11-20 13:00"), ms("2026-11-20 13:15"), M5))
    np.testing.assert_allclose(new.loc[filled, F.METRIC_COLS].to_numpy(), ref.loc[filled, F.METRIC_COLS].to_numpy(), rtol=1e-12)
    assert new.loc[ms("2026-11-25 07:35"), F.METRIC_COLS].notna().all()
    assert new.loc[ms("2026-11-20 12:30"), "sum_open_interest"] == pytest.approx(ref.loc[ms("2026-11-20 12:30"), "sum_open_interest"] * 1.5)
    assert not set(range(ms("2026-10-30 10:00"), ms("2026-10-30 11:00"), M5)) & set(new.index)       # beyond the reach
    assert not set(range(ms("2026-10-20 10:00"), ms("2026-10-20 11:00"), M5)) & set(new.index)       # before the end
    assert np.isnan(new.loc[ms("2026-10-21 07:35"), "count_long_short_ratio"])
    assert_same_frames(before, parity_frames(store))
    stats = [(p, int(q["startTime"])) for p, q in v.calls if p in S.SERIES]
    assert min(s for _, s in stats) >= v.now - S.METRICS_REACH_MS
    near = {p for p, s in stats if abs(s - ms("2026-11-25 07:35")) <= M5}
    assert near == {fx.GLOBAL_ACCOUNT_RATIO}                       # only the empty series is asked for that stamp
    fu = store.load_funding()["t"]
    assert fu.is_unique and fu.is_monotonic_increasing
    # the settlement at the end is the one research's last bar saw: research's, so it stays missing with those before it
    assert set(gone) - set(fu) == {ms(x) // 1000 for x in ("2026-10-20 08:00", "2026-10-25 16:00", "2026-10-26")}
    kinds = sorted((r["series"], r["from"], r["count"]) for r in store.meta()["filled"] if r["series"] == "funding")
    assert kinds == [("funding", "2026-10-26T08:00Z", 1), ("funding", "2026-11-10T08:00Z", 1)]
    assert min(int(q["startTime"]) for p, q in v.calls if p == fx.FUNDING) == HOLD + 1000    # nothing at or before the end is asked
    assert sorted(r["count"] for r in store.meta()["filled"] if r["series"] == "metrics") == [1, 3, 24]
    # once: nothing is asked again before the append's own window
    n = len(v.calls)
    v.now += M5
    again = store.update(client(v), v.now)
    assert again["filled_metrics"] == again["filled_funding"] == 0 and len(store.meta()["filled"]) == 5
    assert all(int(q["startTime"]) >= ms("2026-12-05 00:15") - M5 for p, q in v.calls[n:] if p in S.SERIES)
    assert not [q for p, q in v.calls[n:] if p == fx.FUNDING and int(q["startTime"]) < ms("2026-12-05")]


def test_init_from_reports_holes_before_and_after_the_research_end(tmp_path, capsys):
    from tools.flowbtc import run as R
    panel = tmp_path / "panel"
    panel.mkdir()
    t = np.arange(ms("2026-10-25 21:00"), ms("2026-10-26 06:00"), 60_000, dtype="int64")
    cut = ((t >= ms("2026-10-25 21:00")) & (t < ms("2026-10-25 21:06")))        # 54 minutes at 21:00, before the end
    cut |= (t >= ms("2026-10-25 22:00")) & (t < ms("2026-10-25 23:00"))        # an hour missing before
    cut |= (t >= ms("2026-10-26 02:00")) & (t < ms("2026-10-26 04:00"))        # two hours missing after
    cut |= (t >= ms("2026-10-26 04:00")) & (t < ms("2026-10-26 04:10"))        # 50 minutes at 04:00, after
    t = t[~cut]
    pd.DataFrame({"open_time": t, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 1.0, "quote_volume": 1.0,
                  "count": np.int64(2), "taker_buy_volume": 0.5}).to_parquet(panel / "k1m.parquet")
    st = np.arange(ms("2026-10-25 21:00"), ms("2026-10-26 06:00"), M5, dtype="int64")
    st = st[~(((st >= ms("2026-10-25 22:00")) & (st < ms("2026-10-25 22:15")))
              | ((st >= ms("2026-10-26 01:00")) & (st < ms("2026-10-26 01:25"))))]
    m5 = pd.DataFrame({"sum_open_interest": 1.0, "sum_toptrader_long_short_ratio": 1.0, "count_long_short_ratio": 1.0,
                       "sum_taker_long_short_vol_ratio": 1.0, "t": st})
    m5.loc[m5["t"] == ms("2026-10-26 03:05"), "count_long_short_ratio"] = np.nan
    m5.to_parquet(panel / "m5.parquet")
    fund = tmp_path / "BTC.parquet"
    # missing: 10-25 08:00 (before), 10-26 00:00 (at the end: the last research bar saw it, so before), 10-26 16:00 (after)
    pd.DataFrame({"t": [ms(x) // 1000 for x in ("2026-10-25", "2026-10-25 16:00", "2026-10-26 08:00", "2026-10-27")],
                  "rate": 1e-4}).to_parquet(fund)
    got = S.Store(tmp_path / "store").init_from(panel, fund)
    assert (got["bars_missing_before_holdout"], got["bars_under_55_min_before_holdout"]) == (1, 1)
    assert (got["bars_missing_after_holdout"], got["bars_under_55_min_after_holdout"]) == (2, 1)
    assert got["bars_holes_after_holdout"] == [{"from": "2026-10-26T02:00Z", "to": "2026-10-26T04:00Z", "hours": 3}]
    assert (got["metrics_missing_before_holdout"], got["metrics_missing_after_holdout"], got["metrics_partial_after_holdout"]) == (3, 5, 1)
    assert (got["funding_missing_before_holdout"], got["funding_missing_after_holdout"]) == (2, 1)
    assert S.Store(tmp_path / "store").meta()["seeded"]["bars_holes_after_holdout"] == got["bars_holes_after_holdout"]
    capsys.readouterr()
    assert R.main(["--root", str(tmp_path / "again"), "--init-from", str(panel), "--funding", str(fund)]) == 0
    out = capsys.readouterr()
    assert json.loads(out.out)["seeded"]["bars_missing_after_holdout"] == 2
    assert "holes after the research end" in out.err and '"bars_missing_after_holdout": 2' in out.err


def test_signal_waits_for_the_store_then_records_the_quarter_model_once(market, tmp_path):
    store = market.seed(tmp_path, ms("2021-01-03"))
    v = Venue(market)
    sig = Signal(tmp_path, client=lambda: client(v))
    day = ms("2021-01-03")
    assert set(sig.decide(day, record=True)["missing"]) == {"bars", "metrics", "funding"}
    v.now = day + H + 15 * 60_000
    sig.update(v.now)
    dry = sig.decide(day, record=False)
    assert dry["ok"] and not (tmp_path / "models").exists()          # a dry call writes nothing
    got = sig.decide(day, record=True)
    assert got == dry and got["model"] == "2021Q1"
    man = json.loads((tmp_path / "models" / "manifest.json").read_text())
    assert list(man) == ["2021Q1"]
    assert sig.decide(day, record=True) == got                       # loaded, not refitted
    # the call is the model on the 00:00 row of the features
    b, m, fu = store.frames()
    f = F.features(b, m, fu)
    mdl = M.load(tmp_path, "2021Q1")
    assert got["pred"] == M.predict(mdl, f.loc[[pd.Timestamp(day, unit="ms", tz="UTC")]])[0]
    assert got["long"] == (got["pred"] > mdl["threshold"])
    with pytest.raises(ValueError):
        sig.decide(day + H, record=False)


class Killed(BaseException):
    """A process that dies without running its except blocks."""


def test_a_run_killed_inside_record_refits_the_quarter_at_the_next_run(market, tmp_path, monkeypatch):
    """Killed between the record's two writes: the quarter's model must not be refused for the whole quarter."""

    market.seed(tmp_path, ms("2021-01-03"))
    v = Venue(market)
    sig = Signal(tmp_path, client=lambda: client(v))
    day = ms("2021-01-03")
    v.now = day + H + 15 * 60_000
    sig.update(v.now)
    real = Path.replace

    def replace(self, target):
        out = real(self, target)
        if Path(target).parent.name == "models":
            raise Killed()           # the first of the record's two files has landed; the process dies
        return out

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(Killed):
        sig.decide(day, record=True)
    monkeypatch.setattr(Path, "replace", real)
    got = sig.decide(day, record=True)
    assert got["ok"] and got["model"] == "2021Q1"
    assert M.load(tmp_path, "2021Q1") is not None             # recorded, and its hash matches
    assert sig.decide(day, record=True) == got


# ── parity with research (opt-in) ──────────────────────────────────────────

@pytest.mark.slow
def test_parity_with_combo_on_a_seeded_store():
    root = os.environ.get("FLOWBTC_PARITY_STORE")
    if not root or not S.Store(Path(root)).ready():
        pytest.skip("set FLOWBTC_PARITY_STORE to a store seeded from the packet")
    from tools.flowbtc.run import parity
    got = parity(S.Store(Path(root)))
    assert got["n"] == 1758 and got["net"] == pytest.approx(3.778035761550993, abs=0.01)
