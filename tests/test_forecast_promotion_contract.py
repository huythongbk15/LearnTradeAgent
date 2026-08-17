from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from trading_agent.research.calibration import CalibrationState
from trading_agent.research.forecast import (
    DecisionEnvironment,
    Forecast,
    ForecastRiskPolicy,
    MarketObservation,
    StrategyRiskPipeline,
    StrategyRuntime,
)
from trading_agent.research.lifecycle import ArtifactLifecycle, PromotionError, PromotionState
from trading_agent.research.promotion import (
    EvidenceArtifact,
    EvidenceKind,
    EvidenceSource,
    ResearchLifecycle,
    ResearchPromotionGate,
    ResearchStage,
)


NOW = datetime(2026, 1, 1, tzinfo=UTC)
SUBJECT = "model_sha256_0123456789"


def forecast(**overrides) -> Forecast:
    values = {
        "expected_excess_return": 0.01,
        "horizon": 3600,
        "lower_bound": 0.005,
        "upper_bound": 0.015,
        "direction_probability": 0.70,
        "calibration_state": CalibrationState.CALIBRATED,
        "ood_score": 0.05,
        "model_artifact_id": SUBJECT,
        "generated_at": NOW,
        "metadata": {"feature_set": "v1", "nested": {"lookback": 20}},
    }
    values.update(overrides)
    return Forecast(**values)


def observation() -> MarketObservation:
    return MarketObservation(
        symbol="BTC/USDT",
        observed_at=NOW,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=10.0,
        features={"volatility": 0.01},
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"expected_excess_return": float("nan")},
        {"horizon": 0},
        {"lower_bound": 0.02},
        {"direction_probability": 1.01},
        {"ood_score": -0.01},
        {"model_artifact_id": ""},
        {"generated_at": datetime(2026, 1, 1)},
    ],
)
def test_forecast_rejects_invalid_or_ambiguous_values(overrides) -> None:
    with pytest.raises((TypeError, ValueError)):
        forecast(**overrides)


def test_forecast_metadata_is_deeply_immutable_and_content_addressed() -> None:
    item = forecast()
    with pytest.raises(TypeError):
        item.metadata["new"] = "value"
    with pytest.raises(TypeError):
        item.metadata["nested"]["lookback"] = 50
    assert item.fingerprint == forecast().fingerprint


def test_same_forecast_produces_same_risk_decision() -> None:
    policy = ForecastRiskPolicy()
    first = policy.evaluate(forecast(), requested_exposure=0.8)
    second = policy.evaluate(forecast(), requested_exposure=0.8)
    assert first == second
    assert first.approved
    assert 0.0 < first.allowed_exposure < first.requested_exposure


def test_worse_uncertainty_cannot_increase_exposure() -> None:
    policy = ForecastRiskPolicy()
    base = policy.evaluate(forecast(), requested_exposure=1.0)
    wider = policy.evaluate(
        forecast(lower_bound=0.001, upper_bound=0.019), requested_exposure=1.0
    )
    more_ood = policy.evaluate(forecast(ood_score=0.40), requested_exposure=1.0)
    higher_ece = policy.evaluate(
        forecast(), requested_exposure=1.0, calibration_ece=0.25
    )
    assert wider.allowed_exposure <= base.allowed_exposure
    assert more_ood.allowed_exposure <= base.allowed_exposure
    assert higher_ece.allowed_exposure <= base.allowed_exposure


def test_uncalibrated_or_zero_crossing_forecast_cannot_increase_risk() -> None:
    policy = ForecastRiskPolicy()
    uncalibrated = policy.evaluate(
        forecast(calibration_state=CalibrationState.UNCALIBRATED),
        requested_exposure=1.0,
    )
    crossing = policy.evaluate(
        forecast(lower_bound=-0.01, upper_bound=0.02), requested_exposure=1.0
    )
    assert not uncalibrated.approved and uncalibrated.allowed_exposure == 0.0
    assert not crossing.approved and crossing.allowed_exposure == 0.0


class StaticForecastStrategy:
    def forecast(self, market_observation: MarketObservation) -> Forecast:
        assert market_observation.symbol == "BTC/USDT"
        return forecast()


class RecordingAdapter:
    def __init__(self, environment: DecisionEnvironment) -> None:
        self.environment = environment
        self.published = []

    def publish(self, decision) -> None:
        self.published.append(decision)


def test_all_environments_run_identical_strategy_and_risk_logic() -> None:
    pipeline = StrategyRiskPipeline(
        StaticForecastStrategy(), ForecastRiskPolicy(), requested_exposure=0.5
    )
    outputs = []
    for environment in DecisionEnvironment:
        adapter = RecordingAdapter(environment)
        output = StrategyRuntime(pipeline, adapter).on_observation(observation())
        outputs.append(output)
        assert adapter.published == [output]
    assert len({item.forecast.fingerprint for item in outputs}) == 1
    assert len({item.risk_decision.decision_id for item in outputs}) == 1
    assert not hasattr(StaticForecastStrategy, "place_order")


def evidence(
    kind: EvidenceKind,
    payload: dict,
    source: EvidenceSource = EvidenceSource.RESEARCH,
) -> EvidenceArtifact:
    return EvidenceArtifact.create(
        kind=kind,
        subject_artifact_id=SUBJECT,
        source=source,
        payload=payload,
        validator="pytest-validator",
        created_at=NOW,
    )


def research_evidence() -> list[EvidenceArtifact]:
    return [
        evidence(EvidenceKind.OUTER_OOS, {"net_return": 0.12}),
        evidence(EvidenceKind.MINIMUM_TRADES, {"trade_count": 120}),
        evidence(EvidenceKind.DEFLATED_SHARPE, {"dsr_probability": 0.98}),
        evidence(EvidenceKind.PBO, {"pbo": 0.10}),
        evidence(EvidenceKind.COST_STRESS, {"stressed_net_return": 0.04}),
        evidence(EvidenceKind.PARAMETER_STABILITY, {"stability_score": 0.82}),
    ]


def test_evidence_is_content_addressed_and_tamper_evident() -> None:
    original = evidence(EvidenceKind.OUTER_OOS, {"net_return": 0.12})
    with pytest.raises(ValueError, match="content hash"):
        replace(original, payload={"net_return": 9.0})
    with pytest.raises(TypeError):
        original.payload["net_return"] = 9.0


def test_boolean_integrity_and_promotion_bypasses_are_rejected() -> None:
    legacy = ArtifactLifecycle(SUBJECT)
    with pytest.raises(PromotionError, match="boolean assertions"):
        legacy.transition(PromotionState.REVIEWED, artifact_ok=True)
    with pytest.raises(TypeError, match="sequence"):
        ResearchPromotionGate().assess(SUBJECT, ResearchStage.RESEARCH_VALIDATED, True)


def test_missing_evidence_never_improves_promotion_assessment() -> None:
    gate = ResearchPromotionGate()
    complete = gate.assess(SUBJECT, ResearchStage.RESEARCH_VALIDATED, research_evidence())
    incomplete = gate.assess(
        SUBJECT, ResearchStage.RESEARCH_VALIDATED, research_evidence()[:-1]
    )
    assert complete.passed
    assert not incomplete.passed
    assert len(incomplete.satisfied) < len(complete.satisfied)
    assert EvidenceKind.PARAMETER_STABILITY in incomplete.missing


def test_shadow_calibration_must_be_empirical_and_from_shadow() -> None:
    gate = ResearchPromotionGate()
    fake = evidence(
        EvidenceKind.EMPIRICAL_CALIBRATION,
        {"status": "empirical", "sample_count": 100, "ece": 0.02},
        EvidenceSource.RESEARCH,
    )
    drift = evidence(
        EvidenceKind.DRIFT_UNCERTAINTY,
        {"health_state": "healthy"},
        EvidenceSource.SHADOW,
    )
    result = gate.assess(SUBJECT, ResearchStage.SHADOW_ELIGIBLE, [fake, drift])
    assert not result.passed
    assert "empirical_calibration:non_shadow_source" in result.failed


def test_full_ladder_requires_structured_stage_specific_evidence() -> None:
    lifecycle = ResearchLifecycle(SUBJECT)
    lifecycle.promote(
        ResearchStage.RESEARCH_VALIDATED,
        evidence=research_evidence(),
        actor="research-reviewer",
    )
    integrity = evidence(
        EvidenceKind.ARTIFACT_INTEGRITY,
        {"verified_artifact_id": SUBJECT, "integrity_failures": 0},
        EvidenceSource.SYSTEM,
    )
    lifecycle.promote(
        ResearchStage.PAPER_ELIGIBLE, evidence=[integrity], actor="artifact-store"
    )
    lifecycle.promote(
        ResearchStage.TESTNET_ELIGIBLE,
        evidence=[
            evidence(
                EvidenceKind.EXECUTION_SIMULATION,
                {"scenarios": 1000, "invariant_breaches": 0},
                EvidenceSource.SIMULATOR,
            ),
            evidence(
                EvidenceKind.REALITY_GAP,
                {"score": 0.10, "breach_count": 0},
                EvidenceSource.TESTNET,
            ),
        ],
        actor="execution-reviewer",
    )
    lifecycle.promote(
        ResearchStage.SHADOW_ELIGIBLE,
        evidence=[
            evidence(
                EvidenceKind.EMPIRICAL_CALIBRATION,
                {"status": "empirical", "sample_count": 300, "ece": 0.03},
                EvidenceSource.SHADOW,
            ),
            evidence(
                EvidenceKind.DRIFT_UNCERTAINTY,
                {"health_state": "healthy"},
                EvidenceSource.SHADOW,
            ),
        ],
        actor="model-risk",
    )
    lifecycle.promote(
        ResearchStage.CANARY_ELIGIBLE,
        evidence=[
            evidence(
                EvidenceKind.TESTNET_OPERATIONAL,
                {"days": 35, "unresolved_orders": 0},
                EvidenceSource.TESTNET,
            ),
            evidence(
                EvidenceKind.SHADOW_OPERATIONAL,
                {"days": 35, "critical_alerts": 0},
                EvidenceSource.SHADOW,
            ),
            evidence(
                EvidenceKind.OPERATOR_APPROVAL,
                {"approver": "operator-a", "ticket": "OPS-101"},
                EvidenceSource.OPERATOR,
            ),
        ],
        actor="operator-a",
    )
    lifecycle.promote(ResearchStage.CANARY, evidence=[], actor="operator-a")
    lifecycle.promote(
        ResearchStage.PRODUCTION,
        evidence=[
            evidence(
                EvidenceKind.CANARY_OPERATIONAL,
                {"days": 31, "safety_breaches": 0},
                EvidenceSource.CANARY,
            ),
            evidence(
                EvidenceKind.PRODUCTION_APPROVAL,
                {"approver": "operator-b", "ticket": "OPS-202"},
                EvidenceSource.OPERATOR,
            ),
        ],
        actor="operator-b",
    )
    assert lifecycle.stage == ResearchStage.PRODUCTION
    assert len(lifecycle.events) == 7


def test_promotion_cannot_skip_a_stage() -> None:
    lifecycle = ResearchLifecycle(SUBJECT)
    with pytest.raises(PromotionError, match="stage skipping"):
        lifecycle.promote(
            ResearchStage.PAPER_ELIGIBLE,
            evidence=research_evidence(),
            actor="reviewer",
        )


def test_legacy_plugin_adapter_fails_explicitly_instead_of_silent_empty_output() -> None:
    from trading_agent.strategies.plugins.adapters import MaCrossoverPluginStrategy

    plugin = MaCrossoverPluginStrategy()
    with pytest.raises(NotImplementedError, match="canonical ForecastStrategy"):
        plugin.on_bar(object())
