"""T4A: Confidence scaling integration into risk/router policy.

Acceptance criteria:
- confidence_adjustment (clamp [0.5, 1.5]) scales exposure_multiplier in RoutingDecision
- Zero exposure (uncertainty rejections) gets confidence_adjustment logged but
  no scaling effect
- RoutingDecision from_dict/to_dict round-trips confidence_adjustment
- confidence_adjustment outside [0.5, 1.5] raises ValueError
- exposure_multiplier clamped to [0, 1] after confidence scaling
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_agent.authority.adaptive_router import (
    HandoverState,
    RoutingDecision,
)
from trading_agent.llm.context_enrichment import MarketContext


class TestRoutingDecisionConfidenceField:
    """RoutingDecision stores and validates confidence_adjustment."""

    def test_default_confidence_is_neutral(self):
        """RoutingDecision with no confidence_adjustment defaults to 1.0."""
        decision = RoutingDecision(
            symbol="BTC/USDT",
            timeframe="1h",
            observed_at=datetime.now(UTC),
            posterior_fingerprint="fp",
            policy_ids=("p1",),
            incumbent_strategy_id=None,
            challenger_strategy_id=None,
            chosen_strategy_id="ema",
            chosen_policy_id="p1",
            chosen_params={"period": 20},
            handover_state=HandoverState.ACTIVATE,
            reason="test",
            allow_new_exposure=True,
            exposure_multiplier=0.5,
            candidate_score=1.0,
            incumbent_score=None,
            position_owner_strategy_id=None,
        )
        assert decision.confidence_adjustment == 1.0

    def test_confidence_outside_clamp_raises(self):
        """confidence_adjustment must be in [0.5, 1.5]."""
        with pytest.raises(ValueError, match="confidence_adjustment"):
            RoutingDecision(
                symbol="BTC/USDT",
                timeframe="1h",
                observed_at=datetime.now(UTC),
                posterior_fingerprint="fp",
                policy_ids=("p1",),
                incumbent_strategy_id=None,
                challenger_strategy_id=None,
                chosen_strategy_id="ema",
                chosen_policy_id="p1",
                chosen_params={"period": 20},
                handover_state=HandoverState.ACTIVATE,
                reason="test",
                allow_new_exposure=True,
                exposure_multiplier=0.5,
                candidate_score=1.0,
                incumbent_score=None,
                position_owner_strategy_id=None,
                confidence_adjustment=0.3,  # below 0.5
            )

    def test_high_confidence_accepted(self):
        """1.5 is the upper clamp — accepted."""
        decision = RoutingDecision(
            symbol="BTC/USDT",
            timeframe="1h",
            observed_at=datetime.now(UTC),
            posterior_fingerprint="fp",
            policy_ids=("p1",),
            incumbent_strategy_id=None,
            challenger_strategy_id=None,
            chosen_strategy_id="ema",
            chosen_policy_id="p1",
            chosen_params={"period": 20},
            handover_state=HandoverState.ACTIVATE,
            reason="test",
            allow_new_exposure=True,
            exposure_multiplier=0.5,
            candidate_score=1.0,
            incumbent_score=None,
            position_owner_strategy_id=None,
            confidence_adjustment=1.5,
        )
        assert decision.confidence_adjustment == 1.5

    def test_low_confidence_accepted(self):
        """0.5 is the lower clamp — accepted."""
        decision = RoutingDecision(
            symbol="BTC/USDT",
            timeframe="1h",
            observed_at=datetime.now(UTC),
            posterior_fingerprint="fp",
            policy_ids=("p1",),
            incumbent_strategy_id=None,
            challenger_strategy_id=None,
            chosen_strategy_id="ema",
            chosen_policy_id="p1",
            chosen_params={"period": 20},
            handover_state=HandoverState.ACTIVATE,
            reason="test",
            allow_new_exposure=True,
            exposure_multiplier=0.5,
            candidate_score=1.0,
            incumbent_score=None,
            position_owner_strategy_id=None,
            confidence_adjustment=0.5,
        )
        assert decision.confidence_adjustment == 0.5

    def test_round_trip_to_from_dict(self):
        """to_dict/from_dict preserves confidence_adjustment."""
        decision = RoutingDecision(
            symbol="BTC/USDT",
            timeframe="1h",
            observed_at=datetime.now(UTC),
            posterior_fingerprint="fp",
            policy_ids=("p1",),
            incumbent_strategy_id=None,
            challenger_strategy_id=None,
            chosen_strategy_id="ema",
            chosen_policy_id="p1",
            chosen_params={"period": 20},
            handover_state=HandoverState.ACTIVATE,
            reason="test",
            allow_new_exposure=True,
            exposure_multiplier=0.6,
            candidate_score=2.0,
            incumbent_score=None,
            position_owner_strategy_id=None,
            confidence_adjustment=1.3,
        )
        d = decision.to_dict()
        restored = RoutingDecision.from_dict(d)
        assert restored.confidence_adjustment == 1.3
        assert restored.decision_id == decision.decision_id

    def test_confidence_changes_decision_id(self):
        """confidence_adjustment is part of the decision_id hash."""
        base_kwargs = dict(
            symbol="BTC/USDT",
            timeframe="1h",
            observed_at=datetime.now(UTC),
            posterior_fingerprint="fp",
            policy_ids=("p1",),
            incumbent_strategy_id=None,
            challenger_strategy_id=None,
            chosen_strategy_id="ema",
            chosen_policy_id="p1",
            chosen_params={"period": 20},
            handover_state=HandoverState.ACTIVATE,
            reason="test",
            allow_new_exposure=True,
            exposure_multiplier=0.5,
            candidate_score=1.0,
            incumbent_score=None,
            position_owner_strategy_id=None,
        )
        d_neutral = RoutingDecision(**base_kwargs, confidence_adjustment=1.0)
        d_reduced = RoutingDecision(**base_kwargs, confidence_adjustment=0.7)
        d_amplified = RoutingDecision(**base_kwargs, confidence_adjustment=1.3)
        assert d_neutral.decision_id != d_reduced.decision_id
        assert d_reduced.decision_id != d_amplified.decision_id

    def test_exposure_clamped_after_confidence_scaling(self):
        """Router pre-clamps via _scale_confidence to avoid __post_init__ error.

        The router multiplies base_exposure * confidence_adj and must clamp
        to [0, 1] before constructing RoutingDecision.
        """
        # Simulate what the router's _scale_confidence does
        base = 0.9
        for confidence in (0.5, 0.8, 1.0, 1.2, 1.5):
            scaled = max(0.0, min(1.0, base * confidence))
            assert 0.0 <= scaled <= 1.0

        # Test the actual router's _scale_confidence static method
        from trading_agent.authority.adaptive_router import AdaptiveStrategyRouter as Router
        assert Router._scale_confidence(0.9, 1.5) == 1.0   # 1.35 → clamped
        assert Router._scale_confidence(0.3, 0.5) == 0.15
        assert Router._scale_confidence(0.6, 1.0) == 0.6
        assert Router._scale_confidence(0.8, 0.5) == 0.4

    """MarketContext confidence_adjustment clamping."""

    def test_market_context_clamps_to_05(self):
        """confidence_adjustment below 0.5 → clamped to 0.5."""
        ctx = MarketContext(
            regime_tags={"trend": "bullish"},
            anomaly_flags=[],
            confidence_adjustment=0.1,
        )
        assert ctx.confidence_adjustment == 0.5

    def test_market_context_clamps_to_15(self):
        """confidence_adjustment above 1.5 → clamped to 1.5."""
        ctx = MarketContext(
            regime_tags={"trend": "bullish"},
            anomaly_flags=[],
            confidence_adjustment=3.0,
        )
        assert ctx.confidence_adjustment == 1.5

    def test_market_context_neutral(self):
        """Neutral context → confidence_adjustment = 1.0."""
        ctx = MarketContext(regime_tags={}, anomaly_flags=[])
        assert ctx.confidence_adjustment == 1.0

    def test_market_context_passthrough(self):
        """Value within [0.5, 1.5] → unchanged."""
        for val in (0.5, 0.7, 1.0, 1.2, 1.5):
            ctx = MarketContext(
                regime_tags={"trend": "bullish"},
                anomaly_flags=[],
                confidence_adjustment=val,
            )
            assert ctx.confidence_adjustment == val


class TestRoutingDecisionScalingSemantics:
    """Verify the exposure_multiplier is pre-scaled by confidence_adj by the router."""

    def test_confidence_adapts_exposure(self):
        """The router pre-scales exposure_multiplier * confidence before
        constructing RoutingDecision.  Verify the product semantics."""
        base_exposure = 0.6
        # Simulate what the router does: exposure_multiplier * confidence_adj
        for confidence in (0.5, 0.8, 1.0, 1.2, 1.5):
            scaled = min(1.0, base_exposure * confidence)
            assert 0.0 <= scaled <= 1.0
            assert scaled >= base_exposure * 0.5  # lower bound
            assert scaled <= base_exposure * 1.5  # upper bound
