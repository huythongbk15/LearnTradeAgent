#!/usr/bin/env python3
"""
Run full 8-strategy WFO campaign across all pairs/timeframes.
Then generate campaign_summary.json with Sharpe/PF/return for all cells.
Finally compute portfolio correlation matrix (2×5=10 strategies) + diversification ratio.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import product
from pathlib import Path

from trading_agent.research.param_grids import (
    SINGLE_ASSET_GRIDS,
    CROSS_SECTIONAL_GRIDS,
    get_strategy_code_name,
)

# All 8 strategies (9 classes counting cs variants)
STRATEGIES = [
    # (campaign_strategy_id, cs_variant or None)
    ("trend_pullback", None),           # S1
    ("range_mean_reversion", None),     # S2
    ("volatility_breakout", None),      # S3
    ("funding_carry", None),            # S5
    ("cross_sectional_momentum", "long_only"),   # S4lo
    ("cross_sectional_momentum", "long_short"),  # S4ls
    ("stat_arbitrage", "long_only"),           # S8lo
    ("stat_arbitrage", "long_short"),          # S8ls
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

# Cost scenarios
COST_SCENARIOS = ["1x"]


def get_cli_strategy_name(campaign_id: str, cs_variant: str | None) -> str:
    """Map campaign strategy_id to CLI --strategy name."""
    code_name = get_strategy_code_name(campaign_id)
    if cs_variant:
        return f"{code_name}_{cs_variant}"
    return code_name


def run_single_wfo(args: tuple) -> dict:
    """Run a single WFO cell."""
    campaign_id, cs_variant, pair, timeframe, cost = args
    cli_name = get_cli_strategy_name(campaign_id, cs_variant)

    out_dir = f"data/backtests/wfo_full/{campaign_id}__{pair}__{timeframe}"
    cmd = [
        sys.executable, "scripts/run_wfo_parallel.py",
        "--strategy", cli_name,
        "--symbol", f"{pair.replace('USDT', '/USDT')}",
        "--timeframe", timeframe,
        "--cost", cost,
        "--out", out_dir,
        "--workers", "4",
        "--cell-timeout", "1800",
        "--train-months", "12",
        "--val-months", "3",
        "--test-months", "3",
        "--step-months", "3",
    ]

    print(f"▶ Starting: {cli_name} {pair} {timeframe} {cost}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    success = result.returncode == 0
    print(f"✓ Done: {cli_name} {pair} {timeframe} {cost} → {'OK' if success else 'FAIL'}", flush=True)

    return {
        "strategy_id": campaign_id,
        "cs_variant": cs_variant,
        "cli_name": cli_name,
        "pair": pair,
        "timeframe": timeframe,
        "cost": cost,
        "out_dir": out_dir,
        "success": success,
        "stdout_tail": result.stdout[-500:] if result.stdout else "",
        "stderr_tail": result.stderr[-500:] if result.stderr else "",
    }


def collect_results() -> list[dict]:
    """Collect WFO results from all runs."""
    results = []
    for campaign_id, cs_variant, pair, timeframe, cost in product(
        [s[0] for s in STRATEGIES],
        [s[1] for s in STRATEGIES],
        [p[0] for p in PAIRS],
        [p[1] for p in PAIRS],
        COST_SCENARIOS,
    ):
        out_dir = Path(f"data/backtests/wfo_full/{campaign_id}__{pair}__{timeframe}")
        if not out_dir.exists():
            continue

        # Read the report.json
        report_path = out_dir / "report.json"
        if report_path.exists():
            report = json.loads(report_path.read_text())
            results.append({
                "strategy_id": campaign_id,
                "cs_variant": cs_variant,
                "pair": pair,
                "timeframe": timeframe,
                "cost": cost,
                "sharpe": report.get("sharpe"),
                "profit_factor": report.get("profit_factor"),
                "total_return": report.get("total_return_pct"),
                "max_drawdown": report.get("max_drawdown_pct"),
                "passes_gates": report.get("status") == "PASS",
                "verdict": report.get("status", "UNKNOWN"),
            })
    return results


def generate_campaign_summary(cells: list[dict]) -> dict:
    """Generate campaign_summary.json with Sharpe/PF/return for all cells."""
    return {
        "campaign": "strategy_research_phase3",
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_strategies": len(STRATEGIES),
        "n_cells": len(cells),
        "strategies": [
            {"strategy_id": c["strategy_id"], "passes_gates": c["passes_gates"]}
            for c in cells
        ],
        "cells": cells,
    }


def compute_portfolio_analysis(cells: list[dict]) -> dict:
    """
    Portfolio analysis:
    - Correlation matrix (2×5=10 strategies) using Sharpe ratios across pairs
    - Diversification ratio = avg Sharpe / std of Sharpe across strategies
    """
    import numpy as np

    # Group by strategy, collect Sharpe across pairs/timeframes
    strategy_sharpes: dict[str, list[float]] = {}
    for c in cells:
        sid = c["strategy_id"]
        if c["sharpe"] is not None:
            strategy_sharpes.setdefault(sid, []).append(c["sharpe"])

    # Compute correlation matrix
    strategy_ids = sorted(strategy_sharpes.keys())
    sharpe_matrix = np.array([strategy_sharpes[sid] for sid in strategy_ids])
    # Align lengths (fill with NaN, then use np.corrcoef)
    min_len = min(len(arr) for arr in sharpe_matrix)
    sharpe_matrix_trimmed = np.array([
        arr[:min_len] for arr in sharpe_matrix
    ])

    corr_matrix = np.corrcoef(sharpe_matrix_trimmed) if min_len > 1 else np.eye(len(strategy_ids))

    # Diversification ratio = avg Sharpe / std of avg Sharpe across strategies
    avg_sharpes = [np.mean(arr) for arr in sharpe_matrix]
    avg_sharpe = float(np.mean(avg_sharpes))
    std_sharpe = float(np.std(avg_sharpes))
    diversification_ratio = avg_sharpe / std_sharpe if std_sharpe > 0 else float('inf')

    # Best 5 strategies
    sorted_strategies = sorted(
        zip(strategy_ids, avg_sharpes),
        key=lambda x: x[1],
        reverse=True,
    )[:5]

    return {
        "n_strategies_tested": len(strategy_ids),
        "n_strategies_passing_gates": sum(
            1 for c in cells if c["passes_gates"]
        ),
        "strategy_avg_sharpes": {
            sid: float(np.mean(strategy_sharpes[sid]))
            for sid in strategy_ids
        },
        "correlation_matrix": {
            "strategies": strategy_ids,
            "matrix": corr_matrix.tolist(),
        },
        "best_5_strategies": [
            {"strategy": sid, "avg_sharpe": float(v)}
            for sid, v in sorted_strategies
        ],
        "diversification_ratio": float(diversification_ratio),
    }


def main():
    print("=" * 80)
    print("Phase 3 — Full 8-Strategy WFO Campaign")
    print("=" * 80)

    # Build task list
    tasks = []
    for campaign_id, cs_variant in STRATEGIES:
        for pair, timeframe in PAIRS:
            for cost in COST_SCENARIOS:
                tasks.append((campaign_id, cs_variant, pair, timeframe, cost))

    # Run tasks in parallel using ProcessPoolExecutor
    n_tasks = len(tasks)
    print(f"\nTotal cells to run: {n_tasks}")
    print(f"Strategies: {[t[0] + (f'__{t[1]}' if t[1] else '') for t in STRATEGIES]}")
    print(f"Pairs: {[f'{p}/{t}' for p, t in PAIRS]}")
    print()

    max_workers = min(4, n_tasks)
    results = []

    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(run_single_wfo, task): task for task in tasks}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                task = futures[future]
                print(f"✗ FAILED: {task[0]} {task[1]} {task[2]} {task[3]} — {e}", flush=True)
                results.append({
                    "strategy_id": task[0],
                    "cs_variant": task[1],
                    "pair": task[2],
                    "timeframe": task[3],
                    "success": False,
                    "error": str(e),
                })

            print(f"  Progress: {i}/{n_tasks}", flush=True)

    # Collect results
    cells = collect_results()

    # Generate campaign summary
    summary = generate_campaign_summary(cells)
    summary_path = Path("data/backtests/wfo_full/campaign_summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nCampaign summary written to: {summary_path}")
    print(f"  Cells: {len(cells)}")
    print(f"  Passing gates: {sum(1 for c in cells if c['passes_gates'])}")

    # Portfolio analysis
    portfolio = compute_portfolio_analysis(cells)
    portfolio_path = Path("data/backtests/wfo_full/portfolio_analysis.json")
    portfolio_path.write_text(json.dumps(portfolio, indent=2))
    print(f"\nPortfolio analysis written to: {portfolio_path}")
    print(f"  Strategies tested: {portfolio['n_strategies_tested']}")
    print(f"  Diversification ratio: {portfolio['diversification_ratio']:.2f}")
    print(f"  Best 5: {portfolio['best_5_strategies'][:3]}")


if __name__ == "__main__":
    main()
