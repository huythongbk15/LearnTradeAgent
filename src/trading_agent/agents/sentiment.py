"""
Sentiment Analyst Agent — phân tích market sentiment, momentum, volume.

Không có news feed nên dựa vào price action + volume + RSI để suy luận sentiment.
"""

from __future__ import annotations

import logging

from trading_agent.agents.base import AgentMessage, AnalysisContext, BaseAgent
from trading_agent.agents.llm import ask_agent, llm_enabled

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

        # LLM disabled → rule-based ngay, không build prompt
        if not llm_enabled():
            return self._rule_based(ind, context)

        prompt_lines = [
            f"Symbol: {context.symbol} ({context.timeframe})",
            f"Current Price: ${price:.2f}",
        ]

        price_changes = []
        if context.price_change_1d is not None:
            price_changes.append(f"1d={context.price_change_1d:+.2f}%")
        if context.price_change_1w is not None:
            price_changes.append(f"1w={context.price_change_1w:+.2f}%")
        if context.price_change_1m is not None:
            price_changes.append(f"1m={context.price_change_1m:+.2f}%")
        if price_changes:
            prompt_lines.append(f"Price Changes: {' | '.join(price_changes)}")

        prompt_lines.append("")
        prompt_lines.append("--- Indicators ---")

        if "rsi" in ind:
            prompt_lines.append(f"RSI(14): {ind['rsi']:.1f}")

        if extra.get("volume_ratio_5_20"):
            prompt_lines.append(f"Volume Ratio (5/20): {extra['volume_ratio_5_20']:.2f}x")

        if extra.get("change_5"):
            prompt_lines.append(f"5-bar change: {extra['change_5']:+.2f}%")
            prompt_lines.append(f"20-bar change: {extra['change_20']:+.2f}%")

        if extra.get("volatility_20"):
            prompt_lines.append(f"20-bar volatility: {extra['volatility_20']:.2f}%")

        # ── Alt-data for LLM ───────────────────────────────────────────
        if extra.get("funding_rate") is not None:
            prompt_lines.append(f"Funding Rate: {extra['funding_rate']:.6f} ({extra['funding_rate']*100:.4f}%)")
        if extra.get("open_interest") is not None:
            prompt_lines.append(f"Open Interest: {extra['open_interest']:,.0f}")
        if extra.get("buy_pressure") is not None:
            prompt_lines.append(f"Buy Pressure: {extra['buy_pressure']:.2%} | Sell Pressure: {extra.get('sell_pressure', 0):.2%}")
        if extra.get("cvd_short_window") is not None:
            prompt_lines.append(f"CVD (short): {extra['cvd_short_window']:.2f}")

        prompt_lines.append("")
        prompt_lines.append("What is the market sentiment based on this data? "
                            "Is fear or greed dominating right now?")

        prompt = "\n".join(prompt_lines)

        try:
            result = ask_agent(SYSTEM_PROMPT, prompt, schema="sentiment")
            msg = AgentMessage(
                role="sentiment_analyst",
                signal=result.get("signal", "HOLD"),
                confidence=float(result.get("confidence", 0.4)),
                reasoning=result.get("reasoning", ""),
                details={
                    "sentiment": result.get("details", {}).get("sentiment", "neutral"),
                    "momentum_strength": result.get("details", {}).get("momentum_strength", "weak"),
                    "volume_insight": result.get("details", {}).get("volume_insight", ""),
                    "funding_rate": extra.get("funding_rate"),
                    "buy_pressure": extra.get("buy_pressure"),
                    "cvd": extra.get("cvd_short_window"),
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

        # ── Alt-data: funding, OI, CVD ─────────────────────────────────
        funding_rate = extra.get("funding_rate", 0.0)  # e.g., 0.0001 = 0.01%
        buy_pressure = extra.get("buy_pressure", 0.5)
        sell_pressure = extra.get("sell_pressure", 0.5)
        cvd = extra.get("cvd_short_window", 0.0)
        open_interest = extra.get("open_interest", None)

        # Determine sentiment from trend + RSI
        # Trend-aware: khi MA20 < MA50 (downtrend) thì sentiment bearish
        # (theo trend để hỗ trợ thoát lệnh), ngược lại contrarian ở vùng cực đoan.
        ma_fast = ind.get("ma_20")
        ma_slow = ind.get("ma_50")
        downtrend = isinstance(ma_fast, (int, float)) and isinstance(ma_slow, (int, float)) and ma_fast < ma_slow
        uptrend = isinstance(ma_fast, (int, float)) and isinstance(ma_slow, (int, float)) and ma_fast > ma_slow

        if not isinstance(rsi, (int, float)):
            sentiment = "neutral"
            signal = "HOLD"
            confidence = 0.2
            reasoning = "No RSI data available"
        elif downtrend:
            if rsi >= 30:
                sentiment = "bearish"
                signal = "SELL"
                confidence = 0.40
                reasoning = f"Downtrend (MA20<MA50, RSI {rsi:.0f}) — bearish sentiment"
            else:
                sentiment = "fear"
                signal = "SELL"
                confidence = 0.30
                reasoning = f"Downtrend + oversold (RSI {rsi:.0f}) — capitulation, stay out"
        elif uptrend:
            if rsi < 70:
                sentiment = "bullish"
                signal = "BUY"
                confidence = 0.40
                reasoning = f"Uptrend (MA20>MA50, RSI {rsi:.0f}) — bullish sentiment"
            else:
                sentiment = "greed"
                signal = "HOLD"
                confidence = 0.35
                reasoning = f"Uptrend but RSI {rsi:.0f} overbought — don't chase"
        else:
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
                sentiment = "greedy"
                signal = "SELL"
                confidence = 0.35
                reasoning = f"Moderate bullish sentiment (RSI {rsi:.0f}) — slight caution"
            else:
                sentiment = "fearful"
                signal = "BUY"
                confidence = 0.35
                reasoning = f"Moderate bearish sentiment (RSI {rsi:.0f}) — slight bargain hunting"

        # ── Adjust confidence/signal based on alt-data ───────────────────
        alt_notes = []
        
        # Funding rate interpretation
        if abs(funding_rate) > 0.0005:  # > 0.05%
            if funding_rate > 0:
                # Positive funding = longs pay shorts = crowded longs = risk
                if signal == "BUY":
                    confidence *= 0.7
                    alt_notes.append(f"funding {funding_rate:.4%} (crowded longs)")
                elif signal == "SELL":
                    confidence *= 1.2
                    alt_notes.append(f"funding {funding_rate:.4%} (shorts favored)")
            else:
                # Negative funding = shorts pay longs = crowded shorts = risk
                if signal == "SELL":
                    confidence *= 0.7
                    alt_notes.append(f"funding {funding_rate:.4%} (crowded shorts)")
                elif signal == "BUY":
                    confidence *= 1.2
                    alt_notes.append(f"funding {funding_rate:.4%} (longs favored)")

        # Buy/Sell pressure
        if buy_pressure > 0.65:
            if signal == "BUY":
                confidence *= 1.1
                alt_notes.append(f"buy pressure {buy_pressure:.0%}")
            elif signal == "SELL":
                confidence *= 0.85
                alt_notes.append(f"buy pressure {buy_pressure:.0%} (divergence)")
        elif sell_pressure > 0.65:
            if signal == "SELL":
                confidence *= 1.1
                alt_notes.append(f"sell pressure {sell_pressure:.0%}")
            elif signal == "BUY":
                confidence *= 0.85
                alt_notes.append(f"sell pressure {sell_pressure:.0%} (divergence)")

        # CVD
        if abs(cvd) > 0:
            if cvd > 0 and signal == "SELL":
                confidence *= 0.9
                alt_notes.append("positive CVD (buying)")
            elif cvd < 0 and signal == "BUY":
                confidence *= 0.9
                alt_notes.append("negative CVD (selling)")

        # Volume momentum
        momentum = "weak"
        vol_ratio = extra.get("volume_ratio_5_20", 1.0)
        if isinstance(vol_ratio, (int, float)):
            if vol_ratio > 1.5:
                momentum = "strong"
            elif vol_ratio > 1.2:
                momentum = "moderate"

        # Cap confidence
        confidence = min(max(confidence, 0.1), 0.9)

        reasoning_parts = [reasoning]
        if alt_notes:
            reasoning_parts.append("Alt-data: " + "; ".join(alt_notes))
        reasoning = " | ".join(reasoning_parts)

        return AgentMessage(
            role="sentiment_analyst",
            signal=signal,
            confidence=confidence,
            reasoning=reasoning,
            details={
                "sentiment": sentiment,
                "momentum_strength": momentum,
                "volume_insight": f"Volume ratio: {vol_ratio:.2f}x",
                "funding_rate": funding_rate,
                "buy_pressure": buy_pressure,
                "cvd": cvd,
            },
        )
