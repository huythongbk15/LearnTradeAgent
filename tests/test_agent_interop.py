"""Tests for AgentMessage <-> AgentSignal interop (audit Phase 5: unify
agent ecosystems)."""

from __future__ import annotations

from trading_agent.agents.base import (
    AgentMessage,
    AgentSignal,
    message_to_signal,
    signal_to_message,
)


def test_message_to_signal_roundtrip():
    msg = AgentMessage(
        role="technical_analyst",
        signal="BUY",
        confidence=0.8,
        reasoning="trend up",
        details={"rsi": 60},
        max_position_size_pct=0.25,
        risk_level="LOW",
        warnings=["careful"],
    )
    sig = message_to_signal(msg, symbol="BTC/USDT")
    assert isinstance(sig, AgentSignal)
    assert sig.symbol == "BTC/USDT"
    assert sig.action == "buy"
    assert sig.confidence == 0.8
    assert sig.size_pct == 0.25
    assert sig.reasoning == "trend up"
    assert sig.metadata["role"] == "technical_analyst"
    assert sig.metadata["risk_level"] == "LOW"
    assert sig.metadata["warnings"] == ["careful"]
    assert sig.metadata["details"] == {"rsi": 60}

    # Back to a message: signal, confidence, size survive.
    back = signal_to_message(sig, role="technical_analyst")
    assert back.signal == "BUY"
    assert back.confidence == 0.8
    assert back.max_position_size_pct == 0.25
    assert back.role == "technical_analyst"


def test_signal_to_message_roundtrip():
    sig = AgentSignal(
        signal_id="sig-1",
        symbol="ETH/USDT",
        action="hold",
        confidence=0.55,
        size_pct=0.0,
        reasoning="chờ breakout",
        metadata={"role": "sentiment"},
    )
    msg = signal_to_message(sig, role="sentiment_analyst")
    assert msg.role == "sentiment_analyst"
    assert msg.signal == "HOLD"
    assert msg.confidence == 0.55
    assert msg.reasoning == "chờ breakout"
    assert msg.role == "sentiment_analyst"

    # Full circle preserves the action.
    sig2 = message_to_signal(msg, symbol="ETH/USDT", signal_id="sig-2")
    assert sig2.action == "hold"
    assert sig2.signal_id == "sig-2"


def test_message_to_signal_generates_id_when_omitted():
    msg = AgentMessage(role="trader", signal="SELL", confidence=0.9, reasoning="")
    sig = message_to_signal(msg, symbol="BTC/USDT")
    assert sig.signal_id.startswith("msg-")
    assert len(sig.signal_id) == 12


def test_default_message_fields_survive():
    msg = AgentMessage(role="risk", signal="HOLD", confidence=0.5, reasoning="")
    sig = message_to_signal(msg, symbol="BTC/USDT")
    assert sig.size_pct == 0.0
    assert sig.metadata["warnings"] == []
