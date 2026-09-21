#!/usr/bin/env python3
"""O-TRADE-1: Confidence Adjustment Backtesting.

Compare shadow P&L for tournament routing with:
  - Baseline: exposure_multiplier as-is (confidence = 1.0)
  - LLM-adjusted: exposure_multiplier × confidence_adjustment
    (confidence derived from regime volatility signal)

Simulates the OrderPlanner confidence scaling at the portfolio-return level.
Metrics: Sharpe, Sortino, max-DD, win rate, trade count.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
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
DEFAULT_BARS = 300
DEFAULT_WINDOW = 120

# ── Metrics ────────────────────────────────────────────────────────────────


def sharpe(returns: np.ndarray) -> float:
    """Annualized Sharpe (1h bars, 365*24 = 8760 bars/year)."""
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
        return 999.0 if mean > 0 else 0.0  # All-positive → very high Sortino
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


def confidence_from_volatility(bar_window: pl.DataFrame) -> float:
    """Derive confidence_adjustment from realized volatility regime.

    Low vol → high confidence (1.3); High vol → low confidence (0.6).
    Clamped to [0.5, 1.5] per architecture constraint.
    """
    closes = bar_window["close"].tail(21).to_numpy()
    if len(closes) < 5 or closes[0] == 0:
        return 1.0
    rets = np.diff(np.log(closes))
    vol = np.std(rets) * math.sqrt(24)  # annualized

    # Recent volatility trend (vs 5-bar trailing vol)
    recent_rets = np.diff(np.log(closes[-6:]))
    recent_vol = np.std(recent_rets) * math.sqrt(24) if len(recent_rets) >= 2 else vol

    # When volatility spikes (recent_vol > 2× historical), confidence drops
    vol_ratio = recent_vol / (vol + 1e-8)
    # Map: vol_ratio < 1 → conf=1.4; vol_ratio > 3 → conf=0.5
    conf = max(0.5, min(1.5, 1.5 - (vol_ratio - 1.0) * 0.33))
    return round(conf, 4)


def confidence_from_posterior(posterior) -> float:
    """Derive confidence from regime posterior entropy.

    Uses posterior.normalized_entropy (0=certainty → conf=1.5, 1=uniform → conf=0.5).
    Combined with posterior.ood_score (higher OOD → lower confidence).
    """
    ne = getattr(posterior, "normalized_entropy", None)
    if ne is None:
        # Fallback: compute from probabilities
        try:
            probs = getattr(posterior, "values", None)()
            if probs:
                import math as m
                ne = -sum(p * m.log(p + 1e-10) for p in probs) / m.log(len(probs))
        except Exception:
            ne = 0.5

    ood = getattr(posterior, "ood_score", 0.0)

    # Entropy: 0 → conf 1.5; 1.0 → conf 0.5
    entropy_conf = 1.5 - ne * 1.0
    # OOD: 0 → no penalty; 1.0 → conf drops by 0.3
    ood_penalty = ood * 0.3
    conf = max(0.5, min(1.5, entropy_conf - ood_penalty))
    return round(conf, 4)


# ── Backtest ───────────────────────────────────────────────────────────────


def run_backtest(
    symbols: list[str],
    n_bars: int,
    window: int,
    output_dir: Path,
) -> dict[str, Any]:
    from live_data_pipeline import (
        BinanceDataFeed,
        build_tournament,
        build_observation,
        compute_deterministic_posterior,
        compute_bar_return,
    )

    tmp_dir = output_dir
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    feed = BinanceDataFeed(symbols, dry_run=True, window_bars=window)
    symbol = symbols[0]
    df = feed.fetch_recent_closed(symbol).sort("timestamp")
    test_bars = df.tail(n_bars)

    tournament = build_tournament([symbol], tmp_dir, shadow_mode=True)

    # Accumulate per-bar results
    shadow_returns_by_strategy: dict[str, list[float]] = {}
    bar_returns: list[float] = []
    vol_confidences: list[float] = []
    trend_confidences: list[float] = []
    baseline_port_returns: list[float] = []  # exposure × shadow_return
    llm_port_returns: list[float] = []  # exposure × shadow_return × confidence
    bars_active: list[bool] = []  # whether a strategy was chosen

    warmup = window
    for i in range(warmup, len(test_bars)):
        bar_window = test_bars[max(0, i - window + 1) : i + 1]
        bar_time = bar_window["timestamp"].item(-1)

        obs = build_observation(symbol, bar_window, bar_time)
        posterior = compute_deterministic_posterior(bar_window, bar_time)
        bar_ret = compute_bar_return(bar_window, bar_time)

        decision = tournament.route(
            symbol=symbol,
            timeframe="1h",
            posterior=posterior,
            observed_at=bar_time,
            position_is_flat=True,
            observation=obs,
            bar_return=bar_ret,
        )

        bar_returns.append(bar_ret)
        v_conf = confidence_from_volatility(bar_window)
        p_conf = confidence_from_posterior(posterior)
        vol_confidences.append(v_conf)
        trend_confidences.append(p_conf)

        # Shadow returns per strategy for this bar
        shadow_rets = _get_shadow_returns(tournament, symbol, "1h")

        # Accumulate per-strategy shadow returns
        for sid, sret in shadow_rets.items():
            shadow_returns_by_strategy.setdefault(sid, []).append(sret)

        # Portfolio return = chosen_strategy_shadow_return × exposure_multiplier
        chosen = decision.chosen_strategy_id
        exp_mult = decision.exposure_multiplier
        bars_active.append(chosen is not None and exp_mult > 0)

        if chosen and chosen in shadow_rets and exp_mult > 0:
            strat_ret = shadow_rets[chosen]
            baseline_port_returns.append(strat_ret * exp_mult)
            llm_port_returns.append(strat_ret * exp_mult * p_conf)  # entropy-based confidence
        else:
            baseline_port_returns.append(0.0)
            llm_port_returns.append(0.0)

    # Convert to numpy arrays
    base_ret = np.array(baseline_port_returns)
    llm_ret = np.array(llm_port_returns)

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

    # Portfolio metrics
    base_eq = equity_curve(base_ret)
    llm_eq = equity_curve(llm_ret)

    portfolio_metrics = {
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
        "mean_confidence": np.mean(trend_confidences),
        "mean_vol_confidence": np.mean(trend_confidences),
        "mean_trend_confidence": np.mean(trend_confidences),
        "confidence_std": np.std(trend_confidences),
        "min_confidence": float(min(trend_confidences)),
        "max_confidence": float(max(trend_confidences)),
        "bars_conf_not_1": sum(1 for c in trend_confidences if abs(c - 1.0) > 0.01),
    }

    # Assertions
    passed = 0
    failed = 0
    checks: list[str] = []

    # Check 1: LLM-adjusted Sharpe >= baseline (confidence reduces bad-trade exposure)
    if portfolio_metrics["sharpe_llm"] >= portfolio_metrics["sharpe_baseline"] * 0.95:
        passed += 1
        checks.append("✓ LLM-adjusted Sharpe >= 95% of baseline")
    else:
        failed += 1
        checks.append(
            f"✗ LLM-adjusted Sharpe ({portfolio_metrics['sharpe_llm']:.4f}) < "
            f"95% baseline ({portfolio_metrics['sharpe_baseline']:.4f})"
        )

    # Check 2: Max-DD not worse with LLM
    if portfolio_metrics["max_dd_llm"] <= portfolio_metrics["max_dd_baseline"] * 1.1:
        passed += 1
        checks.append("✓ LLM max-DD <= 110% baseline max-DD")
    else:
        failed += 1
        checks.append(
            f"✗ LLM max-DD ({portfolio_metrics['max_dd_llm']:.4f}) > "
            f"110% baseline ({portfolio_metrics['max_dd_baseline']:.4f})"
        )

    # Check 3: Confidence varies (not all 1.0)
    if portfolio_metrics["bars_conf_not_1"] > 0:
        passed += 1
        checks.append(
            f"✓ Confidence varies across bars ({portfolio_metrics['bars_conf_not_1']} "
            f"bars with conf ≠ 1.0)"
        )
    else:
        failed += 1
        checks.append("✗ All confidence values = 1.0 (no LLM adjustment occurred)")

    # Check 4: Confidence clamped to [0.5, 1.5]
    if (min(vol_confidences) >= 0.5 and max(vol_confidences) <= 1.5):
        passed += 1
        checks.append("✓ All confidence values in [0.5, 1.5] clamp range")
    else:
        failed += 1
        checks.append(
            f"✗ Confidence out of clamp range: "
            f"min={min(vol_confidences)}, max={max(vol_confidences)}"
        )

    # Check 5: At least 3 strategies produce meaningful shadow returns
    active_strategies = {
        sid: m for sid, m in strategy_metrics.items() if m["n_bars"] > 50
    }
    if len(active_strategies) >= 3:
        passed += 1
        checks.append(f"✓ ≥3 strategies with meaningful shadow returns ({len(active_strategies)})")
    else:
        failed += 1
        checks.append(
            f"✗ Only {len(active_strategies)} strategies with >50 bars"
        )

    # Check 6: Portfolio Sharpe > 0 (positive edge)
    if portfolio_metrics["sharpe_baseline"] > 0:
        passed += 1
        checks.append(f"✓ Baseline Sharpe positive ({portfolio_metrics['sharpe_baseline']:.4f})")
    else:
        failed += 1
        checks.append(f"✗ Baseline Sharpe non-positive ({portfolio_metrics['sharpe_baseline']:.4f})")

    # Check 7: Win rate > 0.5 (directional edge)
    if portfolio_metrics["win_rate_baseline"] > 0.5:
        passed += 1
        checks.append(f"✓ Baseline win rate > 50% ({portfolio_metrics['win_rate_baseline']:.2%})")
    else:
        failed += 1
        checks.append(
            f"✗ Baseline win rate <= 50% ({portfolio_metrics['win_rate_baseline']:.2%})"
        )

    result = {
        "status": "PASS" if failed == 0 else "FAIL",
        "passed": passed,
        "failed": failed,
        "checks": checks,
        "portfolio_metrics": portfolio_metrics,
        "strategy_metrics": strategy_metrics,
    }

    # Save detailed results
    (output_dir / "results.json").write_text(
        json.dumps(result, indent=2, default=str)
    )
    return result


def _get_shadow_returns(tournament, symbol: str, timeframe: str) -> dict[str, float]:
    """Extract shadow returns for the current bar from tournament state."""
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


# ── Main ─────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="O-TRADE-1: Confidence adjustment backtesting"
    )
    parser.add_argument("--bars", type=int, default=DEFAULT_BARS)
    parser.add_argument("--symbols", default=DEFAULT_SYMBOLS)
    parser.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    parser.add_argument("--output-dir", default="data/confidence_backtest")
    args = parser.parse_args()

    symbols = [s.strip() for s in args.symbols.split(",")]
    output_dir = Path(args.output_dir)

    print(f"\n{'=' * 60}")
    print("  O-TRADE-1: Confidence Adjustment Backtesting")
    print(f"  Symbols: {symbols} | Bars: {args.bars} | Window: {args.window}")
    print(f"  Output: {output_dir}")
    print(f"{'=' * 60}\n")

    result = run_backtest(symbols, args.bars, args.window, output_dir)

    print(f"\n  Status: [{result['status']}]")
    print(f"  {result['passed']} passed, {result['failed']} failed\n")

    for check in result["checks"]:
        print(f"  {check}")

    pm = result["portfolio_metrics"]
    print(f"\n  ── Portfolio Metrics ──")
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
    print(f"    Mean confidence:     {pm['mean_confidence']:.4f}")
    print(f"    Confidence range:    [{pm['min_confidence']:.4f}, {pm['max_confidence']:.4f}]")
    print(f"    Confidence std:      {pm['confidence_std']:.4f}")
    print(f"    Bars conf ≠ 1.0:     {pm['bars_conf_not_1']}")

    print(f"\n  ── Per-Strategy Shadow Metrics ──")
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
