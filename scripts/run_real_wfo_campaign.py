#!/usr/bin/env python3
"""Run a real nested WFO campaign and build a promotable policy from it.

This is the first attempt since the measured-evidence gate (8870e03) to
produce a policy whose scores come from an actual measurement rather than a
literal. It deliberately starts with a single strategy on a modest window:

  * verify the WFO runner actually completes
  * verify SelectionPolicyBuilder emits metrics the gate accepts
  * verify the built policy survives __post_init__ and activation
  * only then scale to more strategies

If any step fails the script stops with the reason rather than writing a
policy, because a policy written here would be the first artifact in the
repository that a downstream consumer can legitimately trust.

Run: .venv/bin/python scripts/run_real_wfo_campaign.py --strategy enhanced_ma
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

OUT_STORE = ROOT / "data" / "promotion_store"
EVIDENCE = ROOT / "data" / "wfo_real_campaign"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="enhanced_ma")
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--n-outer", type=int, default=3)
    ap.add_argument("--train-months", type=int, default=9)
    ap.add_argument("--val-months", type=int, default=3)
    ap.add_argument("--test-months", type=int, default=3)
    ap.add_argument("--min-trades", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true", help="plan only, do not write")
    args = ap.parse_args()

    from trading_agent.backtest.nested_wfo import WFOSpec, run_nested_wfo

    spec = WFOSpec(
        strategy_id=args.strategy,
        symbol=args.symbol,
        timeframe=args.timeframe,
        # Fold structure is month-based (see WFOSpec). train/val/test with a
        # step equal to the test length gives non-overlapping outer folds.
        train_months=args.train_months,
        val_months=args.val_months,
        test_months=args.test_months,
        step_months=args.test_months,
        param_grid={"fast_period": [10, 20, 30], "slow_period": [60, 80, 120]},
        min_trades_per_fold=args.min_trades,
        registry_path=str(EVIDENCE / "registry"),
    )

    span_months = args.train_months + args.val_months + (
        args.test_months + args.test_months * (args.n_outer - 1)
    )
    start = datetime(2022, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=31 * span_months)

    print(f"=== real WFO: {args.strategy} on {args.symbol} {args.timeframe} ===")
    print(f"  folds        : train {args.train_months}m / val {args.val_months}m "
          f"/ test {args.test_months}m x {args.n_outer} outer")
    print(f"  span         : {start:%Y-%m} .. {end:%Y-%m} (~{span_months} months)")
    print(f"  param grid   : {spec.param_grid}")
    print(f"  min trades   : {spec.min_trades_per_fold} per fold")
    print(f"  evidence     : {spec.evidence_class}")
    print(f"  dry run      : {args.dry_run}")
    if args.dry_run:
        print("\n--dry-run: nothing executed. Re-run without the flag to execute.")
        return

    print("\nrunning nested WFO (this executes backtests)...", flush=True)
    result = run_nested_wfo(spec, out_root=EVIDENCE)
    agg = result.aggregate_metrics

    print("\n=== measured aggregate metrics ===")
    for key in (
        "n_outer_folds", "median_test_sharpe", "median_test_return_pct",
        "total_test_trades", "positive_outer_folds_pct",
        "median_max_drawdown_pct", "promotable",
    ):
        print(f"  {key:28s} {agg.get(key)}")
    print(f"  passes_hard_gates            {result.passes_hard_gates}")
    if getattr(result, "gate_failures", None):
        print(f"  gate_failures                {result.gate_failures}")

    if not result.passes_hard_gates:
        print("\nWFO did not pass its hard gates — refusing to build a policy.")
        print("A policy built here would claim evidence the run did not produce.")
        return

    print("\n=== building policy through the measured-evidence gate ===")
    from trading_agent.research.selection_policy import (
        PolicyActivationService,
        SelectionPolicyBuilder,
        SelectionPolicyRegistry,
    )

    builder_result = result
    for attr in ("to_promotable", "promotable_view", "evidence_view"):
        if hasattr(builder_result, attr):
            builder_result = getattr(builder_result, attr)()
            break

    try:
        policy = SelectionPolicyBuilder.from_wfo_result(
            builder_result,
            regime="trend",
            release_digest="sha256:" + result.study_manifest.release_digest[:64],
        )
    except Exception as exc:
        print(f"policy build refused: {type(exc).__name__}: {exc}")
        print("This is the gate doing its job. No policy was written.")
        return

    print(f"  policy_id       {policy.policy_id[:24]}")
    print(f"  status          {policy.status.value}")
    print(f"  stage           {policy.promotion_stage}")
    print(f"  code_sha        {policy.incumbent.code_sha[:16]}")
    print(f"  scores          {json.dumps(policy.scores, default=str)}")

    store = OUT_STORE
    reg = SelectionPolicyRegistry(store)
    reg.add(policy)
    print(f"\n  persisted to    {store.relative_to(ROOT)}")

    svc = PolicyActivationService(
        reg, signing_key=b"real-wfo-campaign-key0", key_id="wfo-campaign",
        audit_path=store / "activation.jsonl",
    )
    active = svc.activate(
        policy.policy_id, actor="wfo-campaign",
        ticket=f"WFO-{args.strategy}-{args.symbol}", now=datetime.now(UTC),
    )
    print(f"  activated       {active.status.value}")

    print(f"\nevidence: {EVIDENCE.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
