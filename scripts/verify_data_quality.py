#!/usr/bin/env python3
"""Run the canonical OHLCV quality gate over an explicit dataset universe."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import timedelta
from pathlib import Path

from trading_agent.backtest.reporting import (
    DataQualityError,
    assess_ohlcv,
    load_gap_exceptions,
)
from trading_agent.config.loader import config
from trading_agent.data.storage import load_ohlcv


DEFAULT_SYMBOLS = (
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ZEC/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "ADA/USDT",
    "NEAR/USDT",
)
TIMEFRAME_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--timeframe", choices=tuple(TIMEFRAME_SECONDS), default="1h")
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="Comma-separated BASE/QUOTE symbols",
    )
    parser.add_argument("--gap-policy", choices=("record", "reject"), default="reject")
    parser.add_argument("--gap-exceptions", type=Path, default=None)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args()

    symbols = tuple(item.strip() for item in args.symbols.split(",") if item.strip())
    if not symbols:
        parser.error("--symbols must contain at least one symbol")

    datasets: list[dict[str, object]] = []
    for symbol in symbols:
        safe_symbol = symbol.replace("/", "_").replace(":", "_")
        source_path = (
            config.storage_abs_path
            / args.exchange
            / safe_symbol
            / f"{args.timeframe}.parquet"
        )
        try:
            exceptions = (
                load_gap_exceptions(
                    args.gap_exceptions,
                    exchange=args.exchange,
                    symbol=symbol,
                    timeframe=args.timeframe,
                )
                if args.gap_exceptions is not None
                else ()
            )
            report = assess_ohlcv(
                load_ohlcv(args.exchange, symbol, args.timeframe),
                expected_interval=timedelta(seconds=TIMEFRAME_SECONDS[args.timeframe]),
                gap_policy=args.gap_policy,
                gap_exceptions=exceptions,
            )
            result = report.to_dict()
        except DataQualityError as exc:
            result = exc.report.to_dict()
        except (FileNotFoundError, OSError, ValueError) as exc:
            result = {
                "accepted": False,
                "status": "failed_gate_execution",
                "error": f"{type(exc).__name__}: {exc}",
            }
        datasets.append(
            {
                "exchange": args.exchange,
                "symbol": symbol,
                "timeframe": args.timeframe,
                "source_path": str(source_path),
                "source_sha256": _sha256_file(source_path)
                if source_path.exists()
                else None,
                "quality": result,
            }
        )

    accepted = 0
    for item in datasets:
        quality = item.get("quality")
        if isinstance(quality, dict) and bool(quality.get("accepted")):
            accepted += 1
    payload = {
        "schema_version": 1,
        "status": "passed" if accepted == len(datasets) else "failed",
        "gap_policy": args.gap_policy,
        "gap_exception_manifest": str(args.gap_exceptions)
        if args.gap_exceptions is not None
        else None,
        "datasets_total": len(datasets),
        "datasets_accepted": accepted,
        "datasets": datasets,
    }
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
