"""S5 adaptive-router safety, switching, and restart semantics."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import polars as pl

from trading_agent.authority.adaptive_router import (
    AdaptiveForecastRuntime,
    AdaptiveRouterConfig,
    AdaptiveStrategyRouter,
    HandoverState,
    RouterStateStore,
    RoutingDecision,
)
from trading_agent.authority.config import Environment
from trading_agent.ml.regime_detection import RegimePosterior
from trading_agent.research.forecast import MarketObservation
from trading_agent.strategies.canonical.candidates import FIRST_WAVE_DESCRIPTORS
from trading_agent.strategies.canonical.features import FEATURE_OHLCV_WINDOW, build_ohlcv_window
from trading_agent.research.selection_policy import (
    ParamArtifact,
    PolicyActivationService,
    PolicyStatus,
    SelectionPolicyArtifact,
    SelectionPolicyRegistry,
)


NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
KEY = b"adaptive-router-test-signing-key"
REGIME_POLICIES = {
    "trend": ("trend_following", 1.5),
    "mean_reversion": ("mean_reversion", 2.0),
    "high_vol": ("defensive", 1.2),
    "crisis": ("defensive", 1.2),
    "other": ("trend_following", 0.4),
}


def _posterior(
    observed_at: datetime,
    probabilities: tuple[float, float, float, float, float],
    *,
    ood_score: float = 0.1,
    model_id: str = "regime-model-v1",
    generated_at: datetime | None = None,
) -> RegimePosterior:
    return RegimePosterior(
        *probabilities,
        model_id=model_id,
        fitted_start=observed_at - timedelta(days=90),
        fitted_end=observed_at - timedelta(days=1),
        generated_at=generated_at or observed_at,
        ood_score=ood_score,
    )


def _active_registry(tmp_path) -> SelectionPolicyRegistry:
    registry = SelectionPolicyRegistry(tmp_path / "policies")
    service = PolicyActivationService(
        registry,
        signing_key=KEY,
        key_id="release-key",
        audit_path=tmp_path / "policy-activation.jsonl",
    )
    for index, (regime, (strategy_id, score)) in enumerate(REGIME_POLICIES.items()):
        created_at = NOW - timedelta(days=1, minutes=index)
        policy = SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime=regime,
            incumbent=ParamArtifact(
                strategy_id, {"window": 10 + index}, code_sha="e" * 64
            ),
            scores={"selection_score": score},
            evidence_ids=(f"sha256:study-{regime}", f"sha256:outer-{regime}"),
            validity_start=created_at,
            validity_end=NOW + timedelta(days=29),
            risk_cap=0.25,
            status=PolicyStatus.VALIDATED,
            created_at=created_at,
            policy_commit_sha="a" * 40,
            policy_data_manifest_sha="b" * 64,
            policy_feature_manifest_sha="c" * 64,
            policy_release_digest="sha256:" + "d" * 64,
            promotion_stage="paper_eligible",
        )
        registry.add(policy)
        service.activate(
            policy.policy_id,
            actor="release-operator",
            ticket=f"S5-{index}",
            now=created_at + timedelta(minutes=1),
        )
    return registry


def _router(tmp_path, *, config: AdaptiveRouterConfig) -> AdaptiveStrategyRouter:
    return AdaptiveStrategyRouter(
        _active_registry(tmp_path),
        verification_key=KEY,
        key_id="release-key",
        state_store=RouterStateStore(tmp_path / "state"),
        audit_path=tmp_path / "routing-decisions.jsonl",
        config=config,
    )


def _route(
    router: AdaptiveStrategyRouter,
    posterior: RegimePosterior,
    observed_at: datetime,
    *,
    flat: bool = True,
    owner: str | None = None,
):
    return router.route(
        symbol="BTC/USDT",
        timeframe="1h",
        posterior=posterior,
        observed_at=observed_at,
        position_is_flat=flat,
        position_owner_strategy_id=owner,
    )


def test_regime_posterior_has_provenance_fingerprint_and_freshness():
    posterior = _posterior(NOW, (0.8, 0.05, 0.05, 0.05, 0.05))
    same = _posterior(NOW, (0.8, 0.05, 0.05, 0.05, 0.05))

    assert posterior.fingerprint == same.fingerprint
    assert posterior.is_production_ready(now=NOW + timedelta(minutes=1))
    assert not _posterior(
        NOW,
        (0.8, 0.05, 0.05, 0.05, 0.05),
        model_id="unknown",
    ).is_production_ready(now=NOW)


def test_router_uses_full_posterior_weighting_not_argmax_only(tmp_path):
    router = _router(
        tmp_path,
        config=AdaptiveRouterConfig(
            persistence_bars=1,
            min_dwell_bars=1,
            cooldown_bars=0,
            entropy_threshold=1.0,
        ),
    )
    # Trend is the argmax regime, but the stronger mean-reversion policy wins
    # after probability-weighted scoring across the complete posterior.
    posterior = _posterior(NOW, (0.45, 0.40, 0.05, 0.05, 0.05))
    decision = _route(router, posterior, NOW)

    assert decision.handover_state is HandoverState.ACTIVATE
    assert decision.chosen_strategy_id == "mean_reversion"
    assert decision.incumbent_strategy_id is None
    assert decision.allow_new_exposure


@pytest.mark.parametrize(
    ("posterior", "reason"),
    [
        (
            _posterior(NOW, (0.2, 0.2, 0.2, 0.2, 0.2)),
            "POSTERIOR_HIGH_ENTROPY",
        ),
        (
            _posterior(NOW, (0.8, 0.05, 0.05, 0.05, 0.05), ood_score=0.9),
            "POSTERIOR_STALE_OOD_OR_UNVERSIONED",
        ),
        (
            _posterior(
                NOW,
                (0.8, 0.05, 0.05, 0.05, 0.05),
                generated_at=NOW - timedelta(hours=3),
            ),
            "POSTERIOR_STALE_OOD_OR_UNVERSIONED",
        ),
    ],
)
def test_router_fails_closed_on_uncertain_or_invalid_posterior(
    tmp_path, posterior, reason
):
    router = _router(tmp_path, config=AdaptiveRouterConfig(entropy_threshold=0.75))
    decision = _route(router, posterior, NOW)

    assert decision.reason == reason
    assert decision.chosen_strategy_id is None
    assert not decision.allow_new_exposure
    assert decision.exposure_multiplier == 0.0


def test_challenger_requires_persistent_closed_bar_evidence(tmp_path):
    router = _router(
        tmp_path,
        config=AdaptiveRouterConfig(
            persistence_bars=2,
            min_dwell_bars=1,
            cooldown_bars=0,
            entropy_threshold=1.0,
        ),
    )
    posterior = _posterior(NOW, (0.8, 0.05, 0.05, 0.05, 0.05))

    first = _route(router, posterior, NOW)
    second_time = NOW + timedelta(hours=1)
    second = _route(
        router,
        _posterior(second_time, posterior.values),
        second_time,
    )

    assert first.handover_state is HandoverState.SWITCH_PENDING
    assert not first.allow_new_exposure
    assert second.handover_state is HandoverState.ACTIVATE
    assert second.chosen_strategy_id == "trend_following"


def test_open_position_owner_is_pinned_until_flat_then_switches(tmp_path):
    router = _router(
        tmp_path,
        config=AdaptiveRouterConfig(
            persistence_bars=1,
            min_dwell_bars=1,
            cooldown_bars=0,
            entropy_threshold=1.0,
        ),
    )
    trend = _posterior(NOW, (0.8, 0.05, 0.05, 0.05, 0.05))
    established = _route(router, trend, NOW)
    assert established.chosen_strategy_id == "trend_following"

    dwell_time = NOW + timedelta(hours=1)
    retained = _route(router, _posterior(dwell_time, trend.values), dwell_time)
    assert retained.reason == "INCUMBENT_RETAINED"

    switch_time = NOW + timedelta(hours=2)
    switch = _route(
        router,
        _posterior(switch_time, (0.05, 0.8, 0.05, 0.05, 0.05)),
        switch_time,
        flat=False,
        owner="trend_following",
    )
    assert switch.handover_state is HandoverState.WAIT_FLAT
    assert switch.chosen_strategy_id == "trend_following"
    assert not switch.allow_new_exposure

    flat_time = switch_time + timedelta(hours=1)
    activated = _route(
        router,
        _posterior(flat_time, (0.05, 0.8, 0.05, 0.05, 0.05)),
        flat_time,
    )
    assert activated.handover_state is HandoverState.ACTIVATE
    assert activated.incumbent_strategy_id == "trend_following"
    assert activated.chosen_strategy_id == "mean_reversion"


def test_restart_replays_idempotently_and_detects_state_tampering(tmp_path):
    config = AdaptiveRouterConfig(
        persistence_bars=1,
        min_dwell_bars=1,
        cooldown_bars=0,
        entropy_threshold=1.0,
    )
    router = _router(tmp_path, config=config)
    posterior = _posterior(NOW, (0.8, 0.05, 0.05, 0.05, 0.05))
    original = _route(router, posterior, NOW)

    restarted = AdaptiveStrategyRouter(
        router.policy_registry,
        verification_key=KEY,
        key_id="release-key",
        state_store=RouterStateStore(tmp_path / "state"),
        audit_path=tmp_path / "routing-decisions.jsonl",
        config=config,
    )
    replay = _route(restarted, posterior, NOW)
    assert replay.decision_id == original.decision_id
    assert len((tmp_path / "routing-decisions.jsonl").read_text().splitlines()) == 1

    state_path = tmp_path / "state" / "BTC_USDT__1h.json"
    stored = json.loads(state_path.read_text())
    stored["state"]["incumbent_strategy_id"] = "tampered"
    state_path.write_text(json.dumps(stored))
    with pytest.raises(ValueError, match="checksum mismatch"):
        _route(
            restarted,
            _posterior(NOW + timedelta(hours=1), posterior.values),
            NOW + timedelta(hours=1),
        )


def test_forecast_runtime_preserves_router_abstention(tmp_path):
    router = _router(
        tmp_path,
        config=AdaptiveRouterConfig(
            entropy_threshold=0.75,
            persistence_bars=1,
            min_dwell_bars=1,
            cooldown_bars=0,
        ),
    )
    posterior = _posterior(NOW, (0.2, 0.2, 0.2, 0.2, 0.2))
    decision = _route(router, posterior, NOW)
    runtime = AdaptiveForecastRuntime(
        router.policy_registry,
        verification_key=KEY,
        key_id="release-key",
    )
    observation = MarketObservation(
        symbol="BTC/USDT",
        observed_at=NOW,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
    )

    result = runtime.forecast(decision, observation)
    assert result.forecast is None
    assert not result.executable


def test_forecast_runtime_resolves_signed_policy_to_parameterized_adapter(tmp_path):
    registry = SelectionPolicyRegistry(tmp_path / "policies")
    service = PolicyActivationService(
        registry,
        signing_key=KEY,
        key_id="release-key",
        audit_path=tmp_path / "policy-activation.jsonl",
    )
    for index, regime in enumerate(("trend", "mean_reversion", "high_vol", "crisis", "other")):
        descriptor = FIRST_WAVE_DESCRIPTORS["rsi"]
        created_at = NOW - timedelta(days=1, minutes=index)
        policy = SelectionPolicyArtifact(
            symbol="BTC/USDT",
            timeframe="1h",
            regime=regime,
            incumbent=ParamArtifact(
                "rsi", {"period": 14}, code_sha=descriptor.code_sha
            ),
            scores={"selection_score": 1.0},
            evidence_ids=(f"sha256:{regime}",),
            validity_start=created_at,
            validity_end=NOW + timedelta(days=1),
            status=PolicyStatus.VALIDATED,
            created_at=created_at,
            policy_commit_sha="a" * 40,
            policy_data_manifest_sha="b" * 64,
            policy_feature_manifest_sha="c" * 64,
            policy_release_digest="sha256:" + "d" * 64,
            promotion_stage="paper_eligible",
        )
        registry.add(policy)
        service.activate(
            policy.policy_id,
            actor="operator",
            ticket=f"S5-RUNTIME-{index}",
            now=created_at + timedelta(minutes=1),
        )
    router = AdaptiveStrategyRouter(
        registry,
        verification_key=KEY,
        key_id="release-key",
        state_store=RouterStateStore(tmp_path / "state"),
        audit_path=tmp_path / "routing.jsonl",
        config=AdaptiveRouterConfig(
            persistence_bars=1,
            min_dwell_bars=1,
            cooldown_bars=0,
            entropy_threshold=1.0,
        ),
    )
    decision = _route(
        router,
        _posterior(NOW, (0.8, 0.05, 0.05, 0.05, 0.05)),
        NOW,
    )
    frame = pl.DataFrame(
        {
            "time": [NOW - timedelta(hours=18 - i) for i in range(18)],
            "open": [100.0 + i * 0.1 for i in range(18)],
            "high": [101.0 + i * 0.1 for i in range(18)],
            "low": [99.0 + i * 0.1 for i in range(18)],
            "close": [100.5 + i * 0.1 for i in range(18)],
            "volume": [10.0] * 18,
        }
    )
    observation = MarketObservation(
        symbol="BTC/USDT",
        observed_at=NOW,
        open=102.0,
        high=103.0,
        low=101.0,
        close=102.5,
        volume=10.0,
        features={
            FEATURE_OHLCV_WINDOW: build_ohlcv_window(
                frame, observed_at=NOW, bars=17
            )
        },
    )
    result = AdaptiveForecastRuntime(
        registry,
        verification_key=KEY,
        key_id="release-key",
    ).forecast(decision, observation)

    assert result.executable
    assert result.forecast is not None
    assert result.strategy_descriptor_id == FIRST_WAVE_DESCRIPTORS["rsi"].descriptor_id


def test_research_only_candidate_cannot_be_loaded_in_paper_environment(tmp_path):
    registry = SelectionPolicyRegistry(tmp_path / "policies")
    service = PolicyActivationService(
        registry,
        signing_key=KEY,
        key_id="release-key",
        audit_path=tmp_path / "policy-activation.jsonl",
    )
    descriptor = FIRST_WAVE_DESCRIPTORS["rsi"]
    policy = SelectionPolicyArtifact(
        symbol="BTC/USDT",
        timeframe="1h",
        regime="trend",
        incumbent=ParamArtifact("rsi", {"period": 14}, code_sha=descriptor.code_sha),
        scores={"selection_score": 1.0},
        evidence_ids=("sha256:evidence",),
        validity_start=NOW - timedelta(days=1),
        validity_end=NOW + timedelta(days=1),
        status=PolicyStatus.VALIDATED,
        created_at=NOW - timedelta(days=1),
        policy_commit_sha="a" * 40,
        policy_data_manifest_sha="b" * 64,
        policy_feature_manifest_sha="c" * 64,
        policy_release_digest="sha256:" + "d" * 64,
        promotion_stage="paper_eligible",
    )
    registry.add(policy)
    active = service.activate(
        policy.policy_id,
        actor="operator",
        ticket="PAPER-BLOCK",
        now=NOW,
    )
    decision = RoutingDecision(
        symbol="BTC/USDT",
        timeframe="1h",
        observed_at=NOW,
        posterior_fingerprint=_posterior(
            NOW, (0.8, 0.05, 0.05, 0.05, 0.05)
        ).fingerprint,
        policy_ids=(active.policy_id,),
        incumbent_strategy_id=None,
        challenger_strategy_id="rsi",
        chosen_strategy_id="rsi",
        chosen_policy_id=active.policy_id,
        chosen_params={"period": 14},
        handover_state=HandoverState.ACTIVATE,
        reason="test",
        allow_new_exposure=True,
        exposure_multiplier=0.5,
        candidate_score=1.0,
        incumbent_score=None,
        position_owner_strategy_id=None,
    )
    runtime = AdaptiveForecastRuntime(
        registry,
        verification_key=KEY,
        key_id="release-key",
        environment=Environment.PAPER,
    )
    observation = MarketObservation(
        symbol="BTC/USDT",
        observed_at=NOW,
        open=100.0,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=10.0,
    )
    with pytest.raises(ValueError, match="research_only"):
        runtime.forecast(decision, observation)
