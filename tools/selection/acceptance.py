"""Acceptance of the port: proof that tools.selection is the study, then the measure of the masked book.

Replay, on the UNMASKED path: a panel from the seed matrices up to 2025-07-01, the 32 candidates, the series, the 65 lists of the primary, against reference.json, and cell by cell against the study's files when --study names them. Cut checks: the same panel cut at a Monday, as a weekly run sees it. Masked: the seed step into a temporary store, the loader, the forward cost rule, and the four book measure on W1 to W4 that the page asks for. It is a new variant and replaces nothing.

Nothing here loads a bar at or after 2025-07-01. reference.json is written once from the study's own files by --write-reference; it is never built from this package. The unmasked path computes the twelve masked coins on discovery, the one exception to the mask, to prove the port. So its report stays out of the selection root, next to the study's seed by default.

    python -m tools.selection.acceptance --study <folder with library/ and selector/> --write-reference
    python -m tools.selection.acceptance [--study <folder>] [--out <file>]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np

from tools.selection import config, selector as sel
from tools.selection import library as lib
from tools.selection import panel as pn
from tools.selection.config import ts

PRIMARY = "4w_90d_top3"
BEST = "BEST_SO_FAR"
CUT_LISTS = (30, 64)                # lists re-derived from a panel cut at their own Monday


def _sha(a) -> str:
    return hashlib.sha256(np.asarray(a).tobytes()).hexdigest()


def _reference_path(seed: Path) -> Path:
    return seed / "reference.json"


def write_reference(study: Path, seed: Path) -> Path:
    """Hash the study's own outputs into seed/reference.json, so acceptance still works when the scratch folder is gone."""
    study = Path(study)
    W = np.load(study / "library" / "weights_disc.npy", mmap_mode="r")
    z = np.load(study / "library" / "series_disc.npz", allow_pickle=False)
    p = np.load(study / "library" / "panel_disc.npz", allow_pickle=False)
    r = np.load(study / "selector" / "real_disc.npz", allow_pickle=False)
    if [str(x) for x in z["names"]] != lib.NAMES:
        raise ValueError("the study's candidates differ from the frozen names")
    pnames = [str(x) for x in r["names"]]
    ref = {
        "source": "outputs of selector_20261101_01, hashed from the study's files, not from this package",
        "end": config.DISCOVERY_END,
        "stamps": int(W.shape[1]),
        "coins": int(W.shape[2]),
        "t_sha256": _sha(z["t"]),
        "h_sha256": _sha(z["h"]),
        "R_f32_sha256": _sha(p["R"]),
        "c_f32_sha256": _sha(p["c"]),
        "Mem_sha256": _sha(p["Mem"]),
        "candidates": {},
        "portfolios": {},
    }
    for i, name in enumerate(lib.NAMES):
        ref["candidates"][name] = {
            "weights_f32_sha256": _sha(W[i]),
            **{f"{k}_f64_sha256": _sha(z[k][:, i]) for k in ("gross", "net", "cost", "turn", "expo", "nexp", "gross_open")},
        }
    for name in (PRIMARY, BEST):
        i = pnames.index(name)
        ref["portfolios"][name] = {
            "days": [int(x) for x in r[f"days_{name}"]],
            "lists": [sel.slot_names(row) for row in r[f"ch_{name}"]],
            "scores_f64_sha256": _sha(r[f"sc_{name}"]),
            **{f"{k}_f64_sha256": _sha(r[k][:, i]) for k in ("gross", "net", "cost")},
        }
    i = pnames.index("EW")
    ref["portfolios"]["EW"] = {f"{k}_f64_sha256": _sha(r[k][:, i]) for k in ("gross", "net", "cost")}
    path = _reference_path(seed)
    path.write_text(json.dumps(ref, indent=1, ensure_ascii=False) + "\n", encoding="utf8")
    return path


def _cells(a: np.ndarray, b: np.ndarray) -> dict:
    """Cells that differ, exact comparison, and the largest gap."""
    a, b = np.asarray(a), np.asarray(b)
    if a.shape != b.shape:
        return {"cells": int(a.size), "diff": -1, "max_abs": None}
    d = a != b
    return {"cells": int(a.size), "diff": int(d.sum()), "max_abs": float(np.abs(a[d].astype(np.float64) - b[d].astype(np.float64)).max()) if d.any() else 0.0}


def _build(panel: pn.Panel, end=None, live: bool = False) -> tuple[lib.Series, np.ndarray, lib.Data]:
    """Candidates and series, weights kept as the float32 block the study stored."""
    d = lib.Data(panel, end, live=live)
    W = np.zeros((32, d.S, d.N), np.float32)

    def sink(name: str, w: np.ndarray) -> None:
        W[lib.NAMES.index(name)] = w.astype(np.float32)

    return lib.build_series(d, on_weights=sink), W, d


def _portfolio(bk: sel.Book, ch: np.ndarray, days: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    starts = bk.stamp_of_day(days)
    wp = sel.path(bk, ch, starts)
    g = sel.gross_path(bk, ch, starts)
    c = lib.cost_on_weights(wp, bk.c[:bk.S])
    return g, c, g - c, np.abs(wp).sum(1)


def replay(ref: dict, seed: Path, study: Path | None, end: str, cuts=CUT_LISTS) -> dict:
    """The study's universe, the study's stamp cost. Every comparison is exact."""
    out: dict = {}
    p = pn.seed_panel(end, masked=False, seed=seed)
    d0 = lib.Data(p)
    out["stamps"], out["coins"], out["bars_to"] = d0.S, d0.N, d0.T
    out["first_stamp"], out["last_stamp"] = str(np.datetime64(int(d0.t[0]), "s")), str(np.datetime64(int(d0.t[-1]), "s"))
    z = np.load(seed / "stamps_seed.npz", allow_pickle=False)
    pos = np.searchsorted(z["t"], d0.t)
    out["t_equal"] = bool((z["t"][pos] == d0.t).all() and (z["hours"][pos] == d0.h).all()) and _sha(d0.t) == ref["t_sha256"] and _sha(d0.h) == ref["h_sha256"]
    out["mem"] = _cells(d0.Mem, z["Mem"][pos])
    out["mem"]["ref_hash_equal"] = _sha(d0.Mem) == ref["Mem_sha256"]
    rule = lib.tier_cost(d0, p.tier)
    out["cost_rule_vs_seed"] = _cells(np.where(np.isnan(rule), lib.MISSING_COST, rule), d0.cost)
    del d0, rule, z
    s, W, d = _build(p)
    del p, d

    fields = ("gross", "net", "cost", "turn", "expo", "nexp", "gross_open")
    hash_bad = {}
    for i, name in enumerate(lib.NAMES):
        e = ref["candidates"][name]
        bad = [] if _sha(W[i]) == e["weights_f32_sha256"] else ["weights"]
        bad += [k for k in fields if _sha(getattr(s, k)[:, i]) != e[f"{k}_f64_sha256"]]
        if bad:
            hash_bad[name] = bad
    out["candidates_hash_equal"] = 32 - len(hash_bad)
    out["candidates_hash_bad"] = hash_bad
    out["R_hash_equal"] = _sha(s.R.astype(np.float32)) == ref["R_f32_sha256"]
    out["c_hash_equal"] = _sha(s.c.astype(np.float32)) == ref["c_f32_sha256"]

    if study is not None:
        Ws = np.load(study / "library" / "weights_disc.npy", mmap_mode="r")
        zs = np.load(study / "library" / "series_disc.npz", allow_pickle=False)
        ps = np.load(study / "library" / "panel_disc.npz", allow_pickle=False)
        per = {n: _cells(W[i], Ws[i]) for i, n in enumerate(lib.NAMES)}
        out["weights_vs_study"] = {
            "candidates": len(per),
            "cells": sum(v["cells"] for v in per.values()),
            "diff": sum(v["diff"] for v in per.values()),
            "candidates_with_a_diff": [n for n, v in per.items() if v["diff"]],
        }
        out["series_vs_study"] = {k: _cells(getattr(s, k), zs[k]) for k in fields}
        out["R_vs_study"] = _cells(s.R.astype(np.float32), ps["R"])
        out["c_vs_study"] = _cells(s.c.astype(np.float32), ps["c"])
        del Ws, zs, ps

    bk = sel.Book(s.t, s.gross, s.cost, s.c, W)
    sb = sel.score_book(s.net, s.t)
    lists = {}
    for name, table in ((PRIMARY, sb.primary), (BEST, sb.best)):
        e = ref["portfolios"][name]
        days, rows, ch = sel.primary_lists(bk, sb, table=table)
        g, c, net, expo = _portfolio(bk, ch, days)
        lists[name] = {
            "lists": len(ch),
            "days_equal": [int(x) for x in days] == e["days"],
            "lists_diff": sum(sel.slot_names(row) != want for row, want in zip(ch, e["lists"])) + abs(len(ch) - len(e["lists"])),
            "scores_hash_equal": _sha(rows) == e["scores_f64_sha256"],
            "gross_hash_equal": _sha(g) == e["gross_f64_sha256"],
            "cost_hash_equal": _sha(c) == e["cost_f64_sha256"],
            "net_hash_equal": _sha(net) == e["net_f64_sha256"],
            "cash_slots": int((ch == sel.CASH).sum()),
        }
        if name == PRIMARY:
            ch_primary, days_primary, net_p, expo_p = ch, days, net, expo
    g_ew, c_ew, w_ew = sel.ew_series(bk)
    e = ref["portfolios"]["EW"]
    lists["EW"] = {"gross_hash_equal": _sha(g_ew) == e["gross_f64_sha256"], "cost_hash_equal": _sha(c_ew) == e["cost_f64_sha256"], "net_hash_equal": _sha(g_ew - c_ew) == e["net_f64_sha256"]}
    out["portfolios"] = lists

    m = bk.t >= ts(config.FIRST_GRID_DAY)
    expo_ew = np.abs(w_ew).sum(1)
    del w_ew
    ib = lib.NAMES.index("hold_btc")
    mon = bk.mon[m]
    out["headline"] = {
        PRIMARY: {**_row(net_p[m], expo_p[m], mon), **_minus_ew(net_p[m], expo_p[m], (g_ew - c_ew)[m], expo_ew[m], mon)},
        "EW": _row((g_ew - c_ew)[m], expo_ew[m], mon),
        "hold_btc": _row(s.net[m, ib], s.expo[m, ib], mon),
    }
    out["cuts"] = [_cut(seed, W, bk, sb, ch_primary[i], int(days_primary[i])) for i in cuts]
    return out


def _cut(seed: Path, W: np.ndarray, bk: sel.Book, sb: sel.ScoreBook, want: np.ndarray, day: int) -> dict:
    """A panel cut at a Monday, as a weekly run sees it: bars closed before the Monday, a live stamp at the Monday. The weight of that stamp must equal the full build. Scores can differ a little near the cut, where a coin in a gap reads as stopped."""
    monday = str(np.datetime64(day, "D"))
    d = lib.Data(pn.seed_panel(monday, masked=False, seed=seed), live=True)
    if int(d.t[-1]) != day * 86400:
        raise ValueError("the live stamp is not the Monday")
    last = np.zeros((32, d.N), np.float32)

    def sink(name: str, w: np.ndarray) -> None:
        last[lib.NAMES.index(name)] = w[-1].astype(np.float32)

    s = lib.build_series(d, on_weights=sink)
    row, got = sel.select(sel.score_book(s.net, s.t), day)
    return {
        "monday": monday,
        "live_weights_vs_full": _cells(last, W[:, int(bk.stamp_of_day(day))]),
        "score_vs_full": _cells(row, sb.rows([day])[0]),
        "list_equal": got.tolist() == want.tolist(),
    }


def _weekly(x: np.ndarray, mon: np.ndarray) -> np.ndarray:
    _, idx = np.unique(mon, return_index=True)
    return np.expm1(np.add.reduceat(np.log1p(x), idx))


def _week_mean(x: np.ndarray, mon: np.ndarray) -> np.ndarray:
    _, idx = np.unique(mon, return_index=True)
    return np.add.reduceat(x, idx) / np.diff(np.r_[idx, len(x)])


def _row(net: np.ndarray, expo: np.ndarray, mon: np.ndarray) -> dict:
    """Compounded at the stamp, read by week, as the study."""
    rw = _weekly(net, mon)
    eq = np.r_[1.0, np.cumprod(1 + rw)]
    s = rw.std(ddof=1)
    return {
        "weeks": len(rw),
        "total_pct": round(float(np.prod(1 + rw) - 1) * 100, 1),
        "sharpe": round(float(rw.mean() / s * np.sqrt(52)), 2) if s > 0 else None,
        "max_dd_pct": round(float((eq / np.maximum.accumulate(eq) - 1).min()) * 100, 1),
        "gross_exposure": round(float(expo.mean()), 2),
    }


def _minus_ew(net: np.ndarray, expo: np.ndarray, net_ew: np.ndarray, expo_ew: np.ndarray, mon: np.ndarray) -> dict:
    """Mean weekly difference with EW, then at the same exposure two ways. Weekly k is the page's statistic: the book's mean gross exposure over EW's in that week. Constant k is what the study's -0.2 used: one ratio over the whole span."""
    rw, ew = _weekly(net, mon), _weekly(net_ew, mon)
    k = _week_mean(expo, mon) / _week_mean(expo_ew, mon)
    k0 = expo.mean() / expo_ew.mean()
    return {
        "minus_ew_bps_week": round(float((rw - ew).mean()) * 1e4, 1),
        "minus_ew_same_exposure_bps_week": round(float((rw - k * ew).mean()) * 1e4, 1),
        "minus_ew_constant_k_bps_week": round(float((rw - k0 * ew).mean()) * 1e4, 1),
    }


def masked_measure(seed: Path, end: str, tmp: Path) -> dict:
    """The seed step, the loader and the forward cost rule, then W1 to W4 of the four staggered books. Same cost basis as the study: tier, no drift, fill at the stamp's open."""
    out: dict = {}
    root = tmp / "selection"
    out["seed_step"] = pn.seed_store(seed, root)
    p = pn.load_panel(end, root)
    q = pn.seed_panel(end, masked=True, seed=seed)
    same = p.coins == q.coins and p.lists == q.lists and p.tier == q.tier and all(np.array_equal(getattr(p, x), getattr(q, x), equal_nan=True) for x in ("opens", "closes", "quotes"))
    out["store_equals_seed_columns"] = bool(same)
    out["store_files"] = len(list(config.bars_dir(root).glob("*.parquet")))
    replay_cost = q.replay_cost
    del q
    s, W, d = _build(p)
    del p
    out["rule_cost_vs_seed_cost"] = _cells(s.c, lib._replay_cost(d, replay_cost))
    out["names_on_a_list"] = {"min": int(d.Mem.sum(1).min()), "max": int(d.Mem.sum(1).max())}

    bk = sel.Book(s.t, s.gross, s.cost, s.c, W)
    sb = sel.score_book(s.net, s.t)
    g_ew, c_ew, w_ew = sel.ew_series(bk)
    nets = {"EW": g_ew - c_ew}
    expos = {"EW": np.abs(w_ew).sum(1)}
    del w_ew
    for name, table in (("four_books", sb.primary), ("best_so_far_four_books", sb.best)):
        wp = np.zeros((bk.S, bk.N))
        g = np.zeros(bk.S)
        for phase in range(config.PRIMARY_CADENCE_WEEKS):
            days, _, ch = sel.primary_lists(bk, sb, phase=phase, table=table)
            wp += sel.path(bk, ch, bk.stamp_of_day(days))
            g += sel.gross_path(bk, ch, bk.stamp_of_day(days))
            if name == "four_books":
                out[f"cash_slots_phase_{phase}"] = int((ch == sel.CASH).sum())
        wp /= config.PRIMARY_CADENCE_WEEKS
        g /= config.PRIMARY_CADENCE_WEEKS
        nets[name] = g - lib.cost_on_weights(wp, bk.c[:bk.S])
        expos[name] = np.abs(wp).sum(1)
    days, _, ch = sel.primary_lists(bk, sb)
    _, _, nets["phase_0_book"], expos["phase_0_book"] = _portfolio(bk, ch, days)
    ib = lib.NAMES.index("hold_btc")
    nets["hold_btc"], expos["hold_btc"] = s.net[:, ib], s.expo[:, ib]

    windows = {}
    for w, a, b in lib.WINDOWS + (("ALL", config.FIRST_GRID_DAY, end),):
        m = (bk.t >= ts(a)) & (bk.t < ts(b))
        if not m.any():
            continue
        mon = bk.mon[m]
        rows = {}
        for name in nets:
            rows[name] = _row(nets[name][m], expos[name][m], mon)
            if name != "EW":
                rows[name].update(_minus_ew(nets[name][m], expos[name][m], nets["EW"][m], expos["EW"][m], mon))
        windows[w] = rows
    out["windows"] = windows
    return out


def run(seed: Path, study: Path | None, end: str = config.DISCOVERY_END, tmp: Path | None = None, masked: bool = True, cuts=CUT_LISTS) -> dict:
    t0 = time.time()
    seed = Path(seed)
    report: dict = {"seed_files_verified": pn.verify_seed(seed)}
    ref = json.loads(_reference_path(seed).read_text(encoding="utf8"))
    report["replay"] = replay(ref, seed, Path(study) if study else None, end, cuts)
    report["replay_secs"] = round(time.time() - t0, 1)
    if masked:
        work = Path(tempfile.mkdtemp(prefix="selection_acceptance_", dir=tmp))
        try:
            report["masked"] = masked_measure(seed, end, work)
        finally:
            shutil.rmtree(work, ignore_errors=True)
    report["passed"] = passed(report)
    report["secs"] = round(time.time() - t0, 1)
    report["peak_mb"] = _peak_mb()
    return report


def passed(report: dict) -> bool:
    """Every proof holds. The cut checks count on their lists and live weights only, see _cut."""
    r = report["replay"]
    ok = r["t_equal"] and r["mem"]["diff"] == 0 and r["mem"]["ref_hash_equal"] and r["cost_rule_vs_seed"]["diff"] == 0
    ok = ok and r["candidates_hash_equal"] == 32 and r["R_hash_equal"] and r["c_hash_equal"]
    if "weights_vs_study" in r:
        ok = ok and r["weights_vs_study"]["diff"] == 0 and all(v["diff"] == 0 for v in r["series_vs_study"].values())
        ok = ok and r["R_vs_study"]["diff"] == 0 and r["c_vs_study"]["diff"] == 0
    for name in (PRIMARY, BEST):
        q = r["portfolios"][name]
        ok = ok and q["days_equal"] and q["lists_diff"] == 0 and q["scores_hash_equal"] and q["gross_hash_equal"] and q["cost_hash_equal"] and q["net_hash_equal"]
    ok = ok and all(r["portfolios"]["EW"].values())
    ok = ok and all(c["list_equal"] and c["live_weights_vs_full"]["diff"] == 0 for c in r["cuts"])
    if "masked" in report:
        q = report["masked"]
        ok = ok and q["store_equals_seed_columns"] and q["rule_cost_vs_seed_cost"]["diff"] == 0
    return bool(ok)


def _peak_mb() -> int | None:
    try:
        with open("/proc/self/status", encoding="utf8") as f:
            for line in f:
                if line.startswith("VmHWM"):
                    return int(line.split()[1]) // 1024
    except OSError:
        return None
    return None


def report_path(out: str | None, seed: Path) -> Path:
    """Where the report goes: not in a runner store, and not under the selection root, which holds masked records only."""
    path = Path(out).resolve() if out else seed.parent / "acceptance.json"
    config.refuse_runner_store(path)
    if path.is_relative_to(config.selection_root()):
        raise ValueError(f"{path} is under the selection root")
    return path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--study", help="folder with the study's library/ and selector/, read only")
    ap.add_argument("--write-reference", action="store_true", help="hash the study's files into seed/reference.json and stop")
    ap.add_argument("--skip-masked", action="store_true", help="replay only")
    ap.add_argument("--tmp", help="parent of the temporary store")
    ap.add_argument("--out", help="report file, default acceptance.json beside the seed folder, never under the selection root")
    args = ap.parse_args(argv)
    seed = config.seed_dir()
    if args.write_reference:
        if not args.study:
            ap.error("--write-reference needs --study")
        print(write_reference(Path(args.study), seed))
        return 0
    out = report_path(args.out, seed)
    report = run(seed, args.study, tmp=Path(args.tmp) if args.tmp else None, masked=not args.skip_masked)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf8")
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
