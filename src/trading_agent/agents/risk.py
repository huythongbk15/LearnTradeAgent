"""
Risk Manager Agent — đánh giá rủi ro, position sizing, warnings.

Không có vị thế thực (Phase 2) nên đánh giá rủi ro dựa trên volatility + drawdown.

P0.2 (STR-0212): LLM RiskManager thay thế bằng ForecastRiskPolicy.
Toàn bộ LLM call (`ask_agent`) đã được loại bỏ. Risk quyết định dựa trên
công thức toán học: realized volatility + volume ratio + current drawdown.
"""

from __future__ import annotations

import logging

import numpy as np

from trading_agent.agents.base import AgentMessage, AnalysisContext, BaseAgent
from trading_agent.agents.risk_decision import RiskDecision, RiskLevel

logger = logging.getLogger(__name__)


class ForecastRiskPolicy(BaseAgent):
    """Deterministic, volatility-scaled risk policy (STR-0212).

    Replaces the LLM-based RiskManager. No external API calls — risk
    is computed entirely from realized volatility, volume ratio, and
    current drawdown. Designed to be fast, reproducible, and auditable.

    Decision model:
        vol < 1.5%   → LOW risk,   max_pos = risk_based ∩ vol_cap
        vol 1.5–3%   → MEDIUM risk, same sizing
        vol > 3%    → HIGH risk,  max_pos = 0 (market too volatile)

    Position sizing:
        risk_based = RISK_PER_TRADE / stop_distance
        vol_cap    = 0.40 * min(1.0, 1.5 / vol)   (asymmetric decay)
        max_pos    = max(0.05, min(risk_based, vol_cap))
    """

    RISK_PER_TRADE: float = 0.015  # 1.5% equity at risk per trade
    VOL_HIGH_THRESHOLD: float = 3.0   # % daily vol → HIGH
    VOL_MED_THRESHOLD: float = 1.5   # % daily vol → MEDIUM
    VOL_CAP_BASE: float = 0.40       # Base cap at vol=1.5%
    STOP_PCT_MIN: float = 0.03       # 3% minimum stop distance
    STOP_PCT_MAX: float = 0.08       # 8% maximum stop distance

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        """Compute risk policy for the current context → AgentMessage."""
        decision = self._evaluate(context)
        return self._decision_to_message(decision, context)

    # ── Core policy ──────────────────────────────────────────────────

    def _evaluate(self, context: AnalysisContext) -> RiskDecision:
        """Evaluate risk and produce a typed RiskDecision."""
        ind = getattr(context, "indicators", {})
        extra = ind.get("_extra", {}) if isinstance(ind, dict) else {}
        vol = self._compute_volatility(context)
        vol_ratio = extra.get("volume_ratio_5_20", 1.0)

        # ── Volatility-based position sizing ──
        if vol is not None and vol > 0:
            stop_pct = max(self.STOP_PCT_MIN, min(self.STOP_PCT_MAX, vol / 100.0))
            risk_based = self.RISK_PER_TRADE / stop_pct
            vol_cap = self.VOL_CAP_BASE * min(1.0, self.VOL_MED_THRESHOLD / vol)
            max_pos = max(0.05, min(risk_based, vol_cap))

            if vol > self.VOL_HIGH_THRESHOLD:
                risk = RiskLevel.HIGH
                max_pos = 0.0
                reason = f"HIGH vol ({vol:.1f}%) — position REDUCED TO 0%"
            elif vol > self.VOL_MED_THRESHOLD:
                risk = RiskLevel.MEDIUM
                reason = f"MEDIUM vol ({vol:.1f}%) — size {max_pos * 100:.0f}%"
            else:
                risk = RiskLevel.LOW
                reason = f"LOW vol ({vol:.1f}%) — size {max_pos * 100:.0f}%"
        else:
            risk = RiskLevel.MEDIUM
            max_pos = 0.25
            reason = "No vol data — conservative sizing"

        reduce_only = risk in (RiskLevel.HIGH, RiskLevel.EXTREME)

        # Volume adjustment
        if vol_ratio < 0.5:
            if risk == RiskLevel.MEDIUM:
                risk = RiskLevel.HIGH
                max_pos = 0.0
                reduce_only = True
            else:
                max_pos = max_pos * 0.5
            reason += "; low volume — reduce further"

        return RiskDecision(
            risk_level=risk,
            target_exposure_pct=0.0 if reduce_only else max_pos,
            max_new_exposure_pct=0.0 if reduce_only else max_pos,
            reduce_only=reduce_only,
            warnings=(
                f"Position size capped at {max_pos * 100:.0f}%",
                f"Volatility at {vol:.1f}%" if vol else "Unknown volatility",
            ),
        )

    # ── Query interface ──────────────────────────────────────────────

    def should_open_position(
        self, context: AnalysisContext
    ) -> tuple[bool, float, str]:
        """Return (should_open, max_exposure_pct, reason).

        Used by the Trader agent to gate new position entries.
        """
        decision = self._evaluate(context)
        if decision.risk_level == RiskLevel.LOW:
            return True, decision.target_exposure_pct, "LOW risk — open allowed"
        elif decision.risk_level == RiskLevel.MEDIUM:
            return False, 0.0, "MEDIUM risk — neutral, no new exposure"
        else:
            return False, 0.0, f"{decision.risk_level} risk — HOLD/SELL only"

    def should_reduce_position(
        self, context: AnalysisContext
    ) -> tuple[bool, float, str]:
        """Return (should_reduce, reduction_pct, reason).

        Used by the Trader agent to gate position exits.
        """
        decision = self._evaluate(context)
        if decision.risk_level == RiskLevel.HIGH:
            return True, 1.0, "HIGH risk — exit full position"
        elif decision.risk_level == RiskLevel.EXTREME:
            return True, 1.0, "EXTREME risk — emergency exit"
        else:
            return False, 0.0, "Risk level acceptable — hold"

    def position_size_pct(
        self, context: AnalysisContext
    ) -> tuple[float, str, list[str]]:
        """Return (max_position_pct, risk_level_str, warnings).

        Direct position sizing without trading signal bias.
        """
        decision = self._evaluate(context)
        return (
            decision.target_exposure_pct,
            decision.risk_level.value,
            list(decision.warnings),
        )

    # ── Helpers ──────────────────────────────────────────────────────

    def _decision_to_message(
        self,
        decision: RiskDecision,
        context: AnalysisContext,
        reasoning: str = "",
    ) -> AgentMessage:
        """Convert a RiskDecision into the legacy AgentMessage protocol."""
        in_position = (context.current_position_pct or 0.0) > 0.001
        if decision.risk_level == RiskLevel.HIGH:
            signal = "SELL" if in_position else "HOLD"
        elif decision.risk_level == RiskLevel.EXTREME:
            signal = "SELL" if in_position else "HOLD"
        elif decision.risk_level == RiskLevel.LOW:
            signal = "BUY"
        else:
            signal = "HOLD"
        return AgentMessage(
            role="risk_manager",
            signal=signal,
            confidence=0.9 if decision.risk_level != RiskLevel.MEDIUM else 0.5,
            reasoning=reasoning or "ForecastRiskPolicy deterministic assessment",
            details={
                "risk_level": decision.risk_level.value,
                "target_exposure_pct": decision.target_exposure_pct,
                "max_new_exposure_pct": decision.max_new_exposure_pct,
                "reduce_only": decision.reduce_only,
                "key_risks": list(decision.warnings),
            },
            max_position_size_pct=decision.target_exposure_pct,
            risk_level=decision.risk_level.value,
            warnings=list(decision.warnings),
        )

    def _compute_volatility(self, context: AnalysisContext) -> float:
        """Compute realized volatility from raw OHLCV.

        Normalize per-bar volatility to daily units so thresholds remain
        comparable across 15m/1h/4h/daily inputs. Falls back to pre-computed
        ``indicators._extra.volatility_20`` if OHLCV is unavailable.
        """
        df = getattr(context, "ohlcv", None)
        if df is None or len(df) < 20:
            ind = getattr(context, "indicators", {})
            if isinstance(ind, dict):
                vol_20 = ind.get("_extra", {}).get("volatility_20")
                if vol_20 is not None:
                    return float(vol_20)
            return 5.0  # Default moderate volatility

        closes = df["close"].to_numpy()
        if len(closes) < 20:
            return 5.0

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


class RiskManager(ForecastRiskPolicy):
    """Backward-compatible alias for ForecastRiskPolicy (STR-0212).

    All Orchestrator references ``RiskManager`` are transparently redirected
    to ``ForecastRiskPolicy``. New code should import ForecastRiskPolicy directly.
    The LLM-based analysis path has been permanently removed.
    """

    def analyze(self, context: AnalysisContext) -> AgentMessage:
        return super().analyze(context)


__all__ = ["ForecastRiskPolicy", "RiskManager", "RiskDecision", "RiskLevel"]
