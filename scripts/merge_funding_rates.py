#!/usr/bin/env python3
"""Fetch all historical Binance BTCUSDT funding rates and merge into parquet.

Binance API returns max 1000 entries per request. We paginate via startTime
to cover the full data range, then forward-fill funding rates to each
1-hour bar.
"""
from __future__ import annotations

import ccxt
import polars as pl
import pandas as pd
import numpy as np
from datetime import datetime, timezone
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger(__name__)

PARQUET_PATH = Path("data/raw/binance/BTC_USDT/1h.parquet")


def fetch_all_funding_rates(symbol: str = "BTCUSDT") -> list[dict]:
    """Fetch funding rates in paginated batches until we have full coverage."""
    exchange = ccxt.binance()
    all_rates: list[dict] = []
    seen_times: set[int] = set()

    # We want rates from 2023-01-01 onward
    data_start_ms = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    current_start = data_start_ms
    limit = 1000

    while True:
        params = {"symbol": symbol, "limit": limit, "startTime": current_start}
        batch = exchange.fapiPublicGetFundingRate(params)
        if not batch:
            break

        for r in batch:
            ft = int(r["fundingTime"])
            if ft not in seen_times:
                seen_times.add(ft)
                all_rates.append(r)

        oldest = int(batch[-1]["fundingTime"])
        newest = int(batch[0]["fundingTime"])
        logger.info(
            f"  Batch: {len(batch)} rates, "
            f"{datetime.fromtimestamp(newest/1000, tz=timezone.utc)} → "
            f"{datetime.fromtimestamp(oldest/1000, tz=timezone.utc)}"
        )

        # If we got fewer than `limit`, we're at the newest end
        if len(batch) < limit:
            break

        # Move startTime to just after the oldest fetched rate to get older data
        current_start = oldest + 1

        # Safety: avoid infinite loop
        if len(all_rates) > 4000:
            logger.info("  Reached safety cap of 2000 rates")
            break

    all_rates.sort(key=lambda x: int(x["fundingTime"]))
    return all_rates


def merge_funding_rates() -> None:
    """Load parquet, fetch funding rates, merge, and write back."""
    df = pl.read_parquet(PARQUET_PATH)
    logger.info(f"Loaded {len(df)} rows, columns: {df.columns}")
    logger.info(f"Date range: {df['timestamp'].min()} → {df['timestamp'].max()}")

    # Fetch funding rates
    logger.info("\nFetching funding rates from Binance (paginated)...")
    rates_raw = fetch_all_funding_rates()
    logger.info(f"\nTotal funding rates fetched: {len(rates_raw)}")

    if not rates_raw:
        logger.warning("No funding rates fetched — writing all-zero column")
        df = df.with_columns(pl.lit(0.0).alias("funding_rate"))
        df.write_parquet(PARQUET_PATH)
        return

    # Build rates DataFrame
    rates_df = pd.DataFrame([
        {
            "funding_time": datetime.fromtimestamp(int(r["fundingTime"]) / 1000, tz=timezone.utc),
            "funding_rate": float(r["fundingRate"]),
        }
        for r in rates_raw
    ]).sort_values("funding_time").drop_duplicates(subset=["funding_time"])
    rates_df["funding_time"] = rates_df["funding_time"].dt.tz_localize(None)

    logger.info(f"Rate range: {rates_df['funding_time'].min()} → {rates_df['funding_time'].max()}")
    logger.info(f"Rate stats: mean={rates_df['funding_rate'].mean():.6f}, "
                f"min={rates_df['funding_rate'].min():.6f}, "
                f"max={rates_df['funding_rate'].max():.6f}")
    logger.info(f"Negative pct: {(rates_df['funding_rate'] < 0).mean()*100:.1f}%")
    logger.info(f"Positive pct: {(rates_df['funding_rate'] > 0).mean()*100:.1f}%")

    # Merge: each hourly bar gets the funding rate in effect at that time
    df_pd = df.to_pandas()
    # Drop existing funding_rate column (from previous runs) before merge
    if "funding_rate" in df_pd.columns:
        df_pd = df_pd.drop(columns=["funding_rate"])
    df_pd["timestamp"] = pd.to_datetime(df_pd["timestamp"])
    df_pd["_funding_time"] = df_pd["timestamp"].dt.floor("8h")
    df_pd = df_pd.merge(rates_df.rename(columns={"funding_time": "_funding_time"}),
                        on="_funding_time", how="left")
    df_pd = df_pd.drop(columns=["_funding_time"])

    # Forward-fill within gaps (between rate windows), backward-fill for the very start
    df_pd["funding_rate"] = df_pd["funding_rate"].ffill().bfill().fillna(0.0)

    # Check coverage
    zero_count = (df_pd["funding_rate"] == 0.0).sum()
    logger.info(f"\nAfter merge: {zero_count} rows with zero funding rate ({zero_count/len(df_pd)*100:.1f}%)")

    df_final = pl.from_pandas(df_pd)
    df_final.write_parquet(PARQUET_PATH)
    logger.info(f"\n✅ Updated {PARQUET_PATH} with funding_rate column")
    logger.info(f"Columns: {df_final.columns}")


if __name__ == "__main__":
    merge_funding_rates()
