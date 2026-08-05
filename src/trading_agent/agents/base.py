"""
Base agent framework — AgentMessage protocol, BaseAgent abstract class.

Merged from two generations:
- Core (sync): BaseAgent.analyze(context) -> AgentMessage, name-based init.
- Phase 6 (async): AgentConfig/AgentSpec/AgentRole/AgentSignal + process().

BaseAgent supports both styles: ``analyze`` may be sync (core agents) or
async (swarm agents); ``process()`` handles both.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentMessage:
    """Structured message output from any agent in the system."""

    role: str  # "technical_analyst", "sentiment_analyst", "risk_manager", "trader"
    signal: str  # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 to 1.0
    reasoning: str  # 1-2 sentence explanation
    details: dict[str, Any] = field(default_factory=dict)

    # Risk-specific (only for risk_manager)
    max_position_size_pct: float | None = None  # 0.0 to 1.0
    risk_level: str | None = None  # "LOW" | "MEDIUM" | "HIGH" | "EXTREME"
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "signal": self.signal,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "details": self.details,
            "max_position_size_pct": self.max_position_size_pct,
            "risk_level": self.risk_level,
            "warnings": self.warnings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> AgentMessage:
        return cls(
            role=d.get("role", "unknown"),
            signal=d.get("signal", "HOLD"),
            confidence=d.get("confidence", 0.5),
            reasoning=d.get("reasoning", ""),
            details=d.get("details", {}),
            max_position_size_pct=d.get("max_position_size_pct"),
            risk_level=d.get("risk_level"),
            warnings=d.get("warnings", []),
        )


@dataclass
class AgentConfig:
    """Configuration for an agent (Phase 6 style)."""
    name: str
    role: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class BaseAgent(ABC):
    """Abstract base for all trading agents.

    Core style: subclass implements ``analyze(context) -> AgentMessage``
    (may be sync or async). Phase 6 style: subclass may also implement
    ``process(market_data)`` and receive an ``AgentConfig`` at init.
    """

    def __init__(
        self,
        config: AgentConfig | None = None,
        name: str | None = None,
    ):
        self.config = config
        self.name = name or (config.name if config else self.__class__.__name__)
        self.role = config.role if config else getattr(self, "role", None)

    @abstractmethod
    def analyze(self, context: AnalysisContext) -> AgentMessage:
        """Analyze the current market context and return a signal."""
        ...

    async def process(self, market_data: dict[str, Any]) -> AgentMessage:
        """Process market data (interface for swarm coordinator).

        Works for both sync and async ``analyze`` implementations.
        """
        result = self.analyze(market_data)
        if inspect.isawaitable(result):
            return await result
        return result


@dataclass
class AgentSpec:
    """Specification for creating an agent."""
    name: str
    role: str
    symbols: list[str]
    timeframes: list[str] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    weight: float = 1.0
    enabled: bool = True


class AgentRole:
    """Agent roles in the swarm."""
    TECHNICAL = "technical"
    FUNDAMENTAL = "fundamental"
    SENTIMENT = "sentiment"
    RISK = "risk"
    EXECUTION = "execution"
    COORDINATOR = "coordinator"


@dataclass
class AgentSignal:
    """Trading signal from an agent."""
    signal_id: str
    symbol: str
    action: str  # buy, sell, hold, close_long, close_short
    confidence: float
    size_pct: float
    reasoning: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AnalysisContext:
    """All the data an agent needs to make a decision."""

    # Market data
    symbol: str
    timeframe: str
    current_price: float
    ohlcv: Any = None  # full OHLCV DataFrame (polars), optional

    # Computed indicators
    indicators: dict[str, Any] = field(default_factory=dict)

    # Current position state
    current_position_pct: float = 0.0  # 0.0 = flat
    unrealized_pnl_pct: float = 0.0
    portfolio_value: float = 10000.0

    # Previous agent messages (for orchestration)
    agent_messages: list[AgentMessage] = field(default_factory=list)

    # Optional price history summary
    price_change_1d: float | None = None
    price_change_1w: float | None = None
    price_change_1m: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "current_price": self.current_price,
            "indicators": self.indicators,
            "current_position_pct": self.current_position_pct,
            "unrealized_pnl_pct": self.unrealized_pnl_pct,
            "portfolio_value": self.portfolio_value,
            "price_change_1d": self.price_change_1d,
            "price_change_1w": self.price_change_1w,
            "price_change_1m": self.price_change_1m,
        }
