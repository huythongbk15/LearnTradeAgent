"""H-FLOW-BTC-01 live rule: frozen constants, features, events, declustering, the feed and the parity cell. No network."""
from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

from tools.flowflush import feed as F
from tools.flowflush import parity as P
from tools.flowflush import rules as R
from tools.newperp import fetch as nf

MIN = R.MIN_MS
T = 1_793_000_000_000 // R.STAMP_MS * R.STAMP_MS      # a research stamp on the 5-minute grid
PACKET = Path(__file__).resolve().parents[1] / "data" / "flow_btc_20261104_01"


def test_thresholds_are_the_frozen_train_floats():
    assert (R.RLO, R.DLO) == (-0.021138149153270704, -0.03324631085383747)
    assert R.CLUSTER_MS == 5 * MIN + 4 * 60 * MIN and R.ENTRY_DELAY_MS == 5 * MIN and R.W_MS == 60 * MIN
    if (PACKET / "train.json").exists():      # the git-ignored packet, when present
        th = json.loads((PACKET / "train.json").read_text())["thresholds"]["60|0.01"]
        assert (th["rlo"], th["dlo"]) == (R.RLO, R.DLO)


def test_features_use_the_bar_ending_at_t_and_unknown_oi():
    k_open = np.arange(T - 70 * MIN, T + 5 * MIN, MIN, dtype=np.int64)
    close = 100.0 + np.arange(len(k_open), dtype=float)
    oi_t = np.array([T - 60 * MIN, T, T + 5 * MIN], dtype=np.int64)
    oi_v = np.array([200.0, 150.0, 0.0])
    ret, doi = R.features(np.array([T, T + 5 * MIN]), k_open, close, oi_t, oi_v)
    c = dict(zip(k_open.tolist(), close.tolist()))
    assert ret[0] == np.log(c[T - MIN] / c[T - 61 * MIN])           # the bars opening at t - 1 and t - 61 min
    assert doi[0] == np.log(150.0 / 200.0)
    assert math.isnan(doi[1])                                       # OI 0 is unknown, and t - 55 min is missing
    # a missing bar is unknown too
    ret2, _ = R.features(np.array([T]), k_open[k_open != T - MIN], close[k_open != T - MIN], oi_t, oi_v)
    assert math.isnan(ret2[0])


def test_event_is_inclusive_at_both_thresholds_and_needs_both_finite():
    r = np.array([R.RLO, R.RLO, R.RLO + 1e-12, np.nan, -1.0])
    d = np.array([R.DLO, R.DLO + 1e-12, R.DLO, -1.0, np.nan])
    assert R.is_event(r, d).tolist() == [True, False, False, False, False]


def test_decluster_needs_t_past_the_previous_t_plus_five_minutes_plus_the_hold():
    ts = np.array([T, T + 5 * MIN, T + R.CLUSTER_MS, T + R.CLUSTER_MS + R.STAMP_MS, T + 2 * R.CLUSTER_MS])
    assert R.decluster(ts).tolist() == [True, False, False, True, False]
    assert R.clustered(T + R.CLUSTER_MS, T) and not R.clustered(T + R.CLUSTER_MS + 1, T) and not R.clustered(T, None)


class Mainnet:
    """Public 1m klines and 5m OI. A long flush at research stamp T: BTC falls 3% over the hour to T, and
    the OI snapshot taken at T + 5 min (stamped T + 5 min by the endpoint) is 5% below the one an hour
    before. The endpoint publishes a stamp ``lag_s`` seconds after it."""

    def __init__(self, now_ms: int, lag_s: float = 10.0):
        self.now = now_ms
        self.lag = lag_s * 1000
        self.calls: list[tuple[str, dict]] = []

    @staticmethod
    def close(m: int) -> float:       # close of the bar opening at m
        if m < T - 61 * MIN:
            return 100_000.0
        if m < T - MIN:
            return 100_000.0 - 3_000.0 * (m - (T - 61 * MIN)) / (60 * MIN)
        return 97_000.0

    @staticmethod
    def oi(stamp: int) -> float:      # the endpoint's row stamped `stamp`
        return 10_000.0 if stamp <= T else 9_500.0

    def __call__(self, url, timeout):
        nf.check_url(url)
        p = urlsplit(url)
        q = {k: v[0] for k, v in parse_qs(p.query).items()}
        self.calls.append((p.path, q))
        a, b = int(q["startTime"]), int(q["endTime"])
        if p.path == nf.KLINES:
            last = self.now // MIN * MIN          # the forming bar
            rows = [[m, str(self.close(m - MIN)), "0", "0", str(self.close(m)), "1", m + MIN - 1, "1"]
                    for m in range(a - a % MIN, min(b, last) + 1, MIN) if m >= a]
            return 200, json.dumps(rows[: int(q["limit"])]).encode()
        assert p.path == nf.OPEN_INTEREST_HIST and q["period"] == "5m"
        rows = [{"symbol": "BTCUSDT", "timestamp": s, "sumOpenInterest": str(self.oi(s)),
                 "sumOpenInterestValue": str(self.oi(s) * 1e5)}
                for s in range(a - a % R.STAMP_MS + (R.STAMP_MS if a % R.STAMP_MS else 0), b + 1, R.STAMP_MS)
                if s + self.lag <= self.now]
        return 200, json.dumps(rows[-int(q["limit"]):][::-1]).encode()   # newest first, as the venue may


def feed_at(now_ms: int, lag_s: float = 10.0) -> tuple[F.Feed, Mainnet]:
    m = Mainnet(now_ms, lag_s)
    return F.Feed(nf.Client(m, pace=0, sleep=lambda s: None)), m


def test_feed_relabels_endpoint_stamps_and_flags_the_flush_once_published():
    feed, m = feed_at(T + 5 * MIN + 5_000)               # row T + 5 min not out yet
    look = feed.look(m.now)
    assert look.stamps and look.stamps[-1].t == T - 5 * MIN and not any(s.event for s in look.stamps)
    assert look.covered_from == F.grid(m.now - F.LOOKBACK_MS) + R.W_MS
    feed, m = feed_at(T + 5 * MIN + 15_000)
    look = feed.look(m.now)
    last = look.stamps[-1]
    assert last.t == T and last.event
    assert last.ret60 == pytest.approx(math.log(97_000 / 100_000)) and last.doi60 == pytest.approx(math.log(0.95))
    assert [s.t for s in look.stamps if s.event] == [T]
    assert all(s.t % R.STAMP_MS == 0 for s in look.stamps) and [s.t for s in look.stamps] == sorted(s.t for s in look.stamps)
    paths = [c[0] for c in m.calls]
    assert paths == [nf.KLINES, nf.OPEN_INTEREST_HIST]       # one look, two calls
    # the research entry price is in the look; an older minute is asked for
    assert feed.open_at(T + 5 * MIN) == Mainnet.close(T + 4 * MIN) and len(m.calls) == 2
    assert feed.open_at(T - 10 * 60 * MIN) == 100_000.0 and len(m.calls) == 3


def test_feed_never_uses_the_forming_bar():
    rows = [[t, "1", "1", "1", str(1.0 + i), "1", t + MIN - 1, "1"] for i, t in enumerate(range(T - 70 * MIN, T + MIN, MIN))]
    oi = [{"timestamp": s, "sumOpenInterest": "5"} for s in range(T - 60 * MIN, T + 10 * MIN, R.STAMP_MS)]
    look, opens = F.look_from_rows(T + 30_000, rows, oi, T - 120 * MIN)
    assert look.newest_bar == T and look.stamps[-1].t == T          # the bar ending at T is final: T has started
    look, _ = F.look_from_rows(T - 30_000, rows[:-1], oi, T - 120 * MIN)
    assert look.stamps[-1].t == T - 5 * MIN                          # the bar ending at T is still forming
    assert opens[T] == 1.0


def test_parity_cell_orders_selection_then_declustering_like_measure():
    t = np.array([0, 1, 2, 3, 100, 101]) * R.STAMP_MS + P.ms("2021-01-01")
    ev = np.array([True, True, False, True, True, True])
    g = np.array([np.nan, 30.0, 0.0, 50.0, 20.0, 10.0])
    exit_t = t + R.HOLD_MS
    n, net, idx = P.cell(t, ev, g, exit_t, "2020-09-01", "2024-01-01")
    assert n == 2 and idx.tolist() == [1, 4] and net == pytest.approx((30 + 20) / 2 - R.COST_BPS)
    n2, _, idx2 = P.cell(t, ev, g, exit_t, "2020-09-01", "2024-01-01", live_order=True)
    assert idx2.tolist() == [4]        # the unpriced first event still anchors the cluster in the book
    assert P.first_hourly_run(np.array([P.ms("2021-01-01") + 10 * MIN]))[0] == P.ms("2021-01-01") + 15 * MIN
    assert P.first_hourly_run(np.array([P.ms("2021-01-01") + 11 * MIN]))[0] == P.ms("2021-01-01") + 75 * MIN


def test_parity_run_reproduces_a_synthetic_panel(tmp_path, monkeypatch, capsys):
    pl = pytest.importorskip("polars")
    panel = tmp_path / "panel"
    panel.mkdir()
    t0 = P.ms("2021-03-01")
    mins = np.arange(t0 - 2 * 60 * MIN, t0 + 6 * 60 * MIN, MIN, dtype=np.int64)
    close = np.where(mins < t0 - 61 * MIN, 100.0, np.where(mins < t0 - MIN, 100.0 - 3.0 * (mins - (t0 - 61 * MIN)) / (60 * MIN), 97.0))
    opens = np.concatenate([[100.0], close[:-1]])
    pl.DataFrame({"open_time": mins, "open": opens, "close": close}).write_parquet(panel / "k1m.parquet")
    st = np.arange(t0 - 2 * 60 * MIN, t0 + 6 * 60 * MIN, R.STAMP_MS, dtype=np.int64)
    pl.DataFrame({"t": st, "sum_open_interest": np.where(st <= t0 - 60 * MIN, 100.0, 90.0)}).write_parquet(panel / "m5.parquet")
    # events at t0 - 15 min .. t0; the first is kept: entry at the open of t0 - 10 min (97.5), exit 4 h later (97)
    expected = math.log(97.0 / 97.5) * 1e4 - R.COST_BPS
    monkeypatch.setattr(P, "RESEARCH", {"train": (1, expected), "test": (0, float("nan"))})
    P.main(["--panel", str(panel), "--train-json", str(tmp_path / "none.json")])
    rep = json.loads(capsys.readouterr().out)
    assert rep["train"]["exact"] and rep["train"]["n"] == 1 and rep["test"]["n"] == 0
    assert rep["train"]["live_order"]["n"] == 1 and rep["events_raw"] == 4
