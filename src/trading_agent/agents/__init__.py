"""Multi-agent trading system — agents package.

Core agents (sync, orchestrated): TechnicalAnalyst, SentimentAnalyst,
RiskManager, Trader, PortfolioManager.
Phase 6 swarm agents (async): SpecializedAgent family + Coordinator.
"""

from trading_agent.agents.base import (
    AgentMessage,
    AgentConfig,
    AnalysisContext,
    BaseAgent,
    AgentSpec,
    AgentRole,
    AgentSignal,
)
from trading_agent.agents.portfolio import (
    PortfolioAllocation,
    PortfolioDecision,
    PortfolioManager,
)
from trading_agent.agents.risk import RiskManager
from trading_agent.agents.sentiment import SentimentAnalyst
from trading_agent.agents.technical import TechnicalAnalyst
from trading_agent.agents.trader import Trader
from trading_agent.agents.swarm.specialized import (
    TechnicalAgent,
    FundamentalAgent,
    SentimentAgent,
    RiskAgent,
    ExecutionAgent,
)
from trading_agent.agents.swarm.coordinator import (
    CoordinatorAgent,
    SwarmConfig,
    SwarmSignal,
)
from trading_agent.agents.swarm.registry import (
    AgentRegistry,
    SwarmFactory,
)

__all__ = [
    # Base
    "AgentMessage",
    "AgentConfig",
    "AnalysisContext",
    "BaseAgent",
    "AgentSpec",
    "AgentRole",
    "AgentSignal",
    # Core
    "PortfolioAllocation",
    "PortfolioDecision",
    "PortfolioManager",
    "RiskManager",
    "SentimentAnalyst",
    "TechnicalAnalyst",
    "Trader",
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
