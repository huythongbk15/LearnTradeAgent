#!/usr/bin/env python3
"""Quick 3-year multi-asset effectiveness benchmark."""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path("src")))
os.environ["USE_LLM"] = "false"
from scripts.walk_forward_optimize import walk_forward_optimize

PAIRS = [
    ("BTC/USDT", "1h"),
    ("ETH/USDT", "1h"),
    ("XRP/USDT", "1h"),
    ("BTC/USDT", "4h"),
    ("ETH/USDT", "4h"),
    ("XRP/USDT", "4h"),
]
out = {}
for sym, tf in PAIRS:
    print(f"=== {sym} {tf} ===")
    t0 = time.time()
    try:
        res = walk_forward_optimize(
            symbol=sym,
            timeframe=tf,
            train_months=12,
            test_months=3,
            step_months=3,
            top_n=5,
            fast=True,
        )
        best = res[0] if res else None
        agg = {
            "windows": len(res),
            "best_sharpe": best.get("sharpe") if best else None,
            "best_return_pct": best.get("total_return_pct") if best else None,
            "best_max_dd_pct": best.get("max_dd_pct") if best else None,
            "best_win_rate_pct": best.get("win_rate_pct") if best else None,
            "best_params": best.get("params") if best else None,
        }
    except Exception as e:
        agg = {"error": f"{type(e).__name__}: {e}"}
    agg["seconds"] = round(time.time() - t0, 2)
    out[f"{sym}|{tf}"] = agg
    print(agg)
Path("data/benchmarks").mkdir(parents=True, exist_ok=True)
with open("data/benchmarks/benchmark_3y_multi.json", "w") as f:
    json.dump(out, f, indent=2)
print("\nSaved to data/benchmarks/benchmark_3y_multi.json")
