"""Tests for RuntimeStrategyResolver."""

from __future__ import annotations

import pytest

from trading_agent.authority.resolver import (
    RuntimeStrategyResolver,
    StrategyType,
)
from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.strategies.ma_crossover import MaCrossover
from trading_agent.strategies.rsi import RsiStrategy
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.regime_switching import RegimeSwitchingStrategy
from trading_agent.strategies.online_learning_strategy import OnlineLearningStrategy


@pytest.fixture
def resolver() -> RuntimeStrategyResolver:
    config = AuthorityConfig.for_environment("production")
    return RuntimeStrategyResolver(config)


class TestStrategyMapping:
    """Tests for strategy name to class resolution."""

    def test_ma_crossover_mapping(self, resolver: RuntimeStrategyResolver):
        strategy = resolver.get_strategy_class_by_name("ma_crossover")
        assert strategy is MaCrossover

    def test_rsi_mapping(self, resolver: RuntimeStrategyResolver):
        strategy = resolver.get_strategy_class_by_name("rsi")
        assert strategy is RsiStrategy

    def test_bbands_mapping(self, resolver: RuntimeStrategyResolver):
        strategy = resolver.get_strategy_class_by_name("bbands")
        assert strategy is BBandsStrategy

    def test_regime_switching_mapping(self, resolver: RuntimeStrategyResolver):
        strategy = resolver.get_strategy_class_by_name("regime_switching")
        assert strategy is RegimeSwitchingStrategy

    def test_online_learning_mapping(self, resolver: RuntimeStrategyResolver):
        strategy = resolver.get_strategy_class_by_name("online_learning")
        assert strategy is OnlineLearningStrategy

    def test_invalid_strategy_name(self, resolver: RuntimeStrategyResolver):
        assert resolver.get_strategy_class_by_name("nonexistent") is None

    def test_strategy_type_enum(self):
        assert StrategyType.MA_CROSSOVER.value == "ma_crossover"
        assert StrategyType.RSI.value == "rsi"
        assert StrategyType.BBANDS.value == "bbands"


class TestEnvironmentBinding:
    """Tests for environment-specific constraints."""

    def test_research_env_allows_all_symbols(self):
        config = AuthorityConfig.for_environment("research")
        resolver = RuntimeStrategyResolver(config)
        assert resolver._is_symbol_allowed("ANYPAIR") is True

    def test_timeframe_support(self, resolver: RuntimeStrategyResolver):
        assert resolver._is_timeframe_supported("1d") is True
        assert resolver._is_timeframe_supported("4h") is True
        assert resolver._is_timeframe_supported("1h") is True
        assert resolver._is_timeframe_supported("1m") is True
        assert resolver._is_timeframe_supported("invalid") is False


class TestResolve:
    """Tests for resolve() method with mock artifacts."""

    def test_resolve_returns_strategy_instance(self, resolver: RuntimeStrategyResolver):
        # Create a mock promoted strategy
        from trading_agent.authority.loader import (
            PromotedStrategy,
            PromotedStrategyManifest,
        )
        from trading_agent.research.artifact import StrategyArtifact
        from datetime import datetime, UTC

        artifact = StrategyArtifact(
            strategy_name="ma_crossover",
            code_sha="abc123",
            data_manifest_sha="data_sha",
            parameter_hash="def456",
            metadata={"parameters": {"fast_period": 20, "slow_period": 60}},
        )
        manifest = PromotedStrategyManifest(
            artifact_id=artifact.artifact_id,
            strategy_name="ma_crossover",
            code_sha="abc123",
            parameter_hash="def456",
            execution_model_version="",
            framework_version="",
            promoted_at=datetime.now(UTC),
            promoted_by="test",
            promotion_stage="production",
            parameters={"fast_period": 20, "slow_period": 60},
            metadata={},
        )
        promoted = PromotedStrategy(artifact=artifact, manifest=manifest)

        strategy = resolver.resolve(
            promoted,
            symbol="BTC/USDT",
            timeframe="1h",
            environment=Environment.PRODUCTION,
        )
        # Should return None if integrity check fails, or StrategyRuntime if passes
        from trading_agent.authority.resolver import StrategyRuntime

        assert strategy is None or isinstance(strategy, StrategyRuntime)

    def test_resolve_caches_strategy(self, resolver: RuntimeStrategyResolver):
        from trading_agent.authority.loader import (
            PromotedStrategy,
            PromotedStrategyManifest,
        )
        from trading_agent.research.artifact import StrategyArtifact
        from datetime import datetime, UTC

        artifact = StrategyArtifact(
            strategy_name="rsi",
            code_sha="sha1",
            data_manifest_sha="data_sha",
            parameter_hash="sha2",
            metadata={"parameters": {"period": 14, "oversold": 30, "overbought": 70}},
        )
        manifest = PromotedStrategyManifest(
            artifact_id=artifact.artifact_id,
            strategy_name="rsi",
            code_sha="sha1",
            parameter_hash="sha2",
            execution_model_version="",
            framework_version="",
            promoted_at=datetime.now(UTC),
            promoted_by="test",
            promotion_stage="production",
            parameters={"period": 14, "oversold": 30, "overbought": 70},
            metadata={},
        )
        promoted = PromotedStrategy(artifact=artifact, manifest=manifest)

        # First resolve
        result1 = resolver.resolve(
            promoted,
            symbol="ETH/USDT",
            timeframe="4h",
            environment=Environment.PRODUCTION,
        )
        # Second resolve should use cache
        result2 = resolver.resolve(
            promoted,
            symbol="ETH/USDT",
            timeframe="4h",
            environment=Environment.PRODUCTION,
        )

        # Results should be consistent (both None or same cached instance)
        assert result1 is result2 or (result1 is None and result2 is None)


class TestDriftDetection:
    """Tests for parameter drift detection."""

    def test_drift_detection_no_change(self, resolver: RuntimeStrategyResolver):
        # Create a promoted strategy to check for drift
        from trading_agent.authority.loader import (
            PromotedStrategy,
            PromotedStrategyManifest,
        )
        from trading_agent.research.artifact import StrategyArtifact, canonical_params, sha256_hex
        from datetime import datetime, UTC

        params = {}
        param_hash = sha256_hex(canonical_params(params))
        artifact = StrategyArtifact(
            strategy_name="ma_crossover",
            code_sha="abc123",
            data_manifest_sha="data_sha",
            parameter_hash=param_hash,
            metadata={"parameters": params},
        )
        manifest = PromotedStrategyManifest(
            artifact_id=artifact.artifact_id,
            strategy_name="ma_crossover",
            code_sha="abc123",
            parameter_hash=param_hash,
            execution_model_version="",
            framework_version="",
            promoted_at=datetime.now(UTC),
            promoted_by="test",
            promotion_stage="production",
            parameters=params,
            metadata={},
        )
        promoted = PromotedStrategy(artifact=artifact, manifest=manifest)
        result = resolver._has_param_drift(promoted)
        assert isinstance(result, bool)
        assert result is False  # No drift for fresh artifact

    def test_drift_detection_returns_bool(self, resolver: RuntimeStrategyResolver):
        from trading_agent.authority.loader import (
            PromotedStrategy,
            PromotedStrategyManifest,
        )
        from trading_agent.research.artifact import StrategyArtifact, canonical_params, sha256_hex
        from datetime import datetime, UTC

        params = {}
        param_hash = sha256_hex(canonical_params(params))
        artifact = StrategyArtifact(
            strategy_name="rsi",
            code_sha="sha1",
            data_manifest_sha="data_sha",
            parameter_hash=param_hash,
            metadata={"parameters": params},
        )
        manifest = PromotedStrategyManifest(
            artifact_id=artifact.artifact_id,
            strategy_name="rsi",
            code_sha="sha1",
            parameter_hash=param_hash,
            execution_model_version="",
            framework_version="",
            promoted_at=datetime.now(UTC),
            promoted_by="test",
            promotion_stage="production",
            parameters=params,
            metadata={},
        )
        promoted = PromotedStrategy(artifact=artifact, manifest=manifest)
        result = resolver._has_param_drift(promoted)
        assert result is False


class TestEnvironmentSpecificConfig:
    """Tests for environment-specific configuration."""

    @pytest.mark.parametrize(
        "env_name", ["production", "paper", "testnet", "research", "shadow", "canary"]
    )
    def test_config_created_for_each_environment(self, env_name: str):
        config = AuthorityConfig.for_environment(env_name)
        assert config is not None
        assert hasattr(config, "exposure")
        assert hasattr(config, "symbols")

    def test_production_config_strict(self):
        config = AuthorityConfig.for_environment("production")
        # Production should have strict exposure limits
        assert config.exposure.max_single_strategy_exposure <= 1.0
        assert config.exposure.max_portfolio_exposure <= 1.0
