"""Agents module - base classes and swarm implementation."""

from trading.agents.base import (
    AgentMessage,
    AgentConfig,
    BaseAgent,
    AgentSpec,
    AgentRole,
    AgentSignal,
    AnalysisContext,
)

from trading.agents.swarm.specialized import (
    TechnicalAgent,
    FundamentalAgent,
    SentimentAgent,
    RiskAgent,
    ExecutionAgent,
)

from trading.agents.swarm.coordinator import (
    CoordinatorAgent,
    SwarmConfig,
    SwarmSignal,
)

from trading.agents.swarm.registry import (
    AgentRegistry,
    SwarmFactory,
)

__all__ = [
    # Base
    "AgentMessage",
    "AgentConfig", 
    "BaseAgent",
    "AgentSpec",
    "AgentRole",
    "AgentSignal",
    "AnalysisContext",
    # Swarm
    "TechnicalAgent",
    "FundamentalAgent",
    "SentimentAgent",
    "RiskAgent",
    "ExecutionAgent",
    "CoordinatorAgent",
    "SwarmConfig",
    "SwarmSignal",
    "AgentRegistry",
    "SwarmFactory",
]