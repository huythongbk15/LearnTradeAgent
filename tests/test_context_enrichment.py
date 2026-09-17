"""Tests for LLM Context Enrichment Layer.

Tests:
1. MarketContext dataclass construction + validation
2. Deterministic fallback (no LLM)
3. Confidence adjustment clamping
4. Schema validation
5. Integration with AnalysisContext
6. Deterministic backtest mode compatibility
7. LLM never decides signal — only produces context
"""

from __future__ import annotations

from unittest.mock import patch

from trading_agent.agents.base import AnalysisContext
from trading_agent.llm.context_enrichment import (
    ContextEnricher,
    MarketContext,
    SYSTEM_PROMPT,
)


# ── MarketContext dataclass ──────────────────────────────────────────────

def test_market_context_defaults():
    ctx = MarketContext()
    assert ctx.confidence_adjustment == 1.0
    assert ctx.anomaly_flags == []
    assert ctx.regime_tags == {}
    assert ctx.cross_asset_signals == {}


def test_market_context_confidence_clamping():
    """confidence_adjustment must be clamped to [0.5, 1.5]."""
    assert MarketContext(confidence_adjustment=0.1).confidence_adjustment == 0.5
    assert MarketContext(confidence_adjustment=2.0).confidence_adjustment == 1.5
    assert MarketContext(confidence_adjustment=0.75).confidence_adjustment == 0.75


def test_market_context_non_finite_confidence():
    """Non-finite confidence must fall back to 1.0 (no adjustment)."""
    assert MarketContext(confidence_adjustment=float("inf")).confidence_adjustment == 1.0
    assert MarketContext(confidence_adjustment=float("nan")).confidence_adjustment == 1.0


# ── Deterministic fallback ────────────────────────────────────────────────

def test_deterministic_fallback_no_llm():
    """When LLM is disabled, deterministic fallback must be returned."""
    context = AnalysisContext(
        symbol="BTC/USDT",
        timeframe="1h",
        current_price=50000.0,
        indicators={"rsi": 65, "ma_20": 48000, "ma_50": 50000},
    )
    with patch("trading_agent.agents.llm.llm_enabled", return_value=False):
        enricher = ContextEnricher()
        result = enricher.enrich(context)
        assert result.confidence_adjustment == 1.0  # conservative, no change
        assert result.anomaly_flags == []
        assert result.provider == "deterministic"
        assert result.model == "rule_based"


def test_deterministic_regime_detection():
    """Deterministic fallback should classify regime from indicators."""
    context = AnalysisContext(
        symbol="BTC/USDT",
        timeframe="1h",
        current_price=50000.0,
        indicators={
            "rsi": 65,
            "ma_20": 52000,
            "ma_50": 50000,
            "_extra": {"volatility_20": 2.5},
        },
    )
    with patch("trading_agent.agents.llm.llm_enabled", return_value=False):
        enricher = ContextEnricher()
        result = enricher.enrich(context)
        assert "trend" in result.regime_tags
        assert "volatility" in result.regime_tags
        assert result.regime_tags["momentum"] == "normal"


# ── Confidence application ────────────────────────────────────────────────

def test_apply_to_confidence_within_bounds():
    """Confidence after adjustment must remain in [0, 1]."""
    enricher = ContextEnricher()
    ctx = MarketContext(confidence_adjustment=0.7)
    assert 0.0 <= enricher.apply_to_confidence(0.5, ctx) <= 1.0

    ctx = MarketContext(confidence_adjustment=1.5)
    assert 0.0 <= enricher.apply_to_confidence(0.8, ctx) <= 1.0

    ctx = MarketContext(confidence_adjustment=0.5)
    assert 0.0 <= enricher.apply_to_confidence(0.0, ctx) <= 1.0


def test_apply_to_confidence_extreme_values():
    """Edge cases: 0 confidence stays 0, 1.0 confidence stays 1.0 after adjustment."""
    enricher = ContextEnricher()
    ctx = MarketContext(confidence_adjustment=0.5)
    assert enricher.apply_to_confidence(0.0, ctx) == 0.0
    assert enricher.apply_to_confidence(1.0, ctx) == 0.5


# ── Schema validation ────────────────────────────────────────────────────

def test_from_llm_response_valid():
    """Valid LLM response should be parsed into MarketContext."""
    payload = {
        "regime_tags": {"trend": "bullish", "volatility": "low"},
        "anomaly_flags": ["rsi_divergence"],
        "cross_asset_signals": {},
        "confidence_adjustment": 0.8,
        "reasoning": "Test context",
        "details": {"source": "llm"},
    }
    ctx = MarketContext.from_llm_response(payload)
    assert ctx.regime_tags["trend"] == "bullish"
    assert "rsi_divergence" in ctx.anomaly_flags
    assert ctx.confidence_adjustment == 0.8


def test_from_llm_response_clamps_confidence_adjustment():
    """confidence_adjustment outside [0.5, 1.5] should be clamped."""
    payload = {
        "regime_tags": {},
        "anomaly_flags": [],
        "cross_asset_signals": {},
        "confidence_adjustment": 5.0,
        "reasoning": "",
        "details": {},
    }
    ctx = MarketContext.from_llm_response(payload)
    assert ctx.confidence_adjustment == 1.5


# ── LLM never produces signal ─────────────────────────────────────────────

def test_context_enricher_no_buy_sell_signal():
    """The SYSTEM_PROMPT must explicitly forbid producing BUY/SELL signals."""
    assert "not produce a signal" in SYSTEM_PROMPT.lower()
    assert "confidence_adjustment" in SYSTEM_PROMPT


# ── Integration with AnalysisContext ─────────────────────────────────────

def test_enrich_from_analysis_context():
    """ContextEnricher should extract data from AnalysisContext."""
    context = AnalysisContext(
        symbol="ETH/USDT",
        timeframe="4h",
        current_price=3000.0,
        indicators={
            "rsi": 45.2,
            "ma_20": 2950.0,
            "ma_50": 3000.0,
            "_extra": {
                "funding_rate": 0.0001,
                "buy_pressure": 0.55,
                "volatility_20": 2.5,
                "cvd_short_window": 150.0,
            },
        },
    )
    with patch("trading_agent.agents.llm.llm_enabled", return_value=False):
        enricher = ContextEnricher()
        result = enricher.enrich(context)
        assert result.reasoning == "deterministic fallback (LLM unavailable)"


# ── Deterministic backtest mode ─────────────────────────────────────────

def test_backtest_mode_uses_backtest_ask_agent():
    """When backtest mode is enabled, should use backtest_ask_agent not ask_agent."""
    context = AnalysisContext(
        symbol="BTC/USDT",
        timeframe="1h",
        current_price=50000.0,
        indicators={"rsi": 50},
    )

    with patch("trading_agent.agents.llm.llm_enabled", return_value=True), \
         patch("trading_agent.llm.context_enrichment.is_backtest_mode", return_value=True), \
         patch("trading_agent.llm.context_enrichment.backtest_ask_agent") as mock_backtest, \
         patch("trading_agent.llm.context_enrichment.ask_agent") as mock_ask:
        mock_backtest.return_value = {
            "regime_tags": {"trend": "neutral"},
            "anomaly_flags": [],
            "cross_asset_signals": {},
            "confidence_adjustment": 1.0,
            "reasoning": "backtest mode",
            "details": {},
        }
        enricher = ContextEnricher()
        result = enricher.enrich(context)
        assert mock_backtest.called  # backtest path used
        assert not mock_ask.called  # real ask_agent NOT called
        assert result.reasoning == "backtest mode"


def test_non_backtest_mode_uses_ask_agent():
    """When backtest mode is disabled, should use ask_agent."""
    context = AnalysisContext(
        symbol="BTC/USDT",
        timeframe="1h",
        current_price=50000.0,
        indicators={"rsi": 50},
    )

    with patch("trading_agent.agents.llm.llm_enabled", return_value=True), \
         patch("trading_agent.llm.context_enrichment.is_backtest_mode", return_value=False), \
         patch("trading_agent.llm.context_enrichment.ask_agent") as mock_ask:
        mock_ask.return_value = {
            "regime_tags": {"trend": "neutral"},
            "anomaly_flags": [],
            "cross_asset_signals": {},
            "confidence_adjustment": 1.0,
            "reasoning": "live mode",
            "details": {},
        }
        enricher = ContextEnricher()
        result = enricher.enrich(context)
        assert mock_ask.called
