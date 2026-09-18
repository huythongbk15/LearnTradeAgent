"""Tests for AgentMessage interop utilities (P1 protocol unification audit).

All agents — core and swarm alike — return AgentMessage.  The interop
functions are passthroughs that ensure field consistency (symbol/role
population).
"""

from __future__ import annotations

from trading_agent.agents.base import (
    AgentMessage,
    message_to_signal,
    signal_to_message,
)


def test_message_type_single():
    """AgentMessage is the single canonical message type."""
    msg = AgentMessage(
        role="technical_analyst", signal="BUY", confidence=0.8, reasoning="trend up",
    )
    assert isinstance(msg, AgentMessage)


def test_message_to_signal_passthrough():
    """message_to_signal is a passthrough that ensures symbol is populated."""
    msg = AgentMessage(
        role="technical_analyst",
        symbol="BTC/USDT",
        signal="BUY",
        confidence=0.8,
        reasoning="trend up",
        details={"rsi": 60, "role": "technical_analyst", "risk_level": "LOW"},
        max_position_size_pct=0.25,
        risk_level="LOW",
        warnings=["careful"],
    )
    sig = message_to_signal(msg, symbol="BTC/USDT")
    assert isinstance(sig, AgentMessage)
    assert sig.symbol == "BTC/USDT"
    assert sig.signal == "BUY"
    assert sig.confidence == 0.8
    assert sig.max_position_size_pct == 0.25
    assert sig.reasoning == "trend up"
    assert sig.details["rsi"] == 60
    assert sig.risk_level == "LOW"
    assert sig.warnings == ["careful"]


def test_signal_to_message_passthrough():
    """signal_to_message is a passthrough that ensures role is populated."""
    msg = AgentMessage(
        role="agent",
        symbol="ETH/USDT",
        signal="HOLD",
        confidence=0.55,
        reasoning="chờ breakout",
        details={"role": "sentiment"},
    )
    result = signal_to_message(msg, role="sentiment_analyst")
    assert result.role == "sentiment_analyst"
    assert result.signal == "HOLD"
    assert result.confidence == 0.55
    assert result.reasoning == "chờ breakout"
    assert result.symbol == "ETH/USDT"


def test_message_to_signal_ensures_symbol():
    """If the message lacks a symbol, message_to_signal sets it."""
    msg = AgentMessage(
        role="trader", signal="SELL", confidence=0.9, reasoning="", symbol="",
    )
    sig = message_to_signal(msg, symbol="BTC/USDT")
    assert sig.symbol == "BTC/USDT"


def test_default_message_fields_preserved():
    msg = AgentMessage(
        role="risk", signal="HOLD", confidence=0.5, reasoning="", symbol="BTC/USDT"
    )
    sig = message_to_signal(msg)
    assert sig.max_position_size_pct is None
    assert sig.warnings == []
