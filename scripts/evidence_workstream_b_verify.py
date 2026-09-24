#!/usr/bin/env python3
"""Standalone portfolio verification for Workstream B.

Loads existing pickle files from /tmp/wvo_workstream_b/ and runs
the independent oracle verification without re-computing specs.
Usable when the main evidence_workstream_b.py run produced partial results.
"""
from __future__ import annotations

import json
import pickle
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

# Import the functions we need from the evidence module
# We import the module but only use its helper functions, not main()
import types

# Load evidence_workstream_b as a module (skip __main__ execution)
import importlib.util
spec = importlib.util.spec_from_file_location(
    "ewb", str(ROOT / "scripts" / "evidence_workstream_b.py")
)
ewb = importlib.util.module_from_spec(spec)
# Prevent the if __name__ == "__main__" block from running
import builtins
# We need to import the module's functions without running main()
# Set __name__ to something other than "__main__" to skip the main block
ewb.__name__ = "ewb"
spec.loader.exec_module(ewb)

from trading_agent.backtest.nested_wfo import (
    _build_portfolio_selection_result,
    WFOResult,
)
from trading_agent.backtest.scope_lock import r04_default_scope


def main() -> int:
    out_root = Path("/tmp/wvo_workstream_b")
    specs = ewb._build_specs()

    # Collect available results
    results: list[WFOResult] = []
    completed_specs = []
    for i, spec in enumerate(specs):
        spec_dir = out_root / f"{spec.strategy_id}__{spec.symbol.replace('/', '_')}__{spec.timeframe}"
        result_file = spec_dir / "wfo_result.pkl"
        if result_file.exists():
            with open(result_file, "rb") as f:
                results.append(pickle.load(f))
            completed_specs.append(f"{spec.strategy_id}::{spec.symbol}")
        else:
            print(f"  WARNING: Missing pickle for spec {i} ({spec.strategy_id}::{spec.symbol})")

    min_for_portfolio = 3
    if len(results) < min_for_portfolio:
        print(f"  ERROR: Only {len(results)}/{len(specs)} results (need >= {min_for_portfolio})")
        return 1

    print(f"  Collected {len(results)}/{len(specs)} results: {completed_specs}")

    # Build portfolio result
    scope = r04_default_scope()
    portfolio_result = _build_portfolio_selection_result(
        results, run_holdout=True, out_root=out_root
    )

    print(f"  Portfolio verdict (WFO): {portfolio_result.verdict}")
    print(f"  Passes hard gates: {portfolio_result.passes_hard_gates}")

    # Independent Oracle verification
    oracle_result = ewb._oracle_portfolio(results)
    gate_checks = ewb._independent_gate_check(oracle_result)
    holdout_checks = ewb._check_holdout_protection(results)
    scope_checks = ewb._check_scope_lock(scope, results)

    all_checks = gate_checks + holdout_checks + scope_checks
    passed = sum(1 for c in all_checks if c["verdict"] == "PASS")
    failed = sum(1 for c in all_checks if c["verdict"] == "FAIL")
    invalid = sum(1 for c in all_checks if c["verdict"] == "INVALID")

    wfo_passes = portfolio_result.passes_hard_gates
    oracle_passes = failed == 0 and invalid == 0

    conclusion_reasons = [c["reason"] for c in all_checks if c["verdict"] in ("FAIL", "INVALID")]
    conclusion = "FINAL_PASS" if (oracle_passes and wfo_passes) else "NO_TRADE"

    evidence = {
        "workstream": "B",
        "ac_id": "Workstream_B_S3",
        "oracle": (
            "independent_recompute: reads raw trade-level PnL from "
            "outer-fold report.json; recomputes Sharpe/PF/MDD/DSR from "
            "scratch; does NOT trust nested_wfo internal metrics"
        ),
        "scope": scope.to_dict(),
        "n_specs_total": len(specs),
        "n_specs_completed": len(results),
        "specs_completed": completed_specs,
        "specs_missing": [f"{spec.strategy_id}::{spec.symbol}" for i, spec in enumerate(specs) if not (out_root / f"{spec.strategy_id}__{spec.symbol.replace('/', '_')}__{spec.timeframe}/wfo_result.pkl").exists()],
        "specs": [
            {"strategy": s.strategy_id, "symbol": s.symbol,
             "param_grid": s.param_grid,
             "cost_scenarios": [c.name for c in s.cost_scenarios],
             "evidence_class": s.evidence_class}
            for s in specs
        ],
        "portfolio_verdict_wfo": portfolio_result.verdict,
        "portfolio_verdict_oracle": "PASS" if oracle_passes else "FAIL",
        "wfo_passes_hard_gates": wfo_passes,
        "oracle_passes_hard_gates": oracle_passes,
        "oracle_metrics": {k: v for k, v in oracle_result.items() if k != "per_member"},
        "per_member": oracle_result["per_member"],
        "gate_checks": all_checks,
        "total_checks": len(all_checks),
        "passed": passed,
        "failed": failed,
        "invalid": invalid,
        "conclusion": conclusion,
        "conclusion_reasons": conclusion_reasons if conclusion != "FINAL_PASS" else [],
        "created_at": datetime.now(UTC).isoformat(),
    }

    out_file = Path("/tmp/ac_workstream_b.json")
    out_file.write_text(
        json.dumps(evidence, indent=2, default=str, allow_nan=False),
        encoding="utf-8",
    )
    print(f"\nEvidence written: {out_file}")
    print(f"\n=== WORKSTREAM B CONCLUSION: {conclusion} ===")
    print(f"Total checks: {len(all_checks)}, PASS: {passed}, FAIL: {failed}, INVALID: {invalid}")

    return 0 if conclusion == "FINAL_PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
