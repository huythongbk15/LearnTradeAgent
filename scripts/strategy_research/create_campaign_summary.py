#!/usr/bin/env python3
"""Create campaign_summary.json from completed WFO runs."""
import json
import time
from pathlib import Path
from datetime import datetime, timezone

root = Path("/home/huythong/.qwenpaw/workspaces/trading")
base = root / "data/backtests/wfo_full"

def safe_get(d, *keys, default=None):
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k)
        else:
            return default
        if d is None:
            return default
    return d

def extract_metrics_from_reports(out_dir: Path) -> dict | None:
    """Extract metrics from fold reports if no summary exists."""
    fold_reports = list(out_dir.glob("*/report.json"))
    if not fold_reports:
        fold_reports = list(out_dir.glob("*/outer_one_shot/*/report.json"))
    if not fold_reports:
        return None

    sharpes, returns, trades = [], [], []
    positive_folds = 0
    total_valid = 0

    for rp in sorted(fold_reports):
        with open(rp) as f:
            r = json.load(f)
        status = r.get("status", "")
        if status not in ("COMPLETED", "passed"):
            continue
        # Try nested metrics first
        m = r.get("metrics", {})
        sharpe = m.get("sharpe") if m else r.get("sharpe")
        ret = m.get("total_return_pct") if m else r.get("total_return_pct")
        total = m.get("total_trades") if m else r.get("total_trades")
        if sharpe is not None and total is not None:
            sharpes.append(sharpe)
            returns.append(ret or 0)
            trades.append(total)
            total_valid += 1
            if (ret or 0) > 0:
                positive_folds += 1

    if total_valid == 0:
        return None

    def median(lst):
        s = sorted(lst)
        n = len(s)
        return s[n // 2] if n % 2 else (s[n//2-1] + s[n//2]) / 2

    return {
        "median_sharpe": median(sharpes),
        "median_return_pct": median(returns),
        "total_trades": sum(trades),
        "positive_folds_pct": (positive_folds / total_valid * 100) if total_valid else 0,
        "n_valid_folds": total_valid,
    }

# Collect results from all strategy directories
cells = []
strategies_found = set()

for entry in sorted(base.iterdir()):
    if not entry.is_dir():
        continue
    parts = entry.name.split("__")
    if len(parts) < 3:
        continue
    strategy_id = parts[0]
    symbol = parts[1]
    timeframe = parts[2]
    
    strategies_found.add(strategy_id)

    # Try parallel_canonical_summary.json first
    summary_path = entry / "parallel_canonical_summary.json"
    if summary_path.exists():
        with open(summary_path) as f:
            s = json.load(f)
        agg = s.get("aggregate_metrics", {})
        cells.append({
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": s.get("verdict", "UNKNOWN"),
            "passes_hard_gates": s.get("passes_hard_gates", False),
            "median_sharpe": safe_get(agg, "median_test_sharpe", default=0) or 0,
            "mean_sharpe": safe_get(agg, "mean_test_sharpe", default=0) or 0,
            "median_return_pct": safe_get(agg, "median_test_return_pct", default=0) or 0,
            "total_test_trades": safe_get(agg, "total_test_trades", default=0),
            "median_profit_factor": safe_get(agg, "median_profit_factor", default=0) or 0,
            "median_max_drawdown_pct": safe_get(agg, "median_max_drawdown_pct", default=0) or 0,
            "total_oos_net_pnl": safe_get(agg, "total_oos_net_pnl"),
            "positive_outer_folds_pct": safe_get(agg, "positive_outer_folds_pct", default=0) or 0,
            "n_outer_folds": safe_get(agg, "n_outer_folds", default=0),
            "promotable": safe_get(agg, "promotable", default=False),
            "source": "parallel_canonical_summary",
        })
        continue

    # Fallback: extract from fold reports
    metrics = extract_metrics_from_reports(entry)
    if metrics:
        cells.append({
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "COMPLETED",
            "passes_hard_gates": False,
            "median_sharpe": metrics["median_sharpe"],
            "median_return_pct": metrics["median_return_pct"],
            "total_test_trades": metrics["total_trades"],
            "median_profit_factor": 0,
            "median_max_drawdown_pct": 0,
            "total_oos_net_pnl": None,
            "positive_outer_folds_pct": metrics["positive_folds_pct"],
            "n_outer_folds": metrics["n_valid_folds"],
            "promotable": False,
            "source": "fold_reports",
        })

# Build campaign summary
summary = {
    "campaign": "strategy_research_phase3_wfo_8strategy",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "n_strategies_tested": len(strategies_found),
    "n_strategies_passing": sum(1 for s in set(c["strategy_id"] for c in cells) if any(c["passes_hard_gates"] for c in cells if c["strategy_id"] == s)),
    "n_cells_total": len(cells),
    "n_cells_passing_gates": sum(1 for c in cells if c["passes_hard_gates"]),
    "cells": sorted(cells, key=lambda x: (x["strategy_id"], x["symbol"], x["timeframe"])),
    "summary": {
        "strategies": list(strategies_found),
        "pairs": sorted(set(c["symbol"] for c in cells)),
        "timeframes": sorted(set(c["timeframe"] for c in cells)),
        "best_sharpe": max((c["median_sharpe"] for c in cells), default=0),
        "best_cell": max(cells, key=lambda x: x["median_sharpe"] or 0) if cells else None,
    }
}

# Write summary
out_path = base / "campaign_summary.json"
with open(out_path, "w") as f:
    json.dump(summary, f, indent=2, default=str)

print(f"Campaign summary written to: {out_path}")
print(f"  Total cells: {summary['n_cells_total']}")
print(f"  Passing gates: {summary['n_cells_passing_gates']}")
print(f"  Strategies with passing cells: {summary['n_strategies_passing']}")
print()
print("=== Cell Results ===")
for c in sorted(cells, key=lambda x: (x["strategy_id"], x["symbol"], x["timeframe"])):
    status_emoji = "✅" if c["passes_hard_gates"] else "❌"
    print(f"  {status_emoji} {c['strategy_id']} {c['symbol']} {timeframe}: "
          f"Sharpe={c['median_sharpe']:.2f}, Return={c['median_return_pct']:.2f}%, Trades={c['total_test_trades']}")
