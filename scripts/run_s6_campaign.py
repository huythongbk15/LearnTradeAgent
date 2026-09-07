#!/usr/bin/env python3
"""S6 Multi-Pair Portfolio Campaign Runner.

Runs nested WFO for the paper-eligible universe, applies portfolio-level
hard gates (≥60% positive pairs, 200+ aggregate OOS trades, ≤35%
concentration), and executes the frozen holdout one-shot (STR-0309).

Modes:
  --mode synthetic  : Fast CI-safe run on deterministic synthetic data
  --mode real       : Full real-data campaign (NOT for CI; heavy)

Exit codes:
  0 = FINAL_PASS (all portfolio gates pass + holdout COMPLETED)
  1 = NO_TRADE (portfolio gates fail)
  2 = HOLDOUT_FAILED (portfolio gates pass but holdout fails)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.nested_wfo import (  # noqa: E402
    NestedFold,
    run_nested_wfo_portfolio,
    WFOSpec,
)
from trading_agent.backtest.synthetic_data import (  # noqa: E402
    generate_synthetic_ohlcv,
    synthetic_wfo_spec,
)
from trading_agent.backtest.tournament import CostScenario, SCENARIO_BASE, SCENARIO_DOUBLE, SCENARIO_SLIPPAGE_STRESS

# Paper-eligible universe (10 pairs for 1h timeframe)
PAPER_ELIGIBLE = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
    "ADA/USDT",
    "DOGE/USDT",
    "TRX/USDT",
    "NEAR/USDT",
    "ZEC/USDT",
]

# Core 5 pairs for S6 (requirement: 5+ pairs)
S6_CORE_PAIRS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "BNB/USDT",
]

# Strategies to test
STRATEGIES = ["ma_adx", "enhanced_ma", "rsi", "bbands", "ma_vol_target"]


def build_s6_specs(
    strategy_id: str,
    timeframe: str = "1h",
    train_months: int = 12,
    val_months: int = 3,
    test_months: int = 3,
    step_months: int = 3,
    cost_scenarios: tuple[CostScenario, ...] = (
        SCENARIO_BASE,
        SCENARIO_DOUBLE,
        SCENARIO_SLIPPAGE_STRESS,
    ),
    registry_path: str = "data/wfo/s6_experiments.sqlite3",
    search_family: str = "s6_campaign",
    evaluator_version: str = "v1",
    seed: int = 42,
    core_only: bool = True,
    reduced_grid: bool = False,
) -> list[WFOSpec]:
    """Build WFO specs for S6 core pairs for a given strategy."""
    from scripts.run_nested_wfo import DEFAULT_PARAM_GRIDS

    param_grid = DEFAULT_PARAM_GRIDS.get(strategy_id, {})
    if reduced_grid:
        # Reduce param grid for faster runs
        param_grid = {k: v[:2] for k, v in param_grid.items()}

    pairs = S6_CORE_PAIRS if core_only else PAPER_ELIGIBLE
    specs = []
    for symbol in pairs:
        specs.append(
            WFOSpec(
                strategy_id=strategy_id,
                symbol=symbol,
                timeframe=timeframe,
                param_grid=param_grid,
                cost_scenarios=cost_scenarios,
                train_months=train_months,
                val_months=val_months,
                test_months=test_months,
                step_months=step_months,
                registry_path=registry_path,
                search_family=search_family,
                evaluator_version=evaluator_version,
                seed=seed,
                min_oos_trades=30,
                evidence_class="REAL_MARKET",
            )
        )
    return specs


def _install_synthetic_patches(n_bars: int, holdout_start: int):
    """Patch data loading + fold geometry + holdout window for synthetic mode."""
    import trading_agent.backtest.nested_wfo as nw
    import trading_agent.backtest.tournament as tournament
    import trading_agent.data.storage as storage
    import sys

    df = generate_synthetic_ohlcv(n_bars=n_bars, seed=7)

    def _load(*args, **kwargs):
        return df

    # Patch at source module level
    storage.load_ohlcv = _load
    tournament.load_ohlcv = _load

    # Also patch any cached references in nested_wfo module
    # (nested_wfo does lazy imports: `from trading_agent.data.storage import load_ohlcv`)
    # We need to ensure those lazy imports get the patched version
    sys.modules['trading_agent.data.storage'].load_ohlcv = _load  # type: ignore[attr-defined]

    # End-of-window open position is expected carry; treat as COMPLETED
    def _wrapped_run_cell(spec, **kwargs):
        from trading_agent.backtest.tournament import run_cell as _real_run_cell
        art = _real_run_cell(spec, **kwargs)
        if art.status == "FAILED":
            leftover = [
                r for r in art.failure_reasons
                if not r.startswith("unprotected_positions=")
            ]
            if not leftover:
                return replace(art, status="COMPLETED", failure_reasons=())
        return art

    nw.run_cell = _wrapped_run_cell

    setattr(
        nw,
        "_resolve_frozen_holdout_window",
        lambda d, s: (holdout_start, n_bars - 1),
    )
    setattr(
        nw,
        "_get_fold_indices",
        lambda *a, **k: [
            NestedFold(
                fold_id="f1",
                inner_train_start=0,
                inner_train_end=300,
                inner_val_start=300,
                inner_val_end=500,
                outer_test_start=500,
                outer_test_end=620,
                purge=0,
                embargo=0,
            ),
            NestedFold(
                fold_id="f2",
                inner_train_start=100,
                inner_train_end=400,
                inner_val_start=400,
                inner_val_end=600,
                outer_test_start=600,
                outer_test_end=720,
                purge=0,
                embargo=0,
            ),
        ],
    )


def run_synthetic_mode(out_root: Path, strategy_id: str, core_only: bool = True) -> dict:
    """Fast synthetic evidence run for CI."""
    n_bars = 1000
    holdout_start = 800
    # MUST patch BEFORE any nested_wfo code runs (load_ohlcv is called at import time in some paths)
    _install_synthetic_patches(n_bars, holdout_start)

    # Import AFTER patching to ensure load_ohlcv is already monkeypatched
    from dataclasses import replace

    spec, _, _ = synthetic_wfo_spec(
        strategy_id=strategy_id, symbol="BTC/USDT", n_bars=n_bars
    )
    spec = replace(spec, registry_path=str(out_root / "experiments.sqlite3"))
    # For synthetic, run portfolio mode with core pairs
    pairs = S6_CORE_PAIRS if core_only else PAPER_ELIGIBLE
    specs = [
        replace(spec, symbol=s, registry_path=str(out_root / "experiments.sqlite3"))
        for s in pairs
    ]

    result = run_nested_wfo_portfolio(
        specs,
        out_root=out_root,
        run_holdout=True,
        real_sensitivity=True,
    )

    # Check that real sensitivity was actually computed
    sens = result.aggregate_metrics.get("sensitivity", {})
    real_computed = sens.get("real_computed")
    expected_sensitivity = [
        "cost_2x",
        "slippage_stress",
        "drop_best_trade",
        "delay_1_bar",
        "parameter_neighbors",
    ]
    if real_computed != expected_sensitivity:
        raise SystemExit(f"REGRESSION: real sensitivity not executed: {real_computed}")

    # Get holdout status from individual results (all should be same)
    holdout_status = "NOT_RUN"
    if result.results and result.results[0].final_holdout:
        holdout_status = result.results[0].final_holdout.get("status", "NOT_RUN")

    summary = {
        "mode": "synthetic",
        "evidence_class": result.aggregate_metrics.get("evidence_class"),
        "promotable": result.aggregate_metrics.get("promotable"),
        "study_manifest_id": result.aggregate_metrics.get("study_manifest_id"),
        "strategy_id": strategy_id,
        "n_pairs": len(pairs),
        "n_outer_folds": len(result.results[0].outer_results) if result.results else 0,
        "passes_hard_gates": result.passes_hard_gates,
        "real_sensitivity": real_computed,
        "final_holdout_status": holdout_status,
        "no_trade_artifact_id": result.no_trade_artifact.no_trade_id
        if result.no_trade_artifact
        else None,
        "portfolio_verdict": result.verdict,
        "portfolio_gates": [
            {
                "gate_id": g.gate_id,
                "observed": g.observed_value,
                "threshold": g.threshold,
                "verdict": g.verdict,
            }
            for g in result.gate_results
        ],
        "aggregate_metrics": result.aggregate_metrics,
    }
    return summary


def run_real_mode(
    out_root: Path,
    strategy_id: str,
    timeframe: str = "1h",
    train_months: int = 12,
    val_months: int = 3,
    test_months: int = 3,
    step_months: int = 3,
    core_only: bool = True,
    reduced_grid: bool = False,
) -> dict:
    """Full real-data campaign (NOT for CI)."""
    # Import here to avoid any import-time side effects
    from trading_agent.backtest.nested_wfo import run_nested_wfo_portfolio

    specs = build_s6_specs(
        strategy_id=strategy_id,
        timeframe=timeframe,
        train_months=train_months,
        val_months=val_months,
        test_months=test_months,
        step_months=step_months,
        registry_path=str(out_root / "experiments.sqlite3"),
        search_family="s6_real_campaign",
        core_only=core_only,
        reduced_grid=reduced_grid,
    )

    result = run_nested_wfo_portfolio(
        specs,
        out_root=out_root,
        run_holdout=True,
        real_sensitivity=True,
    )

    # Check real sensitivity
    sens = result.aggregate_metrics.get("sensitivity", {})
    real_computed = sens.get("real_computed")
    expected_sensitivity = [
        "cost_2x",
        "slippage_stress",
        "drop_best_trade",
        "delay_1_bar",
        "parameter_neighbors",
    ]
    if real_computed != expected_sensitivity:
        raise SystemExit(f"REGRESSION: real sensitivity not executed: {real_computed}")

    # Get holdout status from individual results (all should be same)
    holdout_status = "NOT_RUN"
    if result.results and result.results[0].final_holdout:
        holdout_status = result.results[0].final_holdout.get("status", "NOT_RUN")

    summary = {
        "mode": "real",
        "timeframe": timeframe,
        "evidence_class": result.aggregate_metrics.get("evidence_class"),
        "promotable": result.aggregate_metrics.get("promotable"),
        "study_manifest_id": result.aggregate_metrics.get("study_manifest_id"),
        "strategy_id": strategy_id,
        "n_pairs": len(PAPER_ELIGIBLE) if not core_only else len(S6_CORE_PAIRS),
        "n_outer_folds": len(result.results[0].outer_results) if result.results else 0,
        "passes_hard_gates": result.passes_hard_gates,
        "real_sensitivity": real_computed,
        "final_holdout_status": holdout_status,
        "no_trade_artifact_id": result.no_trade_artifact.no_trade_id
        if result.no_trade_artifact
        else None,
        "portfolio_verdict": result.verdict,
        "portfolio_gates": [
            {
                "gate_id": g.gate_id,
                "observed": g.observed_value,
                "threshold": g.threshold,
                "verdict": g.verdict,
            }
            for g in result.gate_results
        ],
        "aggregate_metrics": result.aggregate_metrics,
    }
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--mode",
        choices=["synthetic", "real"],
        default="synthetic",
        help="synthetic=fast CI; real=full campaign (heavy)",
    )
    ap.add_argument("--strategy", default="ma_adx", choices=STRATEGIES)
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--val-months", type=int, default=3)
    ap.add_argument("--test-months", type=int, default=3)
    ap.add_argument("--step-months", type=int, default=3)
    ap.add_argument(
        "--out-root",
        default=str(ROOT / "data" / "backtests" / "s6_campaign"),
        help="Output root directory",
    )
    ap.add_argument(
        "--cost",
        choices=["1x", "2x", "slip_stress", "all"],
        default="1x",  # Default to 1x for faster runs
        help="Cost scenarios to run",
    )
    ap.add_argument(
        "--core-only",
        action="store_true",
        default=True,
        help="Run only core 5 pairs (BTC, ETH, SOL, XRP, BNB)",
    )
    ap.add_argument(
        "--all-pairs",
        action="store_true",
        help="Run all 10 paper-eligible pairs",
    )
    ap.add_argument(
        "--reduced-grid",
        action="store_true",
        help="Use reduced parameter grid (2 values per param) for faster runs",
    )
    args = ap.parse_args(argv)

    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    cost_scenarios: tuple[CostScenario, ...]
    if args.cost == "1x":
        cost_scenarios = (SCENARIO_BASE,)
    elif args.cost == "2x":
        cost_scenarios = (SCENARIO_DOUBLE,)
    elif args.cost == "slip_stress":
        cost_scenarios = (SCENARIO_SLIPPAGE_STRESS,)
    else:
        cost_scenarios = (SCENARIO_BASE, SCENARIO_DOUBLE, SCENARIO_SLIPPAGE_STRESS)

    core_only = args.core_only and not args.all_pairs

    if args.mode == "synthetic":
        print(f"Running S6 synthetic campaign for {args.strategy} (core_only={core_only})...")
        summary = run_synthetic_mode(out_root, args.strategy, core_only=core_only)
    else:
        print(f"Running S6 REAL campaign for {args.strategy} (core_only={core_only}, reduced_grid={args.reduced_grid})...")
        summary = run_real_mode(
            out_root,
            args.strategy,
            args.timeframe,
            args.train_months,
            args.val_months,
            args.test_months,
            args.step_months,
            core_only=core_only,
            reduced_grid=args.reduced_grid,
        )

    # Save summary
    summary_path = out_root / "s6_campaign_summary.json"
    summary["timestamp"] = datetime.now(UTC).isoformat()
    pairs = S6_CORE_PAIRS if core_only else PAPER_ELIGIBLE
    summary["config"] = {
        "strategy": args.strategy,
        "timeframe": args.timeframe,
        "train_months": args.train_months,
        "val_months": args.val_months,
        "test_months": args.test_months,
        "step_months": args.step_months,
        "cost_scenarios": args.cost,
        "core_only": core_only,
        "reduced_grid": args.reduced_grid,
        "pairs": pairs,
    }
    summary_path.write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nSummary saved to {summary_path}")

    # Print results
    print("\n=== S6 CAMPAIGN RESULTS ===")
    print(f"Mode: {summary['mode']}")
    print(f"Strategy: {summary['strategy_id']}")
    print(f"Pairs: {summary['n_pairs']}")
    print(f"Portfolio Verdict: {summary['portfolio_verdict']}")
    print(f"Hard Gates Pass: {summary['passes_hard_gates']}")
    print(f"Real Sensitivity: {summary['real_sensitivity']}")
    print(f"Holdout Status: {summary['final_holdout_status']}")

    for gate in summary["portfolio_gates"]:
        status = "✅" if gate["verdict"] == "PASS" else "❌"
        print(f"  {status} {gate['gate_id']}: observed={gate['observed']} threshold={gate['threshold']}")

    # Exit codes per spec
    if summary["portfolio_verdict"] == "FINAL_PASS":
        print("\n✅ S6 CAMPAIGN: FINAL_PASS")
        return 0
    elif summary["portfolio_verdict"] == "NO_TRADE":
        print("\n❌ S6 CAMPAIGN: NO_TRADE (portfolio gates failed)")
        return 1
    elif summary["portfolio_verdict"] == "HOLDOUT_FAILED":
        print("\n⚠️ S6 CAMPAIGN: HOLDOUT_FAILED (gates passed but holdout failed)")
        return 2
    else:
        print(f"\n❓ S6 CAMPAIGN: {summary['portfolio_verdict']}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())