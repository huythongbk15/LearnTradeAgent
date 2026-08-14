#!/usr/bin/env python3
"""
Deep-dive signal analysis trên Binance (dữ liệu PUBLIC live).

Không đặt lệnh — chỉ phân tích: quote 1h Enhanced MA + RSI/ATR/Volume/regime
+ replay ngắn để đánh giá edge.

Cách dùng:
    python scripts/deep_dive_binance.py --symbols XRP/USDT,BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT
"""

import argparse
import sys

sys.path.insert(0, "src")

from datetime import datetime

import ccxt
import numpy as np
import polars as pl

from trading_agent.strategies.enhanced_ma import EnhancedMaCrossover

STRATEGY_PARAMS = {"fast_period": 20, "slow_period": 80, "adx_threshold": 40}
LOOKBACK = 1000


def fetch_df(symbol: str) -> pl.DataFrame:
    exchange = ccxt.binance({"enableRateLimit": True})
    ohlcv = exchange.fetch_ohlcv(symbol, "1h", limit=LOOKBACK)
    return pl.DataFrame(
        {
            "timestamp": [datetime.fromtimestamp(b[0] / 1000) for b in ohlcv],
            "open": [b[1] for b in ohlcv],
            "high": [b[2] for b in ohlcv],
            "low": [b[3] for b in ohlcv],
            "close": [b[4] for b in ohlcv],
            "volume": [b[5] for b in ohlcv],
        }
    ).sort("timestamp")


def hhv(df: pl.DataFrame, col: str, n: int) -> pl.Series:
    return df.select(pl.col(col).rolling_max(window_size=n))[col].alias(
        f"hhv_{col}_{n}"
    )


def analyze(symbol: str) -> dict:
    df = fetch_df(symbol)
    strat = EnhancedMaCrossover(STRATEGY_PARAMS)
    df = strat.compute_indicators(df)
    sig = strat.generate_signals(df).to_numpy()
    close = df["close"].to_numpy()
    i = len(close) - 1

    # ── RSI(14) tự tính ────────────────────────────────────────────────
    delta = pl.col("close").diff()
    gain = pl.when(delta > 0).then(delta).otherwise(0.0)
    loss = pl.when(delta < 0).then(-delta).otherwise(0.0)
    avg_gain = gain.rolling_mean(window_size=14)
    avg_loss = loss.rolling_mean(window_size=14)
    rsi = 100 - 100 / (1 + avg_gain / (avg_loss + 1e-9))
    df = df.with_columns(rsi.alias("rsi"))
    rsi = float(df["rsi"].to_numpy()[i])

    # ATR(14) %
    high, low = df["high"].to_numpy(), df["low"].to_numpy()
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(abs(high[1:] - close[:-1]), abs(low[1:] - close[:-1])),
    )
    atr_pct = float(np.mean(tr[-14:]) / close[i] * 100)

    # Volume ratio 24h vs 30d
    vol = df["volume"].to_numpy()
    vol_ratio = float(vol[-24:].mean() / max(vol[-720:].mean(), 1e-9))

    # Range: distance from 90d high/low
    hi90 = float(df["high"][-720:].max())
    lo90 = float(df["low"][-720:].min())
    dist_hi = (hi90 - close[i]) / hi90 * 100
    dist_lo = (close[i] - lo90) / lo90 * 100

    # Regime
    adx = float(df["adx"][i]) if "adx" in df.columns else float("nan")
    regime = "TRENDING" if adx >= 40 else ("TRANSITION" if adx >= 25 else "RANGING")

    # GC: golden cross state hiện tại
    ma_fast = float(df[f"ma_{STRATEGY_PARAMS['fast_period']}"][i])
    ma_slow = float(df[f"ma_{STRATEGY_PARAMS['slow_period']}"][i])
    bull = ma_fast > ma_slow

    # ── Replay chiến lược (không lookahead) ────────────────────────────
    pos = np.zeros(len(sig), dtype=np.int8)
    in_pos = False
    for k in range(len(sig)):
        if not in_pos and sig[k] == 1:
            in_pos = True
        elif in_pos and sig[k] == -1:
            in_pos = False
        pos[k] = 1 if in_pos else 0

    # Equity curve từ điểm bắt đầu có đủ indicator (bỏ warmup 120 bars)
    start = 120
    ret = np.diff(close) / close[:-1]
    strat_ret = pos[:-1] * ret
    strat_eq = np.cumprod(1 + strat_ret[start:])
    buy_hold_eq = np.cumprod(1 + ret[start:])
    ret_strat = float((strat_eq[-1] - 1) * 100)
    ret_bh = float((buy_hold_eq[-1] - 1) * 100)
    dd = float((strat_eq / np.maximum.accumulate(strat_eq) - 1).min() * 100)
    n_trades = int((sig[1:] != sig[:-1]).sum() / 2)
    n_buy24h = int((sig[-24:] == 1).sum())
    n_sell24h = int((sig[-24:] == -1).sum())

    return {
        "symbol": symbol,
        "price": close[i],
        "ma_fast": ma_fast,
        "ma_slow": ma_slow,
        "bull": bull,
        "adx": adx,
        "regime": regime,
        "rsi": rsi,
        "atr_pct": atr_pct,
        "vol_ratio": vol_ratio,
        "dist_hi90": dist_hi,
        "dist_lo90": dist_lo,
        "state": "LONG" if in_pos else "FLAT",
        "n_buy24h": n_buy24h,
        "n_sell24h": n_sell24h,
        "ret_90d": ret_strat,
        "ret_bh": ret_bh,
        "max_dd": dd,
        "n_trades": n_trades,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--symbols", default="XRP/USDT,BTC/USDT,ETH/USDT,SOL/USDT,BNB/USDT")
    args = p.parse_args()
    syms = [s.strip() for s in args.symbols.split(",") if s.strip()]

    print("=" * 118)
    print(
        "🔬 DEEP-DIVE — Binance PUBLIC 1h | Enhanced MA(20,80,ADX40) + RSI/ATR/Volume/Regime"
    )
    print("=" * 118)
    hdr = (
        f"{'SYMBOL':<11}{'PRICE':>10}{'MA20/MA80':>9}{'BULL':>5}{'ADX':>6}{'REGIME':>11}"
        f"{'RSI':>6}{'ATR%':>6}{'VOLR':>6}{'DIST-HI':>8}{'DIST-LO':>8}  {'STATE':>5}{'24hSIG':>7}"
    )
    print(hdr)
    print("-" * 82)

    rows = []
    for s in syms:
        try:
            r = analyze(s)
            rows.append(r)
            print(
                f"{r['symbol']:<11}{r['price']:>10,.1f}{r['ma_fast']:>9,.0f}/{r['ma_slow']:>5,.0f}"
                f"{'✅' if r['bull'] else '❌':>5}{r['adx']:>6,.1f}{r['regime']:>11}"
                f"{r['rsi']:>6,.0f}{r['atr_pct']:>6,.1f}{r['vol_ratio']:>6,.2f}"
                f"{r['dist_hi90']:>7,.1f}%{r['dist_lo90']:>7,.1f}%  {r['state']:>5}"
                f"{r['n_buy24h']}/{r['n_sell24h']:>3}"
            )
        except Exception as e:
            print(f"{s:<11}ERROR: {e}")

    print("-" * 118)
    print("\n📈 Edge test (replay chiến lược, dữ liệu 1h LIVE, sau warmup):")
    for r in rows:
        verdict = (
            "🔥 +EDGE"
            if r["ret_90d"] > r["ret_bh"] + 5 and r["max_dd"] > -25
            else ("⚠️ SLOW/NOISE" if r["max_dd"] < -40 else "➡️ BÌNH THƯỜNG")
        )
        print(
            f"  {r['symbol']:<11} strat {r['ret_90d']:>7,.1f}% | buy&hold {r['ret_bh']:>7,.1f}% | maxDD {r['max_dd']:>6,.1f}% | trades {r['n_trades']:>4}  {verdict}"
        )


if __name__ == "__main__":
    main()
