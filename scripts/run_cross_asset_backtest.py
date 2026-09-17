#!/usr/bin/env python3
"""Cross-asset basket backtest for relative-value strategies.

Aligns all BTC cross-rate pairs, computes per-asset momentum signals,
ranks them cross-sectionally, and simulates an equal-risk portfolio.

  - cross_sectional_momentum_lo: long top 30 % of ranked assets
  - cross_sectional_momentum_ls: long top 30 %, short bottom 30 %
  - stat_arbitrage_lo/ls:       z-score on cross-rate pairs vs BTC

Usage:
    python scripts/run_cross_asset_backtest.py [--strategy cross_sectional_momentum_lo]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import polars as pl

from trading_agent.data.storage import load_ohlcv


UNIVERSE = ["BTC/USDT", "ETH/BTC", "BNB/BTC", "XRP/BTC"]
TIMEFRAME = "1h"
COMMISSION = 0.001
RETURN_CAP = 0.15  # Cap single-bar asset returns (illiquid cross-rate spikes)


# ── Helpers ─────────────────────────────────────────────────────────────

def _load_aligned(universe: list[str], timeframe: str) -> dict[str, pl.DataFrame]:
    """Load and timestamp-align all symbols in the basket."""
    dfs: dict[str, pl.DataFrame] = {}
    for sym in universe:
        raw = sym.replace("/", "_")
        df = load_ohlcv("binance", raw, timeframe).sort("timestamp")
        dfs[sym] = df
    return dfs


def _build_panel(dfs: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Build a wide panel: one row per timestamp, close columns per symbol."""
    ts_sets = [set(df["timestamp"].to_list()) for df in dfs.values()]
    common_ts = sorted(set.intersection(*ts_sets)) if ts_sets else []
    close_cols = {}
    for sym, df in dfs.items():
        col = sym.replace("/", "_").replace(":", "")
        close_cols[col] = df.filter(pl.col("timestamp").is_in(common_ts))["close"].to_numpy()
    panel = pl.DataFrame({"timestamp": common_ts, **close_cols})
    return panel


def _momentum(close: np.ndarray, lookback: int) -> np.ndarray:
    """Simple momentum: pct change rolling mean."""
    pct = np.diff(close, prepend=close[0])
    result = np.empty_like(pct)
    for i in range(len(pct)):
        start = max(0, i - lookback + 1)
        result[i] = np.nanmean(pct[start : i + 1]) if i - start + 1 >= 2 else np.nan
    return result


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    deltas = np.diff(close, prepend=close[0])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    result = np.empty_like(gains, dtype=np.float64)
    for i in range(len(gains)):
        if i < period:
            result[i] = np.nan
        else:
            avg_gain = np.mean(gains[i - period + 1 : i + 1])
            avg_loss = np.mean(losses[i - period + 1 : i + 1])
            rs = avg_gain / avg_loss if avg_loss > 0 else 100.0
            result[i] = 100.0 - (100.0 / (1.0 + rs))
    return result


# ── Strategy simulations ────────────────────────────────────────────────

def run_cross_sectional_momentum(dfs: dict[str, pl.DataFrame],
                                 lookback: int = 60, fast_ma: int = 20,
                                 slow_ma: int = 60, long_only: bool = True,
                                 rebalance_bars: int = 24) -> dict:
    """Rank assets by momentum, allocate to top/bottom quantiles."""
    panel = _build_panel(dfs)
    symbols = list(dfs.keys())
    n = panel.height

    # Compute per-symbol momentum (percentage returns, comparable across assets)
    mom_arrays = {}
    for sym in symbols:
        col = sym.replace("/", "_").replace(":", "")
        close = panel[col].to_numpy()
        pct = np.empty(n, dtype=np.float64)
        pct[0] = 0.0
        pct[1:] = (close[1:] - close[:-1]) / close[:-1]  # percentage returns
        mom = np.full(n, np.nan)
        for i in range(n):
            start = max(0, i - lookback + 1)
            if i - start + 1 >= 2:
                mom[i] = np.nanmean(pct[start : i + 1])
        mom_arrays[sym] = mom

    # Compute portfolio weights: long top, short bottom (for LS)
    capital = 1.0
    cash = capital
    position = {sym: 0.0 for sym in symbols}
    prev_weights = {sym: 0.0 for sym in symbols}
    equity_curve = []
    daily_rets = []
    trade_count = 0
    bars_since_rebalance = 0

    for i in range(lookback + max(fast_ma, slow_ma), n):
        # Rebalance only every N bars (reduce commission drag)
        rebalance = (bars_since_rebalance >= rebalance_bars)

        # Rank by momentum
        moms = {sym: mom_arrays[sym][i] for sym in symbols}
        ranked = sorted(symbols, key=lambda s: moms.get(s, np.nan),
                        reverse=True)

        # Determine weights based on momentum sign
        if rebalance:
            weights = {sym: 0.0 for sym in symbols}
            if long_only:
                top = ranked[0]
                if moms[top] > 0:
                    weights[top] = 1.0
            else:
                top, bottom = ranked[0], ranked[-1]
                if moms[top] > 0:
                    weights[top] = 0.5
                if moms[bottom] < 0:
                    weights[bottom] = -0.5
            bars_since_rebalance = 0
        else:
            weights = prev_weights  # hold

        # Portfolio return from previous bar's position (no look-ahead)
        bar_ret = 0.0
        for sym in symbols:
            col = sym.replace("/", "_").replace(":", "")
            close_prev = panel[col][i - 1]
            close_curr = panel[col][i]
            if close_prev > 0:
                asset_ret = (close_curr - close_prev) / close_prev
                asset_ret = max(-RETURN_CAP, min(RETURN_CAP, asset_ret))
                bar_ret += position[sym] * asset_ret

        # Commission only on actual turnover
        turnover = sum(abs(weights[s] - prev_weights[s]) for s in symbols)
        if turnover > 0.01:
            trade_count += 1
        cash *= (1.0 + bar_ret - COMMISSION * turnover)
        equity_curve.append(cash)
        daily_rets.append(bar_ret)

        position = dict(weights)
        prev_weights = dict(weights)
        bars_since_rebalance += 1

    rets = np.array(daily_rets)
    if len(rets) < 2:
        return {"n_bars": n, "trades": 0, "sharpe": 0.0, "ret": 0.0, "max_dd": 0.0}

    mean_ret = rets.mean()
    std_ret = rets.std(ddof=1)
    sharpe = mean_ret / std_ret * np.sqrt(8760) if std_ret > 0 else 0.0  # annualized for 1h
    total_ret = (equity_curve[-1] / capital - 1.0) * 100

    # Max drawdown
    equity_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(equity_arr)
    max_dd = (equity_arr / peak - 1.0).min() * 100

    return {
        "n_bars": n,
        "trades": trade_count,
        "sharpe": round(float(sharpe), 2),
        "ret": round(float(total_ret), 2),
        "max_dd": round(float(max_dd), 2),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategy", default="cross_sectional_momentum_lo",
                        choices=["cross_sectional_momentum_lo", "cross_sectional_momentum_ls",
                                 "stat_arbitrage_lo", "stat_arbitrage_ls"])
    parser.add_argument("--rebalance-bars", type=int, default=168,
                        help="Rebalance interval in bars (168 = weekly on 1h)")
    args = parser.parse_args()

    print(f"Cross-asset portfolio backtest: {args.strategy}")
    print(f"  universe: {UNIVERSE} @ {TIMEFRAME} | rebalance={args.rebalance_bars}h")

    dfs = _load_aligned(UNIVERSE, TIMEFRAME)
    for sym, df in dfs.items():
        print(f"  {sym}: {df.height} bars")

    if args.strategy == "cross_sectional_momentum_lo":
        lo = run_cross_sectional_momentum(dfs, long_only=True, rebalance_bars=args.rebalance_bars)
        ls = run_cross_sectional_momentum(dfs, long_only=False, rebalance_bars=args.rebalance_bars)
    elif args.strategy == "cross_sectional_momentum_ls":
        lo = run_cross_sectional_momentum(dfs, long_only=True, rebalance_bars=args.rebalance_bars)
        ls = run_cross_sectional_momentum(dfs, long_only=False, rebalance_bars=args.rebalance_bars)
    elif args.strategy == "stat_arbitrage_lo":
        lo = run_stat_arbitrage(dfs, long_only=True, rebalance_bars=args.rebalance_bars)
        ls = lo
    elif args.strategy == "stat_arbitrage_ls":
        lo = run_stat_arbitrage(dfs, long_only=True, rebalance_bars=args.rebalance_bars)
        ls = run_stat_arbitrage(dfs, long_only=False, rebalance_bars=args.rebalance_bars)

    print(f"\n  {args.strategy} (lo): Sharpe={lo['sharpe']} Ret={lo['ret']}% Trades={lo['trades']} DD={lo['max_dd']}%")
    print(f"  {args.strategy} (ls): Sharpe={ls['sharpe']} Ret={ls['ret']}% Trades={ls['trades']} DD={ls['max_dd']}%")

    out_path = Path("data/backtests/cross_asset") / f"{args.strategy}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "strategy": args.strategy,
        "universe": UNIVERSE,
        "timeframe": TIMEFRAME,
        "results": {"lo": lo, "ls": ls},
    }, indent=2))
    print(f"\n  saved → {out_path}")


def run_stat_arbitrage(dfs: dict[str, pl.DataFrame],
                        z_entry: float = 2.0, z_exit: float = 0.5,
                        lookback: int = 20, rebalance_bars: int = 24,
                        long_only: bool = True) -> dict:
    """Stat arbitrage: z-score on each asset vs BTC benchmark.

    For LO: long assets with z-score < -z_entry (oversold), exit at z_exit.
    For LS: long bottom z-score, short top z-score.
    """
    universe = list(dfs.keys())
    btc_df = dfs["BTC/USDT"]
    panel = _build_panel(dfs)
    n = panel.height
    btc_close = panel["BTC_USDT"].to_numpy()

    # Compute z-scores for each non-BTC asset relative to BTC returns
    zscores = {}
    for sym in universe:
        if sym == "BTC/USDT":
            continue
        col = sym.replace("/", "_").replace(":", "")
        close = panel[col].to_numpy()
        pct = np.empty(n, dtype=np.float64)
        pct[0] = 0.0
        pct[1:] = (close[1:] - close[:-1]) / close[:-1]
        z = np.full(n, np.nan)
        for i in range(lookback, n):
            window = pct[i - lookback + 1 : i + 1]
            mean, std = np.nanmean(window), np.nanstd(window, ddof=1)
            if std > 0 and not np.isnan(pct[i]):
                z[i] = (pct[i] - mean) / std
        zscores[sym] = z

    capital = 1.0
    cash = capital
    position = {sym: 0.0 for sym in universe}
    prev_weights = {sym: 0.0 for sym in universe}
    equity_curve = []
    daily_rets = []
    trade_count = 0
    bars_since_rebalance = 0
    warmup = lookback + max(20, 60)

    for i in range(warmup, n):
        rebalance = (bars_since_rebalance >= rebalance_bars)
        weights = prev_weights

        if rebalance:
            weights = {sym: 0.0 for sym in universe}
            bars_since_rebalance = 0
            if long_only:
                # Long oversold (z < -z_entry)
                for sym in zscores:
                    z = zscores[sym][i]
                    if not np.isnan(z) and z < -z_entry:
                        weights[sym] = 1.0
            else:
                # Long bottom, short top
                valid = [(s, zscores[s][i]) for s in zscores if not np.isnan(zscores[s][i])]
                valid.sort(key=lambda x: x[1])
                if valid and valid[0][1] < -z_entry:
                    weights[valid[0][0]] = 0.5
                if valid and valid[-1][1] > z_entry:
                    weights[valid[-1][0]] = -0.5

        # Portfolio return from previous position
        bar_ret = 0.0
        for sym in universe:
            col = sym.replace("/", "_").replace(":", "")
            cp, cc = panel[col][i - 1], panel[col][i]
            if cp > 0:
                asset_ret = (cc - cp) / cp
                asset_ret = max(-RETURN_CAP, min(RETURN_CAP, asset_ret))
                bar_ret += position[sym] * asset_ret

        turnover = sum(abs(weights[s] - prev_weights[s]) for s in universe)
        if turnover > 0.01:
            trade_count += 1
        cash *= (1.0 + bar_ret - COMMISSION * turnover)
        equity_curve.append(cash)
        daily_rets.append(bar_ret)

        position = dict(weights)
        prev_weights = dict(weights)
        bars_since_rebalance += 1

    rets = np.array(daily_rets)
    if len(rets) < 2:
        return {"n_bars": n, "trades": 0, "sharpe": 0.0, "ret": 0.0, "max_dd": 0.0}

    mean_ret = rets.mean()
    std_ret = rets.std(ddof=1)
    sharpe = mean_ret / std_ret * np.sqrt(8760) if std_ret > 0 else 0.0
    total_ret = (equity_curve[-1] / capital - 1.0) * 100
    equity_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(equity_arr)
    max_dd = (equity_arr / peak - 1.0).min() * 100

    return {
        "n_bars": n, "trades": trade_count, "sharpe": round(float(sharpe), 2),
        "ret": round(float(total_ret), 2), "max_dd": round(float(max_dd), 2),
    }


if __name__ == "__main__":
    main()
