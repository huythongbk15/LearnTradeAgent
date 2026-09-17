"""Specialized agents for the swarm."""

import logging
import uuid
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from trading_agent.agents.base import AgentConfig, AgentMessage, AnalysisContext
from trading_agent.agents.base import BaseAgent as Agent
from trading_agent.agents.risk import ForecastRiskPolicy
from trading_agent.llm.client import LLMClient
from trading_agent.llm.pool import LLMPool

# LLM backend: LLMClient (đơn) hoặc LLMPool (multi-provider failover) — cùng interface chat()
LLMBackend = LLMClient | LLMPool

logger = logging.getLogger(__name__)


def make_signal(
    signal_id: str,
    symbol: str,
    action: str,
    confidence: float,
    size_pct: float,
    reasoning: str,
    metadata: dict[str, Any] | None = None,
) -> AgentMessage:
    """Create an ``AgentMessage`` from legacy ``AgentSignal`` kwargs.

    Since P1 protocol unification, ``AgentSignal == AgentMessage``.  This
    factory accepts the old-style kwargs (``action``, ``size_pct``,
    ``signal_id``, ``metadata``) and maps them to the unified
    ``AgentMessage`` fields so migration of call sites is mechanical.
    """
    meta = dict(metadata or {})
    meta["signal_id"] = signal_id
    return AgentMessage(
        role="agent",
        symbol=symbol,
        signal=str(action).upper(),
        confidence=confidence,
        reasoning=reasoning,
        details=meta,
        max_position_size_pct=size_pct,
    )


class AgentRole(str, Enum):
    """Agent roles in the swarm."""

    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    SENTIMENT = "sentiment"
    RISK = "risk"
    EXECUTION = "execution"
    COORDINATOR = "coordinator"


@dataclass
class AgentSpec:
    """Specification for a swarm agent."""

    role: AgentRole
    name: str
    config: AgentConfig
    symbols: list[str]
    timeframes: list[str]
    weight: float = 1.0
    enabled: bool = True


class SpecializedAgent(Agent):
    """Base class for specialized swarm agents."""

    def __init__(
        self,
        spec: AgentSpec,
        llm_client: Optional[LLMBackend] = None,
    ):
        super().__init__(spec.config)
        self.spec = spec
        self.llm = llm_client
        self.role = spec.role
        self.last_signal: Optional[AgentMessage] = None
        self.performance_history: list[dict] = []

    @abstractmethod
    async def analyze(self, market_data: dict[str, Any]) -> AgentMessage:
        """Analyze market data and produce signal."""
        pass

    async def process(self, market_data: dict[str, Any]) -> AgentMessage:
        """Process market data (interface for coordinator)."""
        signal = await self.analyze(market_data)
        self.last_signal = signal

        # Track performance
        self.performance_history.append(
            {
                "timestamp": datetime.utcnow(),
                "signal": signal.signal.lower(),
                "confidence": signal.confidence,
                "reasoning": signal.reasoning,
            }
        )

        # Keep last 100
        if len(self.performance_history) > 100:
            self.performance_history = self.performance_history[-100:]

        return signal

    def get_performance(self) -> dict:
        """Get agent performance metrics."""
        if not self.performance_history:
            return {"signals": 0, "avg_confidence": 0}

        recent = self.performance_history[-20:]
        return {
            "signals": len(self.performance_history),
            "avg_confidence": sum(s["confidence"] for s in recent) / len(recent),
            "recent_actions": [s["signal"] for s in recent],
        }


class TechnicalAgent(SpecializedAgent):
    """Technical analysis agent - price action, indicators, patterns."""

    SYSTEM_PROMPT = """You are a technical analysis expert. Analyze price data and indicators to generate trading signals.

Consider: trend (EMAs, ADX), momentum (RSI, MACD), volatility (Bollinger, ATR), volume, support/resistance, chart patterns.

Output JSON:
{
  "action": "buy|sell|hold|close_long|close_short",
  "confidence": 0.0-1.0,
  "reasoning": "concise explanation",
  "key_levels": {"support": [], "resistance": []},
  "indicators": {"rsi": 0, "macd": 0, "trend": "up|down|sideways"},
  "time_horizon": "intraday|swing|position",
  "risk_reward": 2.5
}"""

    async def analyze(self, market_data: dict[str, Any]) -> AgentMessage:
        symbol = market_data.get(
            "symbol", self.spec.symbols[0] if self.spec.symbols else "UNKNOWN"
        )

        # Build context from market data
        context = self._build_context(market_data)

        if self.llm:
            # Use LLM for analysis
            response = await self.llm.chat(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=0.2,
                max_tokens=500,
            )

            signal = self._parse_llm_response(response, symbol)
        else:
            # Fallback: rule-based
            signal = self._rule_based_analysis(market_data, symbol)

        signal.metadata["agent_role"] = self.role.value
        signal.metadata["agent_name"] = self.spec.name
        return signal

    def _build_context(self, data: dict) -> str:
        parts = [f"Symbol: {data.get('symbol', 'N/A')}"]

        if "candles" in data:
            candles = data["candles"]
            if len(candles) > 0:
                last = candles[-1]
                parts.append(f"Last Close: {last.get('close', 'N/A')}")
                parts.append(f"Volume: {last.get('volume', 'N/A')}")

        if "indicators" in data:
            ind = data["indicators"]
            parts.append(f"RSI: {ind.get('rsi', 'N/A')}")
            parts.append(f"MACD: {ind.get('macd', 'N/A')}")
            parts.append(f"EMA Fast: {ind.get('ema_fast', 'N/A')}")
            parts.append(f"EMA Slow: {ind.get('ema_slow', 'N/A')}")
            parts.append(f"BB Upper: {ind.get('bb_upper', 'N/A')}")
            parts.append(f"BB Lower: {ind.get('bb_lower', 'N/A')}")
            parts.append(f"ATR: {ind.get('atr', 'N/A')}")
            parts.append(f"ADX: {ind.get('adx', 'N/A')}")

        if "patterns" in data:
            parts.append(f"Patterns: {data['patterns']}")

        if "support_resistance" in data:
            sr = data["support_resistance"]
            parts.append(f"Support: {sr.get('support', [])}")
            parts.append(f"Resistance: {sr.get('resistance', [])}")

        return "\n".join(parts)

    def _parse_llm_response(self, response: str, symbol: str) -> AgentMessage:
        import json
        import re

        try:
            # Extract JSON
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found")
        except Exception:
            data = {}

        action = data.get("action", "hold")
        confidence = float(data.get("confidence", 0.5))

        return make_signal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=data.get("size_pct", 0.02),
            reasoning=data.get("reasoning", "Technical analysis"),
            metadata={
                "key_levels": data.get("key_levels", {}),
                "indicators": data.get("indicators", {}),
                "time_horizon": data.get("time_horizon", "swing"),
                "risk_reward": data.get("risk_reward", 2.0),
            },
        )

    def _rule_based_analysis(self, data: dict, symbol: str) -> AgentMessage:
        """Fallback rule-based analysis."""
        ind = data.get("indicators", {})
        rsi = ind.get("rsi", 50)
        macd = ind.get("macd", 0)
        macd_signal = ind.get("macd_signal", 0)
        ema_fast = ind.get("ema_fast", 0)
        ema_slow = ind.get("ema_slow", 0)

        # Simple rules
        bullish = 0
        bearish = 0

        if rsi < 30:
            bullish += 1
        elif rsi > 70:
            bearish += 1

        if macd > macd_signal:
            bullish += 1
        else:
            bearish += 1

        if ema_fast > ema_slow:
            bullish += 1
        else:
            bearish += 1

        if bullish > bearish:
            action = "buy"
            confidence = min(0.5 + (bullish - bearish) * 0.15, 0.85)
        elif bearish > bullish:
            action = "sell"
            confidence = min(0.5 + (bearish - bullish) * 0.15, 0.85)
        else:
            action = "hold"
            confidence = 0.5

        return make_signal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=0.02,
            reasoning=f"Rule-based: bullish={bullish}, bearish={bearish}",
            metadata={"indicators": ind},
        )


class FundamentalAgent(SpecializedAgent):
    """Fundamental analysis agent - earnings, financials, valuation."""

    SYSTEM_PROMPT = """You are a fundamental equity analyst. Analyze financial data, earnings, and valuation to generate trading signals.

Consider: earnings growth, revenue growth, margins, guidance, valuation (P/E, P/S, EV/EBITDA), balance sheet, cash flow, competitive position.

Output JSON:
{
  "action": "buy|sell|hold|close_long|close_short",
  "confidence": 0.0-1.0,
  "reasoning": "concise explanation",
  "fair_value": 150.0,
  "upside_pct": 25.0,
  "key_metrics": {"pe": 20, "growth": 0.15, "margin": 0.25},
  "catalyst": "earnings|guidance|product|macro",
  "time_horizon": "swing|position|long_term",
  "risk_factors": ["competition", "regulation"]
}"""

    async def analyze(self, market_data: dict[str, Any]) -> AgentMessage:
        symbol = market_data.get(
            "symbol", self.spec.symbols[0] if self.spec.symbols else "UNKNOWN"
        )

        context = self._build_context(market_data)

        if self.llm:
            response = await self.llm.chat(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=0.2,
                max_tokens=500,
            )

            signal = self._parse_llm_response(response, symbol)
        else:
            signal = self._rule_based_analysis(market_data, symbol)

        signal.metadata["agent_role"] = self.role.value
        signal.metadata["agent_name"] = self.spec.name
        return signal

    def _build_context(self, data: dict) -> str:
        parts = [f"Symbol: {data.get('symbol', 'N/A')}"]

        if "fundamentals" in data:
            f = data["fundamentals"]
            parts.append(f"P/E: {f.get('pe', 'N/A')}")
            parts.append(f"Forward P/E: {f.get('forward_pe', 'N/A')}")
            parts.append(f"P/S: {f.get('ps', 'N/A')}")
            parts.append(f"Revenue Growth: {f.get('revenue_growth', 'N/A')}")
            parts.append(f"Earnings Growth: {f.get('earnings_growth', 'N/A')}")
            parts.append(f"Gross Margin: {f.get('gross_margin', 'N/A')}")
            parts.append(f"Operating Margin: {f.get('operating_margin', 'N/A')}")
            parts.append(f"ROE: {f.get('roe', 'N/A')}")
            parts.append(f"Debt/Equity: {f.get('debt_to_equity', 'N/A')}")
            parts.append(f"Free Cash Flow: {f.get('fcf', 'N/A')}")

        if "earnings" in data:
            e = data["earnings"]
            parts.append(f"Last EPS Surprise: {e.get('eps_surprise', 'N/A')}")
            parts.append(f"Guidance: {e.get('guidance', 'N/A')}")
            parts.append(f"Next Earnings: {e.get('next_date', 'N/A')}")

        if "analyst" in data:
            a = data["analyst"]
            parts.append(f"Analyst Rating: {a.get('rating', 'N/A')}")
            parts.append(f"Price Target: {a.get('price_target', 'N/A')}")
            parts.append(f"Upside: {a.get('upside', 'N/A')}%")

        return "\n".join(parts)

    def _parse_llm_response(self, response: str, symbol: str) -> AgentMessage:
        import json
        import re

        try:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found")
        except Exception:
            data = {}

        action = data.get("action", "hold")
        confidence = float(data.get("confidence", 0.5))

        return make_signal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=data.get("size_pct", 0.02),
            reasoning=data.get("reasoning", "Fundamental analysis"),
            metadata={
                "fair_value": data.get("fair_value"),
                "upside_pct": data.get("upside_pct"),
                "key_metrics": data.get("key_metrics", {}),
                "catalyst": data.get("catalyst"),
                "time_horizon": data.get("time_horizon", "position"),
                "risk_factors": data.get("risk_factors", []),
            },
        )

    def _rule_based_analysis(self, data: dict, symbol: str) -> AgentMessage:
        f = data.get("fundamentals", {})
        pe = f.get("pe", 20)
        growth = f.get("earnings_growth", 0.1)
        margin = f.get("operating_margin", 0.15)

        # Simple valuation
        peg = pe / (growth * 100) if growth > 0 else 999

        if peg < 1 and margin > 0.15:
            action = "buy"
            confidence = 0.7
        elif peg > 2 or margin < 0.05:
            action = "sell"
            confidence = 0.65
        else:
            action = "hold"
            confidence = 0.5

        return make_signal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=0.015,
            reasoning=f"PEG={peg:.1f}, Margin={margin:.1%}",
            metadata={"peg": peg, "margin": margin},
        )


class SentimentAgent(SpecializedAgent):
    """Sentiment analysis agent - news, social, options flow."""

    SYSTEM_PROMPT = """You are a market sentiment analyst. Analyze news sentiment, social media, options flow, and positioning to generate signals.

Consider: news sentiment (positive/negative), social buzz (Twitter, Reddit), options flow (put/call ratio, unusual activity), short interest, institutional positioning, analyst revisions.

Output JSON:
{
  "action": "buy|sell|hold|close_long|close_short",
  "confidence": 0.0-1.0,
  "reasoning": "concise explanation",
  "sentiment_score": -1 to 1,
  "news_sentiment": -1 to 1,
  "social_sentiment": -1 to 1,
  "options_sentiment": -1 to 1,
  "key_topics": ["topic1", "topic2"],
  "unusual_activity": false,
  "time_horizon": "intraday|swing",
  "risk_reward": 2.0
}"""

    async def analyze(self, market_data: dict[str, Any]) -> AgentMessage:
        symbol = market_data.get(
            "symbol", self.spec.symbols[0] if self.spec.symbols else "UNKNOWN"
        )

        context = self._build_context(market_data)

        if self.llm:
            response = await self.llm.chat(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": context},
                ],
                temperature=0.2,
                max_tokens=500,
            )

            signal = self._parse_llm_response(response, symbol)
        else:
            signal = self._rule_based_analysis(market_data, symbol)

        signal.metadata["agent_role"] = self.role.value
        signal.metadata["agent_name"] = self.spec.name
        return signal

    def _build_context(self, data: dict) -> str:
        parts = [f"Symbol: {data.get('symbol', 'N/A')}"]

        if "sentiment" in data:
            s = data["sentiment"]
            parts.append(f"Overall Sentiment: {s.get('overall', 'N/A')}")
            parts.append(f"News Sentiment: {s.get('news', 'N/A')}")
            parts.append(f"Social Sentiment: {s.get('social', 'N/A')}")
            parts.append(f"Options Sentiment: {s.get('options', 'N/A')}")

        if "news" in data:
            parts.append(f"Recent News: {data['news'][:3]}")

        if "social" in data:
            soc = data["social"]
            parts.append(f"Social Volume: {soc.get('volume', 'N/A')}")
            parts.append(f"Bullish %: {soc.get('bullish_pct', 'N/A')}")
            parts.append(f"Trending Topics: {soc.get('topics', 'N/A')}")

        if "options" in data:
            o = data["options"]
            parts.append(f"Put/Call Ratio: {o.get('put_call_ratio', 'N/A')}")
            parts.append(f"Unusual Flow: {o.get('unusual_flow', 'N/A')}")
            parts.append(f"IV Rank: {o.get('iv_rank', 'N/A')}")

        if "short_interest" in data:
            parts.append(f"Short Interest: {data['short_interest']}")

        return "\n".join(parts)

    def _parse_llm_response(self, response: str, symbol: str) -> AgentMessage:
        import json
        import re

        try:
            match = re.search(r"\{.*\}", response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found")
        except Exception:
            data = {}

        action = data.get("action", "hold")
        confidence = float(data.get("confidence", 0.5))

        return make_signal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=data.get("size_pct", 0.015),
            reasoning=data.get("reasoning", "Sentiment analysis"),
            metadata={
                "sentiment_score": data.get("sentiment_score", 0),
                "news_sentiment": data.get("news_sentiment", 0),
                "social_sentiment": data.get("social_sentiment", 0),
                "options_sentiment": data.get("options_sentiment", 0),
                "key_topics": data.get("key_topics", []),
                "unusual_activity": data.get("unusual_activity", False),
                "time_horizon": data.get("time_horizon", "swing"),
                "risk_reward": data.get("risk_reward", 2.0),
            },
        )

    def _rule_based_analysis(self, data: dict, symbol: str) -> AgentMessage:
        s = data.get("sentiment", {})
        overall = s.get("overall", 0)

        if overall > 0.3:
            action = "buy"
            confidence = min(0.5 + overall, 0.8)
        elif overall < -0.3:
            action = "sell"
            confidence = min(0.5 + abs(overall), 0.8)
        else:
            action = "hold"
            confidence = 0.5

        return make_signal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=0.015,
            reasoning=f"Sentiment: {overall:.2f}",
            metadata={"sentiment": s},
        )


class RiskAgent(SpecializedAgent):
    """Risk management agent — position sizing, limits, portfolio risk.

    P0.2 migration (STR-0212): Now delegates to ForecastRiskPolicy for
    deterministic volatility-scaled risk assessment. The LLM-based analysis
    path has been permanently removed. Swarm input (dict-based market_data)
    is converted to AnalysisContext before evaluation.
    """

    def __init__(self, spec: AgentSpec, llm_client=None):
        super().__init__(spec, llm_client)
        # Risk limits
        self.max_position_pct = 0.10  # 10% max per position
        self.max_sector_pct = 0.25  # 25% max per sector
        self.max_correlation = 0.7  # Max correlation with portfolio
        self.max_portfolio_var = 0.05  # 5% daily VaR
        self.max_drawdown = 0.15  # 15% max drawdown

    async def analyze(self, market_data: dict[str, Any]) -> AgentMessage:
        """Analyze risk for proposed trades — deterministic (no LLM)."""
        symbol = market_data.get("symbol", "PORTFOLIO")

        proposed_signals = market_data.get("proposed_signals", [])
        portfolio = market_data.get("portfolio", {})

        # Delegate to ForecastRiskPolicy for deterministic risk assessment
        policy = ForecastRiskPolicy()
        context = self._build_analysis_context(market_data)

        vol, vol_ratio, max_pos, risk_level = self._compute_risk(context, portfolio)

        warnings: list[str] = []
        if portfolio.get("drawdown_pct", 0) > self.max_drawdown * 0.8:
            warnings.append("drawdown_approaching_limit")
        if vol > 3.0:
            warnings.append("high_volatility")
        if vol_ratio < 0.5:
            warnings.append("low_volume")

        total_size = sum(s.get("size_pct", 0) for s in proposed_signals)
        if total_size > 0.2:
            warnings.append("high_concentration")
        if portfolio.get("avg_correlation", 0) > self.max_correlation:
            warnings.append("high_correlation")

        # Apply risk level to position sizing
        if risk_level in ("HIGH", "EXTREME"):
            action = "reduce"
            confidence = 0.9
            size_mult = 0.0
        elif risk_level == "MEDIUM":
            action = "reduce"
            confidence = 0.7
            size_mult = 0.5
        else:
            action = "approve"
            confidence = 0.85
            size_mult = 1.0

        suggested_size = min(max_pos, self.max_position_pct) * size_mult

        return make_signal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action="hold",  # Risk agent approves/modifies, doesn't trade
            confidence=confidence,
            size_pct=suggested_size,
            reasoning=f"Risk: {risk_level}, vol={vol:.2f}%, "
            f"max_size={max_pos:.1%}",
            metadata={
                "risk_action": action,
                "warnings": warnings,
                "max_position_pct": self.max_position_pct,
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.05,
                "volatility": vol,
                "vol_ratio": vol_ratio,
            },
        )

    def _build_analysis_context(self, market_data: dict[str, Any]) -> AnalysisContext:
        """Convert swarm market_data dict to AnalysisContext for ForecastRiskPolicy."""
        closes = market_data.get("closes", [])
        ohlcv = None
        if closes and len(closes) >= 2:
            import polars as pl

            ohlcv = pl.DataFrame(
                {
                    "close": closes,
                    "high": closes,
                    "low": closes,
                    "volume": market_data.get("volumes", [100.0] * len(closes)),
                }
            )

        extra = {}
        if "volatility" in market_data:
            extra["volatility_20"] = market_data["volatility"]
        if "volume_ratio" in market_data:
            extra["volume_ratio_5_20"] = market_data["volume_ratio"]

        return AnalysisContext(
            symbol=market_data.get("symbol", "UNKNOWN"),
            timeframe=market_data.get("timeframe", "1h"),
            current_price=float(market_data.get("current_price", 0.0)),
            current_position_pct=float(market_data.get("current_position_pct", 0.0)),
            portfolio_value=float(market_data.get("portfolio", {}).get("total_value", 0.0)),
            ohlcv=ohlcv,
            indicators={"_extra": extra},
        )

    def _compute_risk(
        self, context: AnalysisContext, portfolio: dict
    ) -> tuple[float, float, float, str]:
        """Compute risk level using ForecastRiskPolicy volatility model."""
        ind = context.indicators
        extra = ind.get("_extra", {}) if isinstance(ind, dict) else {}
        vol = ForecastRiskPolicy()._compute_volatility(context)
        vol_ratio = extra.get("volume_ratio_5_20", 1.0)

        if vol > ForecastRiskPolicy.VOL_HIGH_THRESHOLD:
            risk_level = "HIGH"
            max_pos = 0.0
        elif vol > ForecastRiskPolicy.VOL_MED_THRESHOLD:
            risk_level = "MEDIUM"
            stop_pct = max(
                ForecastRiskPolicy.STOP_PCT_MIN,
                min(ForecastRiskPolicy.STOP_PCT_MAX, vol / 100.0),
            )
            risk_based = ForecastRiskPolicy.RISK_PER_TRADE / stop_pct
            vol_cap = ForecastRiskPolicy.VOL_CAP_BASE * min(
                1.0, ForecastRiskPolicy.VOL_MED_THRESHOLD / vol
            )
            max_pos = max(0.05, min(risk_based, vol_cap))
        else:
            risk_level = "LOW"
            stop_pct = max(
                ForecastRiskPolicy.STOP_PCT_MIN,
                min(ForecastRiskPolicy.STOP_PCT_MAX, vol / 100.0),
            )
            risk_based = ForecastRiskPolicy.RISK_PER_TRADE / stop_pct
            vol_cap = ForecastRiskPolicy.VOL_CAP_BASE * min(
                1.0, ForecastRiskPolicy.VOL_MED_THRESHOLD / vol
            )
            max_pos = max(0.05, min(risk_based, vol_cap))

        if vol_ratio < 0.5 and risk_level != "HIGH":
            risk_level = "MEDIUM"
            max_pos = max_pos * 0.5

        return vol, vol_ratio, max_pos, risk_level
