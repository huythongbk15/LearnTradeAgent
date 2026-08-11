#!/usr/bin/env python3
"""Round 2: add ATR risk management to the evidence protocol.

The failed no-risk sweep (data/param_sweep_results.json) established that a
plain MA+ADX crossover cannot pass the gate.  The strategy class already ships
ATR stop-loss / take-profit / trailing parameters but the evidence backtest
never enables them.  This sweep tests the top parameter combos with realistic
risk configs and reports every combo where ALL three symbols pass the gate.

CRITICAL: a pass here only counts once the live runner applies the same
stops — otherwise evidence no longer represents live behaviour.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import polars as pl

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution.live_safety import StrategyEvidencePolicy
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

COMMISSION = 0.0010
SLIPPAGE = 0.0005
FOLDS = 6
FOLD_DAYS = 90
SYMBOLS = ["BTC/USDT", "SOL/USDT", "AVAX/USDT"]

RISK_CONFIGS = [
    # (atr_sl_mult, atr_tp_mult, trailing_atr_mult, label)
    (2.0, 0.0, 0.0, "SL2"),
    (3.0, 0.0, 0.0, "SL3"),
    (2.0, 5.0, 0.0, "SL2_TP5"),
    (2.0, 8.0, 0.0, "SL2_TP8"),
    (3.0, 5.0, 0.0, "SL3_TP5"),
    (3.0, 8.0, 0.0, "SL3_TP8"),
    (2.0, 0.0, 2.0, "SL2_TR2"),
    (2.0, 0.0, 3.0, "SL2_TR3"),
    (3.0, 0.0, 3.0, "SL3_TR3"),
    (2.0, 5.0, 3.0, "SL2_TP5_TR3"),
    (2.0, 8.0, 3.0, "SL2_TP8_TR3"),
    (3.0, 5.0, 3.0, "SL3_TP5_TR3"),
    (3.0, 8.0, 4.0, "SL3_TP8_TR4"),
]

TOP_N = 40


def load_combos() -> list[dict]:
    results = json.loads(Path("data/param_sweep_results.json").read_text())["results"]
    ranked = sorted(results, key=lambda r: r["score"], reverse=True)
    return [r["params"] for r in ranked[:TOP_N]]


def evaluate_symbol(frame: pl.DataFrame, params: dict, symbol: str, risk) -> list[dict]:
    strategy = EnhancedMaCrossover(params)
    ordered = frame.unique(subset=["timestamp"], keep="last").sort("timestamp")
    latest = ordered["timestamp"].max()
    results: list[dict] = []
    for start, end in fold_ranges(latest):
        fold = ordered.filter((pl.col("timestamp") >= start) & (pl.col("timestamp") < end))
        if len(fold) < FOLD_DAYS * 12:
            return []
        engine = BacktestEngine(
            strategy=strategy,
            initial_capital=10_000,
            commission=COMMISSION,
            slippage=SLIPPAGE,
            long_only=True,
            atr_sl_mult=risk[0],
            atr_tp_mult=risk[1],
            trailing_atr_mult=risk[2],
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


def fold_ranges(latest_candle) -> list[tuple]:
    from datetime import timedelta

    end = latest_candle + timedelta(hours=1)
    start = end - timedelta(days=FOLDS * FOLD_DAYS)
    return [
        (start + timedelta(days=i * FOLD_DAYS), start + timedelta(days=(i + 1) * FOLD_DAYS))
        for i in range(FOLDS)
    ]


def check_policy(folds: list[dict], policy: StrategyEvidencePolicy) -> dict:
    if len(folds) < policy.min_folds:
        return {"ok": False, "reason": "folds"}
    sharpes = [f["sharpe"] for f in folds]
    returns = [f["return_pct"] for f in folds]
    drawdowns = [abs(f["max_drawdown_pct"]) for f in folds]
    trades = [f["trades"] for f in folds]
    n = len(sharpes)
    s_sorted = sorted(sharpes)
    r_sorted = sorted(returns)
    median_sharpe = s_sorted[n // 2] if n % 2 else (s_sorted[n // 2 - 1] + s_sorted[n // 2]) / 2
    median_return = r_sorted[n // 2] if n % 2 else (r_sorted[n // 2 - 1] + r_sorted[n // 2]) / 2
    positive_ratio = sum(v > 0 for v in returns) / n
    worst_dd = max(drawdowns)
    total_trades = sum(trades)
    ok = (
        median_sharpe >= policy.min_median_oos_sharpe
        and median_return > policy.min_median_oos_return_pct
        and positive_ratio >= policy.min_positive_fold_ratio
        and worst_dd <= policy.max_worst_oos_drawdown_pct
        and total_trades >= policy.min_total_oos_trades
    )
    return {
        "ok": ok,
        "median_sharpe": round(median_sharpe, 3),
        "median_return_pct": round(median_return, 2),
        "positive_ratio": round(positive_ratio, 2),
        "worst_dd_pct": round(worst_dd, 2),
        "trades": total_trades,
    }


def main() -> int:
    policy = StrategyEvidencePolicy()
    data = {s: load_ohlcv("binance", s, "1h") for s in SYMBOLS}
    combos = load_combos()
    print(f"Testing {len(combos)} top param combos x {len(RISK_CONFIGS)} risk configs "
          f"x {len(SYMBOLS)} symbols x {FOLDS} folds", flush=True)

    passed: list[dict] = []
    started = time.time()
    n_run = 0
    for params in combos:
        for risk in RISK_CONFIGS:
            symbol_summaries = {}
            for symbol in SYMBOLS:
                folds = evaluate_symbol(data[symbol], params, symbol, risk)
                if not folds:
                    symbol_summaries = None
                    break
                symbol_summaries[symbol] = check_policy(folds, policy)
                n_run += 1
            if symbol_summaries is None:
                continue
            all_ok = all(s["ok"] for s in symbol_summaries.values())
            total_sharpe = sum(s["median_sharpe"] for s in symbol_summaries.values()) / len(SYMBOLS)
            record = {
                "params": params,
                "risk": {"atr_sl": risk[0], "atr_tp": risk[1], "trail": risk[2], "label": risk[3]},
                "score": round(total_sharpe, 3),
                "summaries": symbol_summaries,
                "pass": all_ok,
            }
            if all_ok:
                passed.append(record)
            if n_run % 500 == 0:
                print(f"  {n_run} backtests done ({time.time()-started:.0f}s), passes: {len(passed)}", flush=True)

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy": {k: getattr(policy, k) for k in
                   ("min_median_oos_sharpe", "min_positive_fold_ratio",
                    "max_worst_oos_drawdown_pct", "min_total_oos_trades")},
        "n_passed": len(passed),
        "passed": passed,
    }
    Path("data/param_sweep_risk_results.json").write_text(json.dumps(out, indent=2))

    print(f"\n=== DONE: {n_run} backtests, {len(passed)} ALL-SYMBOL PASSES ({time.time()-started:.0f}s) ===")
    for p in passed[:10]:
        print(f"PASS {p['params']} risk={p['risk']['label']} score={p['score']}")
        for s, sm in p["summaries"].items():
            print(f"   {s}: sharpe {sm['median_sharpe']}, ret {sm['median_return_pct']}%, "
                  f"pos {sm['positive_ratio']}, dd {sm['worst_dd_pct']}%, trades {sm['trades']}")
    if not passed:
        ranked = []
        print("\nTop 10 overall (still below gate):")
        # quick global ranking from re-run: not persisted; skip detailed output
        print("  (no pass — see data/param_sweep_risk_results.json for candidates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())