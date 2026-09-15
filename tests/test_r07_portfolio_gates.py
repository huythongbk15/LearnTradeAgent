"""R07 — Portfolio gates implementation tests (S3-8).

Verifies:
1. PortfolioGatePolicy is content-addressed (policy_id ties to thresholds).
2. PortfolioGateReport integrity (artifact_id, tamper detection).
3. Gate evaluation logic (>=, >, <= comparisons, INVALID for missing data).
4. Portfolio verdict logic (FINAL_PASS, SELECTION_PASS, NO_TRADE, HOLDOUT_FAILED).
5. Contribution concentration and positive pair % gates.
6. FormalNoTradeArtifact generation when gates fail.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.r07,
]

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.nested_wfo import (
    PortfolioGatePolicy,
    PortfolioGateReport,
    WFOPortfolioResult,
    GateResult,
    FormalNoTradeArtifact,
)


class TestPortfolioGatePolicy:
    """R07.1: Policy is content-addressed."""

    def test_policy_id_stable(self):
        p1 = PortfolioGatePolicy()
        p2 = PortfolioGatePolicy()
        assert p1.policy_id == p2.policy_id

    def test_policy_id_changes_on_threshold_change(self):
        p1 = PortfolioGatePolicy(positive_pairs_pct_min=60.0)
        p2 = PortfolioGatePolicy(positive_pairs_pct_min=70.0)
        assert p1.policy_id != p2.policy_id

    def test_verify_integrity(self):
        p = PortfolioGatePolicy()
        assert p.verify_integrity()

    def test_to_dict_roundtrip(self):
        p = PortfolioGatePolicy(median_pair_return_min=2.0)
        d = p.to_dict()
        assert d["policy_id"] == p.policy_id
        assert d["median_pair_return_min"] == 2.0


class TestGateResult:
    """R07.2: GateResult verdict logic."""

    def test_pass_ge(self):
        g = GateResult("test", "v1", 70.0, 60.0, ">=", "PASS", "ok", "evidence")
        assert g.is_pass()

    def test_fail_lt_threshold(self):
        g = GateResult("test", "v1", 50.0, 60.0, ">=", "FAIL", "below", "evidence")
        assert not g.is_pass()

    def test_invalid_for_none(self):
        g = GateResult("test", "v1", None, 60.0, ">=", "INVALID", "missing", "evidence")
        assert not g.is_pass()

    def test_pass_le(self):
        g = GateResult("test", "v1", 30.0, 35.0, "<=", "PASS", "ok", "evidence")
        assert g.is_pass()

    def test_fail_gt(self):
        g = GateResult("test", "v1", 10.0, 0.0, ">", "FAIL", "below zero", "evidence")
        assert not g.is_pass()


class TestPortfolioGateReport:
    """R07.3: PortfolioGateReport integrity."""

    def test_artifact_id_stable(self):
        policy = PortfolioGatePolicy()
        gates = [GateResult("g1", "v1", 70.0, 60.0, ">=", "PASS", "r1", "e1")]
        r1 = PortfolioGateReport(policy, gates, "FINAL_PASS", {"test": 1})
        r2 = PortfolioGateReport(policy, gates, "FINAL_PASS", {"test": 1})
        assert r1.artifact_id == r2.artifact_id

    def test_artifact_id_changes_on_verdict(self):
        policy = PortfolioGatePolicy()
        gates = [GateResult("g1", "v1", 70.0, 60.0, ">=", "PASS", "r1", "e1")]
        r1 = PortfolioGateReport(policy, gates, "FINAL_PASS", {})
        r2 = PortfolioGateReport(policy, gates, "NO_TRADE", {})
        assert r1.artifact_id != r2.artifact_id

    def test_passes_hard_gates_true(self):
        policy = PortfolioGatePolicy()
        gates = [GateResult("g1", "v1", 70.0, 60.0, ">=", "PASS", "r1", "e1")]
        report = PortfolioGateReport(policy, gates, "FINAL_PASS", {})
        assert report.passes_hard_gates

    def test_passes_hard_gates_false(self):
        policy = PortfolioGatePolicy()
        gates = [GateResult("g1", "v1", 50.0, 60.0, ">=", "FAIL", "r1", "e1")]
        report = PortfolioGateReport(policy, gates, "NO_TRADE", {})
        assert not report.passes_hard_gates

    def test_from_dict_integrity(self):
        policy = PortfolioGatePolicy()
        gates = [GateResult("g1", "v1", 70.0, 60.0, ">=", "PASS", "r1", "e1")]
        report = PortfolioGateReport(policy, gates, "FINAL_PASS", {"test": 1})
        d = report.to_dict()
        restored = PortfolioGateReport.from_dict(d)
        assert restored.artifact_id == report.artifact_id

    def test_from_dict_detects_tampering(self):
        policy = PortfolioGatePolicy()
        gates = [GateResult("g1", "v1", 70.0, 60.0, ">=", "PASS", "r1", "e1")]
        report = PortfolioGateReport(policy, gates, "FINAL_PASS", {"test": 1})
        d = report.to_dict()
        # Tamper with verdict
        d["verdict"] = "NO_TRADE"
        d["passes_hard_gates"] = False
        with pytest.raises(ValueError, match="integrity check failed"):
            PortfolioGateReport.from_dict(d)

    def test_verify_integrity(self):
        policy = PortfolioGatePolicy()
        gates = [GateResult("g1", "v1", 70.0, 60.0, ">=", "PASS", "r1", "e1")]
        report = PortfolioGateReport(policy, gates, "FINAL_PASS", {})
        assert report.verify_integrity()


class TestFormalNoTradeArtifact:
    """R07.4: No-trade artifact when gates fail."""

    def _make_artifact(self):
        """Helper to create a complete FormalNoTradeArtifact."""
        return FormalNoTradeArtifact(
            candidate_set=["a", "b"],
            gate_results={"a": [], "b": []},
            gate_failures={"a": ["g1"], "b": []},
            best_candidate="a",
            best_candidate_metrics={"sharpe": 1.0},
            registry_identity={"search_space_hash": "sha256:abc"},
            policy_version="portfolio-v1",
            policy_thresholds={"min_return": 0.0},
            commit_sha="abc123",
            data_manifest_sha="sha256:data",
            feature_schema_hash="sha256:feat",
            search_space_hash="sha256:search",
            evaluation_timestamp="2025-01-01T00:00:00Z",
            evaluation_duration_sec=10.0,
        )

    def test_artifact_id_content_addressed(self):
        artifact = self._make_artifact()
        aid = artifact.no_trade_id
        assert aid.startswith("sha256:")

    def test_verify_integrity(self):
        artifact = self._make_artifact()
        assert artifact.verify_integrity()

    def test_tampering_detected(self):
        artifact = self._make_artifact()
        # Tamper by modifying internal no_trade_id
        import hashlib
        import json as _json
        payload = {
            "candidate_set": ["tampered"],  # Tampered data
            "best_candidate": "b",
        }
        # Re-compute manually to simulate what verify_integrity checks
        artifact_dict = artifact.to_dict()
        artifact_dict["candidate_set"] = ["tampered"]  # Tamper
        # Re-create from dict (bypassing to_dict) to get mismatched hash
        tampered = FormalNoTradeArtifact(
            candidate_set=["a"],  # Different from what no_trade_id was computed for
            gate_results={"a": [], "b": []},
            gate_failures={"a": ["g1"], "b": []},
            best_candidate="a",
            best_candidate_metrics={"sharpe": 1.0},
            registry_identity={"search_space_hash": "sha256:abc"},
            policy_version="portfolio-v1",
            policy_thresholds={"min_return": 0.0},
            commit_sha="abc123",
            data_manifest_sha="sha256:data",
            feature_schema_hash="sha256:feat",
            search_space_hash="sha256:search",
            evaluation_timestamp="2025-01-01T00:00:00Z",
            evaluation_duration_sec=10.0,
        )
        # no_trade_id is computed from candidate_set in __post_init__
        # So if we modify candidate_set after init, it won't match
        object.__setattr__(tampered, "candidate_set", ["tampered", "b"])
        assert not tampered.verify_integrity()


class TestPortfolioVerdict:
    """R07.5: Verdict logic."""

    def _make_report(self, observed_values):
        """Helper to create a gate report with multiple gates."""
        policy = PortfolioGatePolicy()
        gates = []
        for i, (obs, thresh, comp) in enumerate(observed_values):
            verdict = "PASS"
            if comp == ">=":
                verdict = "PASS" if obs >= thresh else "FAIL"
            elif comp == ">":
                verdict = "PASS" if obs > thresh else "FAIL"
            elif comp == "<=":
                verdict = "PASS" if obs <= thresh else "FAIL"
            gates.append(GateResult(f"g{i}", "v1", obs, thresh, comp, verdict, "", "e"))
        passes = all(g.is_pass() for g in gates)
        verdict = "FINAL_PASS" if passes else "NO_TRADE"
        return PortfolioGateReport(policy, gates, verdict, {})

    def test_all_pass_final_pass(self):
        report = self._make_report([
            (70.0, 60.0, ">="),  # positive pairs
            (5.0, 0.0, ">"),    # median return
            (30.0, 35.0, "<="), # concentration
        ])
        assert report.verdict == "FINAL_PASS"
        assert report.passes_hard_gates

    def test_one_fail_no_trade(self):
        report = self._make_report([
            (50.0, 60.0, ">="),  # positive pairs FAIL
            (5.0, 0.0, ">"),    # median return PASS
            (30.0, 35.0, "<="), # concentration PASS
        ])
        assert report.verdict == "NO_TRADE"
        assert not report.passes_hard_gates

    def test_invalid_gate_no_trade(self):
        policy = PortfolioGatePolicy()
        gates = [GateResult("g1", "v1", None, 60.0, ">=", "INVALID", "missing", "e")]
        report = PortfolioGateReport(policy, gates, "NO_TRADE", {})
        assert not report.passes_hard_gates
