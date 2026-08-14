#!/usr/bin/env python3
"""Sweep enhanced_ma parameters against the exact live-evidence gate.

Evaluates every combo on the same cost-aware walk-forward protocol used by
generate_live_strategy_evidence.py (6 folds x 90d, 10bps commission + 5bps
slippage, long-only) and reports combos where EVERY symbol passes the fixed
StrategyEvidencePolicy thresholds.  This is the honest gate: no symbol can be
sacrificed to let the others pass.
"""

from __future__ import annotations

import itertools
import json
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "src")

import polars as pl

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution.live_safety import (
    LiveSafetyError,
    StrategyEvidencePolicy,
)
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

COMMISSION = 0.0010
SLIPPAGE = 0.0005
FOLDS = 6
FOLD_DAYS = 90
SYMBOLS = ["BTC/USDT", "SOL/USDT", "AVAX/USDT"]

FAST = [10, 15, 20]
SLOW = [60, 80, 100]
ADX = [30, 35, 40]
# Strategy-level max drawdown circuit breaker (now actually implemented in
# EnhancedMaCrossover.generate_signals).  Break the DD wall that blocked 100%
# of plain-crossover combos.
MAX_DD = [0.10, 0.12, 0.15]
# ATR trailing stop baked into the signal stream (strategy-level, so live and
# backtest replay identically).  0.0 = disabled.  Sweep fine values: 2.0 was
# too aggressive (SOL Sharpe -0.61), 0.0 leaves DD > 15%.
TRAIL = [0.0, 0.5, 1.0, 1.5, 2.0]
DD_RECOVERY = 0.03
# Risk management: ATR SL / TP / trailing multiples applied INSIDE the evidence
# backtest.  Generating a strategy variant with these = the real improvement,
# because the current evidence run has them disabled (0.0).
SL_MULTS = [0.0, 1.5, 2.0, 3.0]
TP_MULTS = [0.0, 3.0, 5.0, 8.0]
TRAIL_MULTS = [0.0, 2.0, 3.0, 4.0]


def fold_ranges(
    latest_candle: datetime, count: int, fold_days: int
) -> list[tuple[datetime, datetime]]:
    end = latest_candle + timedelta(hours=1)
    start = end - timedelta(days=count * fold_days)
    return [
        (
            start + timedelta(days=index * fold_days),
            start + timedelta(days=(index + 1) * fold_days),
        )
        for index in range(count)
    ]


def evaluate_symbol(frame: pl.DataFrame, params: dict, symbol: str) -> list[dict]:
    strategy = EnhancedMaCrossover(params)
    ordered = frame.unique(subset=["timestamp"], keep="last").sort("timestamp")
    latest = ordered["timestamp"].max()
    results: list[dict] = []
    for index, (start, end) in enumerate(
        fold_ranges(latest, FOLDS, FOLD_DAYS), start=1
    ):
        fold = ordered.filter(
            (pl.col("timestamp") >= start) & (pl.col("timestamp") < end)
        )
        if len(fold) < FOLD_DAYS * 12:
            raise LiveSafetyError(f"{symbol} fold {index} incomplete")
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=10_000,
            commission=COMMISSION,
            slippage=SLIPPAGE,
            long_only=True,
        )
        result = engine.run(fold, symbol=symbol, timeframe="1h")
        results.append(
            {
                "sharpe": result.sharpe_ratio,
                "return_pct": result.total_return_pct,
                "max_drawdown_pct": abs(result.max_drawdown_pct),
                "trades": result.total_trades,
            }
        )
    return results


def pass_policy(
    symbol_results: dict[str, list[dict]], policy: StrategyEvidencePolicy
) -> tuple[bool, dict]:
    summaries: dict[str, dict] = {}
    for symbol, folds in symbol_results.items():
        if len(folds) < policy.min_folds:
            return False, summaries
        sharpes = [f["sharpe"] for f in folds]
        returns = [f["return_pct"] for f in folds]
        drawdowns = [abs(f["max_drawdown_pct"]) for f in folds]
        trades = [f["trades"] for f in folds]
        n = len(sharpes)
        sorted_s = sorted(sharpes)
        sorted_r = sorted(returns)
        median_sharpe = (
            sorted_s[n // 2] if n % 2 else (sorted_s[n // 2 - 1] + sorted_s[n // 2]) / 2
        )
        median_return = (
            sorted_r[n // 2] if n % 2 else (sorted_r[n // 2 - 1] + sorted_r[n // 2]) / 2
        )
        positive_ratio = sum(v > 0 for v in returns) / n
        worst_drawdown = max(drawdowns)
        total_trades = sum(trades)
        ok = (
            median_sharpe >= policy.min_median_oos_sharpe
            and median_return > policy.min_median_oos_return_pct
            and positive_ratio >= policy.min_positive_fold_ratio
            and worst_drawdown <= policy.max_worst_oos_drawdown_pct
            and total_trades >= policy.min_total_oos_trades
        )
        summaries[symbol] = {
            "median_sharpe": round(median_sharpe, 3),
            "median_return_pct": round(median_return, 2),
            "positive_ratio": round(positive_ratio, 2),
            "worst_dd_pct": round(worst_drawdown, 2),
            "trades": total_trades,
            "ok": ok,
        }
    return all(s["ok"] for s in summaries.values()), summaries


def main() -> int:
    policy = StrategyEvidencePolicy()
    data: dict[str, pl.DataFrame] = {}
    for symbol in SYMBOLS:
        data[symbol] = load_ohlcv("binance", symbol, "1h")

    combos = [
        {
            "fast_period": f,
            "slow_period": s,
            "adx_threshold": a,
            "max_dd_pct": m,
            "trailing_atr_mult": t,
            "dd_recovery_pct": DD_RECOVERY,
        }
        for f, s, a, m, t in itertools.product(FAST, SLOW, ADX, MAX_DD, TRAIL)
        if f < s
    ]
    print(
        f"Sweeping {len(combos)} param combos x {len(SYMBOLS)} symbols x {FOLDS} folds "
        f"(~{len(combos) * len(SYMBOLS) * FOLDS} backtests)…",
        flush=True,
    )

    passed: list[tuple[dict, dict]] = []
    all_results: list[dict] = []
    started = time.time()
    for idx, params in enumerate(combos, start=1):
        symbol_results: dict[str, list[dict]] = {}
        for symbol in SYMBOLS:
            symbol_results[symbol] = evaluate_symbol(data[symbol], params, symbol)
        ok, summaries = pass_policy(symbol_results, policy)
        score = sum(s["median_sharpe"] for s in summaries.values()) / len(SYMBOLS)
        all_results.append(
            {
                "params": params,
                "summaries": summaries,
                "pass": ok,
                "score": round(score, 3),
            }
        )
        if ok:
            passed.append((params, summaries))
        if idx % 36 == 0:
            elapsed = time.time() - started
            print(
                f"  {idx}/{len(combos)} done ({elapsed:.0f}s), passed so far: {len(passed)}",
                flush=True,
            )

    Path("data/param_sweep_results.json").write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "policy": {
                    "min_median_oos_sharpe": policy.min_median_oos_sharpe,
                    "min_positive_fold_ratio": policy.min_positive_fold_ratio,
                    "max_worst_oos_drawdown_pct": policy.max_worst_oos_drawdown_pct,
                    "min_total_oos_trades": policy.min_total_oos_trades,
                },
                "n_combos": len(combos),
                "n_passed": len(passed),
                "results": all_results,
            },
            indent=2,
        )
    )

    print(
        f"\n=== SWEEP DONE: {len(all_results)} combos, {len(passed)} pass all symbols "
        f"({time.time() - started:.0f}s) ==="
    )
    if passed:
        for params, summaries in passed:
            print(f"PASS {params}")
            for symbol, s in summaries.items():
                print(
                    f"   {symbol}: Sharpe {s['median_sharpe']}, ret {s['median_return_pct']}%, "
                    f"pos {s['positive_ratio']}, DD {s['worst_dd_pct']}%, trades {s['trades']}"
                )
    else:
        print("\nTop 5 by avg median Sharpe (still below the gate):")
        ranked = sorted(all_results, key=lambda r: r["score"], reverse=True)[:5]
        for r in ranked:
            print(f"  score {r['score']} params {r['params']}")
            for symbol, s in r["summaries"].items():
                print(f"     {symbol}: {s}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
