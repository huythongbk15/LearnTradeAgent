"""
Trader Agent — tổng hợp tín hiệu từ các agent, ra quyết định cuối cùng.

Weighted voting system:
- Technical Analyst: 40%
- Sentiment Analyst: 20%
- Risk Manager: 40% (risk override)

Nếu Risk Manager nói HIGH/EXTREME → signal bị override thành HOLD.
"""

from __future__ import annotations

import logging

from trading_agent.agents.base import AgentMessage, AnalysisContext, BaseAgent

logger = logging.getLogger(__name__)


# Weight của mỗi agent trong quyết định cuối
AGENT_WEIGHTS = {
    "technical_analyst": 0.40,
    "sentiment_analyst": 0.20,
    "risk_manager": 0.40,
}

SIGNAL_MAP = {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0}
SIGNAL_INV = {1.0: "BUY", -1.0: "SELL", 0.0: "HOLD"}


class Trader(BaseAgent):
    """Final decision agent — synthesizes all agent signals.

    Uses a weighted voting system:
    1. Collect signals from all agents
    2. Weight by AGENT_WEIGHTS
    3. Apply risk override (HIGH/EXTREME → HOLD)
    4. Return final signal + position size
    """

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        messages = context.agent_messages
        if not messages:
            return self._empty_result("No agent messages to analyze")

        # Weighted voting
        weighted_sum = 0.0
        total_weight = 0.0
        confidences: list[float] = []
        all_reasoning: list[str] = []

        risk_override = False
        risk_level = "LOW"
        max_pos = 0.25

        for msg in messages:
            weight = AGENT_WEIGHTS.get(msg.role, 0.2)
            signal_val = SIGNAL_MAP.get(msg.signal, 0.0)

            weighted_sum += signal_val * weight * msg.confidence
            total_weight += weight
            confidences.append(msg.confidence)
            all_reasoning.append(f"[{msg.role}] {msg.reasoning}")

            # Check for risk override
            if msg.role == "risk_manager" and msg.risk_level in ("HIGH", "EXTREME"):
                risk_override = True
                risk_level = msg.risk_level
                if msg.max_position_size_pct is not None:
                    max_pos = msg.max_position_size_pct

        if total_weight > 0:
            final_score = weighted_sum / total_weight
        else:
            final_score = 0.0

        # Risk override: HIGH/EXTREME risk
        # - Đang giữ vị thế → SELL (thoát ngay, bảo toàn vốn)
        # - Đứng ngoài → HOLD (không mở lệnh mới trong rủi ro cao)
        in_position = (context.current_position_pct or 0.0) > 0.001
        if risk_override:
            final_signal = "SELL" if in_position else "HOLD"
            final_conf = min(0.6, sum(confidences) / len(confidences) if confidences else 0.5)
            reasoning_parts = [
                f"[RISK OVERRIDE] Risk level: {risk_level} "
                f"{'(EXIT POSITION)' if in_position else '(NO NEW ENTRIES)'}",
                *all_reasoning,
            ]
        else:
            # Map score to signal
            if final_score > 0.2:
                final_signal = "BUY"
            elif final_score < -0.2:
                final_signal = "SELL"
            else:
                final_signal = "HOLD"

            final_conf = min(
                abs(final_score),
                sum(confidences) / len(confidences) if confidences else 0.5,
            )
            reasoning_parts = all_reasoning

        return AgentMessage(
            role="trader",
            signal=final_signal,
            confidence=final_conf,
            reasoning="\n".join(reasoning_parts),
            details={
                "weighted_score": round(final_score, 3),
                "risk_level": risk_level,
                "max_position_size_pct": max_pos,
                "agent_signals": [
                    {"role": m.role, "signal": m.signal, "confidence": m.confidence}
                    for m in messages
                ],
            },
            max_position_size_pct=max_pos,
            risk_level=risk_level,
            warnings=[f"Risk level: {risk_level}"] if risk_override else [],
        )

    def _empty_result(self, reason: str) -> AgentMessage:
        return AgentMessage(
            role="trader",
            signal="HOLD",
            confidence=0.2,
            reasoning=reason,
            details={},
        )
