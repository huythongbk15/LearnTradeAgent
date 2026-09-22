# T5A-1: Cross-Asset Diversification with Equities + Daily Timeframe

## Status: PASS (T5A + T2C-1)

## What Changed

### 1. Daily Data Crawl (`scripts/crawl_daily_data.py`)
- **18 equities** crawled from Yahoo Finance: SPY, QQQ, AAPL, MSFT, GOOGL, AMZN,
  NVDA, TSLA, META, BRK-B, JPM, JNJ, XOM, WMT, V, MA, UNH, DIS
- **3 crypto** daily data resampled from 1h Binance data: BTC_USDT, ETH_USDT, BNB_USDT
- Time range: 2020-01-01 → 2026-09-21 (~1687 bars/equity, ~2456 bars/crypto)

### 2. T5A Eval Updated (`scripts/o_trade_345_eval.py`)

#### `load_symbol_daily()`
New loader supporting both yfinance (equities) and Binance (crypto daily) data.

#### `t5a_cross_asset_diversification()` — extended fields:
- `--timeframe daily|1h` flag
- `--t5a-symbols` for custom cross-asset universe
- MA crossover signal (10-day/30-day) for daily timeframe
- Sharpe annualization: sqrt(252) for daily, sqrt(24) for hourly
- Correlation matrix output + equity vs crypto classification

#### T2C-1: Equity Slippage Calibration
New `t2c_equity_slippage_calibration()` + `--t2c-symbol` CLI flag.

### 3. Validation Results

#### T5A (Daily): PASS -- benefit=0.7296 (>0.30 threshold)
- Portfolio Sharpe: 1.4659 vs Mean Individual: 0.7364
- Individual Sharpe: BTC=0.84, ETH=1.01, BNB=1.08, SPY=0.65, QQQ=0.64, AAPL=0.66, MSFT=0.30, GOOGL=0.50, NVDA=0.94
- BTC↔SPY corr: -0.029 (cross-asset-class, very low!)
- SPY↔QQQ corr: 0.934 (equity-internal, high)

#### T2C-1 (SPY): PASS
- Spread: 5.03 bps, Mean slippage: 21.51 bps, Max (95th): 45.97 bps
- Limit fill prob: 88.5%

### 4. Correlation Drop Validation

| Pair Class | Correlation | vs Target |
|---|---|---|
| BTC-USP: -0.029, BTC-AAPL: -0.030, ETH-QQQ: -0.002 | < 0.30 | PASS |
| BTC-ETH: 0.823, SPY-QQQ: 0.934 | > 0.30 | Expected (within-asset-class) |

**Conclusion**: Cross-asset-class correlation drops well below 0.30, enabling 0.73+ benefit.
Within-asset-class remains high (crypto~0.82, equity~0.93).

## Usage

```bash
python scripts/o_trade_345_eval.py   --start-date 2020-01-01 --end-date 2026-09-21 --bars 300   --timeframe daily   --t5a-symbols BTC_USDT,ETH_USDT,BNB_USDT,SPY,QQQ,AAPL,MSFT,GOOGL,NVDA   --t2c-symbol SPY

python scripts/crawl_daily_data.py --start 2020-01-01 --end 2026-09-21
```
