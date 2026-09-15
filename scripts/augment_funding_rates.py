#!/usr/bin/env python3
"""Fetch Binance BTCUSDT funding rates and merge into market data CSV.

Funding rates are published every 8h. We forward-fill to each hourly bar
so that funding_carry strategy receives real rates instead of synthetic.
"""
from __future__ import annotations

import ccxt
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def fetch_funding_rates(symbol: str = "BTCUSDT", hours_back: int = 6000) -> pd.DataFrame:
    """Fetch historical funding rates from Binance Futures."""
    exchange = ccxt.binance()
    all_rates = []
    # Binance returns max 1000 per request; fetch starting from hours_back ago
    start_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000) - hours_back * 3600000
    params = {"symbol": symbol, "limit": 1000, "startTime": start_ms}
    batch = exchange.fapiPublicGetFundingRate(params)
    all_rates.extend(batch)
    logger.info(f"Fetched {len(batch)} funding rates from startTime")

    # Deduplicate by fundingTime
    seen = set()
    unique = []
    for r in all_rates:
        if r["fundingTime"] not in seen:
            seen.add(r["fundingTime"])
            unique.append(r)
    unique.sort(key=lambda x: x["fundingTime"])

    df = pd.DataFrame([
        {
            "funding_time": datetime.fromtimestamp(int(r["fundingTime"]) / 1000, tz=timezone.utc),
            "funding_rate": float(r["fundingRate"]),
        }
        for r in unique
    ])
    return df


def merge_funding_into_csv(csv_path: str, output_path: str | None = None) -> None:
    """Merge funding rates into market data CSV."""
    output_path = output_path or csv_path

    # Load market data
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df["funding_time"] = df["timestamp"].dt.floor("8h")

    # Fetch funding rates covering the data range
    data_start = df["timestamp"].min()
    data_start_ms = int(data_start.timestamp() * 1000)
    exchange = ccxt.binance()
    params = {"symbol": "BTCUSDT", "limit": 1000, "startTime": data_start_ms}
    batch = exchange.fapiPublicGetFundingRate(params)
    logger.info(f"Fetched {len(batch)} rates covering data range {data_start}")

    rates_df = pd.DataFrame([
        {
            "funding_time": pd.to_datetime(int(r["fundingTime"]) / 1000, unit="s", utc=True).tz_localize(None),
            "funding_rate": float(r["fundingRate"]),
        }
        for r in batch
    ])
    rates_df = rates_df.sort_values("funding_time").drop_duplicates(subset="funding_time")

    # Merge: map each bar's funding_time to the nearest preceding funding rate
    df = df.merge(rates_df, on="funding_time", how="left")
    df["funding_rate"] = df["funding_rate"].ffill().bfill().fillna(0.0)

    # Also add perp_price as mark price if available (for future use)
    df = df.drop(columns=["funding_time"])

    # Backup original
    if output_path == csv_path:
        backup = Path(csv_path).with_suffix(".csv.bak")
        if not backup.exists():
            pd.read_csv(csv_path).to_csv(backup, index=False)
            logger.info(f"Backed up original to {backup}")

    df.to_csv(output_path, index=False)
    logger.info(f"Saved augmented data to {output_path}")
    logger.info(f"  Columns: {df.columns.tolist()}")
    logger.info(f"  Funding rate stats: mean={df['funding_rate'].mean():.6f}, "
                f"std={df['funding_rate'].std():.6f}")
    logger.info(f"  Negative pct: {(df['funding_rate'] < -0.0001).mean()*100:.1f}%")
    logger.info(f"  Positive pct: {(df['funding_rate'] > 0).mean()*100:.1f}%")


if __name__ == "__main__":
    merge_funding_into_csv(
        "data/processed/BTC_USDT_1h.csv",
        "data/processed/BTC_USDT_1h_funding.csv",
    )
