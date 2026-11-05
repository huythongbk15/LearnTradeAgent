"""Parity of the live FLOW01 rule with the H-FLOW-BTC-01 research cell, on the research panel.

    python -I tools/flowflush/parity.py --panel data/flow_btc_20261104_01/panel [--align] [--delays]

Runs ``rules.features``, ``rules.is_event`` and ``rules.decluster`` (the functions the live feed and book use)
over ``panel/k1m.parquet`` and ``panel/m5.parquet`` and reproduces the primary cell of
``scripts/measure.py``: entry at the 1m open at t + 5 min, exit at the 1m open 4 h later, net = gross - 11 bps;
train 2020-09-01 to 2024-01-01 (research: n 125, net +19.9), test 2024-01-01 to 2026-10-26 (n 29, +24.2).
Nothing at or after the holdout start (2026-10-26) is read, as in measure.py.

``--align`` compares the public ``/futures/data/openInterestHist`` 5m series and the 1m klines with the panel
on the overlap the API still serves (about 30 days back, cut at the holdout start), through the NL-P
allowlisted client: the matched stamps, the shift that lines them up and the max relative difference.

``--delays`` describes the same events entered later than t + 5 min (first numbers, unverified).
Research description, not advice.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import polars as pl

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.flowflush import rules as R  # noqa: E402

TRAIN = ("2020-09-01", "2024-01-01")
TEST = ("2024-01-01", "2026-10-26")
HOLDOUT = "2026-10-26"
RESEARCH = {"train": (125, 19.905222912455777), "test": (29, 24.157231028378376)}   # train.json / test.json, LF 60 0.01 h4
DAY_MS = 86_400_000


def ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp() * 1000)


def load(panel: Path):
    stop = ms(HOLDOUT)
    k = (pl.scan_parquet(panel / "k1m.parquet").select("open_time", "open", "close")
         .filter(pl.col("open_time") < stop).sort("open_time").collect())
    m = (pl.scan_parquet(panel / "m5.parquet").select("t", "sum_open_interest")
         .filter(pl.col("t") < stop).sort("t").collect())
    return (k["open_time"].to_numpy().astype(np.int64), k["open"].to_numpy(), k["close"].to_numpy(),
            m["t"].to_numpy().astype(np.int64), m["sum_open_interest"].to_numpy().astype(float))


def cell(t, ev, g, exit_t, lo, hi, *, live_order=False):
    """n and mean net of the cell in [lo, hi). Research order: select the window and a finite outcome, then
    decluster. Live order: decluster every event first (the book cannot see the outcome), then select."""

    stop = ms(HOLDOUT)
    win = (t >= ms(lo)) & (t < ms(hi))
    fin = np.isfinite(g) & (exit_t < stop)
    if live_order:
        idx = np.flatnonzero(ev)
        idx = idx[R.decluster(t[idx])]
        idx = idx[win[idx] & fin[idx]]
    else:
        idx = np.flatnonzero(win & ev & fin)
        idx = idx[R.decluster(t[idx])]
    net = g[idx] - R.COST_BPS
    return len(idx), float(net.mean()) if len(idx) else float("nan"), idx


def outcome(t, k_open, k_o, delay_ms):
    entry = t + delay_ms
    g = np.log(R.open_at(entry + R.HOLD_MS, k_open, k_o) / R.open_at(entry, k_open, k_o)) * 1e4
    return g, entry + R.HOLD_MS


def first_hourly_run(t: np.ndarray, minute: int = 15) -> np.ndarray:
    """The first hh:15 at or after t + 5 min."""

    h = R.MIN_MS * 60
    base = (t + R.ENTRY_DELAY_MS) // h * h + minute * R.MIN_MS
    return np.where(base >= t + R.ENTRY_DELAY_MS, base, base + h)


def _cmp(a: np.ndarray, b: np.ndarray) -> dict:
    ok = np.isfinite(a) & np.isfinite(b)
    rel = np.abs(a[ok] / b[ok] - 1.0)
    return {"matched": int(ok.sum()), "equal": int((rel == 0).sum()),
            "max_rel": float(rel.max()) if ok.any() else None, "median_rel": float(np.median(rel)) if ok.any() else None}


def align(panel: Path, k_open, k_c, m_t, m_oi, now_ms: int) -> dict:
    """The endpoint's OI stamps and 1m bars against the panel, on the overlap before the holdout.

    Reports: OI by stamp shift; where in time each series' snapshot sits (implied price value / OI against
    the 1m close ending at stamp + d min); and the live path end to end (``feed.look_from_rows`` on endpoint
    rows against ``rules.features`` on the panel, stamp by stamp).
    """

    from tools.flowflush import feed as F
    from tools.newperp import fetch as nf

    client = nf.Client()
    lo = (now_ms - 29 * DAY_MS) // R.STAMP_MS * R.STAMP_MS
    hi = min(ms(HOLDOUT), int(m_t[-1]) + 1)
    iso = lambda x: datetime.fromtimestamp(x / 1000, UTC).isoformat(timespec="minutes")  # noqa: E731
    out: dict = {"window": [iso(lo), iso(hi)]}
    if lo >= hi:
        return {**out, "error": "no overlap"}
    rows = nf.stats(client, nf.OPEN_INTEREST_HIST, R.SYMBOL, "5m", lo, hi + F.OI_LABEL_SHIFT_MS)
    a_t = np.array([int(r["timestamp"]) for r in rows], dtype=np.int64)
    a_v = np.array([float(r["sumOpenInterest"]) for r in rows], dtype=float)
    a_val = np.array([float(r["sumOpenInterestValue"]) for r in rows], dtype=float)
    out["api_rows"] = len(a_t)
    out["api_off_grid"] = int((a_t % R.STAMP_MS != 0).sum())
    # endpoint row T against the archive row create_time T + k x 5 min
    out["oi_by_shift_min"] = {k * 5: _cmp(a_v, R.oi_at(a_t + k * R.STAMP_MS, m_t, m_oi)) for k in range(-3, 4)}
    feed_map = R.oi_at(np.array([F.api_to_research(int(t)) for t in a_t], dtype=np.int64), m_t, m_oi)
    out["oi_feed_mapping"] = _cmp(a_v, feed_map)
    rel = np.abs(a_v / feed_map - 1.0)
    bad = a_t[np.isfinite(rel) & (rel > 0)]
    days: dict[str, int] = {}
    for t in bad:
        days[iso(int(t))[:10]] = days.get(iso(int(t))[:10], 0) + 1
    out["oi_feed_mapping"]["unequal_by_day"] = days
    # when was each snapshot taken: implied price against the 1m close ending at stamp + d min
    pv = pl.scan_parquet(panel / "m5.parquet").select("t", "sum_open_interest", "sum_open_interest_value") \
        .filter((pl.col("t") >= lo) & (pl.col("t") < hi)).sort("t").collect()
    pt = pv["t"].to_numpy().astype(np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        p_px = pv["sum_open_interest_value"].to_numpy() / pv["sum_open_interest"].to_numpy()
        a_px = a_val / a_v

    def snap(ts, px):
        med = {d: _cmp(px, R.close_ending_at(ts + d * R.MIN_MS, k_open, k_c))["median_rel"] for d in range(-10, 11)}
        best = min((d for d in med if med[d] is not None), key=lambda d: med[d])
        return {"best_d_min": best, "median_rel_bps_at_best": round(med[best] * 1e4, 3),
                "median_rel_bps_at_0": round(med[0] * 1e4, 3)}

    out["snapshot_time"] = {"api_stamp": snap(a_t, a_px), "archive_create_time": snap(pt, p_px)}
    # the live path end to end: endpoint klines and OI through feed.look_from_rows
    kr = nf.klines(client, R.SYMBOL, "1m", lo - R.MIN_MS, hi)      # no bar at or after the holdout start
    look, _ = F.look_from_rows(hi, kr, rows, lo)
    st = np.array([s.t for s in look.stamps], dtype=np.int64)
    live_r = np.array([s.ret60 for s in look.stamps])
    live_d = np.array([s.doi60 for s in look.stamps])
    live_e = np.array([s.event for s in look.stamps], dtype=bool)
    pr, pd_ = R.features(st, k_open, k_c, m_t, m_oi)
    pe = R.is_event(pr, pd_)
    both = np.isfinite(live_r) & np.isfinite(pr) & np.isfinite(live_d) & np.isfinite(pd_)
    out["live_path"] = {
        "stamps": len(st), "both_finite": int(both.sum()),
        "ret60_max_abs_diff": float(np.abs(live_r[both] - pr[both]).max()) if both.any() else None,
        "doi60_max_abs_diff": float(np.abs(live_d[both] - pd_[both]).max()) if both.any() else None,
        "events_live": int(live_e.sum()), "events_panel": int(pe.sum()),
        "event_flags_differ": int((live_e != pe).sum()),
    }
    out["klines"] = _cmp(np.array([float(r[4]) for r in kr]), R._at(k_open, k_c, np.array([int(r[0]) for r in kr])))
    # publication: the newest OI stamp the endpoint serves now
    now2 = int(time.time() * 1000)
    newest = nf.stats(client, nf.OPEN_INTEREST_HIST, R.SYMBOL, "5m", now2 - 30 * R.MIN_MS, now2 + 1)
    if newest:
        last = int(newest[-1]["timestamp"])
        out["publication"] = {"asked_at": datetime.fromtimestamp(now2 / 1000, UTC).isoformat(timespec="seconds"),
                              "newest_stamp": datetime.fromtimestamp(last / 1000, UTC).isoformat(),
                              "lag_s": round((now2 - last) / 1000, 1)}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--panel", required=True, type=Path)
    ap.add_argument("--train-json", type=Path, help="train.json of the packet, to check the frozen thresholds")
    ap.add_argument("--align", action="store_true", help="compare the public API with the panel (network)")
    ap.add_argument("--delays", action="store_true", help="describe later entries (first numbers, unverified)")
    args = ap.parse_args(argv)

    k_open, k_o, k_c, m_t, m_oi = load(args.panel)
    report: dict = {"panel": str(args.panel), "klines": len(k_open), "stamps": len(m_t)}
    tj = args.train_json or args.panel.parent / "train.json"
    if tj.exists():
        th = json.loads(tj.read_text())["thresholds"]["60|0.01"]
        report["thresholds_exact"] = th["rlo"] == R.RLO and th["dlo"] == R.DLO
    ret, doi = R.features(m_t, k_open, k_c, m_t, m_oi)
    ev = R.is_event(ret, doi)
    report["events_raw"] = int(ev.sum())
    g, exit_t = outcome(m_t, k_open, k_o, R.ENTRY_DELAY_MS)
    ok = True
    for name, (lo, hi) in (("train", TRAIN), ("test", TEST)):
        n, net, _ = cell(m_t, ev, g, exit_t, lo, hi)
        rn, rnet = RESEARCH[name]
        n2, net2, _ = cell(m_t, ev, g, exit_t, lo, hi, live_order=True)
        same = n == rn and abs(net - rnet) < 1e-9
        ok &= same
        report[name] = {"n": n, "net_bps": round(net, 4), "research_n": rn, "research_net_bps": round(rnet, 4),
                        "exact": same, "live_order": {"n": n2, "net_bps": round(net2, 4)}}
    if args.delays:
        rows = {}
        for d in (5, 6, 7, 10, 15, 20):
            gd, xd = outcome(m_t, k_open, k_o, d * R.MIN_MS)
            rows[f"t+{d}m"] = {w: dict(zip(("n", "net_bps"), [round(x, 2) if isinstance(x, float) else x
                                                             for x in cell(m_t, ev, gd, xd, lo, hi)[:2]]))
                               for w, (lo, hi) in (("train", TRAIN), ("test", TEST))}
        # first hourly :15 run: same events as research, entered later
        entry = first_hourly_run(m_t)
        gh = np.log(R.open_at(entry + R.HOLD_MS, k_open, k_o) / R.open_at(entry, k_open, k_o)) * 1e4
        rows["first_hh15"] = {w: dict(zip(("n", "net_bps"), [round(x, 2) if isinstance(x, float) else x
                                                            for x in cell(m_t, ev, gh, entry + R.HOLD_MS, lo, hi)[:2]]))
                              for w, (lo, hi) in (("train", TRAIN), ("test", TEST))}
        report["delays"] = rows
    if args.align:
        try:
            report["align"] = align(args.panel, k_open, k_c, m_t, m_oi, int(time.time() * 1000))
        except Exception as exc:     # the parity result stands without the network
            report["align"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    report["parity"] = "exact" if ok else "DIFFERS"
    print(json.dumps(report, indent=1, sort_keys=True))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
