"""R04 — Scope lock, holdout tracking, and campaign orchestration tests.

Verifies that:
1. CampaignScope is content-addressed (same scope → same scope_id).
2. ScopeEnforcer rejects pairs/strategies/cost scenarios not in locked scope.
3. HoldoutAccessGuard refuses re-use of touched holdout (no reset).
4. Campaign orchestrator produces phase artifacts with provenance_digest.
5. Scope lock + holdout guard bind campaign output to locked scope.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.r04,
]

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.scope_lock import (
    CampaignScope,
    HoldoutAccessGuard,
    HoldoutReuseError,
    R04_LOCKED_PAIRS,
    R04_LOCKED_STRATEGIES,
    ScopeEnforcer,
    campaign_phase_artifact,
    r04_default_scope,
)


# =============================================================================
# Test 1: CampaignScope identity
# =============================================================================


class TestCampaignScope:
    """R04.1: CampaignScope is content-addressed and frozen."""

    def test_default_scope_is_locked(self):
        scope = r04_default_scope()
        assert scope.pairs == R04_LOCKED_PAIRS
        assert scope.strategies == R04_LOCKED_STRATEGIES
        assert scope.timeframe == "1h"
        assert "1x" in scope.cost_scenarios
        assert "2x" in scope.cost_scenarios
        assert "slip_stress" in scope.cost_scenarios

    def test_same_scope_yields_same_id(self):
        s1 = r04_default_scope()
        s2 = r04_default_scope()
        # Both are content-addressed by the same fields, so same id
        assert s1.scope_id == s2.scope_id
        assert s1.scope_id.startswith("sha256:")

    def test_different_pairs_yields_different_id(self):
        s1 = r04_default_scope()
        s2 = CampaignScope(
            pairs=("XRP/USDT",),  # different
            strategies=s1.strategies,
            timeframe=s1.timeframe,
            cost_scenarios=s1.cost_scenarios,
            fold_geometry=s1.fold_geometry,
            budget=s1.budget,
        )
        assert s1.scope_id != s2.scope_id

    def test_different_strategies_yields_different_id(self):
        s1 = r04_default_scope()
        s2 = CampaignScope(
            pairs=s1.pairs,
            strategies=("rsi",),  # different
            timeframe=s1.timeframe,
            cost_scenarios=s1.cost_scenarios,
            fold_geometry=s1.fold_geometry,
            budget=s1.budget,
        )
        assert s1.scope_id != s2.scope_id

    def test_different_cost_scenarios_yields_different_id(self):
        s1 = r04_default_scope()
        s2 = CampaignScope(
            pairs=s1.pairs,
            strategies=s1.strategies,
            timeframe=s1.timeframe,
            cost_scenarios=("1x",),  # different
            fold_geometry=s1.fold_geometry,
            budget=s1.budget,
        )
        assert s1.scope_id != s2.scope_id

    def test_different_fold_geometry_yields_different_id(self):
        s1 = r04_default_scope()
        s2 = CampaignScope(
            pairs=s1.pairs,
            strategies=s1.strategies,
            timeframe=s1.timeframe,
            cost_scenarios=s1.cost_scenarios,
            fold_geometry={
                "train_months": 6,  # different
                "val_months": 3,
                "test_months": 3,
                "step_months": 3,
            },
            budget=s1.budget,
        )
        assert s1.scope_id != s2.scope_id

    def test_empty_pairs_rejected(self):
        with pytest.raises(ValueError, match="at least 1 pair"):
            CampaignScope(
                pairs=(),
                strategies=("rsi",),
                timeframe="1h",
                cost_scenarios=("1x",),
                fold_geometry={
                    "train_months": 12,
                    "val_months": 3,
                    "test_months": 3,
                    "step_months": 3,
                },
                budget={},
            )

    def test_empty_strategies_rejected(self):
        with pytest.raises(ValueError, match="at least 1 strategy"):
            CampaignScope(
                pairs=("BTC/USDT",),
                strategies=(),
                timeframe="1h",
                cost_scenarios=("1x",),
                fold_geometry={
                    "train_months": 12,
                    "val_months": 3,
                    "test_months": 3,
                    "step_months": 3,
                },
                budget={},
            )

    def test_empty_cost_scenarios_rejected(self):
        with pytest.raises(ValueError, match="at least 1 cost scenario"):
            CampaignScope(
                pairs=("BTC/USDT",),
                strategies=("rsi",),
                timeframe="1h",
                cost_scenarios=(),
                fold_geometry={
                    "train_months": 12,
                    "val_months": 3,
                    "test_months": 3,
                    "step_months": 3,
                },
                budget={},
            )

    def test_incomplete_fold_geometry_rejected(self):
        with pytest.raises(ValueError, match="missing"):
            CampaignScope(
                pairs=("BTC/USDT",),
                strategies=("rsi",),
                timeframe="1h",
                cost_scenarios=("1x",),
                fold_geometry={"train_months": 12},  # missing others
                budget={},
            )

    def test_to_dict_roundtrip(self):
        scope = r04_default_scope()
        d = scope.to_dict()
        assert d["scope_id"] == scope.scope_id
        assert d["pairs"] == list(scope.pairs)
        assert d["timeframe"] == scope.timeframe

    def test_allows_methods(self):
        scope = r04_default_scope()
        assert scope.allows_pair("BTC/USDT")
        assert not scope.allows_pair("UNKNOWN/USDT")
        assert scope.allows_strategy("rsi")
        assert not scope.allows_strategy("unknown")
        assert scope.allows_cost_scenario("1x")
        assert not scope.allows_cost_scenario("unknown")

    def test_is_within_budget(self):
        scope = r04_default_scope()
        assert scope.is_within_budget(runtime_seconds=10.0, cell_count=5)
        # Exceeds max runtime
        assert not scope.is_within_budget(runtime_seconds=10**9, cell_count=5)
        # Exceeds max cells
        assert not scope.is_within_budget(runtime_seconds=10.0, cell_count=10**9)


# =============================================================================
# Test 2: ScopeEnforcer
# =============================================================================


class TestScopeEnforcer:
    """R04.2: ScopeEnforcer rejects deviations from locked scope."""

    def test_accepts_locked_pair(self):
        scope = r04_default_scope()
        enforcer = ScopeEnforcer(scope)
        assert enforcer.check_pair("BTC/USDT") is True
        assert enforcer.is_clean()

    def test_rejects_unknown_pair(self):
        scope = r04_default_scope()
        enforcer = ScopeEnforcer(scope)
        assert enforcer.check_pair("UNKNOWN/USDT") is False
        assert not enforcer.is_clean()
        assert any(v.kind == "pair" for v in enforcer.violations)

    def test_rejects_unknown_strategy(self):
        scope = r04_default_scope()
        enforcer = ScopeEnforcer(scope)
        assert enforcer.check_strategy("unknown") is False
        assert any(v.kind == "strategy" for v in enforcer.violations)

    def test_rejects_unknown_cost(self):
        scope = r04_default_scope()
        enforcer = ScopeEnforcer(scope)
        assert enforcer.check_cost_scenario("100x") is False
        assert any(v.kind == "cost" for v in enforcer.violations)

    def test_runtime_violation_recorded(self):
        scope = r04_default_scope()
        enforcer = ScopeEnforcer(scope)
        # max_runtime_seconds default is 3600
        assert enforcer.check_runtime(10.0) is True
        assert enforcer.check_runtime(10**9) is False
        assert any(v.kind == "budget" for v in enforcer.violations)

    def test_violations_accumulate(self):
        scope = r04_default_scope()
        enforcer = ScopeEnforcer(scope)
        enforcer.check_pair("UNKNOWN")
        enforcer.check_strategy("unknown")
        enforcer.check_cost_scenario("100x")
        kinds = {v.kind for v in enforcer.violations}
        assert kinds == {"pair", "strategy", "cost"}


# =============================================================================
# Test 3: HoldoutAccessGuard
# =============================================================================


class TestHoldoutAccessGuard:
    """R04.3: HoldoutAccessGuard refuses re-use of touched holdout."""

    def test_first_access_succeeds(self):
        guard = HoldoutAccessGuard()
        rec = guard.request_access("BTC/USDT", "rsi", fold_count=1)
        assert rec.pair == "BTC/USDT"
        assert rec.strategy == "rsi"
        assert rec.fold_count == 1
        assert guard.has_touched("BTC/USDT", "rsi")

    def test_second_access_raises(self):
        guard = HoldoutAccessGuard()
        guard.request_access("BTC/USDT", "rsi", fold_count=1)
        with pytest.raises(HoldoutReuseError, match="already touched"):
            guard.request_access("BTC/USDT", "rsi", fold_count=1)

    def test_different_pair_can_touch(self):
        guard = HoldoutAccessGuard()
        guard.request_access("BTC/USDT", "rsi", fold_count=1)
        rec2 = guard.request_access("ETH/USDT", "rsi", fold_count=1)
        assert rec2.pair == "ETH/USDT"
        # Both are tracked
        assert guard.has_touched("BTC/USDT", "rsi")
        assert guard.has_touched("ETH/USDT", "rsi")

    def test_different_strategy_can_touch(self):
        guard = HoldoutAccessGuard()
        guard.request_access("BTC/USDT", "rsi", fold_count=1)
        rec2 = guard.request_access("BTC/USDT", "ma_adx", fold_count=1)
        assert rec2.strategy == "ma_adx"
        assert guard.has_touched("BTC/USDT", "rsi")
        assert guard.has_touched("BTC/USDT", "ma_adx")

    def test_record_outcome_after_access(self):
        guard = HoldoutAccessGuard()
        guard.request_access("BTC/USDT", "rsi", fold_count=1)
        guard.record_outcome("BTC/USDT", "rsi", outcome="NO_TRADE")
        touched = guard.to_dict()["touched"]
        assert len(touched) == 1
        assert touched[0]["outcome"] == "NO_TRADE"

    def test_record_outcome_without_access_raises(self):
        guard = HoldoutAccessGuard()
        with pytest.raises(KeyError, match="No active holdout"):
            guard.record_outcome("BTC/USDT", "rsi", outcome="NO_TRADE")

    def test_touched_dict_contains_all(self):
        guard = HoldoutAccessGuard()
        guard.request_access("BTC/USDT", "rsi", fold_count=1)
        guard.request_access("ETH/USDT", "ma_adx", fold_count=1)
        d = guard.to_dict()
        assert len(d["touched"]) == 2
        pairs = {(t["pair"], t["strategy"]) for t in d["touched"]}
        assert pairs == {("BTC/USDT", "rsi"), ("ETH/USDT", "ma_adx")}


# =============================================================================
# Test 4: Campaign phase artifact
# =============================================================================


class TestPhaseArtifact:
    """R04.4: campaign_phase_artifact binds phase to scope + provenance."""

    def test_phase_artifact_binds_scope_id(self):
        scope = r04_default_scope()
        result = campaign_phase_artifact(
            phase="smoke",
            scope=scope,
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert result.scope_id == scope.scope_id
        assert result.phase == "smoke"

    def test_phase_artifact_includes_enforcer_violations(self):
        scope = r04_default_scope()
        enforcer = ScopeEnforcer(scope)
        enforcer.check_pair("UNKNOWN")
        result = campaign_phase_artifact(
            phase="scope",
            scope=scope,
            started_at="2026-01-01T00:00:00+00:00",
            enforcer=enforcer,
        )
        assert len(result.scope_violations) == 1
        assert result.scope_violations[0]["kind"] == "pair"

    def test_phase_artifact_includes_holdout_accesses(self):
        scope = r04_default_scope()
        guard = HoldoutAccessGuard()
        guard.request_access("BTC/USDT", "rsi", fold_count=1)
        guard.record_outcome("BTC/USDT", "rsi", outcome="NO_TRADE")
        result = campaign_phase_artifact(
            phase="final",
            scope=scope,
            started_at="2026-01-01T00:00:00+00:00",
            holdout_guard=guard,
        )
        assert len(result.holdout_accesses) == 1
        assert result.holdout_accesses[0]["pair"] == "BTC/USDT"
        assert result.holdout_accesses[0]["outcome"] == "NO_TRADE"

    def test_phase_artifact_to_dict(self):
        scope = r04_default_scope()
        result = campaign_phase_artifact(
            phase="scope",
            scope=scope,
            started_at="2026-01-01T00:00:00+00:00",
            finished_at="2026-01-01T01:00:00+00:00",
            runtime_seconds=3600.0,
            pairs_run=3,
            strategies_run=3,
            cells_executed=18,
            verdicts={"rsi@BTC/USDT": "NO_TRADE"},
            provenance_digest="sha256:abc",
        )
        d = result.to_dict()
        assert d["phase"] == "scope"
        assert d["scope_id"] == scope.scope_id
        assert d["pairs_run"] == 3
        assert d["strategies_run"] == 3
        assert d["cells_executed"] == 18
        assert d["verdicts"]["rsi@BTC/USDT"] == "NO_TRADE"
        assert d["provenance_digest"] == "sha256:abc"


# =============================================================================
# Test 5: Scope change without reset (audit trail)
# =============================================================================


class TestScopeImmutability:
    """R04.5: CampaignScope is frozen — cannot be silently widened."""

    def test_scope_is_frozen_dataclass(self):
        from dataclasses import FrozenInstanceError

        scope = r04_default_scope()
        with pytest.raises(FrozenInstanceError):
            scope.pairs = ("XRP/USDT",)  # type: ignore[misc]

    def test_cannot_extend_pairs_silently(self):
        """A consumer that wants to extend the pair list must create a new
        scope (and a new scope_id) — the old scope is immutable."""
        from dataclasses import replace

        scope = r04_default_scope()
        new_pairs = scope.pairs + ("XRP/USDT",)
        new_scope = replace(scope, pairs=new_pairs)
        assert new_scope.scope_id != scope.scope_id
        # Old scope unchanged
        assert "XRP/USDT" not in scope.pairs


# =============================================================================
# Test 6: End-to-end R04 campaign orchestrator (smoke phase only)
# =============================================================================


class TestCampaignOrchestratorSmoke:
    """R04.6: run_s3_campaign.py smoke phase runs end-to-end with synthetic data."""

    def test_smoke_phase_runs_and_produces_summary(self, tmp_path: Path):
        """The R04 campaign script's smoke phase runs and produces a
        campaign_summary.json with the expected structure."""
        import subprocess

        result = subprocess.run(
            [
                "python",
                "scripts/run_s3_campaign.py",
                "--phase",
                "smoke",
                "--synthetic",
                "--n-bars",
                "200",
                "--out",
                str(tmp_path / "smoke"),
            ],
            capture_output=True,
            text=True,
            cwd=ROOT,
            timeout=60,
        )
        # The campaign may have errors due to small synthetic data, but the
        # campaign_summary.json should still be written
        summary_path = tmp_path / "smoke" / "campaign_summary.json"
        assert summary_path.exists(), (
            f"campaign_summary.json not created. stdout={result.stdout[:500]} "
            f"stderr={result.stderr[:500]}"
        )
        import json

        d = json.loads(summary_path.read_text())
        assert "scope" in d
        assert "phases" in d
        assert "enforcer_clean" in d
        assert "holdout_accesses" in d
        # Smoke phase: 1 pair, 1 strategy, no holdout
        smoke_phase = next(p for p in d["phases"] if p["phase"] == "smoke")
        assert smoke_phase["pairs_run"] == 1
        assert smoke_phase["strategies_run"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
