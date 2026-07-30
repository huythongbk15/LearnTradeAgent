"""
Risk Manager Agent — đánh giá rủi ro, position sizing, warnings.

Không có vị thế thực (Phase 2) nên đánh giá rủi ro dựa trên volatility + drawdown.
"""

from __future__ import annotations

import logging

from trading_agent.agents.base import AgentMessage, AnalysisContext, BaseAgent
from trading_agent.agents.llm import ask_agent

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a Risk Manager in a multi-agent trading system.

Assess risk based on market conditions and current exposure.
Output JSON:
{
  "signal": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0-1.0,
  "reasoning": "1-2 sentence explanation",
  "details": {
    "risk_level": "LOW"|"MEDIUM"|"HIGH"|"EXTREME",
    "max_position_size_pct": 0.0-1.0,
    "key_risks": ["risk1", "risk2"]
  }
}

Guidelines:
- High volatility = smaller position sizes
- Strong trend = can increase size
- Low volume breakouts = reduce size
- Never suggest >50% position in high volatility
- Default to HOLD and 0% if risk is extreme
- Keep it conservative — preserve capital first"""


class RiskManager(BaseAgent):
    """Assesses risk and suggests position sizing."""

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        ind = context.indicators
        extra = ind.get("_extra", {})
        price = context.current_price

        prompt_lines = [
            f"Symbol: {context.symbol} ({context.timeframe})",
            f"Current Price: ${price:.2f}",
            f"Current Position: {context.current_position_pct * 100:.0f}%",
            f"Portfolio Value: ${context.portfolio_value:,.2f}",
            "",
            "--- Risk Indicators ---",
        ]

        if extra.get("volatility_20"):
            prompt_lines.append(f"20-bar volatility: {extra['volatility_20']:.2f}%")
        if extra.get("volume_ratio_5_20"):
            prompt_lines.append(f"Volume ratio (5/20): {extra['volume_ratio_5_20']:.2f}x")

        if "rsi" in ind:
            rsi = ind["rsi"]
            prompt_lines.append(f"RSI(14): {rsi:.1f}")

        # Price changes
        for label, key in [("1d", "price_change_1d"),
                           ("1w", "price_change_1w"),
                           ("1m", "price_change_1m")]:
            val = getattr(context, key, None)
            if val is not None:
                prompt_lines.append(f"Change {label}: {val:+.2f}%")

        prompt_lines.append("")

        if context.current_position_pct == 0:
            prompt_lines.append("Assess the risk level for opening a new long position "
                                "and suggest a safe position size.")
        else:
            prompt_lines.append("Assess the risk level for holding the current position "
                                "and advise on position size adjustment.")

        prompt = "\n".join(prompt_lines)

        try:
            result = ask_agent(SYSTEM_PROMPT, prompt)
            details = result.get("details", {})
            max_pos = details.get("max_position_size_pct")
            if max_pos is not None:
                try:
                    max_pos = float(max_pos)
                except (ValueError, TypeError):
                    max_pos = None

            msg = AgentMessage(
                role="risk_manager",
                signal=result.get("signal", "HOLD"),
                confidence=float(result.get("confidence", 0.5)),
                reasoning=result.get("reasoning", ""),
                details={
                    "risk_level": details.get("risk_level", "MEDIUM"),
                    "max_position_size_pct": max_pos,
                    "key_risks": details.get("key_risks", []),
                },
                max_position_size_pct=max_pos,
                risk_level=details.get("risk_level", "MEDIUM"),
                warnings=details.get("key_risks", []),
            )
        except Exception as e:
            logger.warning(f"Risk LLM failed ({e}), using rule-based")
            msg = self._rule_based(ind, context)

        return msg

    def _rule_based(self, ind: dict, context: AnalysisContext) -> AgentMessage:
        """Rule-based risk assessment."""
        extra = ind.get("_extra", {})
        vol = extra.get("volatility_20", None)
        vol_ratio = extra.get("volume_ratio_5_20", 1.0)

        # Determine risk level
        if vol is not None:
            if vol > 3.0:
                risk = "HIGH"
                max_pos = 0.15
                reason = f"High volatility ({vol:.1f}%) — reduce position size"
            elif vol > 1.5:
                risk = "MEDIUM"
                max_pos = 0.30
                reason = f"Moderate volatility ({vol:.1f}%) — standard sizing"
            else:
                risk = "LOW"
                max_pos = 0.40
                reason = f"Low volatility ({vol:.1f}%) — can increase size"
        else:
            risk = "MEDIUM"
            max_pos = 0.25
            reason = "No volatility data — using conservative sizing"

        # Adjust for volume
        if vol_ratio < 0.5:
            risk = "HIGH" if risk == "MEDIUM" else risk
            max_pos *= 0.5
            reason += "; low volume — reduce further"

        signal = "HOLD" if risk in ("HIGH", "EXTREME") else "BUY"

        return AgentMessage(
            role="risk_manager",
            signal=signal,
            confidence=0.5,
            reasoning=reason,
            details={
                "risk_level": risk,
                "max_position_size_pct": max_pos,
                "key_risks": [f"Volatility at {vol:.1f}%" if vol else "Unknown volatility"],
            },
            max_position_size_pct=max_pos,
            risk_level=risk,
            warnings=[f"Position size capped at {max_pos * 100:.0f}%"],
        )
