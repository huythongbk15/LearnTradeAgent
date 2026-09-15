#!/usr/bin/env python3
"""Generate campaign_summary.json from completed WFO runs.

This script collects results from individual strategy/pair WFO runs and
produces a consolidated campaign summary with Sharpe/PF/return metrics
and portfolio-level analysis.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.research.param_grids import (
    SINGLE_ASSET_GRIDS,
    CROSS_SECTIONAL_GRIDS,
    get_strategy_code_name,
)

# All 8 strategies
STRATEGIES = [
    ("trend_pullback", None),
    ("range_mean_reversion", None),
    ("volatility_breakout", None),
    ("funding_carry", None),
    ("cross_sectional_momentum", "long_only"),
    ("cross_sectional_momentum", "long_short"),
    ("stat_arbitrage", "long_only"),
    ("stat_arbitrage", "long_short"),
]

# Pairs and timeframes
PAIRS = [
    ("BTCUSDT", "1h"),
    ("BTCUSDT", "4h"),
    ("ETHUSDT", "4h"),
    ("SOLUSDT", "4h"),
    ("AVAXUSDT", "4h"),
    ("BNBUSDT", "4h"),
]

COST_SCENARIOS = ["1x"]

# Campaign ID to actual code name mapping
CAMPAIGN_TO_CLI = {
    "trend_pullback": "trend_pullback",
    "range_mean_reversion": "range_mr",
    "range_mr": "range_mr",
    "volatility_breakout": "volatility_breakout",
    "funding_carry": "funding_carry",
    "cross_sectional_momentum": "cross_sectional_momentum",
    "stat_arbitrage": "stat_arbitrage",
}


def collect_completed_cells(base: Path) -> list[dict]:
    """Collect results from completed WFO runs.

    Prioritizes parallel_canonical_summary.json (campaign-level), but also
    falls back to outer_one_shot fold reports for strategies/pairs that don't
    have a campaign summary yet.
    """
    results = []

    for campaign_id, _ in STRATEGIES:
        cli_name = CAMPAIGN_TO_CLI.get(campaign_id, campaign_id)
        for pair, timeframe in PAIRS:
            pair_fs = pair.replace("USDT", "_USDT")
            for cost in COST_SCENARIOS:
                out_dir = base / f"{campaign_id}__{pair}__{timeframe}"
                if not out_dir.exists():
                    continue

                # Try to find parallel_canonical_summary.json
                summary_path = out_dir / "parallel_canonical_summary.json"
                if not summary_path.exists():
                    # Funding carry might save to root
                    root_summary = base / "parallel_canonical_summary.json"
                    if root_summary.exists():
                        with open(root_summary) as f:
                            root_data = json.load(f)
                        if root_data.get("strategy") == campaign_id:
                            summary_path = root_summary

                if summary_path.exists():
                    with open(summary_path) as f:
                        summary = json.load(f)
                    agg = summary.get("aggregate_metrics", {})
                    passes = summary.get("passes_hard_gates", False)
                    verdict = summary.get("verdict", "UNKNOWN")
                    results.append({
                        "strategy_id": campaign_id,
                        "symbol": pair,
                        "timeframe": timeframe,
                        "cost": cost,
                        "out_dir": str(out_dir),
                        "success": True,
                        "passes_gates": passes,
                        "verdict": verdict,
                        "median_sharpe": agg.get("median_test_sharpe", 0),
                        "mean_sharpe": agg.get("mean_test_sharpe", 0),
                        "median_return_pct": agg.get("median_test_return_pct", 0),
                        "total_oos_trades": agg.get("total_test_trades", 0),
                        "total_oos_net_pnl": agg.get("total_oos_net_pnl", None),
                        "positive_outer_folds_pct": agg.get("positive_outer_folds_pct", 0),
                        "median_profit_factor": agg.get("median_profit_factor", 0),
                        "median_max_drawdown_pct": agg.get("median_max_drawdown_pct", 0),
                        "median_calmar": agg.get("median_calmar", 0),
                        "n_outer_folds": agg.get("n_outer_folds", 0),
                        "study_manifest_id": agg.get("study_manifest_id"),
                        "promotable": agg.get("promotable", False),
                    })
                    continue  # Found summary, skip fold-level scan

                # Fallback: extract from outer_one_shot fold reports
                fold_reports = list(out_dir.glob("*/report.json"))
                if not fold_reports:
                    fold_reports = list(out_dir.glob("outer_one_shot/fold_*/report.json"))

                if fold_reports:
                    sharpes, returns, trades, pnls = [], [], [], []
                    positive_folds, total_folds = 0, 0

                    for fold_path in fold_reports:
                        with open(fold_path) as f:
                            report = json.load(f)
                        # Handle two report formats:
                        # 1. EvaluationArtifact (outer_one_shot): status + metrics dict
                        # 2. Full system backtest (inner param): "passed" + top-level metrics
                        status = report.get("status", "")
                        if status != "COMPLETED" and status != "passed":
                            continue
                        # Extract metrics from either location
                        if "metrics" in report and isinstance(report["metrics"], dict):
                            m = report["metrics"]
                        else:
                            m = report  # Full backtest format has metrics at top level
                        sharpe = m.get("sharpe") or m.get("metrics", {}).get("sharpe")
                        if sharpe is None:
                            continue
                        sharpes.append(sharpe)
                        returns.append(m.get("total_return_pct", m.get("metrics", {}).get("total_return_pct", 0)))
                        trades.append(m.get("total_trades", m.get("metrics", {}).get("total_trades", 0)))
                        pnls.append(m.get("net_pnl", 0))
                        total_folds += 1
                        if m.get("total_return_pct", 0) > 0:
                            positive_folds += 1

                    if total_folds > 0:
                        def median(lst):
                            s = sorted(lst)
                            n = len(s)
                            return s[n // 2] if n % 2 else (s[n//2-1]+s[n//2])/2 if n else 0
                        median_sharpe = median(sharpes)
                        median_return = median(returns)
                        passes = median_sharpe >= 0.8 and median_return > 0 and sum(trades) >= 30
                        results.append({
                            "strategy_id": campaign_id,
                            "symbol": pair,
                            "timeframe": timeframe,
                            "cost": cost,
                            "out_dir": str(out_dir),
                            "success": True,
                            "passes_gates": passes,
                            "verdict": "PASS" if passes else "FAIL",
                            "median_sharpe": median_sharpe,
                            "mean_sharpe": sum(sharpes) / len(sharpes) if sharpes else 0,
                            "median_return_pct": median_return,
                            "total_oos_trades": sum(trades),
                            "total_oos_net_pnl": sum(pnls) if pnls else None,
                            "positive_outer_folds_pct": (positive_folds / total_folds * 100) if total_folds else 0,
                            "n_outer_folds": total_folds,
                            "promotable": passes,
                            "source": "fold_reports",
                        })

    return results


def generate_campaign_summary(base: Path) -> dict:
    """Generate campaign_summary.json."""
    cells = collect_completed_cells(base)

    # Strategy-level aggregation
    by_strategy: dict[str, list[dict]] = {}
    for cell in cells:
        sid = cell["strategy_id"]
        by_strategy.setdefault(sid, []).append(cell)

    strategy_summary = []
    for sid, cell_list in by_strategy.items():
        passing = [c for c in cell_list if c["passes_gates"]]
        best_cell = max(cell_list, key=lambda c: c.get("median_sharpe", 0) or 0)
        strategy_summary.append({
            "strategy_id": sid,
            "n_runs": len(cell_list),
            "n_passing": len(passing),
            "best_pair": best_cell["symbol"],
            "best_timeframe": best_cell["timeframe"],
            "best_sharpe": best_cell["median_sharpe"],
            "best_return_pct": best_cell["median_return_pct"],
            "best_pnl": best_cell.get("total_oos_net_pnl"),
            "promotable": best_cell.get("promotable", False),
        })

    summary = {
        "campaign": "strategy_research_phase3_full_8strategy",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_strategies": len(STRATEGIES),
        "n_strategies_completed": len(by_strategy),
        "n_cells_total": len(cells),
        "n_cells_passing_gates": sum(1 for c in cells if c["passes_gates"]),
        "strategies": strategy_summary,
        "cells": cells,
    }

    return summary


def main():
    base = Path("data/backtests/wfo_full")

    # Generate summary
    summary = generate_campaign_summary(base)

    summary_path = base / "campaign_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)

    print("Campaign summary written to:", summary_path)
    print(f"  Total cells: {summary['n_cells_total']}")
    print(f"  Completed strategies: {summary['n_strategies_completed']}/8")
    print(f"  Passing gates: {summary['n_cells_passing_gates']}")

    # Print strategy summary
    print("\n=== Strategy Summary ===")
    for s in summary["strategies"]:
        status = "✅" if s["n_passing"] > 0 else "❌"
        print(f"  {status} {s['strategy_id']}: {s['n_passing']}/{s['n_runs']} pairs pass")
        if s["best_sharpe"] is not None:
            print(f"      Best: {s['best_pair']}/{s['best_timeframe']} "
                  f"Sharpe={s['best_sharpe']:.3f}, Return={s['best_return_pct']:.2f}%")

    # Portfolio analysis
    print("\n=== Portfolio Analysis ===")
    cells = summary["cells"]
    sharpes = [c["median_sharpe"] for c in cells if c["median_sharpe"] is not None]
    avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0
    diversification_ratio = avg_sharpe / (sum((s - avg_sharpe)**2 for s in sharpes) / len(sharpes))**0.5 if len(sharpes) > 1 else 0

    print(f"  Average Sharpe across all cells: {avg_sharpe:.4f}")
    print(f"  Diversification ratio (approx): {diversification_ratio:.2f}")
    print(f"  Strategies passing gates: {sum(1 for s in summary['strategies'] if s['n_passing'] > 0)}")


if __name__ == "__main__":
    main()
