"""Tests for unified order permission gate."""

from __future__ import annotations

from datetime import UTC, datetime

from trading_agent.execution.canonical import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionHealth,
    ExposureEffect,
    TrustedPrice,
)
from trading_agent.execution.permission import (
    OrderPermission,
    PermissionContext,
    PermissionReason,
    evaluate_order_permission,
)


def _fresh_price(age_seconds: float = 0.0) -> TrustedPrice:
    now = datetime.now(UTC)
    return TrustedPrice(
        price=100.0,
        exchange_timestamp=now,
        received_at=now,
        sequence_id=1,
    )


def _stale_price() -> TrustedPrice:
    now = datetime.now(UTC)
    return TrustedPrice(
        price=100.0,
        exchange_timestamp=now,
        received_at=datetime(2020, 1, 1, tzinfo=UTC),
        sequence_id=1,
    )


def _sample_risk_decision(
    *,
    risk_level: RiskLevel = RiskLevel.LOW,
    allowed_target_exposure: float = 0.25,
    max_new_exposure: float = 0.25,
    reduce_only: bool = False,
    calibration_state: EvidenceState = EvidenceState.KNOWN,
    ood_state: EvidenceState = EvidenceState.KNOWN,
    regime_state: EvidenceState = EvidenceState.KNOWN,
) -> UnifiedRiskDecision:
    return UnifiedRiskDecision(
        decision_id="test-decision",
        forecast_fingerprint="test-fp",
        model_artifact_id="test-model",
        requested_target_exposure=0.5,
        allowed_target_exposure=allowed_target_exposure,
        max_new_exposure=max_new_exposure,
        reduce_only=reduce_only,
        risk_level=risk_level,
        reason_codes=("APPROVED",),
        calibration_state=calibration_state,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=ood_state,
        ood_score=0.1,
        regime_state=regime_state,
        regime_entropy=0.2,
        interval_width=0.05,
        created_at=datetime.now(UTC),
    )


class TestOrderPermission:
    def test_normal_buy_allowed(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.INCREASE,
                risk_decision=_sample_risk_decision(
                    risk_level=RiskLevel.LOW,
                    allowed_target_exposure=0.25,
                    max_new_exposure=0.25,
                ),
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.ALLOW

    def test_high_risk_blocks_new_buy(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.INCREASE,
                risk_decision=_sample_risk_decision(
                    risk_level=RiskLevel.HIGH,
                    max_new_exposure=0.0,
                    reduce_only=True,
                ),
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.HIGH_RISK_NEW_EXPOSURE

    def test_manual_blocked_blocks_new_exposure(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.MANUAL_BLOCKED,
                exposure_effect=ExposureEffect.INCREASE,
                manual_blocked=True,
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.MANUAL_BLOCKED

    def test_protection_gap_blocks_new_exposure(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.PROTECTION_GAP,
                exposure_effect=ExposureEffect.INCREASE,
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.PROTECTION_GAP

    def test_stale_price_blocks_order(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.INCREASE,
                trusted_price=_stale_price(),
                max_price_age_seconds=60.0,
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.STALE_MARKET_DATA

    def test_reconciliation_unresolved_blocks_new_exposure(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.RECONCILING,
                exposure_effect=ExposureEffect.INCREASE,
                reconciliation_state="started",
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.RECONCILIATION_UNRESOLVED

    def test_unknown_broker_state_blocks_order(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.INCREASE,
                broker_state="UNKNOWN",
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.UNKNOWN_BROKER_STATE

    def test_sell_above_inventory_blocked(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.REDUCE,
                trusted_price=_fresh_price(),
                order_side="sell",
                order_size=10.0,
                free_inventory=5.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.INSUFFICIENT_INVENTORY

    def test_reduce_only_allowed_when_risk_high(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.REDUCE,
                risk_decision=_sample_risk_decision(
                    risk_level=RiskLevel.HIGH,
                    max_new_exposure=0.0,
                    reduce_only=True,
                ),
                trusted_price=_fresh_price(),
                order_side="sell",
                order_size=1.0,
                free_inventory=10.0,
            )
        )
        assert result.permission == OrderPermission.REDUCE_ONLY
        assert result.reason == PermissionReason.REDUCE_ONLY

    def test_unknown_calibration_evidence_blocks_buy(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.INCREASE,
                risk_decision=_sample_risk_decision(
                    risk_level=RiskLevel.LOW,
                    allowed_target_exposure=0.25,
                    max_new_exposure=0.25,
                    calibration_state=EvidenceState.UNKNOWN,
                ),
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.MISSING_CALIBRATION_EVIDENCE

    def test_missing_ood_evidence_blocks_buy(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.INCREASE,
                risk_decision=_sample_risk_decision(
                    risk_level=RiskLevel.LOW,
                    allowed_target_exposure=0.25,
                    max_new_exposure=0.25,
                    ood_state=EvidenceState.MISSING,
                ),
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.MISSING_OOD_EVIDENCE

    def test_stale_regime_evidence_blocks_buy(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.INCREASE,
                risk_decision=_sample_risk_decision(
                    risk_level=RiskLevel.LOW,
                    allowed_target_exposure=0.25,
                    max_new_exposure=0.25,
                    regime_state=EvidenceState.STALE,
                ),
                trusted_price=_fresh_price(),
                order_side="buy",
                order_size=1.0,
                free_inventory=0.0,
            )
        )
        assert result.permission == OrderPermission.BLOCK
        assert result.reason == PermissionReason.MISSING_REGIME_EVIDENCE

    def test_unknown_evidence_allows_safe_reduce(self):
        result = evaluate_order_permission(
            PermissionContext(
                execution_health=ExecutionHealth.NORMAL,
                exposure_effect=ExposureEffect.REDUCE,
                risk_decision=_sample_risk_decision(
                    risk_level=RiskLevel.LOW,
                    allowed_target_exposure=0.25,
                    max_new_exposure=0.25,
                    calibration_state=EvidenceState.UNKNOWN,
                    ood_state=EvidenceState.UNKNOWN,
                    regime_state=EvidenceState.UNKNOWN,
                ),
                trusted_price=_fresh_price(),
                order_side="sell",
                order_size=1.0,
                free_inventory=10.0,
            )
        )
        assert result.permission == OrderPermission.REDUCE_ONLY
        assert result.reason == PermissionReason.REDUCE_ONLY
