#!/usr/bin/env python3
"""Run a WFO campaign that writes a real policy into the real promotion store.

This is the missing link between research and runtime. Every campaign so far
stopped at "the gate allowed it" or "the gate refused it"; nothing has
carried a WFO-produced policy into the store the router reads, so the path
from a measurement to a routed decision has never been exercised.

Configuration comes from plan_promotable_campaign.py rather than being
guessed. The spread gate at p<=0.20 needs 10/14 folds, 13/21 or 19/32, and
the first attempt used 18/3/3/6, which yields 4 folds on 1h — a campaign
that cannot pass at any clearing rate. Daily bars at 12/2/2/2 give 32 folds
and a 59% bar, so that is the default here.

Two candidates are run rather than one. Picking a single strategy by its
ratio on a single window is selection on the evaluation data — the probe
that ranked ma_adx above enhanced_ma differed by 4%, which is noise. Running
both costs twice as much and answers the question without the bias.

Writes nothing on failure. The promotion gate refuses unmeasured policies,
and this script does not work around it.

Run: .venv/bin/python scripts/build_campaign_policy.py --dry-run
Run: .venv/bin/python scripts/build_campaign_policy.py
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import polars as pl

from trading_agent.backtest.nested_wfo import _binomial_upper_tail, _get_fold_indices

STORE = ROOT / "data" / "promotion_store"
EVIDENCE = ROOT / "data" / "campaign_evidence"

# (strategy_id, module, class) — the same mapping probe_daily_tradeability.py
# uses, verified against the canonical registry's strategy_id.
CANDIDATES = [
    ("enhanced_ma", "enhanced_ma", "EnhancedMaCrossover"),
    ("ma_adx", "enhanced_ma", "MaAdxCrossover"),
]

P_MAX = 0.20
MIN_FOLDS = 14


def fold_plan(symbol: str, timeframe: str, train: int, val: int, test: int, step: int):
    path = ROOT / "data" / "raw" / "binance" / symbol / f"{timeframe}.parquet"
    n_bars = pl.read_parquet(path, columns=["close"]).height
    purge = 100 if timeframe == "1h" else 25
    folds = _get_fold_indices(n_bars, timeframe, train, val, test, step, purge, purge)
    need = next((k for k in range(len(folds) + 1)
                 if _binomial_upper_tail(k, len(folds)) <= P_MAX), None)
    return n_bars, folds, need


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC_USDT")
    ap.add_argument("--timeframe", default="1d")
    ap.add_argument("--train-months", type=int, default=12)
    ap.add_argument("--val-months", type=int, default=2)
    ap.add_argument("--test-months", type=int, default=2)
    ap.add_argument("--step-months", type=int, default=2)
    ap.add_argument("--strategy", default=None,
                    help="run one strategy instead of both candidates")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    n_bars, folds, need = fold_plan(
        args.symbol, args.timeframe, args.train_months,
        args.val_months, args.test_months, args.step_months,
    )

    print("=" * 74)
    print("CAMPAIGN — WFO to a policy in the real promotion store")
    print("=" * 74)
    print(f"  symbol/timeframe : {args.symbol} {args.timeframe}")
    print(f"  data             : {n_bars:,} bars")
    print(f"  folds            : {len(folds)} "
          f"({args.train_months}/{args.val_months}/{args.test_months}/"
          f"{args.step_months} months)")
    print(f"  spread gate      : need {need}/{len(folds)} cost-clearing folds "
          f"({need / len(folds) * 100:.0f}%) at p<={P_MAX}")

    if len(folds) < MIN_FOLDS:
        print(f"\nABORT: {len(folds)} folds cannot resolve the gate "
              f"(minimum {MIN_FOLDS}).")
        print("A campaign with too few folds measures nothing and must not run.")
        return
    if need is None:
        print(f"\nABORT: no clearing rate reaches p<={P_MAX} at n={len(folds)}.")
        return

    cands = [args.strategy] if args.strategy else [c[0] for c in CANDIDATES]
    print(f"  candidates       : {', '.join(cands)}")
    print(f"  store            : {STORE.relative_to(ROOT)}")

    if args.dry_run:
        print("\n--dry-run: nothing executed. Re-run without the flag.")
        return

    from trading_agent.backtest.nested_wfo import WFOSpec, run_nested_wfo
    from trading_agent.research.selection_policy import (
        PolicyActivationService,
        SelectionPolicyBuilder,
        SelectionPolicyRegistry,
    )

    EVIDENCE.mkdir(parents=True, exist_ok=True)
    STORE.mkdir(parents=True, exist_ok=True)

    results = []
    for sid in cands:
        print(f"\n{'=' * 74}\n{sid}\n{'=' * 74}")
        spec = WFOSpec(
            strategy_id=sid,
            symbol=args.symbol.replace("_", "/"),
            timeframe=args.timeframe,
            train_months=args.train_months,
            val_months=args.val_months,
            test_months=args.test_months,
            step_months=args.step_months,
            min_trades_per_fold=1,
            registry_path=str(EVIDENCE / f"registry_{sid}.sqlite3"),
        )
        out = EVIDENCE / sid
        out.mkdir(parents=True, exist_ok=True)
        print(f"  running {len(folds)} folds ...", flush=True)
        try:
            res = run_nested_wfo(spec, out_root=out, real_sensitivity=False)
        except Exception as exc:
            print(f"  FAILED: {type(exc).__name__}: {exc}")
            results.append({"strategy": sid, "status": "run_failed",
                            "error": str(exc)[:200]})
            continue

        agg = res.aggregate_metrics
        print(f"  median sharpe    : {agg.get('median_test_sharpe')}")
        print(f"  median return %  : {agg.get('median_test_return_pct')}")
        print(f"  total trades     : {agg.get('total_test_trades')}")
        print(f"  passes hard gates: {res.passes_hard_gates}")
        if getattr(res, "gate_failures", None):
            print(f"  gate failures    : {res.gate_failures}")

        entry = {"strategy": sid, "status": "ran",
                 "aggregate": {k: agg.get(k) for k in (
                     "median_test_sharpe", "median_test_return_pct",
                     "total_test_trades", "median_oos_trades" if "median_oos_trades"
                     in agg else "median_test_trades", "n_outer_folds")},
                 "passes_hard_gates": bool(res.passes_hard_gates),
                 "gate_failures": list(getattr(res, "gate_failures", []) or [])}

        if not res.passes_hard_gates:
            entry["status"] = "hard_gates_failed"
            print("\n  hard gates failed — no policy built. Correct: the run did")
            print("  not produce a promotable result.")
            results.append(entry)
            continue

        print("\n  building policy ...")
        digest = getattr(res.study_manifest, "release_digest", None) or "0" * 64
        try:
            policy = SelectionPolicyBuilder.from_wfo_result(
                res, regime="trend", release_digest=f"sha256:{digest[:64]}"
            )
        except Exception as exc:
            entry["status"] = "policy_refused"
            entry["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  policy refused by the promotion gate: {exc}")
            print("  This is the gate working. Nothing written.")
            results.append(entry)
            continue

        reg = SelectionPolicyRegistry(STORE)
        reg.add(policy)
        print(f"  policy_id        {policy.policy_id[:24]}")
        print(f"  status           {policy.status.value}")
        print(f"  stage            {policy.promotion_stage}")
        print(f"  scores           {json.dumps(policy.scores, default=str)}")

        svc = PolicyActivationService(
            reg, signing_key=b"campaign-policy-signing0", key_id="campaign",
            audit_path=EVIDENCE / "activation.jsonl",
        )
        try:
            active = svc.activate(
                policy.policy_id, actor="campaign",
                ticket=f"CAMPAIGN-{sid}", now=datetime.now(UTC),
            )
            print(f"  ACTIVATED        {active.status.value}")
            entry["status"] = "activated"
            entry["policy_id"] = policy.policy_id
        except Exception as exc:
            entry["status"] = "activation_refused"
            entry["error"] = str(exc)[:200]
            entry["policy_id"] = policy.policy_id
            print(f"  activation refused: {exc}")

        results.append(entry)

    print(f"\n{'=' * 74}\nSUMMARY\n{'=' * 74}")
    for r in results:
        print(f"  {r['strategy']:16s} {r['status']}")
    activated = [r for r in results if r["status"] == "activated"]
    print(f"\nactivated: {len(activated)} of {len(results)}")
    json.dump(results, open(EVIDENCE / "campaign_summary.json", "w"),
              indent=2, default=str)
    print(f"summary: {(EVIDENCE / 'campaign_summary.json').relative_to(ROOT)}")
    if not activated:
        print("\nNo policy activated. That is a real answer, not a failure of")
        print("the harness: it means no candidate cleared the promotion gates.")
        print("The store stays empty and the router will abstain.")


if __name__ == "__main__":
    main()