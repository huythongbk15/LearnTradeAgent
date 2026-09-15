#!/usr/bin/env python3
"""Direct (sequential) WFO runner for funding_carry BTC/USDT 1h.

Bypasses ProcessPoolExecutor entirely — runs cells one-by-one in the
main process to avoid multiprocess spawn issues in this environment.
"""
from __future__ import annotations

import itertools
import json
import logging
import sys
import time
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fc_wfo_direct")

# Ensure src is on the path
ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from trading_agent.backtest.tournament import run_cell
from trading_agent.backtest.nested_wfo import EvaluationCellSpec
from trading_agent.trading.costs import CostScenario, DEFAULT_SCENARIOS

STRATEGY = "funding_carry"
SYMBOL = "BTCUSDT"
TIMEFRAME = "1h"
COST = CostScenario(DEFAULT_SCENARIOS["1x"])
OUT_DIR = ROOT / "data" / "backtests" / "wfo_funding_carry_only"
LOG_FILE = Path("/tmp/fc_wfo_direct.log")

PARAM_GRID = {
    "funding_entry_threshold": [-0.00005, -0.00003],
    "funding_exit_threshold": [0.00008, 0.0001],
    "max_hold_periods": [0],
    "vol_window": [20],
    "fr_lookback_bars": [22],
}

TRAIN_MONTHS = 12
VAL_MONTHS = 3
TEST_MONTHS = 3
STEP_MONTHS = 3


def expand_grid(grid: dict) -> list[dict]:
    keys = list(grid.keys())
    return [dict(zip(keys, combo)) for combo in itertools.product(*[grid[k] for k in keys])]


def main() -> None:
    combos = expand_grid(PARAM_GRID)
    logger.info(f"Param grid: {len(combos)} combos")
    logger.info(f"Output: {OUT_DIR}")
    logger.info(f"Combos: {combos}")

    # First, discover how many folds are available by looking at one cell
    # We'll try fold 0 first
    all_results = []

    for i, params in enumerate(combos):
        logger.info(f"=== Combo {i+1}/{len(combos)}: {params} ===")
        fold = 0
        fold_num = 0
        while True:
            spec = EvaluationCellSpec(
                strategy_id=STRATEGY,
                symbol=SYMBOL,
                timeframe=TIMEFRAME,
                params=params,
                cost_scenario=COST,
            )
            try:
                result = run_cell(
                    spec,
                    out_root=OUT_DIR,
                    fold_index=fold,
                    train_months=TRAIN_MONTHS,
                    val_months=VAL_MONTHS,
                    test_months=TEST_MONTHS,
                    step_months=STEP_MONTHS,
                    run_holdout=True,
                    real_sensitivity=True,
                    timeout_seconds=600,
                    max_retries=1,
                    _use_multiprocessing=False,
                )
                fold_num += 1
                trades = result.metrics.total_trades
                ret = result.metrics.total_return_pct
                sharpe = result.metrics.sharpe
                logger.info(f"  fold {fold}: {trades} trades, return={ret:.2f}%, sharpe={sharpe:.2f}")
                all_results.append({
                    "combo_idx": i,
                    "params": params,
                    "fold": fold,
                    "trades": trades,
                    "return": ret,
                    "sharpe": sharpe,
                })
                # Increment fold for next iteration
                fold += 1
                if fold > 15:  # safety limit
                    break
            except StopIteration:
                logger.info(f"  No more folds (stopped at fold {fold})")
                break
            except Exception as e:
                logger.error(f"  Error at fold {fold}: {type(e).__name__}: {e}")
                break

        # Log to file
        with open(LOG_FILE, "a") as f:
            f.write(f"=== Combo {i+1}/{len(combos)}: {params} ===\n")
            for r in all_results[-fold_num:]:
                f.write(f"  fold {r['fold']}: {r['trades']} trades, return={r['return']:.2f}%, sharpe={r['sharpe']:.2f}\n")

    # Summary
    logger.info("\n=== SUMMARY ===")
    for r in all_results:
        logger.info(f"combo {r['combo_idx']} fold {r['fold']}: entry={r['params']['funding_entry_threshold']} exit={r['params']['funding_exit_threshold']} -> trades={r['trades']}, return={r['return']:.2f}%, sharpe={r['sharpe']:.2f}")

    # Save summary
    summary_file = OUT_DIR / "funding_carry_summary.json"
    summary_file.parent.mkdir(parents=True, exist_ok=True)
    summary_file.write_text(json.dumps(all_results, indent=2, default=str))
    logger.info(f"Summary saved to {summary_file}")


if __name__ == "__main__":
    main()
