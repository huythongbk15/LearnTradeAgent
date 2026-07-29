"""
Sentiment Analyst Agent — phân tích market sentiment, momentum, volume.

Không có news feed nên dựa vào price action + volume + RSI để suy luận sentiment.
"""

from __future__ import annotations

import logging

from trading_agent.agents.base import AnalysisContext, AgentMessage, BaseAgent
from trading_agent.agents.llm import ask_agent

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Sentiment Analyst in a multi-agent trading system.

Infer market sentiment from technical data (no news feed available).
Output JSON:
{
  "signal": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentence explanation",
  "details": {
    "sentiment": "bullish"|"bearish"|"neutral"|"fear"|"greed",
    "momentum_strength": "strong"|"moderate"|"weak",
    "volume_insight": "..."
  }
}

Guidelines:
- Strong trend + rising volume = conviction
- Divergence between price and RSI = weakening momentum
- High volume on up days = accumulation
- High volume on down days = distribution
- Low volume breakout = suspect"""


class SentimentAnalyst(BaseAgent):
    """Analyzes market sentiment from price action and volume."""

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        ind = context.indicators
        extra = ind.get("_extra", {})
        price = context.current_price

        prompt_lines = [
            f"Symbol: {context.symbol} ({context.timeframe})",
            f"Current Price: ${price:.2f}",
            f"Price Changes: 1d={context.price_change_1d:+.2f}% | "
            f"1w={context.price_change_1w:+.2f}% | "
            f"1m={context.price_change_1m:+.2f}%",
            "",
            "--- Indicators ---",
        ]

        if "rsi" in ind:
            prompt_lines.append(f"RSI(14): {ind['rsi']:.1f}")

        if extra.get("volume_ratio_5_20"):
            prompt_lines.append(f"Volume Ratio (5/20): {extra['volume_ratio_5_20']:.2f}x")

        if extra.get("change_5"):
            prompt_lines.append(f"5-bar change: {extra['change_5']:+.2f}%")
            prompt_lines.append(f"20-bar change: {extra['change_20']:+.2f}%")

        if extra.get("volatility_20"):
            prompt_lines.append(f"20-bar volatility: {extra['volatility_20']:.2f}%")

        prompt_lines.append("")
        prompt_lines.append("What is the market sentiment based on this data? "
                            "Is fear or greed dominating right now?")

        prompt = "\n".join(prompt_lines)

        try:
            result = ask_agent(SYSTEM_PROMPT, prompt)
            msg = AgentMessage(
                role="sentiment_analyst",
                signal=result.get("signal", "HOLD"),
                confidence=float(result.get("confidence", 0.4)),
                reasoning=result.get("reasoning", ""),
                details={
                    "sentiment": result.get("details", {}).get("sentiment", "neutral"),
                    "momentum_strength": result.get("details", {}).get("momentum_strength", "weak"),
                    "volume_insight": result.get("details", {}).get("volume_insight", ""),
                },
            )
        except Exception as e:
            logger.warning(f"Sentiment LLM failed ({e}), using rule-based")
            msg = self._rule_based(ind, context)

        return msg

    def _rule_based(self, ind: dict, context: AnalysisContext) -> AgentMessage:
        """Rule-based sentiment when LLM unavailable."""
        rsi = ind.get("rsi", 50)
        extra = ind.get("_extra", {})

        # Determine sentiment from RSI and volume
        if isinstance(rsi, (int, float)):
            if rsi < 30:
                sentiment = "fear"
                signal = "BUY"
                confidence = 0.5
                reasoning = f"RSI {rsi:.0f} indicates fear/oversold — contrarian buy"
            elif rsi > 70:
                sentiment = "greed"
                signal = "SELL"
                confidence = 0.5
                reasoning = f"RSI {rsi:.0f} indicates greed/overbought — caution"
            elif 45 <= rsi <= 55:
                sentiment = "neutral"
                signal = "HOLD"
                confidence = 0.3
                reasoning = "RSI in neutral zone — no clear sentiment signal"
            elif 55 < rsi <= 70:
                sentiment = "bullish"
                signal = "HOLD"
                confidence = 0.3
                reasoning = f"Moderate bullish sentiment (RSI {rsi:.0f})"
            else:
                sentiment = "bearish"
                signal = "HOLD"
                confidence = 0.3
                reasoning = f"Moderate bearish sentiment (RSI {rsi:.0f})"
        else:
            sentiment = "neutral"
            signal = "HOLD"
            confidence = 0.2
            reasoning = "No RSI data available"

        momentum = "weak"
        vol_ratio = extra.get("volume_ratio_5_20", 1.0)
        if isinstance(vol_ratio, (int, float)):
            if vol_ratio > 1.5:
                momentum = "strong"
            elif vol_ratio > 1.2:
                momentum = "moderate"

        return AgentMessage(
            role="sentiment_analyst",
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            details={
                "sentiment": sentiment,
                "momentum_strength": momentum,
                "volume_insight": f"Volume ratio: {vol_ratio:.2f}x",
            },
        )
