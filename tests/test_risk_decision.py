"""Tests for RiskDecision semantics."""

from __future__ import annotations

import pytest

from trading_agent.agents.risk_decision import RiskDecision, RiskLevel


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
