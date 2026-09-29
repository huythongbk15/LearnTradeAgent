#!/usr/bin/env python3
"""Apply the eeea331 spread gates to the on-disk enhanced_ma cells, per symbol.

The gates in nested_wfo.py are written against WFOOuterResult, where each
fold contributes one outer_results row — the params the inner selection
picked. On disk each cell is a (param × window) pair, so the mapping here
is: a fold is a test window, and within a window the cell that cleared cost
best is the one an inner selection would have carried out.

That is deliberately favourable to the strategy: it takes each window's
best cell rather than a median, so a window counts as clearing cost if any
parameter combination in it did. If the gates still fail under that
reading, no reading is more favourable.

Thresholds are copied from nested_wfo.py, not re-derived.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# ── thresholds, identical to nested_wfo.py (eeea331) ─────────────────────
ROUND_TRIP_COST_PCT = 0.32
ZERO_TRADE_FOLD_MAX_PCT = 50.0
MEDIAN_TRADES_MIN = 20.0
COST_CLEARING_FOLD_MIN_PCT = 50.0
SINGLE_WINDOW_MAX_PCT = 60.0

SYMBOL_DIRS = {
    "SOLUSDT": "wfo_parallel_enhanced_ma",
    "ETHUSDT": "wfo_parallel_enhanced_ma_eth",
    "BTCUSDT": "wfo_parallel_enhanced_ma_btc",
}


def load_cells(directory: Path) -> dict[str, list[tuple[float, float, int]]]:
    """window_id -> [(return_pct, trades, cost_scenario), ...]"""
    by_window: dict[str, list[tuple[float, float, int]]] = defaultdict(list)
    for report in sorted(directory.glob("enhanced_ma__*/report.json")):
        # dir name: enhanced_ma__<symbol>__<timeframe>__<cost>__p<params>__w<window>
        parts = report.parent.name.split("__")
        if len(parts) < 5:
            continue
        scenario = parts[3]
        window = parts[-1]
        try:
            d = json.loads(report.read_text())
        except Exception:
            continue
        s = d.get("summary") or d.get("metrics") or {}
        r = s.get("total_return_pct", s.get("return_pct"))
        t = s.get("total_trades", s.get("trades"))
        if r is None or t is None:
            continue
        by_window[window].append((float(r), float(t), scenario))
    return by_window


def evaluate(by_window: dict, scenario: str) -> dict:
    """Run the five gates over one symbol at one cost scenario."""
    windows = {w: c for w, c in by_window.items()
               if any(x[2] == scenario for x in c)}
    if not windows:
        return {}

    # Best cell per window: an inner selection carries the best params out.
    per_window_best = {}
    for w, cells in windows.items():
        c = [x for x in cells if x[2] == scenario]
        per_window_best[w] = max(c, key=lambda x: x[0])

    rows = list(per_window_best.values())
    trading = [x for x in rows if x[1] > 0]
    n_trading = len(trading)
    zero_pct = (len(rows) - n_trading) / len(rows) * 100

    clearing = [x for x in trading if x[0] > ROUND_TRIP_COST_PCT * x[1]]
    spread_pct = (len(clearing) / n_trading * 100) if n_trading else 0.0
    top_share = (1.0 / len(clearing) * 100) if clearing else 0.0

    import statistics as st
    med_trades = st.median([x[1] for x in trading]) if n_trading else 0.0
    med_ret = st.median([x[0] for x in clearing]) if clearing else None
    floor = ROUND_TRIP_COST_PCT * med_trades if n_trading else None

    gates = [
        ("zero_trade_fold_pct_le_50", zero_pct, "<=", ZERO_TRADE_FOLD_MAX_PCT,
         f"{zero_pct:.1f}%"),
        ("median_trades_per_trading_fold_ge_20", med_trades, ">=", MEDIAN_TRADES_MIN,
         f"{med_trades:.1f}"),
        ("cost_clearing_fold_pct_ge_50", spread_pct, ">=", COST_CLEARING_FOLD_MIN_PCT,
         f"{len(clearing)}/{n_trading} = {spread_pct:.1f}%"),
        ("cost_clearing_single_window_le_60pct", top_share, "<=", SINGLE_WINDOW_MAX_PCT,
         f"{top_share:.1f}% (1 cell per window)"),
        ("median_return_clears_cost_floor", med_ret, ">", floor,
         f"{med_ret:.3f}%" if med_ret is not None else "n/a"),
    ]
    return {
        "windows": len(rows),
        "trading_windows": n_trading,
        "clearing": len(clearing),
        "gates": [
            {"id": g, "observed": o, "cmp": c, "threshold": th, "detail": d,
             "pass": (o <= th if c == "<=" else o >= th if c == ">=" else o > th)}
            for g, o, c, th, d in gates
        ],
    }


def main() -> None:
    print("=" * 78)
    print("SPREAD GATES (eeea331) applied to on-disk enhanced_ma cells, per symbol")
    print(f"round trip {ROUND_TRIP_COST_PCT}% | thresholds copied from nested_wfo.py")
    print("=" * 78)

    out = {}
    for sym, dirname in SYMBOL_DIRS.items():
        d = ROOT / "data" / "backtests" / dirname
        if not d.exists():
            print(f"\n{sym}: no data at {dirname}")
            continue
        by_window = load_cells(d)
        cells = sum(len(c) for c in by_window.values())
        print(f"\n{sym}: {len(by_window)} windows, {cells} cells "
              f"({dirname})")
        out[sym] = {}
        for scenario in ("1x", "2x", "slip_stress"):
            res = evaluate(by_window, scenario)
            if not res:
                continue
            out[sym][scenario] = res
            verdict = "PASS" if all(g["pass"] for g in res["gates"]) else "FAIL"
            print(f"  [{scenario}] {verdict}  "
                  f"windows={res['windows']} trading={res['trading_windows']} "
                  f"clearing={res['clearing']}")
            for g in res["gates"]:
                mark = "ok  " if g["pass"] else "FAIL"
                print(f"      {mark} {g['id']:38s} {g['detail']}")

    json.dump(out, open("/tmp/spread_gates_by_symbol.json", "w"), indent=2, default=str)
    print("\nevidence: /tmp/spread_gates_by_symbol.json")


if __name__ == "__main__":
    main()
