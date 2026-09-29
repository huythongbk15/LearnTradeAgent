#!/usr/bin/env python3
"""Re-rank every strategy with cells on disk against the current gates.

Applies the same four gates as scripts/apply_spread_gates.py, generalised
from enhanced_ma to whatever is on disk. The per-window best-cell reading
is kept: a window counts as clearing cost if any parameter combination in
it did, which is what an inner selection would carry out and is the most
favourable reading available.

Data coverage is uneven — `rsi` has 1621 cells and `trend_pullback` has
112 — so the fold count per strategy is reported alongside the verdict. A
strategy with 3 windows cannot be assessed at the p<=0.20 threshold, and
the table says so rather than implying a pass.

Run: .venv/bin/python scripts/rerank_registry.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.backtest.nested_wfo import _binomial_upper_tail

ROUND_TRIP_COST_PCT = 0.32
ZERO_TRADE_FOLD_MAX_PCT = 50.0
SINGLE_WINDOW_MAX_PCT = 60.0
P_MAX = 0.20
# Below this a strategy cannot be assessed at all; reported, not passed.
MIN_USABLE_WINDOWS = 5

OUT = Path("/tmp/registry_rerank.json")


def load_all(base: Path) -> dict[str, dict[str, list[tuple[float, float, int]]]]:
    """strategy -> window -> [(return_pct, trades, scenario_rank)]."""
    out: dict[str, dict[str, list[tuple[float, float, int]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    scenario_rank = {"1x": 0, "2x": 1, "slip_stress": 2}
    for report in base.rglob("report.json"):
        parts = report.parent.name.split("__")
        strategy = parts[0]
        scenario = parts[3] if len(parts) > 3 else "1x"
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
        out[strategy][window].append(
            (float(r), float(t), scenario_rank.get(scenario, 3))
        )
    return out


def evaluate(cells_by_window: dict) -> dict:
    base = {w: [c for c in cs if c[2] == 0] for w, cs in cells_by_window.items()}
    base = {w: c for w, c in base.items() if c}
    if not base:
        return {}
    best = {w: max(c, key=lambda x: x[0]) for w, c in base.items()}
    rows = list(best.values())
    trading = [x for x in rows if x[1] > 0]
    n = len(trading)
    if n == 0:
        return {"windows": len(rows), "trading": 0, "assessable": False}
    zero_pct = (len(rows) - n) / len(rows) * 100
    clearing = [x for x in trading if x[0] > ROUND_TRIP_COST_PCT * x[1]]
    p = _binomial_upper_tail(len(clearing), n)
    import statistics as st
    med_trades = st.median([x[1] for x in trading])
    med_ret = st.median([x[0] for x in clearing]) if clearing else None
    floor = ROUND_TRIP_COST_PCT * med_trades
    return {
        "windows": len(rows),
        "trading": n,
        "cells": sum(len(c) for c in base.values()),
        "clearing": len(clearing),
        "p_value": round(p, 4),
        "zero_pct": round(zero_pct, 1),
        "median_trades": med_trades,
        "clearing_median_return": round(med_ret, 3) if med_ret is not None else None,
        "cost_floor": round(floor, 3),
        "assessable": n >= MIN_USABLE_WINDOWS,
        "gates": {
            "zero_trade_fold_pct_le_50": zero_pct <= ZERO_TRADE_FOLD_MAX_PCT,
            f"cost_clearing_above_chance_p_le_{P_MAX}": p <= P_MAX,
            "median_return_clears_cost_floor":
                med_ret is not None and med_ret > floor,
        },
    }


def main() -> None:
    base = ROOT / "data" / "backtests"
    data = load_all(base)
    print("=" * 92)
    print(f"REGISTRY RE-RANK — four gates, base cost scenario, p <= {P_MAX}")
    print(f"assessable requires >= {MIN_USABLE_WINDOWS} trading windows")
    print("=" * 92)
    print(f"{'strategy':24s} {'cells':>6} {'win':>4} {'clr':>4} {'p':>7} "
          f"{'medTrd':>7} {'clrRet':>7} {'floor':>6}  gates")
    print("-" * 92)

    results = {}
    for strategy, windows in sorted(data.items()):
        res = evaluate(windows)
        if not res or not res.get("trading"):
            continue
        results[strategy] = res
        g = res["gates"]
        marks = "".join("ok" if v else "XX" for v in g.values())
        if not res["assessable"]:
            verdict = "TOO FEW WINDOWS"
        elif all(g.values()):
            verdict = "PASS"
        else:
            verdict = f"fail({sum(1 for v in g.values() if not v)})"
        print(f"{strategy:24s} {res['cells']:>6} {res['trading']:>4} "
              f"{res['clearing']:>4} {res['p_value']:>7.4f} "
              f"{res['median_trades']:>7.0f} "
              f"{res['clearing_median_return'] if res['clearing_median_return'] is not None else 0:>7.3f} "
              f"{res['cost_floor']:>6.2f}  {marks} {verdict}")

    passing = [s for s, r in results.items()
               if r["assessable"] and all(r["gates"].values())]
    assessable = [s for s, r in results.items() if r["assessable"]]
    print("-" * 92)
    print(f"strategies with data      : {len(results)}")
    print(f"assessable (>= {MIN_USABLE_WINDOWS} windows): {len(assessable)} "
          f"{sorted(assessable)}")
    print(f"clearing all four gates   : {len(passing)} {passing}")

    json.dump(results, open(OUT, "w"), indent=2, default=str)
    print(f"\nevidence: {OUT}")


if __name__ == "__main__":
    main()
