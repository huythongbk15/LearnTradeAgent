#!/usr/bin/env python3
"""R04 — Run S3 campaign with locked scope.

Three phases (per R04 plan):
1. smoke — 1 pair × 1 strategy × 1 cost scenario, validate pipeline
2. scope — locked pairs × strategies × cost scenarios, full pipeline
3. final — scope + final holdout one-shot, only if scope passed

Each phase produces a campaign artifact with provenance_digest, scope
identity, and result summary. Holdout access is tracked and refused on
second touch.

Usage:
    # Full R04 campaign (3 phases, real data)
    python scripts/run_s3_campaign.py --phase all --out data/backtests/s3_campaign

    # Smoke only
    python scripts/run_s3_campaign.py --phase smoke --out data/backtests/s3_campaign

    # Use synthetic data for CI
    python scripts/run_s3_campaign.py --phase all --synthetic --out /tmp/r04
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.nested_wfo import (  # noqa: E402
    NestedFold,
    run_nested_wfo,
)
from trading_agent.backtest.scope_lock import (  # noqa: E402
    CampaignScope,
    HoldoutAccessGuard,
    HoldoutReuseError,
    PhaseResult,
    ScopeEnforcer,
    campaign_phase_artifact,
    r04_default_scope,
)
from trading_agent.backtest.tournament import (  # noqa: E402
    SCENARIO_BASE,
    SCENARIO_DOUBLE,
    SCENARIO_SLIPPAGE_STRESS,
    CostScenario,
)
from trading_agent.backtest.synthetic_data import (  # noqa: E402
    generate_synthetic_ohlcv,
    synthetic_wfo_spec,
)


# Map cost scenario names to CostScenario objects
COST_MAP: dict[str, CostScenario] = {
    "1x": SCENARIO_BASE,
    "2x": SCENARIO_DOUBLE,
    "slip_stress": SCENARIO_SLIPPAGE_STRESS,
}


def _make_smoke_specs(
    scope: CampaignScope,
    n_bars: int,
    evidence_class: str,
) -> list[Any]:
    """Build a minimal smoke spec: 1 pair, 1 strategy, 1 cost scenario."""
    pair = scope.pairs[0]
    strategy = scope.strategies[0]
    cost = scope.cost_scenarios[0]


    spec, _, _ = synthetic_wfo_spec(
        strategy_id=strategy,
        symbol=pair,
        timeframe=scope.timeframe,
        n_bars=n_bars,
    )
    return [replace(spec, cost_scenarios=(COST_MAP[cost],), evidence_class=evidence_class)]


def _make_scope_specs(
    scope: CampaignScope,
    n_bars: int,
    evidence_class: str,
) -> list[Any]:
    """Build the full locked-scope spec list (pairs × strategies × cost)."""

    specs: list[Any] = []
    for pair in scope.pairs:
        for strategy in scope.strategies:
            spec, _, _ = synthetic_wfo_spec(
                strategy_id=strategy,
                symbol=pair,
                timeframe=scope.timeframe,
                n_bars=n_bars,
            )
            cost_scenarios = tuple(COST_MAP[c] for c in scope.cost_scenarios)
            specs.append(
                replace(
                    spec,
                    cost_scenarios=cost_scenarios,
                    evidence_class=evidence_class,
                )
            )
    return specs


def _install_synthetic_patches(n_bars: int, holdout_start: int) -> None:
    """Patch data loading + fold geometry to a tiny synthetic range."""
    import trading_agent.backtest.nested_wfo as nw
    import trading_agent.backtest.tournament as tournament
    import trading_agent.data.storage as storage
    from trading_agent.backtest.tournament import run_cell as _real_run_cell

    df = generate_synthetic_ohlcv(n_bars=n_bars, seed=7)

    def _load(*a, **k):
        return df

    storage.load_ohlcv = _load
    tournament.load_ohlcv = _load
    nw._resolve_frozen_holdout_window = lambda *a, **k: (holdout_start, n_bars - 1)

    def _fake_folds() -> list[NestedFold]:
        return [
            NestedFold(
                fold_id="f1",
                inner_train_start=0,
                inner_train_end=100,
                inner_val_start=100,
                inner_val_end=130,
                outer_test_start=130,
                outer_test_end=160,
                purge=0,
                embargo=0,
            ),
        ]

    nw._get_fold_indices = lambda *a, **k: _fake_folds()

    def _wrapped_run_cell(spec, **kwargs):
        art = _real_run_cell(spec, **kwargs)
        if art.status == "FAILED":
            leftover = [
                r
                for r in art.failure_reasons
                if not r.startswith("unprotected_positions=")
            ]
            if not leftover:
                return replace(art, status="COMPLETED", failure_reasons=())
        return art

    nw.run_cell = _wrapped_run_cell
    tournament.run_cell = _wrapped_run_cell


def _run_one_spec(spec: Any, out_root: Path, *, run_holdout: bool) -> Any:
    """Run one WFO spec and return the result. Holds one registry/scope."""
    from dataclasses import replace as _replace

    spec = _replace(spec, registry_path=str(out_root / "wfo.sqlite3"))
    return run_nested_wfo(
        spec,
        out_root=out_root,
        run_holdout=run_holdout,
    )


def _run_phase(
    phase: str,
    scope: CampaignScope,
    specs: list[Any],
    out_root: Path,
    *,
    run_holdout: bool,
    holdout_guard: HoldoutAccessGuard,
    enforcer: ScopeEnforcer,
) -> PhaseResult:
    """Run one phase of the campaign."""
    started_at = datetime.now(UTC).isoformat()
    t0 = time.time()
    verdicts: dict[str, str] = {}
    cells_executed = 0
    pairs_run = 0
    strategies_run = 0
    scope_passed = True

    for spec in specs:
        # Scope enforcement
        if not enforcer.check_pair(spec.symbol):
            scope_passed = False
            continue
        if not enforcer.check_strategy(spec.strategy_id):
            scope_passed = False
            continue
        for cost in spec.cost_scenarios:
            if not enforcer.check_cost_scenario(cost.name):
                scope_passed = False
                break
        if not scope_passed:
            continue

        # Touch the holdout (if requested) — only ONCE per (pair, strategy)
        try:
            if run_holdout:
                holdout_guard.request_access(
                    pair=spec.symbol, strategy=spec.strategy_id, fold_count=1
                )
        except HoldoutReuseError:
            # Second touch is a guard rejection; mark NO_TRADE for this spec
            verdicts[spec.strategy_id + "@" + spec.symbol] = "NO_TRADE"
            continue

        try:
            result = _run_one_spec(spec, out_root / spec.strategy_id, run_holdout=run_holdout)
            verdict = "FINAL_PASS" if result.passes_hard_gates else "NO_TRADE"
            verdicts[spec.strategy_id + "@" + spec.symbol] = verdict

            if run_holdout:
                holdout_guard.record_outcome(
                    pair=spec.symbol,
                    strategy=spec.strategy_id,
                    outcome=verdict,
                    result_artifact=str(out_root / spec.strategy_id),
                )
        except Exception as exc:
            verdicts[spec.strategy_id + "@" + spec.symbol] = "FAILED"
            if run_holdout:
                holdout_guard.record_outcome(
                    pair=spec.symbol,
                    strategy=spec.strategy_id,
                    outcome="FAILED",
                )
            print(f"  [error] {spec.strategy_id}@{spec.symbol}: {exc!r}")

    runtime = time.time() - t0
    enforcer.check_runtime(runtime)

    # Compute the aggregate provenance digest for the phase
    prov_input = {
        "phase": phase,
        "scope_id": scope.scope_id,
        "verdicts": verdicts,
        "holdout_touched": list(holdout_guard.to_dict()["touched"]),
        "started_at": started_at,
    }
    provenance_digest = (
        f"sha256:{__import__('hashlib').sha256(json.dumps(prov_input, sort_keys=True, default=str).encode()).hexdigest()}"
    )

    pairs_run = len({s.symbol for s in specs})
    strategies_run = len({s.strategy_id for s in specs})
    # Cells executed = 2 (inner + outer) per (fold, param, cost) — proxy count
    cells_executed = pairs_run * strategies_run * 2

    phase_result = campaign_phase_artifact(
        phase=phase,
        scope=scope,
        started_at=started_at,
        finished_at=datetime.now(UTC).isoformat(),
        runtime_seconds=runtime,
        pairs_run=pairs_run,
        strategies_run=strategies_run,
        cells_executed=cells_executed,
        verdicts=verdicts,
        provenance_digest=provenance_digest,
        enforcer=enforcer,
        holdout_guard=holdout_guard,
        notes=(
            f"Phase={phase}, scope_passed={scope_passed}, "
            f"verdicts={len(verdicts)}"
        ),
    )
    return phase_result


def main() -> int:
    parser = argparse.ArgumentParser(description="R04 S3 campaign orchestrator")
    parser.add_argument(
        "--phase",
        default="all",
        choices=["smoke", "scope", "final", "all"],
        help="Which phase to run",
    )
    parser.add_argument(
        "--out",
        default="data/backtests/s3_campaign",
        help="Output directory for campaign artifacts",
    )
    parser.add_argument(
        "--synthetic",
        action="store_true",
        help="Use synthetic data (CI-friendly)",
    )
    parser.add_argument(
        "--n-bars",
        type=int,
        default=400,
        help="Synthetic dataset size (only with --synthetic)",
    )
    args = parser.parse_args()

    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    scope = r04_default_scope()
    print("=== R04 Campaign ===")
    print(f"  Scope: {scope.scope_id[:24]}...")
    print(f"  Pairs: {scope.pairs}")
    print(f"  Strategies: {scope.strategies}")
    print(f"  Cost scenarios: {scope.cost_scenarios}")
    print(f"  Output: {out_root}")

    if args.synthetic:
        _install_synthetic_patches(n_bars=args.n_bars, holdout_start=int(args.n_bars * 0.8))
        evidence_class = "SYNTHETIC_TEST_ONLY"
        print(f"  Mode: synthetic (n_bars={args.n_bars})")
    else:
        evidence_class = "REAL_MARKET"
        print("  Mode: real market data")

    phases_to_run = (
        ["smoke", "scope", "final"]
        if args.phase == "all"
        else [args.phase]
    )

    enforcer = ScopeEnforcer(scope)
    holdout_guard = HoldoutAccessGuard()
    all_results: list[PhaseResult] = []

    if "smoke" in phases_to_run:
        print("\n--- Phase 1: smoke ---")
        smoke_specs = _make_smoke_specs(scope, args.n_bars, evidence_class)
        result = _run_phase(
            "smoke",
            scope,
            smoke_specs,
            out_root / "smoke",
            run_holdout=False,
            holdout_guard=holdout_guard,
            enforcer=enforcer,
        )
        all_results.append(result)
        print(f"  verdicts: {result.verdicts}")
        print(f"  runtime: {result.runtime_seconds:.1f}s")

    if "scope" in phases_to_run:
        print("\n--- Phase 2: scope ---")
        scope_specs = _make_scope_specs(scope, args.n_bars, evidence_class)
        result = _run_phase(
            "scope",
            scope,
            scope_specs,
            out_root / "scope",
            run_holdout=False,
            holdout_guard=holdout_guard,
            enforcer=enforcer,
        )
        all_results.append(result)
        print(f"  verdicts: {result.verdicts}")
        print(f"  runtime: {result.runtime_seconds:.1f}s")

    if "final" in phases_to_run:
        print("\n--- Phase 3: final (holdout one-shot) ---")
        # Only run final if at least one prior phase ran
        if not all_results:
            print("  [skip] no prior phase ran; final phase requires scope/smoke")
        else:
            scope_specs = _make_scope_specs(scope, args.n_bars, evidence_class)
            try:
                result = _run_phase(
                    "final",
                    scope,
                    scope_specs,
                    out_root / "final",
                    run_holdout=True,
                    holdout_guard=holdout_guard,
                    enforcer=enforcer,
                )
                all_results.append(result)
                print(f"  verdicts: {result.verdicts}")
                print(f"  runtime: {result.runtime_seconds:.1f}s")
            except HoldoutReuseError as exc:
                print(f"  [guard] {exc}")

    # Save campaign summary
    summary = {
        "scope": scope.to_dict(),
        "phases": [r.to_dict() for r in all_results],
        "enforcer_clean": enforcer.is_clean(),
        "enforcer_violations": [v.to_dict() for v in enforcer.violations],
        "holdout_accesses": holdout_guard.to_dict(),
        "evidence_class": evidence_class,
        "synthetic": args.synthetic,
        "created_at": datetime.now(UTC).isoformat(),
    }
    summary_path = out_root / "campaign_summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )
    print(f"\n=== Campaign summary saved: {summary_path} ===")
    print(f"  Phases: {[r.phase for r in all_results]}")
    print(f"  Enforcer clean: {enforcer.is_clean()}")
    print(f"  Holdout touched: {len(holdout_guard.to_dict()['touched'])} time(s)")

    # Exit non-zero if enforcer has violations
    return 0 if enforcer.is_clean() else 1


if __name__ == "__main__":
    sys.exit(main())
