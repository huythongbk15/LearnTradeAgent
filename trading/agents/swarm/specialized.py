"""Specialized agents for the swarm."""

import logging
from abc import abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional
import uuid

from trading.agents.base import BaseAgent as Agent, AgentSignal, AgentConfig
from trading.llm.client import LLMClient

logger = logging.getLogger(__name__)


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
        llm_client: Optional[LLMClient] = None,
    ):
        super().__init__(spec.config)
        self.spec = spec
        self.llm = llm_client
        self.role = spec.role
        self.last_signal: Optional[AgentSignal] = None
        self.performance_history: list[dict] = []
    
    @abstractmethod
    async def analyze(self, market_data: dict[str, Any]) -> AgentSignal:
        """Analyze market data and produce signal."""
        pass
    
    async def process(self, market_data: dict[str, Any]) -> AgentSignal:
        """Process market data (interface for coordinator)."""
        signal = await self.analyze(market_data)
        self.last_signal = signal
        
        # Track performance
        self.performance_history.append({
            "timestamp": datetime.utcnow(),
            "signal": signal.action,
            "confidence": signal.confidence,
            "reasoning": signal.reasoning,
        })
        
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
    
    async def analyze(self, market_data: dict[str, Any]) -> AgentSignal:
        symbol = market_data.get("symbol", self.spec.symbols[0] if self.spec.symbols else "UNKNOWN")
        
        # Build context from market data
        context = self._build_context(market_data)
        
        if self.llm:
            # Use LLM for analysis
            response = await self.llm.chat([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": context}
            ], temperature=0.2, max_tokens=500)
            
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
    
    def _parse_llm_response(self, response: str, symbol: str) -> AgentSignal:
        import json
        import re
        
        try:
            # Extract JSON
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found")
        except Exception:
            data = {}
        
        action = data.get("action", "hold")
        confidence = float(data.get("confidence", 0.5))
        
        return AgentSignal(
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
            }
        )
    
    def _rule_based_analysis(self, data: dict, symbol: str) -> AgentSignal:
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
        
        return AgentSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=0.02,
            reasoning=f"Rule-based: bullish={bullish}, bearish={bearish}",
            metadata={"indicators": ind}
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
    
    async def analyze(self, market_data: dict[str, Any]) -> AgentSignal:
        symbol = market_data.get("symbol", self.spec.symbols[0] if self.spec.symbols else "UNKNOWN")
        
        context = self._build_context(market_data)
        
        if self.llm:
            response = await self.llm.chat([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": context}
            ], temperature=0.2, max_tokens=500)
            
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
    
    def _parse_llm_response(self, response: str, symbol: str) -> AgentSignal:
        import json
        import re
        
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found")
        except Exception:
            data = {}
        
        action = data.get("action", "hold")
        confidence = float(data.get("confidence", 0.5))
        
        return AgentSignal(
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
            }
        )
    
    def _rule_based_analysis(self, data: dict, symbol: str) -> AgentSignal:
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
        
        return AgentSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=0.015,
            reasoning=f"PEG={peg:.1f}, Margin={margin:.1%}",
            metadata={"peg": peg, "margin": margin}
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
    
    async def analyze(self, market_data: dict[str, Any]) -> AgentSignal:
        symbol = market_data.get("symbol", self.spec.symbols[0] if self.spec.symbols else "UNKNOWN")
        
        context = self._build_context(market_data)
        
        if self.llm:
            response = await self.llm.chat([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": context}
            ], temperature=0.2, max_tokens=500)
            
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
    
    def _parse_llm_response(self, response: str, symbol: str) -> AgentSignal:
        import json
        import re
        
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found")
        except Exception:
            data = {}
        
        action = data.get("action", "hold")
        confidence = float(data.get("confidence", 0.5))
        
        return AgentSignal(
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
            }
        )
    
    def _rule_based_analysis(self, data: dict, symbol: str) -> AgentSignal:
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
        
        return AgentSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=action,
            confidence=confidence,
            size_pct=0.015,
            reasoning=f"Sentiment: {overall:.2f}",
            metadata={"sentiment": s}
        )


class RiskAgent(SpecializedAgent):
    """Risk management agent - position sizing, limits, portfolio risk."""
    
    SYSTEM_PROMPT = """You are a risk management expert. Evaluate trading signals against risk limits and portfolio constraints.

Consider: position size limits, sector/concentration limits, correlation risk, VaR, drawdown limits, leverage, liquidity, margin requirements.

Output JSON:
{
  "action": "approve|reduce|reject|hedge",
  "confidence": 0.0-1.0,
  "reasoning": "concise explanation",
  "max_position_pct": 0.05,
  "suggested_size_pct": 0.02,
  "stop_loss_pct": 0.02,
  "take_profit_pct": 0.05,
  "risk_metrics": {"var_95": 0.02, "max_drawdown": 0.1, "correlation": 0.3},
  "warnings": ["concentration", "correlation"],
  "hedge_suggestion": "SPY put|VIX call|none"
}"""
    
    def __init__(self, spec: AgentSpec, llm_client: Optional[LLMClient] = None):
        super().__init__(spec, llm_client)
        # Risk limits
        self.max_position_pct = 0.10  # 10% max per position
        self.max_sector_pct = 0.25    # 25% max per sector
        self.max_correlation = 0.7    # Max correlation with portfolio
        self.max_portfolio_var = 0.05 # 5% daily VaR
        self.max_drawdown = 0.15      # 15% max drawdown
    
    async def analyze(self, market_data: dict[str, Any]) -> AgentSignal:
        """Analyze risk for proposed trades."""
        symbol = market_data.get("symbol", "PORTFOLIO")
        
        # Get proposed trade from other agents
        proposed_signals = market_data.get("proposed_signals", [])
        portfolio = market_data.get("portfolio", {})
        
        context = self._build_context(proposed_signals, portfolio)
        
        if self.llm:
            response = await self.llm.chat([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": context}
            ], temperature=0.1, max_tokens=500)
            
            signal = self._parse_llm_response(response, symbol)
        else:
            signal = self._rule_based_analysis(proposed_signals, portfolio, symbol)
        
        signal.metadata["agent_role"] = self.role.value
        signal.metadata["agent_name"] = self.spec.name
        return signal
    
    def _build_context(self, signals: list, portfolio: dict) -> str:
        parts = ["Proposed Trades:"]
        for s in signals:
            parts.append(f"  {s.get('symbol')}: {s.get('action')} {s.get('size_pct', 0):.1%} (conf: {s.get('confidence', 0):.2f})")
        
        parts.append("\nPortfolio:")
        parts.append(f"  Total Value: ${portfolio.get('total_value', 0):,.0f}")
        parts.append(f"  Cash: ${portfolio.get('cash', 0):,.0f}")
        parts.append(f"  Positions: {len(portfolio.get('positions', {}))}")
        parts.append(f"  Current Drawdown: {portfolio.get('drawdown_pct', 0):.1%}")
        parts.append(f"  Portfolio VaR: {portfolio.get('var_95', 0):.2%}")
        
        if "positions" in portfolio:
            for sym, pos in portfolio["positions"].items():
                parts.append(f"  {sym}: {pos.get('size_pct', 0):.1%} (PnL: {pos.get('unrealized_pnl_pct', 0):.1%})")
        
        return "\n".join(parts)
    
    def _parse_llm_response(self, response: str, symbol: str) -> AgentSignal:
        import json
        import re
        
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found")
        except Exception:
            data = {}
        
        action = data.get("action", "approve")
        confidence = float(data.get("confidence", 0.8))
        
        # Map risk actions to trading actions
        if action == "reject":
            trade_action = "hold"
        elif action == "reduce":
            trade_action = "hold"  # Will size down
        elif action == "hedge":
            trade_action = "buy"   # Buy hedge
        else:
            trade_action = "hold"  # Pass through
        
        return AgentSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action=trade_action,
            confidence=confidence,
            size_pct=data.get("suggested_size_pct", 0.02),
            reasoning=data.get("reasoning", "Risk check"),
            metadata={
                "risk_action": action,
                "max_position_pct": data.get("max_position_pct", 0.1),
                "stop_loss_pct": data.get("stop_loss_pct", 0.02),
                "take_profit_pct": data.get("take_profit_pct", 0.05),
                "risk_metrics": data.get("risk_metrics", {}),
                "warnings": data.get("warnings", []),
                "hedge_suggestion": data.get("hedge_suggestion", "none"),
            }
        )
    
    def _rule_based_analysis(self, signals: list, portfolio: dict, symbol: str) -> AgentSignal:
        """Rule-based risk check."""
        warnings = []
        
        # Check portfolio drawdown
        dd = portfolio.get("drawdown_pct", 0)
        if dd > self.max_drawdown * 0.8:
            warnings.append("drawdown_approaching_limit")
        
        # Check concentration
        total_size = sum(s.get("size_pct", 0) for s in signals)
        if total_size > 0.2:
            warnings.append("high_concentration")
        
        # Check correlation (simplified)
        if portfolio.get("avg_correlation", 0) > self.max_correlation:
            warnings.append("high_correlation")
        
        if warnings:
            action = "reduce"
            confidence = 0.7
            size_mult = 0.5
        else:
            action = "approve"
            confidence = 0.85
            size_mult = 1.0
        
        return AgentSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action="hold",  # Risk agent doesn't trade, it approves/modifies
            confidence=confidence,
            size_pct=0.02 * size_mult,
            reasoning=f"Risk check: {', '.join(warnings) or 'OK'}",
            metadata={
                "risk_action": action,
                "warnings": warnings,
                "max_position_pct": self.max_position_pct,
                "stop_loss_pct": 0.02,
                "take_profit_pct": 0.05,
            }
        )


class ExecutionAgent(SpecializedAgent):
    """Execution agent - order routing, timing, slippage minimization."""
    
    SYSTEM_PROMPT = """You are an execution trader. Determine optimal order type, timing, and routing for approved trades.

Consider: liquidity, spread, volatility, time of day, order size relative to ADV, urgency, market impact.

Output JSON:
{
  "action": "market|limit|twap|vwap|iceberg|conditional",
  "confidence": 0.0-1.0,
  "reasoning": "concise explanation",
  "order_type": "market|limit|stop|stop_limit",
  "limit_price_offset": 0.001,
  "time_in_force": "DAY|IOC|GTC",
  "slice_size_pct": 0.1,
  "duration_minutes": 60,
  "venue": "best|specific",
  "urgency": "low|medium|high"
}"""
    
    async def analyze(self, market_data: dict[str, Any]) -> AgentSignal:
        symbol = market_data.get("symbol", self.spec.symbols[0] if self.spec.symbols else "UNKNOWN")
        
        context = self._build_context(market_data)
        
        if self.llm:
            response = await self.llm.chat([
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": context}
            ], temperature=0.1, max_tokens=400)
            
            signal = self._parse_llm_response(response, symbol)
        else:
            signal = self._rule_based_analysis(market_data, symbol)
        
        signal.metadata["agent_role"] = self.role.value
        signal.metadata["agent_name"] = self.spec.name
        return signal
    
    def _build_context(self, data: dict) -> str:
        parts = [f"Symbol: {data.get('symbol', 'N/A')}"]
        
        if "market" in data:
            m = data["market"]
            parts.append(f"Bid: {m.get('bid', 'N/A')}")
            parts.append(f"Ask: {m.get('ask', 'N/A')}")
            parts.append(f"Spread: {m.get('spread_pct', 'N/A'):.3%}")
            parts.append(f"ADV: {m.get('adv', 'N/A'):,.0f}")
            parts.append(f"Volatility: {m.get('volatility', 'N/A'):.2%}")
        
        if "order" in data:
            o = data["order"]
            parts.append(f"Side: {o.get('side', 'N/A')}")
            parts.append(f"Size: {o.get('size', 'N/A')}")
            parts.append(f"Urgency: {o.get('urgency', 'medium')}")
        
        parts.append(f"Time: {data.get('time', 'N/A')}")
        
        return "\n".join(parts)
    
    def _parse_llm_response(self, response: str, symbol: str) -> AgentSignal:
        import json
        import re
        
        try:
            match = re.search(r'\{.*\}', response, re.DOTALL)
            if match:
                data = json.loads(match.group())
            else:
                raise ValueError("No JSON found")
        except Exception:
            data = {}
        
        action = data.get("action", "market")
        confidence = float(data.get("confidence", 0.8))
        
        # Execution agent outputs execution params, not trade direction
        return AgentSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action="hold",  # Doesn't generate trade direction
            confidence=confidence,
            size_pct=0,
            reasoning=data.get("reasoning", "Execution plan"),
            metadata={
                "execution_action": action,
                "order_type": data.get("order_type", "market"),
                "limit_price_offset": data.get("limit_price_offset", 0.001),
                "time_in_force": data.get("time_in_force", "DAY"),
                "slice_size_pct": data.get("slice_size_pct", 0.1),
                "duration_minutes": data.get("duration_minutes", 60),
                "venue": data.get("venue", "best"),
                "urgency": data.get("urgency", "medium"),
            }
        )
    
    def _rule_based_analysis(self, data: dict, symbol: str) -> AgentSignal:
        m = data.get("market", {})
        spread = m.get("spread_pct", 0.001)
        adv = m.get("adv", 1_000_000)
        order_size = data.get("order", {}).get("size", 0)
        urgency = data.get("order", {}).get("urgency", "medium")
        
        participation = order_size / adv if adv > 0 else 0
        
        if participation < 0.01 and urgency == "low":
            exec_action = "twap"
            duration = 120
        elif participation < 0.05:
            exec_action = "vwap"
            duration = 60
        elif spread < 0.0005:
            exec_action = "limit"
            duration = 10
        else:
            exec_action = "market"
            duration = 5
        
        return AgentSignal(
            signal_id=str(uuid.uuid4()),
            symbol=symbol,
            action="hold",
            confidence=0.8,
            size_pct=0,
            reasoning=f"Participation: {participation:.2%}, Spread: {spread:.3%}",
            metadata={
                "execution_action": exec_action,
                "order_type": "limit" if exec_action == "limit" else "market",
                "duration_minutes": duration,
                "participation_rate": participation,
            }
        )


