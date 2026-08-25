"""
Golden test for execute_promoted_strategy — canonical one-call API.

This test verifies the complete flow:
execute_promoted_strategy(symbol, timeframe, environment, ...)
  → resolver.resolve_for(symbol, timeframe, environment)
  → execute_strategy()
  → DecisionAuthority → ExposureAuthority → ExecutionAuthority
  → OrderPlanner → Permission → ExecutionLifecycle → BrokerGateway
  → PaperExecutionAdapter.submit() called exactly ONCE
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import tempfile

import pytest

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.loader import PromotedStrategy, PromotedStrategyManifest
from trading_agent.authority.promotion_store import (
    PromotionStateStore,
    PromotionRecord,
)
from trading_agent.execution.canonical.order_planner import InstrumentRules
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.research.artifact import (
    StrategyArtifact,
    PersistentArtifactStore,
    canonical_params,
    sha256_hex,
)
from trading_agent.research.promotion import ResearchPromotionEvent, ResearchStage
from trading_agent.strategies.ma_crossover import MaCrossover


class TestGoldenExecutePromotedStrategy:
    """Golden test for the canonical execute_promoted_strategy API."""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def config(self) -> AuthorityConfig:
        return AuthorityConfig.for_environment(Environment.PAPER)

    @pytest.fixture
    def promotion_store(self, temp_dir: Path) -> PromotionStateStore:
        return PromotionStateStore(temp_dir / "promotion.db")

    @pytest.fixture
    def artifact_store(self, temp_dir: Path) -> PersistentArtifactStore:
        return PersistentArtifactStore(temp_dir / "artifacts")

    @pytest.fixture
    def btc_artifact(
        self,
        artifact_store: PersistentArtifactStore,
        promotion_store: PromotionStateStore,
    ) -> StrategyArtifact:
        """Create a BTC MA Crossover artifact and promote it to PAPER_ELIGIBLE."""
        params = {"fast_period": 10, "slow_period": 30}
        artifact = StrategyArtifact(
            strategy_name="ma_crossover",
            code_sha="abc123",
            data_manifest_sha="data_sha",
            parameter_hash=sha256_hex(canonical_params(params)),
            execution_model_version="1.0",
            framework_version="1.0",
            metadata={
                "symbol": "BTC/USDT",
                "timeframe": "1h",
                "parameters": params,
                "calibration_state": "KNOWN",
                "ood_state": "KNOWN",
                "regime_state": "KNOWN",
            },
        )
        artifact_store.add(artifact)

        # Also promote it
        promo_event = ResearchPromotionEvent(
            subject_artifact_id=artifact.artifact_id,
            from_stage=ResearchStage.RESEARCH_VALIDATED,
            to_stage=ResearchStage.PAPER_ELIGIBLE,
            evidence_ids=("wfo_sha",),
            actor="test",
            timestamp=datetime.now(UTC),
        )
        record = PromotionRecord(
            artifact_id=artifact.artifact_id,
            stage=ResearchStage.PAPER_ELIGIBLE,
            latest_event=promo_event,
            updated_at=datetime.now(UTC),
        )
        promotion_store.upsert(record)

        return artifact

    @pytest.fixture
    def promoted_btc(
        self,
        btc_artifact: StrategyArtifact,
        promotion_store: PromotionStateStore,
    ) -> PromotedStrategy:
        """Get the promoted BTC artifact as PromotedStrategy."""
        params = {"fast_period": 10, "slow_period": 30}
        # The artifact is already promoted by the btc_artifact fixture
        manifest = PromotedStrategyManifest(
            artifact_id=btc_artifact.artifact_id,
            strategy_name="ma_crossover",
            code_sha="abc123",
            parameter_hash=sha256_hex(canonical_params(params)),
            execution_model_version="1.0",
            framework_version="1.0",
            promoted_at=datetime.now(UTC),
            promoted_by="test",
            promotion_stage="paper_eligible",
            parameters=params,
            metadata={"symbol": "BTC/USDT", "timeframe": "1h"},
        )
        return PromotedStrategy(artifact=btc_artifact, manifest=manifest)

    @pytest.fixture
    def instrument_rules(self) -> dict[str, InstrumentRules]:
        """Minimal instrument rules for BTC/USDT."""
        return {
            "BTC/USDT": InstrumentRules(
                symbol="BTC/USDT",
                asset_class="spot",
                min_order_qty=0.0001,
                max_order_qty=100.0,
                qty_step=0.0001,
                price_precision=2,
                min_notional=10.0,
                max_leverage=1.0,
            )
        }

    @pytest.fixture
    def execution_engine(
        self,
        config: AuthorityConfig,
        promotion_store: PromotionStateStore,
        artifact_store: PersistentArtifactStore,
        instrument_rules: dict[str, InstrumentRules],
        temp_dir: Path,
    ) -> ExecutionEngine:
        """Create ExecutionEngine with full authority chain and resolver."""
        engine = ExecutionEngine(
            exchange_name="paper",
            initial_capital=100_000.0,
            commission=0.001,
            slippage=0.0005,
            instrument_rules=instrument_rules["BTC/USDT"],
            authority_config=config,
            promotion_store=promotion_store,
            artifact_store=artifact_store,
            state_dir=temp_dir / "paper_state",
            event_store_path=temp_dir / "events.db",
        )
        yield engine
        # Cleanup
        engine._graceful_shutdown()

    @pytest.fixture
    def market_data(self):
        """Deterministic OHLCV where MA crossover (BUY) lands EXACTLY on the last bar.

        Math: fast=10, slow=30, all closes flat at 50_000 except the last = 55_000.
        - Index 48: fast_ma == slow_ma == 50_000 → raw = 0 (no change)
        - Index 49: fast_ma = (9*50k + 55k)/10 = 50_500
                    slow_ma = (29*50k + 55k)/30 ≈ 50_166.7
                    → raw = +1 ≠ prev(0) → BUY signal at the LAST bar.
        """
        import polars as pl

        n = 50
        flat = [50_000.0] * (n - 1)
        spike = 55_000.0
        prices = flat + [spike]

        df = pl.DataFrame(
            {
                "timestamp": [datetime(2024, 1, 1, tzinfo=UTC) for _ in range(n)],
                "open": prices,
                "high": prices,
                "low": prices,
                "close": prices,
                "volume": [100.0] * n,
            }
        )
        return df

    @pytest.fixture
    def observation(self, market_data):
        """Create EnrichedMarketObservation from market data."""
        from trading_agent.execution.canonical import EnrichedMarketObservation

        last_row = market_data.tail(1).to_dicts()[0]
        now = datetime.now(UTC)
        return EnrichedMarketObservation(
            symbol="BTC/USDT",
            observed_at=now,
            open=float(last_row["open"]),
            high=float(last_row["high"]),
            low=float(last_row["low"]),
            close=float(last_row["close"]),
            volume=float(last_row["volume"]),
            features={},
            timeframe="1h",
            bar_close_at=now,
            is_closed=True,
            data_manifest_id="test_manifest",
            feature_artifact_id="test_feature",
        )

    def test_execute_promoted_strategy_submits_exactly_once(
        self,
        execution_engine: ExecutionEngine,
        promoted_btc: PromotedStrategy,
        market_data,
        observation,
    ):
        """
        Golden test: execute_promoted_strategy() → broker adapter receives the
        ENTRY order exactly ONCE (no duplicate execution), plus the deterministic
        protective stop that the engine attaches after a BUY fill.

        Flow:
        1. execute_promoted_strategy("BTC/USDT", "1h", "paper", ...)
        2. resolver.resolve_for("BTC/USDT", "1h", Environment.PAPER) → StrategyRuntime
        3. execute_strategy() → authority chain → planner → lifecycle → gateway
        4. PaperExecutionAdapter.submit_order(): 1 entry + 1 protection = 2 total
        """
        # Seed paper exchange with current price
        last_price = float(market_data.tail(1)["close"][0])
        execution_engine.exchange._last_price_cache["BTC/USDT"] = last_price
        execution_engine.exchange._last_price_timestamps["BTC/USDT"] = datetime.now(
            UTC
        ).timestamp()

        # Spy on the adapter's submit_order method
        original_submit_order = execution_engine.gateway._adapter.submit_order
        submit_calls: list = []

        def spy_submit_order(request):
            submit_calls.append(request)
            return original_submit_order(request)

        execution_engine.gateway._adapter.submit_order = spy_submit_order

        # Execute the canonical one-call API
        orders = execution_engine.execute_promoted_strategy(
            symbol="BTC/USDT",
            timeframe="1h",
            environment="paper",
            observation=observation,
            market_data=market_data,
        )

        # ── Golden invariant: entry submitted EXACTLY ONCE ──
        def _is_protection(request) -> bool:
            intent_id = str(getattr(request, "intent_id", ""))
            return intent_id.startswith("prot_") and intent_id.endswith("_submit")

        entry_calls = [r for r in submit_calls if not _is_protection(r)]
        protection_calls = [r for r in submit_calls if _is_protection(r)]

        assert len(entry_calls) == 1, (
            "Golden invariant violated: expected exactly ONE entry submission "
            f"to broker adapter, got {len(entry_calls)} "
            f"(total submissions={len(submit_calls)})"
        )
        # Deterministic bundle: BUY fill must be protected by exactly one stop
        assert len(protection_calls) == 1, (
            f"Expected exactly 1 protective stop for the BUY fill, "
            f"got {len(protection_calls)}"
        )

        # Idempotency keys must be unique across ALL submissions
        idem_keys = [str(r.idempotency_key) for r in submit_calls]
        assert len(idem_keys) == len(set(idem_keys)), (
            f"Duplicate idempotency keys across submissions: {idem_keys}"
        )

        # Entry order content sanity (compare enum by value — gateway uses its own
        # OrderSide class from exchanges.models, not execution.types)
        entry = entry_calls[0]
        assert entry.symbol.base == "BTC" and entry.symbol.quote == "USDT"
        assert str(entry.side.value) == "buy"
        assert str(entry.order_type.value) == "market"
        assert float(entry.quantity) > 0

        # API returns the filled entry order(s) from this single call
        assert isinstance(orders, list)

    def test_execute_promoted_strategy_resolves_correct_artifact(
        self,
        execution_engine: ExecutionEngine,
        promoted_btc: PromotedStrategy,
        market_data,
        observation,
    ):
        """Test that the correct artifact is resolved for the given symbol/timeframe."""
        # Verify resolver is configured
        assert execution_engine.resolver is not None
        assert execution_engine.resolver.promotion_store is not None
        assert execution_engine.resolver._artifact_store is not None

        # Call the API
        orders = execution_engine.execute_promoted_strategy(
            symbol="BTC/USDT",
            timeframe="1h",
            environment="paper",
            observation=observation,
            market_data=market_data,
        )

        # Verify cache was populated
        runtime = execution_engine.resolver.get_cached(
            "BTC/USDT", "1h", Environment.PAPER
        )
        assert runtime is not None
        assert runtime.symbol == "BTC/USDT"
        assert runtime.timeframe == "1h"
        assert runtime.environment == Environment.PAPER
        assert runtime.strategy_name == "ma_crossover"
        assert isinstance(runtime.strategy, MaCrossover)

    def test_execute_promoted_strategy_fails_gracefully_without_resolver(self):
        """Test that API fails gracefully when resolver not configured."""
        from trading_agent.execution.canonical.order_planner import InstrumentRules

        engine = ExecutionEngine(
            exchange_name="paper",
            initial_capital=100_000.0,
            instrument_rules=InstrumentRules(
                symbol="BTC/USDT",
                asset_class="spot",
                min_order_qty=0.0001,
                max_order_qty=100.0,
                qty_step=0.0001,
                price_precision=2,
                min_notional=10.0,
                max_leverage=1.0,
            ),
            # No promotion_store or artifact_store → no resolver
        )

        with pytest.raises(RuntimeError, match="RuntimeStrategyResolver"):
            engine.execute_promoted_strategy(
                symbol="BTC/USDT",
                timeframe="1h",
                environment="paper",
                observation=None,
                market_data=None,
            )

    def test_execute_promoted_strategy_fails_gracefully_without_instrument_rules(
        self,
        config: AuthorityConfig,
        promotion_store: PromotionStateStore,
        artifact_store: PersistentArtifactStore,
        temp_dir: Path,
    ):
        """Test that API fails gracefully when instrument_rules not configured."""
        engine = ExecutionEngine(
            exchange_name="paper",
            initial_capital=100_000.0,
            authority_config=config,
            promotion_store=promotion_store,
            artifact_store=artifact_store,
            # No instrument_rules → no execution_service
        )

        with pytest.raises(RuntimeError, match="instrument_rules"):
            engine.execute_promoted_strategy(
                symbol="BTC/USDT",
                timeframe="1h",
                environment="paper",
                observation=None,
                market_data=None,
            )

    def test_resolve_for_filters_by_symbol_timeframe(
        self,
        execution_engine: ExecutionEngine,
        btc_artifact: StrategyArtifact,
        promotion_store: PromotionStateStore,
        artifact_store: PersistentArtifactStore,
    ):
        """Test that resolve_for filters by (symbol, timeframe) before selecting latest."""
        # Create a second artifact for ETH with different timeframe
        eth_params = {"fast_period": 20, "slow_period": 60}
        eth_artifact = StrategyArtifact(
            strategy_name="ma_crossover",
            code_sha="eth456",
            data_manifest_sha="eth_data_sha",
            parameter_hash=sha256_hex(canonical_params(eth_params)),
            execution_model_version="1.0",
            framework_version="1.0",
            metadata={
                "symbol": "ETH/USDT",
                "timeframe": "4h",
                "parameters": eth_params,
            },
        )
        artifact_store.add(eth_artifact)

        # Promote ETH artifact
        promo_event = ResearchPromotionEvent(
            subject_artifact_id=eth_artifact.artifact_id,
            from_stage=ResearchStage.RESEARCH_VALIDATED,
            to_stage=ResearchStage.PAPER_ELIGIBLE,
            evidence_ids=("eth_wfo_sha",),
            actor="test",
            timestamp=datetime.now(UTC),
        )
        record = PromotionRecord(
            artifact_id=eth_artifact.artifact_id,
            stage=ResearchStage.PAPER_ELIGIBLE,
            latest_event=promo_event,
            updated_at=datetime.now(UTC),
        )
        promotion_store.upsert(record)

        # resolve_for for BTC/USDT 1h should return BTC artifact, not ETH
        runtime = execution_engine.resolver.resolve_for(
            symbol="BTC/USDT",
            timeframe="1h",
            environment=Environment.PAPER,
        )
        assert runtime is not None
        assert runtime.symbol == "BTC/USDT"
        assert runtime.timeframe == "1h"
        assert runtime.parameters == {"fast_period": 10, "slow_period": 30}

        # resolve_for for ETH/USDT 4h should return ETH artifact
        runtime_eth = execution_engine.resolver.resolve_for(
            symbol="ETH/USDT",
            timeframe="4h",
            environment=Environment.PAPER,
        )
        assert runtime_eth is not None
        assert runtime_eth.symbol == "ETH/USDT"
        assert runtime_eth.timeframe == "4h"
        assert runtime_eth.parameters == {"fast_period": 20, "slow_period": 60}

    def test_resolve_for_returns_none_for_missing_symbol_timeframe(
        self,
        execution_engine: ExecutionEngine,
        market_data,
        observation,
    ):
        """Test that resolve_for returns None when no matching artifact exists."""
        # No artifact for SOL/USDT 15m promoted
        runtime = execution_engine.resolver.resolve_for(
            symbol="SOL/USDT",
            timeframe="15m",
            environment=Environment.PAPER,
        )
        assert runtime is None

        # execute_promoted_strategy should return empty list
        orders = execution_engine.execute_promoted_strategy(
            symbol="SOL/USDT",
            timeframe="15m",
            environment="paper",
            observation=observation,
            market_data=market_data,
        )
        assert orders == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
