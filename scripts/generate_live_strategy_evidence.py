#!/usr/bin/env python3
"""Generate cost-aware rolling evidence for the exact Binance live strategy.

The script never changes live parameters.  It evaluates the frozen configuration
on independent chronological folds and publishes the mainnet evidence file only
when every fixed readiness threshold passes.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np
import polars as pl
from live_config import ATR_SL_MULT, STRATEGY_PARAMS, TIMEFRAME

from trading_agent.backtest.engine import BacktestEngine
from trading_agent.data.storage import load_ohlcv
from trading_agent.execution.live_safety import (
    LiveSafetyError,
    sign_strategy_evidence,
    validate_build_sha,
    validate_integrity_key,
    validate_strategy_evidence,
)
from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

COMMISSION = 0.0010
SLIPPAGE = 0.0005
SPREAD_BPS = 2.0
INITIAL_CAPITAL = 10_000.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", default="BTC/USDT,SOL/USDT,AVAX/USDT")
    parser.add_argument("--weights", default="4,3,3", help="Live equity percentages")
    parser.add_argument("--folds", type=int, default=6)
    parser.add_argument("--fold-days", type=int, default=90)
    parser.add_argument("--output", default="data/live_strategy_evidence.json")
    parser.add_argument(
        "--build-sha",
        default=os.getenv("TRADING_BUILD_SHA", ""),
        help="Commit SHA of the immutable live build",
    )
    parser.add_argument("--bootstrap-iters", type=int, default=1_000)
    parser.add_argument(
        "--trials",
        type=int,
        default=8_000,
        help="Number of parameter trials already explored; deflates the Sharpe",
    )
    parser.add_argument("--min-trades-per-fold", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--dsr-min",
        type=float,
        default=0.95,
        help="Minimum deflated Sharpe ratio to publish evidence",
    )
    parser.add_argument(
        "--no-stats-gate",
        action="store_true",
        help="Report statistical metrics without rejecting on them",
    )
    return parser


def fold_ranges(
    latest_candle: datetime, count: int, fold_days: int
) -> list[tuple[datetime, datetime]]:
    if count < 6:
        raise LiveSafetyError("at least 6 folds are required")
    if fold_days < 90:
        raise LiveSafetyError("folds shorter than 90 days are not accepted")
    end = latest_candle + timedelta(hours=1)
    start = end - timedelta(days=count * fold_days)
    return [
        (
            start + timedelta(days=index * fold_days),
            start + timedelta(days=(index + 1) * fold_days),
        )
        for index in range(count)
    ]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def validate_hourly_fold(
    fold: pl.DataFrame,
    *,
    symbol: str,
    index: int,
    start: datetime,
    end: datetime,
) -> None:
    expected_bars = int((end - start).total_seconds() / 3_600)
    timestamps = [_as_utc(value) for value in fold["timestamp"].to_list()]
    if len(timestamps) != expected_bars:
        raise LiveSafetyError(
            f"{symbol} fold {index} contains hourly gaps: "
            f"{len(timestamps)} != {expected_bars} bars"
        )
    if (
        not timestamps
        or timestamps[0] != start
        or timestamps[-1] != end - timedelta(hours=1)
    ):
        raise LiveSafetyError(
            f"{symbol} fold {index} does not cover its complete time range"
        )
    if any(
        current - previous != timedelta(hours=1)
        for previous, current in zip(timestamps, timestamps[1:], strict=False)
    ):
        raise LiveSafetyError(f"{symbol} fold {index} is not continuous hourly data")


def evaluate_symbol(
    frame: pl.DataFrame,
    symbol: str,
    ranges: list[tuple[datetime, datetime]],
    allocation: float,
    *,
    commission: float = COMMISSION,
    slippage: float = SLIPPAGE,
    spread_bps: float = SPREAD_BPS,
) -> tuple[list[dict], list[pl.DataFrame]]:
    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(frame.columns)
    if missing:
        raise LiveSafetyError(f"{symbol} data is missing columns: {sorted(missing)}")
    if frame["timestamp"].n_unique() != len(frame):
        raise LiveSafetyError(f"{symbol} data contains duplicate timestamps")
    ordered = frame.sort("timestamp")
    if ordered.is_empty():
        raise LiveSafetyError(f"{symbol} data is empty")
    numeric_columns = ["open", "high", "low", "close", "volume"]
    invalid = ordered.select(
        [
            (~pl.col(column).cast(pl.Float64).is_finite()).any().alias(column)
            for column in numeric_columns
        ]
    ).row(0)
    if any(invalid):
        raise LiveSafetyError(f"{symbol} data contains non-finite OHLCV values")
    if ordered.filter(pl.col("volume") < 0).height:
        raise LiveSafetyError(f"{symbol} data contains negative volume")
    results: list[dict] = []
    equity_curves: list[pl.DataFrame] = []
    for index, (start, end) in enumerate(ranges, start=1):
        # Dataset timestamps are stored naive UTC; fold boundaries come from
        # an aware datetime.  Normalize before filtering to avoid polars
        # Datetime('us') vs Datetime('us','UTC') comparison errors.
        start_filter = start.replace(tzinfo=None) if start.tzinfo else start
        end_filter = end.replace(tzinfo=None) if end.tzinfo else end
        fold = ordered.filter(
            (pl.col("timestamp") >= start_filter) & (pl.col("timestamp") < end_filter)
        )
        validate_hourly_fold(
            fold,
            symbol=symbol,
            index=index,
            start=start,
            end=end,
        )
        engine = BacktestEngine(
            strategy=EnhancedMaCrossover(STRATEGY_PARAMS),
            initial_capital=INITIAL_CAPITAL,
            commission=commission,
            slippage=slippage,
            spread_bps=spread_bps,
            long_only=True,
            atr_sl_mult=ATR_SL_MULT,
            trailing_atr_mult=ATR_SL_MULT,
            position_sizing_method="fixed",
            fixed_position_pct=allocation,
            max_position_pct=allocation,
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
        equity_curves.append(result.equity_curve.select("timestamp", "equity"))
    return results, equity_curves


def build_portfolio_folds(
    symbol_results: dict[str, dict],
    equity_curves: dict[str, list[pl.DataFrame]],
) -> tuple[list[dict], np.ndarray]:
    """Return (portfolio fold summaries, concatenated hourly returns).

    The concatenated hourly return series stitches every fold end-to-end so
    the statistical-hardening toolkit (bootstrap CI, PSR, DSR) has one long
    continuous sample instead of six isolated point estimates.
    """
    symbols = list(symbol_results)
    count = len(symbol_results[symbols[0]]["folds"])
    portfolio: list[dict] = []
    all_hourly_returns: list[np.ndarray] = []
    for index in range(count):
        first = equity_curves[symbols[0]][index]
        timestamps = first["timestamp"].to_list()
        combined_return = np.zeros(len(first), dtype=np.float64)
        trades = 0
        for symbol in symbols:
            curve = equity_curves[symbol][index]
            if curve["timestamp"].to_list() != timestamps:
                raise LiveSafetyError(
                    f"portfolio fold {index + 1} timestamps do not align"
                )
            equity = curve["equity"].to_numpy().astype(np.float64)
            combined_return += equity / INITIAL_CAPITAL - 1.0
            trades += int(symbol_results[symbol]["folds"][index]["trades"])
        portfolio_equity = INITIAL_CAPITAL * (1.0 + combined_return)
        if np.any(~np.isfinite(portfolio_equity)) or np.any(portfolio_equity <= 0):
            raise LiveSafetyError(f"portfolio fold {index + 1} equity is invalid")
        hourly_returns = portfolio_equity[1:] / portfolio_equity[:-1] - 1.0
        if len(hourly_returns):
            all_hourly_returns.append(hourly_returns.astype(np.float64))
        volatility = (
            float(np.std(hourly_returns, ddof=1)) if len(hourly_returns) > 1 else 0.0
        )
        sharpe = (
            float(np.mean(hourly_returns) / volatility * math.sqrt(365 * 24))
            if volatility > 0
            else 0.0
        )
        peaks = np.maximum.accumulate(portfolio_equity)
        drawdown = (peaks - portfolio_equity) / peaks
        source = symbol_results[symbols[0]]["folds"][index]
        portfolio.append(
            {
                "start": source["start"],
                "end": source["end"],
                "bars": source["bars"],
                "sharpe": sharpe,
                "return_pct": float(
                    (portfolio_equity[-1] / INITIAL_CAPITAL - 1.0) * 100
                ),
                "max_drawdown_pct": float(np.max(drawdown) * 100),
                "trades": trades,
            }
        )
    if not all_hourly_returns:
        raise LiveSafetyError("no hourly returns produced for portfolio")
    return portfolio, np.concatenate(all_hourly_returns)


def utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)


def run(args: argparse.Namespace) -> int:
    symbols = [
        value.strip().upper() for value in args.symbols.split(",") if value.strip()
    ]
    if not symbols or len(set(symbols)) != len(symbols):
        raise LiveSafetyError("--symbols must be a unique, non-empty list")
    weight_parts = [value.strip() for value in args.weights.split(",") if value.strip()]
    if len(weight_parts) != len(symbols):
        raise LiveSafetyError("--weights must contain one value per symbol")
    try:
        weights = [float(value) / 100.0 for value in weight_parts]
    except ValueError as exc:
        raise LiveSafetyError("--weights must contain numbers") from exc
    if any(not math.isfinite(value) or not 0 < value < 1 for value in weights):
        raise LiveSafetyError("--weights must be finite percentages between 0 and 100")
    if sum(weights) > 1:
        raise LiveSafetyError("--weights cannot exceed 100% in total")
    allocations = dict(zip(symbols, weights, strict=True))
    build_sha = validate_build_sha(args.build_sha)
    integrity_key = os.getenv("LIVE_SAFETY_HMAC_KEY", "")
    integrity_key = validate_integrity_key(integrity_key)

    symbol_results: dict[str, dict] = {}
    frames: dict[str, pl.DataFrame] = {}
    data_ends: list[datetime] = []
    for symbol in symbols:
        frame = load_ohlcv("binance", symbol, TIMEFRAME)
        if frame.is_empty():
            raise LiveSafetyError(f"{symbol} data is empty")
        frames[symbol] = frame
        data_ends.append(_as_utc(frame["timestamp"].max()))

    common_data_end = min(data_ends)
    ranges = fold_ranges(common_data_end, args.folds, args.fold_days)
    curves: dict[str, list[pl.DataFrame]] = {}
    for symbol in symbols:
        folds, curves[symbol] = evaluate_symbol(
            frames[symbol],
            symbol,
            ranges,
            allocations[symbol],
        )
        symbol_results[symbol] = {
            "allocation": allocations[symbol],
            "folds": folds,
        }
        print(f"{symbol}: evaluated {len(folds)} folds")
    portfolio_folds, portfolio_returns = build_portfolio_folds(symbol_results, curves)

    # P2 statistical hardening: per-fold trade minimums, bootstrap CI, PSR/DSR.
    from trading_agent.alpha_research.stats import (
        min_trades_check,
        summarize_sharpe,
    )

    violations = min_trades_check(
        portfolio_folds, args.min_trades_per_fold, label="portfolio"
    )
    if violations:
        raise LiveSafetyError("; ".join(violations))
    stats_summary = summarize_sharpe(
        portfolio_returns,
        periods_per_year=365 * 24,
        trials=args.trials,
        bootstrap_iters=args.bootstrap_iters,
        seed=args.seed,
    )
    if not args.no_stats_gate and (
        stats_summary["deflated_sharpe_ratio"] < args.dsr_min
        or stats_summary["sharpe_ci95_lo"] <= 0.0
    ):
        raise LiveSafetyError(
            "statistical gate failed: "
            f"DSR={stats_summary['deflated_sharpe_ratio']:.3f} "
            f"(min {args.dsr_min}), "
            f"Sharpe 95% CI=[{stats_summary['sharpe_ci95_lo']:.2f}, "
            f"{stats_summary['sharpe_ci95_hi']:.2f}]"
        )

    now = datetime.now(UTC)
    evidence = {
        "version": 1,
        "strategy": "enhanced_ma",
        "build_sha": build_sha,
        "strategy_params": STRATEGY_PARAMS,
        "generated_at": now.isoformat(),
        "data_end": utc_iso(common_data_end),
        "allocations": allocations,
        "costs": {
            "commission_bps": COMMISSION * 10_000,
            "slippage_bps": SLIPPAGE * 10_000,
            "spread_bps": SPREAD_BPS,
        },
        "symbols": symbol_results,
        "portfolio": {"folds": portfolio_folds},
        "statistical": stats_summary,
    }
    evidence = sign_strategy_evidence(evidence, integrity_key)

    output = Path(args.output)
    candidate = output.with_suffix(".candidate.json")
    write_json_atomic(candidate, evidence)
    try:
        summaries = validate_strategy_evidence(
            candidate,
            expected_symbols=symbols,
            expected_params=STRATEGY_PARAMS,
            expected_allocations=allocations,
            expected_build_sha=build_sha,
            integrity_key=integrity_key,
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
        label = "PORTFOLIO" if symbol == "__portfolio__" else symbol
        print(
            f"PASS {label}: median Sharpe {summary['median_sharpe']:.2f}, "
            f"median return {summary['median_return_pct']:.2f}%, "
            f"worst DD {summary['worst_drawdown_pct']:.2f}%"
        )
    print(
        f"STATS portfolio: Sharpe {stats_summary['annualized_sharpe']:.2f} "
        f"(95% CI [{stats_summary['sharpe_ci95_lo']:.2f}, "
        f"{stats_summary['sharpe_ci95_hi']:.2f}]), "
        f"PSR {stats_summary['probabilistic_sharpe_ratio']:.3f}, "
        f"DSR({stats_summary['trials']} trials) "
        f"{stats_summary['deflated_sharpe_ratio']:.3f}"
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
