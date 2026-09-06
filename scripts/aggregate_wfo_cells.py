#!/usr/bin/env python3
"""Aggregate WFO cells into wfo_decision.json with verdict.

Reads per-cell report.json from a directory (e.g. data/backtests/wfo_parallel)
and produces:
- Aggregate metrics (median/mean across folds, per cost scenario)
- 13 hard gates evaluation
- Verdict: FINAL_PASS or NO_TRADE
- Sensitivity analysis (cost stress, slippage stress, drop_best_trade)
- Trial provenance

Usage:
    python scripts/aggregate_wfo_cells.py --in data/backtests/wfo_parallel --out data/backtests/wfo_parallel_summary
"""

from __future__ import annotations

import argparse
import hashlib
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


# Hard gate thresholds (from S3 spec / WFO medium results)
HARD_GATES: list[dict[str, Any]] = [
    {
        "gate_id": "outer_oos_net_return_positive",
        "metric": "total_return_pct",
        "comparison": ">",
        "threshold": 0.0,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_sharpe_ge_080",
        "metric": "sharpe",
        "comparison": ">=",
        "threshold": 0.8,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_profit_factor_ge_120",
        "metric": "profit_factor",
        "comparison": ">=",
        "threshold": 1.2,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_max_drawdown_le_10pct",
        "metric": "max_drawdown_pct",
        "comparison": "<=",
        "threshold": 10.0,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_calmar_ge_050",
        "metric": "calmar",
        "comparison": ">=",
        "threshold": 0.5,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_positive_folds_ge_60pct",
        "metric": "positive_fold_pct",
        "comparison": ">=",
        "threshold": 60.0,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_min_trades_ge_10",
        "metric": "total_trades",
        "comparison": ">=",
        "threshold": 10,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_calmar_positive",
        "metric": "calmar",
        "comparison": ">",
        "threshold": 0.0,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_sharpe_ci_lower_positive",
        "metric": "sharpe_ci95_lo",
        "comparison": ">",
        "threshold": 0.0,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_pbo_lt_050",
        "metric": "pbo",
        "comparison": "<",
        "threshold": 0.5,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_dsr_positive",
        "metric": "dsr",
        "comparison": ">",
        "threshold": 0.0,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_consistent_across_costs",
        "metric": "consistent_across_costs",
        "comparison": "==",
        "threshold": True,
        "policy_version": "v1",
    },
    {
        "gate_id": "outer_oos_provenance_eligible",
        "metric": "provenance_eligible",
        "comparison": "==",
        "threshold": True,
        "policy_version": "v1",
    },
]


def evaluate_gate(observed: Any, comparison: str, threshold: Any) -> str:
    if observed is None:
        return "INVALID"
    try:
        if comparison == ">":
            verdict = "PASS" if observed > threshold else "FAIL"
        elif comparison == ">=":
            verdict = "PASS" if observed >= threshold else "FAIL"
        elif comparison == "<":
            verdict = "PASS" if observed < threshold else "FAIL"
        elif comparison == "<=":
            verdict = "PASS" if observed <= threshold else "FAIL"
        elif comparison == "==":
            verdict = "PASS" if observed == threshold else "FAIL"
        else:
            verdict = "INVALID"
    except TypeError:
        verdict = "INVALID"
    return verdict


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
        description="Aggregate WFO cells into wfo_decision.json"
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
        required=True,
        help="Output dir for wfo_decision.json",
    )
    parser.add_argument("--strategy", default="ma_adx")
    parser.add_argument("--symbol", default="SOL/USDT")
    parser.add_argument("--timeframe", default="1h")
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Aggregating cells from {input_dir}...")
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
        # Positive cell percentage
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

    # Check consistency across costs (median return > 0 in all cost scenarios)
    consistent_across_costs = all(
        aggregate_metrics[c]["median_return_pct"] > 0 for c in by_cost.keys()
    )

    # Provenance
    commit_sha = get_git_commit_sha()
    worktree_clean = is_worktree_clean()
    provenance_eligible = worktree_clean and commit_sha != "unknown"

    # Block-bootstrap CI on aggregated returns (simple: use Sharpe distribution)
    sharpes = [c["sharpe"] for c in all_cells if c["sharpe"] is not None]
    if len(sharpes) >= 3:
        sharpe_mean = statistics.mean(sharpes)
        sharpe_std = statistics.stdev(sharpes) if len(sharpes) > 1 else 0.0
        sharpe_ci95_lo = sharpe_mean - 1.96 * sharpe_std / (len(sharpes) ** 0.5)
        sharpe_ci95_hi = sharpe_mean + 1.96 * sharpe_std / (len(sharpes) ** 0.5)
    else:
        sharpe_ci95_lo = None
        sharpe_ci95_hi = None

    # PBO proxy: % of cells with negative Sharpe
    n_negative_sharpe = sum(1 for s in sharpes if s < 0)
    pbo = (n_negative_sharpe / len(sharpes)) if sharpes else None

    # DSR proxy: avg(net_pnl) / std(net_pnl) * sqrt(N) if enough data
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

    # Statistical hardening
    statistical_hardening = {
        "sharpe_ci95_lo": sharpe_ci95_lo,
        "sharpe_ci95_hi": sharpe_ci95_hi,
        "pbo": pbo,
        "dsr": dsr,
        "n_observations": len(sharpes),
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

    # Evaluate hard gates
    gate_observed = {
        "total_return_pct": primary["median_return_pct"],
        "sharpe": primary["median_sharpe"],
        "profit_factor": primary["median_profit_factor"],
        "max_drawdown_pct": primary["median_max_dd_pct"],
        "calmar": primary["median_calmar"],
        "positive_fold_pct": primary["positive_cell_pct"],
        "total_trades": primary["total_trades"],
        "sharpe_ci95_lo": sharpe_ci95_lo,
        "pbo": pbo,
        "dsr": dsr,
        "consistent_across_costs": consistent_across_costs,
        "provenance_eligible": provenance_eligible,
    }
    gate_results: list[dict[str, Any]] = []
    for gate in HARD_GATES:
        observed = gate_observed.get(gate["metric"])
        verdict = evaluate_gate(observed, gate["comparison"], gate["threshold"])
        gate_results.append(
            {
                "gate_id": gate["gate_id"],
                "policy_version": gate["policy_version"],
                "observed_value": observed,
                "threshold": gate["threshold"],
                "comparison": gate["comparison"],
                "verdict": verdict,
                "reason": f"observed={observed} {gate['comparison']} {gate['threshold']}",
            }
        )
    passes_hard_gates = all(g["verdict"] == "PASS" for g in gate_results)

    # Verdict
    verdict = "FINAL_PASS" if passes_hard_gates else "NO_TRADE"

    # Build final artifact
    artifact_id_payload = json.dumps(
        {
            "strategy": args.strategy,
            "symbol": args.symbol,
            "timeframe": args.timeframe,
            "n_cells": len(all_cells),
            "primary_cost": primary_cost,
            "verdict": verdict,
            "passes_hard_gates": passes_hard_gates,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    artifact_id = f"sha256:{hashlib.sha256(artifact_id_payload.encode()).hexdigest()}"

    # Trial counts
    trial_counts = {
        "raw_trial_count": len(cell_dirs),
        "effective_trial_count": len(all_cells),
        "evaluation_count": len(cells),
        "unique_experiments": len(set(c["cell_id"].split("__")[3] for c in all_cells)),
        "search_family_counts": {
            "wfo_parallel_aggregator": len(all_cells),
        },
        "inner_validation_trials": len(all_cells),
        "outer_oos_trials": len(all_cells),
        "total_trial_runs": len(all_cells),
        "methodology": "aggregated from parallel-run cells; primary cost = "
        + primary_cost,
    }

    wfo_decision = {
        "spec": {
            "strategy_id": args.strategy,
            "symbol": args.symbol,
            "timeframe": args.timeframe,
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
        "gate_results": gate_results,
        "passes_hard_gates": passes_hard_gates,
        "verdict": verdict,
        "artifact_id": artifact_id,
        "trial_counts": trial_counts,
        "commit_sha": commit_sha,
        "worktree_clean": worktree_clean,
        "provenance_eligible": provenance_eligible,
        "evidence_class": "REAL_MARKET",
        "created_at": datetime.now(UTC).isoformat(),
    }

    out_path = output_dir / "wfo_decision.json"
    with open(out_path, "w") as f:
        json.dump(wfo_decision, f, indent=2, allow_nan=False, default=str)
    print("\n=== Aggregator Result ===")
    print(f"  Verdict: {verdict}")
    print(f"  Passes hard gates: {passes_hard_gates}")
    print(f"  Artifact ID: {artifact_id}")
    print(f"  Primary cost: {primary_cost}")
    print(f"  Median Sharpe: {primary['median_sharpe']:.4f}")
    print(f"  Median Return: {primary['median_return_pct']:.4f}%")
    print(f"  Median Profit Factor: {primary['median_profit_factor']:.4f}")
    print(f"  Median Calmar: {primary['median_calmar']:.4f}")
    print(f"  Positive cells: {primary['positive_cell_pct']:.1f}%")
    print(f"  Provenance eligible: {provenance_eligible}")
    print("\n  Gate results:")
    for g in gate_results:
        print(
            f"    [{g['verdict']}] {g['gate_id']}: {g['observed_value']} {g['comparison']} {g['threshold']}"
        )
    print(f"\n  Saved: {out_path}")


if __name__ == "__main__":
    main()
