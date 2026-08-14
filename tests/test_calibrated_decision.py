"""Tests for CalibratedDecision layer (Wave E)."""

from __future__ import annotations

import pytest

from trading_agent.research.uncertainty import (
    Action,
    CalibratedDecision,
    DecisionPolicy,
    ThresholdDecisionPolicy,
    UncertaintySignal,
    UncertaintyState,
    isotonic_calibration,
    temperature_scale,
    uncertainty_signal_to_decision,
)


class TestAction:
    def test_action_enum_values(self):
        assert Action.INCREASE.value == "increase"
        assert Action.HOLD.value == "hold"
        assert Action.REDUCE.value == "reduce"
        assert Action.ABSTAIN.value == "abstain"

    def test_risk_level_ordering(self):
        assert Action.INCREASE.risk_level == 3
        assert Action.HOLD.risk_level == 2
        assert Action.REDUCE.risk_level == 1
        assert Action.ABSTAIN.risk_level == 0


class TestCalibratedDecision:
    def test_valid_probabilities(self):
        decision = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.5,
                Action.HOLD: 0.3,
                Action.REDUCE: 0.1,
                Action.ABSTAIN: 0.1,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        assert decision.most_likely_action == Action.INCREASE
        assert decision.can_increase_exposure

    def test_invalid_probabilities_sum_raises(self):
        with pytest.raises(ValueError, match="sum to 1.0"):
            CalibratedDecision(
                action_probabilities={
                    Action.INCREASE: 0.5,
                    Action.HOLD: 0.3,
                    Action.REDUCE: 0.1,
                    Action.ABSTAIN: 0.05,  # sum = 0.95
                },
                expected_return=0.5,
                prediction_interval_lower=-0.2,
                prediction_interval_upper=1.2,
                calibration_score=0.9,
                ood_score=0.1,
            )

    def test_missing_action_raises(self):
        # Missing action causes sum < 1.0, caught by sum validation first
        with pytest.raises(ValueError, match="sum to 1.0"):
            CalibratedDecision(
                action_probabilities={
                    Action.INCREASE: 0.5,
                    Action.HOLD: 0.3,
                    Action.REDUCE: 0.1,
                    # ABSTAIN missing
                },
                expected_return=0.5,
                prediction_interval_lower=-0.2,
                prediction_interval_upper=1.2,
                calibration_score=0.9,
                ood_score=0.1,
            )

    def test_uncertainty_state_derivation(self):
        # HIGH uncertainty
        high = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.1,
                Action.HOLD: 0.2,
                Action.REDUCE: 0.3,
                Action.ABSTAIN: 0.4,
            },
            expected_return=0.5,
            prediction_interval_lower=-2.0,
            prediction_interval_upper=3.0,
            calibration_score=0.4,
            ood_score=0.8,
        )
        assert high.uncertainty_state == UncertaintyState.HIGH
        assert not high.can_increase_exposure

        # LOW uncertainty
        low = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.5,
                Action.HOLD: 0.3,
                Action.REDUCE: 0.1,
                Action.ABSTAIN: 0.1,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.95,
            ood_score=0.05,
        )
        assert low.uncertainty_state == UncertaintyState.LOW
        assert low.can_increase_exposure

    def test_serialization(self):
        decision = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.5,
                Action.HOLD: 0.3,
                Action.REDUCE: 0.1,
                Action.ABSTAIN: 0.1,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
            horizon="4h",
            temperature=1.2,
        )
        d = decision.to_dict()
        assert d["action_probabilities"]["increase"] == 0.5
        assert d["horizon"] == "4h"
        assert d["temperature"] == 1.2
        assert d["uncertainty_state"] == "low"


class TestDecisionPolicy:
    def test_aggressive_allows_increase_at_lower_threshold(self):
        policy = DecisionPolicy("aggressive")
        decision = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.4,
                Action.HOLD: 0.3,
                Action.REDUCE: 0.15,
                Action.ABSTAIN: 0.15,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        allowed = policy.allowed_actions(decision)
        assert Action.INCREASE in allowed

    def test_moderate_requires_higher_increase_prob(self):
        policy = DecisionPolicy("moderate")
        decision = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.4,
                Action.HOLD: 0.3,
                Action.REDUCE: 0.15,
                Action.ABSTAIN: 0.15,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        allowed = policy.allowed_actions(decision)
        assert Action.INCREASE not in allowed  # 0.4 < 0.50

        decision2 = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.55,
                Action.HOLD: 0.25,
                Action.REDUCE: 0.1,
                Action.ABSTAIN: 0.1,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        allowed = policy.allowed_actions(decision2)
        assert Action.INCREASE in allowed

    def test_conservative_strict_thresholds(self):
        policy = DecisionPolicy("conservative")
        decision = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.6,
                Action.HOLD: 0.2,
                Action.REDUCE: 0.1,
                Action.ABSTAIN: 0.1,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        allowed = policy.allowed_actions(decision)
        assert Action.INCREASE not in allowed  # 0.6 < 0.65

        decision2 = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.7,
                Action.HOLD: 0.15,
                Action.REDUCE: 0.05,
                Action.ABSTAIN: 0.1,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        allowed = policy.allowed_actions(decision2)
        assert Action.INCREASE in allowed

    def test_abstain_always_allowed(self):
        for appetite in ("aggressive", "moderate", "conservative"):
            policy = DecisionPolicy(appetite)
            decision = CalibratedDecision(
                action_probabilities={
                    Action.INCREASE: 0.05,
                    Action.HOLD: 0.1,
                    Action.REDUCE: 0.15,
                    Action.ABSTAIN: 0.7,
                },
                expected_return=0.5,
                prediction_interval_lower=-0.2,
                prediction_interval_upper=1.2,
                calibration_score=0.9,
                ood_score=0.1,
            )
            allowed = policy.allowed_actions(decision)
            assert Action.ABSTAIN in allowed

    def test_reduce_allowed_on_high_uncertainty(self):
        policy = DecisionPolicy("moderate")
        decision = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.1,
                Action.HOLD: 0.2,
                Action.REDUCE: 0.3,
                Action.ABSTAIN: 0.4,
            },
            expected_return=0.5,
            prediction_interval_lower=-2.0,
            prediction_interval_upper=3.0,
            calibration_score=0.4,
            ood_score=0.8,  # HIGH uncertainty
        )
        allowed = policy.allowed_actions(decision)
        assert Action.REDUCE in allowed
        assert Action.INCREASE not in allowed

    def test_recommended_action_picks_highest_allowed(self):
        policy = DecisionPolicy("moderate")
        decision = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.55,
                Action.HOLD: 0.25,
                Action.REDUCE: 0.1,
                Action.ABSTAIN: 0.1,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        rec = policy.recommended_action(decision)
        assert rec == Action.INCREASE

        decision2 = CalibratedDecision(
            action_probabilities={
                Action.INCREASE: 0.3,
                Action.HOLD: 0.5,
                Action.REDUCE: 0.1,
                Action.ABSTAIN: 0.1,
            },
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        rec = policy.recommended_action(decision2)
        assert rec == Action.HOLD  # INCREASE not allowed, HOLD highest


class TestTemperatureScale:
    def test_temperature_softens_distribution(self):
        logits = [2.0, 1.0, 0.0, -1.0]
        # Higher temperature = softer (more uniform)
        p1 = temperature_scale(logits, 0.5)
        p2 = temperature_scale(logits, 2.0)
        # max prob should be lower with higher temperature
        assert max(p2) < max(p1)

    def test_temperature_1_is_softmax(self):
        import math

        logits = [2.0, 1.0, 0.0, -1.0]
        p = temperature_scale(logits, 1.0)
        expected = [math.exp(x) for x in logits]
        expected = [e / sum(expected) for e in expected]
        for a, b in zip(p, expected):
            assert a == pytest.approx(b, rel=1e-6)

    def test_invalid_temperature_raises(self):
        with pytest.raises(ValueError):
            temperature_scale([1.0, 2.0], 0.0)
        with pytest.raises(ValueError):
            temperature_scale([1.0, 2.0], -1.0)


class TestIsotonicCalibration:
    def test_isotonic_monotonic(self):
        predictions = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
        outcomes = [0.0, 0.0, 0.1, 0.3, 0.5, 0.6, 0.7, 0.9, 1.0]
        calibrated, _ = isotonic_calibration(predictions, outcomes)
        # Isotonic should be non-decreasing
        for i in range(len(calibrated) - 1):
            assert calibrated[i] <= calibrated[i + 1] + 1e-6

    def test_isotonic_fallback_no_sklearn(self):
        # Test that it doesn't crash if sklearn not available
        predictions = [0.1, 0.2, 0.3]
        outcomes = [0.0, 0.5, 1.0]
        calibrated, boundaries = isotonic_calibration(predictions, outcomes)
        # Should return something (fallback is identity)
        assert len(calibrated) == 3


class TestUncertaintySignalToDecision:
    def test_positive_expected_return_maps_to_increase(self):
        signal = UncertaintySignal(
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        decision = uncertainty_signal_to_decision(signal)
        assert (
            decision.action_probabilities[Action.INCREASE]
            > decision.action_probabilities[Action.REDUCE]
        )
        assert decision.uncertainty_state == UncertaintyState.LOW

    def test_negative_expected_return_maps_to_reduce(self):
        signal = UncertaintySignal(
            expected_return=-0.5,
            prediction_interval_lower=-1.2,
            prediction_interval_upper=0.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        decision = uncertainty_signal_to_decision(signal)
        assert (
            decision.action_probabilities[Action.REDUCE]
            > decision.action_probabilities[Action.INCREASE]
        )

    def test_low_calibration_shifts_to_abstain(self):
        signal = UncertaintySignal(
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.4,  # Low calibration
            ood_score=0.5,  # High OOD
        )
        decision = uncertainty_signal_to_decision(signal)
        # Should shift probability to ABSTAIN/HOLD
        assert decision.action_probabilities[Action.ABSTAIN] > 0.1
        assert decision.action_probabilities[Action.INCREASE] < 0.6
        assert decision.uncertainty_state != UncertaintyState.LOW

    def test_temperature_applied(self):
        signal = UncertaintySignal(
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        decision1 = uncertainty_signal_to_decision(signal, temperature=0.5)
        decision2 = uncertainty_signal_to_decision(signal, temperature=2.0)
        # Higher temperature should make distribution more uniform
        assert max(decision2.action_probabilities.values()) < max(
            decision1.action_probabilities.values()
        )


class TestThresholdDecisionPolicy:
    def test_adapter_wraps_signal(self):
        policy = ThresholdDecisionPolicy("moderate")
        signal = UncertaintySignal(
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        allowed = policy.allowed_actions(signal)
        assert Action.ABSTAIN in allowed
        assert Action.HOLD in allowed

    def test_can_increase_delegates(self):
        policy = ThresholdDecisionPolicy("moderate")
        signal = UncertaintySignal(
            expected_return=0.5,
            prediction_interval_lower=-0.2,
            prediction_interval_upper=1.2,
            calibration_score=0.9,
            ood_score=0.1,
        )
        assert policy.can_increase(signal) == (
            Action.INCREASE in policy.allowed_actions(signal)
        )

    def test_invalid_appetite_raises(self):
        with pytest.raises(ValueError):
            ThresholdDecisionPolicy("invalid")
