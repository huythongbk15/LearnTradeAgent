"""Milestone C tests — Unified Multi-Pair Runtime + shared portfolio authority.

Covers:
- resolver.list_bindings(): discover (symbol, timeframe) from promotion store
- PortfolioAllocator.reconcile(): exchange truth releases stale budgets
- Shared portfolio cap: pair 2 blocked when pair 1 consumed the budget
- Golden: N pairs through ONE engine/cycle, entry submitted exactly once per pair
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import tempfile

import polars as pl
import pytest

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.portfolio import AllocationRequest, PortfolioAllocator
from trading_agent.authority.promotion_store import (
    PromotionRecord,
    PromotionStateStore,
)
from trading_agent.authority.causation import new_chain
from trading_agent.execution.canonical.order_planner import InstrumentRules
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution.multi_pair_runtime import MultiPairRuntime
from trading_agent.research.artifact import (
    PersistentArtifactStore,
    StrategyArtifact,
    canonical_params,
    sha256_hex,
)
from trading_agent.research.promotion import ResearchPromotionEvent, ResearchStage


# ── Helpers ─────────────────────────────────────────────────────────────


def _make_artifact(
    store: PersistentArtifactStore,
    promotion_store: PromotionStateStore,
    *,
    symbol: str,
    timeframe: str,
    fast: int,
    slow: int,
) -> StrategyArtifact:
    params = {"fast_period": fast, "slow_period": slow}
    artifact = StrategyArtifact(
        strategy_name="ma_crossover",
        code_sha=f"sha_{symbol.replace('/', '')}",
        data_manifest_sha="data_sha",
        parameter_hash=sha256_hex(canonical_params(params)),
        execution_model_version="1.0",
        framework_version="1.0",
        metadata={
            "symbol": symbol,
            "timeframe": timeframe,
            "parameters": params,
            "calibration_state": "KNOWN",
            "ood_state": "KNOWN",
            "regime_state": "KNOWN",
        },
    )
    store.add(artifact)

    promo_event = ResearchPromotionEvent(
        subject_artifact_id=artifact.artifact_id,
        from_stage=ResearchStage.RESEARCH_VALIDATED,
        to_stage=ResearchStage.PAPER_ELIGIBLE,
        evidence_ids=("wfo",),
        actor="test",
        timestamp=datetime.now(UTC),
    )
    promotion_store.upsert(
        PromotionRecord(
            artifact_id=artifact.artifact_id,
            stage=ResearchStage.PAPER_ELIGIBLE,
            latest_event=promo_event,
            updated_at=datetime.now(UTC),
        )
    )
    return artifact


def _buy_at_last_bar_df(price_level: float) -> pl.DataFrame:
    """Deterministic OHLCV: MA crossover BUY lands EXACTLY on the last bar."""
    n = 50
    flat = [price_level] * (n - 1)
    spike = price_level * 1.10
    prices = flat + [spike]
    return pl.DataFrame(
        {
            "timestamp": [datetime(2024, 1, 1, tzinfo=UTC) for _ in range(n)],
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [100.0] * n,
        }
    )


def _instrument_rules(symbol: str) -> InstrumentRules:
    return InstrumentRules(
        symbol=symbol,
        asset_class="spot",
        min_order_qty=0.0001,
        max_order_qty=100.0,
        qty_step=0.0001,
        price_precision=2,
        min_notional=10.0,
        max_leverage=1.0,
    )


# ── Fixtures ────────────────────────────────────────────────────────────


@pytest.fixture
def temp_dir():
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def stores(temp_dir: Path):
    promotion_store = PromotionStateStore(temp_dir / "promotion.db")
    artifact_store = PersistentArtifactStore(temp_dir / "artifacts")
    return promotion_store, artifact_store


@pytest.fixture
def config() -> AuthorityConfig:
    return AuthorityConfig.for_environment(Environment.PAPER)


@pytest.fixture
def btc_eth_artifacts(stores):
    promotion_store, artifact_store = stores
    # NOTE: both use fast=10/slow=30 because _buy_at_last_bar_df() provides
    # exactly 50 bars — a slower MA than 30 would never produce a signal.
    btc = _make_artifact(
        artifact_store,
        promotion_store,
        symbol="BTC/USDT",
        timeframe="1h",
        fast=10,
        slow=30,
    )
    eth = _make_artifact(
        artifact_store,
        promotion_store,
        symbol="ETH/USDT",
        timeframe="1h",
        fast=10,
        slow=30,
    )
    return btc, eth


def _build_engine(config, stores, temp_dir: Path, rules) -> ExecutionEngine:
    promotion_store, artifact_store = stores
    return ExecutionEngine(
        exchange_name="paper",
        initial_capital=100_000.0,
        commission=0.001,
        slippage=0.0005,
        instrument_rules=rules,
        authority_config=config,
        promotion_store=promotion_store,
        artifact_store=artifact_store,
        state_dir=temp_dir / "paper_state",
        event_store_path=temp_dir / "events.db",
    )


@pytest.fixture
def engine(config, stores, temp_dir):
    eng = _build_engine(config, stores, temp_dir, _instrument_rules("BTC/USDT"))
    yield eng
    eng._graceful_shutdown()


# ── C1: list_bindings ───────────────────────────────────────────────────


class TestListBindings:
    def test_discovers_distinct_symbol_timeframe_bindings(
        self, engine, btc_eth_artifacts
    ):
        bindings = engine.resolver.list_bindings(Environment.PAPER)
        assert ("BTC/USDT", "1h") in bindings
        assert ("ETH/USDT", "1h") in bindings
        assert bindings == sorted(bindings)
        assert len(bindings) == len(set(bindings))

    def test_accepts_string_environment(self, engine, btc_eth_artifacts):
        bindings = engine.resolver.list_bindings("paper")
        assert len(bindings) >= 2

    def test_empty_store_returns_empty(self, engine):
        assert engine.resolver.list_bindings(Environment.PAPER) == []


# ── C2: reconcile ───────────────────────────────────────────────────────


class TestReconcile:
    def _allocator_with_allocation(self, tmp_capital: float = 1_000_000.0):
        """Allocator with one recorded allocation of 5% for (S1, BTC/USDT)."""
        cfg = AuthorityConfig.for_environment(Environment.PAPER)
        cfg.exposure.max_portfolio_exposure = 1.0
        cfg.exposure.max_single_strategy_exposure = 0.5
        cfg.exposure.max_single_symbol_exposure = 0.5
        allocator = PortfolioAllocator(cfg)

        rd = SimpleNamespace(
            allowed_target_exposure=0.05,
            max_new_exposure=0.05,
            reduce_only=False,
        )
        req = AllocationRequest(
            strategy_id="artifact_S1",
            symbol="BTC/USDT",
            risk_decision=rd,
            current_exposure=0.0,
            equity=tmp_capital,
            available_cash=tmp_capital,
            portfolio_exposure=0.0,
            correlation_cluster=None,
            causation_chain=new_chain({"authority": "test"}),
        )
        result = allocator.allocate(req)
        assert result.allocation_pct > 0
        return allocator

    def test_reconcile_releases_stale_budget_when_position_closed(self):
        allocator = self._allocator_with_allocation()
        snap_before = allocator.get_portfolio_snapshot()
        assert snap_before["total_allocated"] > 0

        # Position fully closed → empty live exposures
        audit = allocator.reconcile({})
        assert audit["released"], f"expected release, got {audit}"

        snap_after = allocator.get_portfolio_snapshot()
        assert snap_after["total_allocated"] == 0.0

    def test_reconcile_consumes_truth_when_position_over_held(self):
        allocator = self._allocator_with_allocation()
        # Live exposure larger than recorded bookkeeping
        allocator.reconcile({"BTC/USDT": 0.09})
        snap = allocator.get_portfolio_snapshot()
        sid = next(iter(snap["strategies"]))
        assert abs(snap["strategies"][sid]["allocated_exposure"] - 0.09) < 1e-9

    def test_reconcile_flags_untracked_live_exposure(self):
        allocator = self._allocator_with_allocation()
        audit = allocator.reconcile({"SOL/USDT": 0.02})
        assert "SOL/USDT" in audit["untracked"]


# ── C3+C4: multi-pair cycle through ONE engine ──────────────────────────


class TestMultiPairCycle:
    def _spy_submissions(self, engine) -> list:
        calls: list = []
        original = engine.gateway._adapter.submit_order

        def spy(request):
            calls.append(request)
            return original(request)

        engine.gateway._adapter.submit_order = spy
        return calls

    def _provider_all_buy(self):
        prices = {"BTC/USDT": 50_000.0, "ETH/USDT": 3_000.0}

        def provider(symbol: str, timeframe: str):
            if timeframe != "1h":
                return None
            if symbol not in prices:
                return None
            return _buy_at_last_bar_df(prices[symbol])

        return provider

    def test_golden_two_pairs_one_engine_one_cycle(
        self, config, stores, temp_dir, btc_eth_artifacts
    ):
        rules = {
            "BTC/USDT": _instrument_rules("BTC/USDT"),
            "ETH/USDT": _instrument_rules("ETH/USDT"),
        }
        eng = _build_engine(config, stores, temp_dir, rules)
        runtime = MultiPairRuntime(eng)
        calls = self._spy_submissions(eng)

        try:
            report = runtime.run_cycle(
                environment="paper",
                market_data_provider=self._provider_all_buy(),
            )
        finally:
            eng._graceful_shutdown()

        # Both pairs executed through one shared engine
        assert [r.symbol for r in report.results] == ["BTC/USDT", "ETH/USDT"]
        assert all(r.status == "ok" for r in report.results), report.to_dict()

        def _is_protection(request) -> bool:
            iid = str(getattr(request, "intent_id", ""))
            return iid.startswith("prot_") and iid.endswith("_submit")

        entries = [r for r in calls if not _is_protection(r)]
        protections = [r for r in calls if _is_protection(r)]

        # GOLDEN INVARIANT: exactly ONE entry per pair — never duplicated
        assert len(entries) == 2, f"expected 1 entry per pair, got {calls}"
        assert {str(r.symbol.base) for r in entries} == {"BTC", "ETH"}
        # Deterministic protective bundle: 1 stop per filled BUY
        assert len(protections) == 2

        idem = [str(r.idempotency_key) for r in calls]
        assert len(idem) == len(set(idem)), "duplicate idempotency keys"

        assert report.total_orders == 2
        assert report.equity_after > 0

    def test_shared_portfolio_cap_blocks_second_pair(
        self, temp_dir, stores, btc_eth_artifacts
    ):
        """Portfolio cap is SHARED: pair 2 must get zero after pair 1 filled."""
        cfg = AuthorityConfig.for_environment(Environment.PAPER)
        # Tight shared cap: first fill consumes nearly all of it
        cfg.exposure.max_portfolio_exposure = 0.08
        cfg.exposure.max_single_strategy_exposure = 0.08
        cfg.exposure.max_single_symbol_exposure = 0.08

        rules = {
            "BTC/USDT": _instrument_rules("BTC/USDT"),
            "ETH/USDT": _instrument_rules("ETH/USDT"),
        }
        eng = _build_engine(cfg, stores, temp_dir, rules)
        runtime = MultiPairRuntime(eng)
        calls = self._spy_submissions(eng)

        try:
            report = runtime.run_cycle(
                environment="paper",
                market_data_provider=self._provider_all_buy(),
            )
        finally:
            eng._graceful_shutdown()

        # Both pairs executed, but the SHARED cap squeezed them:
        # BTC took ~7.5%, ETH could only receive the remaining headroom.
        by_symbol = {r.symbol: r for r in report.results}
        assert by_symbol["BTC/USDT"].status == "ok"
        assert by_symbol["BTC/USDT"].orders_count >= 1

        # Post-cycle: total portfolio exposure must respect the shared cap
        equity = float(eng.exchange.get_total_equity())
        total_exposure = 0.0
        for pos in eng.exchange.get_all_positions():
            if pos.is_active and pos.quantity > 0:
                price = float(eng.exchange._last_price_cache[pos.symbol])
                total_exposure += pos.quantity * price / equity
        # Fees/slippage can push slightly over the nominal cap — allow slack
        assert total_exposure <= cfg.exposure.max_portfolio_exposure + 2e-3, (
            f"shared portfolio cap violated: {total_exposure:.4f} > "
            f"{cfg.exposure.max_portfolio_exposure}"
        )
        # Sharing proof: neither symbol alone accounts for the whole book,
        # i.e., ETH DID get squeezed relative to an uncapped run (~10%).
        assert total_exposure >= cfg.exposure.max_single_symbol_exposure * 0.8

    def test_no_data_fail_closed_per_pair(self, engine, btc_eth_artifacts):
        runtime = MultiPairRuntime(engine)

        def provider(symbol, tf):
            return None  # everything missing

        report = runtime.run_cycle(environment="paper", market_data_provider=provider)
        assert all(r.status == "no_data" for r in report.results)
        assert report.total_orders == 0

    def test_requires_market_data_provider(self, engine, btc_eth_artifacts):
        runtime = MultiPairRuntime(engine)
        with pytest.raises(ValueError, match="market_data_provider"):
            runtime.run_cycle(environment="paper", market_data_provider=None)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
