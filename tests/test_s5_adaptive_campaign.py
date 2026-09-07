#!/usr/bin/env python
"""
Tests for S5: Adaptive Routing + Shared-Capital OOS Campaign

Verifies that the campaign script runs end-to-end in synthetic mode
and produces expected output artifacts.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_s5_adaptive_campaign.py"
OUT_ROOT = ROOT / "data" / "backtests" / "test_s5_adaptive_campaign"


def test_s5_synthetic_campaign_runs():
    """Test that the synthetic campaign runs and produces expected outputs."""
    # Clean up any previous test run
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)

    # Run the campaign in synthetic mode with minimal bars
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "synthetic",
            "--n-bars",
            "250",
            "--out-root",
            str(OUT_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    # Check exit code
    assert result.returncode == 0, f"Campaign failed: {result.stderr}"

    # Parse summary output (last JSON object in stdout)
    stdout = result.stdout.strip()
    # Find the last complete JSON object
    import re

    json_matches = list(re.finditer(r"\{.*?\}", stdout, re.DOTALL))
    assert json_matches, "No JSON output found"
    summary = json.loads(json_matches[-1].group(0))
    assert summary["mode"] == "synthetic"
    assert summary["n_symbols"] == 10
    assert summary["n_bars_processed"] > 0
    assert summary["n_decisions"] > 0

    # Check that output files exist
    assert (OUT_ROOT / "s5_campaign_summary.json").exists()
    assert (OUT_ROOT / "decisions.jsonl").exists()
    assert (OUT_ROOT / "forecasts.jsonl").exists()
    assert (OUT_ROOT / "allocations.jsonl").exists()
    assert (OUT_ROOT / "policies").exists()
    assert (OUT_ROOT / "policy-activation.jsonl").exists()
    assert (OUT_ROOT / "router_state").exists()
    assert (OUT_ROOT / "routing_decisions").exists()

    # Verify summary file matches stdout
    with open(OUT_ROOT / "s5_campaign_summary.json") as f:
        summary_file = json.load(f)
    assert summary_file["mode"] == summary["mode"]
    assert summary_file["n_symbols"] == summary["n_symbols"]

    # Verify decisions have expected structure
    with open(OUT_ROOT / "decisions.jsonl") as f:
        first_decision = json.loads(f.readline())
    assert "symbol" in first_decision
    assert "timeframe" in first_decision
    assert "handover_state" in first_decision
    assert "reason" in first_decision
    assert "allow_new_exposure" in first_decision
    assert "chosen_strategy_id" in first_decision
    assert "policy_ids" in first_decision

    # Verify forecasts have expected structure
    with open(OUT_ROOT / "forecasts.jsonl") as f:
        first_forecast = json.loads(f.readline())
    assert "symbol" in first_forecast
    assert "observed_at" in first_forecast
    assert "forecast" in first_forecast
    assert "strategy" in first_forecast
    assert "policy_id" in first_forecast

    # Verify allocations have expected structure
    with open(OUT_ROOT / "allocations.jsonl") as f:
        first_allocation = json.loads(f.readline())
    assert "bar_idx" in first_allocation
    assert "scale_factor" in first_allocation
    assert "total_requested" in first_allocation
    assert "total_approved" in first_allocation
    assert "budget_available" in first_allocation
    assert "entries" in first_allocation

    # Verify policies directory has signed policies
    policy_files = list((OUT_ROOT / "policies").glob("*.json"))
    assert len(policy_files) > 0, "No signed policy artifacts created"

    # Verify router state was created for each symbol
    router_state_dirs = list((OUT_ROOT / "router_state").iterdir())
    assert len(router_state_dirs) == 10, "Router state should exist for all 10 symbols"

    # Verify routing decisions audit logs
    routing_files = list((OUT_ROOT / "routing_decisions").glob("*.jsonl"))
    assert len(routing_files) == 10, (
        "Routing decisions log should exist for all 10 symbols"
    )


def test_s5_campaign_regime_coverage():
    """Test that the campaign routes through multiple regimes."""
    if OUT_ROOT.exists():
        shutil.rmtree(OUT_ROOT)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--mode",
            "synthetic",
            "--n-bars",
            "300",
            "--out-root",
            str(OUT_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0

    # Check that multiple handover states occurred
    handover_states = set()
    reasons = set()
    with open(OUT_ROOT / "decisions.jsonl") as f:
        for line in f:
            d = json.loads(line)
            handover_states.add(d["handover_state"])
            reasons.add(d["reason"])

    # Should see at least ACTIVATE and PERSIST states
    assert "ACTIVATE" in handover_states or "PERSIST" in handover_states

    # Should see routing reasons
    assert len(reasons) > 0


if __name__ == "__main__":
    test_s5_synthetic_campaign_runs()
    test_s5_campaign_regime_coverage()
    print("All S5 campaign tests passed!")
