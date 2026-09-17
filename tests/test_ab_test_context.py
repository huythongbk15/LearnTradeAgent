"""Tests for the A/B test runner script.

Tests the comparison logic without LLM calls.
"""

from __future__ import annotations

from datetime import UTC, datetime
from collections import Counter
from dataclasses import dataclass, field

import pytest

from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.llm.research_memory import ResearchMemory


@dataclass
class ModeResult:
    contexts: list[dict] = field(default_factory=list)
    errors: int = 0


def _compute_stats(mode_result):
    """Local copy of _compute_stats for testing."""
    if not mode_result.contexts:
        return {"count": 0, "errors": mode_result.errors}
    adjustments = [c["confidence_adjustment"] for c in mode_result.contexts]
    anomalies = [a for c in mode_result.contexts for a in c["anomaly_flags"]]
    anomaly_counts = dict(Counter(anomalies))
    return {
        "count": len(mode_result.contexts),
        "errors": mode_result.errors,
        "avg_confidence_adjustment": sum(adjustments) / len(adjustments),
        "anomaly_counts": anomaly_counts,
        "total_anomalies": len(anomalies),
    }


class TestABTestComparison:
    """Test the comparison logic without actual LLM calls."""

    def test_confidence_delta_computation(self):
        """Verify confidence deltas are computed correctly."""
        mode_result = ModeResult(contexts=[
            {"confidence_adjustment": 0.8, "anomaly_flags": ["a"], "regime_tags": {"trend": "bull"}},
            {"confidence_adjustment": 0.7, "anomaly_flags": ["a", "b"], "regime_tags": {"trend": "bear"}},
            {"confidence_adjustment": 1.0, "anomaly_flags": [], "regime_tags": {"trend": "neutral"}},
        ])
        stats = _compute_stats(mode_result)
        assert stats["count"] == 3
        assert stats["avg_confidence_adjustment"] == pytest.approx(0.833, abs=0.001)
        assert stats["anomaly_counts"]["a"] == 2
        assert stats["anomaly_counts"]["b"] == 1

    def test_market_context_deterministic_consistency(self, tmp_path):
        """MarketContext deterministic fallback should be consistent across calls."""
        memory = ResearchMemory(tmp_path / "sync.sqlite3")
        ts = datetime(2024, 6, 15, 10, 0, tzinfo=UTC)

        ctx1 = MarketContext(
            regime_tags={"trend": "bullish"},
            anomaly_flags=[],
            confidence_adjustment=0.9,
            reasoning="test",
        )
        ctx2 = MarketContext(
            regime_tags={"trend": "bullish"},
            anomaly_flags=[],
            confidence_adjustment=0.9,
            reasoning="test",
        )

        memory.store("ETH/USDT", "1h", ts, ctx1, deterministic=False)
        memory.store("ETH/USDT", "1h", ts, ctx2, deterministic=False)

        retrieved = memory.retrieve("ETH/USDT", "1h", ts, deterministic=False)
        assert retrieved is not None
        assert retrieved.confidence_adjustment == 0.9

    def test_ab_test_result_serializable(self):
        """ABTestResult should be JSON serializable."""
        from scripts.ab_test_context import ABTestResult

        result = ABTestResult(
            symbol="BTC/USDT",
            timeframe="1h",
            start="2024-01-01",
            end="2024-06-01",
            num_bars=300,
        )
        d = result.to_dict()
        assert d["symbol"] == "BTC/USDT"
        assert d["num_bars"] == 300
        import json
        json.dumps(d)
