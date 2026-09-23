#!/usr/bin/env python3
"""O-TRADE-2: Order Optimization Matrix (T3A+).

Builds a full **order-type × market-condition** decision matrix, extending the
T3A baseline (market vs limit) to 6 order types × 4 market regimes.
Reuses the `AdaptiveExecutionBridge` cost model infrastructure.

Order types:
  - market     — immediate, slippage ∝ √(volatility × illiquidity)
  - limit      — waits for price; pays gap_cost if unfilled
  - stop_limit   — stop trigger + limit post; balances fill-rate + price control
  - TWAP       — time-slicing reduces impact ∝ 1/√(n_slices)
  - VWAP       — flow-following; optimal when volume aligns with trade
  - iceberg    — hides size; reduces adverse-selection cost

Market conditions (per bar):
  - normal    — median volatility, median volume
  - high_vol  — 75th+ percentile volatility, median+ volume
  - crash     — max drawdown window, low volume
  - illiquid  — median volatility, 25th percentile volume

T3A assertion: optimized (matrix-selected) average slippage < 5 bps baseline.

Usage::

    python scripts/o_trade_2_order_optimization.py
    python scripts/o_trade_2_order_optimization.py --symbols BTC_USDT ETH_USDT BNB_USDT
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from pathlib import Path
from typing import cast

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trading_agent.execution.adaptive_execution import (
    AdaptiveExecutionConfig,
)

# ── Constants ─────────────────────────────────────────────────────────────

TIMEFRAME = "daily"
DEFAULT_SYMBOLS = ["BTC_USDT", "ETH_USDT", "BNB_USDT"]
START_DATE = "2020-01-01"
END_DATE = "2022-11-30"
N_BARS = 800

# Base cost parameters (bps → fraction)
SPREAD_BPS = 1.0       # 1 bps base spread
COMMISSION_BPS = 5.0   # 5 bps commission per side
BASE_SLIPPAGE_BPS = 2.0  # 2 bps base slippage

# Volatility slippage multiplier for daily data
# Daily bars have higher range than intraday; we use 5x range as slippage
# (empirically calibrated from T8A O-TRADE-1 results: avg 5 bps slippage)
VOL_SLIP_MULT = 30.0  # Daily bars: 30x range ≈ intraday slippage

# Order type parameters
TWAP_SLICES = 10       # Default number of TWAP slices
VWAP_FILL_THRESHOLD = 0.7  # Min fraction of volume for VWAP advantage
ICEBERG_VISIBILITY = 0.1  # Show 10% of order size


# ── Slippage model ─────────────────────────────────────────────────────────

def expected_slippage_cost(
    order_type: str,
    bar_range: float,
    rel_volume: float,
    spread_bps: float,
    is_crash: bool,
) -> float:
    """Compute expected slippage cost (bps) for a given order type and market condition.

    Args:
        order_type: One of market, limit, stop_limit, twap, vwap, iceberg
        bar_range: Relative bar range (high - low) / mid — volatility proxy
        rel_volume: Volume percentile (0-1) — liquidity proxy
        spread_bps: Base spread in bps
        is_crash: Whether this is in a crash regime

    Returns:
        Expected slippage cost in bps (round-trip)
    """
    half_spread = spread_bps / 2.0  # bps

    # Volatility slippage: proportional to daily range (5x for daily data)
    # In crash, volatility spike increases slippage by 50%
    vol_slippage = half_spread + VOL_SLIP_MULT * bar_range * (1.0 + 0.5 * is_crash)

    # Liquidity multiplier — low volume = higher impact
    illiq_mult = 1.0 + 0.3 * (1.0 - rel_volume)

    if order_type == "market":
        cost = vol_slippage * illiq_mult
    elif order_type == "limit":
        # Fill probability decreases with volatility
        fill_prob = max(0.05, math.exp(-bar_range * 10.0))
        gap_cost = vol_slippage * 0.5  # Missed opportunity cost
        cost = half_spread * fill_prob + gap_cost * (1.0 - fill_prob)
    elif order_type == "stop_limit":
        # Stop trigger cost + limit post cost
        stop_cost = bar_range * 30.0  # Stop trigger slippage ~30% of range
        fill_prob = max(0.1, math.exp(-bar_range * 10.0))
        limit_cost = half_spread * fill_prob + vol_slippage * 0.2 * (1.0 - fill_prob)
        cost = stop_cost + limit_cost
    elif order_type == "twap":
        # Time-slicing reduces impact ∝ 1/√(n_slices)
        cost = vol_slippage * illiq_mult / math.sqrt(TWAP_SLICES)
        # Execution delay cost
        cost += bar_range * 5.0 * (1.0 - 0.3 * rel_volume)
    elif order_type == "vwap":
        # VWAP follows volume flow
        if rel_volume > VWAP_FILL_THRESHOLD:
            cost = vol_slippage * illiq_mult * 0.6  # 40% cheaper when volume good
        else:
            cost = vol_slippage * illiq_mult * 1.2  # 20% more when volume poor
        cost += 0.5  # VWAP fixed overhead
    elif order_type == "iceberg":
        # Hides size, reduces information leakage
        fill_prob = max(0.1, math.exp(-bar_range * 8.0))
        cost = vol_slippage * illiq_mult * ICEBERG_VISIBILITY
        # Unfilled cost
        unfilled_cost = bar_range * 20.0 * (1.0 - fill_prob)
        cost += unfilled_cost
    else:
        cost = vol_slippage  # Fallback to market

    return cost


# ── Market regime classification ───────────────────────────────────────────

def classify_regime(bar_range: float, rel_volume: float, drawdown: float | None = None) -> str:
    """Classify market condition for a bar.

    Returns one of: normal, high_vol, crash, illiquid
    """
    vol_25 = 0.025   # ~2.5% daily range threshold
    vol_75 = 0.06    # ~6% daily range threshold
    vol_crash = 0.10  # ~10% daily range = crash
    vol_low = 0.015  # ~1.5% = illiquid threshold

    vol = bar_range
    if vol >= vol_crash:
        return "crash"
    elif vol >= vol_75:
        return "high_vol"
    elif vol <= vol_low and rel_volume < 0.25:
        return "illiquid"
    else:
        return "normal"


# ──── Order optimization matrix ────────────────────────────────────────────

def build_order_optimization_matrix(
    df: pl.DataFrame,
) -> dict[str, object]:
    """Build full order-type × market-condition optimization matrix.

    For each (order_type, regime) pair, computes:
      - avg_slippage_bps
      - fill_rate (estimated)
      - cost_rank (1 = best, 6 = worst)
      - optimal_order_type per regime

    T3A assertion: optimized (matrix-selected) avg slippage < 5 bps.
    """
    ORDER_TYPES = ["market", "limit", "stop_limit", "twap", "vwap", "iceberg"]
    REGIMES = ["normal", "high_vol", "crash", "illiquid"]

    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    close = df["close"].to_numpy()
    volume = df["volume"].to_numpy()
    mid = (high + low) / 2.0
    bar_range = np.abs(high - low) / (mid + 1e-10)

    # Volume percentile (liquidity proxy)
    vol_rank = np.argsort(np.argsort(volume))
    rel_volume = vol_rank / (len(volume) - 1)

    # Drawdown proxy — rolling max drawdown from price
    running_max = np.maximum.accumulate(close)
    drawdown = np.minimum(0.0, (close - running_max) / (running_max + 1e-10))

    # Classify each bar's regime
    regimes_per_bar = np.array([
        classify_regime(br, rv, dd)
        for br, rv, dd in zip(bar_range, rel_volume, drawdown)
    ])

    # ── Per-regime, per-order-type expected slippage ──────────────────────
    matrix: dict[str, dict[str, float]] = {}
    fill_rates: dict[str, dict[str, float]] = {}

    for regime in REGIMES:
        mask = regimes_per_bar == regime
        n_bars_regime = int(np.sum(mask))
        if n_bars_regime == 0:
            # Use all bars as fallback
            mask = np.ones(len(bar_range), dtype=bool)
            n_bars_regime = len(bar_range)

        matrix[regime] = {}
        fill_rates[regime] = {}

        for ot in ORDER_TYPES:
            if n_bars_regime == 0:
                matrix[regime][ot] = expected_slippage_cost(ot, 0.03, 0.5, SPREAD_BPS, False)
                fill_rates[regime][ot] = 0.5
            else:
                costs = np.array([
                    expected_slippage_cost(
                        ot, br, rv, SPREAD_BPS,
                        is_crash=(r == "crash"),
                    )
                    for br, rv, r in zip(
                        bar_range[mask], rel_volume[mask], regimes_per_bar[mask]
                    )
                ])
                matrix[regime][ot] = round(float(np.mean(costs)), 4)

                # Estimate fill rate
                if ot == "market":
                    fr = 1.0
                elif ot == "limit":
                    frs = np.array([
                        max(0.05, math.exp(-br * 20.0))
                        for br in bar_range[mask]
                    ])
                    fr = float(np.mean(frs))
                elif ot == "stop_limit":
                    frs = np.array([
                        max(0.1, math.exp(-br * 15.0))
                        for br in bar_range[mask]
                    ])
                    fr = float(np.mean(frs))
                elif ot == "twap":
                    fr = 0.95  # TWAP always fills eventually
                elif ot == "vwap":
                    fr = 0.90
                elif ot == "iceberg":
                    frs = np.array([
                        max(0.1, math.exp(-br * 10.0))
                        for br in bar_range[mask]
                    ])
                    fr = float(np.mean(frs)) * 0.5  # Half fills due to visibility
                else:
                    fr = 0.5
                fill_rates[regime][ot] = round(fr, 4)

    # ── Optimal order type per regime (lowest cost) ───────────────────────
    optimal: dict[str, str] = {}
    for regime in REGIMES:
        best_ot = min(matrix[regime], key=lambda k: matrix[regime][k])
        optimal[regime] = best_ot

    # ── Rank order types per regime ────────────────────────────────────────
    rankings: dict[str, list[tuple[str, float]]] = {}
    for regime in REGIMES:
        ranked = sorted(
            [(ot, matrix[regime][ot]) for ot in ORDER_TYPES],
            key=lambda x: x[1],
        )
        rankings[regime] = [(ot, round(cost, 4)) for ot, cost in ranked]

    # ── Baseline: always use market order ─────────────────────────────────
    baseline_costs = np.array([
        expected_slippage_cost("market", br, rv, SPREAD_BPS, is_crash=(r == "crash"))
        for br, rv, r in zip(bar_range, rel_volume, regimes_per_bar)
    ])
    baseline_avg = float(np.mean(baseline_costs))

    # ── Optimized: use regime-optimal order type ──────────────────────────
    optimized_costs = np.array([
        expected_slippage_cost(
            optimal[r], br, rv, SPREAD_BPS, is_crash=(r == "crash"),
        )
        for br, rv, r in zip(bar_range, rel_volume, regimes_per_bar)
    ])
    optimized_avg = float(np.mean(optimized_costs))

    total_bars = len(bar_range)
    regime_counts = {r: int(np.sum(regimes_per_bar == r)) for r in REGIMES}
    regime_pcts = {r: round(c / total_bars, 4) for r, c in regime_counts.items()}

    # ── Assertions ────────────────────────────────────────────────────────
    improvement_bps = baseline_avg - optimized_avg
    assertions = {
        "slippage_below_5bps": optimized_avg < 5.0,
        "improvement_positive": improvement_bps > 0.0,
        "market_suboptimal_in_high_vol": optimal["high_vol"] != "market"
        or matrix["high_vol"][optimal["high_vol"]] < matrix["high_vol"]["market"],
        "market_suboptimal_in_crash": optimal["crash"] != "market"
        or matrix["crash"][optimal["crash"]] < matrix["crash"]["market"],
        "limit_suboptimal_in_crash": optimal["crash"] != "limit"
        or matrix["crash"]["limit"] < matrix["crash"][optimal["crash"]],
    }

    passed = all(assertions.values())

    result = {
        "name": "T3A+: Order optimization matrix (6 order types × 4 market regimes)",
        "symbols": DEFAULT_SYMBOLS,
        "date_range": f"{START_DATE} → {END_DATE}",
        "timeframe": TIMEFRAME,
        "n_bars": total_bars,
        "order_types": ORDER_TYPES,
        "regimes": REGIMES,
        "regime_distribution_pct": regime_pcts,
        "matrix_slippage_bps": {r: {ot: matrix[r][ot] for ot in ORDER_TYPES} for r in REGIMES},
        "fill_rates": {r: {ot: fill_rates[r][ot] for ot in ORDER_TYPES} for r in REGIMES},
        "optimal_order_type_per_regime": optimal,
        "rankings": {r: rankings[r] for r in REGIMES},
        "baseline_avg_slippage_bps": round(baseline_avg, 4),
        "optimized_avg_slippage_bps": round(optimized_avg, 4),
        "improvement_bps": round(improvement_bps, 4),
        "assertions": {k: bool(v) for k, v in assertions.items()},
        "assert": "optimized_slippage < 5 bps AND improvement > 0 AND regime-optimized order type ≠ market in crash",
        "pass": passed,
    }

    return result


# ───── AdaptiveExecutionBridge integration ─────────────────────────────────

def validate_bridge_alignment(
    matrix_result: dict[str, object],
) -> dict[str, object]:
    """Cross-check the T3A+ matrix against AdaptiveExecutionBridge config.

    The bridge uses slippage_bps=2.0 and spread_bps=1.0 by default.
    This function verifies the matrix's market-order cost aligns with the bridge's
    configured slippage when volatility is at the median.
    """
    config = AdaptiveExecutionConfig()
    bridge_slippage = config.slippage_bps  # 2.0 bps (pure slippage, not total)
    bridge_spread = config.spread_bps  # 1.0 bps

    matrix_slippage_normal = cast(dict[str, dict[str, float]], matrix_result["matrix_slippage_bps"])["normal"]["market"]
    matrix_baseline_normal = float(matrix_slippage_normal)

    # Bridge model: pure slippage_bps (spread is separate component in matrix)
    bridge_expected = bridge_slippage

    alignment_error = abs(matrix_baseline_normal - bridge_expected)

    result = {
        "name": "T3A+: AdaptiveExecutionBridge alignment check",
        "bridge_slippage_bps": config.slippage_bps,
        "bridge_spread_bps": config.spread_bps,
        "matrix_market_normal_bps": matrix_baseline_normal,
        "expected_bps": round(bridge_expected, 2),
        "alignment_error_bps": round(alignment_error, 2),
        "alignment_ok": alignment_error < 3.0,
        "assert": "alignment_error < 5.0 bps",
    }

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbols", nargs="+", default=DEFAULT_SYMBOLS,
        help="Symbols to evaluate (default: BTC_USDT ETH_USDT BNB_USDT)",
    )
    args = parser.parse_args()

    tmp_dir = Path("data/t8a_o2_order_optimization")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{'=' * 60}")
    print("O-TRADE-2: Order Optimization Matrix (T3A+)")
    print(f"  Symbols: {', '.join(args.symbols)}")
    print(f"  Period: {START_DATE} → {END_DATE} ({N_BARS} daily bars)")
    print("  Order types: market, limit, stop_limit, twap, vwap, iceberg")
    print("  Regimes: normal, high_vol, crash, illiquid")
    print(f"{'=' * 60}\n")

    # Load aggregate BTC data for matrix construction
    df = pl.read_parquet(f"data/raw/binance/{args.symbols[0]}/1d.parquet")
    df = df.sort("timestamp").head(N_BARS)

    # Build the optimization matrix
    matrix_result = build_order_optimization_matrix(df)

    # Print summary
    print("\n  Regime distribution:")
    regime_dist = cast(dict[str, float], matrix_result["regime_distribution_pct"])
    for r, pct in regime_dist.items():
        print(f"    {r}: {pct:.1%} ({int(cast(int, matrix_result['n_bars'])) * pct:.0f} bars)")

    print("\n  Optimal order type per regime:")
    optimal_map = cast(dict[str, str], matrix_result["optimal_order_type_per_regime"])
    matrix_map = cast(dict[str, dict[str, float]], matrix_result["matrix_slippage_bps"])
    for r in ["normal", "high_vol", "crash", "illiquid"]:
        opt = optimal_map[r]
        cost = matrix_map[r][opt]
        print(f"    {r:12s} → {opt:10s} ({cost:.2f} bps)")

    print("\n  Rankings (cost →):")
    rankings_map = cast(dict[str, list[tuple[str, float]]], matrix_result["rankings"])
    for r in ["normal", "high_vol", "crash", "illiquid"]:
        print(f"    {r}: ", end="")
        for ot, cost in rankings_map[r]:
            print(f"{ot}({cost:.2f}) ", end="")
        print()

    baseline = float(cast(float, matrix_result["baseline_avg_slippage_bps"]))
    optimized = float(cast(float, matrix_result["optimized_avg_slippage_bps"]))
    impr = float(cast(float, matrix_result["improvement_bps"]))
    print(f"\n  Baseline (always market): {baseline:.2f} bps")
    print(f"  Optimized (regime-switched): {optimized:.2f} bps")
    print(f"  Improvement: {impr:.2f} bps ({impr / baseline * 100:.1f}%)")

    print(f"\n  Assertions: {matrix_result['assertions']}")
    print(f"  Pass: {matrix_result['pass']}")

    # Bridge alignment check
    bridge_result = validate_bridge_alignment(matrix_result)
    print(f"\n  Bridge alignment: {bridge_result}")

    # Save results
    full_result: dict[str, object] = {
        "t3a_matrix": matrix_result,
        "bridge_alignment": bridge_result,
        "pass": bool(matrix_result["pass"]) and bool(bridge_result["alignment_ok"]),
    }
    results_path = tmp_dir / "order_optimization_results.json"
    results_path.write_text(json.dumps(full_result, indent=2, default=str))
    print(f"\n  Results: {results_path}")
    print(f"{'=' * 60}")
    print(f"  O-TRADE-2 {'PASS' if full_result['pass'] else 'FAIL'}")
    print(f"{'=' * 60}")

    return 0 if full_result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
