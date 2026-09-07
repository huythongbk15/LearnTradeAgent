#!/usr/bin/env python3
"""Diagnostic WFO cell analyzer: reads per-cell reports and presents metrics for exploration.

This is a DIAGNOSTIC TOOL ONLY - it does NOT produce promotion-eligible verdicts.
For canonical S3 validation, use `run_wfo_parallel.py` which runs the canonical WFO
with inner-selection-freeze-before-outer and proper statistical hardening.

Usage:
    python scripts/diagnose_wfo_cells.py --in data/backtests/wfo_parallel
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def get_git_commit_sha() -> str:
    try: 
        return (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
    except Exception:
        return "unknown"


def is_worktree_clean() -> bool:
    try:
        out = (
            subprocess.check_output(
                ["git", "status", "--porcelain"], cwd=ROOT, stderr=subprocess.DEVNULL
            )
            .decode()
            .strip()
        )
        return len(out) == 0
    except Exception:
        return False


def parse_cell_dir(cell_dir: Path) -> dict[str, Any] | None:
    """Parse one cell's report.json into normalized dict."""
    report_path = cell_dir / "report.json"
    if not report_path.exists():
        return None
    try:
        with open(report_path) as f:
            data = json.load(f)
    except Exception:
        return None
    metrics = data.get("metrics", {})
    return {
        "cell_id": cell_dir.name,
        "status": data.get("status"),
        "symbol": data.get("symbol"),
        "timeframe": data.get("timeframe"),
        "final_equity": metrics.get("final_equity"),
        "total_return_pct": metrics.get("total_return_pct"),
        "sharpe": metrics.get("sharpe"),
        "max_drawdown_pct": metrics.get("max_drawdown_pct"),
        "total_trades": metrics.get("total_trades"),
        "win_rate_pct": metrics.get("win_rate_pct"),
        "profit_factor": metrics.get("profit_factor"),
        "calmar": metrics.get("calmar"),
        "sortino": metrics.get("sortino"),
        "gross_profit": metrics.get("gross_profit"),
        "gross_loss": metrics.get("gross_loss"),
        "net_pnl": metrics.get("net_pnl"),
        "circuit_breakers": data.get("circuit_breakers", 0),
        "cost_attribution": data.get("cost_attribution", {}),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Diagnostic WFO cell analyzer (NO promotion verdict)"
    )
    parser.add_argument(
        "--in",
        dest="input_dir",
        required=True,
        help="Cell directory (e.g. data/backtests/wfo_parallel)",
    )
    parser.add_argument(
        "--out",
        dest="output_dir",
        default=None,
        help="Optional output dir for diagnostic report",
    )
    parser.add_argument("--strategy", default="ma_adx")
    parser.add_argument("--symbol", default="SOL/USDT")
    parser.add_argument("--timeframe", default="1h")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = input_dir / "diagnostic"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"DIAGNOSTIC: Analyzing cells from {input_dir}...")
    print("  WARNING: This is a diagnostic tool only. Results are NOT promotion-eligible.")
    print("  For canonical S3 validation, use run_wfo_parallel.py")
    cell_dirs = sorted([d for d in input_dir.iterdir() if d.is_dir()])
    cells: list[dict[str, Any]] = []
    for d in cell_dirs:
        parsed = parse_cell_dir(d)
        if parsed and parsed.get("status") == "passed":
            cells.append(parsed)
    print(f"  Total cells: {len(cell_dirs)}")
    print(f"  Parsed (passed): {len(cells)}")

    if not cells:
        print("ERROR: No valid cells found")
        sys.exit(1)

    # Group by cost scenario (parse from cell_id: e.g. ma_adx__SOLUSDT__1h__1x__p..__w..)
    by_cost: dict[str, list[dict[str, Any]]] = {}
    for cell in cells:
        parts = cell["cell_id"].split("__")
        if len(parts) >= 4:
            cost = parts[3]
            by_cost.setdefault(cost, []).append(cell)

    print(f"  By cost: {', '.join(f'{c}={len(v)}' for c, v in by_cost.items())}")

    # Compute aggregate metrics per cost (use median across all cells in cost)
    aggregate_metrics: dict[str, Any] = {}
    for cost, cost_cells in by_cost.items():
        # Per-cost: median metrics (OOS)
        aggregate_metrics[cost] = {
            "n_cells": len(cost_cells),
            "median_sharpe": statistics.median(
                c["sharpe"] for c in cost_cells if c["sharpe"] is not None
            ),
            "median_return_pct": statistics.median(
                c["total_return_pct"]
                for c in cost_cells
                if c["total_return_pct"] is not None
            ),
            "median_max_dd_pct": statistics.median(
                c["max_drawdown_pct"]
                for c in cost_cells
                if c["max_drawdown_pct"] is not None
            ),
            "median_profit_factor": statistics.median(
                c["profit_factor"] for c in cost_cells if c["profit_factor"] is not None
            ),
            "median_calmar": statistics.median(
                c["calmar"] for c in cost_cells if c["calmar"] is not None
            ),
            "total_trades": sum(c["total_trades"] or 0 for c in cost_cells),
            "total_net_pnl": sum(c["net_pnl"] or 0 for c in cost_cells),
            "positive_cells": sum(
                1 for c in cost_cells if (c["total_return_pct"] or 0) > 0
            ),
        }
        n = len(cost_cells)
        aggregate_metrics[cost]["positive_cell_pct"] = (
            100.0 * aggregate_metrics[cost]["positive_cells"] / n if n > 0 else 0.0
        )

    # Use 1x cost as primary (or first available)
    primary_cost = "1x" if "1x" in by_cost else next(iter(by_cost.keys()))
    primary = aggregate_metrics[primary_cost]

    # Aggregate across all costs
    all_cells = [c for cells_in_cost in by_cost.values() for c in cells_in_cost]
    aggregate_metrics["overall"] = {
        "n_cells": len(all_cells),
        "median_sharpe": statistics.median(
            c["sharpe"] for c in all_cells if c["sharpe"] is not None
        ),
        "median_return_pct": statistics.median(
            c["total_return_pct"]
            for c in all_cells
            if c["total_return_pct"] is not None
        ),
        "median_max_dd_pct": statistics.median(
            c["max_drawdown_pct"]
            for c in all_cells
            if c["max_drawdown_pct"] is not None
        ),
        "median_profit_factor": statistics.median(
            c["profit_factor"] for c in all_cells if c["profit_factor"] is not None
        ),
        "median_calmar": statistics.median(
            c["calmar"] for c in all_cells if c["calmar"] is not None
        ),
        "total_trades": sum(c["total_trades"] or 0 for c in all_cells),
        "total_net_pnl": sum(c["net_pnl"] or 0 for c in all_cells),
        "positive_cells": sum(1 for c in all_cells if (c["total_return_pct"] or 0) > 0),
    }
    n_all = len(all_cells)
    aggregate_metrics["overall"]["positive_cell_pct"] = (
        100.0 * aggregate_metrics["overall"]["positive_cells"] / n_all
        if n_all > 0
        else 0.0
    )

    # Consistency across costs
    consistent_across_costs = all(
        aggregate_metrics[c]["median_return_pct"] > 0 for c in by_cost.keys()
    )

    # Provenance
    commit_sha = get_git_commit_sha()
    worktree_clean = is_worktree_clean()
    provenance_eligible = worktree_clean and commit_sha != "unknown"

    # Simple statistics (NOT canonical - uses Sharpe distribution as proxy)
    sharpes = [c["sharpe"] for c in all_cells if c["sharpe"] is not None]
    if len(sharpes) >= 3:
        sharpe_mean = statistics.mean(sharpes)
        sharpe_std = statistics.stdev(sharpes) if len(sharpes) > 1 else 0.0
        sharpe_ci95_lo = sharpe_mean - 1.96 * sharpe_std / (len(sharpes) ** 0.5)
        sharpe_ci95_hi = sharpe_mean + 1.96 * sharpe_std / (len(sharpes) ** 0.5)
    else:
        sharpe_ci95_lo = None
        sharpe_ci95_hi = None

    # PBO proxy (NOT canonical - uses % negative Sharpe)
    n_negative_sharpe = sum(1 for s in sharpes if s < 0)
    pbo = (n_negative_sharpe / len(sharpes)) if sharpes else None

    # DSR proxy (NOT canonical - uses net_pnl)
    pnls = [c["net_pnl"] for c in all_cells if c["net_pnl"] is not None]
    if len(pnls) >= 5:
        pnl_mean = statistics.mean(pnls)
        pnl_std = statistics.stdev(pnls) if len(pnls) > 1 else 0.0
        dsr = pnl_mean / (pnl_std + 1e-9) * (len(pnls) ** 0.5)
    else:
        dsr = None

    # Multi-dim backing (per cost)
    multi_dimensional: dict[str, Any] = {
        "by_cost": aggregate_metrics,
        "by_cell": [
            {
                "cell_id": c["cell_id"],
                "sharpe": c["sharpe"],
                "return_pct": c["total_return_pct"],
                "trades": c["total_trades"],
                "profit_factor": c["profit_factor"],
                "max_drawdown_pct": c["max_drawdown_pct"],
            }
            for c in all_cells
        ],
    }

    # Statistical hardening (PROXY - not canonical)
    statistical_hardening = {
        "sharpe_ci95_lo": sharpe_ci95_lo,
        "sharpe_ci95_hi": sharpe_ci95_hi,
        "pbo": pbo,
        "dsr": dsr,
        "n_observations": len(sharpes),
        "note": "PROXY statistics - uses Sharpe distribution as proxy. Canonical WFO uses real return series.",
    }

    # Sensitivity (cost_2x and slip_stress vs 1x)
    sensitivity: dict[str, Any] = {
        "baseline_cost": primary_cost,
    }
    for cost in ["2x", "slip_stress"]:
        if cost in aggregate_metrics and primary_cost in aggregate_metrics:
            baseline = aggregate_metrics[primary_cost]
            stressed = aggregate_metrics[cost]
            sensitivity[f"cost_{cost}_delta_return_pct"] = (
                stressed["median_return_pct"] - baseline["median_return_pct"]
            )
            sensitivity[f"cost_{cost}_delta_sharpe"] = (
                stressed["median_sharpe"] - baseline["median_sharpe"]
            )

    # Build diagnostic report (NO verdict, NO promotion logic)
    diagnostic_report = {
        "meta": {
            "tool": "diagnose_wfo_cells.py",
            "purpose": "diagnostic_exploration_only",
            "not_promotion_eligible": True,
            "canonical_authority": "run_wfo_parallel.py (canonical WFO)",
            "strategy": args.strategy,
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "commit_sha": commit_sha,
            "worktree_clean": worktree_clean,
            "provenance_eligible": provenance_eligible,
            "created_at": datetime.now(UTC).isoformat(),
        },
        "aggregate_metrics": {
            "n_cells": len(all_cells),
            "primary_cost": primary_cost,
            "median_test_sharpe": primary["median_sharpe"],
            "median_test_return_pct": primary["median_return_pct"],
            "mean_test_sharpe": statistics.mean(
                c["sharpe"] for c in all_cells if c["sharpe"] is not None
            ),
            "mean_test_return_pct": statistics.mean(
                c["total_return_pct"]
                for c in all_cells
                if c["total_return_pct"] is not None
            ),
            "total_test_trades": primary["total_trades"],
            "total_oos_net_pnl": aggregate_metrics["overall"]["total_net_pnl"],
            "positive_cell_pct": primary["positive_cell_pct"],
            "median_profit_factor": primary["median_profit_factor"],
            "median_max_drawdown_pct": primary["median_max_dd_pct"],
            "median_calmar": primary["median_calmar"],
            "by_cost": aggregate_metrics,
        },
        "statistical_hardening": statistical_hardening,
        "multi_dimensional": multi_dimensional,
        "sensitivity": sensitivity,
        "consistency": {
            "consistent_across_costs": consistent_across_costs,
        },
    }

    out_path = output_dir / "wfo_diagnostic.json"
    with open(out_path, "w") as f:
        json.dump(diagnostic_report, f, indent=2, allow_nan=False, default=str)

    print("\n=== DIAGNOSTIC Report ===")
    print("  Tool: diagnose_wfo_cells.py (diagnostic only)")
    print("  NOT promotion-eligible")
    print(f"  Primary cost: {primary_cost}")
    print(f"  Median Sharpe: {primary['median_sharpe']:.4f}")
    print(f"  Median Return: {primary['median_return_pct']:.4f}%")
    print(f"  Median Profit Factor: {primary['median_profit_factor']:.4f}")
    print(f"  Median Calmar: {primary['median_calmar']:.4f}")
    print(f"  Positive cells: {primary['positive_cell_pct']:.1f}%")
    print(f"  Provenance eligible: {provenance_eligible}")
    print(f"\n  Saved: {out_path}")
    print("\n  For canonical S3 validation, run:")
    print(f"    python scripts/run_wfo_parallel.py --strategy {args.strategy} --symbol {args.symbol} --timeframe {args.timeframe} --cost all --workers 4")


if __name__ == "__main__":
    main()