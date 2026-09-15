#!/usr/bin/env python3
"""
Re-run broken strategies with fixes + complete remaining cross-sectional/stat_arbitrage.
Skip trend_pullback (already complete with good results).
"""

import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import product
from pathlib import Path

from trading_agent.research.param_grids import get_strategy_code_name

# Only re-run these (trend_pullback already done)
STRATEGIES = [
    ("range_mean_reversion", None),     # Fixed: relaxed conditions
    ("volatility_breakout", None),      # Fixed: removed volume filter, added exit logic
    ("funding_carry", None),            # Fixed: synthetic funding + null-fill
    ("cross_sectional_momentum", "lo"),   # S4lo — new
    ("cross_sectional_momentum", "ls"),  # S4ls — new
    ("stat_arbitrage", "lo"),            # S8lo — fixed LS signal logic
    ("stat_arbitrage", "ls"),            # S8ls — fixed LS signal logic
]

PAIRS = [
    ("BTCUSDT", "1h"),
    ("BTCUSDT", "4h"),
    ("ETHUSDT", "4h"),
    ("SOLUSDT", "4h"),
    ("AVAXUSDT", "4h"),
    ("BNBUSDT", "4h"),
]

COST_SCENARIOS = ["1x"]


def get_full_strategy_id(campaign_id: str, cs_variant: str | None) -> str:
    """Build the full strategy_id (with variant suffix) for CLI + output dir."""
    code_name = get_strategy_code_name(campaign_id)
    if cs_variant:
        return f"{code_name}_{cs_variant}"
    return code_name


def run_single_wfo(args: tuple) -> dict:
    campaign_id, cs_variant, pair, timeframe, cost = args
    cli_name = get_full_strategy_id(campaign_id, cs_variant)

    out_dir = f"data/backtests/wfo_full/{cli_name}__{pair}__{timeframe}"
    cmd = [
        sys.executable, "scripts/run_wfo_parallel.py",
        "--strategy", cli_name,
        "--symbol", f"{pair.replace('USDT', '/USDT')}",
        "--timeframe", timeframe,
        "--cost", cost,
        "--out", out_dir,
        "--workers", "2",
        "--cell-timeout", "900",  # Reduced timeout
        "--train-months", "12",
        "--val-months", "3",
        "--test-months", "3",
        "--step-months", "3",
    ]

    print(f"▶ Starting: {cli_name} {pair} {timeframe} {cost}", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    success = result.returncode == 0
    print(f"✓ Done: {cli_name} {pair} {timeframe} {cost} → {'OK' if success else 'FAIL'}", flush=True)
    if not success:
        print(f"  stderr: {result.stderr[-500:]}", flush=True)

    return {
        "strategy": campaign_id,
        "cs_variant": cs_variant,
        "cli_name": cli_name,
        "pair": pair,
        "timeframe": timeframe,
        "cost": cost,
        "out_dir": out_dir,
        "success": success,
    }


def main():
    print("=" * 80)
    print("Phase 3 — Re-run broken strategies + complete remaining 4 strategies")
    print("=" * 80)

    tasks = []
    for campaign_id, cs_variant in STRATEGIES:
        for pair, timeframe in PAIRS:
            for cost in COST_SCENARIOS:
                cli_name = get_full_strategy_id(campaign_id, cs_variant)
                out_dir = f"data/backtests/wfo_full/{cli_name}__{pair}__{timeframe}"
                if Path(out_dir).exists() and list(Path(out_dir).rglob("report.json")):
                    # Already has reports with the OLD code — re-run with fixed code
                    print(f"  RE-RUNNING: {campaign_id} {pair} {timeframe} (has old reports)")
                    tasks.append((campaign_id, cs_variant, pair, timeframe, cost))
                elif not Path(out_dir).exists():
                    # Directory doesn't exist — needs to run
                    print(f"  NEW: {campaign_id} {pair} {timeframe}")
                    tasks.append((campaign_id, cs_variant, pair, timeframe, cost))
                else:
                    # Dir exists but no reports — incomplete, re-run
                    print(f"  RE-RUNNING: {campaign_id} {pair} {timeframe} (incomplete)")
                    tasks.append((campaign_id, cs_variant, pair, timeframe, cost))

    n_tasks = len(tasks)
    print(f"\nTotal tasks: {n_tasks}")
    print(f"Strategies: {[t[0] + (f'__{t[1]}' if t[1] else '') for t in STRATEGIES]}")
    print()

    max_workers = min(2, n_tasks)  # 2 parallel to fit in 7.7GB RAM (2×2=4 cell workers)
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
                    "strategy": task[0],
                    "cs_variant": task[1],
                    "pair": task[2],
                    "timeframe": task[3],
                    "success": False,
                    "error": str(e),
                })
            print(f"  Progress: {i}/{n_tasks}", flush=True)

    # Collect all results
    print("\n" + "=" * 80)
    print("Collecting results...")
    cells = []
    for pair_dir in sorted(Path("data/backtests/wfo_full").iterdir()):
        if not pair_dir.is_dir():
            continue
        parts = pair_dir.name.split("__")
        if len(parts) < 3:
            continue
        strategy = parts[0]
        pair = parts[1]
        timeframe = parts[2]
        cs_variant = None
        for s in STRATEGIES:
            if get_full_strategy_id(s[0], s[1]) == strategy:
                cs_variant = s[1]
                break

        reports = list(pair_dir.rglob("report.json"))
        for r in reports:
            try:
                data = json.loads(r.read_text())
                cells.append({
                    "strategy": strategy,
                    "cs_variant": cs_variant,
                    "pair": pair,
                    "timeframe": timeframe,
                    "sharpe": data.get("sharpe"),
                    "profit_factor": data.get("profit_factor"),
                    "total_return_pct": data.get("total_return_pct"),
                    "max_drawdown_pct": data.get("max_drawdown_pct"),
                    "total_trades": data.get("total_trades"),
                    "status": data.get("status"),
                })
            except Exception as e:
                print(f"Error reading {r}: {e}")

    # Generate campaign summary
    summary = {
        "campaign": "strategy_research_phase3_full",
        "total_cells": len(cells),
        "cells": cells,
    }
    summary_path = Path("data/backtests/wfo_full/campaign_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\nCampaign summary: {summary_path} ({len(cells)} cells)")

    # Portfolio analysis
    import numpy as np
    from collections import defaultdict

    by_strategy = defaultdict(list)
    for c in cells:
        if c["sharpe"] is not None and c["sharpe"] > 0:
            by_strategy[f"{c['strategy']}_{c['pair']}"].append(c["sharpe"])

    # Group by strategy across pairs for correlation
    by_strat = defaultdict(list)
    for c in cells:
        if c["sharpe"] is not None:
            by_strat[c["strategy"]].append(c["sharpe"])

    strategy_ids = sorted(by_strat.keys())
    min_len = min(len(by_strat[s]) for s in strategy_ids if by_strat[s])
    sharpe_matrix = np.array([
        by_strat[s][:min_len] for s in strategy_ids if by_strat[s]
    ])

    corr = np.corrcoef(sharpe_matrix) if min_len > 1 else np.eye(len(strategy_ids))

    avg_sharpes = [float(np.mean(by_strat[s])) for s in strategy_ids]
    diversification_ratio = float(np.mean(avg_sharpes) / np.std(avg_sharpes)) if np.std(avg_sharpes) > 0 else float('inf')

    best_5 = sorted(zip(strategy_ids, avg_sharpes), key=lambda x: -x[1])[:5]

    portfolio = {
        "n_strategies_tested": len(strategy_ids),
        "n_strategies_passing_gates": sum(1 for c in cells if c.get("status") == "passed"),
        "strategy_avg_sharpes": {s: float(np.mean(by_strat[s])) for s in strategy_ids},
        "correlation_matrix": {
            "strategies": strategy_ids,
            "matrix": corr.tolist(),
        },
        "best_5_strategies": [{"strategy": s, "avg_sharpe": v} for s, v in best_5],
        "diversification_ratio": diversification_ratio,
    }

    portfolio_path = Path("data/backtests/wfo_full/portfolio_analysis.json")
    portfolio_path.write_text(json.dumps(portfolio, indent=2))
    print(f"Portfolio analysis: {portfolio_path}")
    print(f"  Strategies: {portfolio['n_strategies_tested']}")
    print(f"  Diversification ratio: {portfolio['diversification_ratio']:.2f}")
    print(f"  Best 5: {portfolio['best_5_strategies'][:3]}")


if __name__ == "__main__":
    main()
