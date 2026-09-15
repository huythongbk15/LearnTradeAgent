#!/usr/bin/env python3
"""Quick check of funding_carry WFO results."""
import json
import sys
from pathlib import Path

base = Path("/home/huythong/.qwenpaw/workspaces/trading/data/backtests/wfo_full")

# Check parallel summary
summary_path = base / "parallel_canonical_summary.json"
if summary_path.exists():
    with open(summary_path) as f:
        s = json.load(f)
    print("=== Parallel Canonical Summary ===")
    print(f"Strategy: {s.get('strategy')}")
    print(f"Symbol: {s.get('symbol')}")
    print(f"Timeframe: {s.get('timeframe')}")
    print(f"Verdict: {s.get('verdict')}")
    print(f"Passes gates: {s.get('passes_hard_gates')}")
    print(f"Median Sharpe: {s['aggregate_metrics']['median_test_sharpe']}")
    print(f"Median Return: {s['aggregate_metrics']['median_test_return_pct']}")
    print(f"Total test trades: {s['aggregate_metrics']['total_test_trades']}")
    print()
    print("=== Gate Results ===")
    for g in s.get('gate_results', []):
        print(f"  {g.get('gate_id', 'N/A')}: {g.get('verdict', 'N/A')} — {g.get('reason', '')}")
else:
    print("No parallel_canonical_summary.json found")

# Check individual fold reports
fc_dir = base / "funding_carry__BTCUSDT__1h"
if fc_dir.exists():
    print(f"\n=== Fold reports in {fc_dir} ===")
    for param_dir in sorted(fc_dir.iterdir()):
        if param_dir.name == "study_manifests":
            continue
        report = param_dir / "report.json"
        if report.exists():
            with open(report) as f:
                r = json.load(f)
            print(f"  {param_dir.name}:")
            print(f"    status: {r.get('status')}")
            print(f"    sharpe: {r.get('sharpe')}")
            print(f"    total_return_pct: {r.get('total_return_pct')}")
            print(f"    total_trades: {r.get('total_trades')}")
            print(f"    profit_factor: {r.get('profit_factor')}")
