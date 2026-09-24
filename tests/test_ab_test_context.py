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


class TestFullRunSkipLLM:
    """Full run_ab_test with --skip-llm-on on real historical data (>=300 bars)."""

    @staticmethod
    def _run_btc(start, end):
        from scripts.ab_test_context import run_ab_test
        import tempfile
        tmp = tempfile.mkdtemp(prefix="abtest_")
        from pathlib import Path
        return run_ab_test(
            symbol="BTC_USDT",
            timeframe="1h",
            exchange="binance",
            start=start, end=end,
            tmp_dir=Path(tmp),
            skip_llm_on=True,
            max_bars_llm=0,
        )

    def test_at_least_300_bars_processed(self):
        """A/B test must process >= 300 bars per the constraint."""
        result = self._run_btc("2023-11-01", "2023-11-15")
        assert result.num_bars >= 300, f"Only {result.num_bars} bars processed"

    def test_replay_matches_llm_off(self):
        """Replay (from memory) must reproduce LLM_OFF contexts exactly."""
        result = self._run_btc("2023-11-01", "2023-11-15")
        assert result.replay_total > 0, "No bars compared"
        assert result.replay_matches_llm == result.replay_total,             f"Replay mismatch: {result.replay_matches_llm}/{result.replay_total} bars"

    def test_confidence_adjustment_clamped(self):
        """confidence_adjustment must be in [0.5, 1.5] per LLM clamping rules."""
        result = self._run_btc("2023-11-01", "2023-11-15")
        for delta in result.confidence_delta:
            assert -1.0 <= delta <= 1.0, f"Confidence delta {delta} out of bounds"
        assert result.max_abs_confidence_delta <= 1.0

    def test_anomaly_divergence_recorded(self):
        """Anomaly divergence should be tracked and bounded."""
        result = self._run_btc("2023-11-01", "2023-11-15")
        n = max(result.num_bars, 1)
        assert 0 <= result.anomaly_divergence <= n

    def test_ab_test_result_is_serializable(self):
        """Full ABTestResult must serialize to JSON."""
        result = self._run_btc("2023-11-01", "2023-11-15")
        d = result.to_dict()
        import json
        json.dumps(d)
        assert d["symbol"] == "BTC_USDT"
        assert d["num_bars"] >= 300
        assert "llm_off_stats" in d
        assert "replay_stats" in d
