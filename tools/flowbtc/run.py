"""H-FLOW-BTC-02 data store: seed, update, parity with research, feed check.

Public data only: no key, no order. It reads packet files, so run it as a file
with -I (python -I tools/flowbtc/run.py ...):
    run.py [--root DIR] --init-from PANEL_DIR --funding PARQUET
    run.py [--root DIR] --update
    run.py [--root DIR] --parity
    run.py [--root DIR] --check-feed [--days 30]

--init-from prints the seed's counts with its holes before and after the
research end (2026-10-26): hours missing or under 55 minutes, 5 m stamps
missing or partly empty, funding settlements missing. --update fills those
after the research end; the "filled_*" counts say how many hours, 5 m stamps
and settlements it filled. A fill that failed is listed in "fill_failed" and
--update then exits 1; a 418 or 429 stops it with an error.

--init-from and --update write the store, so they first take the runners'
lock, exclusive and without waiting, as the hourly and 5-minute wrappers do.
For a store at <checkout>/data/flowbtc (the default) it is that checkout's
logs/.perp_demo.lock, whichever checkout run.py sits in; for any other
--root, the one under run.py's own checkout. While a wrapper's run or another
writing run.py holds it they write nothing and exit 3; while they hold it,
the wrappers' runs skip. --parity and --check-feed only read and take no lock.

--parity replays the research walk (2022-01-01 to 2026-10-25, decisions on
the 00:00 bar, long-only, 5.5 bps a side) on the store, through the same
fit and predict the demo book uses. It must give combo.json's
"h24|ridge|FULL|long". --check-feed compares the last ~30 days of the public
endpoints with the seeded packet rows on the stamps both hold: counts, the
largest relative difference and the stamp offset that lines them up. It
prints no price.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from pathlib import Path
from typing import IO

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[2]
if not __package__:   # run as a file, e.g. python -I tools/flowbtc/run.py: the repository root only
    sys.path[:0] = [str(REPO)]

from tools.flowbtc import features as F  # noqa: E402
from tools.flowbtc import model as M  # noqa: E402
from tools.flowbtc import store as S  # noqa: E402
from tools.newperp import fetch as fx  # noqa: E402

DAY_MS = 24 * F.H_MS
OFFSETS_MIN = (-10, -5, 0, 5, 10)
# The wrappers' lock (scripts/ops/run_perp_demo.sh, run_flow_flush.sh: exec 9>"$ROOT/logs/.perp_demo.lock"
# at the checkout root): the hourly run writes the store under it. Tests point LOCK elsewhere.
LOCK_NAME = Path("logs") / ".perp_demo.lock"
LOCK = REPO / LOCK_NAME
LOCK_HELD = 3   # exit code: a run holds the lock, nothing was written


def lock_for(root: Path) -> Path:
    """The runners' lock that guards the store at ``root``.

    A store at ``<checkout>/data/flowbtc`` is the one that checkout's hourly
    run writes, so its lock is ``<checkout>/logs/.perp_demo.lock``, whichever
    checkout run.py sits in. Any other root takes LOCK, this checkout's.
    The path is not resolved: a symlinked ``data`` keeps its checkout.
    """
    r = root.absolute()
    return r.parents[1] / LOCK_NAME if r.parts[-2:] == ("data", "flowbtc") else LOCK


def take_lock(path: Path) -> IO[str] | None:
    """``path`` locked exclusive without waiting: the open file holding the lock, or None while another holds it.

    Only the writing modes take it, never Store.update: the book calls update
    inside the hourly run, whose wrapper already holds this lock on its own
    open file, so a lock taken there would always be refused.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a", encoding="utf8")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        fh.close()
        return None
    return fh


def parity(store: S.Store) -> dict:
    b, m, fu = store.frames(end=F.HOLDOUT)
    f = F.features(b, m, fu)
    y = F.targets(b, M.H)
    d = M.walk(f, y)
    out = M.score_long_only(d)
    out.update(hours=len(b), first=str(b.index[0]), last=str(b.index[-1]), quarters=len({(t.year, (t.month - 1) // 3) for t in d.index}))
    return out


def _rel(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(a - b) / np.maximum(np.abs(b), 1e-300)


def _cmp(api: dict[int, float], seeded: pd.Series, configured_ms: int) -> dict:
    """Line API rows up with seeded rows at each offset (vision stamp = API stamp + offset).

    Reports the offset with the smallest median relative difference, and the
    configured offset of store.SERIES. A seeded value at or below 0 is counted
    apart (the features treat it as unknown).
    """
    rows = []
    for off in OFFSETS_MIN:
        ts = np.array([t for t in api if t + off * 60_000 in seeded.index], dtype="int64")
        if not len(ts):
            continue
        a = np.array([api[t] for t in ts])
        s = seeded.loc[ts + off * 60_000].to_numpy(dtype=float)
        ok = np.isfinite(a) & np.isfinite(s) & (s > 0)
        rel = _rel(a[ok], s[ok])
        rows.append({"offset_min": off, "compared": int(ok.sum()), "seeded_not_positive": int((s <= 0).sum()),
                     "exact": int((rel < 1e-9).sum()), "within_1e-3": int((rel < 1e-3).sum()),
                     "median_rel": float(np.median(rel)), "max_rel": float(rel.max())})
    if not rows:
        return {"compared": 0}
    best = min(rows, key=lambda r: r["median_rel"])
    return {**best, "configured_offset_min": configured_ms // 60_000,
            "configured_is_best": best["offset_min"] == configured_ms // 60_000}


def check_feed(store: S.Store, client: fx.Client, now_ms: int, days: int = 30) -> dict:
    start = (now_ms - days * DAY_MS) // F.H_MS * F.H_MS
    out: dict = {"window": [S._iso(start), S._iso(now_ms)], "metrics": {}}
    m = store.load_metrics().set_index("t")
    for path, (field, col, off) in S.SERIES.items():
        rows = fx.stats(client, path, S.SYMBOL, "5m", start, now_ms)
        api = {int(r["timestamp"]): float(r[field]) for r in rows}
        stamps = np.array(sorted(api))
        seeded = m[col].dropna()
        res = _cmp(api, seeded, off)
        res.update(api_rows=len(api), api_first=S._iso(int(stamps[0])) if len(stamps) else None,
                   api_last=S._iso(int(stamps[-1])) if len(stamps) else None,
                   api_off_grid=int((stamps % S.M5_MS != 0).sum()) if len(stamps) else 0,
                   seeded_in_window=int(((seeded.index >= start) & (seeded.index < now_ms)).sum()))
        out["metrics"][col] = res
    # hourly bars: public 1 h klines against the hours built from vision 1 m klines
    bars = store.load_bars()
    seeded = bars[(bars["src"] == 0) & (bars["open_time"] >= start)].set_index("open_time")
    if len(seeded):
        api = store.fetch_bars(client, start, int(seeded.index.max()) + F.H_MS).set_index("open_time")
        both = seeded.index.intersection(api.index)
        res = {"api_rows": len(api), "seeded_rows": len(seeded), "compared": len(both),
               "seeded_under_60_min": int((seeded.loc[both, "n"] < 60).sum())}
        for c in ("open", "close", "vol", "qv", "cnt", "tbv"):
            rel = _rel(api.loc[both, c].to_numpy(float), seeded.loc[both, c].to_numpy(float))
            res[c] = {"max_rel": float(rel.max()) if len(rel) else None, "exact": int((rel < 1e-9).sum())}
        full = both[seeded.loc[both, "n"].to_numpy() == 60]
        res["full_hours_exact_all"] = int(np.all(np.stack([
            _rel(api.loc[full, c].to_numpy(float), seeded.loc[full, c].to_numpy(float)) < 1e-9
            for c in ("open", "close", "vol", "qv", "cnt", "tbv")]), axis=0).sum()) if len(full) else 0
        res["full_hours"] = len(full)
        out["bars"] = res
    # funding: the API's settlement time in ms against the packet's seconds
    fu = store.load_funding().set_index("t")["rate"]
    rows = fx.funding(client, S.SYMBOL, start, now_ms)
    raw_ms = np.array([t for t, _ in rows], dtype="int64")
    api = {t // 1000: r for t, r in rows}
    both = sorted(set(api) & set(fu.index))
    rel = _rel(np.array([api[t] for t in both]), fu.loc[both].to_numpy(float)) if both else np.array([])
    out["funding"] = {"api_rows": len(api), "compared": len(both), "exact": int((rel < 1e-12).sum()),
                      "max_rel": float(rel.max()) if len(rel) else None,
                      "api_ms_off_the_second": int((raw_ms % 1000 != 0).sum()),
                      "api_seconds_off_the_hour": int(((raw_ms // 1000) % 3600 != 0).sum()),
                      "seeded_in_window": int(((fu.index * 1000 >= start) & (fu.index * 1000 < now_ms)).sum())}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=str(S.ROOT))
    ap.add_argument("--init-from", metavar="PANEL_DIR", help="seed from flow_btc_20261104_01/panel (k1m.parquet, m5.parquet)")
    ap.add_argument("--funding", metavar="PARQUET", help="with --init-from: perp_funding_20261102/parsed/BTC.parquet")
    ap.add_argument("--update", action="store_true", help="append closed public data")
    ap.add_argument("--parity", action="store_true", help="replay the research walk on the store")
    ap.add_argument("--check-feed", action="store_true", help="compare the public endpoints with the seeded rows")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)
    if args.init_from and not args.funding:
        print("--init-from needs --funding", file=sys.stderr)
        return 2
    if not (args.init_from or args.update):   # --parity and --check-feed only read
        return _run(args)
    path = lock_for(Path(args.root))
    lock = take_lock(path)
    if lock is None:
        print(f"{path} is held: an hourly or 5-minute perp demo run, or another run.py writer, is going, and the "
              "hourly run writes this store. Nothing was written; run this again after that run ends.", file=sys.stderr)
        return LOCK_HELD
    with lock:
        return _run(args)


def _run(args: argparse.Namespace) -> int:
    store = S.Store(Path(args.root))
    if args.init_from:
        if store.exists():
            print("store exists; refusing to reseed", file=sys.stderr)
            return 2
        got = store.init_from(Path(args.init_from), Path(args.funding))
        print(json.dumps({"seeded": got}, sort_keys=True))
        after = {k: got[k] for k in ("bars_missing_after_holdout", "bars_under_55_min_after_holdout",
                                     "metrics_missing_after_holdout", "metrics_partial_after_holdout",
                                     "funding_missing_after_holdout") if got[k]}
        if after:
            print(f"holes after the research end {json.dumps(after, sort_keys=True)}: --update fills them "
                  "(5 m metrics only inside the last 29 days)", file=sys.stderr)
        return 0
    if not store.ready():
        print("no store; seed it with --init-from first", file=sys.stderr)
        return 2
    now_ms = int(time.time() * 1000)
    rc = 0
    if args.update:
        got = store.update(fx.Client(), now_ms)
        print(json.dumps({"update": got}, sort_keys=True))
        if got.get("fill_failed"):
            print(f"fill failed for {', '.join(got['fill_failed'])}: those files stay as they were; the next --update "
                  "asks again", file=sys.stderr)
            rc = 1
    if args.parity:
        print(json.dumps({"parity": parity(store)}, sort_keys=True))
    if args.check_feed:
        print(json.dumps({"check_feed": check_feed(store, fx.Client(), now_ms, args.days)}, sort_keys=True, indent=1))
    return rc


if __name__ == "__main__":
    sys.exit(main())
