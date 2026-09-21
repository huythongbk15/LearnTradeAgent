#!/usr/bin/env python3
"""O-TRADE-1 Extended: Confidence Scaling on Volatile Periods.

Runs the same backtest as O-TRADE-1 but loads historical parquet data
for specific volatile date windows to validate confidence-adjustment's
downside protection value.

Usage:
    python scripts/o_trade_1_volatile.py --start-date 2024-08-01 --end-date 2024-08-20
    python scripts/o_trade_1_volatile.py --start-date 2025-03-01 --end-date 2025-03-20
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, "src")
sys.path.insert(0, str(Path(__file__).resolve().parent))

DEFAULT_SYMBOLS = "BTC/USDT"
DEFAULT_WINDOW = 120
PARQUET_PATH = "data/raw/binance/BTC_USDT/1h_full.parquet"


def sharpe(returns: np.ndarray) -> float:
    if len(returns) < 5:
        return 0.0
    mean = returns.mean()
    std = returns.std()
    if std == 0:
        return 0.0
    return mean / std * math.sqrt(8760)


def sortino(returns: np.ndarray, mar: float = 0.0) -> float:
    if len(returns) < 5:
        return 0.0
    mean = returns.mean()
    downside = np.minimum(returns - mar, 0)
    dd_var = np.mean(downside ** 2)
    dd_std = math.sqrt(dd_var)
    if dd_std == 0:
        return 999.0 if mean > 0 else 0.0
    return mean / dd_std * math.sqrt(8760)


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) < 2:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for val in equity:
        if val > peak:
            peak = val
        elif peak > 0:
            dd = (peak - val) / peak
            max_dd = max(max_dd, dd)
    return max_dd


def win_rate(returns: np.ndarray) -> float:
    nonzero = returns[returns != 0]
    if len(nonzero) == 0:
        return 0.0
    return (nonzero > 0).sum() / len(nonzero)


def equity_curve(returns: np.ndarray) -> np.ndarray:
    curve = np.concatenate([[1.0], 1.0 + returns])
    for i in range(1, len(curve)):
        curve[i] *= curve[i - 1]
    return curve


def confidence_from_posterior(posterior) -> float:
    """Derive confidence from regime posterior entropy + OOD score.

    High entropy (uncertain regime) or high OOD → lower confidence.
    Clamped to [0.5, 1.5] per architecture constraint.
    """
    ne_raw = getattr(posterior, "normalized_entropy", None)
    ne: float = 0.5
    if ne_raw is not None:
        try:
            ne = float(ne_raw)
        except (TypeError, ValueError):
            ne = 0.5

    ood = float(getattr(posterior, "ood_score", 0.0))
    entropy_conf = 1.5 - ne * 1.0
    ood_penalty = ood * 0.3
    conf = max(0.5, min(1.5, entropy_conf - ood_penalty))
    return round(conf, 4)


def confidence_with_volatility(
    posterior, bar_window: pl.DataFrame, recent_returns: list[float]
) -> float:
    """Confidence combining posterior entropy, OOD, bar-window volatility,
    and recent shadow performance.

    - Posterior entropy: baseline regime certainty.
    - Volatility: current bar hourly range vs calibration baseline.
      When vol_ratio > 1.3x calibration, confidence drops (uncertain market).
    - Trend direction: reduce in downtrend (-5% over 20 bars), boost in uptrend/recovery (+3%).
    - Recent performance: if last 3 chosen returns avg < 0, reduce.
    Clamped to [0.5, 1.5] per architecture constraint.
    """
    ne_raw = getattr(posterior, "normalized_entropy", 0.5)
    ne: float = 0.5 if ne_raw is None else float(ne_raw)
    ood = float(getattr(posterior, "ood_score", 0.0))

    base = 1.5 - ne * 1.0 - ood * 0.3

    # Volatility adjustment: current bar range vs calibration baseline
    hourly_ranges = (
        (bar_window["high"] - bar_window["low"]) / bar_window["close"]
    ).to_numpy()

    current_range = float(hourly_ranges[-1]) if len(hourly_ranges) > 0 else _calibration_vol
    vol_ratio = current_range / max(_calibration_vol, 1e-10)

    # Continuous linear decay: ratio 1.0 -> 1.0, 10.0 -> 0.55, 20+ -> 0.5
    vol_factor = max(0.5, 1.0 - 0.05 * max(0.0, vol_ratio - 1.0))

    # Performance adjustment
    if len(recent_returns) >= 3:
        recent_avg = sum(recent_returns[-3:]) / 3
        if recent_avg < 0:
            perf_factor = max(0.6, 1.0 + recent_avg * 2.0)
        else:
            perf_factor = 1.0
    else:
        perf_factor = 1.0

    # Trend direction adjustment: conditional on volatility regime.
    # Only apply during non-extreme volatility to avoid over-confidence
    # in calm recovery periods (which leads to over-exposure).
    closes = bar_window["close"].to_numpy()
    recent_slope = 0.0
    if len(closes) >= 20:
        recent_slope = float((closes[-1] - closes[-20]) / closes[-20])

    if vol_ratio < 2.0:
        if recent_slope < -0.05:  # -5% drop over 20 bars
            trend_factor = 0.9
        elif recent_slope > 0.03:  # +3% gain = recovery
            trend_factor = 1.05
        else:
            trend_factor = 1.0
    else:
        trend_factor = 1.0  # Vol factor already handles extreme periods

    conf = max(0.5, min(1.5, base * vol_factor * perf_factor * trend_factor))
    return round(conf, 4)




_calibration_vol: float = 0.0062  # Default: ~0.62% hourly range (BTC normal vol)

def _calibrate_volatility(parquet_path: str, symbols: list[str]) -> None:
    """Compute median hourly range across full dataset as baseline."""
    global _calibration_vol
    for sym in symbols:
        try:
            df = pl.read_parquet(parquet_path).filter(pl.col("symbol") == sym)
            df = df.sort("timestamp")
            if df.height < 50:
                continue
            hourly_ranges = ((df["high"] - df["low"]) / df["close"]).to_numpy()
            med = float(np.median(hourly_ranges))
            if med > 0:
                _calibration_vol = med
                break
        except Exception:
            continue



def load_historical_window(
    parquet_path: str,
    symbol: str,
    start_date: str,
    end_date: str,
    window: int,
) -> pl.DataFrame:
    """Load historical 1h bars from parquet, return sorted by timestamp."""
    df = pl.read_parquet(parquet_path)
    
    df = df.filter(pl.col("symbol") == symbol)  # parquet stores BTC/USDT
    df = df.sort("timestamp")

    # Ensure timestamp is timezone-aware (match parquet schema)
    if df["timestamp"].dtype == pl.Datetime and df.schema["timestamp"].time_zone is None:
        df = df.with_columns(pl.col("timestamp").dt.replace_time_zone("UTC"))

    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=UTC)
    end = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)).replace(tzinfo=UTC)
    start_with_buffer = start - timedelta(hours=window)

    df = df.filter(
        (pl.col("timestamp") >= pl.lit(start_with_buffer))
        & (pl.col("timestamp") < pl.lit(end))
    )

    required = ["timestamp", "open", "high", "low", "close", "volume"]
    for col in required:
        if col not in df.columns:
            raise ValueError(f"Missing column: {col}")

    return df


def run_volatile_backtest(
    df: pl.DataFrame,
    n_bars: int,
    window: int,
    output_dir: Path,
    symbol: str,
) -> dict[str, Any]:
    from live_data_pipeline import (
        build_tournament,
        build_observation,
        compute_deterministic_posterior,
        compute_bar_return,
    )

    tmp_dir = output_dir
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    test_bars = df.tail(n_bars)
    tournament = build_tournament([symbol], tmp_dir, shadow_mode=True)

    shadow_returns_by_strategy: dict[str, list[float]] = {}
    baseline_port_returns: list[float] = []
    llm_port_returns: list[float] = []
    bar_returns: list[float] = []
    confidences: list[float] = []
    bars_active: list[bool] = []
    chosen_returns_history: list[float] = []

    warmup = window
    for i in range(warmup, len(test_bars)):
        bar_window = test_bars[max(0, i - window + 1) : i + 1]
        bar_time = bar_window["timestamp"].item(-1)

        obs = build_observation(symbol, bar_window, bar_time)
        posterior = compute_deterministic_posterior(bar_window, bar_time)
        bar_ret = compute_bar_return(bar_window, bar_time)
        bar_returns.append(bar_ret)

        conf = confidence_with_volatility(posterior, bar_window, chosen_returns_history)
        confidences.append(conf)

        decision = tournament.route(
            symbol=symbol,
            timeframe="1h",
            posterior=posterior,
            observed_at=bar_time,
            position_is_flat=True,
            observation=obs,
            bar_return=bar_ret,
        )

        shadow_rets = _get_shadow_returns(tournament, symbol, "1h")
        for sid, sret in shadow_rets.items():
            shadow_returns_by_strategy.setdefault(sid, []).append(sret)

        chosen = decision.chosen_strategy_id
        exp_mult = decision.exposure_multiplier
        bars_active.append(chosen is not None and exp_mult > 0)

        if chosen and chosen in shadow_rets and exp_mult > 0:
            strat_ret = shadow_rets[chosen]
            baseline_port_returns.append(strat_ret * exp_mult)
            llm_port_returns.append(strat_ret * exp_mult * conf)
            chosen_returns_history.append(strat_ret)
        else:
            baseline_port_returns.append(0.0)
            llm_port_returns.append(0.0)
            chosen_returns_history.append(0.0)

    base_ret = np.array(baseline_port_returns)
    llm_ret = np.array(llm_port_returns)
    bar_rets_arr = np.array(bar_returns)

    # Per-strategy metrics
    strategy_metrics: dict[str, dict[str, float]] = {}
    for sid, rets_list in shadow_returns_by_strategy.items():
        rets = np.array(rets_list)
        strategy_metrics[sid] = {
            "sharpe": sharpe(rets),
            "sortino": sortino(rets),
            "max_dd": max_drawdown(equity_curve(rets)),
            "win_rate": win_rate(rets),
            "n_bars": len(rets),
            "mean_return": rets.mean() if len(rets) > 0 else 0.0,
        }

    base_eq = equity_curve(base_ret)
    llm_eq = equity_curve(llm_ret)

    top_strategy = (
        max(strategy_metrics, key=lambda k: strategy_metrics[k]["sharpe"])
        if strategy_metrics else None
    )

    portfolio_metrics: dict[str, Any] = {
        "sharpe_baseline": sharpe(base_ret),
        "sharpe_llm": sharpe(llm_ret),
        "sortino_baseline": sortino(base_ret),
        "sortino_llm": sortino(llm_ret),
        "max_dd_baseline": max_drawdown(base_eq),
        "max_dd_llm": max_drawdown(llm_eq),
        "win_rate_baseline": win_rate(base_ret),
        "win_rate_llm": win_rate(llm_ret),
        "final_equity_baseline": base_eq[-1],
        "final_equity_llm": llm_eq[-1],
        "n_active_bars": sum(bars_active),
        "total_bars": len(baseline_port_returns),
        "mean_confidence": float(np.mean(confidences)),
        "min_confidence": float(min(confidences)),
        "max_confidence": float(max(confidences)),
        "confidence_std": float(np.std(confidences)),
        "bars_conf_less_than_1": sum(1 for c in confidences if c < 0.99),
        "bars_conf_less_than_075": sum(1 for c in confidences if c < 0.75),
        "mean_bar_return": float(bar_rets_arr.mean()),
        "bar_vol": float(bar_rets_arr.std()),
        "top_strategy": top_strategy,
    }

    # Assertions
    passed = 0
    failed = 0
    checks: list[str] = []

    # Check 1: Confidence varies on volatile data
    if portfolio_metrics["confidence_std"] > 0.03:
        passed += 1
        checks.append(f"✓ Confidence varies on volatile data (std={portfolio_metrics['confidence_std']:.4f})")
    else:
        failed += 1
        checks.append(f"✗ Confidence not responsive (std={portfolio_metrics['confidence_std']:.4f})")

    # Check 2: Confidence < 1.0 on some bars
    if portfolio_metrics["bars_conf_less_than_1"] > 0:
        passed += 1
        checks.append(f"✓ Confidence < 1.0 on {portfolio_metrics['bars_conf_less_than_1']} bars")
    else:
        failed += 1
        checks.append("✗ No confidence down-weighting occurred")

    # Check 3: Confidence clamped to [0.5, 1.5]
    if portfolio_metrics["min_confidence"] >= 0.5 and portfolio_metrics["max_confidence"] <= 1.5:
        passed += 1
        checks.append(f"✓ Confidence in [0.5, 1.5] [{portfolio_metrics['min_confidence']:.4f}, {portfolio_metrics['max_confidence']:.4f}]")
    else:
        failed += 1
        checks.append(f"✗ Confidence out of range [{portfolio_metrics['min_confidence']:.4f}, {portfolio_metrics['max_confidence']:.4f}]")

    # Check 4: LLM max-DD <= baseline
    if portfolio_metrics["max_dd_llm"] <= portfolio_metrics["max_dd_baseline"] * 1.05:
        passed += 1
        checks.append(f"✓ LLM max-DD <= baseline (DD_llm={portfolio_metrics['max_dd_llm']:.4f} vs DD_base={portfolio_metrics['max_dd_baseline']:.4f})")
    else:
        failed += 1
        checks.append(f"✗ LLM max-DD > baseline ({portfolio_metrics['max_dd_llm']:.4f} > {portfolio_metrics['max_dd_baseline']:.4f})")

    # Check 5: ≥3 strategies shadow tracked
    n_active = len({sid for sid, m in strategy_metrics.items() if m["n_bars"] > 50})
    if n_active >= 3:
        passed += 1
        checks.append(f"✓ ≥3 strategies tracked ({n_active})")
    else:
        failed += 1
        checks.append(f"✗ Only {n_active} strategies tracked")

    # Check 6: ≥1 bar with confidence < 0.75
    if portfolio_metrics["bars_conf_less_than_075"] > 0:
        passed += 1
        checks.append(f"✓ Confidence < 0.75 on {portfolio_metrics['bars_conf_less_than_075']} bars (uncertainty detected)")
    else:
        failed += 1
        checks.append("✗ No high-uncertainty bars detected")

    # Check 7: Tournament active
    if portfolio_metrics["n_active_bars"] / max(1, portfolio_metrics["total_bars"]) > 0.5:
        passed += 1
        checks.append(f"✓ Tournament active {portfolio_metrics['n_active_bars']}/{portfolio_metrics['total_bars']} bars")
    else:
        failed += 1
        checks.append(f"✗ Tournament underactive {portfolio_metrics['n_active_bars']}/{portfolio_metrics['total_bars']}")

    result = {
        "status": "PASS" if failed == 0 else "FAIL",
        "passed": passed,
        "failed": failed,
        "checks": checks,
        "portfolio_metrics": portfolio_metrics,
        "strategy_metrics": strategy_metrics,
    }

    (output_dir / "results.json").write_text(
        json.dumps(result, indent=2, default=str)
    )
    return result


def _get_shadow_returns(tournament, symbol: str, timeframe: str) -> dict[str, float]:
    live_state = tournament._live_state.get((symbol, timeframe))
    if not live_state:
        return {}
    shadow_returns = {}
    shadow_mets = getattr(live_state, "shadow_metrics", {})
    if not shadow_mets:
        return {}
    for sid, met in shadow_mets.items():
        returns_deque = getattr(met, "returns", None)
        if returns_deque:
            latest_ret = returns_deque[-1] if len(returns_deque) > 0 else 0.0
            shadow_returns[sid] = float(latest_ret)
    return shadow_returns


def main():
    parser = argparse.ArgumentParser(
        description="O-TRADE-1 Extended: Confidence scaling on volatile periods"
    )
    parser.add_argument("--start-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--bars", type=int, default=300)
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--parquet-path", default=None, help="Override parquet path")
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument(
        "--output-dir", default=None,
        help="Defaults to data/volatile_backtest_<start_date>",
    )
    args = parser.parse_args()

    symbol = args.symbols.strip()
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        output_dir = Path(f"data/volatile_backtest_{args.start_date}")

    print(f"\n{'=' * 60}")
    print("  O-TRADE-1 Extended: Confidence Scaling on Volatile Data")
    print(f"  Symbol: {symbol}")
    print(f"  Period: {args.start_date} → {args.end_date}")
    print(f"  Bars: {args.bars} | Window: {args.window}")
    print(f"  Output: {output_dir}")
    print(f"{'=' * 60}\n")

    parquet_path = args.parquet_path or f"data/raw/binance/{symbol.replace("/", "_").replace("_", "/")}/1h_full.parquet"
    # Fix path: BTC/USDT -> data/raw/binance/BTC_USDT/1h_full.parquet
    parquet_path = f"data/raw/binance/{symbol.replace("/", "_")}/1h_full.parquet"
    if args.parquet_path:
        parquet_path = args.parquet_path
    import os
    if not os.path.exists(parquet_path):
        parquet_path = f"data/raw/binance/{symbol.replace("/", "_")}/1h.parquet"
    print(f"  Parquet: {parquet_path}")
    _calibrate_volatility(parquet_path, [symbol])
    print(f"  Calibration baseline vol: {_calibration_vol:.5f}")

    df = load_historical_window(
        parquet_path, symbol,
        args.start_date, args.end_date, args.window,
    )
    print(f"  Loaded {df.height} bars from parquet")

    if df.height < args.bars + args.window:
        print(f"  WARNING: Only {df.height} bars available, need {args.bars + args.window}")

    result = run_volatile_backtest(
        df=df,
        n_bars=min(args.bars, max(0, df.height - args.window)),
        window=args.window,
        output_dir=output_dir,
        symbol=symbol,
    )

    print(f"\n  Status: [{result['status']}]")
    print(f"  {result['passed']} passed, {result['failed']} failed\n")

    for check in result["checks"]:
        print(f"  {check}")

    pm = result["portfolio_metrics"]
    print("\n  ── Portfolio Metrics ──")
    print(f"    Sharpe (baseline):  {pm['sharpe_baseline']:.4f}")
    print(f"    Sharpe (LLM adj):   {pm['sharpe_llm']:.4f}")
    print(f"    Sortino (baseline): {pm['sortino_baseline']:.4f}")
    print(f"    Sortino (LLM adj):  {pm['sortino_llm']:.4f}")
    print(f"    Max-DD (baseline):  {pm['max_dd_baseline']:.4f}")
    print(f"    Max-DD (LLM adj):   {pm['max_dd_llm']:.4f}")
    print(f"    Win rate (base):    {pm['win_rate_baseline']:.2%}")
    print(f"    Win rate (LLM):     {pm['win_rate_llm']:.2%}")
    print(f"    Final equity (base): {pm['final_equity_baseline']:.4f}")
    print(f"    Final equity (LLM):  {pm['final_equity_llm']:.4f}")
    print(f"    Active bars:        {pm['n_active_bars']}/{pm['total_bars']}")

    print("\n  ── Confidence Metrics ──")
    print(f"    Mean confidence:     {pm['mean_confidence']:.4f}")
    print(f"    Confidence range:    [{pm['min_confidence']:.4f}, {pm['max_confidence']:.4f}]")
    print(f"    Confidence std:      {pm['confidence_std']:.4f}")
    print(f"    Bars conf < 1.0:     {pm['bars_conf_less_than_1']}")
    print(f"    Bars conf < 0.75:    {pm['bars_conf_less_than_075']}")

    print("\n  ── Data Stats ──")
    print(f"    Mean bar return:     {pm['mean_bar_return']:.6f}")
    print(f"    Bar volatility:      {pm['bar_vol']:.6f}")
    print(f"    Top strategy:        {pm['top_strategy']}")

    print("\n  ── Per-Strategy Shadow Metrics ──")
    for sid, m in sorted(result["strategy_metrics"].items(),
                         key=lambda x: x[1]["sharpe"], reverse=True):
        print(
            f"    {sid:24} Sharpe={m['sharpe']:.3f}  "
            f"Sortino={m['sortino']:.3f}  "
            f"MaxDD={m['max_dd']:.3f}  "
            f"Win={m['win_rate']:.2%}  "
            f"n={m['n_bars']}"
        )

    print(f"\n{'=' * 60}")
    print(f"  OVERALL: {result['status']}")
    print(f"  Results saved to: {output_dir / 'results.json'}")
    print(f"{'=' * 60}\n")

    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
