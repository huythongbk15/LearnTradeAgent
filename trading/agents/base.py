"""Base agent framework for trading system."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentMessage:
    """Structured message output from any agent in the system."""
    role: str
    signal: str  # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 to 1.0
    reasoning: str
    details: dict[str, Any] = field(default_factory=dict)
    max_position_size_pct: float | None = None
    risk_level: str | None = None
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
    def from_dict(cls, d: dict) -> "AgentMessage":
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
    """Configuration for an agent."""
    name: str
    role: str
    params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True


class BaseAgent(ABC):
    """Abstract base for all trading agents."""
    
    def __init__(self, config: AgentConfig):
        self.config = config
        self.name = config.name
        self.role = config.role
    
    @abstractmethod
    async def analyze(self, context: dict[str, Any]) -> AgentMessage:
        """Analyze the current market context and return a signal."""
        pass
    
    async def process(self, market_data: dict[str, Any]) -> AgentMessage:
        """Process market data (interface for swarm coordinator)."""
        return await self.analyze(market_data)


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
    symbol: str
    timeframe: str
    current_price: float
    ohlcv: Any = None
    indicators: dict[str, Any] = field(default_factory=dict)
    current_position_pct: float = 0.0
    unrealized_pnl_pct: float = 0.0
    portfolio_value: float = 10000.0
    agent_messages: list[AgentMessage] = field(default_factory=list)
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