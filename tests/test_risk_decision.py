"""Tests for RiskDecision semantics."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_agent.agents.risk_decision import RiskDecision, RiskLevel
from trading_agent.execution.canonical.risk_decision import (
    EvidenceState,
    RiskReason,
    UnifiedRiskDecision,
)


class TestRiskDecision:
    def test_high_risk_blocks_new_exposure_and_allows_exit(self):
        decision = RiskDecision(
            risk_level=RiskLevel.HIGH,
            target_exposure_pct=0.0,
            max_new_exposure_pct=0.0,
            reduce_only=True,
        )
        assert decision.max_new_exposure_pct == 0.0
        assert decision.reduce_only is True

    def test_low_risk_allows_new_exposure(self):
        decision = RiskDecision(
            risk_level=RiskLevel.LOW,
            target_exposure_pct=0.25,
            max_new_exposure_pct=0.25,
            reduce_only=False,
        )
        assert decision.max_new_exposure_pct == 0.25
        assert decision.reduce_only is False

    def test_extreme_risk_blocks_new_exposure(self):
        decision = RiskDecision(
            risk_level=RiskLevel.EXTREME,
            target_exposure_pct=0.0,
            max_new_exposure_pct=0.0,
            reduce_only=True,
        )
        assert decision.max_new_exposure_pct == 0.0
        assert decision.reduce_only is True

    def test_from_legacy_high_risk_maps_to_zero(self):
        decision = RiskDecision.from_legacy(
            max_position_size_pct=0.0, risk_level="HIGH"
        )
        assert decision.risk_level == RiskLevel.HIGH
        assert decision.max_new_exposure_pct == 0.0
        assert decision.reduce_only is True

    def test_from_legacy_low_risk_preserves_size(self):
        decision = RiskDecision.from_legacy(max_position_size_pct=0.3, risk_level="LOW")
        assert decision.risk_level == RiskLevel.LOW
        assert decision.max_new_exposure_pct == pytest.approx(0.3)
        assert decision.reduce_only is False

    def test_invalid_max_new_exposure_above_target_raises(self):
        with pytest.raises(ValueError):
            RiskDecision(
                target_exposure_pct=0.2,
                max_new_exposure_pct=0.3,
            )

    def test_out_of_range_raises(self):
        with pytest.raises(ValueError):
            RiskDecision(target_exposure_pct=1.5, max_new_exposure_pct=-0.1)


# ── UnifiedRiskDecision serialization ─────────────────────────────────


class TestUnifiedRiskDecisionSerialization:
    def test_round_trip_preserves_all_fields(self):
        decision = UnifiedRiskDecision(
            decision_id="d1",
            forecast_fingerprint="fp-1",
            model_artifact_id="m1",
            requested_target_exposure=0.5,
            allowed_target_exposure=0.4,
            max_new_exposure=0.3,
            reduce_only=False,
            risk_level=RiskLevel.LOW,
            reason_codes=(RiskReason.APPROVED, RiskReason.CALIBRATION_NOT_CURRENT),
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
            created_at=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            metadata={"source": "test"},
            warnings=("warn-1",),
        )
        data = decision.to_dict()
        restored = UnifiedRiskDecision.from_dict(data)
        assert restored.decision_id == decision.decision_id
        assert restored.forecast_fingerprint == decision.forecast_fingerprint
        assert restored.model_artifact_id == decision.model_artifact_id
        assert restored.requested_target_exposure == pytest.approx(decision.requested_target_exposure)
        assert restored.allowed_target_exposure == pytest.approx(decision.allowed_target_exposure)
        assert restored.max_new_exposure == pytest.approx(decision.max_new_exposure)
        assert restored.reduce_only == decision.reduce_only
        assert restored.risk_level == decision.risk_level
        assert restored.reason_codes == decision.reason_codes
        assert restored.calibration_state == decision.calibration_state
        assert restored.calibration_artifact_id == decision.calibration_artifact_id
        assert restored.calibration_ece == pytest.approx(decision.calibration_ece)
        assert restored.ood_state == decision.ood_state
        assert restored.ood_score == pytest.approx(decision.ood_score)
        assert restored.regime_state == decision.regime_state
        assert restored.regime_entropy == pytest.approx(decision.regime_entropy)
        assert restored.interval_width == pytest.approx(decision.interval_width)
        assert restored.created_at == decision.created_at
        assert restored.metadata == decision.metadata
        assert restored.warnings == decision.warnings

    def test_round_trip_with_all_evidence_states(self):
        for state in EvidenceState:
            decision = UnifiedRiskDecision(
                decision_id="d-evidence",
                forecast_fingerprint="fp-evidence",
                model_artifact_id="m-evidence",
                requested_target_exposure=0.3,
                allowed_target_exposure=0.2,
                max_new_exposure=0.1,
                reduce_only=False,
                risk_level=RiskLevel.MEDIUM,
                reason_codes=(),
                calibration_state=state,
                calibration_artifact_id="cal-evidence",
                calibration_ece=0.05 if state is EvidenceState.KNOWN else 1.0,
                ood_state=state,
                ood_score=0.05 if state is EvidenceState.KNOWN else 1.0,
                regime_state=state,
                regime_entropy=0.1 if state is EvidenceState.KNOWN else 1.0,
                interval_width=0.1 if state is EvidenceState.KNOWN else 1.0,
                created_at=datetime.now(UTC),
            )
            data = decision.to_dict()
            restored = UnifiedRiskDecision.from_dict(data)
            assert restored.calibration_state is state
            assert restored.ood_state is state
            assert restored.regime_state is state

    def test_missing_evidence_cannot_have_zero_uncertainty(self):
        with pytest.raises(ValueError, match="calibration_ece must be > 0"):
            UnifiedRiskDecision(
                decision_id="d-bad",
                forecast_fingerprint="fp-bad",
                model_artifact_id="m-bad",
                requested_target_exposure=0.3,
                allowed_target_exposure=0.2,
                max_new_exposure=0.1,
                reduce_only=False,
                risk_level=RiskLevel.MEDIUM,
                reason_codes=(),
                calibration_state=EvidenceState.MISSING,
                calibration_artifact_id=None,
                calibration_ece=0.0,
                ood_state=EvidenceState.KNOWN,
                ood_score=0.1,
                regime_state=EvidenceState.KNOWN,
                regime_entropy=0.2,
                interval_width=0.05,
                created_at=datetime.now(UTC),
            )
        with pytest.raises(ValueError, match="ood_score must be > 0"):
            UnifiedRiskDecision(
                decision_id="d-bad",
                forecast_fingerprint="fp-bad",
                model_artifact_id="m-bad",
                requested_target_exposure=0.3,
                allowed_target_exposure=0.2,
                max_new_exposure=0.1,
                reduce_only=False,
                risk_level=RiskLevel.MEDIUM,
                reason_codes=(),
                calibration_state=EvidenceState.KNOWN,
                calibration_artifact_id="cal-1",
                calibration_ece=0.02,
                ood_state=EvidenceState.UNKNOWN,
                ood_score=0.0,
                regime_state=EvidenceState.KNOWN,
                regime_entropy=0.2,
                interval_width=0.05,
                created_at=datetime.now(UTC),
            )
        with pytest.raises(ValueError, match="regime_entropy must be > 0"):
            UnifiedRiskDecision(
                decision_id="d-bad",
                forecast_fingerprint="fp-bad",
                model_artifact_id="m-bad",
                requested_target_exposure=0.3,
                allowed_target_exposure=0.2,
                max_new_exposure=0.1,
                reduce_only=False,
                risk_level=RiskLevel.MEDIUM,
                reason_codes=(),
                calibration_state=EvidenceState.KNOWN,
                calibration_artifact_id="cal-1",
                calibration_ece=0.02,
                ood_state=EvidenceState.KNOWN,
                ood_score=0.1,
                regime_state=EvidenceState.STALE,
                regime_entropy=0.0,
                interval_width=0.05,
                created_at=datetime.now(UTC),
            )
