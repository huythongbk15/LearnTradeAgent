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
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

OUT_STORE = ROOT / "data" / "promotion_store"
EVIDENCE = ROOT / "data" / "wfo_real_campaign"

# Frozen promotion gates. A FAIL bundle is still evidence, but it cannot
# authorize a policy, so these decide whether anything is promotable.
DEFAULT_GATE_SET = [
    {
        "gate_id": "median_sharpe_positive",
        "metric": "aggregate.median_sharpe",
        "comparison": ">",
        "threshold": 0.5,
        "unit": "ratio",
    },
    {
        "gate_id": "median_return_positive",
        "metric": "aggregate.median_return_pct",
        "comparison": ">",
        "threshold": 0.0,
        "unit": "pct",
    },
    {
        "gate_id": "min_total_trades",
        "metric": "aggregate.total_trades",
        "comparison": ">=",
        "threshold": 30,
        "unit": "trades",
    },
]



def _rel(path: Path) -> str:
    """Repo-relative when inside the repo, absolute when it is not.

    --out-root may point outside the tree, and pathlib.relative_to raises
    rather than degrading, which would fail a run that had otherwise
    succeeded.
    """
    try:
        return str(Path(path).relative_to(ROOT))
    except ValueError:
        return str(path)

def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategy", default="enhanced_ma")
    ap.add_argument(
        "--strategies",
        default=None,
        help=(
            "Comma-separated strategies to campaign together. Each is planned, "
            "run, and published as its own schema-v2 bundle under one frozen "
            "plan per strategy."
        ),
    )
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--market-type", default="spot")
    ap.add_argument("--timeframe", default="1h")
    ap.add_argument("--n-outer", type=int, default=3)
    ap.add_argument("--train-months", type=int, default=9)
    ap.add_argument("--val-months", type=int, default=3)
    ap.add_argument("--test-months", type=int, default=3)
    ap.add_argument("--min-trades", type=int, default=10)
    ap.add_argument("--purge-bars", type=int, default=0)
    ap.add_argument("--embargo-bars", type=int, default=0)
    ap.add_argument(
        "--data-file",
        default=None,
        help=(
            "Local parquet of the exact bars the campaign reads. Required: a "
            "campaign cannot freeze a plan without binding the data it ran on."
        ),
    )
    ap.add_argument(
        "--gate-set",
        default=None,
        help="JSON file with the frozen gate list; defaults to the documented gates.",
    )
    ap.add_argument("--dry-run", action="store_true", help="plan only, do not write")
    ap.add_argument(
        "--param-grid",
        default=None,
        help=(
            "JSON param grid. The default is 9 combinations, which makes an "
            "inner search over a real window slow enough to need a smoke "
            "campaign with a single combination to verify the chain."
        ),
    )
    ap.add_argument(
        "--out-root",
        default=None,
        help=(
            "Evidence directory for this campaign. Defaults to "
            "data/wfo_real_campaign. Point two campaigns at separate roots to "
            "run them concurrently without one corrupting the other's cells."
        ),
    )
    ap.add_argument(
        "--no-publish",
        action="store_true",
        help="Build and verify the bundle but do not publish it.",
    )
    args = ap.parse_args()

    strategies = (
        [s.strip() for s in args.strategies.split(",") if s.strip()]
        if args.strategies
        else [args.strategy]
    )
    if not strategies:
        raise SystemExit("NOT_QUALIFIED: no strategy requested")
    if not args.data_file:
        raise SystemExit(
            "NOT_QUALIFIED: --data-file is required; a plan without a data "
            "binding would not say what the campaign ran on"
        )


    from trading_agent.backtest.campaign_integrity import (
        capture_campaign_source,
        require_wfo_campaign_evidence,
        verify_campaign_coverage,
    )
    from trading_agent.backtest.campaign_plan import (
        build_campaign_bundle,
        build_data_manifest,
        freeze_campaign_plan,
        plan_folds_from_spec,
        verify_frozen_plan,
    )
    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.backtest.nested_wfo import WFOSpec, run_nested_wfo
    from trading_agent.research.selection_policy import (
        PolicyActivationService,
        SelectionPolicyBuilder,
        SelectionPolicyRegistry,
    )

    evidence = (
        (ROOT / args.out_root).resolve() if args.out_root else EVIDENCE
    )
    data_path = (ROOT / args.data_file).resolve()
    if not data_path.is_file():
        raise SystemExit(f"NOT_QUALIFIED: data file not found: {data_path}")
    from trading_agent.backtest.campaign_evidence import read_ohlcv

    frame = read_ohlcv(data_path)
    if "timestamp" not in frame.columns or "close" not in frame.columns:
        raise SystemExit("NOT_QUALIFIED: data file needs timestamp and close columns")

    data_root = data_path.parent
    gate_set = (
        json.loads(Path(args.gate_set).read_text())
        if args.gate_set
        else DEFAULT_GATE_SET
    )

    source_binding = capture_campaign_source(ROOT)
    published: list[tuple[str, Path, str]] = []

    for strategy in strategies:
        print(f"\n{'=' * 70}\n=== {strategy} on {args.symbol} {args.timeframe}\n{'=' * 70}")
        spec = WFOSpec(
            strategy_id=strategy,
            symbol=args.symbol,
            timeframe=args.timeframe,
            train_months=args.train_months,
            val_months=args.val_months,
            test_months=args.test_months,
            step_months=args.test_months,
            param_grid=(
                json.loads(args.param_grid)
                if args.param_grid
                else {"fast_period": [10, 20, 30], "slow_period": [60, 80, 120]}
            ),
            min_trades_per_fold=args.min_trades,
            registry_path=str(evidence / "registry"),
        )

        span_months = (
            args.train_months
            + args.val_months
            + (args.test_months + args.test_months * (args.n_outer - 1))
        )
        print(f"  folds        : train {args.train_months}m / val {args.val_months}m "
              f"/ test {args.test_months}m x {args.n_outer} outer")
        print(f"  param grid   : {spec.param_grid}")
        print(f"  data         : {data_path.name} ({frame.height} bars)")
        print(f"  gate_set     : {[g['gate_id'] for g in gate_set]}")

        # Freeze before running. The thresholds, spans and data binding are
        # committed to now, so nothing about the decision can be chosen once
        # the numbers are visible.
        subject = {
            "strategy_id": strategy,
            "symbol": args.symbol,
            "market_type": args.market_type,
            "timeframe": args.timeframe,
            "params_hash": content_hash(spec.param_grid),
        }
        manifest = build_data_manifest(
            data_path,
            data_root=data_root,
            subject=subject,
            window={
                "start": frame["timestamp"][0].isoformat(),
                "end": frame["timestamp"][-1].isoformat(),
                "timeframe": args.timeframe,
                "market": args.market_type,
            },
        )
        folds = plan_folds_from_spec(
            frame,
            timeframe=args.timeframe,
            train_months=args.train_months,
            val_months=args.val_months,
            test_months=args.test_months,
            step_months=args.test_months,
            purge=args.purge_bars,
            embargo=args.embargo_bars,
        )
        if not folds:
            raise SystemExit(
                f"NOT_QUALIFIED: {frame.height} bars is too few for the "
                f"requested fold structure ({span_months} months needed)"
            )
        commission_bps, slippage_bps, spread_bps = 5.0, 2.0, 1.0
        plan = freeze_campaign_plan(
            subject=subject,
            folds=folds,
            data_manifest=manifest,
            cost_schedule={
                "commission_bps": commission_bps,
                "slippage_bps": slippage_bps,
                "spread_bps": spread_bps,
                "round_trip_bps": 2 * commission_bps + 2 * slippage_bps + spread_bps,
            },
            gate_set=gate_set,
            param_grid=spec.param_grid,
            frozen_by=f"run_real_wfo_campaign.py:{os.getenv('USER', 'operator')}",
        )
        verify_frozen_plan(plan)
        print(f"  frozen plan  : {plan['plan_fingerprint'][:16]} "
              f"({plan['fold_count']} folds)")

        if args.dry_run:
            print("\n--dry-run: plan frozen, nothing executed or written.")
            continue

        plan_path = evidence / strategy / "campaign_plan.json"
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(json.dumps(plan, indent=2, sort_keys=True))
        print(f"  plan written : {_rel(plan_path)}")

        print("\nrunning nested WFO (this executes backtests)...", flush=True)
        result = run_nested_wfo(spec, out_root=evidence)
        if capture_campaign_source(ROOT) != source_binding:
            raise SystemExit("NOT_QUALIFIED: source changed during campaign")

        try:
            from trading_agent.backtest.campaign_writer import (
                collect_wfo_campaign_rows,
            )

            measured_rows = collect_wfo_campaign_rows(result)
            print(f"  measured rows: {len(measured_rows)} verified fold rows")

            coverage = verify_campaign_coverage(
                requested_strategies=[strategy], out_root=evidence
            )
            if not coverage.ok:
                raise ValueError(f"incomplete campaign: {coverage.summary()}")

            # Producer: derive the aggregate, gate outcomes, verdict and id
            # from the measurements. It accepts a plan and rows, nothing else.
            bundle = build_campaign_bundle(
                plan=plan,
                rows=measured_rows,
                source=source_binding,
                data_root=data_root,
            )
            require_wfo_campaign_evidence(
                result, evidence, data_root=data_root, source_binding=source_binding
            )
            print(f"  verdict      : {bundle['verdict']} "
                  f"({len(bundle['failed_gates'])} failed gates)")
            for gate in bundle["failed_gates"]:
                print(f"    - {gate['gate_id']}: observed {gate['observed']} "
                      f"{gate['comparison']} {gate['threshold']} {gate['unit']} FAILED")
            for key, value in bundle["aggregate"].items():
                print(f"    {key:20s} {value}")

            bundle_path = evidence / strategy / "campaign_evidence.json"
            if not args.no_publish:
                from trading_agent.backtest.campaign_writer import (
                    publish_campaign_bundle,
                )

                published_path = publish_campaign_bundle(
                    result,
                    bundle,
                    bundle_path.parent,
                    data_root=data_root,
                    source_binding=source_binding,
                )
                print(f"  published    : {_rel(published_path)}")
                published.append((strategy, published_path, bundle["verdict"]))
            else:
                bundle_path.write_text(json.dumps(bundle, indent=2, sort_keys=True))
                print(f"  bundle (dry) : {_rel(bundle_path)}")
        except ValueError as exc:
            raise SystemExit(f"NOT_QUALIFIED: {exc}; no bundle or policy written") from exc

        agg = result.aggregate_metrics
        print("\n=== measured aggregate metrics ===")
        for key in (
            "n_outer_folds",
            "median_test_sharpe",
            "median_test_return_pct",
            "total_test_trades",
            "positive_outer_folds_pct",
            "median_max_drawdown_pct",
            "promotable",
        ):
            print(f"  {key:28s} {agg.get(key)}")
        print(f"  passes_hard_gates            {result.passes_hard_gates}")
        if getattr(result, "gate_failures", None):
            print(f"  gate_failures                {result.gate_failures}")

        if bundle["verdict"] != "PASS":
            print("\ncampaign verdict is FAIL — refusing to build a policy.")
            continue
        if not result.passes_hard_gates:
            print("\nWFO did not pass its hard gates — refusing to build a policy.")
            continue

        print("\n=== building policy through the measured-evidence gate ===")
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
        except Exception as exc:  # the gate refusing is a valid outcome
            print(f"policy build refused: {type(exc).__name__}: {exc}")
            print("This is the gate doing its job. No policy was written.")
            continue
        print(f"  policy_id       {policy.policy_id[:24]}")
        print(f"  status          {policy.status.value}")
        print(f"  stage           {policy.promotion_stage}")
        print(f"  code_sha        {policy.incumbent.code_sha[:16]}")

        reg = SelectionPolicyRegistry(OUT_STORE)
        reg.add(policy)
        svc = PolicyActivationService(
            reg,
            signing_key=b"real-wfo-campaign-key0",
            key_id="wfo-campaign",
            audit_path=OUT_STORE / "activation.jsonl",
        )
        active = svc.activate(
            policy.policy_id,
            actor="wfo-campaign",
            ticket=f"WFO-{strategy}-{args.symbol}",
            now=datetime.now(UTC),
        )
        print(f"  activated       {active.status.value}")

    if published:
        print(f"\n=== published {len(published)} campaign bundle(s) ===")
        for strategy, path, verdict in published:
            print(f"  {strategy:24s} {verdict:5s} {_rel(path)}")
    print(f"\nevidence: {_rel(evidence)}")


if __name__ == "__main__":
    main()