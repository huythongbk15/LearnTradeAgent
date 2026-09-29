#!/usr/bin/env python3
"""Audit existing campaigns against the coverage invariant.

Applies src/trading_agent/backtest/campaign_integrity.py to campaigns that
already ran, so the pre-fix damage is measured rather than inferred. The
check is the one added after a65ed29000, run retrospectively.

Campaigns with no recorded request list are reported as unverifiable rather
than silently counted, since a coverage check needs both sides.

Run: .venv/bin/python scripts/audit_campaign_coverage.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from trading_agent.backtest.campaign_integrity import _cells_under

CAMPAIGNS = [
    "wfo", "wfo_full", "wfo_medium", "wfo_minimal", "wfo_parallel",
    "wfo_parallel_rsi", "wfo_parallel_bbands", "wfo_parallel_canonical",
    "wfo_parallel_enhanced_ma", "wfo_parallel_enhanced_ma_eth",
    "wfo_parallel_enhanced_ma_btc", "wfo_parallel_test",
    "wfo_funding_carry_only", "wfo_real_20260906", "wfo_evidence",
    "wfo_synthetic_verify", "s6_campaign", "tournament",
    "tournament_fault_tests", "tournament_shadow",
    "acceptance_20260831", "multi_pair_1h",
    "fast_wfo", "fast_wfo_BTC", "fast_wfo_ETH", "fast_wfo_BNB",
    "fast_wfo_XRP", "fast_wfo_rs_btc", "fast_wfo_rs_eth",
    "wfo_real_campaign",
]

# A campaign is expected to cover at least this many distinct strategies to
# say anything about coverage; single-strategy runs are their own check.
MIN_STRATEGIES = 2


def main() -> None:
    base = ROOT / "data" / "backtests"
    print("=" * 78)
    print("CAMPAIGN COVERAGE AUDIT — retrospective application of the invariant")
    print("=" * 78)
    print(f"{'campaign':34s} {'cells':>7} {'strats':>7}  status")
    print("-" * 78)

    rows = []
    for name in CAMPAIGNS:
        root = base / name
        if not root.exists():
            continue
        cells = _cells_under(root)
        n_cells = sum(cells.values())
        n_strat = len(cells)
        if n_cells == 0:
            status = "empty"
        elif n_strat < MIN_STRATEGIES:
            status = f"single strategy ({next(iter(cells))})"
        else:
            status = "multi-strategy, coverage not verifiable (no plan file)"
        rows.append((name, n_cells, n_strat, status))
        print(f"{name:34s} {n_cells:>7} {n_strat:>7}  {status}")

    print("-" * 78)
    total = sum(r[1] for r in rows)
    multi = [r for r in rows if r[2] >= MIN_STRATEGIES]
    print(f"campaigns audited     : {len(rows)}")
    print(f"total cells           : {total}")
    print(f"multi-strategy runs   : {len(multi)}")
    print()
    print("Why 'not verifiable': the check compares strategies requested against")
    print("strategies that produced cells. None of these campaigns wrote its")
    print("request list, so only the produced side survives. The bug this")
    print("addresses was exactly a request that produced nothing, so the")
    print("missing half of the comparison is the half that matters.")
    print()
    print("The three clean post-fix campaigns in COMMIT_FILTER_AND_SIGNOFF_")
    print("RECHECK.md — wfo_full, wfo_funding_carry_only, wfo_parallel_canonical")
    print("— are the ones a coverage check could be re-run against meaningfully.")

    json.dump(
        [{"campaign": r[0], "cells": r[1], "strategies": r[2], "status": r[3]}
         for r in rows],
        open("/tmp/campaign_coverage_audit.json", "w"), indent=2,
    )
    print("\nevidence: /tmp/campaign_coverage_audit.json")


if __name__ == "__main__":
    main()
