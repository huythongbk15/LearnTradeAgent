"""Milestone D — Research → Runtime bridge (PromotionHook) tests.

Covers:
- Golden bridge: lifecycle.promote(on_event=hook) → authoritative store →
  resolver.resolve_for() sees the strategy immediately (no restart).
- Idempotency: handling the same event twice converges.
- Atomic fail-closed: hook failure aborts the promotion (stage unchanged).
- Hot swap: promoting a newer artifact for the same (symbol, timeframe)
  makes the NEXT resolve_for return the new artifact.
- Loader integration: RuntimeLoader writes a manifest on bridged promotion.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.promotion_hook import BridgeError, PromotionHook
from trading_agent.authority.promotion_store import PromotionStateStore
from trading_agent.authority.resolver import RuntimeStrategyResolver
from trading_agent.research.artifact import (
    PersistentArtifactStore,
    StrategyArtifact,
    canonical_params,
    sha256_hex,
)
from trading_agent.research.lifecycle import PromotionError
from trading_agent.research.promotion import (
    EvidenceArtifact,
    EvidenceKind,
    EvidenceSource,
    ResearchLifecycle,
    ResearchPromotionEvent,
    ResearchStage,
)

NOW = datetime.now(UTC)


def _make_artifact(
    tmp_path: Path,
    store: PersistentArtifactStore,
    params: dict | None = None,
    symbol: str = "BTC/USDT",
    timeframe: str = "1h",
) -> StrategyArtifact:
    params = params or {"fast_period": 10, "slow_period": 30}
    artifact = StrategyArtifact(
        strategy_name="ma_crossover",
        code_sha=sha256_hex("ma_crossover_v1"),
        data_manifest_sha="data_sha_test",
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
    return artifact


def _evidence(kind: EvidenceKind, artifact_id: str, payload: dict) -> EvidenceArtifact:
    return EvidenceArtifact.create(
        kind=kind,
        subject_artifact_id=artifact_id,
        source=EvidenceSource.RESEARCH,
        payload=payload,
        validator="pytest-validator",
        created_at=NOW,
    )


def _research_evidence(artifact_id: str) -> list[EvidenceArtifact]:
    """Evidence required for EXPLORATORY → RESEARCH_VALIDATED."""
    return [
        _evidence(EvidenceKind.OUTER_OOS, artifact_id, {"net_return": 0.12}),
        _evidence(EvidenceKind.MINIMUM_TRADES, artifact_id, {"trade_count": 120}),
        _evidence(EvidenceKind.DEFLATED_SHARPE, artifact_id, {"dsr_probability": 0.98}),
        _evidence(EvidenceKind.PBO, artifact_id, {"pbo": 0.10}),
        _evidence(EvidenceKind.COST_STRESS, artifact_id, {"stressed_net_return": 0.04}),
        _evidence(
            EvidenceKind.PARAMETER_STABILITY, artifact_id, {"stability_score": 0.82}
        ),
    ]


def _integrity_evidence(artifact_id: str) -> list[EvidenceArtifact]:
    """Evidence required for RESEARCH_VALIDATED → PAPER_ELIGIBLE."""
    return [
        _evidence(
            EvidenceKind.ARTIFACT_INTEGRITY,
            artifact_id,
            {
                "verified_artifact_id": artifact_id,
                "integrity_failures": 0,
            },
        )
    ]


def _promote_to_paper(
    lifecycle: ResearchLifecycle,
    artifact_id: str,
    on_event,
) -> None:
    lifecycle.promote(
        ResearchStage.RESEARCH_VALIDATED,
        evidence=_research_evidence(artifact_id),
        actor="bridge-test",
        on_event=on_event,
    )
    lifecycle.promote(
        ResearchStage.PAPER_ELIGIBLE,
        evidence=_integrity_evidence(artifact_id),
        actor="bridge-test",
        on_event=on_event,
    )


@pytest.fixture
def paper_config() -> AuthorityConfig:
    cfg = AuthorityConfig.for_environment(Environment.PAPER)
    cfg.exposure.max_single_strategy_exposure = 0.2
    cfg.exposure.max_portfolio_exposure = 1.0
    return cfg


class TestGoldenBridge:
    def test_promote_with_hook_is_immediately_resolvable(self, tmp_path, paper_config):
        store = PersistentArtifactStore(tmp_path / "artifacts")
        promo = PromotionStateStore(tmp_path / "promotion.db")
        hook = PromotionHook(artifact_store=store, promotion_store=promo)

        artifact = _make_artifact(tmp_path, store)
        lifecycle = ResearchLifecycle(artifact.artifact_id)
        _promote_to_paper(lifecycle, artifact.artifact_id, hook.handle)

        assert promo.get_stage(artifact.artifact_id) is ResearchStage.PAPER_ELIGIBLE

        resolver = RuntimeStrategyResolver(
            config=paper_config, promotion_store=promo, artifact_store=store
        )
        runtime = resolver.resolve_for("BTC/USDT", "1h", Environment.PAPER)
        assert runtime is not None
        assert runtime.strategy_name == "ma_crossover"
        assert ("BTC/USDT", "1h") in resolver.list_bindings(Environment.PAPER)

    def test_hook_without_loader_persists_only(self, tmp_path):
        store = PersistentArtifactStore(tmp_path / "artifacts")
        promo = PromotionStateStore(tmp_path / "promotion.db")
        hook = PromotionHook(artifact_store=store, promotion_store=promo)

        artifact = _make_artifact(tmp_path, store)
        outcome = hook.handle(
            ResearchPromotionEvent(
                subject_artifact_id=artifact.artifact_id,
                from_stage=ResearchStage.EXPLORATORY,
                to_stage=ResearchStage.RESEARCH_VALIDATED,
                evidence_ids=(),
                actor="test",
            )
        )
        assert outcome.persisted is True
        assert outcome.loaded_into_runtime is False
        assert promo.get_stage(artifact.artifact_id) is (
            ResearchStage.RESEARCH_VALIDATED
        )

    def test_bridge_error_is_bridgeerror_not_silent(self, tmp_path):
        store = PersistentArtifactStore(tmp_path / "artifacts")
        promo = PromotionStateStore(tmp_path / "promotion.db")
        hook = PromotionHook(artifact_store=store, promotion_store=promo)

        # Event references an artifact that was never stored → fail-closed.
        ghost_event = ResearchPromotionEvent(
            subject_artifact_id="ghost_artifact",
            from_stage=ResearchStage.EXPLORATORY,
            to_stage=ResearchStage.RESEARCH_VALIDATED,
            evidence_ids=(),
            actor="test",
        )
        with pytest.raises(BridgeError, match="not found"):
            hook.handle(ghost_event)
        assert promo.get_stage("ghost_artifact") is None


class TestIdempotency:
    def test_double_handle_converges(self, tmp_path):
        store = PersistentArtifactStore(tmp_path / "artifacts")
        promo = PromotionStateStore(tmp_path / "promotion.db")
        hook = PromotionHook(artifact_store=store, promotion_store=promo)

        artifact = _make_artifact(tmp_path, store)
        event = ResearchPromotionEvent(
            subject_artifact_id=artifact.artifact_id,
            from_stage=ResearchStage.RESEARCH_VALIDATED,
            to_stage=ResearchStage.PAPER_ELIGIBLE,
            evidence_ids=("ev_1",),
            actor="test",
        )

        first = hook.handle(event)
        second = hook.handle(event)

        assert first.persisted and second.persisted
        assert second.idempotent_replay is True
        assert promo.get_stage(artifact.artifact_id) is ResearchStage.PAPER_ELIGIBLE


class TestAtomicFailClosed:
    def test_hook_failure_blocks_stage_change(self, tmp_path):
        store = PersistentArtifactStore(tmp_path / "artifacts")
        promo = PromotionStateStore(tmp_path / "promotion.db")
        hook = PromotionHook(artifact_store=store, promotion_store=promo)

        # Artifact NOT put into the store — the bridge must fail closed.
        orphan_params = {"fast_period": 5, "slow_period": 20}
        orphan_id = f"orphan_{sha256_hex(canonical_params(orphan_params))[:12]}"

        lifecycle = ResearchLifecycle(orphan_id)
        with pytest.raises(PromotionError, match="bridge failed"):
            lifecycle.promote(
                ResearchStage.RESEARCH_VALIDATED,
                evidence=_research_evidence(orphan_id),
                actor="bridge-test",
                on_event=hook.handle,
            )

        # Atomicity: stage unchanged, no events recorded, nothing persisted.
        assert lifecycle.stage is ResearchStage.EXPLORATORY
        assert lifecycle.events == []
        assert promo.get_stage(orphan_id) is None

    def test_unhooked_promotion_still_works_backwards_compat(self, tmp_path):
        lifecycle = ResearchLifecycle("compat_artifact")
        event = lifecycle.promote(
            ResearchStage.RESEARCH_VALIDATED,
            evidence=_research_evidence("compat_artifact"),
            actor="bridge-test",
        )
        assert lifecycle.stage is ResearchStage.RESEARCH_VALIDATED
        assert event.to_stage is ResearchStage.RESEARCH_VALIDATED


class TestHotSwap:
    def test_newer_artifact_wins_on_next_resolve(self, tmp_path, paper_config):
        store = PersistentArtifactStore(tmp_path / "artifacts")
        promo = PromotionStateStore(tmp_path / "promotion.db")
        hook = PromotionHook(artifact_store=store, promotion_store=promo)
        resolver = RuntimeStrategyResolver(
            config=paper_config, promotion_store=promo, artifact_store=store
        )

        v1 = _make_artifact(
            tmp_path, store, params={"fast_period": 10, "slow_period": 30}
        )
        _promote_to_paper(
            ResearchLifecycle(v1.artifact_id), v1.artifact_id, hook.handle
        )

        rt1 = resolver.resolve_for("BTC/USDT", "1h", Environment.PAPER)
        assert rt1 is not None
        assert rt1.artifact_id == v1.artifact_id

        # v2: different parameters → different content-addressed id, same binding.
        v2 = _make_artifact(
            tmp_path, store, params={"fast_period": 20, "slow_period": 60}
        )
        assert v2.artifact_id != v1.artifact_id
        _promote_to_paper(
            ResearchLifecycle(v2.artifact_id), v2.artifact_id, hook.handle
        )

        # NO restart: next resolve picks the newest eligible artifact.
        rt2 = resolver.resolve_for("BTC/USDT", "1h", Environment.PAPER)
        assert rt2 is not None
        assert rt2.artifact_id == v2.artifact_id


class TestLoaderIntegration:
    def test_bridged_promotion_loads_into_runtime_loader(self, tmp_path):
        from trading_agent.authority.loader import RuntimeLoader

        store = PersistentArtifactStore(tmp_path / "artifacts")
        promo = PromotionStateStore(tmp_path / "promotion.db")
        manifest_dir = tmp_path / "promoted_strategies"
        loader = RuntimeLoader(
            artifact_store=store,
            manifest_dir=manifest_dir,
            config=AuthorityConfig.for_environment(Environment.PAPER),
            promotion_store=promo,
        )
        hook = PromotionHook(
            artifact_store=store, promotion_store=promo, runtime_loader=loader
        )

        artifact = _make_artifact(tmp_path, store)
        lifecycle = ResearchLifecycle(artifact.artifact_id)
        outcomes: list = []
        _promote_to_paper(
            lifecycle,
            artifact.artifact_id,
            lambda ev: outcomes.append(hook.handle(ev)),
        )

        loaded = loader.get_loaded(artifact.artifact_id)
        assert loaded is not None
        assert loaded.manifest.strategy_name == "ma_crossover"
        manifests = list(manifest_dir.glob("*.json"))
        assert len(manifests) >= 1
        final_outcome = outcomes[-1]
        assert final_outcome.persisted is True
        assert final_outcome.loaded_into_runtime is True


@pytest.mark.slow
class TestConcurrentStoreAccess:
    """Regression: hot-reload watcher thread + main thread sharing one db path.

    Before the per-path lock + unique tmp names, two interleaved connections
    crashed with FileNotFoundError on os.replace(promotion.tmp → promotion.db)
    and could lose updates via stale snapshot replacement.
    """

    def test_parallel_instances_no_crash_no_lost_update(self, tmp_path):
        from concurrent.futures import ThreadPoolExecutor

        promo_a = PromotionStateStore(tmp_path / "promotion.db")
        promo_b = PromotionStateStore(tmp_path / "promotion.db")

        n = 48

        def worker(i: int) -> None:
            store = promo_a if i % 2 else promo_b
            artifact_id = f"artifact_{i}"
            event = ResearchPromotionEvent(
                subject_artifact_id=artifact_id,
                from_stage=ResearchStage.EXPLORATORY,
                to_stage=ResearchStage.PAPER_ELIGIBLE,
                evidence_ids=(),
                actor="race-test",
            )
            store.upsert_from_event(event)
            assert store.get_stage(artifact_id) is ResearchStage.PAPER_ELIGIBLE

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(worker, range(n)))

        # Every write survived — no lost updates from stale snapshots.
        assert promo_a.count() == n
        assert promo_b.count() == n

    def test_reader_writer_interleave_keeps_all_writes(self, tmp_path):
        import time
        from concurrent.futures import ThreadPoolExecutor

        promo_writer = PromotionStateStore(tmp_path / "promotion.db")
        promo_reader = PromotionStateStore(tmp_path / "promotion.db")

        stop = threading.Event()

        def reader() -> None:
            while not stop.is_set():
                promo_reader.list_eligible("paper")

        def writer(i: int) -> None:
            event = ResearchPromotionEvent(
                subject_artifact_id=f"w_{i}",
                from_stage=ResearchStage.RESEARCH_VALIDATED,
                to_stage=ResearchStage.PAPER_ELIGIBLE,
                evidence_ids=(),
                actor="race-test",
            )
            promo_writer.upsert_from_event(event)

        with ThreadPoolExecutor(max_workers=2) as pool:
            reader_future = pool.submit(reader)
            time.sleep(0.05)
            with ThreadPoolExecutor(max_workers=8) as writers:
                list(writers.map(writer, range(24)))
            stop.set()
            reader_future.result()

        assert promo_writer.count() == 24
