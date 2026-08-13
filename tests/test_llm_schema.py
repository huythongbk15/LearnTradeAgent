"""Tests for structured output enforcement (audit Phase 5: JSON schema)."""

from __future__ import annotations

import pytest

from trading_agent.agents.llm import (
    AGENT_SCHEMAS,
    validate_agent_output,
)


class TestValidateAgentOutput:
    def test_normalizes_valid_payload(self) -> None:
        out = validate_agent_output(
            {"signal": "buy", "confidence": "0.7", "reasoning": "ok"},
            "technical",
        )
        assert out["signal"] == "BUY"
        assert out["confidence"] == 0.7
        assert out["reasoning"] == "ok"

    def test_clamps_confidence(self) -> None:
        assert (
            validate_agent_output({"signal": "BUY", "confidence": 1.7}, "trader")[
                "confidence"
            ]
            == 1.0
        )
        assert (
            validate_agent_output({"signal": "BUY", "confidence": -0.3}, "trader")[
                "confidence"
            ]
            == 0.0
        )

    def test_missing_required_key_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            validate_agent_output({"signal": "BUY"}, "technical")

    def test_risk_requires_details(self) -> None:
        with pytest.raises(ValueError, match="details"):
            validate_agent_output({"signal": "HOLD", "confidence": 0.5}, "risk")

    def test_invalid_signal_raises(self) -> None:
        with pytest.raises(ValueError, match="invalid signal"):
            validate_agent_output({"signal": "MOON", "confidence": 0.9}, "trader")

    def test_non_numeric_confidence_raises(self) -> None:
        with pytest.raises(ValueError, match="confidence"):
            validate_agent_output({"signal": "BUY", "confidence": "high"}, "trader")

    def test_non_object_payload_raises(self) -> None:
        with pytest.raises(ValueError, match="non-object"):
            validate_agent_output([1, 2, 3], "trader")

    def test_unknown_schema_raises(self) -> None:
        with pytest.raises(ValueError, match="unknown agent schema"):
            validate_agent_output({"signal": "BUY", "confidence": 0.5}, "nope")

    def test_details_must_be_dict(self) -> None:
        out = validate_agent_output(
            {"signal": "HOLD", "confidence": 0.4, "details": "nope"},
            "risk",
        )
        assert out["details"] == {}

    def test_extra_keys_preserved(self) -> None:
        out = validate_agent_output(
            {
                "signal": "SELL",
                "confidence": 0.8,
                "details": {},
                "max_position_size_pct": 0.1,
            },
            "risk",
        )
        assert out["max_position_size_pct"] == 0.1


class TestSchemas:
    def test_all_core_agents_have_schemas(self) -> None:
        for role in ("technical", "sentiment", "risk", "trader"):
            assert role in AGENT_SCHEMAS
            assert "required" in AGENT_SCHEMAS[role]
            assert "signal" in AGENT_SCHEMAS[role]["required"]

    def test_schema_roundtrip(self) -> None:
        """Every valid minimal payload for every schema passes validation."""
        for name, schema in AGENT_SCHEMAS.items():
            payload = {
                key: ("BUY" if key == "signal" else 0.5) for key in schema["required"]
            }
            out = validate_agent_output(payload, name)
            assert out["signal"] == "BUY"
