"""Agent Swarm - multiple specialized agents with coordinator."""

from trading_agent.agents.swarm.coordinator import CoordinatorAgent, SwarmConfig, SwarmMode, SwarmSignal
from trading_agent.agents.swarm.specialized import (
    TechnicalAgent,
    FundamentalAgent,
    SentimentAgent,
    RiskAgent,
    ExecutionAgent,
)
from trading_agent.agents.swarm.registry import AgentRegistry, AgentSpec

__all__ = [
    "CoordinatorAgent",
    "SwarmConfig",
    "SwarmMode",
    "SwarmSignal",
    "TechnicalAgent",
    "FundamentalAgent",
    "SentimentAgent",
    "RiskAgent",
    "ExecutionAgent",
    "AgentRegistry",
    "AgentSpec",
]