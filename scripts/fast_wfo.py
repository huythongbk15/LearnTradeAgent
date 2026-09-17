#!/usr/bin/env python3
"""Fast WFO runner — uses BacktestEngine (vectorized) instead of FullSystemSimulator.

Speedup: ~2600x for fast strategies (0.05s vs 130s per cell), ~2.4x for
regime_switching (54s vs 130s). Designed for tournament pool calibration:
7 strategies x 4 pairs in ~2-3 min on 12 cores (OMP_NUM_THREADS=1).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from itertools import product
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.data.storage import load_ohlcv
from trading_agent.backtest.engine import BacktestEngine
from trading_agent.strategies.enhanced_ma import (
    MaAdxCrossover, EnhancedMaCrossover, MaVolTargetCrossover,
)
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.volatility_breakout import VolatilityBreakoutStrategy
from trading_agent.strategies.funding_carry import FundingCarryStrategy
from trading_agent.strategies.regime_switching import RegimeSwitchingStrategy
from trading_agent.strategies.trend_pullback import TrendPullbackStrategy

STRATEGY_SPECS = {
    "ma_adx": {"cls": MaAdxCrossover, "grid": {"fast_period": [10, 20, 30], "slow_period": [40, 60, 80],
               "adx_period": [14], "adx_threshold": [30]}, "warmup": 50},
    "enhanced_ma": {"cls": EnhancedMaCrossover, "grid": {"fast_period": [10, 20], "slow_period": [40, 60, 80],
               "adx_period": [14], "adx_threshold": [30], "require_close_above_slow": [False], "momentum_period": [0],
               "atr_period": [14], "atr_sl_mult": [2.0], "atr_tp_mult": [3.0], "max_dd_pct": [0.15],
               "dd_cooldown_bars": [0], "dd_recovery_pct": [0.03], "trailing_atr_mult": [0.0], "risk_per_trade": [0.02]}, "warmup": 60},
    "rsi": {"cls": RsiStrategy, "grid": {"period": [14, 21], "oversold": [30, 25], "overbought": [70, 75]}, "warmup": 30},
    "bbands": {"cls": BBandsStrategy, "grid": {"period": [20, 21], "std_dev": [2.0, 2.5]}, "warmup": 40},
    "ma_vol_target": {"cls": MaVolTargetCrossover, "grid": {"fast_period": [20, 30], "slow_period": [60, 80]}, "warmup": 50},
    "volatility_breakout": {"cls": VolatilityBreakoutStrategy, "grid": {"bb_period": [14, 20, 21], "bb_std": [2.0],
               "compression_percentile": [0.03, 0.05], "atr_spike_mult": [1.5, 2.0], "max_hold_bars": [10, 20]}, "warmup": 60},
    "funding_carry": {"cls": FundingCarryStrategy, "grid": {"funding_entry_threshold": [-0.0001, -0.00008, -0.00005, -0.00003],
               "funding_exit_threshold": [0.0, 0.00005], "max_hold_periods": [0], "vol_window": [20], "fr_lookback_bars": [22]}, "warmup": 50},
    "regime_switching": {"cls": RegimeSwitchingStrategy, "grid": {"regime_method": ["rule_based", "hybrid"],
               "min_confidence": [0.4, 0.65], "regime_smoothing": [2, 3], "base_position_pct": [0.1, 0.2]}, "warmup": 200},
    "trend_pullback": {"cls": TrendPullbackStrategy, "grid": {"ma_fast": [5, 10, 20, 30],
               "ma_slow": [50, 80, 120, 200], "adx_threshold": [15, 20, 25],
               "adx_period": [14], "rsi_period": [14], "vol_multiplier": [0.5, 1.0, 1.5]}, "warmup": 150},
}

PAIRS = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "XRP/USDT"]
TIMEFRAME = "1h"
TRAIN_MONTHS, VAL_MONTHS, TEST_MONTHS, STEP_MONTHS = 12, 3, 3, 3


def _min_oos_trades(timeframe: str) -> int:
    """Timeframe-aware minimum OOS trades per fold (higher TF → fewer trades expected)."""
    tf = timeframe.lower()
    if tf.endswith("d") and tf[:-1].isdigit():
        return 3  # daily: trend-following naturally low-frequency; 3+ trades per fold acceptable
    if tf.endswith("h") and int(tf[:-1]) >= 4:
        return 15  # 4h+: ~15 trades per fold
    return 30  # 1h default


def _bars_per_month(timeframe: str) -> int:
    """Map timeframe string to approximate bars per calendar month."""
    tf = timeframe.lower()
    if tf.endswith("m") and tf[:-1].isdigit():
        return int(30 * 24 * 60 / int(tf[:-1]))
    if tf.endswith("h") and tf[:-1].isdigit():
        return int(30 * 24 / int(tf[:-1]))
    if tf.endswith("d") and tf[:-1].isdigit():
        return int(30 / int(tf[:-1]))
    return 720  # default: 1h


def compute_folds(n_bars: int, bars_per_month: int) -> list[dict]:
    train_bars = TRAIN_MONTHS * bars_per_month
    val_bars = VAL_MONTHS * bars_per_month
    test_bars = TEST_MONTHS * bars_per_month
    total = train_bars + val_bars + test_bars
    step = STEP_MONTHS * bars_per_month
    folds = []
    start = 0
    while start + total <= n_bars:
        tr_s, tr_e = start, start + train_bars
        val_s, val_e = tr_e, tr_e + val_bars
        test_s, test_e = val_e, val_e + test_bars
        folds.append({
            "fold_id": len(folds),
            "train_start": tr_s, "train_end": tr_e,
            "val_start": val_s, "val_end": val_e,
            "test_start": test_s, "test_end": test_e,
        })
        start += step
    return folds


def enumerate_grid(grid: dict) -> list[dict]:
    keys = list(grid.keys())
    return [dict(zip(keys, c)) for c in product(*[grid[k] for k in keys])]


def _backtest(
    strategy_cls, params: dict, df, start: int, end: int, warmup: int,
) -> dict:
    """Run one BacktestEngine cell, return metrics dict."""
    window = df[start:end]
    if len(window) < warmup + 1:
        return {"status": "SKIP", "reason": "insufficient_data",
                "sharpe": -999, "return": 0, "trades": 0, "max_dd": 0, "calmar": 0}

    try:
        strategy = strategy_cls(params=params)
        engine = BacktestEngine(
            strategy=strategy, initial_capital=10000,
            commission=0.0005, slippage=0.0002,
            long_only=True, timeframe="1h",
        )
        result = engine.run(window)

        eq = result.equity_curve["equity"].to_numpy() if len(result.equity_curve) > 0 else np.array([10000])
        if len(eq) > 1:
            rets = np.diff(np.log(eq))
            std = np.std(rets) if len(rets) > 1 else 0
            sharpe = float(np.sqrt(365 * 24) * np.mean(rets) / std) if std > 0 else 0.0
        else:
            sharpe = 0.0

        max_dd = float(result.max_drawdown_pct) if result.max_drawdown_pct else 0.0
        calmar = float(result.total_return_pct) / abs(max_dd) if max_dd != 0 else 0.0

        metrics = {
            "status": "COMPLETED",
            "sharpe": sharpe,
            "return": float(result.total_return_pct),
            "max_dd": max_dd,
            "trades": int(result.total_trades),
            "calmar": calmar,
        }
        del engine, strategy, result  # Release HMM model + engine before caller GC
        return metrics
    except Exception as exc:
        return {"status": "FAILED", "reason": f"{type(exc).__name__}: {exc}",
                "sharpe": -999, "return": 0, "trades": 0, "max_dd": 0, "calmar": 0}


# Shared DataFrame (set before forking so children inherit via COW)
_SHARED_DF = None

import gc

# Shared DataFrame (set before forking so children inherit via COW)
_SHARED_DF = None

def _backtest_cell(args):
    """Module-level wrapper for multiprocessing (picklable)."""
    strategy_id, params, start, end, warmup = args
    spec = STRATEGY_SPECS[strategy_id]
    return params, _backtest(spec["cls"], params, _SHARED_DF, start, end, warmup)


def run_fast_wfo(strategy_id: str, symbol: str, timeframe: str = "1h", workers: int = 1) -> dict:
    spec = STRATEGY_SPECS[strategy_id]
    cls = spec["cls"]
    grid = spec["grid"]
    warmup = spec["warmup"]
    combos = enumerate_grid(grid)

    symbol_raw = symbol.replace("/", "_")
    global _SHARED_DF
    _SHARED_DF = load_ohlcv("binance", symbol_raw, timeframe).sort("timestamp")
    n_bars = _SHARED_DF.height
    bpm = _bars_per_month(timeframe)
    folds = compute_folds(n_bars, bpm)

    total_cells = len(combos) * len(folds)
    print(f"  [{strategy_id} {symbol}] {len(combos)} params × {len(folds)} folds = {total_cells} cells, warmup={warmup}, workers={workers}", flush=True)

    # Phase 1: Inner validation — find best params per fold
    fold_best = []
    pool = None
    if workers > 1:
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        pool = ctx.Pool(processes=workers)

    for fi, fold in enumerate(folds):
        sim_start = max(0, fold["train_start"] - warmup - 200)
        val_end = fold["val_end"]
        cell_args = [(strategy_id, p, sim_start, val_end, warmup) for p in combos]

        fold_start = time.time()
        if pool is not None:
            results = pool.map(_backtest_cell, cell_args)
            gc.collect()
        else:
            results = []
            for p in combos:
                results.append((p, _backtest(cls, p, _SHARED_DF, sim_start, val_end, warmup)))
                gc.collect()  # Per-cell GC to prevent HMM model accumulation

        best_sharpe = -999
        best_params = None
        best_metrics = None
        for p, m in results:
            if m["status"] == "COMPLETED" and m["sharpe"] > best_sharpe:
                best_sharpe = m["sharpe"]
                best_params = p
                best_metrics = m

        fold_time = time.time() - fold_start
        print(f"    fold {fi}: {fold_time:.1f}s", flush=True)

        if best_params is None:
            best_params = combos[0]
            best_metrics = {"sharpe": 0, "return": 0, "trades": 0, "max_dd": 0, "calmar": 0}

        fold_best.append((best_params, best_metrics))

    if pool is not None:
        pool.close()
        pool.join()

    # Phase 2: Outer OOS test with frozen params
    test_metrics = []
    for fold, (best_params, _) in zip(folds, fold_best):
        test_sim_start = max(0, fold["test_start"] - warmup - 200)
        m = _backtest(cls, best_params, _SHARED_DF, test_sim_start, fold["test_end"], warmup)
        test_metrics.append(m)

    # Aggregate
    oos = [m for m in test_metrics if m["status"] == "COMPLETED"]
    test_sharpes = [m["sharpe"] for m in oos]
    test_returns = [m["return"] for m in oos]
    test_trades = [m["trades"] for m in oos]
    test_maxdds = [m["max_dd"] for m in oos]

    med_sharpe = float(np.median(test_sharpes)) if test_sharpes else -999
    med_return = float(np.median(test_returns)) if test_returns else 0
    med_trades = float(np.median(test_trades)) if test_trades else 0
    med_dd = float(np.median(test_maxdds)) if test_maxdds else 0
    med_calmar = med_return / abs(med_dd) if med_dd != 0 else 0
    total_oos_trades = sum(test_trades)
    no_trade = total_oos_trades == 0
    min_trades = _min_oos_trades(timeframe)

    # Portfolio gates
    gates = {
        "positive_sharpe": {"pass": med_sharpe >= 0.0, "observed": med_sharpe, "threshold": 0.0},
        "min_trades": {"pass": med_trades >= min_trades, "observed": med_trades, "threshold": min_trades},
        "max_dd_below_40pct": {"pass": med_dd <= 40.0, "observed": med_dd, "threshold": 40.0},
    }
    passes_hard_gates = all(g["pass"] for g in gates.values()) and med_sharpe > 0

    return {
        "strategy": strategy_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "param_combos": len(combos),
        "n_folds": len(folds),
        "total_cells": total_cells,
        "verdict": "PASS" if passes_hard_gates else "FAIL",
        "passes_hard_gates": passes_hard_gates,
        "no_trade": no_trade,
        "aggregate_metrics": {
            "median_test_sharpe": round(med_sharpe, 4),
            "median_test_return_pct": round(med_return, 2),
            "median_oos_trades": round(med_trades, 1),
            "median_max_dd_pct": round(med_dd, 2),
            "median_calmar": round(med_calmar, 4),
            "n_passing_folds": len(oos),
            "n_no_trade_folds": sum(1 for m in test_metrics if m["trades"] == 0),
        },
        "gate_results": gates,
        "fold_best_params": [dict(p) for p, _ in fold_best],
    }


def _run_wrapper(strat, pair, wfo_workers=1, timeframe="1h", out_dir="data/backtests/fast_wfo"):
    s = time.time()
    r = run_fast_wfo(strat, pair, workers=wfo_workers, timeframe=timeframe)
    m = r["aggregate_metrics"]
    print(f"  {strat} {pair}: {time.time()-s:.1f}s | Sharpe={m['median_test_sharpe']:.2f} | Trades={m['median_oos_trades']:.0f} | {r['verdict']}", flush=True)
    return f"{strat}__{pair.replace('/', '_')}", r


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default="ma_adx")
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--all-strategies", action="store_true")
    parser.add_argument("--all-pairs", action="store_true")
    parser.add_argument("--timeframe", default="1h", help="OHLCV timeframe (1h, 4h, 1d)")
    parser.add_argument("--out", default="data/backtests/fast_wfo")
    parser.add_argument("--wfo-workers", type=int, default=1, help="Cell-level parallelism per (strategy, pair)")
    args = parser.parse_args()

    os.makedirs(args.out, exist_ok=True)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("POLARS_MAX_THREADS", "1")

    strategies = list(STRATEGY_SPECS.keys()) if args.all_strategies else [args.strategy]
    pairs = PAIRS if args.all_pairs else [args.symbol]
    jobs = [(s, p) for s in strategies for p in pairs]

    print(f"Fast WFO: {len(strategies)} strategies × {len(pairs)} pairs = {len(jobs)} runs", flush=True)
    start = time.time()
    results = {}

    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed

    n_workers = min(12, len(jobs))
    ctx = mp.get_context("fork")
    if len(jobs) == 1:
        # Single job: call directly (avoids nested fork pools)
        key, r = _run_wrapper(jobs[0][0], jobs[0][1], args.wfo_workers, timeframe=args.timeframe, out_dir=args.out)
        results[key] = r
    else:
        with ProcessPoolExecutor(max_workers=n_workers, mp_context=ctx) as pool:
            futures = {pool.submit(_run_wrapper, s, p, 1, args.timeframe, args.out): (s, p) for s, p in jobs}
            for i, fut in enumerate(as_completed(futures)):
                key, r = fut.result()
                results[key] = r
                pct = (i + 1) / len(jobs) * 100
                print(f"  [{pct:.0f}%] {key}", flush=True)

    elapsed = time.time() - start
    summary = {"timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
               "total_runs": len(results), "total_elapsed_seconds": round(elapsed, 1), "results": results}
    Path(args.out, "summary.json").write_text(json.dumps(summary, indent=2))

    # Table
    print(f"\n{'Strategy':20} {'Pair':10} {'Sharpe':>8} {'Trades':>8} {'MaxDD%':>8} {'Verdict':>8}")
    print("-" * 65)
    for key in sorted(results):
        r = results[key]
        m = r["aggregate_metrics"]
        print(f"{r['strategy']:20} {r['symbol']:10} {m['median_test_sharpe']:>8.2f} {m['median_oos_trades']:>8.0f} {m['median_max_dd_pct']:>8.1f} {r['verdict']:>8}")
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
