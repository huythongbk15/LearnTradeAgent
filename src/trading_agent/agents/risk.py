"""
Risk Manager Agent — đánh giá rủi ro, position sizing, warnings.

Không có vị thế thực (Phase 2) nên đánh giá rủi ro dựa trên volatility + drawdown.
"""

from __future__ import annotations

import logging
import numpy as np

from trading_agent.agents.base import AgentMessage, AnalysisContext, BaseAgent
from trading_agent.agents.llm import ask_agent, llm_enabled

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

        # LLM disabled → rule-based ngay, không build prompt
        if not llm_enabled():
            return self._rule_based(ind, context)

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
            prompt_lines.append(
                f"Volume ratio (5/20): {extra['volume_ratio_5_20']:.2f}x"
            )

        if "rsi" in ind:
            rsi = ind["rsi"]
            prompt_lines.append(f"RSI(14): {rsi:.1f}")

        # Price changes
        for label, key in [
            ("1d", "price_change_1d"),
            ("1w", "price_change_1w"),
            ("1m", "price_change_1m"),
        ]:
            val = getattr(context, key, None)
            if val is not None:
                prompt_lines.append(f"Change {label}: {val:+.2f}%")

        prompt_lines.append("")

        if context.current_position_pct == 0:
            prompt_lines.append(
                "Assess the risk level for opening a new long position "
                "and suggest a safe position size."
            )
        else:
            prompt_lines.append(
                "Assess the risk level for holding the current position "
                "and advise on position size adjustment."
            )

        prompt = "\n".join(prompt_lines)

        try:
            result = ask_agent(SYSTEM_PROMPT, prompt, schema="risk")
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

    def _compute_volatility(self, context: AnalysisContext) -> float:
        """Compute realized volatility from raw OHLCV (no dependency on pre-computed context)."""
        df = context.ohlcv
        if df is None or len(df) < 20:
            return 5.0  # Default moderate volatility

        closes = df["close"].to_numpy()
        if len(closes) < 20:
            return 5.0

        # Normalize per-bar volatility to a 24-hour volatility so thresholds
        # remain comparable across 15m/1h/4h/daily inputs.
        returns = np.diff(closes[-21:]) / closes[-21:-1]
        timeframe_minutes = self._timeframe_minutes(context.timeframe)
        bars_per_day = max(1.0, 24 * 60 / timeframe_minutes)
        daily_vol = float(np.std(returns) * np.sqrt(bars_per_day) * 100)
        return max(daily_vol, 0.5)  # Floor at 0.5%

    @staticmethod
    def _timeframe_minutes(timeframe: str) -> int:
        tf = timeframe.lower().strip()
        units = {"m": 1, "h": 60, "d": 1440, "w": 10080}
        if len(tf) < 2 or tf[-1] not in units:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        try:
            amount = int(tf[:-1])
        except ValueError as exc:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}") from exc
        if amount <= 0:
            raise ValueError(f"Unsupported timeframe: {timeframe!r}")
        return amount * units[tf[-1]]

    def _rule_based(self, ind: dict, context: AnalysisContext) -> AgentMessage:
        """Rule-based risk assessment with volatility-scaled position sizing."""
        extra = ind.get("_extra", {})
        vol = self._compute_volatility(context)
        vol_ratio = extra.get("volume_ratio_5_20", 1.0)

        # ── Position sizing theo volatility ──────────────────────────────
        # Công thức liên tục, hai ràng buộc:
        #   1) Risk-based: mỗi lệnh chỉ rủi ro ~1.5% equity
        #      size = risk_per_trade / stop_distance
        #   2) Vol-cap: vol càng cao → size càng nhỏ (bất đối xứng với rủi ro)
        RISK_PER_TRADE = 0.015
        if vol is not None and vol > 0:
            # Stop distance giãn theo vol (3-8%) → rủi ro thực tế được chuẩn hoá
            stop_pct = max(0.03, min(0.08, vol / 100.0))
            risk_based = RISK_PER_TRADE / stop_pct
            # Vol cap liên tục: vol 1.5 → 0.40, vol 3.0 → 0.20, vol 6.0 → 0.10
            vol_cap = 0.40 * min(1.0, 1.5 / vol)
            max_pos = max(0.05, min(risk_based, vol_cap))
            if vol > 3.0:
                risk = "HIGH"
                max_pos = 0.0  # HIGH risk: symmetric — reduce to 0 both entry AND exit
                reason = f"High volatility ({vol:.1f}%) — position size REDUCED TO 0%"
            elif vol > 1.5:
                risk = "MEDIUM"
                reason = f"Moderate volatility ({vol:.1f}%) — size {max_pos * 100:.0f}% of equity"
            else:
                risk = "LOW"
                reason = (
                    f"Low volatility ({vol:.1f}%) — size {max_pos * 100:.0f}% of equity"
                )
        else:
            risk = "MEDIUM"
            max_pos = 0.25
            reason = "No volatility data — using conservative sizing"

        # Adjust for volume
        if vol_ratio < 0.5:
            risk = "HIGH" if risk == "MEDIUM" else risk
            max_pos = 0.0 if risk == "HIGH" else max_pos * 0.5
            reason += "; low volume — reduce further"

        # Risk agent chỉ vote hướng khi rõ ràng:
        #   LOW  → BUY (cho phép vào lệnh)
        #   MEDIUM → HOLD (trung lập, không bias weighted vote)
        #   HIGH → SELL nếu đang giữ vị thế (thoát), HOLD nếu đang đứng ngoài
        #          + risk_level HIGH (trader override vẫn chặn lệnh mua mới)
        in_position = (context.current_position_pct or 0.0) > 0.001
        signal = "HOLD"
        if risk == "LOW":
            signal = "BUY"
        elif risk == "HIGH":
            signal = "SELL" if in_position else "HOLD"

        return AgentMessage(
            role="risk_manager",
            signal=signal,
            confidence=0.5,
            reasoning=reason,
            details={
                "risk_level": risk,
                "max_position_size_pct": max_pos,
                "key_risks": [
                    f"Volatility at {vol:.1f}%" if vol else "Unknown volatility"
                ],
            },
            max_position_size_pct=max_pos,
            risk_level=risk,
            warnings=[f"Position size capped at {max_pos * 100:.0f}%"],
        )
