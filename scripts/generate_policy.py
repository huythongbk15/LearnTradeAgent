#!/usr/bin/env python3
"""Generate SelectionPolicyArtifact from WFO results (S4).

Reads nested WFO results and creates policy artifacts for symbols
that pass hard gates. Pairs that fail → NO_TRADE policy.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyStatus,
    SelectionPolicyArtifact,
    SelectionPolicyRegistry,
)


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


def load_wfo_results(wfo_root: Path) -> list[dict]:
    """Load WFO results from summary JSON."""
    summary_path = wfo_root / "wfo_summary.json"
    if not summary_path.exists():
        # Try to find individual reports
        results = []
        for cell_dir in wfo_root.iterdir():
            if not cell_dir.is_dir():
                continue
            report_path = cell_dir / "report.json"
            if report_path.exists():
                with open(report_path) as f:
                    report = json.load(f)
                results.append(report)
        return results

    with open(summary_path) as f:
        data = json.load(f)
        # Handle both formats: direct list or {"results": [...]}
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "results" in data:
            return data["results"]
        return []


def create_policy_from_wfo(
    wfo_result: dict,
    *,
    regime: str = "TRENDING_UP",
    timeframe: str = "1h",
    commit_sha: str = "auto",
    data_manifest_sha: str = "auto",
    feature_manifest_sha: str = "auto",
    release_digest: str = "auto",
    validity_days: int = 30,
    actor: str = "system",
    ticket: str = "auto",
) -> SelectionPolicyArtifact:
    """Create SelectionPolicyArtifact from WFO result."""
    strategy_id = wfo_result.get("strategy", "unknown")
    symbol = wfo_result.get("symbol", "unknown")
    passes = wfo_result.get("passes", False)
    gate_failures = wfo_result.get("gate_failures", [])
    aggregate = wfo_result.get("aggregate", {})
    statistical = wfo_result.get("statistical", {})

    # Determine scores
    scores = {
        "median_test_sharpe": aggregate.get("median_test_sharpe", 0.0),
        "median_test_return_pct": aggregate.get("median_test_return_pct", 0.0),
        "positive_outer_folds_pct": aggregate.get("positive_outer_folds_pct", 0.0),
        "total_test_trades": aggregate.get("total_test_trades", 0),
        "dsr": statistical.get("dsr", 0.0),
        "psr": statistical.get("psr", 0.0),
        "pbo": statistical.get("pbo", 0.0),
    }

    # Generate evidence IDs (placeholder - would come from actual evidence artifacts)
    evidence_ids = tuple(
        f"evidence_{kind}_{symbol.replace('/', '').replace('-', '')}"
        for kind in [
            "outer_oos",
            "minimum_trades",
            "deflated_sharpe",
            "pbo",
            "cost_stress",
        ]
    )

    if passes:
        # Create incumbent param artifact
        incumbent = ParamArtifact(
            strategy_id=strategy_id,
            params={"placeholder": "params_from_wfo"},  # Would be best params from WFO
        )

        policy = SelectionPolicyArtifact(
            symbol=symbol,
            timeframe=timeframe,
            regime=regime,
            incumbent=incumbent,
            challengers=(),  # No challengers for now
            scores=scores,
            evidence_ids=evidence_ids,
            validity_start=datetime.now(UTC),
            validity_end=datetime.now(UTC) + timedelta(days=validity_days),
            fallback="NO_TRADE",
            risk_cap=0.25,
            status=PolicyStatus.VALIDATED,  # Ready for activation
            policy_commit_sha=commit_sha if commit_sha != "auto" else os.getenv("GITHUB_SHA", "unknown"),
            policy_data_manifest_sha=data_manifest_sha,
            policy_feature_manifest_sha=feature_manifest_sha,
            policy_release_digest=release_digest,
        )
    else:
        # NO_TRADE policy
        incumbent = ParamArtifact(
            strategy_id="NO_TRADE",
            params={},
        )
        policy = SelectionPolicyArtifact(
            symbol=symbol,
            timeframe=timeframe,
            regime=regime,
            incumbent=incumbent,
            challengers=(),
            scores=scores,
            evidence_ids=evidence_ids,
            validity_start=datetime.now(UTC),
            validity_end=datetime.now(UTC) + timedelta(days=validity_days),
            fallback="NO_TRADE",
            risk_cap=0.0,
            status=PolicyStatus.VALIDATED,
            policy_commit_sha=commit_sha if commit_sha != "auto" else os.getenv("GITHUB_SHA", "unknown"),
            policy_data_manifest_sha=data_manifest_sha,
            policy_feature_manifest_sha=feature_manifest_sha,
            policy_release_digest=release_digest,
        )

    return policy


def main():
    parser = argparse.ArgumentParser(description="Generate SelectionPolicyArtifacts from WFO results")
    parser.add_argument("--wfo-root", default="data/backtests/wfo", help="WFO results root directory")
    parser.add_argument("--out", default="data/policies", help="Output registry path")
    parser.add_argument("--regime", default="TRENDING_UP", help="Regime for policy")
    parser.add_argument("--timeframe", default="1h", help="Timeframe")
    parser.add_argument("--validity-days", type=int, default=30, help="Policy validity window in days")
    parser.add_argument("--commit-sha", default="auto", help="Git commit SHA")
    parser.add_argument("--data-manifest", default="auto", help="Data manifest SHA")
    parser.add_argument("--feature-manifest", default="auto", help="Feature manifest SHA")
    parser.add_argument("--release-digest", default="auto", help="Release image digest")
    parser.add_argument("--actor", default="system", help="Activating actor")
    parser.add_argument("--ticket", default="auto", help="Approval ticket")
    parser.add_argument("--activate", action="store_true", help="Activate policies immediately")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be created")

    args = parser.parse_args()

    wfo_root = Path(args.wfo_root)
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    # Load WFO results
    results = load_wfo_results(wfo_root)

    if not results:
        print(f"No WFO results found in {wfo_root}")
        return

    registry = SelectionPolicyRegistry(out_root)

    print(f"Generating policies from {len(results)} WFO results...")
    print(f"Registry: {out_root}")
    print()

    for result in results:
        if not isinstance(result, dict):
            continue
        policy = create_policy_from_wfo(
            result,
            regime=args.regime,
            timeframe=args.timeframe,
            commit_sha=args.commit_sha,
            data_manifest_sha=args.data_manifest,
            feature_manifest_sha=args.feature_manifest,
            release_digest=args.release_digest,
            validity_days=args.validity_days,
            actor=args.actor,
            ticket=args.ticket,
        )

        if args.dry_run:
            print(f"  {policy.symbol} {policy.timeframe} {policy.regime}:")
            print(f"    strategy: {policy.incumbent.strategy_id}")
            print(f"    passes: {policy.incumbent.strategy_id != 'NO_TRADE'}")
            print(f"    scores: {policy.scores}")
            print(f"    policy_id: {policy.policy_id}")
            continue

        # Add to registry
        policy_id = registry.add(policy)
        print(f"  ✅ {policy.symbol} {policy.timeframe} {policy.regime} → {policy_id} [{policy.incumbent.strategy_id}]")

        # Activate if requested
        if args.activate and policy.incumbent.strategy_id != "NO_TRADE":
            activated = policy.activate(args.actor, args.ticket)
            registry.add(activated)
            print(f"     → ACTIVATED by {args.actor} (ticket: {args.ticket})")

    print(f"\nDone. Policies in {out_root}")


if __name__ == "__main__":
    main()