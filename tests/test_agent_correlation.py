"""Tests for AgentCorrelationTracker — correlation-aware ensemble weighting
(audit Phase 3: rolling signal correlation -> diversification discount)."""

from __future__ import annotations

import numpy as np

from trading_agent.agents.base import AgentMessage
from trading_agent.agents.orchestrator import AgentCorrelationTracker


def _msg(role: str, signal: str, confidence: float) -> AgentMessage:
    return AgentMessage(
        role=role,
        signal=signal,
        confidence=confidence,
        reasoning=f"{role} {signal}",
    )


def _update_many(tracker: AgentCorrelationTracker, n: int) -> None:
    """Feed n identical triplets: tech BUY 1.0, senti BUY 1.0, risk SELL 1.0."""
    for _ in range(n):
        tracker.update(
            [
                _msg("technical_analyst", "BUY", 1.0),
                _msg("sentiment_analyst", "BUY", 1.0),
                _msg("risk_manager", "SELL", 1.0),
            ]
        )


def test_window_caps_history() -> None:
    tracker = AgentCorrelationTracker(window=10)
    _update_many(tracker, 25)
    assert len(tracker.signal_history["technical_analyst"]) == 10
    assert len(tracker.signal_history["risk_manager"]) == 10


def test_insufficient_data_no_matrix() -> None:
    tracker = AgentCorrelationTracker(window=50)
    _update_many(tracker, 5)
    assert tracker.get_correlation_matrix() is None
    assert tracker.get_diversification_discount() == 1.0
    per = tracker.get_per_agent_correlation()
    assert per["technical_analyst"] == 0


def test_correlation_matrix_shape() -> None:
    tracker = AgentCorrelationTracker(window=50)
    _update_many(tracker, 20)
    corr = tracker.get_correlation_matrix()
    assert corr is not None
    assert corr.shape == (3, 3)
    # tech & sentiment both BUY -> high positive corr; risk SELL -> negative.
    assert corr[0, 1] > 0.9
    assert corr[0, 2] < -0.9


def test_high_correlation_deep_discount() -> None:
    tracker = AgentCorrelationTracker(window=50)
    # All three agents always agree: fully correlated signals.
    for _ in range(30):
        tracker.update(
            [
                _msg("technical_analyst", "BUY", 1.0),
                _msg("sentiment_analyst", "BUY", 1.0),
                _msg("risk_manager", "BUY", 1.0),
            ]
        )
    discount = tracker.get_diversification_discount()
    assert discount <= 0.6  # near the 0.5 floor


def test_low_correlation_keeps_weight() -> None:
    tracker = AgentCorrelationTracker(window=50)
    # Alternate signals to keep pairwise correlation near zero.
    rng = np.random.default_rng(0)
    for _ in range(30):
        tech = "BUY" if rng.random() < 0.5 else "SELL"
        sent = "BUY" if rng.random() < 0.5 else "SELL"
        risk = "BUY" if rng.random() < 0.5 else "SELL"
        tracker.update(
            [
                _msg("technical_analyst", tech, 1.0),
                _msg("sentiment_analyst", sent, 1.0),
                _msg("risk_manager", risk, 1.0),
            ]
        )
    discount = tracker.get_diversification_discount()
    assert discount > 0.9


def test_discount_bounds() -> None:
    tracker = AgentCorrelationTracker(window=50)
    for _ in range(30):
        tracker.update(
            [
                _msg("technical_analyst", "BUY", 1.0),
                _msg("sentiment_analyst", "BUY", 1.0),
                _msg("risk_manager", "BUY", 1.0),
            ]
        )
    assert 0.5 <= tracker.get_diversification_discount() <= 1.0


def test_unknown_roles_ignored() -> None:
    tracker = AgentCorrelationTracker(window=50)
    tracker.update([_msg("trader", "BUY", 0.9)])
    assert tracker.signal_history["technical_analyst"] == []
