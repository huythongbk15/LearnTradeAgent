"""Tests for Research Memory + LLM Decision Trace.

Tests:
1. ResearchMemory store/retrieve roundtrip
2. ResearchMemory retrieve_range for A/B comparison
3. ResearchMemory compare_runs (deterministic vs LLM)
4. LLMDecisionTrace immutability + fingerprint
5. DecisionTraceBuffer drain
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from trading_agent.llm.context_enrichment import MarketContext
from trading_agent.llm.decision_trace import (
    DecisionTraceBuffer,
    LLMDecisionTrace,
)
from trading_agent.llm.research_memory import ResearchMemory


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    return tmp_path / "research.sqlite3"


@pytest.fixture
def memory(tmp_db: Path):
    return ResearchMemory(tmp_db)


@pytest.fixture
def sample_context():
    return MarketContext(
        regime_tags={"trend": "bullish", "volatility": "low"},
        anomaly_flags=["rsi_divergence"],
        cross_asset_signals={},
        confidence_adjustment=0.85,
        reasoning="Test context",
        details={"source": "llm"},
    )


# ── ResearchMemory ────────────────────────────────────────────────────────

def test_store_retrieve_roundtrip(memory: ResearchMemory, sample_context: MarketContext):
    ts = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    memory.store("BTC/USDT", "1h", ts, sample_context)

    result = memory.retrieve("BTC/USDT", "1h", ts)
    assert result is not None
    assert result.regime_tags["trend"] == "bullish"
    assert "rsi_divergence" in result.anomaly_flags
    assert result.confidence_adjustment == 0.85


def test_store_retrieve_missing(memory: ResearchMemory):
    ts = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    result = memory.retrieve("BTC/USDT", "1h", ts)
    assert result is None


def test_retrieve_range(memory: ResearchMemory, sample_context: MarketContext):
    start = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
    mid = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    end = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

    # Store two LLM contexts
    memory.store("BTC/USDT", "1h", mid, sample_context, deterministic=False)
    # Store one deterministic fallback
    det_ctx = MarketContext(reasoning="deterministic fallback")
    memory.store("BTC/USDT", "1h", start, det_ctx, deterministic=True)

    llm_results = memory.retrieve_range("BTC/USDT", "1h", start, end, deterministic=False)
    det_results = memory.retrieve_range("BTC/USDT", "1h", start, end, deterministic=True)

    assert len(llm_results) == 1
    assert len(det_results) == 1
    assert llm_results[0][1].confidence_adjustment == 0.85
    assert det_results[0][1].reasoning == "deterministic fallback"


def test_store_multiple_bars(memory: ResearchMemory):
    base = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
    for i in range(5):
        ctx = MarketContext(
            regime_tags={"trend": f"bar_{i}"},
            confidence_adjustment=1.0,
        )
        memory.store("BTC/USDT", "1h", base + timedelta(hours=i), ctx)

    results = memory.retrieve_range(
        "BTC/USDT", "1h",
        base, base + timedelta(hours=10),
        deterministic=False,
    )
    assert len(results) == 5
    assert results[0][1].regime_tags["trend"] == "bar_0"
    assert results[4][1].regime_tags["trend"] == "bar_4"


def test_deterministic_flag_isolation(memory: ResearchMemory, sample_context):
    """LLM and deterministic contexts with same timestamp must not collide."""
    ts = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)

    memory.store("BTC/USDT", "1h", ts, sample_context, deterministic=False)
    det_ctx = MarketContext(reasoning="fallback")
    memory.store("BTC/USDT", "1h", ts, det_ctx, deterministic=True)

    llm = memory.retrieve("BTC/USDT", "1h", ts, deterministic=False)
    det = memory.retrieve("BTC/USDT", "1h", ts, deterministic=True)

    assert llm.confidence_adjustment == 0.85
    assert det.confidence_adjustment == 1.0


def test_count(memory: ResearchMemory, sample_context: MarketContext):
    ts = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    memory.store("BTC/USDT", "1h", ts, sample_context, deterministic=False)
    memory.store("BTC/USDT", "1h", ts, sample_context, deterministic=True)

    assert memory.count() == 2
    assert memory.count(symbol="BTC/USDT") == 2
    assert memory.count(deterministic=False) == 1
    assert memory.count(deterministic=True) == 1
    assert memory.count(symbol="ETH/USDT") == 0


def test_compare_runs(memory: ResearchMemory, sample_context: MarketContext):
    start = datetime(2024, 1, 15, 10, 0, tzinfo=UTC)
    end = datetime(2024, 1, 15, 11, 0, tzinfo=UTC)

    # 2 LLM contexts
    memory.store("BTC/USDT", "1h", start, sample_context, deterministic=False)
    memory.store("BTC/USDT", "1h", start + timedelta(minutes=30), sample_context, deterministic=False)
    # 1 deterministic
    det_ctx = MarketContext(reasoning="fallback")
    memory.store("BTC/USDT", "1h", start + timedelta(minutes=15), det_ctx, deterministic=True)

    report = memory.compare_runs("BTC/USDT", "1h", start, end)
    assert report["llm_context_bars"] == 2
    assert report["deterministic_bars"] == 1
    assert report["anomaly_coverage"]["rsi_divergence"] == 2


# ── LLMDecisionTrace ─────────────────────────────────────────────────────

def test_trace_immutability_and_fingerprint():
    ts = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    trace = LLMDecisionTrace(
        symbol="BTC/USDT",
        timeframe="1h",
        bar_timestamp=ts,
        market_context={"regime_tags": {"trend": "bullish"}, "confidence_adjustment": 0.8},
        context_fingerprint="abc123",
        deterministic_forecast={"direction": 1, "score": 0.75},
        exposure_applied=0.25,
        confidence_before=0.6,
        confidence_after=0.48,  # 0.6 * 0.8
        context_ignored=False,
        reason="LLM confirmed bullish regime",
    )

    # Frozen dataclass
    import dataclasses
    assert dataclasses.is_dataclass(trace)
    assert not hasattr(trace, "__setattr__") or trace.__dataclass_params__.frozen

    # Trace ID is non-empty and deterministic
    assert len(trace.trace_id) == 24
    assert all(c in "0123456789abcdef" for c in trace.trace_id)

    # Re-creating with same inputs gives same fingerprint
    trace2 = LLMDecisionTrace(
        symbol="BTC/USDT",
        timeframe="1h",
        bar_timestamp=ts,
        market_context={"regime_tags": {"trend": "bullish"}, "confidence_adjustment": 0.8},
        context_fingerprint="abc123",
        deterministic_forecast={"direction": 1, "score": 0.75},
        exposure_applied=0.25,
        confidence_before=0.6,
        confidence_after=0.48,
        context_ignored=False,
        reason="LLM confirmed bullish regime",
    )
    assert trace.trace_id == trace2.trace_id

    # Different input → different fingerprint
    trace3 = LLMDecisionTrace(
        symbol="BTC/USDT",
        timeframe="1h",
        bar_timestamp=ts,
        market_context={"regime_tags": {"trend": "bearish"}, "confidence_adjustment": 0.8},
        context_fingerprint="abc124",
        deterministic_forecast={"direction": 1, "score": 0.75},
        exposure_applied=0.25,
        confidence_before=0.6,
        confidence_after=0.48,
        context_ignored=False,
        reason="LLM confirmed bullish regime",
    )
    assert trace.trace_id != trace3.trace_id


def test_trace_to_dict():
    ts = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)
    trace = LLMDecisionTrace(
        symbol="BTC/USDT",
        timeframe="1h",
        bar_timestamp=ts,
        market_context={"regime_tags": {}, "confidence_adjustment": 1.0},
        context_fingerprint="def456",
        deterministic_forecast={"direction": 0, "score": 0.0},
        exposure_applied=0.0,
        confidence_before=0.3,
        confidence_after=0.3,
        context_ignored=True,
        reason="Anomalies detected, context ignored",
    )
    d = trace.to_dict()
    assert d["symbol"] == "BTC/USDT"
    assert d["context_ignored"] is True
    assert d["trace_id"] == trace.trace_id
    assert d["confidence_after"] == 0.3


# ── DecisionTraceBuffer ─────────────────────────────────────────────────

def test_trace_buffer_drain():
    buf = DecisionTraceBuffer(max_size=100)
    ts = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)

    for i in range(5):
        trace = LLMDecisionTrace(
            symbol="BTC/USDT",
            timeframe="1h",
            bar_timestamp=ts,
            market_context={"confidence_adjustment": 1.0},
            context_fingerprint="xyz",
            deterministic_forecast={"direction": 0, "score": 0.0},
            exposure_applied=0.0,
            confidence_before=0.3,
            confidence_after=0.3,
            context_ignored=False,
            reason=f"Bar {i}",
        )
        buf.add(trace)

    assert len(buf) == 5
    traces = buf.drain()
    assert len(traces) == 5
    assert len(buf) == 0  # buffer is empty after drain


def test_trace_buffer_maxsize():
    buf = DecisionTraceBuffer(max_size=3)
    ts = datetime(2024, 1, 15, 10, 30, tzinfo=UTC)

    for i in range(5):
        trace = LLMDecisionTrace(
            symbol="BTC/USDT",
            timeframe="1h",
            bar_timestamp=ts,
            market_context={"confidence_adjustment": 1.0},
            context_fingerprint="xyz",
            deterministic_forecast={"direction": 0, "score": 0.0},
            exposure_applied=0.0,
            confidence_before=0.3,
            confidence_after=0.3,
            context_ignored=False,
            reason=f"Bar {i}",
        )
        buf.add(trace)

    assert len(buf) == 3  # only last 3 kept
