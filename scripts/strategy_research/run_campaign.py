#!/usr/bin/env python3
"""
Strategy Research Campaign Orchestrator

Runs all 8 strategy candidates across BTC/ETH/SOL/BNB/XRP on 4h (then 1h)
through unified WFO protocol with statistical hardening (DSR/PBO/CSCV),
regime breakdown, and portfolio-level correlation analysis.

Usage:
    python scripts/strategy_research/run_campaign.py --phase single-asset
    python scripts/strategy_research/run_campaign.py --phase cross-asset
    python scripts/strategy_research/run_campaign.py --phase portfolio-analysis
    python scripts/strategy_research/run_campaign.py --phase all

Output:
    data/backtests/wfo/research/
    ├── {strategy_id}__{symbol}_{timeframe}__/
    │   └── report.json (per fold)
    ├── inner_selection_freezes/
    ├── outer_one_shot/
    └── campaign_summary.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import hashlib
import sys
import time
from concurrent.futures import as_completed
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.research.strategy_catalog import (
    STRATEGY_CATALOG,
)
from trading_agent.research.param_grids import (
    get_param_grid,
    get_strategy_code_name,
)
from trading_agent.research.portfolio_analysis import (
    compute_pnl_correlation,
    compute_diversification_ratio,
    compute_marginal_sharpe_contribution,
    compute_drawdown_correlation,
)

import numpy as np  # used in portfolio analysis

logger = logging.getLogger("research_campaign")

ASSETS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT"]
# Single-asset strategies: run on each asset individually
# S1-S3, S5-S7 are single-asset; S4/S8 are cross-sectional (run on full universe)
SINGLE_ASSET_STRATEGIES = [
    "trend_pullback",
    "range_mean_reversion",
    "volatility_breakout",
    "funding_carry",
    "vol_target",
    "regime_ensemble",
]
CROSS_ASSET_STRATEGIES = ["cross_sectional_momentum", "stat_arbitrage"]
FROZEN_INPUTS: dict = {}
JOB_TIMEOUT_SECONDS = 3600


def run_single_strategy_cell(
    strategy_id: str,
    symbol: str,
    timeframe: str,
    out_root: Path,
    workers: int = 4,
) -> dict:
    """Run WFO for one (strategy, symbol, timeframe) combination.

    Uses subprocess to run run_wfo_parallel.py CLI, avoiding nested
    process pools. Each combination runs as its own process.
    """
    import subprocess

    input_record = FROZEN_INPUTS.get((symbol, timeframe))
    if FROZEN_INPUTS and (input_record is None or strategy_id == "funding_carry"):
        return {"strategy_id": strategy_id, "symbol": symbol, "timeframe": timeframe,
                "status": "BLOCKED_DATA", "reason": "No approved scenario-specific frozen input"}
    child_env = os.environ.copy()
    if input_record is not None:
        input_path = Path(input_record["path"])
        if hashlib.sha256(input_path.read_bytes()).hexdigest() != input_record["byte_sha256"]:
            raise ValueError("frozen input changed before subprocess launch")
        child_env["TRADING_FROZEN_RESEARCH_DATA"] = json.dumps({
            "path": str(input_path), "sha256": input_record["byte_sha256"],
            "exchange": "binance", "symbol": symbol, "timeframe": timeframe,
        })

    base_id = strategy_id.split("__")[0]
    cs_variant = strategy_id.split("__")[1] if "__" in strategy_id else None
    code_name = get_strategy_code_name(base_id, cs_variant=cs_variant)
    spec = STRATEGY_CATALOG[base_id]
    param_grid = get_param_grid(base_id, cs_variant=cs_variant)

    if not param_grid:
        return {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "SKIPPED",
            "reason": "No param grid defined",
        }
    if spec.implementation == "TODO":
        return {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "SKIPPED",
            "reason": f"Implementation: {spec.implementation}",
        }

    # Launch subprocess
    out_dir = str(out_root / f"{strategy_id}__{symbol.replace('/', '')}__{timeframe}")
    cmd = [
        sys.executable, "scripts/run_wfo_parallel.py",
        "--strategy", code_name,
        "--symbol", symbol,
        "--timeframe", timeframe,
        "--out", out_dir,
        "--workers", str(workers),
        "--cost", "all",
    ]

    start = time.time()
    job_dir = Path(out_dir)
    job_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = job_dir / "worker_stdout.log"
    stderr_path = job_dir / "worker_stderr.log"
    # Preserve worker output even if the parent session disappears.
    started_path = job_dir / "execution_started.json"
    with started_path.open("x", encoding="utf-8") as stream:
        json.dump({"cmd": cmd, "started_at": datetime.now(timezone.utc).isoformat(),
                   "binding": input_record, "status": "STARTED"}, stream, indent=2)
    try:
        with stdout_path.open("x", encoding="utf-8") as stdout_log, \
                stderr_path.open("x", encoding="utf-8") as stderr_log:
            result = subprocess.run(
                cmd, stdout=stdout_log, stderr=stderr_log, text=True, timeout=JOB_TIMEOUT_SECONDS,
                cwd=str(ROOT), env=child_env,
            )
        elapsed = time.time() - start
        if result.returncode != 0:
            return {
                "strategy_id": strategy_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "status": "ERROR",
                "error": stderr_path.read_text()[-2000:] or "Worker exited without stderr",
            }

        # Parse summary
        summary_path = Path(out_dir) / "parallel_canonical_summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text())
            m = summary.get("aggregate_metrics", {})
            return {
                "strategy_id": strategy_id,
                "symbol": symbol,
                "timeframe": timeframe,
                "status": "PASS" if summary.get("passes_hard_gates") else "FAIL",
                "verdict": summary.get("verdict", "N/A"),
                "passes_hard_gates": bool(summary.get("passes_hard_gates")),
                "n_outer_folds": int(m.get("n_outer_folds", 0)),
                "total_test_trades": int(m.get("total_test_trades", 0)),
                "median_test_sharpe": float(m.get("median_test_sharpe", 0)),
                "median_test_return_pct": float(m.get("median_test_return_pct", 0)),
                "median_profit_factor": float(m.get("median_profit_factor", 0)),
                "positive_outer_folds_pct": float(m.get("positive_outer_folds_pct", 0)),
                "final_holdout_status": summary.get("final_holdout_status"),
                "elapsed_seconds": elapsed,
            }
        return {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "COMPLETED",
            "elapsed_seconds": elapsed,
        }
    except subprocess.TimeoutExpired:
        return {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "TIMEOUT",
            "elapsed_seconds": time.time() - start,
        }
    except Exception as exc:
        return {
            "strategy_id": strategy_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "status": "ERROR",
            "error": str(exc),
        }


def collect_return_series(
    out_root: Path,
) -> dict[str, dict[str, list[float]]]:
    """
    Collect return series for portfolio analysis.

    Returns dict: strategy_id → symbol → return_series
    """
    results: dict[str, dict[str, list[float]]] = {}

    for strategy_dir in sorted(out_root.iterdir()):
        if not strategy_dir.is_dir() or "__" not in strategy_dir.name:
            continue
        parts = strategy_dir.name.split("__")
        if len(parts) < 3:
            continue
        strategy_id = parts[0]
        symbol = parts[1]

        # Look for outer one-shot reports which have return series
        one_shot_dir = strategy_dir / "outer_one_shot"
        if not one_shot_dir.exists():
            continue

        return_series: list[float] = []
        for report_file in sorted(one_shot_dir.glob("*.json")):
            try:
                report = json.loads(report_file.read_text())
                returns = report.get("return_series") or report.get("metrics", {}).get("return_series")
                if isinstance(returns, list):
                    return_series.extend(float(r) for r in returns)
            except Exception:
                pass

        if return_series:
            results.setdefault(strategy_id, {})[symbol] = return_series

    return results


def run_portfolio_analysis(
    out_root: Path,
    campaign_summary: dict,
) -> dict:
    """Run portfolio-level correlation and diversification analysis."""

    # Collect return series
    return_series_map = collect_return_series(out_root)

    if not return_series_map:
        return {"status": "no data"}

    # Build per-strategy aggregate returns (concatenate all assets)
    strategy_returns: dict[str, list[float]] = {}
    strategy_equity: dict[str, list[float]] = {}

    for strategy_id, asset_returns in return_series_map.items():
        # Aggregate across assets (equal-weight)
        all_series = list(asset_returns.values())
        if not all_series:
            continue

        min_len = min(len(s) for s in all_series)
        if min_len < 3:
            continue

        aligned = [np.array(s[-min_len:]) for s in all_series]
        # Equal-weight portfolio of assets for this strategy
        avg_returns = np.mean(aligned, axis=0)

        # Convert to cumulative equity curve
        cum_ret = np.cumprod(1 + avg_returns) * 10000
        equity = cum_ret.tolist()

        strategy_returns[strategy_id] = avg_returns.tolist()
        strategy_equity[strategy_id] = equity

    if len(strategy_returns) < 2:
        return {"status": "insufficient strategies", "n_strategies": len(strategy_returns)}

    # Compute correlations
    corr = compute_pnl_correlation(strategy_returns)
    dd_corr = compute_drawdown_correlation(strategy_equity)
    dr = compute_diversification_ratio(strategy_returns)

    # Marginal Sharpe contribution
    mscr: dict[str, float] = {}
    for s in strategy_returns:
        mscr[s] = compute_marginal_sharpe_contribution(strategy_returns, s)

    return {
        "n_strategies": len(strategy_returns),
        "pnl_correlation": corr,
        "drawdown_correlation": dd_corr,
        "diversification_ratio": dr,
        "marginal_sharpe_contribution": mscr,
    }


def run_single_asset_phase(
    out_root: Path,
    assets: list[str],
    timeframes: list[str],
    workers: int = 4,
    strategies: list[str] | None = None,
) -> list[dict]:
    """Run all single-asset strategies across assets and timeframes."""

    results = []
    cells = []
    for strategy_id in SINGLE_ASSET_STRATEGIES:
        if strategies is not None and strategy_id not in strategies:
            continue
        spec = STRATEGY_CATALOG[strategy_id]
        if spec.implementation == "TODO":
            results.append({
                "strategy_id": strategy_id,
                "status": "SKIPPED",
                "reason": f"Implementation: {spec.implementation}",
            })
            continue
        for asset in assets:
            for tf in timeframes:
                cells.append((strategy_id, asset, tf))

    print(f"Single-asset phase: {len(cells)} job(s), {workers} workers")

    results = []
    completed = 0
    # Run cells in parallel using ThreadPool (each cell spawns a subprocess)
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(run_single_strategy_cell, sid, asset, tf, out_root, 2): (sid, asset, tf)
            for sid, asset, tf in cells
        }
        for future in as_completed(future_map):
            sid, asset, tf = future_map[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"strategy_id": sid, "symbol": asset, "timeframe": tf,
                          "status": "ERROR", "error": str(exc)}
            results.append(result)
            completed += 1
            status = result.get("status", "?")
            sharpe = result.get("median_test_sharpe", 0)
            print(f"  [{completed}/{len(cells)}] {sid} | {asset} | {tf} → {status} (Sharpe={sharpe:.3f})")

    # Prove the run measured everything it asked for. This is the check whose
    # absence let a65ed29000's first bug run silently: strategies whose params
    # failed validation produced no cell, and "completed" counted artifacts
    # rather than strategies, so the campaign reported success having skipped
    # part of its own plan.
    _assert_coverage(cells, results, out_root)
    return results


def _assert_coverage(cells, results, out_root: Path) -> None:
    from trading_agent.backtest.campaign_integrity import (
        verify_campaign_coverage,
    )

    requested = {get_strategy_code_name(sid) for sid, _asset, _tf in cells}
    canonical_results = [{**r, "strategy_id": get_strategy_code_name(r["strategy_id"])}
                         if r.get("strategy_id") else r for r in results]
    report = verify_campaign_coverage(
        requested_strategies=requested, out_root=out_root, results=canonical_results
    )
    print(report.summary())
    if not report.ok:
        raise SystemExit(
            "campaign incomplete: strategies were requested that produced no "
            "cells. Treating this as success is what produced unreproducible "
            "evidence before; refusing to exit 0 until the gap is explained."
        )


def run_cross_asset_phase(
    out_root: Path,
    assets: list[str],
    timeframes: list[str],
    workers: int = 4,
) -> list[dict]:
    """Run cross-sectional strategies (long-only and long-short variants)."""

    results = []
    completed = 0
    for strategy_id in CROSS_ASSET_STRATEGIES:
        spec = STRATEGY_CATALOG[strategy_id]
        if spec.implementation == "TODO":
            for variant in ["long_only", "long_short"]:
                for tf in timeframes:
                    results.append({
                        "strategy_id": strategy_id,
                        "cs_variant": variant,
                        "timeframe": tf,
                        "status": "SKIPPED",
                        "reason": f"Implementation: {spec.implementation}",
                    })
            continue

        for cs_variant in ["long_only", "long_short"]:
            for tf in timeframes:
                # Cross-sectional strategies run on the full universe
                result = run_single_strategy_cell(
                    f"{strategy_id}__{cs_variant}", "UNIVERSE", tf, out_root, workers
                )
                result["cs_variant"] = cs_variant
                results.append(result)
                completed += 1
                status = result.get("status", "?")
                print(f"  [{completed}] {strategy_id}[{cs_variant}] | UNIVERSE | {tf} → {status}")

    return results


def main():
    global FROZEN_INPUTS, JOB_TIMEOUT_SECONDS
    parser = argparse.ArgumentParser(description="Strategy Research Campaign")
    parser.add_argument(
        "--phase",
        choices=["single-asset", "cross-asset", "portfolio-analysis", "all"],
        default="all",
    )
    parser.add_argument("--assets", nargs="+", default=ASSETS)
    parser.add_argument("--timeframes", nargs="+", default=["4h", "1h"])
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--out", default="data/backtests/wfo/research")
    parser.add_argument(
        "--strategies", nargs="+",
        help="Limit to specific strategy IDs (overrides default universe)",
    )
    parser.add_argument("--input-manifest", type=Path,
                        help="Audited per-subject pre-holdout manifest; forwarded to child workers")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--job-timeout-seconds", type=int, default=3600)
    args = parser.parse_args()
    if args.job_timeout_seconds <= 0:
        parser.error("job timeout must be positive")
    JOB_TIMEOUT_SECONDS = args.job_timeout_seconds
    if args.input_manifest:
        manifest = json.loads(args.input_manifest.read_text())
        FROZEN_INPUTS = {(r["symbol"], r["timeframe"]): r for r in manifest["inputs"]
                         if r["status"] == "FROZEN"}
        if not FROZEN_INPUTS:
            raise ValueError("manifest contains no frozen inputs")
    if args.preflight_only:
        for sid in SINGLE_ASSET_STRATEGIES:
            if args.strategies and sid not in args.strategies:
                continue
            for symbol in args.assets:
                for tf in args.timeframes:
                    record = FROZEN_INPUTS.get((symbol, tf))
                    status = "READY_INPUT" if record and sid != "funding_carry" else "BLOCKED_DATA"
                    print(json.dumps({"strategy": sid, "symbol": symbol,
                                      "timeframe": tf, "status": status,
                                      "grid": get_param_grid(sid)}))
        print("Cross-asset requires separately bound universe/scenario inputs; no execution performed")
        return

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    all_results: list[dict] = []
    start_time = time.time()

    # ── Phase 1: Single-Asset Strategies ─────────────────────────────────
    if args.phase in ("single-asset", "all"):
        single_results = run_single_asset_phase(
            out_root, args.assets, args.timeframes, args.workers, args.strategies,
        )
        all_results.extend(single_results)

    # ── Phase 2: Cross-Asset Strategies ────────────────────────────────
    if args.phase in ("cross-asset", "all"):
        cross_results = run_cross_asset_phase(
            out_root, args.assets, args.timeframes, args.workers
        )
        all_results.extend(cross_results)

    # ── Phase 3: Portfolio Analysis ──────────────────────────────────────
    portfolio_results = {}
    if args.phase in ("portfolio-analysis", "all"):
        portfolio_results = run_portfolio_analysis(out_root, all_results)

    elapsed = time.time() - start_time

    # ── Save Campaign Summary ────────────────────────────────────────────
    summary = {
        "campaign_id": f"research_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "assets": args.assets,
        "timeframes": args.timeframes,
        "strategies_attempted": [
            r.get("strategy_id") for r in all_results if r.get("strategy_id")
        ],
        "results": all_results,
        "portfolio_analysis": portfolio_results,
        "elapsed_seconds": elapsed,
        "total_cells": len(all_results),
    }

    summary_path = out_root / "campaign_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nSummary saved → {summary_path}")

    # ── Quick stats ─────────────────────────────────────────────────────
    passed = [r for r in all_results if r.get("status") == "PASS"]
    failed = [r for r in all_results if r.get("status") == "FAIL"]
    skipped = [r for r in all_results if r.get("status") == "SKIPPED"]
    errored = [r for r in all_results if r.get("status") == "ERROR"]

    print(f"\n{'='*50}")
    print("Campaign Summary")
    print(f"{'='*50}")
    print(f"  Total cells: {len(all_results)}")
    print(f"  PASS: {len(passed)}  FAIL: {len(failed)}  SKIPPED: {len(skipped)}  ERROR: {len(errored)}")
    if portfolio_results.get("pnl_correlation"):
        corr = portfolio_results["pnl_correlation"]
        print(f"  Strategies correlated: {corr.get('n_strategies', 0)}")
        print(f"  Mean PnL correlation: {corr.get('mean_corr', 0):.3f}")
        print(f"  Diversification Ratio: {portfolio_results.get('diversification_ratio', 0):.3f}")


if __name__ == "__main__":
    main()
