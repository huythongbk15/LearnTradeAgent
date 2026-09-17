"""
LLM Context Enrichment Layer — produces structured ``MarketContext`` artifact.

**LLM does NOT make decisions.** It produces a ``MarketContext`` containing
regime_tags, anomaly_flags, and cross_asset_signals. The deterministic
quant engine consumes these as **metadata enrichment** — adjusting
confidence weighting or triggering additional validation — but never
as a direct signal/exposure authority.

Design:
    Market Observation → Deterministic Feature Extraction →
        → Context Enricher (LLM batch call, cached) →
        → MarketContext { regime_confluence, anomaly_flags, ... }
        → Deterministic engine reads MarketContext.metadata →
        → Adjusts confidence_weight multiplier (0.7x–1.3x) on EXISTING signal

Deterministic backtest mode: `enable_backtest_mode()` from `trading_agent.agents.llm`
forces temperature=0, fixed seed, no-cache, single provider.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, asdict
from datetime import UTC, datetime
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from trading_agent.llm.research_memory import ResearchMemory

from trading_agent.agents.base import AnalysisContext
from trading_agent.agents.llm import (
    ask_agent,
    backtest_ask_agent,
    is_backtest_mode,
)

logger = logging.getLogger(__name__)

# ── Structured output schema ─────────────────────────────────────────────

# The context layer does NOT produce signals. Validation is inline in
# MarketContext.from_llm_response — no AGENT_SCHEMAS entry needed.


# ── MarketContext dataclass ──────────────────────────────────────────────

@dataclass(frozen=True, slots=True)
class MarketContext:
    """Structured market context produced by the LLM context layer.

    This is the ONLY thing LLM should output in the decision pipeline.
    Every field is consumed by the deterministic engine to enrich
    an existing signal — never to generate a new one.
    """

    # Regime classification (deterministic engine confidence weighting)
    regime_tags: dict[str, str] = field(default_factory=dict)
    # e.g. {"trend": "bullish_weak", "volatility": "low_mean_reverting",
    #        "momentum": "diverging", "seasonality": "neutral"}

    # Anomaly flags (deterministic engine triggers validation if set)
    anomaly_flags: list[str] = field(default_factory=list)
    # e.g. ["cvd_price_divergence", "funding_extreme", "oi_spike", "volume_anomaly"]

    # Cross-asset signals (deterministic engine checks coherence)
    cross_asset_signals: dict[str, dict[str, Any]] = field(default_factory=dict)
    # e.g. {"ETH/USDT": {"signal": "BUY", "confidence": 0.7}, ...}

    # LLM confidence adjustment for the EXISTING deterministic signal
    # (0.7x = reduce confidence, 1.3x = amplify, NOT a direct exposure)
    confidence_adjustment: float = 1.0

    # Freeform reasoning (for audit trace, NOT for computation)
    reasoning: str = ""

    # Full LLM response (for audit/debug)
    details: dict[str, Any] = field(default_factory=dict)

    # Metadata
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    provider: str = ""
    model: str = ""
    tokens_used: int = 0

    def __post_init__(self) -> None:
        # Clamp confidence_adjustment to safe range
        if not math.isfinite(self.confidence_adjustment):
            object.__setattr__(self, "confidence_adjustment", 1.0)
        else:
            object.__setattr__(
                self,
                "confidence_adjustment",
                max(0.5, min(1.5, self.confidence_adjustment)),
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage in deterministic DecisionPacket metadata."""
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        return d

    @classmethod
    def from_llm_response(cls, payload: dict[str, Any]) -> "MarketContext":
        """Build MarketContext from a validated LLM JSON response.

        Uses inline validation (NOT ``validate_agent_output``) because the
        context layer does NOT produce BUY/SELL/HOLD signals — enforcing
        the architectural invariant that LLM is enrichment-only.
        """
        if not isinstance(payload, dict):
            raise ValueError("context_enrichment returned non-object JSON")

        # Required keys
        if "regime_tags" not in payload:
            raise ValueError("context_enrichment missing 'regime_tags'")
        if "anomaly_flags" not in payload:
            raise ValueError("context_enrichment missing 'anomaly_flags'")
        if "cross_asset_signals" not in payload:
            raise ValueError("context_enrichment missing 'cross_asset_signals'")
        if "confidence_adjustment" not in payload:
            raise ValueError("context_enrichment missing 'confidence_adjustment'")

        regime_tags = payload["regime_tags"]
        if not isinstance(regime_tags, dict):
            raise ValueError("'regime_tags' must be a dict")

        anomaly_flags = payload["anomaly_flags"]
        if not isinstance(anomaly_flags, list):
            anomaly_flags = [str(anomaly_flags)]

        cross_asset = payload["cross_asset_signals"]
        if not isinstance(cross_asset, dict):
            raise ValueError("'cross_asset_signals' must be a dict")

        adjustment_raw = payload["confidence_adjustment"]
        try:
            adjustment = float(adjustment_raw)
            if not math.isfinite(adjustment):
                adjustment = 1.0
        except (TypeError, ValueError):
            adjustment = 1.0

        return cls(
            regime_tags={str(k): str(v) for k, v in regime_tags.items()},
            anomaly_flags=[str(f) for f in anomaly_flags],
            cross_asset_signals=cross_asset,
            confidence_adjustment=adjustment,
            reasoning=str(payload.get("reasoning", "")),
            details=payload.get("details", {}),
        )


# ── Deterministic fallback ───────────────────────────────────────────────

def _deterministic_context(
    context: AnalysisContext,
    indicators: dict[str, Any] | None = None,
    *,
    extra_market_data: dict[str, Any] | None = None,
) -> MarketContext:
    """Rule-based MarketContext when LLM is unavailable.

    Always conservative: NO anomaly flags, neutral regime, confidence_adjustment = 1.0.
    """
    ind = indicators if indicators is not None else (context.indicators or {})
    extra = ind.get("_extra", {}) if isinstance(ind, dict) else {}
    if extra_market_data:
        extra = {**extra, **extra_market_data}
    regime_tags: dict[str, str] = {}

    # Trend regime (deterministic)
    ma_fast = ind.get("ma_20")
    ma_slow = ind.get("ma_50")
    if isinstance(ma_fast, (int, float)) and isinstance(ma_slow, (int, float)):
        if ma_fast > ma_slow * 1.005:
            regime_tags["trend"] = "bullish"
        elif ma_fast < ma_slow * 0.995:
            regime_tags["trend"] = "bearish"
        else:
            regime_tags["trend"] = "neutral"

    # Volatility regime
    vol_20 = extra.get("volatility_20")
    if isinstance(vol_20, (int, float)):
        if vol_20 > 3.0:
            regime_tags["volatility"] = "high"
        elif vol_20 < 1.0:
            regime_tags["volatility"] = "low"
        else:
            regime_tags["volatility"] = "medium"

    # RSI regime
    rsi = ind.get("rsi")
    if isinstance(rsi, (int, float)):
        if rsi < 30:
            regime_tags["momentum"] = "oversold"
        elif rsi > 70:
            regime_tags["momentum"] = "overbought"
        else:
            regime_tags["momentum"] = "normal"

    return MarketContext(
        regime_tags=regime_tags,
        anomaly_flags=[],
        cross_asset_signals={},
        confidence_adjustment=1.0,
        reasoning="deterministic fallback (LLM unavailable)",
        details={"source": "deterministic", "regime_from": "technical_indicators"},
        provider="deterministic",
        model="rule_based",
    )


# ── Context Enricher ─────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a Market Context Analyst in a systematic trading system.

Your job is to analyze market conditions and produce a Structured MarketContext.
You do NOT decide BUY/SELL signals — the deterministic engine does that.
Your output modifies the confidence weighting of EXISTING signals.

Analyze the provided data and output JSON:
{
  "regime_tags": {
    "trend": "bullish" | "bearish" | "neutral",
    "volatility": "high" | "medium" | "low",
    "momentum": "bullish_diverging" | "bearish_diverging" | "normal" | "oversold" | "overbought",
    "volume_regime": "accumulation" | "distribution" | "neutral",
    "seasonality": "favorable" | "unfavorable" | "neutral"
  },
  "anomaly_flags": ["string", ...],
  "cross_asset_signals": {},
  "confidence_adjustment": 0.5–1.5,
  "reasoning": "1-2 sentence summary",
  "details": {}
}

Guidelines:
- anomaly_flags: list technical anomalies like 'rsi_divergence', 'cvd_price_divergence', 'funding_extreme', 'oi_spike', 'order_book_imbalance'
- cross_asset_signals: empty dict unless you see related-asset context
- confidence_adjustment: adjust based on regime clarity (clear regime = higher, uncertain = lower)
- Do NOT produce a signal — only context for the deterministic engine
"""


class ContextEnricher:
    """LLM-powered context enrichment for deterministic trading engine.

    Usage:
        enricher = ContextEnricher()
        context = enricher.enrich(analysis_context, indicators)

    The resulting ``MarketContext`` is merged into the deterministic
    DecisionPacket as metadata. The confidence_adjustment field modifies
    the confidence of the EXISTING signal (0.7x = reduce, 1.3x = amplify).
    """

    # Confidence adjustment multiplier applied to deterministic signals
    # when specific anomalies are detected
    _ANOMALY_CONFIDENCE_PENALTIES: dict[str, float] = {
        "rsi_divergence": 0.75,
        "cvd_price_divergence": 0.8,
        "funding_extreme": 0.85,
        "oi_spike": 0.9,
        "volume_anomaly": 0.9,
        "order_book_imbalance": 0.85,
    }

    def __init__(self) -> None:
        """Initialize ContextEnricher.

        Validation schema is inline (MarketContext.from_llm_response) —
        the context layer does NOT use AGENT_SCHEMAS because it does NOT
        produce BUY/SELL/HOLD signals.
        """
        pass

    def enrich(
        self,
        context: AnalysisContext,
        indicators: dict[str, Any] | None = None,
        *,
        symbol: str | None = None,
        timeframe: str | None = None,
        extra_market_data: dict[str, Any] | None = None,
    ) -> MarketContext:
        """Enrich deterministic signal with LLM-produced MarketContext.

        Args:
            context: AnalysisContext with indicators and price data
            indicators: Optional override for indicators dict
            symbol: Optional symbol override
            timeframe: Optional timeframe override
            extra_market_data: Optional dict with funding_rate, open_interest,
                              buy_pressure, sell_pressure, CVD, etc.

        Returns:
            MarketContext — structured, validated, deterministic-fallback on error
        """
        ind = indicators if indicators is not None else (context.indicators or {})
        extra = ind.get("_extra", extra_market_data or {})
        sym = symbol or context.symbol
        tf = timeframe or context.timeframe
        price = context.current_price

        # Build prompt from observable data only — no hallucination seeds
        prompt = self._build_prompt(context, ind, extra, sym, tf, price)

        if not self._llm_available():
            return _deterministic_context(context, ind, extra_market_data=extra)

        try:
            # Use backtest_ask_agent if in backtest mode, else ask_agent
            if is_backtest_mode():
                raw = backtest_ask_agent(
                    SYSTEM_PROMPT, prompt
                )
            else:
                raw = ask_agent(SYSTEM_PROMPT, prompt)

            return MarketContext.from_llm_response(raw)
        except Exception as e:
            logger.warning(f"Context enrichment LLM call failed ({e}), using deterministic fallback")
            return _deterministic_context(context, ind, extra_market_data=extra)

    def _llm_available(self) -> bool:
        """Check if LLM is enabled and configured."""
        from trading_agent.agents.llm import llm_enabled
        return llm_enabled()

    def _build_prompt(
        self,
        context: AnalysisContext,
        ind: dict[str, Any],
        extra: dict[str, Any],
        symbol: str,
        timeframe: str,
        price: float,
    ) -> str:
        """Build deterministic market data prompt for LLM context analysis."""
        lines = [
            f"Symbol: {symbol} ({timeframe})",
            f"Current Price: ${price:.2f}",
        ]

        # Price changes
        if context.price_change_1d is not None:
            lines.append(f"1d change: {context.price_change_1d:+.4f}")
        if context.price_change_1w is not None:
            lines.append(f"1w change: {context.price_change_1w:+.4f}")
        if context.price_change_1m is not None:
            lines.append(f"1m change: {context.price_change_1m:+.4f}")

        # Key indicators
        lines.append("")
        lines.append("--- Technical Indicators ---")
        if "rsi" in ind:
            lines.append(f"RSI(14): {ind['rsi']:.1f}")
        if extra.get("volume_ratio_5_20"):
            lines.append(f"Volume ratio (5/20): {extra['volume_ratio_5_20']:.2f}x")

        # Moving averages
        for ma_name in ("ma_9", "ma_20", "ma_21", "ma_50", "ma_200"):
            if ma_name in ind and isinstance(ind[ma_name], (int, float)):
                lines.append(f"{ma_name.upper()}: {ind[ma_name]:.2f}")

        # Volatility
        if extra.get("volatility_20"):
            lines.append(f"20-bar volatility: {extra['volatility_20']:.2f}%")

        # Alt-data
        if extra.get("funding_rate") is not None:
            lines.append(f"Funding rate: {extra['funding_rate']:.6f} ({extra['funding_rate'] * 100:.4f}%)")
        if extra.get("open_interest") is not None:
            lines.append(f"Open interest: {extra['open_interest']:,.0f}")
        if extra.get("buy_pressure") is not None:
            lines.append(f"Buy pressure: {extra['buy_pressure']:.2%} | Sell pressure: {extra.get('sell_pressure', 0):.2%}")
        if extra.get("cvd_short_window") is not None:
            lines.append(f"CVD (short): {extra['cvd_short_window']:.2f}")

        # EMA ribbon, ADX, OBV, etc
        for k in ("adx", "dx", "obv", "ema_12", "ema_26", "ema_diff"):
            if k in ind and isinstance(ind[k], (int, float)):
                lines.append(f"{k.upper()}: {ind[k]:.2f}")

        lines.append("")
        lines.append("Analyze regime, anomalies, and provide confidence adjustment (0.5-1.5).")
        lines.append("DO NOT output a BUY/SELL signal. Only context for deterministic engine.")

        return "\n".join(lines)

    def apply_to_confidence(self, original_confidence: float, context: MarketContext) -> float:
        """Apply LLM confidence_adjustment to a deterministic signal's confidence.

        This is the ONLY way the deterministic engine should consume the
        MarketContext — as a **multiplier** on an existing confidence, NOT
        as a signal source.
        """
        return max(0.0, min(1.0, original_confidence * context.confidence_adjustment))

    def replay(
        self,
        context: AnalysisContext,
        indicators: dict[str, Any] | None = None,
        *,
        bar_timestamp: datetime | None = None,
        symbol: str | None = None,
        timeframe: str | None = None,
        extra_market_data: dict[str, Any] | None = None,
        memory: ResearchMemory | None = None,
    ) -> MarketContext:
        """Replay stored MarketContext from ResearchMemory (no LLM calls).

        This is the A/B test entry point: replay a previously stored
        MarketContext for a given bar instead of calling the LLM.

        Args:
            context: AnalysisContext with indicators and price data
            indicators: Optional override for indicators dict
            bar_timestamp: Timestamp of the bar to retrieve context for
            symbol: Optional symbol override
            timeframe: Optional timeframe override
            extra_market_data: Optional dict for deterministic fallback
            memory: ResearchMemory instance to read from

        Returns:
            MarketContext from storage, or deterministic fallback if not found
        """
        if memory is None:
            raise ValueError("ResearchMemory instance required for replay mode")

        ind = indicators if indicators is not None else (context.indicators or {})
        sym = symbol or context.symbol
        tf = timeframe or context.timeframe
        ts = bar_timestamp or getattr(context, "bar_timestamp", None) or datetime.now(UTC)

        stored = memory.retrieve(sym, tf, ts, deterministic=False)
        if stored is not None:
            return stored

        # Not found in memory → use deterministic fallback
        logger.debug(
            f"Replay: no stored MarketContext for {sym}/{tf} at {ts}, "
            "using deterministic fallback"
        )
        return _deterministic_context(
            context, ind, extra_market_data=extra_market_data
        )
