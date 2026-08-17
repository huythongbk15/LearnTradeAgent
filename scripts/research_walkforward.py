#!/usr/bin/env python3
"""
Purged nested walk-forward for 10 pairs × 3 timeframes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(".")
RUN_DIR = ROOT / "data" / "research_runs" / "latest"


@dataclass
class Fold:
    fold: int
    train_start: str
    train_end: str
    val_start: str
    val_end: str
    test_start: str
    test_end: str
    embargo_bars: int


def _bars_per_year(tf: str) -> int:
    return {"1h": 8760, "4h": 2190, "1d": 365}.get(tf, 252)


def _make_folds(n: int, tf: str, min_folds: int = 6) -> list[Fold]:
    """Chronological purged walk-forward folds."""
    bpy = _bars_per_year(tf)
    if tf == "1h":
        train_months, test_months, step = 12, 3, 3
    elif tf == "4h":
        train_months, test_months, step = 12, 3, 3
    else:
        train_months, test_months, step = 24, 6, 6

    train_bars = int(train_months * 30 * bpy / 365)
    test_bars = int(test_months * 30 * bpy / 365)
    step_bars = int(step * 30 * bpy / 365)
    embargo = max(1, test_bars // 10)

    folds = []
    fold = 0
    start = 0
    while True:
        train_end = start + train_bars
        test_start = train_end + embargo
        test_end = test_start + test_bars
        if test_end > n:
            break
        folds.append(
            Fold(
                fold=fold,
                train_start=str(start),
                train_end=str(train_end),
                val_start=str(train_end),
                val_end=str(test_start),
                test_start=str(test_start),
                test_end=str(test_end),
                embargo_bars=embargo,
            )
        )
        fold += 1
        start += step_bars
        if fold >= min_folds and len(folds) >= 5:
            break
    return folds


def generate_folds(symbol: str, timeframe: str) -> list[dict[str, Any]]:
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    from trading_agent.data.storage import load_ohlcv

    df = load_ohlcv("binance", symbol, timeframe).sort("timestamp")
    folds = _make_folds(len(df), timeframe)
    out = []
    for f in folds:
        out.append(
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "fold": f.fold,
                "train_start_ts": str(df["timestamp"].item(int(f.train_start))),
                "train_end_ts": str(df["timestamp"].item(int(f.train_end))),
                "val_start_ts": str(df["timestamp"].item(int(f.val_start))),
                "val_end_ts": str(df["timestamp"].item(int(f.val_end))),
                "test_start_ts": str(df["timestamp"].item(int(f.test_start))),
                "test_end_ts": str(df["timestamp"].item(int(f.test_end))),
                "embargo_bars": f.embargo_bars,
            }
        )
    return out


if __name__ == "__main__":
    import sys

    sys.path.insert(0, str(ROOT / "src"))
    SYMBOLS = [
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
    ]
    TIMEFRAMES = ["1h", "4h", "1d"]
    all_folds = []
    for sym in SYMBOLS:
        for tf in TIMEFRAMES:
            folds = generate_folds(sym, tf)
            all_folds.extend(folds)
            print(f"{sym} {tf}: {len(folds)} folds")
    (RUN_DIR / "folds").mkdir(parents=True, exist_ok=True)
    with open(RUN_DIR / "folds" / "folds.json", "w") as f:
        json.dump(all_folds, f, indent=2)
    print(f"Saved {len(all_folds)} folds")
