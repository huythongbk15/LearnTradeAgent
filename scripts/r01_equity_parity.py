#!/usr/bin/env python3
"""R01: Verify equity curve parity between canonical signal injection and legacy strategy."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv
from trading_agent.strategies.canonical.candidates import build_parameterized_adapter
import trading_agent.data.storage as storage_module


SYNTHETIC_N_BARS = 1000
SYNTHETIC_SEED = 7

GOLDEN_ENHANCED_MA_PARAMS = {
    "fast_period": 15,
    "slow_period": 50,
    "adx_threshold": 40.0,
    "atr_sl_mult": 2.0,
    "atr_tp_mult": 3.0,
    "target_exposure_pct": 0.25,
}


def _install_synthetic_patch(n_bars: int = SYNTHETIC_N_BARS):
    """Patch load_ohlcv to return synthetic data."""
    df = generate_synthetic_ohlcv(n_bars=n_bars, seed=SYNTHETIC_SEED)

    def _load(*args, **kwargs):
        return df

    storage_module.load_ohlcv = _load


def run_with_canonical_signals(
    params: dict,
    symbol: str = "BTC/USDT",
    out_root: Path | None = None,
) -> dict[str, Any]:
    """Run FullSystemSimulator with canonical signals injected."""
    if out_root is None:
        out_root = Path("data/backtests/r01_canonical_signals")
    out_root.mkdir(parents=True, exist_ok=True)

    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.strategies.canonical.candidates import build_default_registry

    registry = build_default_registry()
    _, adapter = build_parameterized_adapter("enhanced_ma", params)
    legacy_strategy = adapter._strategy

    # Generate signals from legacy strategy (same as canonical)
    df = generate_synthetic_ohlcv(n_bars=SYNTHETIC_N_BARS, seed=SYNTHETIC_SEED)
    df_with_indicators = legacy_strategy.compute_indicators(df)
    signal_series = legacy_strategy.generate_signals(df_with_indicators).rename(
        "signal"
    )

    # Convert to list of int for injection
    signals = signal_series.to_list()

    sim = FullSystemSimulator(
        symbol=symbol,
        timeframe="1h",
        strategy_name="enhanced_ma",
        strategy_params_override=params,
        commission=0.001,
        slippage=0.0005,
        fresh=True,
        state_dir=str(out_root / "execution"),
        report_path=str(out_root / "report.json"),
        signal_series=signals,
    )

    result = sim.run()
    return result


def run_with_legacy_strategy(
    params: dict,
    symbol: str = "BTC/USDT",
    out_root: Path | None = None,
) -> dict[str, Any]:
    """Run FullSystemSimulator with legacy strategy (default path)."""
    if out_root is None:
        out_root = Path("data/backtests/r01_legacy_strategy")
    out_root.mkdir(parents=True, exist_ok=True)

    from scripts.full_system_backtest import FullSystemSimulator

    sim = FullSystemSimulator(
        symbol=symbol,
        timeframe="1h",
        strategy_name="enhanced_ma",
        strategy_params_override=params,
        commission=0.001,
        slippage=0.0005,
        fresh=True,
        state_dir=str(out_root / "execution"),
        report_path=str(out_root / "report.json"),
    )

    result = sim.run()
    return result


def compare_equity(report1: dict, report2: dict) -> dict:
    """Compare equity curves and metrics."""
    eq1 = report1.get("equity_curve", [])
    eq2 = report2.get("equity_curve", [])

    # Compare final equity
    final_eq1 = eq1[-1][1] if eq1 else None
    final_eq2 = eq2[-1][1] if eq2 else None

    # Compare metrics
    m1 = report1.get("metrics", {})
    m2 = report2.get("metrics", {})

    key_metrics = [
        "final_equity",
        "total_return_pct",
        "sharpe",
        "max_drawdown_pct",
        "total_trades",
        "win_rate_pct",
        "profit_factor",
    ]

    metric_comparison = {}
    for k in key_metrics:
        v1 = m1.get(k)
        v2 = m2.get(k)
        if v1 is not None and v2 is not None:
            if isinstance(v1, float) and isinstance(v2, float):
                if v1 == 0 and v2 == 0:
                    match = True
                    pct_diff = 0
                elif v1 == 0:
                    match = abs(v2) < 0.01
                    pct_diff = float("inf")
                else:
                    pct_diff = abs((v2 - v1) / v1) * 100
                    match = pct_diff < 0.01  # Essentially identical
            else:
                match = v1 == v2
                pct_diff = None
            metric_comparison[k] = {
                "canonical": v1,
                "legacy": v2,
                "match": match,
                "pct_diff": pct_diff,
            }

    # Equity curve point-by-point comparison (sample)
    min_len = min(len(eq1), len(eq2))
    eq_match = True
    max_eq_diff = 0
    for i in range(0, min_len, max(1, min_len // 100)):  # Sample 100 points
        diff = abs(eq1[i][1] - eq2[i][1])
        max_eq_diff = max(max_eq_diff, diff)
        if diff > 0.01:  # 1 cent tolerance
            eq_match = False

    return {
        "final_equity_canonical": final_eq1,
        "final_equity_legacy": final_eq2,
        "equity_match": eq_match,
        "max_equity_diff": max_eq_diff,
        "metrics": metric_comparison,
    }


def main():
    print("=" * 60)
    print("R01: Equity Curve Parity Check")
    print("=" * 60)

    _install_synthetic_patch()

    params = GOLDEN_ENHANCED_MA_PARAMS
    symbol = "BTC/USDT"

    print("\nStrategy: enhanced_ma")
    print(f"Params: {params}")
    print(f"Synthetic data: {SYNTHETIC_N_BARS} bars, seed={SYNTHETIC_SEED}")

    # Run with canonical signals injected
    print("\n[1/2] Running FullSystemSimulator with canonical signals injected...")
    canonical_result = run_with_canonical_signals(params, symbol)
    print(f"  Status: {canonical_result.get('status')}")
    c_metrics = canonical_result.get("metrics", {})
    print(f"  Final equity: {c_metrics.get('final_equity')}")
    print(f"  Total return: {c_metrics.get('total_return_pct')}%")
    print(f"  Sharpe: {c_metrics.get('sharpe')}")
    print(f"  Trades: {c_metrics.get('total_trades')}")

    # Run with legacy strategy
    print("\n[2/2] Running FullSystemSimulator with legacy strategy...")
    legacy_result = run_with_legacy_strategy(params, symbol)
    print(f"  Status: {legacy_result.get('status')}")
    l_metrics = legacy_result.get("metrics", {})
    print(f"  Final equity: {l_metrics.get('final_equity')}")
    print(f"  Total return: {l_metrics.get('total_return_pct')}%")
    print(f"  Sharpe: {l_metrics.get('sharpe')}")
    print(f"  Trades: {l_metrics.get('total_trades')}")

    # Compare
    print("\n[3/3] Comparing equity curves...")
    comparison = compare_equity(canonical_result, legacy_result)

    print(f"  Final equity (canonical): {comparison['final_equity_canonical']}")
    print(f"  Final equity (legacy): {comparison['final_equity_legacy']}")
    print(f"  Equity curves match: {comparison['equity_match']}")
    print(f"  Max equity diff: ${comparison['max_equity_diff']:.2f}")

    print("\n  Metric comparison:")
    all_match = True
    for k, v in comparison["metrics"].items():
        status = "✅" if v["match"] else "❌"
        diff_str = f"{v['pct_diff']:.4f}%" if v["pct_diff"] is not None else "N/A"
        print(
            f"    {status} {k}: canonical={v['canonical']} legacy={v['legacy']} diff={diff_str}"
        )
        if not v["match"]:
            all_match = False

    # Overall
    overall_pass = all_match and comparison["equity_match"]

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Equity curve parity: {'PASS' if overall_pass else 'FAIL'}")
    print(f"Overall R01 equity: {'PASS' if overall_pass else 'FAIL'}")

    # Save report
    report = {
        "test": "R01_equity_parity_check",
        "timestamp": datetime.now(UTC).isoformat(),
        "strategy": "enhanced_ma",
        "params": params,
        "symbol": symbol,
        "synthetic_bars": SYNTHETIC_N_BARS,
        "seed": SYNTHETIC_SEED,
        "canonical_result": {
            "status": canonical_result.get("status"),
            "metrics": c_metrics,
        },
        "legacy_result": {
            "status": legacy_result.get("status"),
            "metrics": l_metrics,
        },
        "comparison": comparison,
        "overall_passed": overall_pass,
    }

    report_path = Path("data/backtests/r01_parity") / "r01_equity_parity_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=str))
    print(f"\nReport saved: {report_path}")

    return 0 if overall_pass else 1


if __name__ == "__main__":
    sys.exit(main())
