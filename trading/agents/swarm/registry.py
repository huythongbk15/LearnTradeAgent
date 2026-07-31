"""Agent registry for swarm management."""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from trading.agents.base import BaseAgent as Agent, AgentSpec, AgentRole
from trading.agents.swarm.specialized import (
    TechnicalAgent, FundamentalAgent, SentimentAgent, RiskAgent, ExecutionAgent
)
from trading.llm.client import LLMClient, LLMConfig

logger = logging.getLogger(__name__)


@dataclass
class AgentInstance:
    """Registered agent instance."""
    spec: AgentSpec
    agent: Agent
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_active: datetime = field(default_factory=datetime.utcnow)
    call_count: int = 0
    total_latency: float = 0.0
    errors: int = 0
    enabled: bool = True


class AgentRegistry:
    """Registry for managing swarm agents."""
    
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client
        self._agents: dict[str, AgentInstance] = {}
        self._role_index: dict[AgentRole, list[str]] = {}
    
    def register(self, spec: AgentSpec, agent: Optional[Agent] = None) -> Agent:
        """Register an agent."""
        if spec.name in self._agents:
            logger.warning(f"Agent {spec.name} already registered, replacing")
        
        if agent is None:
            agent = self._create_agent(spec)
        
        instance = AgentInstance(spec=spec, agent=agent)
        self._agents[spec.name] = instance
        
        # Update role index
        if spec.role not in self._role_index:
            self._role_index[spec.role] = []
        if spec.name not in self._role_index[spec.role]:
            self._role_index[spec.role].append(spec.name)
        
        logger.info(f"Registered agent: {spec.name} ({spec.role.value})")
        return agent
    
    def _create_agent(self, spec: AgentSpec) -> Agent:
        """Create agent from spec."""
        use_llm = spec.params.get("use_llm", True)
        llm = self.llm_client if use_llm else None
        
        if spec.role == AgentRole.TECHNICAL:
            return TechnicalAgent(spec, llm)
        elif spec.role == AgentRole.FUNDAMENTAL:
            return FundamentalAgent(spec, llm)
        elif spec.role == AgentRole.SENTIMENT:
            return SentimentAgent(spec, llm)
        elif spec.role == AgentRole.RISK:
            return RiskAgent(spec, llm)
        elif spec.role == AgentRole.EXECUTION:
            return ExecutionAgent(spec, llm)
        else:
            raise ValueError(f"Unknown agent role: {spec.role}")
    
    def unregister(self, name: str) -> bool:
        """Unregister an agent."""
        if name not in self._agents:
            return False
        
        instance = self._agents[name]
        role = instance.spec.role
        
        del self._agents[name]
        
        if role in self._role_index:
            self._role_index[role] = [n for n in self._role_index[role] if n != name]
        
        logger.info(f"Unregistered agent: {name}")
        return True
    
    def get(self, name: str) -> Optional[Agent]:
        """Get agent by name."""
        if name in self._agents:
            instance = self._agents[name]
            instance.last_active = datetime.utcnow()
            instance.call_count += 1
            return instance.agent
        return None
    
    def get_by_role(self, role: AgentRole) -> list[Agent]:
        """Get all agents of a role."""
        agents = []
        for name in self._role_index.get(role, []):
            agent = self.get(name)
            if agent:
                agents.append(agent)
        return agents
    
    def list_agents(self) -> list[dict]:
        """List all registered agents with status."""
        result = []
        for name, instance in self._agents.items():
            avg_latency = instance.total_latency / instance.call_count if instance.call_count > 0 else 0
            result.append({
                "name": name,
                "role": instance.spec.role.value,
                "symbols": instance.spec.symbols,
                "enabled": instance.enabled,
                "created_at": instance.created_at.isoformat(),
                "last_active": instance.last_active.isoformat(),
                "call_count": instance.call_count,
                "avg_latency_ms": avg_latency * 1000,
                "errors": instance.errors,
            })
        return result
    
    def enable(self, name: str) -> bool:
        """Enable an agent."""
        if name in self._agents:
            self._agents[name].enabled = True
            return True
        return False
    
    def disable(self, name: str) -> bool:
        """Disable an agent."""
        if name in self._agents:
            self._agents[name].enabled = False
            return True
        return False
    
    def record_call(self, name: str, latency: float, error: bool = False) -> None:
        """Record agent call metrics."""
        if name in self._agents:
            instance = self._agents[name]
            instance.total_latency += latency
            if error:
                instance.errors += 1
    
    def get_stats(self) -> dict:
        """Get registry statistics."""
        total_calls = sum(i.call_count for i in self._agents.values())
        total_errors = sum(i.errors for i in self._agents.values())
        total_latency = sum(i.total_latency for i in self._agents.values())
        
        return {
            "total_agents": len(self._agents),
            "enabled_agents": sum(1 for i in self._agents.values() if i.enabled),
            "by_role": {
                role.value: len(names) 
                for role, names in self._role_index.items()
            },
            "total_calls": total_calls,
            "total_errors": total_errors,
            "error_rate": total_errors / total_calls if total_calls > 0 else 0,
            "avg_latency_ms": (total_latency / total_calls * 1000) if total_calls > 0 else 0,
        }


class SwarmFactory:
    """Factory for creating pre-configured swarms."""
    
    @staticmethod
    def create_standard_swarm(
        symbols: list[str],
        llm_client: Optional[LLMClient] = None,
        coordinator_mode: str = "hierarchical",
    ) -> tuple["CoordinatorAgent", AgentRegistry]:
        """Create standard swarm with all agent types."""
        from trading.agents.swarm.coordinator import (
            CoordinatorAgent, SwarmConfig, SwarmMode,
            ConsensusSwarm, CompetitiveSwarm
        )
        
        registry = AgentRegistry(llm_client)
        
        # Create specialized agents
        specs = [
            AgentSpec(
                name="TechnicalAnalyst",
                role=AgentRole.TECHNICAL,
                symbols=symbols,
                params={"use_llm": True},
                weight=1.0,
            ),
            AgentSpec(
                name="FundamentalAnalyst",
                role=AgentRole.FUNDAMENTAL,
                symbols=symbols,
                params={"use_llm": True},
                weight=1.0,
            ),
            AgentSpec(
                name="SentimentAnalyst",
                role=AgentRole.SENTIMENT,
                symbols=symbols,
                params={"use_llm": True},
                weight=0.8,
            ),
            AgentSpec(
                name="RiskManager",
                role=AgentRole.RISK,
                symbols=symbols,
                params={},
                weight=1.5,  # Higher weight for risk
            ),
            AgentSpec(
                name="ExecutionTrader",
                role=AgentRole.EXECUTION,
                symbols=symbols,
                params={},
                weight=1.0,
            ),
        ]
        
        for spec in specs:
            registry.register(spec)
        
        # Create coordinator
        coord_spec = AgentSpec(
            name="SwarmCoordinator",
            role=AgentRole.COORDINATOR,
            symbols=symbols,
            params={},
        )
        
        swarm_config = SwarmConfig(mode=SwarmMode(coordinator_mode))
        
        if coordinator_mode == "consensus":
            coordinator = ConsensusSwarm(coord_spec, swarm_config)
        elif coordinator_mode == "competitive":
            coordinator = CompetitiveSwarm(coord_spec, swarm_config)
        else:
            coordinator = CoordinatorAgent(coord_spec, swarm_config)
        
        # Register agents with coordinator
        for name, instance in registry._agents.items():
            if name != "SwarmCoordinator":
                coordinator.agents[name] = instance.agent
        
        registry.register(coord_spec, coordinator)
        
        return coordinator, registry
    
    @staticmethod
    def create_minimal_swarm(
        symbols: list[str],
        llm_client: Optional[LLMClient] = None,
    ) -> tuple["CoordinatorAgent", AgentRegistry]:
        """Create minimal swarm (technical + risk only)."""
        from trading.agents.swarm.coordinator import CoordinatorAgent, SwarmConfig
        
        registry = AgentRegistry(llm_client)
        
        specs = [
            AgentSpec(
                name="TechnicalAnalyst",
                role=AgentRole.TECHNICAL,
                symbols=symbols,
                params={"use_llm": True},
            ),
            AgentSpec(
                name="RiskManager",
                role=AgentRole.RISK,
                symbols=symbols,
                params={},
            ),
        ]
        
        for spec in specs:
            registry.register(spec)
        
        coord_spec = AgentSpec(
            name="SwarmCoordinator",
            role=AgentRole.COORDINATOR,
            symbols=symbols,
            params={},
        )
        
        coordinator = CoordinatorAgent(coord_spec, SwarmConfig())
        
        for name, instance in registry._agents.items():
            if name != "SwarmCoordinator":
                coordinator.agents[name] = instance.agent
        
        registry.register(coord_spec, coordinator)
        
        return coordinator, registry
    
    @staticmethod
    def create_crypto_swarm(
        symbols: list[str],
        llm_client: Optional[LLMClient] = None,
    ) -> tuple["CoordinatorAgent", AgentRegistry]:
        """Create crypto-optimized swarm."""
        from trading.agents.swarm.coordinator import CoordinatorAgent, SwarmConfig
        
        registry = AgentRegistry(llm_client)
        
        specs = [
            AgentSpec(
                name="TechnicalAnalyst",
                role=AgentRole.TECHNICAL,
                symbols=symbols,
                params={"use_llm": True},
            ),
            AgentSpec(
                name="SentimentAnalyst",
                role=AgentRole.SENTIMENT,
                symbols=symbols,
                params={"use_llm": True},
            ),
            AgentSpec(
                name="OnChainAnalyst",  # Custom agent for on-chain data
                role=AgentRole.FUNDAMENTAL,  # Reuse fundamental role
                symbols=symbols,
                params={"use_llm": True, "data_source": "onchain"},
            ),
            AgentSpec(
                name="RiskManager",
                role=AgentRole.RISK,
                symbols=symbols,
                params={"max_leverage": 3.0},
            ),
            AgentSpec(
                name="ExecutionTrader",
                role=AgentRole.EXECUTION,
                symbols=symbols,
                params={"prefer_dex": True},
            ),
        ]
        
        for spec in specs:
            registry.register(spec)
        
        coord_spec = AgentSpec(
            name="SwarmCoordinator",
            role=AgentRole.COORDINATOR,
            symbols=symbols,
            params={},
        )
        
        coordinator = CoordinatorAgent(coord_spec, SwarmConfig())
        
        for name, instance in registry._agents.items():
            if name != "SwarmCoordinator":
                coordinator.agents[name] = instance.agent
        
        registry.register(coord_spec, coordinator)
        
        return coordinator, registry