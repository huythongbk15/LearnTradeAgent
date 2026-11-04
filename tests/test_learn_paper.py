"""H-LEARN-11 forward paper log: frozen features, due decisions, an append-only ledger, no look-ahead."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from tools.learnpaper import model as M
from tools.learnpaper import run as R
from tools.newperp import fetch as fx

HOUR = 3_600_000


def to_ms(idx):
    return ((idx - pd.Timestamp(0, tz="UTC")) // pd.Timedelta(milliseconds=1)).to_numpy().astype("int64")


def synth(start="2026-08-01", end="2026-10-26", seed=0) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, end, freq="h", tz="UTC", inclusive="left")
    out = {}
    for c in M.COINS:
        close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, len(idx))))
        out[c] = pd.DataFrame({"open": np.r_[100, close[:-1]], "high": close * 1.01, "low": close * 0.99, "close": close,
                               "volume": rng.uniform(1, 2, len(idx)), "quote_volume": rng.uniform(100, 200, len(idx)),
                               "count": rng.integers(10, 20, len(idx)).astype(float),
                               "taker_buy_volume": rng.uniform(0.4, 1.0, len(idx))}, index=idx)
    return out


def seed_store(root, bars):
    for c, b in bars.items():
        d = b.copy()
        d.insert(0, "open_time", to_ms(d.index))
        R.bars_path(root, c).parent.mkdir(parents=True, exist_ok=True)
        d.reset_index(drop=True).to_parquet(R.bars_path(root, c), index=False)


class ConstModel:
    def __init__(self, v):
        self.v = v

    def predict(self, X):
        return np.full(len(X), self.v)


def test_features_use_only_past_bars():
    bars = synth()
    t = pd.Timestamp("2026-10-20 23:00", tz="UTC")
    full = M.features(bars)
    cut = M.features({c: b[b.index <= t] for c, b in bars.items()})
    for c in ("LTC", "HBAR"):
        a, b = full[c].loc[t], cut[c].loc[t]
        assert np.allclose(a.to_numpy(dtype=float), b.to_numpy(dtype=float), equal_nan=True)


def test_positions_trade_only_past_the_round_trip():
    assert M.positions({"A": 0.0021, "B": -0.0025, "C": 0.0019, "D": -0.002}) == {"A": 1, "B": -1, "C": 0, "D": 0}


def test_quarter_of():
    assert M.quarter_of(pd.Timestamp("2026-10-26 23:00", tz="UTC")) == pd.Timestamp("2026-10-01", tz="UTC")
    assert M.quarter_of(pd.Timestamp("2027-03-31 23:00", tz="UTC")) == pd.Timestamp("2027-01-01", tz="UTC")


def test_due_waits_for_the_entry_bar_to_close():
    t0 = R.FIRST_DECISION
    ms = lambda ts: int(ts.timestamp() * 1000)  # noqa: E731
    assert R.due(ms(t0 + pd.Timedelta(hours=1, minutes=59)), set()) == []
    assert R.due(ms(t0 + pd.Timedelta(hours=2)), set()) == [t0]
    later = R.due(ms(t0 + pd.Timedelta(days=2, hours=3)), {R.iso(t0)})
    assert later == [t0 + pd.Timedelta(days=1), t0 + pd.Timedelta(days=2)]


def test_decide_records_entry_open_and_missing_coins():
    bars = synth(end="2026-10-28")
    bars["LTC"].loc[pd.Timestamp("2026-10-27 00:00", tz="UTC"), "open"] = np.nan
    feats = M.features(bars)
    cols = list(feats["BCH"].columns)
    rec = R.decide(bars, feats, ConstModel(-0.003), cols, R.FIRST_DECISION)
    assert rec["missing"] == ["LTC"] and "LTC" not in rec["pos"]
    assert set(rec["pos"].values()) == {-1}
    assert rec["entry_open"]["BCH"] == bars["BCH"].at[pd.Timestamp("2026-10-27 00:00", tz="UTC"), "open"]


def fake_transport(bars):
    def t(url, timeout):
        from urllib.parse import parse_qs, urlsplit
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        b = bars[q["symbol"].removesuffix("USDT")]
        ms = to_ms(b.index)
        sel = b[(ms >= int(q["startTime"])) & (ms <= int(q["endTime"]))].iloc[: int(q["limit"])]
        rows = [[int(m), *map(str, (r.open, r.high, r.low, r.close, r.volume)), 0, str(r.quote_volume),
                 int(r["count"]), str(r.taker_buy_volume), "0", "0"] for m, (_, r) in zip(to_ms(sel.index), sel.iterrows())]
        return 200, json.dumps(rows).encode()
    return t


def test_run_appends_due_decisions_once_and_shows_counts(tmp_path, monkeypatch):
    allbars = synth(end="2026-10-30")
    seed_store(tmp_path, {c: b[b.index < R.HOLDOUT] for c, b in allbars.items()})
    monkeypatch.setattr(M, "fit", lambda bars, q: (ConstModel(0.003), list(M.features(bars)["BCH"].columns), 7))
    client = fx.Client(fake_transport(allbars), pace=0, sleep=lambda s: None)
    now = int(pd.Timestamp("2026-10-29 12:00", tz="UTC").timestamp() * 1000)
    st = R.run(now, client, tmp_path, log=lambda *_: None)
    assert st["recorded_this_run"] == 3 and st["decisions"] == 3 and st["last_long"] == 20
    recs = R.load_ledger(tmp_path / "ledger.jsonl")
    assert [r["bar"] for r in recs] == ["2026-10-26T23:00Z", "2026-10-27T23:00Z", "2026-10-28T23:00Z"]
    assert all(r["model"] == "2026Q4" for r in recs)
    assert set(json.loads((tmp_path / "status.json").read_text())) >= {"decisions", "last_long", "last_short"}
    assert R.run(now, client, tmp_path, log=lambda *_: None)["recorded_this_run"] == 0


def test_a_changed_model_file_is_refused(tmp_path, monkeypatch):
    bars = synth(end="2026-10-26")
    monkeypatch.setattr(M, "fit", lambda b, q: (ConstModel(0.0), ["r1"], 1))
    q = pd.Timestamp("2026-10-01", tz="UTC")
    R.model_for(tmp_path, bars, q, log=lambda *_: None)
    (tmp_path / "models" / "2026Q4.pkl").write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="hash"):
        R.model_for(tmp_path, bars, q, log=lambda *_: None)


def test_the_coins_are_open_to_the_client():
    from tools.selection import config as sel
    assert sel.sealed_hits([f"{c}USDT" for c in M.COINS]) == []
