#!/usr/bin/env python3
"""Fail-closed multi-pair 1h full-system backtest runner."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from trading_agent.execution.canonical.instrument_registry import TEN_PAIR_1H_SYMBOLS

PAIRS = list(TEN_PAIR_1H_SYMBOLS)

EXCHANGE = os.getenv("EXCHANGE", "binance")
TIMEFRAME = "1h"
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "100000"))
MAX_WORKERS = max(1, int(os.getenv("MAX_WORKERS", "4")))
TAIL_BARS = int(os.getenv("BARS", "0"))
STATE_FLUSH_BARS = max(1, int(os.getenv("BACKTEST_STATE_FLUSH_BARS", "100")))
OUT_DIR = ROOT / "data" / "benchmarks" / "multi_pair_1h"
RUNS_DIR = ROOT / "data" / "backtests" / "multi_pair_1h"


REQUIRED_METRICS = {
    "symbol",
    "timeframe",
    "final_equity",
    "total_return_pct",
    "sharpe",
    "max_drawdown_pct",
    "total_trades",
    "win_rate_pct",
    "data_manifest_id",
    "feature_artifact_id",
    "execution_health",
}
FINITE_METRICS = {
    "final_equity",
    "total_return_pct",
    "sharpe",
    "max_drawdown_pct",
    "win_rate_pct",
}


def _safe_symbol(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_")


def _data_path(symbol: str) -> Path:
    return (
        ROOT / "data" / "raw" / EXCHANGE / _safe_symbol(symbol) / f"{TIMEFRAME}.parquet"
    )


def _preflight() -> None:
    if len(PAIRS) != 10 or len(set(PAIRS)) != 10:
        raise RuntimeError(
            "The canonical 1h universe must contain exactly 10 unique pairs"
        )
    missing = [
        str(_data_path(symbol)) for symbol in PAIRS if not _data_path(symbol).is_file()
    ]
    if missing:
        raise FileNotFoundError("Missing 1h input data: " + ", ".join(missing))
    if TAIL_BARS < 0:
        raise ValueError("BARS must be zero (full history) or a positive integer")


def _validate_report(symbol: str, report: object) -> dict[str, object]:
    if not isinstance(report, dict):
        raise ValueError("child report is not a JSON object")
    missing = sorted(REQUIRED_METRICS.difference(report))
    if missing:
        raise ValueError(f"child report missing required fields: {', '.join(missing)}")
    if report["symbol"] != symbol or report["timeframe"] != TIMEFRAME:
        raise ValueError(
            "child report identity does not match requested symbol/timeframe"
        )
    for key in FINITE_METRICS:
        value = report[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"child report field {key} is not numeric")
        if not math.isfinite(float(value)):
            raise ValueError(f"child report field {key} is not finite")
    total_trades = report["total_trades"]
    if (
        isinstance(total_trades, bool)
        or not isinstance(total_trades, int)
        or total_trades < 0
    ):
        raise ValueError(
            "child report field total_trades must be a non-negative integer"
        )
    health = report["execution_health"]
    if not isinstance(health, dict):
        raise ValueError("child execution_health is not an object")
    unsafe_health = (
        health.get("status") != "normal"
        or bool(health.get("unknown_orders", 0))
        or bool(health.get("manual_interventions", 0))
        or bool(health.get("unprotected_positions", []))
    )
    if unsafe_health:
        raise ValueError(f"unsafe terminal execution state: {health}")
    return report


def run_backtest(symbol: str, run_id: str) -> dict[str, object]:
    """Run one isolated child and load its machine-readable report."""
    safe_symbol = _safe_symbol(symbol)
    pair_dir = RUNS_DIR / run_id / safe_symbol
    state_dir = pair_dir / "execution"
    report_path = pair_dir / "report.json"
    env = os.environ.copy()
    env.update(
        {
            "SYMBOL": symbol,
            "TIMEFRAME": TIMEFRAME,
            "EXCHANGE": EXCHANGE,
            "INITIAL_CAPITAL": str(INITIAL_CAPITAL),
            "USE_LLM": "false",
            "BACKTEST_RUN_ID": run_id,
        }
    )
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "full_system_backtest.py"),
        "--fresh",
        "--symbol",
        symbol,
        "--timeframe",
        TIMEFRAME,
        "--run-id",
        run_id,
        "--state-dir",
        str(state_dir),
        "--report-path",
        str(report_path),
        "--state-flush-bars",
        str(STATE_FLUSH_BARS),
        "--allow-new-exposure",
    ]
    if TAIL_BARS:
        cmd.extend(["--tail-bars", str(TAIL_BARS)])
    print(f"\n{'=' * 60}")
    print(f"🚀 Running {symbol} {TIMEFRAME}")
    print(f"{'=' * 60}")
    proc = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=7200,
        check=False,
    )
    stdout = proc.stdout
    stderr = proc.stderr
    print(stdout)
    if proc.returncode != 0:
        print(f"❌ {symbol} failed: {stderr[-500:] if stderr else 'unknown'}")
        return {"symbol": symbol, "error": f"returncode={proc.returncode}"}

    if not report_path.is_file():
        return {"symbol": symbol, "error": f"missing child report: {report_path}"}
    try:
        with report_path.open(encoding="utf-8") as source:
            report = _validate_report(symbol, json.load(source))
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return {"symbol": symbol, "error": str(exc)}
    report["report_path"] = str(report_path)
    report["state_dir"] = str(state_dir)
    return report


def main() -> None:
    _preflight()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S_%fZ')}_{uuid.uuid4().hex[:8]}"
    timestamp = run_id
    results: list[dict[str, object]] = []

    # Run backtests in parallel
    with ProcessPoolExecutor(max_workers=min(MAX_WORKERS, len(PAIRS))) as executor:
        future_to_symbol = {
            executor.submit(run_backtest, symbol, run_id): symbol for symbol in PAIRS
        }
        for future in as_completed(future_to_symbol):
            symbol = future_to_symbol[future]
            try:
                res = future.result()
            except Exception as e:
                print(f"❌ {symbol} failed with exception: {e}")
                res = {"symbol": symbol, "error": str(e)}
            results.append(res)

    # Sort results by symbol for consistent output
    results.sort(key=lambda r: r.get("symbol", ""))
    failures = [result for result in results if "error" in result]

    # Save raw results
    out_file = OUT_DIR / f"multi_pair_1h_{timestamp}.json"
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "schema_version": 1,
                "run_id": run_id,
                "status": "passed"
                if not failures and len(results) == len(PAIRS)
                else "failed",
                "successful_pairs": len(results) - len(failures),
                "failed_pairs": len(failures),
                "tail_bars": TAIL_BARS or None,
                "generated_at": datetime.now(UTC).isoformat(),
                "exchange": EXCHANGE,
                "timeframe": TIMEFRAME,
                "initial_capital": INITIAL_CAPITAL,
                "pairs": PAIRS,
                "results": results,
            },
            f,
            indent=2,
            allow_nan=False,
        )

    # Print summary table
    print(f"\n{'=' * 80}")
    print(f"📊 Multi-Pair 1h Backtest Summary — {timestamp}")
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

    print(f"\n✅ Saved to {out_file}")
    if failures or len(results) != len(PAIRS):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
