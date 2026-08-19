#!/usr/bin/env python3
"""Multi-pair 1h full system backtest — last 1 year only for fast comparison."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "DOGE/USDT",
    "AVAX/USDT",
]

EXCHANGE = os.getenv("EXCHANGE", "binance")
TIMEFRAME = "1h"
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100000"))
OUT_DIR = ROOT / "data" / "benchmarks" / "multi_pair_1y_1h"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def run_backtest(symbol: str) -> dict:
    env = os.environ.copy()
    env.update(
        {
            "SYMBOL": symbol,
            "TIMEFRAME": TIMEFRAME,
            "EXCHANGE": EXCHANGE,
            "INITIAL_CAPITAL": str(INITIAL_CAPITAL),
            "USE_LLM": "false",
        }
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "full_system_backtest.py"),
        "--fresh",
    ]
    print(f"\n{'=' * 60}")
    print(f"🚀 Running {symbol} {TIMEFRAME}")
    print(f"{'=' * 60}")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, cwd=str(ROOT))
    stdout = proc.stdout
    print(stdout)
    if proc.returncode != 0:
        print(f"❌ {symbol} failed: {proc.stderr[-500:] if proc.stderr else 'unknown'}")
        return {"symbol": symbol, "error": f"returncode={proc.returncode}"}

    metrics = {"symbol": symbol, "timeframe": TIMEFRAME}
    for line in stdout.splitlines():
        if "Final Equity:" in line:
            try:
                metrics["final_equity"] = float(
                    line.split("Final Equity:")[1]
                    .strip()
                    .replace("$", "")
                    .replace(",", "")
                )
            except Exception:
                pass
        if "Total Return:" in line:
            try:
                metrics["total_return_pct"] = float(
                    line.split("Total Return:")[1].strip().replace("%", "")
                )
            except Exception:
                pass
        if "Max Drawdown:" in line:
            try:
                metrics["max_drawdown_pct"] = float(
                    line.split("Max Drawdown:")[1].strip().replace("%", "")
                )
            except Exception:
                pass
        if "Sharpe Ratio:" in line:
            try:
                metrics["sharpe"] = float(line.split("Sharpe Ratio:")[1].strip())
            except Exception:
                pass
        if "Win Rate:" in line:
            try:
                metrics["win_rate_pct"] = float(
                    line.split("Win Rate:")[1].strip().replace("%", "")
                )
            except Exception:
                pass
        if "Total Trades:" in line:
            try:
                metrics["total_trades"] = int(line.split("Total Trades:")[1].strip())
            except Exception:
                pass
    return metrics


def main() -> None:
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    results = []
    for symbol in PAIRS:
        res = run_backtest(symbol)
        results.append(res)

    out_file = OUT_DIR / f"multi_pair_1y_1h_{timestamp}.json"
    with open(out_file, "w") as f:
        json.dump(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "exchange": EXCHANGE,
                "timeframe": TIMEFRAME,
                "period": "last_1y",
                "initial_capital": INITIAL_CAPITAL,
                "pairs": PAIRS,
                "results": results,
            },
            f,
            indent=2,
        )

    print(f"\n{'=' * 80}")
    print(f"📊 Multi-Pair 1h (1y) Backtest Summary — {timestamp}")
    print(f"{'=' * 80}")
    print(
        f"{'Symbol':<12} {'Final Eq':>12} {'Return%':>10} {'MaxDD%':>10} {'Sharpe':>8} {'Trades':>8} {'Win%':>8}"
    )
    print("-" * 80)
    for r in results:
        if "error" in r:
            print(f"{r.get('symbol', '?'):<12} ERROR: {r['error']}")
            continue
        print(
            f"{r.get('symbol', '?'):<12} "
            f"{r.get('final_equity', 0):>12,.0f} "
            f"{r.get('total_return_pct', 0):>10.2f} "
            f"{r.get('max_drawdown_pct', 0):>10.2f} "
            f"{r.get('sharpe', 0):>8.2f} "
            f"{r.get('total_trades', 0):>8d} "
            f"{r.get('win_rate_pct', 0):>8.2f}"
        )

    # Compare with previous run in same dir
    baseline_files = sorted(OUT_DIR.glob("multi_pair_1y_1h_*.json"))
    if len(baseline_files) >= 2:
        prev_file = baseline_files[-2]
        with open(prev_file) as f:
            prev = json.load(f)
        print(f"\n📈 Comparison vs {prev_file.name}:")
        print("-" * 80)
        prev_map = {r["symbol"]: r for r in prev.get("results", [])}
        for r in results:
            sym = r.get("symbol")
            if sym not in prev_map:
                continue
            p = prev_map[sym]
            if "error" in r or "error" in p:
                continue
            ret_delta = r.get("total_return_pct", 0) - p.get("total_return_pct", 0)
            dd_delta = r.get("max_drawdown_pct", 0) - p.get("max_drawdown_pct", 0)
            sharpe_delta = r.get("sharpe", 0) - p.get("sharpe", 0)
            print(
                f"{sym:<12} "
                f"ΔRet={ret_delta:>+8.2f}% "
                f"ΔDD={dd_delta:>+8.2f}% "
                f"ΔSharpe={sharpe_delta:>+8.2f}"
            )

    print(f"\n✅ Saved to {out_file}")


if __name__ == "__main__":
    main()
