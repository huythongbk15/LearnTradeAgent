#!/usr/bin/env python3
"""Generate BTC cross-rate pairs (ETH/BTC, BNB/BTC, XRP/BTC) from USDT pairs.

Derives synthetic OHLCV for quote-currency-1 pairs by dividing USDT-denominated
prices by BTC/USDT.  Volume is taken from the quote asset side; close-to-close
ratio gives the cross-rate.

Usage:
    python scripts/generate_cross_rate_data.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from trading_agent.data.storage import load_ohlcv, save_ohlcv


def _cross(df_quote: pl.DataFrame, df_btc: pl.DataFrame) -> pl.DataFrame:
    """Compute cross-rate OHLCV (quote per BTC) by aligning on timestamp."""
    q = df_quote.select(["timestamp", "open", "high", "low", "close", "volume"])
    b = df_btc.select(["timestamp", "open", "high", "low", "close", "volume"])

    joined = q.join(b, on="timestamp", how="inner", suffix="_btc")

    cross = joined.select(
        pl.col("timestamp"),
        (pl.col("open") / pl.col("open_btc")).alias("open"),
        (pl.col("high") / pl.col("low_btc")).alias("high"),    # max quote / min btc
        (pl.col("low") / pl.col("high_btc")).alias("low"),      # min quote / max btc
        (pl.col("close") / pl.col("close_btc")).alias("close"),
        (pl.col("volume") / pl.col("close_btc")).alias("volume"),  # vol in BTC terms
    ).sort("timestamp")
    return cross


def main() -> None:
    exchange = "binance"
    timeframe = "1h"
    quote_pairs = {
        "ETH/BTC": "ETH_USDT",
        "BNB/BTC": "BNB_USDT",
        "XRP/BTC": "XRP_USDT",
    }

    btc = load_ohlcv(exchange, "BTC_USDT", timeframe).sort("timestamp")
    print(f"BTC/USDT: {btc.height} bars")

    for cross_symbol, usdt_symbol in quote_pairs.items():
        cross_raw = cross_symbol.replace("/", "_")
        print(f"Generating {cross_symbol} from {usdt_symbol} / BTC_USDT ...")
        try:
            df_quote = load_ohlcv(exchange, usdt_symbol, timeframe)
            cross = _cross(df_quote, btc)
            print(f"  {cross_symbol}: {cross.height} rows, range {cross['close'].min():.6f} – {cross['close'].max():.6f}")
            save_ohlcv(cross, exchange, cross_raw, timeframe)
            print(f"  saved → data/raw/binance/{cross_raw}/{timeframe}.parquet")
        except Exception as exc:
            print(f"  FAIL {cross_symbol}: {exc}")


if __name__ == "__main__":
    main()
