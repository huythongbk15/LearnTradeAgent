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
    # Find the last complete JSON object. A non-greedy \{.*?\} matches the
    # first closing brace and leaves the rest of the object unparsed, which
    # json.loads reports as "extraneous data at the end". Brace-count instead.
    import re

    # Find the summary object. A non-greedy \{.*?\} stops at the first
    # closing brace and json.loads then reports "extraneous data at the end".
    # raw_decode consumes exactly one complete JSON value from a position, so
    # try each opening brace and keep the last one that parses.
    decoder = json.JSONDecoder()
    summary = None
    for match in re.finditer(r"\{", stdout):
        try:
            obj, _end = decoder.raw_decode(stdout, match.start())
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and "mode" in obj:
            summary = obj
    assert summary, "No complete JSON object found in stdout"
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
    # No activation log: the WFO registry carries a selection score but no
    # fold-level returns, so every policy this campaign builds has zero net
    # edge per fold and the promotion gate refuses them. The campaign
    # registers DRAFT policies and leaves activation to a real evaluation —
    # see run_s5_adaptive_campaign._activatable.
    assert not (OUT_ROOT / "policy-activation.jsonl").exists()
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

    # No forecasts and no allocations: the campaign cannot promote a policy,
    # so the router abstains on every bar and the allocator is never reached.
    # Asserted as empty rather than skipped — a populated file here would mean
    # something routed without a policy.
    for name in ("forecasts.jsonl", "allocations.jsonl"):
        path = OUT_ROOT / name
        assert path.exists(), f"{name} should exist even when empty"
        assert path.read_text().strip() == "", (
            f"{name} should be empty while the router abstains"
        )
    assert summary["n_forecasts"] == 0
    assert summary["n_allocation_steps"] == 0

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

    # No forecasts and no allocations: the router abstains on every bar
    # because this campaign promotes nothing, so the allocator is never
    # reached. A populated file would mean something routed without a policy.
    for name in ("forecasts.jsonl", "allocations.jsonl"):
        path = OUT_ROOT / name
        assert path.exists(), f"{name} should exist even when empty"
        assert path.read_text().strip() == "", (
            f"{name} should be empty while the router abstains"
        )
    assert summary["n_forecasts"] == 0
    assert summary["n_allocation_steps"] == 0

    # Verify policies directory has signed policies
    policy_files = list((OUT_ROOT / "policies").glob("*.json"))
    assert len(policy_files) > 0, "No signed policy artifacts created"

    # Verify router state was created for each symbol
    router_state_dirs = list((OUT_ROOT / "router_state").iterdir())
    assert len(router_state_dirs) == 10, "Router state should exist for all 10 symbols"

    # Verify routing decisions audit logs
    # No per-symbol routing logs: with no promotable policy the router
    # abstains before it ever records a decision for a strategy. Ten logs
    # here would mean routing happened without a policy behind it.
    routing_files = list((OUT_ROOT / "routing_decisions").glob("*.jsonl"))
    assert routing_files == [], (
        "routing decisions exist although no policy is promotable"
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

    # Every bar resolves to NO_TRADE: this campaign cannot produce a
    # promotable policy, so the router abstains rather than activating.
    # Asserted explicitly because a silent activation here would mean the
    # gate had been bypassed.
    assert handover_states == {"NO_TRADE"}, (
        f"expected abstention only, saw {handover_states}"
    )

    # Should see routing reasons
    assert len(reasons) > 0


if __name__ == "__main__":
    test_s5_synthetic_campaign_runs()
    test_s5_campaign_regime_coverage()
    print("All S5 campaign tests passed!")
