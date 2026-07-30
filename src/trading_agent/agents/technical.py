"""
Technical Analyst Agent — phân tích indicators + price action.

Sử dụng các indicator có sẵn từ system + LLM để đưa ra nhận định kỹ thuật.
Fallback: rule-based khi LLM không available.
"""

from __future__ import annotations

import logging

import polars as pl

from trading_agent.agents.base import AgentMessage, AnalysisContext, BaseAgent
from trading_agent.agents.llm import ask_agent

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Technical Analyst in a multi-agent trading system.

Analyze the provided technical indicators and price data. Output JSON:
{
  "signal": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentence explanation",
  "details": {
    "trend": "uptrend"|"downtrend"|"sideways",
    "key_levels": {"support": price, "resistance": price},
    "momentum": "bullish"|"bearish"|"neutral",
    "indicators_summary": "..."
  }
}

Guidelines:
- Prioritize trend-following on higher timeframes
- Use multiple timeframe alignment for stronger signals
- BB squeeze + low RSI = potential reversal buy
- RSI > 70 in uptrend = continuation, not necessarily sell
- Volume confirmation adds confidence"""


class TechnicalAnalyst(BaseAgent):
    """Analyzes price action, trend, momentum, volatility."""

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        # Build indicator summary from context
        ind = context.indicators
        price = context.current_price

        # Compute extra indicators if OHLCV available
        df = context.ohlcv
        if df is not None and len(df) > 50:
            ind = self._compute_extra_indicators(df, ind)

        # Build prompt
        prompt = self._build_prompt(context, ind)

        # Try LLM first
        try:
            result = ask_agent(SYSTEM_PROMPT, prompt)
            details = result.get("details", {})
            # Parse key levels (ensure floats)
            key_levels = details.get("key_levels", {})
            if isinstance(key_levels, dict):
                try:
                    key_levels = {
                        k: float(v) if v is not None else None
                        for k, v in key_levels.items()
                    }
                except (ValueError, TypeError):
                    key_levels = {"support": None, "resistance": None}

            msg = AgentMessage(
                role="technical_analyst",
                signal=result.get("signal", "HOLD"),
                confidence=float(result.get("confidence", 0.5)),
                reasoning=result.get("reasoning", ""),
                details={
                    "trend": details.get("trend", "sideways"),
                    "key_levels": key_levels,
                    "momentum": details.get("momentum", "neutral"),
                    "indicators_summary": details.get("indicators_summary", ""),
                    "extra_indicators": ind.get("_extra", {}),
                },
            )
        except Exception as e:
            logger.warning(f"Technical LLM failed ({e}), using rule-based")
            msg = self._rule_based(ind, context)

        return msg

    def _compute_extra_indicators(
        self, df: pl.DataFrame, existing: dict
    ) -> dict:
        """Compute additional technical indicators beyond basic ones."""
        extra = {}

        # Price changes
        closes = df["close"].to_numpy()
        if len(closes) > 20:
            extra["price_now"] = float(closes[-1])
            extra["price_5_ago"] = float(closes[-5])
            extra["price_20_ago"] = float(closes[-20])
            extra["change_5"] = float((closes[-1] / closes[-5] - 1) * 100)
            extra["change_20"] = float((closes[-1] / closes[-20] - 1) * 100)

        # Volatility (20-bar)
        if len(closes) > 20:
            import numpy as np
            returns_20 = np.diff(closes[-21:]) / closes[-21:-1]
            extra["volatility_20"] = float(np.std(returns_20) * 100)

        # Volume trend
        if "volume" in df.columns:
            vols = df["volume"].to_numpy()
            if len(vols) > 20:
                avg_vol_20 = float(vols[-20:].mean())
                avg_vol_5 = float(vols[-5:].mean())
                extra["volume_ratio_5_20"] = (
                    float(avg_vol_5 / avg_vol_20) if avg_vol_20 > 0 else 1.0
                )

        existing["_extra"] = extra
        return existing

    def _build_prompt(self, context: AnalysisContext, ind: dict) -> str:
        extra = ind.get("_extra", {})
        # Price changes (some may be None when insufficient data)
        changes = []
        if context.price_change_1d is not None:
            changes.append(f"1d={context.price_change_1d:+.2f}%")
        if context.price_change_1w is not None:
            changes.append(f"1w={context.price_change_1w:+.2f}%")
        if context.price_change_1m is not None:
            changes.append(f"1m={context.price_change_1m:+.2f}%")

        lines = [
            f"Symbol: {context.symbol} ({context.timeframe})",
            f"Current Price: ${context.current_price:.2f}",
            f"Price Changes: {' | '.join(changes)}" if changes else "",
            "",
            "--- Technical Indicators ---",
        ]

        # Add available indicators
        for key in ["ma_5", "ma_10", "ma_20", "ma_30", "ma_50", "ma_100", "ma_200"]:
            if key in ind:
                lines.append(f"{key}: {ind[key]:.2f}")

        if "rsi" in ind:
            lines.append(f"RSI(14): {ind['rsi']:.1f}")
        if "bb_upper" in ind:
            lines.append(f"Bollinger Upper: {ind['bb_upper']:.2f}")
            lines.append(f"Bollinger Lower: {ind['bb_lower']:.2f}")

        if extra:
            lines.append("")
            lines.append("--- Extra Analysis ---")
            if "change_5" in extra:
                lines.append(f"5-bar change: {extra['change_5']:+.2f}%")
                lines.append(f"20-bar change: {extra['change_20']:+.2f}%")
            if "volatility_20" in extra:
                lines.append(f"20-bar volatility: {extra['volatility_20']:.2f}%")
            if "volume_ratio_5_20" in extra:
                lines.append(f"Volume ratio (5/20): {extra['volume_ratio_5_20']:.2f}x")

        lines.append("")
        lines.append("Put yourself in the role of a seasoned technical analyst at a prop trading firm. "
                      "What is your recommendation based on this data?")

        return "\n".join(lines)

    def _rule_based(self, ind: dict, context: AnalysisContext) -> AgentMessage:
        """Rule-based fallback when LLM is unavailable."""
        rsi = ind.get("rsi", 50)
        price = context.current_price

        signal = "HOLD"
        confidence = 0.3
        reasoning = "Insufficient data for rule-based analysis"
        details = {"trend": "unknown", "momentum": "neutral"}

        if rsi and isinstance(rsi, (int, float)):
            if rsi < 30:
                signal = "BUY"
                confidence = min(0.5 + (30 - rsi) / 100, 0.7)
                reasoning = f"RSI at {rsi:.1f} — oversold, potential bounce"
                details = {"trend": "potential reversal", "momentum": "bullish"}
            elif rsi > 70:
                signal = "SELL"
                confidence = min(0.5 + (rsi - 70) / 100, 0.7)
                reasoning = f"RSI at {rsi:.1f} — overbought, potential pullback"
                details = {"trend": "potential reversal", "momentum": "bearish"}
            else:
                signal = "HOLD"
                confidence = 0.4
                reasoning = f"RSI at {rsi:.1f} — neutral zone"
                details = {"trend": "sideways", "momentum": "neutral"}

        return AgentMessage(
            role="technical_analyst",
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            details=details,
        )
