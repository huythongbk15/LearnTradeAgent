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
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


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

    def __post_init__(self) -> None:
        """Normalize untrusted agent/LLM output to a safe domain message."""
        if not isinstance(self.details, dict):
            self.details = {}
        if not isinstance(self.warnings, list):
            self.warnings = [str(self.warnings)]
        else:
            self.warnings = [str(warning) for warning in self.warnings]

        signal = str(self.signal).upper()
        if signal not in {"BUY", "SELL", "HOLD"}:
            self.warnings.append(f"Invalid signal {self.signal!r}; forced to HOLD")
            signal = "HOLD"
        self.signal = signal

        try:
            confidence = float(self.confidence)
            if not math.isfinite(confidence):
                raise ValueError("non-finite confidence")
            self.confidence = max(0.0, min(1.0, confidence))
        except (TypeError, ValueError):
            self.warnings.append("Invalid confidence; forced to 0")
            self.confidence = 0.0

        if self.risk_level is not None:
            risk_level = str(self.risk_level).upper()
            if risk_level not in {"LOW", "MEDIUM", "HIGH", "EXTREME"}:
                self.warnings.append(
                    f"Invalid risk level {self.risk_level!r}; forced to MEDIUM"
                )
                risk_level = "MEDIUM"
            self.risk_level = risk_level

        if self.max_position_size_pct is not None:
            try:
                position_size = float(self.max_position_size_pct)
                if not math.isfinite(position_size):
                    raise ValueError("non-finite position size")
                self.max_position_size_pct = max(0.0, min(1.0, position_size))
            except (TypeError, ValueError):
                self.warnings.append("Invalid position size; forced to 0")
                self.max_position_size_pct = 0.0

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


# ─── AgentMessage <-> AgentSignal interop ─────────────────────────────────
# The core agents emit AgentMessage; the Phase 6 swarm consumes AgentSignal.
# These converters bridge the two ecosystems losslessly so a swarm can run
# core agents (or vice versa) without rewriting either side.


def message_to_signal(
    msg: AgentMessage,
    *,
    symbol: str,
    signal_id: str | None = None,
) -> AgentSignal:
    """Convert a core ``AgentMessage`` into a swarm ``AgentSignal``."""
    action = str(msg.signal).lower()
    return AgentSignal(
        signal_id=signal_id or f"msg-{uuid4().hex[:8]}",
        symbol=symbol,
        action=action,
        confidence=msg.confidence,
        size_pct=msg.max_position_size_pct or 0.0,
        reasoning=msg.reasoning or "",
        metadata={
            "role": msg.role,
            "risk_level": msg.risk_level,
            "warnings": list(msg.warnings),
            "details": dict(msg.details or {}),
        },
    )


def signal_to_message(
    sig: AgentSignal,
    *,
    role: str = "agent",
) -> AgentMessage:
    """Convert a swarm ``AgentSignal`` into a core ``AgentMessage``."""
    return AgentMessage(
        role=role,
        signal=str(sig.action).upper(),
        confidence=sig.confidence,
        reasoning=sig.reasoning or "",
        details=dict(sig.metadata or {}),
        max_position_size_pct=sig.size_pct,
    )


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
