"""
Base agent framework — AgentMessage protocol, BaseAgent abstract class.
"""

from __future__ import annotations

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


class BaseAgent(ABC):
    """Abstract base for all trading agents.

    Each agent subclass implements ``analyze()`` which receives a
    ``AnalysisContext`` and returns an ``AgentMessage``.
    """

    def __init__(self, name: str | None = None):
        self.name = name or self.__class__.__name__

    @abstractmethod
    def analyze(self, context: AnalysisContext) -> AgentMessage:
        """Analyze the current market context and return a signal."""
        ...


@dataclass
class AnalysisContext:
    """All the data an agent needs to make a decision."""

    # Market data
    symbol: str
    timeframe: str
    current_price: float
    ohlcv: Any  # full OHLCV DataFrame (polars)

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
