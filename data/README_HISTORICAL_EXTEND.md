# Extending Historical Data

## Context
The existing parquet (`data/raw/binance/BTC_USDT/1h.parquet`) only covers **2023-01-01 → 2026-08-17**.
Critical volatility regimes missing:
- **2020 March crash** (BTC -50% in 3 days, hourly vol >10%)
- **2022 Nov FTX collapse** (BTC -30% in 24h, hourly vol >7%)

## How to Add Historical Data

```bash
# BTC/USDT 1h from 2020-01-01 (covers both crashes)
python -m trading_agent.data.collector download --symbol BTC/USDT --tf 1h --since 2020-01-01

# ETH/USDT for cross-asset
python -m trading_agent.data.collector download --symbol ETH/USDT --tf 1h --since 2020-01-01
```

## Data Volume
- BTC 1h: ~43,800 candles/year × 6.5 years ≈ 285,000 candles
- Binance API limit: 1500 candles/request → ~190 requests per year
- Rate limit: 1200 req/min → should take 2-5 minutes per year

## Usage in O-TRADE-1
After extending, run:
```bash
python scripts/o_trade_1_volatile.py --start-date 2020-03-09 --end-date 2020-03-20 --bars 300
```
