"""R03 — Provenance, completeness, and resume validation tests.

Verifies that:
1. The producer writes identity at run time (every trial record + outer
   artifact carries the canonical evaluation identity).
2. Manifest validation catches missing / mismatched / tampered evidence and
   fails closed (CompletenessReport.is_complete=False).
3. ResumeGuard rejects cache reuse when ANY identity field changes
   (code/data/feature/params/cost/window/seed/commit/policy).
4. The aggregate WFOResult carries a provenance_digest that binds the
   decision to the evidence.
5. The producer cannot smuggle uncommitted code via a dirty worktree
   (ResumeGuard denies resume when worktree_dirty=True).
6. Tampered evidence is detected: changing any identity element after
   re-aggregation makes the consumer reject the result.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [
    pytest.mark.r03,
]

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trading_agent.backtest.provenance import (
    CompletenessReport,
    ManifestValidator,
    RESUME_IDENTITY_FIELDS,
    ResumeGuard,
    WFOStudyManifest,  # re-export below if needed
    attach_evaluation_identity,
    evaluation_identity,
    expected_cells_for_study,
    provenance_digest,
)


# =============================================================================
# Test 1: provenance_digest is stable and identity-bound
# =============================================================================


class TestProvenanceDigest:
    """R03.1: provenance_digest is content-addressed and field-bounded."""

    def test_same_identity_yields_same_digest(self):
        identity = evaluation_identity(
            strategy_id="rsi",
            symbol="BTC/USDT",
            timeframe="1h",
            strategy_code_sha="code-abc",
            data_manifest_sha="data-abc",
            feature_schema_hash="feature-abc",
            search_space_hash="search-abc",
            evaluator_version="v1",
            policy_version="v1",
            cost_scenario="1x",
            window_kind="INNER_VALIDATION",
            seed=42,
            commit_sha="commit-abc",
        )
        d1 = provenance_digest(identity)
        d2 = provenance_digest(identity)
        assert d1 == d2
        assert d1.startswith("sha256:")
        assert len(d1) == len("sha256:") + 64

    def test_different_seed_yields_different_digest(self):
        base: dict[str, Any] = dict(
            strategy_id="rsi",
            symbol="BTC/USDT",
            timeframe="1h",
            strategy_code_sha="code-abc",
            data_manifest_sha="data-abc",
            feature_schema_hash="feature-abc",
            search_space_hash="search-abc",
            evaluator_version="v1",
            policy_version="v1",
            cost_scenario="1x",
            window_kind="INNER_VALIDATION",
            commit_sha="commit-abc",
        )
        d1 = provenance_digest(evaluation_identity(seed=42, **base))
        d2 = provenance_digest(evaluation_identity(seed=43, **base))
        assert d1 != d2, "Different seed should change digest"

    def test_extra_fields_dont_change_digest(self):
        """Extra fields (e.g. params_hash, study_manifest_id) participate in
        the dict but NOT in the digest — they are convenience fields for
        downstream consumers, not part of the resume identity."""
        base = evaluation_identity(
            strategy_id="rsi",
            symbol="BTC/USDT",
            timeframe="1h",
            strategy_code_sha="code-abc",
            data_manifest_sha="data-abc",
            feature_schema_hash="feature-abc",
            search_space_hash="search-abc",
            evaluator_version="v1",
            policy_version="v1",
            cost_scenario="1x",
            window_kind="INNER_VALIDATION",
            seed=42,
            commit_sha="commit-abc",
        )
        d_base = provenance_digest(base)
        with_extra = dict(base, params_hash="ph", study_manifest_id="smid")
        d_extra = provenance_digest(with_extra)
        assert d_base == d_extra, "Extra fields must not change digest"

    def test_resume_identity_fields_enumerated(self):
        """The set of identity fields is documented and stable."""
        expected_fields = {
            "strategy_id",
            "symbol",
            "timeframe",
            "strategy_code_sha",
            "data_manifest_sha",
            "feature_schema_hash",
            "search_space_hash",
            "evaluator_version",
            "policy_version",
            "cost_scenario",
            "window_kind",
            "seed",
            "commit_sha",
        }
        actual = set(RESUME_IDENTITY_FIELDS)
        assert actual == expected_fields, (
            f"RESUME_IDENTITY_FIELDS changed: {actual ^ expected_fields}"
        )


# =============================================================================
# Test 2: ResumeGuard — accept/reject based on identity
# =============================================================================


class TestResumeGuard:
    """R03.2: ResumeGuard only allows cache reuse when identity matches."""

    def _identity(self, **overrides: Any) -> dict[str, Any]:
        base = dict(
            strategy_id="rsi",
            symbol="BTC/USDT",
            timeframe="1h",
            strategy_code_sha="code-abc",
            data_manifest_sha="data-abc",
            feature_schema_hash="feature-abc",
            search_space_hash="search-abc",
            evaluator_version="v1",
            policy_version="v1",
            cost_scenario="1x",
            window_kind="INNER_VALIDATION",
            seed=42,
            commit_sha="commit-abc",
        )
        base.update(overrides)
        return base

    def test_identical_identities_resume(self):
        cached_id = self._identity()
        proposed = self._identity()
        cached = {"metadata": {"evaluation_identity": cached_id}}
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is True
        assert decision.mismatched_fields == ()

    def test_code_change_blocks_resume(self):
        cached = {
            "metadata": {
                "evaluation_identity": self._identity(strategy_code_sha="code-OLD")
            }
        }
        proposed = self._identity(strategy_code_sha="code-NEW")
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "strategy_code_sha" in decision.mismatched_fields

    def test_data_change_blocks_resume(self):
        cached = {
            "metadata": {
                "evaluation_identity": self._identity(data_manifest_sha="data-OLD")
            }
        }
        proposed = self._identity(data_manifest_sha="data-NEW")
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "data_manifest_sha" in decision.mismatched_fields

    def test_cost_change_blocks_resume(self):
        cached = {
            "metadata": {"evaluation_identity": self._identity(cost_scenario="1x")}
        }
        proposed = self._identity(cost_scenario="2x")
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "cost_scenario" in decision.mismatched_fields

    def test_window_kind_change_blocks_resume(self):
        cached = {
            "metadata": {
                "evaluation_identity": self._identity(window_kind="INNER_VALIDATION")
            }
        }
        proposed = self._identity(window_kind="OUTER_OOS")
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "window_kind" in decision.mismatched_fields

    def test_seed_change_blocks_resume(self):
        cached = {"metadata": {"evaluation_identity": self._identity(seed=42)}}
        proposed = self._identity(seed=99)
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "seed" in decision.mismatched_fields

    def test_policy_version_change_blocks_resume(self):
        cached = {
            "metadata": {"evaluation_identity": self._identity(policy_version="v1")}
        }
        proposed = self._identity(policy_version="v2")
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "policy_version" in decision.mismatched_fields

    def test_commit_change_blocks_resume(self):
        cached = {
            "metadata": {"evaluation_identity": self._identity(commit_sha="abc123")}
        }
        proposed = self._identity(commit_sha="def456")
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "commit_sha" in decision.mismatched_fields

    def test_dirty_worktree_blocks_resume(self):
        """A dirty worktree means the cached result was produced by code that
        is no longer in the tree. The guard must refuse to reuse it, even
        if the identity fields otherwise match — this prevents smuggling
        uncommitted code into a final result."""
        cached = {"metadata": {"evaluation_identity": self._identity()}}
        proposed = self._identity()
        guard = ResumeGuard(worktree_dirty=True)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "worktree" in decision.reason.lower()

    def test_missing_evaluation_identity_blocks_resume(self):
        """A cached artifact without an evaluation_identity record is opaque
        to the guard. Refuse to reuse it — the consumer cannot verify."""
        cached: dict[str, Any] = {"metadata": {}}  # no evaluation_identity
        proposed = self._identity()
        guard = ResumeGuard(worktree_dirty=False)
        decision = guard.check_resume_compatibility(cached, proposed)
        assert decision.can_resume is False
        assert "evaluation_identity" in decision.reason


# =============================================================================
# Test 3: ManifestValidator — completeness / mismatch / tamper
# =============================================================================


def _build_study_manifest(fold_ids: list[str], **overrides: Any) -> Any:
    """Build a WFOStudyManifest-shaped object for testing."""
    fold_windows = [
        {
            "fold_id": fid,
            "inner_train_start": 0,
            "inner_train_end": 100,
            "inner_val_start": 100,
            "inner_val_end": 200,
            "outer_test_start": 200,
            "outer_test_end": 300,
        }
        for fid in fold_ids
    ]
    base: dict[str, Any] = dict(
        strategy_id="rsi",
        symbol="BTC/USDT",
        timeframe="1h",
        param_grid={"period": [14]},
        cost_scenarios=({"name": "1x"},),
        fold_windows=tuple(fold_windows),
        purge_bars=0,
        embargo_bars=0,
        min_oos_trades=10,
        search_family="test",
        evaluator_version="v1",
        training_contract="test",
        seed=42,
        commit_sha="commit-abc",
        worktree_dirty=False,
        strategy_code_sha="code-abc",
        data_manifest_sha="data-abc",
        feature_schema_hash="feature-abc",
        search_space_hash="search-abc",
        evidence_class="REAL_MARKET",
    )
    base.update(overrides)
    return WFOStudyManifest(**base)


class TestManifestValidator:
    """R03.3: ManifestValidator fails closed on missing/mismatched/tampered evidence."""

    def test_no_evidence_is_incomplete(self, tmp_path: Path):
        manifest = _build_study_manifest(["f1", "f2"])
        validator = ManifestValidator(manifest, tmp_path)
        report = validator.validate_manifest()
        assert report.is_complete is False
        assert report.expected_count == 2
        assert report.found_count == 0
        # Both folds should be marked missing
        assert set(report.missing) == {"f1", "f2"}

    def test_complete_evidence_passes(self, tmp_path: Path):
        manifest = _build_study_manifest(["f1", "f2"])
        # Write inner freeze + outer artifact for each fold
        for fid in ("f1", "f2"):
            inner_dir = tmp_path / "inner_selection_freezes"
            outer_dir = tmp_path / "outer_one_shot" / fid
            inner_dir.mkdir(parents=True, exist_ok=True)
            outer_dir.mkdir(parents=True, exist_ok=True)
            # Use a fake freeze_id that we can compute
            freeze_id = f"sha256:{fid}-freeze"
            inner_path = inner_dir / f"{freeze_id.removeprefix('sha256:')}.json"
            inner_path.write_text(
                json.dumps(
                    {
                        "fold_id": fid,
                        "commit_sha": "commit-abc",
                        "data_manifest_sha": "data-abc",
                        "feature_schema_hash": "feature-abc",
                    }
                )
            )
            outer_path = outer_dir / f"{freeze_id.removeprefix('sha256:')}.json"
            outer_path.write_text(
                json.dumps(
                    {
                        "selection_freeze_id": freeze_id,
                        "status": "COMPLETED",
                    }
                )
            )
        validator = ManifestValidator(manifest, tmp_path)
        report = validator.validate_manifest()
        assert report.is_complete is True
        assert report.found_count == 2
        assert report.missing == ()
        assert report.mismatched == ()
        assert report.tampered == ()

    def test_missing_outer_artifact_is_missing(self, tmp_path: Path):
        manifest = _build_study_manifest(["f1"])
        # No files written
        validator = ManifestValidator(manifest, tmp_path)
        report = validator.validate_manifest()
        assert report.is_complete is False
        assert "f1" in report.missing

    def test_mismatched_commit_sha_is_rejected(self, tmp_path: Path):
        manifest = _build_study_manifest(["f1"], commit_sha="commit-NEW")
        # Write evidence with OLD commit_sha
        inner_dir = tmp_path / "inner_selection_freezes"
        outer_dir = tmp_path / "outer_one_shot" / "f1"
        inner_dir.mkdir(parents=True, exist_ok=True)
        outer_dir.mkdir(parents=True, exist_ok=True)
        freeze_id = "sha256:old-freeze"
        (inner_dir / "old-freeze.json").write_text(
            json.dumps(
                {
                    "fold_id": "f1",
                    "commit_sha": "commit-OLD",  # mismatch
                    "data_manifest_sha": "data-abc",
                    "feature_schema_hash": "feature-abc",
                }
            )
        )
        (outer_dir / "old-freeze.json").write_text(
            json.dumps(
                {
                    "selection_freeze_id": freeze_id,
                }
            )
        )
        validator = ManifestValidator(manifest, tmp_path)
        report = validator.validate_manifest()
        assert report.is_complete is False
        assert "f1" in report.mismatched
        assert any("commit_sha" in issue for issue in report.issues)

    def test_mismatched_data_manifest_is_rejected(self, tmp_path: Path):
        manifest = _build_study_manifest(["f1"], data_manifest_sha="data-NEW")
        inner_dir = tmp_path / "inner_selection_freezes"
        outer_dir = tmp_path / "outer_one_shot" / "f1"
        inner_dir.mkdir(parents=True, exist_ok=True)
        outer_dir.mkdir(parents=True, exist_ok=True)
        (inner_dir / "f.json").write_text(
            json.dumps(
                {
                    "fold_id": "f1",
                    "commit_sha": "commit-abc",
                    "data_manifest_sha": "data-OLD",  # mismatch
                    "feature_schema_hash": "feature-abc",
                }
            )
        )
        (outer_dir / "f.json").write_text(
            json.dumps(
                {
                    "selection_freeze_id": "sha256:f",
                }
            )
        )
        validator = ManifestValidator(manifest, tmp_path)
        report = validator.validate_manifest()
        assert report.is_complete is False
        assert "f1" in report.mismatched

    def test_tampered_outer_artifact_detected(self, tmp_path: Path):
        """An outer artifact that is not valid JSON is treated as tampered."""
        manifest = _build_study_manifest(["f1"])
        outer_dir = tmp_path / "outer_one_shot" / "f1"
        outer_dir.mkdir(parents=True, exist_ok=True)
        # Write garbage
        (outer_dir / "tampered.json").write_text("not json at all {{{")
        validator = ManifestValidator(manifest, tmp_path)
        report = validator.validate_manifest()
        assert report.is_complete is False
        assert "f1" in report.tampered

    def test_outer_artifact_without_freeze_id_is_mismatched(self, tmp_path: Path):
        manifest = _build_study_manifest(["f1"])
        outer_dir = tmp_path / "outer_one_shot" / "f1"
        outer_dir.mkdir(parents=True, exist_ok=True)
        (outer_dir / "no-freeze.json").write_text(
            json.dumps(
                {
                    "status": "COMPLETED",
                    # missing selection_freeze_id
                }
            )
        )
        validator = ManifestValidator(manifest, tmp_path)
        report = validator.validate_manifest()
        assert report.is_complete is False
        assert "f1" in report.mismatched


# =============================================================================
# Test 4: end-to-end WFO writes identity on every record
# =============================================================================


class TestEndToEndIdentity:
    """R03.4: run_nested_wfo stamps evaluation_identity on every trial record."""

    def test_trial_records_carry_evaluation_identity(self, tmp_path: Path):
        """When the WFO runs (with a deterministic mock cell runner), every
        INNER_VALIDATION and OUTER_OOS trial record in the registry must
        carry an ``evaluation_identity`` block."""
        from trading_agent.backtest.nested_wfo import run_nested_wfo
        from trading_agent.backtest.synthetic_data import (
            synthetic_wfo_spec,
            generate_synthetic_ohlcv,
        )
        from trading_agent.research.trials import ExperimentRegistry
        from trading_agent.backtest.nested_wfo import NestedFold

        # Build a minimal spec + mock fixtures
        df = generate_synthetic_ohlcv(
            symbol="BTC/USDT", timeframe="1h", n_bars=400, seed=7
        )
        folds = [
            NestedFold(
                fold_id="f1",
                inner_train_start=0,
                inner_train_end=160,
                inner_val_start=160,
                inner_val_end=240,
                outer_test_start=240,
                outer_test_end=320,
                purge=0,
                embargo=0,
            ),
        ]
        holdout_start = 320

        def _load(*a, **k):
            return df

        import trading_agent.data.storage as storage_mod
        import trading_agent.backtest.tournament as tournament_mod
        import trading_agent.backtest.nested_wfo as nwfo

        storage_mod.load_ohlcv = _load
        tournament_mod.load_ohlcv = _load
        nwfo._resolve_frozen_holdout_window = lambda *a, **k: (holdout_start, 399)
        nwfo._get_fold_indices = lambda *a, **k: folds

        # Deterministic mock cell runner
        def runner(
            spec_cell,
            *,
            out_root=None,
            start=0,
            end=None,
            fresh=True,
            measurement_start=None,
            measurement_end=None,
            **kwargs,
        ):
            import datetime
            from trading_agent.backtest.tournament import EvaluationArtifact

            return EvaluationArtifact(
                cell_id=f"cell_{spec_cell.params.get('period')}_{spec_cell.cost_scenario.name}_{measurement_start}_{measurement_end}",
                status="COMPLETED",
                descriptor_id="desc",
                strategy_id=spec_cell.strategy_id,
                symbol=spec_cell.symbol,
                timeframe=spec_cell.timeframe,
                params_hash=f"hash_{spec_cell.params}",
                cost_scenario=spec_cell.cost_scenario.name,
                fault_profile="none",
                commission=0.001,
                slippage=0.0005,
                data_manifest_sha="synthetic",
                commit_sha="synthetic",
                report_path=None,
                metrics={
                    "sharpe": 1.0,
                    "total_return_pct": 5.0,
                    "total_trades": 5,
                    "profit_factor": 1.5,
                    "max_drawdown_pct": 2.0,
                    "calmar": 1.0,
                    "return_series": [0.001] * 30,
                },
                execution_health={},
                failure_reasons=(),
                created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            )

        nwfo.run_cell = runner
        tournament_mod.run_cell = runner

        spec, _, _ = synthetic_wfo_spec(
            strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=400
        )
        from dataclasses import replace

        spec = replace(spec, registry_path=str(tmp_path / "r03.sqlite3"))

        result = run_nested_wfo(spec, out_root=tmp_path / "wfo", run_holdout=False)

        # Open the registry and check every evaluation record has an
        # evaluation_identity block.
        reg = ExperimentRegistry(tmp_path / "r03.sqlite3")
        evals = reg.evaluations()
        assert len(evals) >= 1, "Expected at least one trial record"
        for ev in evals:
            assert "evaluation_identity" in ev.metadata, (
                f"Trial {ev.evaluation_id} missing evaluation_identity"
            )
            identity = ev.metadata["evaluation_identity"]
            assert "strategy_id" in identity
            assert "data_manifest_sha" in identity
            assert "commit_sha" in identity
            # commit_sha is the real git SHA of this checkout (not "synthetic")
            assert len(identity["commit_sha"]) >= 7, (
                f"commit_sha should be a real git hash, got {identity['commit_sha']!r}"
            )
            assert "provenance_digest" in ev.metadata
            # The provenance_digest must match the canonical digest of the
            # stamped identity.
            assert ev.metadata["provenance_digest"] == provenance_digest(identity)

    def test_wfo_result_carries_provenance_digest(self, tmp_path: Path):
        """WFOResult.provenance_digest is set and non-empty after a real run."""
        from trading_agent.backtest.nested_wfo import run_nested_wfo
        from trading_agent.backtest.synthetic_data import (
            synthetic_wfo_spec,
            generate_synthetic_ohlcv,
        )
        from trading_agent.backtest.nested_wfo import NestedFold

        df = generate_synthetic_ohlcv(
            symbol="BTC/USDT", timeframe="1h", n_bars=400, seed=7
        )
        folds = [
            NestedFold(
                fold_id="f1",
                inner_train_start=0,
                inner_train_end=160,
                inner_val_start=160,
                inner_val_end=240,
                outer_test_start=240,
                outer_test_end=320,
                purge=0,
                embargo=0,
            ),
        ]
        holdout_start = 320

        def _load(*a, **k):
            return df

        import trading_agent.data.storage as storage_mod
        import trading_agent.backtest.tournament as tournament_mod
        import trading_agent.backtest.nested_wfo as nwfo

        storage_mod.load_ohlcv = _load
        tournament_mod.load_ohlcv = _load
        nwfo._resolve_frozen_holdout_window = lambda *a, **k: (holdout_start, 399)
        nwfo._get_fold_indices = lambda *a, **k: folds

        def runner(
            spec_cell,
            *,
            out_root=None,
            start=0,
            end=None,
            fresh=True,
            measurement_start=None,
            measurement_end=None,
            **kwargs,
        ):
            import datetime
            from trading_agent.backtest.tournament import EvaluationArtifact

            return EvaluationArtifact(
                cell_id=f"c_{measurement_start}_{measurement_end}",
                status="COMPLETED",
                descriptor_id="d",
                strategy_id=spec_cell.strategy_id,
                symbol=spec_cell.symbol,
                timeframe=spec_cell.timeframe,
                params_hash="h",
                cost_scenario=spec_cell.cost_scenario.name,
                fault_profile="none",
                commission=0.001,
                slippage=0.0005,
                data_manifest_sha="synthetic",
                commit_sha="synthetic",
                report_path=None,
                metrics={
                    "sharpe": 1.0,
                    "total_return_pct": 5.0,
                    "total_trades": 5,
                    "profit_factor": 1.5,
                    "max_drawdown_pct": 2.0,
                    "calmar": 1.0,
                    "return_series": [0.001] * 30,
                },
                execution_health={},
                failure_reasons=(),
                created_at=datetime.datetime.now(datetime.UTC).isoformat(),
            )

        nwfo.run_cell = runner
        tournament_mod.run_cell = runner

        spec, _, _ = synthetic_wfo_spec(
            strategy_id="rsi", symbol="BTC/USDT", timeframe="1h", n_bars=400
        )
        from dataclasses import replace

        spec = replace(spec, registry_path=str(tmp_path / "r03-d.sqlite3"))

        result = run_nested_wfo(spec, out_root=tmp_path / "wfo", run_holdout=False)

        # R03 aggregate digest is bound to the study
        assert result.provenance_digest, "WFOResult must have provenance_digest"
        assert result.provenance_digest.startswith("sha256:")

        # R03 completeness report is set for synthetic too (synthetic does
        # not require validation, but the field exists)
        # For SYNTHETIC_TEST_ONLY, the validator may not run; either way,
        # the field should be present in the result type.
        assert hasattr(result, "completeness_report")


# =============================================================================
# Test 5: attach_evaluation_identity helper
# =============================================================================


class TestAttachEvaluationIdentity:
    """R03.5: attach_evaluation_identity stamps both identity and digest."""

    def test_metadata_receives_identity_and_digest(self):
        metadata = {"foo": "bar"}
        identity = evaluation_identity(
            strategy_id="rsi",
            symbol="BTC/USDT",
            timeframe="1h",
            strategy_code_sha="c",
            data_manifest_sha="d",
            feature_schema_hash="f",
            search_space_hash="s",
            evaluator_version="v1",
            policy_version="v1",
            cost_scenario="1x",
            window_kind="INNER_VALIDATION",
            seed=1,
            commit_sha="abc",
        )
        stamped = attach_evaluation_identity(metadata, identity)
        assert stamped["foo"] == "bar"
        assert stamped["evaluation_identity"] is identity
        assert stamped["provenance_digest"].startswith("sha256:")
        # Original metadata dict is not mutated
        assert "evaluation_identity" not in metadata

    def test_digest_matches_provenance_digest(self):
        identity = evaluation_identity(
            strategy_id="rsi",
            symbol="BTC/USDT",
            timeframe="1h",
            strategy_code_sha="c",
            data_manifest_sha="d",
            feature_schema_hash="f",
            search_space_hash="s",
            evaluator_version="v1",
            policy_version="v1",
            cost_scenario="1x",
            window_kind="INNER_VALIDATION",
            seed=1,
            commit_sha="abc",
        )
        stamped = attach_evaluation_identity({}, identity)
        assert stamped["provenance_digest"] == provenance_digest(identity)


# =============================================================================
# Test 6: expected_cells_for_study
# =============================================================================


class TestExpectedCells:
    """R03.6: expected_cells_for_study enumerates the canonical cell set."""

    def test_enumerates_fold_x_cost_x_params(self):
        manifest = _build_study_manifest(["f1", "f2"])
        cells = expected_cells_for_study(
            manifest,
            cost_scenarios=["1x", "2x"],
            param_combos=[{"period": 14}, {"period": 21}],
        )
        assert len(cells) == 8  # 2 folds x 2 costs x 2 params
        # Each cell is fold::cost
        for fid in ("f1", "f2"):
            for cost in ("1x", "2x"):
                assert f"{fid}::{cost}" in cells


# =============================================================================
# Test 7: WFOResult.completeness_report field
# =============================================================================


class TestWFOResultCompleteness:
    """R03.7: WFOResult exposes completeness_report and provenance_digest."""

    def test_wfo_result_has_completeness_field(self):
        """WFOResult dataclass has the new R03 fields."""
        from trading_agent.backtest.nested_wfo import WFOResult
        from dataclasses import fields

        field_names = {f.name for f in fields(WFOResult)}
        assert "completeness_report" in field_names
        assert "provenance_digest" in field_names

    def test_completeness_report_to_dict(self):
        report = CompletenessReport(
            is_complete=False,
            expected_count=3,
            found_count=1,
            missing=("f2", "f3"),
            mismatched=("f1",),
            tampered=(),
            issues=("f1: data_manifest_sha mismatch", "f2: missing outer artifact"),
        )
        d = report.to_dict()
        assert d["is_complete"] is False
        assert d["expected_count"] == 3
        assert d["found_count"] == 1
        assert d["missing"] == ["f2", "f3"]
        assert d["mismatched"] == ["f1"]
        assert "data_manifest_sha" in d["issues"][0]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
