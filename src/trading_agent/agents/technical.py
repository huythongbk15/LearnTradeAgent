"""
Technical Analyst Agent — phân tích indicators + price action.

Sử dụng các indicator có sẵn từ system + LLM để đưa ra nhận định kỹ thuật.
Fallback: rule-based khi LLM không available.
"""

from __future__ import annotations

import logging

import polars as pl

from trading_agent.agents.base import AgentMessage, AnalysisContext, BaseAgent
from trading_agent.agents.llm import ask_agent, llm_enabled
from trading_agent.regime import add_regime_indicators

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

        # LLM disabled → rule-based ngay, không build prompt (backtest USE_LLM=false)
        if not llm_enabled():
            return self._rule_based(ind, context)

        # Build prompt
        prompt = self._build_prompt(context, ind)

        # Try LLM first
        try:
            result = ask_agent(SYSTEM_PROMPT, prompt, schema="technical")
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
                    "regime": {
                        "vol_regime": ind.get("_extra", {}).get("vol_regime"),
                        "trend_regime": ind.get("_extra", {}).get("trend_regime"),
                        "trend_dir": ind.get("_extra", {}).get("trend_dir"),
                        "adx": ind.get("_extra", {}).get("adx"),
                        "atr_pctl": ind.get("_extra", {}).get("atr_pctl"),
                    },
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

        # === REGIME DETECTION ===
        # Fast path: upstream (e.g. agent_ensemble backtest) precomputes regime
        # indicators vectorized once and passes them via existing["_extra"]
        # ["_regime"]. Use them directly instead of re-running
        # add_regime_indicators per call (~3.5ms x N bars).
        pre_regime = (existing.get("_extra") or {}).get("_regime")
        if pre_regime:
            extra.update(pre_regime)
        elif len(df) > 50:
            try:
                regime_df = add_regime_indicators(df)
                last = regime_df.tail(1)
                extra["vol_regime"] = last["vol_regime"].item()
                extra["trend_regime"] = last["trend_regime"].item()
                extra["trend_dir"] = last["trend_dir"].item()
                extra["atr"] = float(last["atr"].item()) if last["atr"].item() else None
                extra["adx"] = float(last["adx"].item()) if last["adx"].item() else None
                extra["atr_pctl"] = float(last["atr_pctl"].item()) if last["atr_pctl"].item() else None
            except Exception as e:
                logger.warning(f"Regime detection failed: {e}")

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
        """Rule-based fallback khi LLM không available — multi-factor regime-aware:
        RSI + MA crossover + Bollinger Bands + Regime filter (ADX, ATR percentile)."""
        rsi = ind.get("rsi")
        ma_fast = ind.get("ma_20")
        ma_slow = ind.get("ma_50")
        price = context.current_price
        bb_upper = ind.get("bb_upper")
        bb_lower = ind.get("bb_lower")

        # Regime info
        extra = ind.get("_extra", {})
        vol_regime = extra.get("vol_regime", "mid_vol")
        trend_regime = extra.get("trend_regime", "ranging")
        trend_dir = extra.get("trend_dir", "up")
        adx = extra.get("adx")
        atr_pctl = extra.get("atr_pctl")

        score = 0.0  # > 0 bullish, < 0 bearish
        factors = 0
        reasons = []

        # 1. RSI
        if isinstance(rsi, (int, float)):
            if rsi < 30:
                score += 1
                factors += 1
                reasons.append(f"RSI {rsi:.1f} oversold — potential bounce")
            elif rsi > 70:
                score -= 1
                factors += 1
                reasons.append(f"RSI {rsi:.1f} overbought — potential pullback")

        # 2. MA crossover (trend filter)
        if ma_fast and ma_slow:
            if ma_fast > ma_slow:
                score += 1
                factors += 1
                reasons.append(f"MA20 {ma_fast:.0f} > MA50 {ma_slow:.0f} — uptrend")
            else:
                score -= 1
                factors += 1
                reasons.append(f"MA20 {ma_fast:.0f} < MA50 {ma_slow:.0f} — downtrend")

        # 3. Bollinger Bands (mean reversion)
        if price and bb_upper and bb_lower:
            if price < bb_lower:
                score += 1
                factors += 1
                reasons.append("Price below lower band — oversold")
            elif price > bb_upper:
                score -= 1
                factors += 1
                reasons.append("Price above upper band — overbought")

        # 4. REGIME-AWARE ADJUSTMENTS
        # In trending regime: favor trend-following, discount mean-reversion
        if trend_regime == "trending" and adx and adx > 25:
            # Boost MA trend signal weight
            if ma_fast and ma_slow and ((ma_fast > ma_slow and trend_dir == "up") or (ma_fast < ma_slow and trend_dir == "down")):
                score += 0.5
                reasons.append(f"Strong trend (ADX {adx:.1f}, dir={trend_dir}) — trend signal boosted")
            # Reduce mean-reversion (RSI/BB) weight
            if isinstance(rsi, (int, float)):
                if rsi < 30 and trend_dir == "down":
                    score -= 0.5
                    reasons.append("Oversold RSI in downtrend — mean reversion discounted")
                elif rsi > 70 and trend_dir == "up":
                    score += 0.5
                    reasons.append("Overbought RSI in uptrend — continuation likely")

        # In ranging regime: favor mean-reversion, discount trend
        elif trend_regime == "ranging":
            # RSI/BB mean reversion more reliable
            if isinstance(rsi, (int, float)):
                if rsi < 35 or rsi > 65:
                    reasons.append("Ranging market — RSI mean reversion favored")
            # MA crossover less reliable (whipsaws)
            if ma_fast and ma_slow:
                reasons.append("Ranging market — MA crossover discounted (whipsaw risk)")

        # Volatility regime adjustments
        if vol_regime == "high_vol":
            # Wider stops needed, reduce position conviction
            score *= 0.7
            reasons.append(f"High vol regime (ATR pctl {atr_pctl:.0%}) — conviction reduced" if atr_pctl else "High vol regime — conviction reduced")
        elif vol_regime == "low_vol":
            # Tighter stops, potential breakout
            reasons.append(f"Low vol regime (ATR pctl {atr_pctl:.0%}) — breakout watch" if atr_pctl else "Low vol regime — breakout watch")

        # Trend gate: KHÔNG BUY khi MA20 < MA50 (downtrend) — tránh bắt dao rơi.
        # Mean-reversion (RSI/BB) không được lấn át trend đã đảo chiều.
        trend_down = isinstance(ma_fast, (int, float)) and isinstance(ma_slow, (int, float)) and ma_fast < ma_slow
        if trend_down and score >= 1:
            score = 0
            reasons.append("Downtrend (MA20<MA50) — contrarian BUY blocked by trend filter")

        if score >= 1:
            signal = "BUY"
            confidence = min(0.45 + 0.08 * factors, 0.65)
            trend = "bullish" if score > 1 else "potential reversal"
        elif score <= -1:
            signal = "SELL"
            confidence = min(0.45 + 0.08 * factors, 0.65)
            trend = "bearish" if score < -1 else "potential reversal"
        else:
            signal = "HOLD"
            confidence = 0.3
            trend = "mixed"
            reasons.append("Signals mixed/neutral — wait for confluence")

        return AgentMessage(
            role="technical_analyst",
            signal=signal,
            confidence=confidence,
            reasoning=" | ".join(reasons) if reasons else "No indicator data",
            details={
                "trend": trend,
                "momentum": "bullish" if score > 0 else "bearish" if score < 0 else "neutral",
                "regime": {
                    "vol_regime": vol_regime,
                    "trend_regime": trend_regime,
                    "trend_dir": trend_dir,
                    "adx": adx,
                    "atr_pctl": atr_pctl,
                },
            },
        )
