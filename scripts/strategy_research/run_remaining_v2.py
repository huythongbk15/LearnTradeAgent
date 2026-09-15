#!/usr/bin/env python3
"""
Run remaining strategies with proper settings:
- Fix range MR, vol_breakout, funding_carry re-runs (old code produced 0 trades)
- Run cross-sectional momentum (lo/ls) and stat_arb (lo/ls)
- Sequential execution to avoid resource contention
- Shorter cell_timeout for faster completion
"""

import json
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from trading_agent.research.param_grids import get_strategy_code_name, get_param_grid

STRATEGIES = [
    ("range_mean_reversion", None),           # Fixed code
    ("volatility_breakout", None),             # Fixed code
    ("funding_carry", None),                   # Fixed code
    ("cross_sectional_momentum", "lo"),        # S4lo
    ("cross_sectional_momentum", "ls"),        # S4ls
    ("stat_arbitrage", "lo"),                  # S8lo
    ("stat_arbitrage", "ls"),                  # S8ls
]

PAIRS = [
    ("BTCUSDT", "1h"),
    ("BTCUSDT", "4h"),
    ("ETHUSDT", "4h"),
    ("SOLUSDT", "4h"),
    ("AVAXUSDT", "4h"),
    ("BNBUSDT", "4h"),
]


def get_cli_name(campaign_id: str, cs_variant: str | None) -> str:
    code = get_strategy_code_name(campaign_id)
    if cs_variant:
        return f"{code}_{cs_variant}"
    return code


def run_single_wfo(args: tuple) -> dict:
    campaign_id, cs_variant, pair, timeframe = args
    cli_name = get_cli_name(campaign_id, cs_variant)

    # Compute param grid size to estimate runtime
    full_cs = "long_only" if cs_variant == "lo" else "long_short" if cs_variant == "ls" else None
    grid = get_param_grid(campaign_id, full_cs)
    n_params = 1
    for v in grid.values():
        n_params *= len(v)

    # Use more workers and longer timeouts for computationally intensive strategies
    # (cross_sectional_momentum, stat_arbitrage need more time per cell)
    is_cs_or_statarb = campaign_id in ("cross_sectional_momentum", "stat_arbitrage")
    workers = 4 if is_cs_or_statarb else (2 if n_params >= 8 else 1)
    cell_timeout = 1800 if is_cs_or_statarb else 600
    # Cross-sectional momentum has 12 params × ~8 folds = ~96 cells
    # With 4 workers: ~24 cell-slots. Allow up to 2h per task.
    subprocess_timeout = 7200 if campaign_id == "cross_sectional_momentum" else (
        3600 if is_cs_or_statarb else 1800
    )
    out_dir = f"data/backtests/wfo_full/{campaign_id}__{pair}__{timeframe}"

    cmd = [
        sys.executable, "scripts/run_wfo_parallel.py",
        "--strategy", cli_name,
        "--symbol", f"{pair.replace('USDT', '/USDT')}",
        "--timeframe", timeframe,
        "--cost", "1x",
        "--out", out_dir,
        f"--workers={workers}",
        "--cell-timeout", str(cell_timeout),
        "--train-months", "12",
        "--val-months", "3",
        "--test-months", "3",
        "--step-months", "3",
    ]

    print(f"▶ {cli_name} {pair} {timeframe} ({n_params} params, {workers} workers, cell_timeout={cell_timeout}s)", flush=True)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=subprocess_timeout)
    success = result.returncode == 0

    # Count reports
    reports = list(Path(out_dir).rglob("report.json"))
    print(f"✓ {cli_name} {pair} {timeframe} → {'OK' if success else 'FAIL'} ({len(reports)} reports)", flush=True)
    if not success:
        stderr_lines = result.stderr.strip().split('\n')[-5:]
        print(f"  stderr tail: {stderr_lines}", flush=True)

    return {
        "strategy": campaign_id,
        "cs_variant": cs_variant,
        "cli_name": cli_name,
        "pair": pair,
        "timeframe": timeframe,
        "n_params": n_params,
        "n_reports": len(reports),
        "success": success,
    }


def collect_all_results() -> list[dict]:
    """Collect results from all report.json files across all strategies."""
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

        # Determine cs_variant from strategy name
        cs_variant = None
        if strategy.endswith("_lo"):
            cs_variant = "lo"
            strategy = strategy[:-3]
        elif strategy.endswith("_ls"):
            cs_variant = "ls"
            strategy = strategy[:-3]

        for r in pair_dir.rglob("report.json"):
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
    return cells


def generate_portfolio_analysis(cells: list[dict]) -> dict:
    """Portfolio analysis: correlation matrix + diversification ratio."""
    import numpy as np
    from collections import defaultdict

    by_strat = defaultdict(list)
    for c in cells:
        if c["sharpe"] is not None:
            key = c["strategy"]
            if c["cs_variant"]:
                key = f"{c['strategy']}_{c['cs_variant']}"
            by_strat[key].append(c["sharpe"])

    strategy_ids = sorted(by_strat.keys())
    avg_sharpes = {s: float(np.mean(by_strat[s])) for s in strategy_ids}

    # Correlation matrix (across pairs for each strategy)
    min_len = min(len(by_strat[s]) for s in strategy_ids) if strategy_ids else 0
    if min_len > 1:
        sharpe_matrix = np.array([
            by_strat[s][:min_len] for s in strategy_ids
        ])
        corr = np.corrcoef(sharpe_matrix).tolist()
    else:
        corr = [[1.0 if i == j else 0.0 for j in range(len(strategy_ids))] for i in range(len(strategy_ids))]

    avg_sharpe = float(np.mean(list(avg_sharpes.values())))
    std_sharpe = float(np.std(list(avg_sharpes.values())))
    diversification_ratio = avg_sharpe / std_sharpe if std_sharpe > 0 else 0.0

    best_5 = sorted(avg_sharpes.items(), key=lambda x: -x[1])[:5]

    n_passing = sum(1 for c in cells if c.get("status") == "passed")
    n_good = sum(1 for c in cells if c.get("sharpe") and c["sharpe"] > 0.5)

    return {
        "total_cells": len(cells),
        "n_strategies": len(strategy_ids),
        "n_strategies_passing_gates": n_passing,
        "n_cells_sharpe_gt_05": n_good,
        "strategy_avg_sharpes": avg_sharpes,
        "correlation_matrix": {
            "strategies": strategy_ids,
            "matrix": corr,
        },
        "best_5_strategies": [{"strategy": s, "avg_sharpe": v} for s, v in best_5],
        "diversification_ratio": diversification_ratio,
    }


def main():
    print("=" * 80)
    print("Phase 3 — Remaining WFO Campaign (v2)")
    print("=" * 80)

    # Build task list
    tasks = []
    for campaign_id, cs_variant in STRATEGIES:
        for pair, timeframe in PAIRS:
            out_dir = f"data/backtests/wfo_full/{campaign_id}__{pair}__{timeframe}"
            if Path(out_dir).exists():
                reports = list(Path(out_dir).rglob("report.json"))
                if reports:
                    print(f"  SKIP (has reports): {campaign_id} {pair} {timeframe}")
                    continue
            tasks.append((campaign_id, cs_variant, pair, timeframe))

    print(f"\nTotal tasks to run: {len(tasks)}")
    for t in tasks:
        print(f"  {t[0]}:{t[1]} {t[2]} {t[3]}")
    print()

    # Run sequentially (1 worker) to avoid resource contention
    results = []
    for i, task in enumerate(tasks, 1):
        print(f"\n[{i}/{len(tasks)}]", flush=True)
        result = run_single_wfo(task)
        results.append(result)

    # Collect all results
    cells = collect_all_results()
    summary = {
        "campaign": "strategy_research_phase3_full",
        "total_cells": len(cells),
        "cells": cells,
    }
    summary_path = Path("data/backtests/wfo_full/campaign_summary.json")
    summary_path.write_text(json.dumps(summary, indent=2))
    print(f"\n{'='*80}")
    print(f"Campaign summary: {summary_path} ({len(cells)} cells)")

    # Portfolio analysis
    portfolio = generate_portfolio_analysis(cells)
    portfolio_path = Path("data/backtests/wfo_full/portfolio_analysis.json")
    portfolio_path.write_text(json.dumps(portfolio, indent=2))
    print(f"Portfolio analysis: {portfolio_path}")
    print(f"  Strategies: {portfolio['n_strategies']}")
    print(f"  Cells with Sharpe > 0.5: {portfolio['n_cells_sharpe_gt_05']}")
    print(f"  Diversification ratio: {portfolio['diversification_ratio']:.2f}")
    print(f"  Best 5: {portfolio['best_5_strategies'][:3]}")


if __name__ == "__main__":
    main()
