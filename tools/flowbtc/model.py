"""Frozen model of H-FLOW-BTC-02: ridge on the FULL set, long-only, 24 h, decision on the 00:00 bar.

The research walk (scripts/combo.py ``walk``, ridge branch) as code the demo
book runs:

- ridge, alpha 10, on standardized features: the train median fills a gap,
  then the mean of the filled train is taken off and the train standard
  deviation (0 becomes 1) divides;
- training rows from 2020-09-01 whose 24 h label (open of t + 1 h to open of
  t + 25 h) ends by the quarter start;
- threshold: the 80th percentile of |prediction| on its own training rows;
- long iff the prediction is above the threshold, else flat (long-only);
- refit at each calendar quarter start; the decision bar's quarter picks the model.

A fitted model is a JSON file of numbers, recorded with its sha256 in
``models/manifest.json``; a file that no longer matches is refused. Any change
here changes the book; it needs the operator's ruling in
docs/operations/PERP_DEMO_RUNNER.md.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from tools.flowbtc import features as F

ALPHA = 10.0
QUANTILE = 0.8
H = 24                                                # hold, hours
DECISION_HOUR = 0                                     # the bar opening 00:00 UTC; entry at the 01:00 open
TRAIN_START = pd.Timestamp("2020-09-01", tz="UTC")
FIRST_QUARTER = pd.Timestamp("2022-01-01", tz="UTC")  # research's first out-of-sample quarter
SIDE_BPS = 5.5                                        # research cost a side


class ModelError(RuntimeError):
    pass


def quarter_of(t: pd.Timestamp) -> pd.Timestamp:
    return pd.Timestamp(year=t.year, month=(t.month - 1) // 3 * 3 + 1, day=1, tz="UTC")


def quarter_tag(q: pd.Timestamp) -> str:
    return f"{q.year}Q{(q.month - 1) // 3 + 1}"


def train_mask(idx: pd.DatetimeIndex, y: pd.Series, q: pd.Timestamp) -> np.ndarray:
    label_end = idx + pd.Timedelta(hours=1 + H)
    return np.asarray((idx >= TRAIN_START) & (label_end <= q) & y.notna().to_numpy())


def fit(f: pd.DataFrame, y: pd.Series, q: pd.Timestamp, cols: list[str] = F.FULL) -> dict:
    """The quarter's model as plain numbers: standardization, ridge weights, threshold."""
    tr = train_mask(f.index, y, q)
    if not tr.any():
        raise ModelError(f"no training rows before {q}")
    Xtr, ytr = f.loc[tr, cols], y[tr]
    med = Xtr.median()
    sd = Xtr.std().replace(0, 1)
    mu = Xtr.fillna(med).mean()
    if med.isna().any() or sd.isna().any():
        raise ModelError(f"a feature has no training values: {sorted(med.index[med.isna() | sd.isna()])}")
    z = ((Xtr.fillna(med) - mu) / sd).to_numpy()
    mdl = Ridge(alpha=ALPHA).fit(z, ytr.to_numpy())
    thr = float(np.quantile(np.abs(mdl.predict(z)), QUANTILE))
    return {"quarter": quarter_tag(q), "cols": list(cols), "alpha": ALPHA, "quantile": QUANTILE,
            "median": [float(v) for v in med], "mean": [float(v) for v in mu], "sd": [float(v) for v in sd],
            "coef": [float(v) for v in mdl.coef_], "intercept": float(mdl.intercept_), "threshold": thr,
            "train_rows": int(tr.sum()), "train_first": str(f.index[tr][0]), "train_last": str(f.index[tr][-1])}


def predict(model: dict, X: pd.DataFrame) -> np.ndarray:
    """Ridge predictions for the rows of ``X``: sklearn's X @ coef + intercept on combo's standardization."""
    cols = model["cols"]
    med = pd.Series(model["median"], index=cols)
    mu = pd.Series(model["mean"], index=cols)
    sd = pd.Series(model["sd"], index=cols)
    z = ((X[cols].fillna(med) - mu) / sd).to_numpy()
    return z @ np.asarray(model["coef"], dtype=float) + model["intercept"]


def is_long(model: dict, pred: float) -> bool:
    """Long-only: long when the prediction is above the threshold (combo: sign if |p| > thr, then max(., 0))."""
    return bool(abs(pred) > model["threshold"] and pred > 0)


# ── recorded fits ──────────────────────────────────────────────────────────

def _dumps(model: dict) -> bytes:
    return json.dumps(model, sort_keys=True, indent=1).encode()


def load(root: Path, tag: str) -> dict | None:
    """The recorded model of a quarter, checked against its hash; None if never fitted."""
    path, man = root / "models" / f"{tag}.json", root / "models" / "manifest.json"
    if not path.exists():
        return None
    manifest = json.loads(man.read_text()) if man.exists() else {}
    blob = path.read_bytes()
    if manifest.get(tag, {}).get("sha256") != hashlib.sha256(blob).hexdigest():
        raise ModelError(f"model {tag} does not match its recorded hash; refusing")
    return json.loads(blob)


def record(root: Path, model: dict) -> None:
    """Write a fitted model and its hash; an existing quarter is never overwritten.

    The hash goes into the manifest first, then the file. A run killed in
    between leaves an entry with no file: ``load`` finds no model, and the
    next run fits the same rows and records again. The other order would
    leave a file with no hash, which ``load`` refuses for the whole quarter.
    """
    tag = model["quarter"]
    mdir = root / "models"
    path, man = mdir / f"{tag}.json", mdir / "manifest.json"
    if path.exists():
        raise ModelError(f"model {tag} is already recorded")
    blob = _dumps(model)
    manifest = json.loads(man.read_text()) if man.exists() else {}
    mdir.mkdir(parents=True, exist_ok=True)
    manifest[tag] = {"sha256": hashlib.sha256(blob).hexdigest(), "train_rows": model["train_rows"],
                     "fitted": datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")}
    tmp = man.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    tmp.replace(man)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(blob)
    tmp.replace(path)


# ── research replica ───────────────────────────────────────────────────────

def walk(f: pd.DataFrame, y: pd.Series, cols: list[str] = F.FULL,
         first: pd.Timestamp = FIRST_QUARTER, end: pd.Timestamp = F.HOLDOUT) -> pd.DataFrame:
    """combo.walk for h 24 on the decision rows (hour_no % 24 == 0), one fit a quarter through ``fit``/``predict``."""
    idx = f.index
    quarters = pd.date_range(first, end - pd.Timedelta(days=1), freq="QS", tz="UTC")
    label_end = idx + pd.Timedelta(hours=1 + H)
    on_grid = F.hour_no(idx) % H == 0
    out = []
    for qi, q in enumerate(quarters):
        qend = quarters[qi + 1] if qi + 1 < len(quarters) else end
        te = np.asarray((idx >= q) & (idx < qend) & on_grid & y.notna().to_numpy() & (label_end < end))
        m = fit(f, y, q, cols)
        out.append(pd.DataFrame({"pred": predict(m, f.loc[te]), "fwd": y[te].to_numpy(), "thr": m["threshold"]},
                                index=idx[te]))
    return pd.concat(out)


def score_long_only(d: pd.DataFrame, side_bps: float = SIDE_BPS) -> dict:
    """combo.score, long-only: net in bps a decision, flat decisions included, cost on every change of position."""
    pos = np.where(np.abs(d["pred"]) > d["thr"], np.sign(d["pred"]), 0.0)
    pos = np.maximum(pos, 0.0)
    prev = np.concatenate([[0.0], pos[:-1]])
    net = (pos * d["fwd"].to_numpy() - side_bps / 1e4 * np.abs(pos - prev)) * 1e4
    return {"n": int(len(net)), "net": float(net.mean()), "exposure": float((pos != 0).mean())}
