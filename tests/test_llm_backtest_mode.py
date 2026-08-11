"""Tests for deterministic LLM backtest mode (audit Phase 3: enable LLM in
backtest with deterministic mode — seed=0, temp=0, fixed provider, no cache)."""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from trading_agent.agents import llm


@dataclass
class FakeLLMResponse:
    content: str


@pytest.fixture(autouse=True)
def _clean_mode():
    llm.disable_backtest_mode()
    yield
    llm.disable_backtest_mode()


def test_enable_sets_deterministic_config(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_chat(messages, **kwargs):
        calls.append(kwargs)
        return FakeLLMResponse(content="{}")

    monkeypatch.setattr(llm, "chat", fake_chat)
    llm.enable_backtest_mode(
        provider="opencode",
        model="deepseek-v4-flash-free",
        temperature=0.0,
        max_tokens=500,
        seed=0,
        use_cache=False,
    )
    assert llm.is_backtest_mode()
    cfg = llm._BACKTEST_CONFIG
    assert cfg["provider"] == "opencode"
    assert cfg["model"] == "deepseek-v4-flash-free"
    assert cfg["temperature"] == 0.0
    assert cfg["seed"] == 0
    assert cfg["use_cache"] is False

    llm.backtest_chat([{"role": "user", "content": "hi"}])
    assert calls, "backtest_chat must call chat in mode"
    sent = calls[-1]
    assert sent["provider"] == "opencode"
    assert sent["model"] == "deepseek-v4-flash-free"
    assert sent["temperature"] == 0.0
    assert sent["use_cache"] is False


def test_disabled_mode_forwards_normally(monkeypatch) -> None:
    calls: list[dict] = []

    def fake_chat(messages, **kwargs):
        calls.append(kwargs)
        return FakeLLMResponse(content="{}")

    monkeypatch.setattr(llm, "chat", fake_chat)
    llm.backtest_chat([{"role": "user", "content": "hi"}], temperature=0.9)
    assert calls
    # No forced provider/model — caller defaults apply.
    assert "provider" not in calls[-1]


def test_disable_resets_mode() -> None:
    llm.enable_backtest_mode()
    assert llm.is_backtest_mode()
    llm.disable_backtest_mode()
    assert not llm.is_backtest_mode()


def test_backtest_ask_agent_parses_json(monkeypatch) -> None:
    payload = {"signal": "BUY", "confidence": 0.6}
    monkeypatch.setattr(
        llm,
        "chat",
        lambda messages, **kwargs: FakeLLMResponse(
            content=f"```json\n{json.dumps(payload)}\n```"
        ),
    )
    llm.enable_backtest_mode()
    result = llm.backtest_ask_agent("sys", "user")
    assert result["signal"] == "BUY"
    assert result["confidence"] == 0.6


def test_backtest_ask_agent_fallback_on_bad_json(monkeypatch) -> None:
    monkeypatch.setattr(
        llm,
        "chat",
        lambda messages, **kwargs: FakeLLMResponse(content="not json at all"),
    )
    llm.enable_backtest_mode()
    result = llm.backtest_ask_agent("sys", "user")
    # Fallback returns a well-formed decision dict.
    assert isinstance(result, dict)
    assert "signal" in result or "action" in result


def test_backtest_ask_agent_fallback_on_llm_error(monkeypatch) -> None:
    def boom(messages, **kwargs):
        raise llm.LLMError("provider down")

    monkeypatch.setattr(llm, "chat", boom)
    llm.enable_backtest_mode()
    result = llm.backtest_ask_agent("sys", "user")
    assert isinstance(result, dict)
