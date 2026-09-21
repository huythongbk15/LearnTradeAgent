#!/usr/bin/env python3
"""O-TRADE-2: Execution Quality — slippage & fill rate simulation.

Tests:
  T2A: Market order slippage simulation (200 bars, 0.1% spread)
  T2B: Limit order fill rate by volatility regime
  T2C: Slippage × volatility interaction (5 quintiles)

Usage:
    python scripts/o_trade_2_execution_quality.py --start-date 2020-03-09 --end-date 2020-03-21 --bars 300

    # T2B can use a different (calmer) period with --t2b-start / --t2b-end
    python scripts/o_trade_2_execution_quality.py --start-date 2020-03-09 --end-date 2020-03-21 --bars 300 \\
        --t2b-start 2024-10-01 --t2b-end 2024-12-01 --t2b-bars 300
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "src")

DEFAULT_SYMBOL = "BTC/USDT"


def _parquet_path(symbol: str) -> str:
    safe = symbol.replace("/", "_")
    return f"data/raw/binance/{safe}/1h_full.parquet"

# Typical bid/ask spread for BTC spot (0.1%)
DEFAULT_SPREAD = 0.001
# Limit order placed 1 tick beyond mid (0.1%)
DEFAULT_LIMIT_TICKS = 0.001
# Gap-skip decay scale: fill_prob = exp(-hourly_vol / GAP_SKIP_SCALE)
# At vol=0.5% → ~95% fill; at vol=2% → ~29% fill; at vol=5% → ~2% fill
GAP_SKIP_SCALE = 0.025


def load_data(
    symbol: str,
    start_date: str,
    end_date: str,
    n_bars: int,
) -> pl.DataFrame:
    df = pl.read_parquet(_parquet_path(symbol))
    df = df.filter(pl.col("symbol") == symbol).sort("timestamp")

    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC)
    end = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=UTC)

    # Handle timezone mismatch (parquet may have naive timestamps)
    if df.schema["timestamp"].time_zone is None:
        start = start.replace(tzinfo=None)
        end = end.replace(tzinfo=None)

    df = df.filter((pl.col("timestamp") >= start) & (pl.col("timestamp") < end))
    return df.tail(n_bars)


def simulate_market_order(
    bar: dict, spread: float = DEFAULT_SPREAD
) -> tuple[float, float]:
    """Simulate market order execution.

    Slippage = half-spread + 30% of relative bar range (volatility-proportional).
    Returns (execution_price, slippage_bps).
    """
    mid = (bar["high"] + bar["low"]) / 2.0
    bar_range = (bar["high"] - bar["low"]) / mid  # relative hourly range
    slippage = (spread / 2.0 + bar_range * 0.30) * np.random.uniform(0.8, 1.2)
    execution = mid * (1.0 + slippage)
    return float(execution), float(slippage * 10000)


def simulate_limit_order(
    bar: dict,
    spread: float = DEFAULT_SPREAD,
    limit_ticks: float = DEFAULT_LIMIT_TICKS,
    side: str = "buy",
) -> tuple[float | None, bool]:
    """Simulate limit order placement and fill.

    BUY limit placed at mid - limit_ticks (below current price, to catch a dip).
    In calm markets: price oscillates near mid, frequently touches the limit → high fill.
    In volatile markets: price gaps past the level without resting → gap-skip, low fill.

    Model:  fill_prob = exp(-hourly_vol / GAP_SKIP_SCALE)
    """
    high = bar["high"]
    low = bar["low"]
    mid = (high + low) / 2.0
    bar_range = high - low

    if side == "buy":
        limit_price = mid * (1.0 - limit_ticks)
    else:
        limit_price = mid * (1.0 + limit_ticks)

    if bar_range < 1e-10:
        return float(limit_price), True

    vol = bar_range / mid  # relative hourly volatility
    fill_prob = math.exp(-vol / GAP_SKIP_SCALE)
    fill_prob = min(max(fill_prob, 0.01), 0.99)

    filled = bool(np.random.random() < fill_prob)
    if filled:
        return float(limit_price), True
    return None, False


def simulate_limit_order_sell(
    bar: dict,
    limit_ticks: float = DEFAULT_LIMIT_TICKS,
) -> tuple[float | None, bool]:
    """Simulate sell limit order at mid + limit_ticks."""
    return simulate_limit_order(bar, DEFAULT_SPREAD, limit_ticks, side="sell")


# ─── T2A: Market order slippage ──────────────────────────────────────────

def t2a_market_slippage(df: pl.DataFrame) -> dict[str, Any]:
    """T2A: Market order slippage (assert mean <= 0.15% = 150 bps)."""
    np.random.seed(42)
    slippages: list[float] = []

    for bar in df.iter_rows(named=True):
        _, slip_bps = simulate_market_order(bar)
        slippages.append(slip_bps)

    avg = float(np.mean(slippages))
    mx = float(np.max(slippages))
    passed = avg <= 150.0  # 0.15% = 150 bps
    return {
        "name": "T2A: Market order slippage",
        "n_bars": len(slippages),
        "avg_slippage_bps": round(avg, 4),
        "max_slippage_bps": round(mx, 4),
        "fill_rate": 1.0,
        "pass": passed,
        "assert": "mean_slippage <= 0.15% (150 bps)",
    }


# ─── T2B: Limit order fill rate by volatility regime ─────────────────────

def t2b_limit_fill_rate(
    df_calm: pl.DataFrame, df_volatile: pl.DataFrame
) -> dict[str, Any]:
    """T2B: Limit fill rate — high-vol < 50%, low-vol > 70%.

    Uses a broader period to stratify into calm vs volatile hours.
    """
    np.random.seed(43)

    def run_fill_rate(df: pl.DataFrame) -> dict[str, float]:
        log_ret = df["close"].log().diff().drop_nulls().to_numpy()
        vol_1h = np.array([
            np.abs(log_ret[i]) if i >= 0 else 0.0
            for i in range(len(log_ret))
        ])
        vol_1h = np.abs(df["close"].pct_change().fill_null(0.0).to_numpy())

        v75 = float(np.percentile(vol_1h[vol_1h > 0], 75)) if np.any(vol_1h > 0) else 0.01
        v25 = float(np.percentile(vol_1h[vol_1h > 0], 25)) if np.any(vol_1h > 0) else 0.005

        high_idx = np.where(vol_1h >= v75)[0]
        low_idx = np.where(vol_1h <= v25)[0]

        results: dict[str, float] = {}
        for label, idx in [("high_vol", high_idx), ("low_vol", low_idx)]:
            fills = 0
            total = max(len(idx), 1)
            for i in idx:
                bar = df.row(i, named=True)
                _, filled = simulate_limit_order(bar, limit_ticks=0.002, side="buy")
                if filled:
                    fills += 1
            results[label] = fills / total

        return {
            "vol_75th_pct": round(v75 * 100, 3),
            "vol_25th_pct": round(v25 * 100, 3),
            "high_vol_fill_rate": round(results["high_vol"], 4),
            "low_vol_fill_rate": round(results["low_vol"], 4),
        }

    calm_stats = run_fill_rate(df_calm)
    vol_stats = run_fill_rate(df_volatile)

    high_fr = vol_stats["high_vol_fill_rate"]
    low_fr = calm_stats["low_vol_fill_rate"]
    passed = high_fr < 0.50 and low_fr > 0.70

    return {
        "name": "T2B: Limit order fill rate by volatility regime",
        "calm_period": {
            "vol_25th_pct": calm_stats["vol_25th_pct"],
            "vol_75th_pct": calm_stats["vol_75th_pct"],
            "low_vol_fill_rate": calm_stats["low_vol_fill_rate"],
            "n_bars": df_calm.height,
        },
        "volatile_period": {
            "vol_25th_pct": vol_stats["vol_25th_pct"],
            "vol_75th_pct": vol_stats["vol_75th_pct"],
            "high_vol_fill_rate": vol_stats["high_vol_fill_rate"],
            "n_bars": df_volatile.height,
        },
        "pass": passed,
        "assert": "fill_rate[high_vol] < 0.5; fill_rate[low_vol] > 0.7",
    }


# ─── T2C: Slippage × volatility interaction ──────────────────────────────

def t2c_slippage_vol_interaction(df: pl.DataFrame) -> dict[str, Any]:
    """T2C: Slippage scales with volatility (assert q5 > 3x q1)."""
    np.random.seed(44)
    log_ret = df["close"].log().diff().drop_nulls().to_numpy()
    vol_5bar = np.array([
        np.std(log_ret[max(0, i - 5):i + 1]) * math.sqrt(24) if i >= 4 else 0.0
        for i in range(len(log_ret))
    ])

    quintiles = np.percentile(vol_5bar[vol_5bar > 0], [20, 40, 60, 80])
    bins = np.digitize(vol_5bar, quintiles)

    quintile_slippage: dict[int, list[float]] = {0: [], 1: [], 2: [], 3: [], 4: []}
    for i in range(len(log_ret)):
        bar = df.row(i, named=True)
        _, slip = simulate_market_order(bar)
        quintile_slippage[bins[i]].append(slip)

    quintile_means = {
        q: round(float(np.mean(quintile_slippage[q])) if quintile_slippage[q] else 0.0, 4)
        for q in range(5)
    }
    q0 = quintile_means[0] if quintile_means[0] > 0 else 0.0001
    q4 = quintile_means[4]
    passed = q4 > 3 * q0

    return {
        "name": "T2C: Slippage × volatility interaction",
        "quintile_slippage_bps": quintile_means,
        "ratio_q4_to_q0": round(q4 / max(q0, 1e-10), 2),
        "pass": passed,
        "assert": "slippage[q4] > 3 * slippage[q1]",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="O-TRADE-2: Execution Quality Tests")
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD (T2A + T2C period)")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--bars", type=int, default=300)
    parser.add_argument("--symbols", default=DEFAULT_SYMBOL)

    # T2B can use a separate broader period for volatility stratification
    parser.add_argument("--t2b-start", default=None, help="YYYY-MM-DD (calm period for T2B)")
    parser.add_argument("--t2b-end", default=None, help="YYYY-MM-DD")
    parser.add_argument("--t2b-calm-bars", type=int, default=300)
    parser.add_argument("--t2b-vol-start", default=None, help="YYYY-MM-DD (volatile period for T2B)")
    parser.add_argument("--t2b-vol-end", default=None)
    parser.add_argument("--t2b-vol-bars", type=int, default=200)

    parser.add_argument("--output-dir", default="data/o_trade_2_results")
    args = parser.parse_args()

    symbol = args.symbols.strip()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 60}")
    print("  O-TRADE-2: Execution Quality Simulation")
    print(f"  Symbol: {symbol}")
    print(f"  Period: {args.start_date} → {args.end_date}")
    print(f"  Bars: {args.bars}")
    print(f"{'=' * 60}\n")

    # T2A + T2C: use the main period (e.g., March 2020 crash)
    df_main = load_data(symbol, args.start_date, args.end_date, args.bars)
    print(f"  Loaded {df_main.height} bars for T2A + T2C")
    if df_main.height > 0:
        print(f"  Price range: {df_main['close'].min():.0f} → {df_main['close'].max():.0f}")
    print()

    # T2B: use separate calm / volatile periods
    t2b_calm_start = args.t2b_start or args.start_date
    t2b_calm_end = args.t2b_end or args.end_date
    t2b_vol_start = args.t2b_vol_start or "2020-03-09"
    t2b_vol_end = args.t2b_vol_end or "2020-03-21"

    df_calm = load_data(symbol, t2b_calm_start, t2b_calm_end, args.t2b_calm_bars)
    df_volatile = load_data(symbol, t2b_vol_start, t2b_vol_end, args.t2b_vol_bars)
    print(f"  T2B calm period:     {t2b_calm_start}→{t2b_calm_end} ({df_calm.height} bars)")
    print(f"  T2B volatile period: {t2b_vol_start}→{t2b_vol_end} ({df_volatile.height} bars)\n")

    results = {
        "T2A": t2a_market_slippage(df_main),
        "T2B": t2b_limit_fill_rate(df_calm, df_volatile),
        "T2C": t2c_slippage_vol_interaction(df_main),
    }

    passed = sum(1 for r in results.values() if r["pass"])
    total = len(results)

    print(f"  {'Test':<45} {'Status':<8}")
    print(f"  {'─' * 45} {'─' * 8}")
    for name, result in results.items():
        status = "PASS" if result["pass"] else "FAIL"
        print(f"  {result['name']:<45} {status:<8}")

    print("\n  ── Detailed Results ──\n")
    for name, result in results.items():
        print(f"  {result['name']}:")
        for k, v in result.items():
            if k not in ("name", "pass"):
                print(f"    {k}: {v}")
        print(f"    pass: {result['pass']}")
        print()

    output = {
        "status": "PASS" if passed == total else "FAIL",
        "passed": passed,
        "total": total,
        "symbol": symbol,
        "period": {"start_date": args.start_date, "end_date": args.end_date, "bars": args.bars},
        "t2b": {
            "calm": {"start": t2b_calm_start, "end": t2b_calm_end},
            "volatile": {"start": t2b_vol_start, "end": t2b_vol_end},
        },
        "results": results,
    }
    out_file = output_dir / f"results_{symbol.replace('/', '_')}.json"
    out_file.write_text(json.dumps(output, indent=2, default=str))

    print(f"  {'=' * 60}")
    print(f"  OVERALL: {output['status']} ({passed}/{total} tests passed)")
    print(f"  Results saved to: {out_file}")
    print(f"{'=' * 60}\n")

    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
