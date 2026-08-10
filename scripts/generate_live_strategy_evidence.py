#!/usr/bin/env python3
"""Generate cost-aware rolling evidence for the exact Binance live strategy.

The script never changes live parameters.  It evaluates the frozen configuration
on independent chronological folds and publishes the mainnet evidence file only
when every fixed readiness threshold passes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "src")

import polars as pl

from live_config import STRATEGY_PARAMS, TIMEFRAME
from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution.live_safety import LiveSafetyError, validate_strategy_evidence
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover


COMMISSION = 0.0010
SLIPPAGE = 0.0005


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="BTC/USDT,SOL/USDT,AVAX/USDT")
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--fold-days", type=int, default=90)
    parser.add_argument("--output", default="data/live_strategy_evidence.json")
    return parser


def fold_ranges(latest_candle: datetime, count: int, fold_days: int) -> list[tuple[datetime, datetime]]:
    if count < 6:
        raise LiveSafetyError("at least 6 folds are required")
    if fold_days < 30:
        raise LiveSafetyError("folds shorter than 30 days are not accepted")
    end = latest_candle + timedelta(hours=1)
    start = end - timedelta(days=count * fold_days)
    return [
        (
            start + timedelta(days=index * fold_days),
            start + timedelta(days=(index + 1) * fold_days),
        )
        for index in range(count)
    ]


def evaluate_symbol(frame: pl.DataFrame, symbol: str, count: int, fold_days: int) -> list[dict]:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise LiveSafetyError(f"{symbol} data is missing columns: {sorted(missing)}")
    ordered = frame.unique(subset=["timestamp"], keep="last").sort("timestamp")
    if ordered.is_empty():
        raise LiveSafetyError(f"{symbol} data is empty")
    latest = ordered["timestamp"].max()
    results: list[dict] = []
    for index, (start, end) in enumerate(fold_ranges(latest, count, fold_days), start=1):
        fold = ordered.filter((pl.col("timestamp") >= start) & (pl.col("timestamp") < end))
        if len(fold) < fold_days * 12:
            raise LiveSafetyError(
                f"{symbol} fold {index} is incomplete: {len(fold)} bars"
            )
        engine = BacktestEngine(
            strategy=EnhancedMaCrossover(STRATEGY_PARAMS),
            initial_capital=10_000,
            commission=COMMISSION,
            slippage=SLIPPAGE,
            long_only=True,
        )
        result = engine.run(fold, symbol=symbol, timeframe=TIMEFRAME)
        results.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "bars": len(fold),
                "sharpe": result.sharpe_ratio,
                "return_pct": result.total_return_pct,
                "max_drawdown_pct": abs(result.max_drawdown_pct),
                "trades": result.total_trades,
            }
        )
    return results


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def run(args: argparse.Namespace) -> int:
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    if not symbols or len(set(symbols)) != len(symbols):
        raise LiveSafetyError("--symbols must be a unique, non-empty list")

    symbol_results: dict[str, dict] = {}
    data_ends: list[datetime] = []
    for symbol in symbols:
        frame = load_ohlcv("binance", symbol, TIMEFRAME)
        data_ends.append(frame["timestamp"].max())
        folds = evaluate_symbol(frame, symbol, args.folds, args.fold_days)
        symbol_results[symbol] = {"folds": folds}
        print(f"{symbol}: evaluated {len(folds)} folds")

    now = datetime.now(UTC)
    evidence = {
        "version": 1,
        "strategy": "enhanced_ma",
        "strategy_params": STRATEGY_PARAMS,
        "generated_at": now.isoformat(),
        "data_end": utc_iso(min(data_ends)),
        "costs": {
            "commission_bps": COMMISSION * 10_000,
            "slippage_bps": SLIPPAGE * 10_000,
        },
        "symbols": symbol_results,
    }

    output = Path(args.output)
    candidate = output.with_suffix(".candidate.json")
    write_json_atomic(candidate, evidence)
    try:
        summaries = validate_strategy_evidence(
            candidate,
            expected_symbols=symbols,
            expected_params=STRATEGY_PARAMS,
            now=now,
        )
    except LiveSafetyError as exc:
        rejected = output.with_suffix(".rejected.json")
        os.replace(candidate, rejected)
        print(f"REJECTED: {exc}", file=sys.stderr)
        print(f"Evidence retained for review: {rejected}", file=sys.stderr)
        return 2

    os.replace(candidate, output)
    for symbol, summary in summaries.items():
        print(
            f"PASS {symbol}: median Sharpe {summary['median_sharpe']:.2f}, "
            f"median return {summary['median_return_pct']:.2f}%, "
            f"worst DD {summary['worst_drawdown_pct']:.2f}%"
        )
    print(f"Mainnet evidence published: {output}")
    return 0


def main() -> int:
    try:
        return run(build_parser().parse_args())
    except Exception as exc:
        print(f"FATAL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
