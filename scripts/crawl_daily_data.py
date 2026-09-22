#!/usr/bin/env python3
"""Crawl daily OHLCV data for crypto (Binance) + equities (Yahoo Finance).

Usage:
    python scripts/crawl_daily_data.py --start 2020-01-01 --end 2026-09-21

Output:
    data/raw/binance/BTCUSDT/1d.parquet       (crypto, 1d)
    data/raw/binance/ETHUSDT/1d.parquet
    data/raw/binance/BNBUSDT/1d.parquet
    data/raw/yfinance/SPY.parquet             (equities, 1d)
    data/raw/yfinance/QQQ.parquet
    ...
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

EQUITY_SYMBOLS = [
    "SPY", "QQQ", "AAPL", "MSFT", "GOOGL", "AMZN",
    "NVDA", "TSLA", "META", "BRK-B", "JPM", "JNJ",
    "XOM", "WMT", "V", "MA", "UNH", "DIS",
]

CRYPTO_SYMBOLS = ["BTC_USDT", "ETH_USDT", "BNB_USDT"]


def crawl_equity_daily(
    symbol: str, start: str, end: str, cache_dir: Path
) -> int:
    """Download daily data from Yahoo Finance."""
    import yfinance as yf

    cache_file = cache_dir / f"{symbol}.parquet"
    if cache_file.exists():
        logger.info("T5A: %s already cached, skipping", symbol)
        return 0

    logger.info("T5A: Downloading %s daily from Yahoo Finance", symbol)
    df = yf.download(
        symbol,
        start=start,
        end=end,
        interval="1d",
        progress=False,
    )

    if df.empty:
        logger.warning("T5A: No data for %s", symbol)
        return 0

    # Flatten multi-index columns (yfinance 1.5+ returns MultiIndex for single ticker)
    import pandas as pd
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] if col[0] else col[1] for col in df.columns]
    df = df.reset_index()
    # Map yfinance columns to our schema flexibly
    col_map = {}
    for col in df.columns:
        cl = col.lower()
        if cl in ("date", "timestamp", "datetime", "index"):
            col_map[col] = "timestamp"
        elif cl == "open":
            col_map[col] = "open"
        elif cl == "high":
            col_map[col] = "high"
        elif cl == "low":
            col_map[col] = "low"
        elif cl == "close":
            col_map[col] = "close"
        elif cl == "volume":
            col_map[col] = "volume"
    df = df.rename(columns=col_map)
    keep = {"timestamp", "open", "high", "low", "close", "volume"}
    df = df[[c for c in df.columns if c in keep]]

    df = pl.from_pandas(df)
    df = df.with_columns(
        pl.lit(symbol).alias("symbol"),
        pl.col("timestamp").cast(pl.Datetime(time_unit="us", time_zone="UTC")).alias("timestamp"),
    )

    df = df.sort("timestamp")
    cache_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(cache_file)
    logger.info("T5A: %s → %d bars, saved to %s", symbol, df.height, cache_file)
    return df.height


def crawl_crypto_daily(
    symbol: str, start: str, end: str, binance_dir: Path
) -> int:
    """Read crypto daily from existing 1h data by resampling."""
    hourly_path = binance_dir / symbol / "1h_full.parquet"
    if not hourly_path.exists():
        hourly_path = binance_dir / symbol / "1h.parquet"
    if not hourly_path.exists():
        logger.warning("T5A: No hourly data for %s, skipping crypto daily", symbol)
        return 0

    df = pl.read_parquet(hourly_path)
    # Normalize timestamp to UTC for consistent filtering
    ts_dtype = df.schema.get("timestamp")
    if ts_dtype and ts_dtype.time_zone is None:
        df = df.with_columns(
            pl.col("timestamp").dt.replace_time_zone("UTC", ambiguous="earliest")
        )
    # Filter date range
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=UTC)
    end_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=UTC)
    df = df.filter(
        (pl.col("timestamp") >= start_dt) & (pl.col("timestamp") <= end_dt)
    )

    # Resample to daily OHLCV
    df = df.with_columns(
        pl.col("timestamp").dt.date().alias("date")
    )
    daily = df.group_by("date").agg(
        pl.col("open").first().alias("open"),
        pl.col("high").max().alias("high"),
        pl.col("low").min().alias("low"),
        pl.col("close").last().alias("close"),
        pl.col("volume").sum().alias("volume"),
    ).sort("date")

    daily = daily.with_columns(
        pl.lit(symbol).alias("symbol"),
        pl.col("date").cast(pl.Date).alias("date"),
    )
    # Convert date to datetime
    daily = daily.with_columns(
        pl.col("date").cast(pl.Datetime(time_unit="us", time_zone="UTC")).alias("timestamp")
    )

    daily_dir = binance_dir / symbol
    daily_dir.mkdir(parents=True, exist_ok=True)
    daily_path = daily_dir / "1d.parquet"
    daily.write_parquet(daily_path)
    logger.info("T5A: %s daily → %d bars, saved to %s", symbol, daily.height, daily_path)
    return daily.height


async def crawl_all(start: str, end: str) -> None:
    """Crawl all symbols."""
    base = Path("data/raw")
    equity_dir = base / "yfinance"
    binance_dir = base / "binance"

    equity_dir.mkdir(parents=True, exist_ok=True)
    binance_dir.mkdir(parents=True, exist_ok=True)

    # Crawl equities sequentially (Yahoo Finance rate limits)
    for sym in EQUITY_SYMBOLS:
        try:
            crawl_equity_daily(sym, start, end, equity_dir)
        except Exception as exc:
            logger.error("T5A: Failed to crawl %s: %s", sym, exc)

    # Crawl crypto daily from hourly resampling
    for sym in CRYPTO_SYMBOLS:
        try:
            crawl_crypto_daily(sym, start, end, binance_dir)
        except Exception as exc:
            logger.error("T5A: Failed to crawl %s daily: %s", sym, exc)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    parser = argparse.ArgumentParser(description="Crawl daily OHLCV data")
    parser.add_argument("--start", default="2020-01-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", default="2026-09-21", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    asyncio.run(crawl_all(args.start, args.end))
    logger.info("T5A: Daily data crawl complete")


if __name__ == "__main__":
    main()
