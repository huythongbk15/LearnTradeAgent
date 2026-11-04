"""Frozen features and model of H-LEARN-11 (data/learn_candle_20261104_01/PROTOCOL.md).

Any change here changes the paper book; it needs the operator's ruling in
docs/operations/LEARN_PAPER_RUN.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

COINS = ("LTC", "BCH", "XLM", "FIL", "AAVE", "ETC", "UNI", "ATOM", "ALGO", "XTZ",
         "VET", "THETA", "CRV", "COMP", "SNX", "SAND", "MANA", "AXS", "CHZ", "HBAR")
H = 24                      # hold, hours
DECISION_HOUR = 23          # the decision bar opens at 23:00 UTC; entry at the 00:00 open
COST_BPS = 10.0             # a side
THRESHOLD = 2 * COST_BPS / 1e4
TRAIN_START = pd.Timestamp("2021-12-01", tz="UTC")
GBM = dict(max_iter=300, learning_rate=0.05, max_leaf_nodes=15, min_samples_leaf=1000, l2_regularization=1.0, random_state=11)
BAR_COLS = ["open", "high", "low", "close", "volume", "quote_volume", "count", "taker_buy_volume"]


def coin_features(b: pd.DataFrame) -> pd.DataFrame:
    """Features at the close of each hourly bar, past data only. ``b`` is on a gap-filled hourly UTC grid."""
    f = pd.DataFrame(index=b.index)
    lc = np.log(b["close"])
    for n in (1, 4, 12, 24, 72, 168):
        f[f"r{n}"] = lc.diff(n)
    r1 = lc.diff()
    f["vol24"] = r1.rolling(24).std()
    f["vol168"] = r1.rolling(168).std()
    f["volratio"] = np.log(f["vol24"] / f["vol168"])
    f["range24"] = ((b["high"] - b["low"]) / b["close"]).rolling(24).mean()
    hi, lo = b["high"].rolling(168).max(), b["low"].rolling(168).min()
    f["pos168"] = (b["close"] - lo) / (hi - lo)
    qv24, cnt24 = b["quote_volume"].rolling(24).sum(), b["count"].rolling(24).sum()
    f["qvz"] = np.log(qv24) - np.log(qv24.rolling(720, min_periods=600).median())
    f["cntz"] = np.log(cnt24) - np.log(cnt24.rolling(720, min_periods=600).median())
    for n in (4, 24):
        f[f"tk{n}"] = b["taker_buy_volume"].rolling(n).sum() / b["volume"].rolling(n).sum()
    size = qv24 / cnt24
    f["sizez"] = np.log(size) - np.log(size.rolling(720, min_periods=600).median())
    h = f.index.hour
    f["hs"], f["hc"], f["dow"] = np.sin(2 * np.pi * h / 24), np.cos(2 * np.pi * h / 24), f.index.dayofweek
    return f.replace([np.inf, -np.inf], np.nan)


def features(bars: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    feats = {c: coin_features(b) for c, b in bars.items()}
    r24 = pd.DataFrame({c: f["r24"] for c, f in feats.items()})
    r168 = pd.DataFrame({c: f["r168"] for c, f in feats.items()})
    v168 = pd.DataFrame({c: f["vol168"] for c, f in feats.items()})
    m24, m168 = r24.mean(axis=1), r168.mean(axis=1)
    rk24, rkv = r24.rank(axis=1, pct=True), v168.rank(axis=1, pct=True)
    for c, f in feats.items():
        f["mkt24"], f["mkt168"] = m24.reindex(f.index), m168.reindex(f.index)
        f["rank24"], f["rankvol"] = rk24[c].reindex(f.index), rkv[c].reindex(f.index)
    return feats


def target(bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    opens = pd.DataFrame({c: b["open"] for c, b in bars.items()})
    return opens.shift(-(1 + H)) / opens.shift(-1) - 1


def fit(bars: dict[str, pd.DataFrame], quarter_start: pd.Timestamp):
    """The quarter's model: pooled rows from TRAIN_START whose label ends by the quarter start."""
    from sklearn.ensemble import HistGradientBoostingRegressor

    feats, y = features(bars), target(bars)
    rows = []
    for c, f in feats.items():
        d = f.copy()
        d["y"] = y[c].reindex(f.index)
        rows.append(d)
    big = pd.concat(rows)
    big = big[big.index >= TRAIN_START]
    label_end = big.index + pd.Timedelta(hours=1 + H)
    tr = (label_end <= quarter_start) & big["y"].notna().to_numpy()
    cols = [c for c in big.columns if c != "y"]
    ytr = big.loc[tr, "y"]
    ytr = ytr.clip(ytr.quantile(0.01), ytr.quantile(0.99))
    model = HistGradientBoostingRegressor(**GBM).fit(big.loc[tr, cols], ytr)
    return model, cols, int(tr.sum())


def positions(preds: dict[str, float]) -> dict[str, int]:
    return {c: (int(np.sign(p)) if abs(p) > THRESHOLD else 0) for c, p in preds.items()}


def quarter_of(ts: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=ts.year, month=3 * ((ts.month - 1) // 3) + 1, day=1, tz="UTC")
