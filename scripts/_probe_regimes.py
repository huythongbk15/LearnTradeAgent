#!/usr/bin/env python3
"""Probe BTC/USDT 1h for real regime structure to pick a representative window."""

import polars as pl

df = (
    pl.read_parquet("data/raw/binance/BTC_USDT/1h_full.parquet")
    .sort("timestamp")
    .with_columns(
        ret=(pl.col("close") / pl.col("close").shift(1) - 1),
        vol20=pl.col("close").pct_change(20).std().over(pl.len() - 20),
    )
    .with_columns(
        vol_rank=pl.col("ret").abs().rolling_mean(48).rank().over(pl.len() - 48) / pl.len()
    )
)

# Monthly realised vol + directional drift to expose regime blocks
monthly = (
    df.group_by_dynamic(pl.col("timestamp"), every="1mo")
    .agg(
        pl.col("close").last().alias("close"),
        pl.col("ret").std().alias("vol"),
        pl.col("ret").mean().alias("drift"),
        pl.len().alias("bars"),
    )
    .with_columns(trend_ratio=(pl.col("close") / pl.col("close").shift(3) - 1))
    .drop_nulls()
)

print(f"total bars: {df.height}")
print(f"months: {monthly.height}")
print("\n=== monthly regime profile (vol z-scored across months) ===")
mm = monthly.with_columns(
    vol_z=(pl.col("vol") - pl.col("vol").mean()) / pl.col("vol").std()
)
top_vol = mm.sort("vol_z", descending=True).head(6)
top_calm = mm.sort("vol_z").head(6)
print("HIGH volatility months:")
for r in top_vol.iter_rows(named=True):
    print(f"  {r['timestamp']:%Y-%m}  vol_z={r['vol_z']:+.2f}  ret_m={r['drift']*100:+.3f}%")
print("LOW volatility months:")
for r in top_calm.iter_rows(named=True):
    print(f"  {r['timestamp']:%Y-%m}  vol_z={r['vol_z']:+.2f}  ret_m={r['drift']*100:+.3f}%")

# Find a contiguous 8000-bar window that spans the most distinct vol regimes
print("\n=== candidate 8000-bar windows scored by regime variety ===")
best = []
n = df.height
step = 2000
for start in range(0, n - 8000, step):
    chunk = df.slice(start, 8000)
    v = chunk["ret"].std()
    m = chunk["ret"].abs().rolling_mean(48).fill_null(strategy="forward")
    # variety = how much the rolling vol varies within the window
    variety = float(m.std() / max(float(m.mean()), 1e-12))
    best.append((variety, start, float(v)))

best.sort(reverse=True)
for variety, start, v in best[:5]:
    ts0 = df["timestamp"][start]
    ts1 = df["timestamp"][start + 7999]
    print(f"  start_bar={start:6d} variety={variety:.3f} vol={v:.5f}  {ts0} -> {ts1}")
