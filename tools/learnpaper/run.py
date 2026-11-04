"""H-LEARN-11 forward paper log: one run.

Keeps hourly bars of the 20 coins from public mainnet klines, refits the frozen
model at each quarter start, and records every due daily decision: the bar
opening at 23:00 UTC is the decision bar, the position is held from the next
00:00 open for 24 h. The ledger is append only and holds predictions,
positions and open prices; the return is computed only at a read
(docs/operations/LEARN_PAPER_RUN.md). The status file shows counts only.

Paper only: no key, no order. Usage:
    python -m tools.learnpaper.run [--root DIR] [--init-from DIR ...]
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import pickle
import sys
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from tools.learnpaper import model as M
from tools.newperp import fetch as fx

ROOT = Path(__file__).resolve().parents[2]
HOUR_MS = 3_600_000
FIRST_DECISION = pd.Timestamp("2026-10-26 23:00", tz="UTC")   # the first bar no study read
HOLDOUT = pd.Timestamp("2026-10-26", tz="UTC")                # archive bars stop before this
KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
              "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]


# ── bars ───────────────────────────────────────────────────────────────────

def bars_path(root: Path, coin: str) -> Path:
    return root / "bars" / f"{coin}.parquet"


def _frame(rows: pd.DataFrame) -> pd.DataFrame:
    rows = rows.drop_duplicates("open_time").sort_values("open_time")
    out = rows[["open_time"] + M.BAR_COLS].astype(float)
    out["open_time"] = out["open_time"].astype("int64")
    return out.reset_index(drop=True)


def init_from_zips(root: Path, dirs: list[Path]) -> dict[str, int]:
    """Seed the bar store from monthly 1h kline zips (data.binance.vision), bars before HOLDOUT only."""
    counts = {}
    for coin in M.COINS:
        frames = []
        for d in dirs:
            for z in sorted((d / f"k_{coin}").glob("*.zip")):
                with zipfile.ZipFile(z) as zf:
                    (name,) = [n for n in zf.namelist() if n.endswith(".csv")]
                    raw = zf.read(name).decode()
                header = raw.split("\n", 1)[0].startswith("open_time")
                frames.append(pd.read_csv(io.StringIO(raw), header=0 if header else None, names=None if header else KLINE_COLS))
        if not frames:
            raise SystemExit(f"no kline zips for {coin}")
        df = _frame(pd.concat(frames))
        df = df[df["open_time"] < int(HOLDOUT.timestamp() * 1000)]
        p = bars_path(root, coin)
        p.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(p, index=False)
        counts[coin] = len(df)
    return counts


def update_bars(client: fx.Client, root: Path, now_ms: int) -> int:
    """Append every closed hourly bar after the last stored one. Returns bars added."""
    last_closed = now_ms // HOUR_MS * HOUR_MS          # bars opening before this are closed
    added = 0
    for coin in M.COINS:
        p = bars_path(root, coin)
        old = pd.read_parquet(p)
        start = int(old["open_time"].max()) + HOUR_MS
        if start >= last_closed:
            continue
        rows = fx.klines(client, f"{coin}USDT", "1h", start, last_closed)
        if not rows:
            continue
        new = pd.DataFrame([r[:len(KLINE_COLS)] for r in rows], columns=KLINE_COLS)
        df = _frame(pd.concat([old, _frame(new)]))
        tmp = p.with_suffix(".tmp")
        df.to_parquet(tmp, index=False)
        os.replace(tmp, p)
        added += len(new)
    return added


def load_bars(root: Path) -> dict[str, pd.DataFrame]:
    out = {}
    for coin in M.COINS:
        d = pd.read_parquet(bars_path(root, coin))
        d.index = pd.to_datetime(d["open_time"], unit="ms", utc=True)
        d = d[M.BAR_COLS]
        out[coin] = d.reindex(pd.date_range(d.index[0], d.index[-1], freq="h", tz="UTC"))
    return out


# ── models ─────────────────────────────────────────────────────────────────

def quarter_tag(q: pd.Timestamp) -> str:
    return f"{q.year}Q{(q.month - 1) // 3 + 1}"


def model_for(root: Path, bars: dict[str, pd.DataFrame], q: pd.Timestamp, log=print):
    """Load the quarter's model, checked against its recorded hash, or fit and record it."""
    tag = quarter_tag(q)
    mdir = root / "models"
    path, man_path = mdir / f"{tag}.pkl", mdir / "manifest.json"
    manifest = json.loads(man_path.read_text()) if man_path.exists() else {}
    if path.exists():
        blob = path.read_bytes()
        if manifest.get(tag, {}).get("sha256") != hashlib.sha256(blob).hexdigest():
            raise SystemExit(f"model {tag} does not match its recorded hash; refusing")
        rec = pickle.loads(blob)  # our own file, hash checked above
        return rec["model"], rec["cols"], tag
    last_bar = max(b.index[-1] for b in bars.values())
    if last_bar + pd.Timedelta(hours=1) < q:
        raise SystemExit(f"bars end {last_bar}, before the {tag} refit point")
    mdl, cols, n = M.fit(bars, q)
    blob = pickle.dumps({"model": mdl, "cols": cols})
    mdir.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    manifest[tag] = {"sha256": hashlib.sha256(blob).hexdigest(), "train_rows": n, "fitted": iso_now()}
    man_path.write_text(json.dumps(manifest, indent=1, sort_keys=True))
    log(f"fitted model {tag} on {n} rows")
    return mdl, cols, tag


# ── ledger ─────────────────────────────────────────────────────────────────

def load_ledger(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf8").splitlines()
    out = []
    for i, line in enumerate(lines):
        try:
            out.append(json.loads(line))
        except ValueError:
            if i < len(lines) - 1:
                raise
    return out


def append(path: Path, recs: list[dict]) -> None:
    if not recs:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf8") as f:
        for r in recs:
            f.write(json.dumps(r, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def iso(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%MZ")


def iso_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%MZ")


def due(now_ms: int, done: set[str]) -> list[pd.Timestamp]:
    """Decision bars whose entry bar (the next 00:00) has closed, not yet recorded."""
    now = pd.Timestamp(now_ms, unit="ms", tz="UTC")
    out, t = [], FIRST_DECISION
    while t + pd.Timedelta(hours=2) <= now:
        if iso(t) not in done:
            out.append(t)
        t += pd.Timedelta(days=1)
    return out


def decide(bars, feats, model, cols, t: pd.Timestamp) -> dict:
    entry = t + pd.Timedelta(hours=1)
    preds, opens, missing = {}, {}, []
    for c in M.COINS:
        f, b = feats[c], bars[c]
        if t not in f.index or entry not in b.index or not np.isfinite(b.at[entry, "open"]) or not np.isfinite(b.at[t, "close"]):
            missing.append(c)
            continue
        preds[c] = float(model.predict(f.loc[[t], cols])[0])
        opens[c] = float(b.at[entry, "open"])
    pos = M.positions(preds)
    return {"kind": "decision", "bar": iso(t), "entry": iso(entry), "pred": preds, "pos": pos,
            "entry_open": opens, "missing": missing}


def status(recs: list[dict]) -> dict:
    dec = [r for r in recs if r.get("kind") == "decision"]
    last = dec[-1] if dec else {}
    pos = list((last.get("pos") or {}).values())
    return {"as_of": iso_now(), "decisions": len(dec), "last_bar": last.get("bar"), "model": last.get("model"),
            "last_long": sum(p > 0 for p in pos), "last_short": sum(p < 0 for p in pos), "last_flat": sum(p == 0 for p in pos),
            "last_missing": len(last.get("missing") or [])}


def run(now_ms: int, client: fx.Client, root: Path, log=print) -> dict:
    added = update_bars(client, root, now_ms)
    path = root / "ledger.jsonl"
    recs = load_ledger(path)
    todo = due(now_ms, {r["bar"] for r in recs if r.get("kind") == "decision"})
    new = []
    if todo:
        bars = load_bars(root)
        feats = M.features(bars)
        for t in todo:
            mdl, cols, tag = model_for(root, bars, M.quarter_of(t), log)
            rec = decide(bars, feats, mdl, cols, t)
            rec["model"] = tag
            rec["recorded"] = iso_now()
            new.append(rec)
        append(path, new)
    st = status(recs + new)
    st.update(bars_added=added, recorded_this_run=len(new))
    tmp = root / ".status.json.tmp"
    tmp.write_text(json.dumps(st, indent=1, sort_keys=True), encoding="utf8")
    os.replace(tmp, root / "status.json")
    return st


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(ROOT / "data" / "learn_paper"))
    ap.add_argument("--init-from", nargs="+", metavar="DIR", help="seed the bar store from k_{COIN}/*.zip folders")
    args = ap.parse_args(argv)
    root = Path(args.root)
    if args.init_from:
        if any(bars_path(root, c).exists() for c in M.COINS):
            print("bar store exists; refusing to reseed", file=sys.stderr)
            return 2
        print(json.dumps({"seeded": init_from_zips(root, [Path(d) for d in args.init_from])}, sort_keys=True))
        return 0
    if not all(bars_path(root, c).exists() for c in M.COINS):
        print("no bar store; seed it with --init-from first", file=sys.stderr)
        return 2
    st = run(int(time.time() * 1000), fx.Client(), root)
    print(json.dumps(st, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
