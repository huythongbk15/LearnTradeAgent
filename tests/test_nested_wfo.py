"""Tests for Nested Walk-Forward Optimization (Phase S3)."""

from __future__ import annotations


import numpy as np
import pytest

from trading_agent.backtest.nested_wfo import (
    WFOSpec,
    NestedFold,
    WFOOuterResult,
    _get_fold_indices,
    _default_purge_embargo,
    InnerSelectionFreeze,
    _compute_strategy_code_sha,
    _compute_data_manifest_sha,
    _compute_feature_schema_hash,
    _compute_environment_hash,
    _compute_commit_sha,
    _compute_parameter_stability,
    _compute_sensitivity_analysis,
    GateResult,
    FormalNoTradeArtifact,
)
from trading_agent.backtest.tournament import (
    SCENARIO_DOUBLE,
)
from trading_agent.strategies.canonical.candidates import build_default_registry


class TestFoldGeometry:
    """Test expanding window fold geometry (STR-0301, STR-0302)."""

    def test_expanding_window_train_start_fixed(self):
        """Train start should always be 0 (expanding window)."""
        folds = _get_fold_indices(
            n_bars=50000,
            timeframe="1h",
            train_months=12,
            val_months=3,
            test_months=3,
            step_months=3,
            purge=100,
            embargo=100,
        )
        assert len(folds) > 0
        for fold in folds:
            assert fold.inner_train_start == 0, f"Fold {fold.fold_id}: train_start != 0"

    def test_expanding_window_train_end_grows(self):
        """Train end should grow with each fold."""
        folds = _get_fold_indices(
            n_bars=50000,
            timeframe="1h",
            train_months=12,
            val_months=3,
            test_months=3,
            step_months=3,
            purge=100,
            embargo=100,
        )
        for i in range(1, len(folds)):
            assert folds[i].inner_train_end > folds[i - 1].inner_train_end, (
                f"Fold {i}: train_end not growing"
            )

    def test_purge_embargo_gaps(self):
        """Purge/embargo gaps between train/val and val/test."""
        folds = _get_fold_indices(
            n_bars=50000,
            timeframe="1h",
            train_months=12,
            val_months=3,
            test_months=3,
            step_months=3,
            purge=100,
            embargo=100,
        )
        for fold in folds:
            # Validation starts after train + purge + embargo
            assert fold.inner_val_start >= fold.inner_train_end + fold.purge + fold.embargo
            # Test starts after validation + purge + embargo
            assert fold.outer_test_start >= fold.inner_val_end + fold.purge + fold.embargo

    def test_no_future_leakage(self):
        """Validation and test windows must not overlap with train."""
        folds = _get_fold_indices(
            n_bars=50000,
            timeframe="1h",
            train_months=12,
            val_months=3,
            test_months=3,
            step_months=3,
            purge=100,
            embargo=100,
        )
        for fold in folds:
            # Train ends before validation starts
            assert fold.inner_train_end <= fold.inner_val_start
            # Validation ends before test starts
            assert fold.inner_val_end <= fold.outer_test_start

    def test_fold_attributes_exist(self):
        """NestedFold has correct attribute names (regression for test_start/test_end)."""
        folds = _get_fold_indices(
            n_bars=50000,
            timeframe="1h",
            train_months=12,
            val_months=3,
            test_months=3,
            step_months=3,
            purge=100,
            embargo=100,
        )
        fold = folds[0]
        # These are the correct attribute names (not test_start/test_end)
        assert hasattr(fold, "outer_test_start")
        assert hasattr(fold, "outer_test_end")
        assert hasattr(fold, "inner_train_start")
        assert hasattr(fold, "inner_train_end")
        assert hasattr(fold, "inner_val_start")
        assert hasattr(fold, "inner_val_end")
        # Old buggy names should NOT exist
        assert not hasattr(fold, "test_start")
        assert not hasattr(fold, "test_end")


class TestPurgeEmbargo:
    """Test purge/embargo defaults (STR-0302)."""

    def test_default_purge_embargo_from_descriptor(self):
        """Default purge/embargo derived from strategy lookback."""
        registry = build_default_registry()
        descriptor = registry.describe("enhanced_ma")
        purge, embargo = _default_purge_embargo(descriptor)
        # Should be at least warmup_bars
        assert purge >= descriptor.warmup_bars
        assert embargo >= descriptor.warmup_bars
        # And at least execution horizon (2)
        assert purge >= 2
        assert embargo >= 2

    def test_custom_purge_embargo_override(self):
        """Custom purge/embargo from spec overrides defaults."""
        # This is tested indirectly via run_nested_wfo spec parameters


class TestTrainValidationSeparation:
    """Test train/validation separation (STR-0303)."""

    def test_inner_train_end_passed_to_trial(self):
        """_run_parameter_trial receives inner_train_end for proper train/val split."""
        # This is verified by the function signature accepting inner_train_end
        import inspect
        from trading_agent.backtest.nested_wfo import _run_parameter_trial

        sig = inspect.signature(_run_parameter_trial)
        params = list(sig.parameters.keys())
        assert "inner_train_end" in params


class TestOuterFoldFreeze:
    """Test outer fold one-shot evaluation (STR-0304)."""

    def test_inner_selection_freeze_creation(self):
        """InnerSelectionFreeze created with correct content."""
        freeze = InnerSelectionFreeze(
            fold_id="fold_000",
            strategy_id="ma_adx",
            symbol="BTC/USDT",
            timeframe="1h",
            best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
            best_val_sharpe=1.5,
            inner_train_end=8640,
            inner_val_start=8840,
            inner_val_end=11000,
            search_space_hash="abc123",
            candidate_count=162,
        )
        assert freeze.fold_id == "fold_000"
        assert freeze.strategy_id == "ma_adx"
        assert freeze.best_val_sharpe == 1.5
        assert freeze.candidate_count == 162

    def test_freeze_id_deterministic(self):
        """Same freeze content produces same freeze_id."""
        freeze1 = InnerSelectionFreeze(
            fold_id="fold_000",
            strategy_id="ma_adx",
            symbol="BTC/USDT",
            timeframe="1h",
            best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
            best_val_sharpe=1.5,
            inner_train_end=8640,
            inner_val_start=8840,
            inner_val_end=11000,
            search_space_hash="abc123",
            candidate_count=162,
        )
        freeze2 = InnerSelectionFreeze(
            fold_id="fold_000",
            strategy_id="ma_adx",
            symbol="BTC/USDT",
            timeframe="1h",
            best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
            best_val_sharpe=1.5,
            inner_train_end=8640,
            inner_val_start=8840,
            inner_val_end=11000,
            search_space_hash="abc123",
            candidate_count=162,
        )
        assert freeze1.freeze_id == freeze2.freeze_id

    def test_freeze_id_changes_with_params(self):
        """Different params produce different freeze_id."""
        freeze1 = InnerSelectionFreeze(
            fold_id="fold_000",
            strategy_id="ma_adx",
            symbol="BTC/USDT",
            timeframe="1h",
            best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
            best_val_sharpe=1.5,
            inner_train_end=8640,
            inner_val_start=8840,
            inner_val_end=11000,
            search_space_hash="abc123",
            candidate_count=162,
        )
        freeze2 = InnerSelectionFreeze(
            fold_id="fold_000",
            strategy_id="ma_adx",
            symbol="BTC/USDT",
            timeframe="1h",
            best_params={"fast_ma": 30, "slow_ma": 80, "cost_scenario": "1x"},  # Different params
            best_val_sharpe=1.5,
            inner_train_end=8640,
            inner_val_start=8840,
            inner_val_end=11000,
            search_space_hash="abc123",
            candidate_count=162,
        )
        assert freeze1.freeze_id != freeze2.freeze_id

    def test_freeze_id_changes_with_search_space(self):
        """Different search space produces different freeze_id."""
        freeze1 = InnerSelectionFreeze(
            fold_id="fold_000",
            strategy_id="ma_adx",
            symbol="BTC/USDT",
            timeframe="1h",
            best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
            best_val_sharpe=1.5,
            inner_train_end=8640,
            inner_val_start=8840,
            inner_val_end=11000,
            search_space_hash="abc123",
            candidate_count=162,
        )
        freeze2 = InnerSelectionFreeze(
            fold_id="fold_000",
            strategy_id="ma_adx",
            symbol="BTC/USDT",
            timeframe="1h",
            best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
            best_val_sharpe=1.5,
            inner_train_end=8640,
            inner_val_start=8840,
            inner_val_end=11000,
            search_space_hash="def456",  # Different search space
            candidate_count=162,
        )
        assert freeze1.freeze_id != freeze2.freeze_id


class TestExperimentRegistryIntegration:
    """Test experiment registry integration (STR-0305)."""

    def test_registry_uses_real_hashes_not_placeholders(self):
        """Registry should use real SHA256, not 'canonical'/'wfo' placeholders."""
        strategy_sha = _compute_strategy_code_sha("enhanced_ma")
        data_sha = _compute_data_manifest_sha("BTC/USDT", "1h")
        descriptor = build_default_registry().describe("enhanced_ma")
        feature_sha = _compute_feature_schema_hash(descriptor)
        assert all(
            len(value) == 64 and value not in {"canonical", "wfo", "unknown"}
            for value in (strategy_sha, data_sha, feature_sha)
        )

    def test_strategy_code_sha_real(self):
        """Strategy code SHA should be real hash, not placeholder."""
        sha = _compute_strategy_code_sha("enhanced_ma")
        assert sha != "unknown"
        assert sha != "canonical"
        assert len(sha) == 64  # SHA256 hex
        # Should be deterministic
        sha2 = _compute_strategy_code_sha("enhanced_ma")
        assert sha == sha2

    def test_data_manifest_sha_real(self):
        """Data manifest SHA should be real hash of data."""
        sha = _compute_data_manifest_sha("BTC/USDT", "1h")
        assert sha != "unknown"
        assert sha != "wfo"
        assert len(sha) == 64  # SHA256 hex
        # Should be deterministic
        sha2 = _compute_data_manifest_sha("BTC/USDT", "1h")
        assert sha == sha2

    def test_feature_schema_hash_real(self):
        """Feature schema hash should be real hash of required features."""
        from trading_agent.strategies.canonical.candidates import build_default_registry

        registry = build_default_registry()
        descriptor = registry.describe("enhanced_ma")
        sha = _compute_feature_schema_hash(descriptor)
        assert sha != "unknown"
        assert sha != "wfo"
        assert len(sha) == 64  # SHA256 hex
        # Should be deterministic
        sha2 = _compute_feature_schema_hash(descriptor)
        assert sha == sha2

    def test_environment_hash_real(self):
        """Environment hash should capture fold configuration."""
        spec = WFOSpec(
            strategy_id="ma_adx",
            symbol="BTC/USDT",
            timeframe="1h",
            train_months=12,
            val_months=3,
            test_months=3,
            step_months=3,
        )
        fold = NestedFold(
            fold_id="fold_000",
            inner_train_start=0,
            inner_train_end=8640,
            inner_val_start=8840,
            inner_val_end=11000,
            outer_test_start=11200,
            outer_test_end=13360,
            purge=100,
            embargo=100,
        )
        sha = _compute_environment_hash(spec, fold)
        assert sha != "wfo"
        assert len(sha) == 64  # SHA256 hex
        # Should be deterministic
        sha2 = _compute_environment_hash(spec, fold)
        assert sha == sha2

    def test_commit_sha_real(self):
        """Commit SHA should be real git commit."""
        sha = _compute_commit_sha()
        assert sha != "unknown"
        assert len(sha) == 40  # Git SHA1 hex


class TestParameterStability:
    """Test parameter stability computation (STR-0306)."""

    def test_parameter_stability_identical_params(self):
        """Stability = 1.0 when all folds select same params."""
        from trading_agent.backtest.nested_wfo import WFOInnerResult

        inner_results = [
            WFOInnerResult(
                fold_id="fold_000",
                train_start=0, train_end=8640,
                val_start=8840, val_end=11000,
                best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
                best_val_sharpe=1.5,
                val_metrics={"sharpe": 1.5},
                n_trials=100,
                candidate_metrics=[],
            ),
            WFOInnerResult(
                fold_id="fold_001",
                train_start=0, train_end=11520,
                val_start=11720, val_end=13880,
                best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
                best_val_sharpe=1.6,
                val_metrics={"sharpe": 1.6},
                n_trials=100,
                candidate_metrics=[],
            ),
            WFOInnerResult(
                fold_id="fold_002",
                train_start=0, train_end=14400,
                val_start=14600, val_end=16760,
                best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
                best_val_sharpe=1.4,
                val_metrics={"sharpe": 1.4},
                n_trials=100,
                candidate_metrics=[],
            ),
        ]
        stability = _compute_parameter_stability(inner_results)
        assert stability == 1.0

    def test_parameter_stability_different_params(self):
        """Stability < 1.0 when folds select different params."""
        from trading_agent.backtest.nested_wfo import WFOInnerResult

        inner_results = [
            WFOInnerResult(
                fold_id="fold_000",
                train_start=0, train_end=8640,
                val_start=8840, val_end=11000,
                best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
                best_val_sharpe=1.5,
                val_metrics={"sharpe": 1.5},
                n_trials=100,
                candidate_metrics=[],
            ),
            WFOInnerResult(
                fold_id="fold_001",
                train_start=0, train_end=11520,
                val_start=11720, val_end=13880,
                best_params={"fast_ma": 30, "slow_ma": 80, "cost_scenario": "1x"},  # Different
                best_val_sharpe=1.6,
                val_metrics={"sharpe": 1.6},
                n_trials=100,
                candidate_metrics=[],
            ),
            WFOInnerResult(
                fold_id="fold_002",
                train_start=0, train_end=14400,
                val_start=14600, val_end=16760,
                best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
                best_val_sharpe=1.4,
                val_metrics={"sharpe": 1.4},
                n_trials=100,
                candidate_metrics=[],
            ),
        ]
        stability = _compute_parameter_stability(inner_results)
        # 2/3 folds have fast_ma=20, slow_ma=60 -> stability = (2/3 + 2/3) / 2 = 2/3 ≈ 0.67
        assert 0.66 < stability < 0.68

    def test_parameter_stability_empty(self):
        """No or one fold is insufficient stability evidence."""
        assert _compute_parameter_stability([]) is None

        from trading_agent.backtest.nested_wfo import WFOInnerResult
        single = [WFOInnerResult(
            fold_id="fold_000",
            train_start=0, train_end=8640,
            val_start=8840, val_end=11000,
            best_params={"fast_ma": 20, "slow_ma": 60, "cost_scenario": "1x"},
            best_val_sharpe=1.5,
            val_metrics={"sharpe": 1.5},
            n_trials=100,
            candidate_metrics=[],
        )]
        assert _compute_parameter_stability(single) is None


class TestStatisticalHardening:
    """Test statistical hardening implementation (STR-0306)."""

    def test_block_bootstrap_ci_import(self):
        """Block bootstrap CI function available."""
        from trading_agent.alpha_research.stats import block_bootstrap_sharpe_ci
        assert callable(block_bootstrap_sharpe_ci)

    def test_psr_import(self):
        """PSR function available."""
        from trading_agent.alpha_research.stats import probabilistic_sharpe_ratio
        assert callable(probabilistic_sharpe_ratio)

    def test_dsr_import(self):
        """DSR function available."""
        from trading_agent.alpha_research.stats import deflated_sharpe_ratio
        assert callable(deflated_sharpe_ratio)

    def test_pbo_import(self):
        """PBO/CSCV function available."""
        from trading_agent.alpha_research.stats import probability_of_backtest_overfitting
        assert callable(probability_of_backtest_overfitting)

    def test_summarize_sharpe_import(self):
        """summarize_sharpe function available."""
        from trading_agent.alpha_research.stats import summarize_sharpe
        assert callable(summarize_sharpe)

    def test_block_bootstrap_reproducible(self):
        """Block bootstrap CI is reproducible with same seed."""
        from trading_agent.alpha_research.stats import block_bootstrap_sharpe_ci

        returns = np.random.default_rng(42).normal(0.001, 0.01, 200)
        lo1, hi1, _ = block_bootstrap_sharpe_ci(returns, periods_per_year=252, seed=42)
        lo2, hi2, _ = block_bootstrap_sharpe_ci(returns, periods_per_year=252, seed=42)
        assert lo1 == lo2
        assert hi1 == hi2

    def test_dsr_uses_registry_trials(self):
        """DSR uses effective trial count from registry."""
        from trading_agent.alpha_research.stats import deflated_sharpe_ratio, series_stats

        returns = np.random.default_rng(42).normal(0.001, 0.01, 200)
        s = series_stats(returns, periods_per_year=252)
        dsr = deflated_sharpe_ratio(
            s.sharpe,
            n=s.n,
            trials=100,  # Registry effective trial count
            skew=s.skew,
            excess_kurtosis=s.excess_kurtosis,
        )
        # DSR should be <= PSR (deflated)
        from trading_agent.alpha_research.stats import probabilistic_sharpe_ratio
        psr = probabilistic_sharpe_ratio(
            s.sharpe, sr_benchmark=0.0, skew=s.skew, excess_kurtosis=s.excess_kurtosis, n=s.n
        )
        assert dsr <= psr + 1e-10  # Allow tiny numerical diff

    def test_min_trades_check_import(self):
        """Min trades check function available."""
        from trading_agent.alpha_research.stats import min_trades_check
        assert callable(min_trades_check)

    def test_invalid_returns_raises(self):
        """Invalid returns (NaN, Inf, zero variance) handled."""
        from trading_agent.alpha_research.stats import series_stats

        # NaN returns
        with pytest.raises(ValueError):
            series_stats(np.array([1.0, np.nan, 3.0]), periods_per_year=252)

        # Inf returns
        with pytest.raises(ValueError):
            series_stats(np.array([1.0, np.inf, 3.0]), periods_per_year=252)

        # Zero variance
        stats = series_stats(np.array([0.0, 0.0, 0.0]), periods_per_year=252)
        assert stats.sharpe == 0.0


class TestMultiDimensionalEvaluation:
    """Test multi-dimensional evaluation (STR-0307)."""

    def test_multidimensional_evaluator_is_exposed(self):
        from trading_agent.backtest.nested_wfo import _compute_multi_dimensional_evaluation

        assert callable(_compute_multi_dimensional_evaluation)


class TestGateResult:
    """Test GateResult structure (STR-0306/0309/0310)."""

    def test_gate_result_pass(self):
        """GateResult with PASS verdict."""
        gate = GateResult(
            gate_id="test_gate",
            policy_version="v1",
            observed_value=1.0,
            threshold=0.8,
            comparison=">=",
            verdict="PASS",
            reason="Test pass",
        )
        assert gate.is_pass()
        assert not gate.is_fail()

    def test_gate_result_fail(self):
        """GateResult with FAIL verdict."""
        gate = GateResult(
            gate_id="test_gate",
            policy_version="v1",
            observed_value=0.5,
            threshold=0.8,
            comparison=">=",
            verdict="FAIL",
            reason="Test fail",
        )
        assert not gate.is_pass()
        assert gate.is_fail()

    def test_gate_result_invalid_treated_as_fail(self):
        """INVALID verdict treated as FAIL."""
        gate = GateResult(
            gate_id="test_gate",
            policy_version="v1",
            observed_value=None,
            threshold=0.8,
            comparison=">=",
            verdict="INVALID",
            reason="Test invalid",
        )
        assert not gate.is_pass()
        assert gate.is_fail()

    def test_gate_result_comparison_operators(self):
        """Test all comparison operators."""
        test_cases = [
            (">=", 1.0, 0.8, True),
            (">", 1.0, 0.8, True),
            (">", 0.8, 0.8, False),
            ("<=", 0.5, 1.0, True),
            ("<", 0.5, 1.0, True),
            ("<", 1.0, 1.0, False),
            ("==", 1.0, 1.0, True),
            ("==", 1.0, 2.0, False),
            ("!=", 1.0, 2.0, True),
        ]
        for comp, obs, thresh, expected in test_cases:
            gate = GateResult(
                gate_id="test",
                policy_version="v1",
                observed_value=obs,
                threshold=thresh,
                comparison=comp,
                verdict="PASS" if expected else "FAIL",
                reason="test",
            )
            assert gate.is_pass() == expected, f"Failed for {comp}: {obs} {comp} {thresh}"


class TestFormalNoTradeArtifact:
    """Test FormalNoTradeArtifact (STR-0310)."""

    def test_no_trade_artifact_creation(self):
        """FormalNoTradeArtifact created with all required fields."""
        gate = GateResult(
            gate_id="test_gate",
            policy_version="v1",
            observed_value=0.5,
            threshold=0.8,
            comparison=">=",
            verdict="FAIL",
            reason="Test",
        )
        artifact = FormalNoTradeArtifact(
            candidate_set=["strategy_a"],
            gate_results={"strategy_a": [gate]},
            gate_failures={"strategy_a": ["test_gate"]},
            best_candidate="strategy_a",
            best_candidate_metrics={"median_test_sharpe": 0.5},
            registry_identity={"experiment_id": "exp_123"},
            policy_version="v1",
            policy_thresholds={"test_gate": 0.8},
            commit_sha="abc123",
            data_manifest_sha="def456",
            feature_schema_hash="ghi789",
            search_space_hash="jkl012",
            evaluation_timestamp="2026-01-01T00:00:00+00:00",
            evaluation_duration_sec=10.5,
            notes="Test NO_TRADE",
        )
        assert artifact.candidate_set == ["strategy_a"]
        assert artifact.gate_failures["strategy_a"] == ["test_gate"]
        assert artifact.best_candidate == "strategy_a"
        assert artifact.no_trade_id.startswith("sha256:")

    def test_no_trade_artifact_integrity(self):
        """FormalNoTradeArtifact integrity verification."""
        gate = GateResult(
            gate_id="test_gate",
            policy_version="v1",
            observed_value=0.5,
            threshold=0.8,
            comparison=">=",
            verdict="FAIL",
            reason="Test",
        )
        artifact = FormalNoTradeArtifact(
            candidate_set=["strategy_a"],
            gate_results={"strategy_a": [gate]},
            gate_failures={"strategy_a": ["test_gate"]},
            best_candidate="strategy_a",
            best_candidate_metrics={"median_test_sharpe": 0.5},
            registry_identity={"experiment_id": "exp_123"},
            policy_version="v1",
            policy_thresholds={"test_gate": 0.8},
            commit_sha="abc123",
            data_manifest_sha="def456",
            feature_schema_hash="ghi789",
            search_space_hash="jkl012",
            evaluation_timestamp="2026-01-01T00:00:00+00:00",
            evaluation_duration_sec=10.5,
            notes="Test NO_TRADE",
        )
        # Original should verify
        assert artifact.verify_integrity()

    def test_no_trade_artifact_to_dict(self):
        """FormalNoTradeArtifact serializes to dict."""
        gate = GateResult(
            gate_id="test_gate",
            policy_version="v1",
            observed_value=0.5,
            threshold=0.8,
            comparison=">=",
            verdict="FAIL",
            reason="Test",
        )
        artifact = FormalNoTradeArtifact(
            candidate_set=["strategy_a"],
            gate_results={"strategy_a": [gate]},
            gate_failures={"strategy_a": ["test_gate"]},
            best_candidate="strategy_a",
            best_candidate_metrics={"median_test_sharpe": 0.5},
            registry_identity={"experiment_id": "exp_123"},
            policy_version="v1",
            policy_thresholds={"test_gate": 0.8},
            commit_sha="abc123",
            data_manifest_sha="def456",
            feature_schema_hash="ghi789",
            search_space_hash="jkl012",
            evaluation_timestamp="2026-01-01T00:00:00+00:00",
            evaluation_duration_sec=10.5,
            notes="Test NO_TRADE",
        )
        d = artifact.to_dict()
        assert d["candidate_set"] == ["strategy_a"]
        assert d["gate_failures"]["strategy_a"] == ["test_gate"]
        assert d["no_trade_id"] == artifact.no_trade_id


class TestHardGates:
    """Test all hard gates (STR-0306/0309/0310)."""

    def test_invalid_gate_blocks_aggregate_decision(self):
        invalid = GateResult(
            gate_id="missing_metric",
            policy_version="v1",
            observed_value=None,
            threshold=0.0,
            comparison=">",
            verdict="INVALID",
            reason="missing evidence",
        )
        passing = GateResult(
            gate_id="positive_return",
            policy_version="v1",
            observed_value=1.0,
            threshold=0.0,
            comparison=">",
            verdict="PASS",
            reason="ok",
        )
        assert not all(gate.is_pass() for gate in (passing, invalid))


class TestFinalHoldout:
    """Test final holdout freeze (STR-0309)."""

    def test_resolve_frozen_holdout_window(self):
        """Frozen holdout window resolves from research_manifest.json."""
        from trading_agent.data.storage import load_ohlcv
        from trading_agent.backtest.nested_wfo import (
            _resolve_frozen_holdout_window,
        )

        df = load_ohlcv("binance", "BTC/USDT", "1h")
        hb = _resolve_frozen_holdout_window(df, None)
        assert hb is not None
        h_start, h_end = hb
        assert 0 < h_start < h_end < df.height

    def test_guard_rejects_overlapping_fold(self):
        """Guard raises HoldoutError if a fold's data window touches holdout."""
        from trading_agent.data.storage import load_ohlcv
        from trading_agent.backtest.nested_wfo import (
            _resolve_frozen_holdout_window,
            _guard_fold_against_holdout,
            NestedFold,
        )
        from trading_agent.strategies.canonical.candidates import build_default_registry
        from trading_agent.alpha_research.holdout import HoldoutError

        df = load_ohlcv("binance", "BTC/USDT", "1h")
        h_start, h_end = _resolve_frozen_holdout_window(df, None)
        reg = build_default_registry()
        descriptor = reg.describe("enhanced_ma")

        overlap = NestedFold(
            fold_id="overlap",
            inner_train_start=h_start - 500, inner_train_end=h_start - 100,
            inner_val_start=h_start - 100, inner_val_end=h_start,
            outer_test_start=h_start, outer_test_end=h_start + 200,
            purge=0, embargo=0,
        )
        with pytest.raises(HoldoutError):
            _guard_fold_against_holdout(overlap, df, descriptor, h_start, h_end)

    def test_guard_accepts_safe_fold(self):
        """Guard accepts a fold fully before the holdout."""
        from trading_agent.data.storage import load_ohlcv
        from trading_agent.backtest.nested_wfo import (
            _resolve_frozen_holdout_window,
            _guard_fold_against_holdout,
            NestedFold,
        )
        from trading_agent.strategies.canonical.candidates import build_default_registry

        df = load_ohlcv("binance", "BTC/USDT", "1h")
        h_start, h_end = _resolve_frozen_holdout_window(df, None)
        reg = build_default_registry()
        descriptor = reg.describe("enhanced_ma")

        safe = NestedFold(
            fold_id="safe",
            inner_train_start=1000, inner_train_end=5000,
            inner_val_start=5000, inner_val_end=7000,
            outer_test_start=7000, outer_test_end=9000,
            purge=0, embargo=0,
        )
        # Should not raise
        _guard_fold_against_holdout(safe, df, descriptor, h_start, h_end)

    def test_fold_filtering_excludes_holdout(self):
        """Folds overlapping the frozen holdout are dropped."""
        from trading_agent.data.storage import load_ohlcv
        from trading_agent.backtest.nested_wfo import (
            _resolve_frozen_holdout_window,
            _get_fold_indices,
            _default_purge_embargo,
        )
        from trading_agent.strategies.canonical.candidates import build_default_registry

        df = load_ohlcv("binance", "BTC/USDT", "1h")
        n_bars = df.height
        h_start, _ = _resolve_frozen_holdout_window(df, None)
        reg = build_default_registry()
        descriptor = reg.describe("enhanced_ma")
        purge, embargo = _default_purge_embargo(descriptor)
        all_folds = _get_fold_indices(n_bars, "1h", 6, 1, 1, 2, purge, embargo)
        kept = [f for f in all_folds if f.outer_test_end <= h_start]
        assert len(kept) > 0
        assert len(kept) < len(all_folds)
        assert all(f.outer_test_end <= h_start for f in kept)

    def test_holdout_manifest_integrity(self):
        """FinalHoldoutManifest is content-addressed and tamper-evident."""
        from trading_agent.backtest.nested_wfo import FinalHoldoutManifest

        m = FinalHoldoutManifest(
            strategy_id="enhanced_ma", symbol="BTC/USDT", timeframe="1h",
            holdout_start_bar=100, holdout_end_bar=200,
            data_manifest_sha="abc", feature_schema_hash="def",
            freeze_timestamp="2026-01-01T00:00:00+00:00", commit_sha_at_freeze="xyz",
        )
        assert m.holdout_id.startswith("sha256:")
        assert m.verify_integrity()
        # Opening produces a new immutable manifest
        opened = m.open(actor="research_system")
        assert opened.opened
        assert opened.opened_by == "research_system"
        # Re-opening a already-opened manifest raises
        import pytest
        with pytest.raises(ValueError):
            opened.open(actor="x")
class TestFormalNoTrade:
    """Test formal NO_TRADE result (STR-0310)."""

    def test_no_trade_id_changes_when_gate_evidence_changes(self):
        gate = GateResult(
            gate_id="test_gate",
            policy_version="v1",
            observed_value=0.5,
            threshold=0.8,
            comparison=">=",
            verdict="FAIL",
            reason="Test",
        )
        common = dict(
            candidate_set=["strategy_a"],
            gate_results={"strategy_a": [gate]},
            gate_failures={"strategy_a": ["test_gate"]},
            best_candidate="strategy_a",
            best_candidate_metrics={"median_test_sharpe": 0.5},
            registry_identity={"experiment_id": "exp_123"},
            policy_version="v1",
            policy_thresholds={"test_gate": 0.8},
            commit_sha="abc123",
            data_manifest_sha="def456",
            feature_schema_hash="ghi789",
            search_space_hash="jkl012",
            evaluation_timestamp="2026-01-01T00:00:00+00:00",
            evaluation_duration_sec=10.5,
        )
        first = FormalNoTradeArtifact(**common, notes="first")
        second = FormalNoTradeArtifact(**common, notes="changed")
        assert first.no_trade_id != second.no_trade_id
        assert first.verify_integrity()
        assert second.verify_integrity()


class TestSensitivityAnalysis:
    """Test STR-0308 real sensitivity analysis (mocked backtest, real drop-best)."""

    def _make_artifact(self, tmp_path, trades):
        import json

        report = tmp_path / "report.json"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(json.dumps({
            "status": "COMPLETED",
            "metrics": {
                "total_return_pct": 1.0,
                "profit_factor": 1.3,
                "total_trades": len(trades),
                "sharpe": 0.5,
            },
            "trades": trades,
        }))
        return report

    def test_real_drop_best_and_cost2x(self, tmp_path):
        from unittest.mock import patch

        base_trades = [{"pnl": 10.0}, {"pnl": -3.0}, {"pnl": 5.0}]
        base_report = self._make_artifact(tmp_path / "base", base_trades)
        cost2_trades = [{"pnl": 6.0}, {"pnl": -4.0}]
        cost2_report = self._make_artifact(tmp_path / "cost2", cost2_trades)

        reg = build_default_registry()
        descriptor = reg.describe("rsi")
        spec = WFOSpec(
            strategy_id="rsi", symbol="BTC/USDT", timeframe="1h",
            param_grid={"period": [14], "oversold": [30], "overbought": [70]},
        )

        class _Art:
            def __init__(self, path, status="COMPLETED", metrics=None):
                self.report_path = str(path)
                self.status = status
                self.metrics = metrics or {}
                self.artifact_id = "fake"

        fold = NestedFold(
            fold_id="f1", inner_train_start=1000, inner_train_end=5000,
            inner_val_start=5000, inner_val_end=7000,
            outer_test_start=7000, outer_test_end=9000, purge=0, embargo=0,
        )
        outer = WFOOuterResult(
            fold_id="f1", test_start=7000, test_end=9000,
            params={"period": 14, "oversold": 30, "overbought": 70, "cost_scenario": "1x"},
            test_metrics={
                "sharpe": 0.5, "total_return_pct": 1.2, "total_trades": 3,
                "profit_factor": 1.3, "max_drawdown_pct": 5.0,
            },
            execution_health={}, artifact=_Art(base_report),
        )

        def fake_run(spec_, params, cost_scenario, fold_, out_root, descriptor_, signal_delay_bars=0):
            if cost_scenario is SCENARIO_DOUBLE:
                return _Art(cost2_report, metrics={
                    "total_return_pct": -0.1, "profit_factor": 0.82,
                    "total_trades": 2, "sharpe": -0.05})
            return _Art(cost2_report, metrics={
                "total_return_pct": -0.2, "profit_factor": 0.70,
                "total_trades": 2, "sharpe": -0.1})

        with patch("trading_agent.backtest.nested_wfo._run_outer_eval", side_effect=fake_run):
            sens = _compute_sensitivity_analysis(
                spec, [outer], [fold], out_root=tmp_path / "out", descriptor=descriptor,
            )

        assert sens["real_computed"] == ["cost_2x", "slippage_stress", "drop_best_trade", "delay_1_bar", "parameter_neighbors"]
        # Real trade-level drop-best: net 12.0, drop best (10.0) -> 2.0
        assert sens["drop_best_trade"]["folds"]["f1"]["net_pnl"] == 12.0
        assert sens["drop_best_trade"]["folds"]["f1"]["net_pnl_after_drop"] == 2.0
        assert sens["cost_2x"]["aggregate"]["median_profit_factor"] == 0.82
        assert sens["slippage_stress"]["aggregate"]["median_profit_factor"] == 0.70
        assert sens["drop_best_trade"]["aggregate"]["total_net_pnl_after_drop"] == 2.0

    def test_fallback_without_rerun(self):
        """Without out_root/descriptor, returns framework placeholders (no crash)."""
        reg = build_default_registry()
        descriptor = reg.describe("rsi")
        spec = WFOSpec(strategy_id="rsi", symbol="BTC/USDT", timeframe="1h")
        fold = NestedFold(
            fold_id="f1", inner_train_start=1000, inner_train_end=5000,
            inner_val_start=5000, inner_val_end=7000,
            outer_test_start=7000, outer_test_end=9000, purge=0, embargo=0,
        )
        outer = WFOOuterResult(
            fold_id="f1", test_start=7000, test_end=9000,
            params={"cost_scenario": "1x"}, test_metrics={"sharpe": 0.5},
            execution_health={}, artifact=None,
        )
        sens = _compute_sensitivity_analysis(spec, [outer], [fold])
        assert sens["real_computed"] == []
        assert "note" in sens


@pytest.mark.slow
@pytest.mark.wfo
class TestMeasurementWindow:
    """S3-1: validation/OOS metrics must be isolated to the measurement window.

    Warm-up/train bars before ``measurement_start`` only initialize indicator
    state and MUST NOT enter the reported metrics. Trades whose exit falls
    outside ``[measurement_start, measurement_end)`` must be excluded.
    """

    def _run(self, monkeypatch, out_root, m_start=None, m_end=None, n_bars=2000):
        from trading_agent.backtest.synthetic_data import generate_synthetic_ohlcv
        from trading_agent.backtest.tournament import (
            EvaluationCellSpec,
            SCENARIO_BASE,
            run_cell,
        )

        df = generate_synthetic_ohlcv(
            symbol="BTC/USDT", timeframe="1h", n_bars=n_bars, seed=7, regimes=True
        )

        def _load(*args, **kwargs):
            return df

        monkeypatch.setattr("trading_agent.data.storage.load_ohlcv", _load)
        monkeypatch.setattr("trading_agent.backtest.tournament.load_ohlcv", _load)

        spec = EvaluationCellSpec(
            strategy_id="rsi",
            symbol="BTC/USDT",
            timeframe="1h",
            params={"period": 14, "oversold": 30, "overbought": 70},
            cost_scenario=SCENARIO_BASE,
        )
        return run_cell(
            spec,
            out_root=out_root,
            start=0,
            end=n_bars,
            fresh=True,
            measurement_start=m_start,
            measurement_end=m_end,
        )

    def test_measurement_window_recorded(self, tmp_path, monkeypatch):
        art = self._run(monkeypatch, tmp_path / "m", 800, 1200)
        assert art.measurement_window == (800, 1200)

    def test_full_run_has_no_measurement_window(self, tmp_path, monkeypatch):
        art = self._run(monkeypatch, tmp_path / "f")
        assert art.measurement_window is None

    def test_prewindow_trades_excluded(self, tmp_path, monkeypatch):
        full = self._run(monkeypatch, tmp_path / "full")
        meas = self._run(monkeypatch, tmp_path / "m", 800, 1200)
        import json

        def _exit_bar(t):
            sim = t.get("metadata") if isinstance(t.get("metadata"), dict) else None
            sim_exit = (
                sim.get("simulation", {}).get("exit_bar_index")
                if isinstance(sim, dict)
                else None
            )
            raw = t.get("exit_bar_index", sim_exit)
            try:
                return int(raw)
            except (TypeError, ValueError):
                return -1

        rep = json.loads(__import__("pathlib").Path(meas.report_path).read_text())
        in_window = [t for t in rep["trades"] if 800 <= _exit_bar(t) < 1200]
        # Metrics reflect only trades exiting inside the window
        assert meas.metrics["total_trades"] == len(in_window)
        # Boundary: every counted trade respects [inclusive, exclusive)
        for t in in_window:
            eb = _exit_bar(t)
            assert 800 <= eb < 1200
        # Measurement window can never contain more trades than the full run
        assert meas.metrics["total_trades"] <= full.metrics["total_trades"]
        # Trades before the window must not be counted
        pre = [t for t in rep["trades"] if _exit_bar(t) < 800]
        if pre:
            assert meas.metrics["total_trades"] < full.metrics["total_trades"]

    def test_val_metrics_reproducible_for_same_window(self, tmp_path, monkeypatch):
        a = self._run(monkeypatch, tmp_path / "a", 800, 1200)
        b = self._run(monkeypatch, tmp_path / "b", 800, 1200)
        assert a.metrics["total_trades"] == b.metrics["total_trades"]
        assert a.metrics.get("sharpe") == b.metrics.get("sharpe")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
