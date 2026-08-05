"""Coordinator agent implementations for swarm."""

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from trading_agent.agents.base import BaseAgent as Agent, AgentSignal, AgentSpec

logger = logging.getLogger(__name__)


class SwarmMode(str, Enum):
    """Swarm operation modes."""
    CONSENSUS = "consensus"
    HIERARCHICAL = "hierarchical"
    PIPELINE = "pipeline"
    COMPETITIVE = "competitive"


@dataclass
class SwarmConfig:
    """Configuration for agent swarm."""
    mode: SwarmMode = SwarmMode.HIERARCHICAL
    min_consensus: float = 0.6
    max_parallel_agents: int = 4
    timeout_seconds: float = 30.0
    risk_override: bool = True
    execution_integration: bool = True


@dataclass
class SwarmSignal:
    """Aggregated signal from swarm."""
    swarm_id: str
    symbol: str
    final_action: str
    final_confidence: float
    final_size_pct: float
    agent_signals: list[AgentSignal]
    risk_approved: bool = True
    execution_plan: Optional[dict] = None
    consensus_score: float = 0.0
    dissenting_agents: list[str] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    metadata: dict = field(default_factory=dict)


class CoordinatorAgent(Agent):
    """Base coordinator for agent swarm."""
    
    def __init__(self, spec: AgentSpec, config: Optional[SwarmConfig] = None):
        super().__init__(spec)
        self.config = config or SwarmConfig()
        self.agents: dict[str, Agent] = {}
        self.signal_history: list[SwarmSignal] = []
    
    def add_agent(self, name: str, agent: Agent) -> None:
        """Add agent to swarm."""
        self.agents[name] = agent
        logger.info(f"Added agent {name} to swarm")
    
    def remove_agent(self, name: str) -> bool:
        """Remove agent from swarm."""
        if name in self.agents:
            del self.agents[name]
            return True
        return False
    
    async def process(self, market_data: dict[str, Any]) -> SwarmSignal:
        """Process market data through swarm."""
        symbol = market_data.get("symbol", "UNKNOWN")
        
        # Run agents in parallel (with semaphore for limit)
        semaphore = asyncio.Semaphore(self.config.max_parallel_agents)
        
        async def run_agent(name: str, agent: Agent):
            async with semaphore:
                try:
                    # Prepare data for this agent
                    agent_data = market_data.copy()
                    agent_data["symbol"] = symbol
                    return name, await asyncio.wait_for(
                        agent.process(agent_data),
                        timeout=self.config.timeout_seconds
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"Agent {name} timed out")
                    return name, None
                except Exception as e:
                    logger.error(f"Agent {name} error: {e}")
                    return name, None
        
        # Run all agents
        tasks = [run_agent(name, agent) for name, agent in self.agents.items()]
        results = await asyncio.gather(*tasks)
        
        # Collect signals
        signals = []
        for name, signal in results:
            if signal:
                signal.metadata["agent_name"] = name
                signals.append(signal)
        
        # Aggregate
        swarm_signal = self._aggregate(symbol, signals, market_data)
        
        # Record
        self.signal_history.append(swarm_signal)
        if len(self.signal_history) > 1000:
            self.signal_history = self.signal_history[-1000:]
        
        return swarm_signal
    
    def _aggregate(
        self, 
        symbol: str, 
        signals: list[AgentSignal], 
        market_data: dict
    ) -> SwarmSignal:
        """Aggregate agent signals."""
        if not signals:
            return SwarmSignal(
                swarm_id=f"swarm_{datetime.utcnow().timestamp()}",
                symbol=symbol,
                final_action="hold",
                final_confidence=0.0,
                final_size_pct=0.0,
                agent_signals=[],
            )
        
        # Separate risk signals
        risk_signals = [s for s in signals if s.metadata.get("agent_role") == "risk"]
        trading_signals = [s for s in signals if s.metadata.get("agent_role") != "risk"]
        execution_signals = [s for s in signals if s.metadata.get("agent_role") == "execution"]
        
        # Risk check
        risk_approved = True
        risk_warnings = []
        if risk_signals and self.config.risk_override:
            risk = risk_signals[0]
            risk_action = risk.metadata.get("risk_action", "approve")
            if risk_action == "reject":
                risk_approved = False
            elif risk_action == "reduce":
                risk_warnings = risk.metadata.get("warnings", [])
        
        # Aggregate trading signals
        if self.config.mode == SwarmMode.CONSENSUS:
            final = self._consensus_aggregate(trading_signals, risk_approved, risk_warnings)
        elif self.config.mode == SwarmMode.PIPELINE:
            final = self._pipeline_aggregate(trading_signals, risk_approved, risk_warnings)
        else:  # HIERARCHICAL
            final = self._hierarchical_aggregate(trading_signals, risk_approved, risk_warnings)
        
        # Execution plan
        execution_plan = None
        if execution_signals and self.config.execution_integration:
            exec_signal = execution_signals[0]
            execution_plan = {
                "action": exec_signal.metadata.get("execution_action"),
                "order_type": exec_signal.metadata.get("order_type"),
                "limit_price_offset": exec_signal.metadata.get("limit_price_offset"),
                "duration_minutes": exec_signal.metadata.get("duration_minutes"),
                "venue": exec_signal.metadata.get("venue"),
            }
        
        return SwarmSignal(
            swarm_id=f"swarm_{datetime.utcnow().timestamp()}",
            symbol=symbol,
            final_action=final["action"],
            final_confidence=final["confidence"],
            final_size_pct=final["size_pct"],
            agent_signals=signals,
            risk_approved=risk_approved,
            execution_plan=execution_plan,
            consensus_score=final["consensus"],
            dissenting_agents=final["dissenters"],
            metadata={
                "mode": self.config.mode.value,
                "risk_warnings": risk_warnings,
                "num_agents": len(signals),
            }
        )
    
    def _consensus_aggregate(
        self, 
        signals: list[AgentSignal], 
        risk_approved: bool, 
        risk_warnings: list
    ) -> dict:
        """Consensus-based aggregation."""
        if not signals:
            return {"action": "hold", "confidence": 0, "size_pct": 0, "consensus": 0, "dissenters": []}
        
        # Weight votes by confidence and agent weight
        votes = {"buy": 0, "sell": 0, "hold": 0, "close_long": 0, "close_short": 0}
        total_weight = 0
        
        for s in signals:
            weight = s.confidence * s.metadata.get("weight", 1.0)
            action = s.action
            if action in votes:
                votes[action] += weight
            total_weight += weight
        
        if total_weight == 0:
            return {"action": "hold", "confidence": 0, "size_pct": 0, "consensus": 0, "dissenters": []}
        
        # Normalize
        for k in votes:
            votes[k] /= total_weight
        
        # Check consensus
        max_vote = max(votes.values())
        consensus = max_vote
        
        if consensus < self.config.min_consensus:
            action = "hold"
            confidence = 0.3
        else:
            action = max(votes, key=votes.get)
            confidence = max_vote
        
        # Calculate size (weighted average)
        size_pct = sum(s.size_pct * s.confidence for s in signals) / sum(s.confidence for s in signals) if signals else 0
        
        # Apply risk adjustments
        if not risk_approved:
            action = "hold"
            size_pct = 0
            confidence *= 0.5
        elif risk_warnings:
            size_pct *= 0.5
        
        # Find dissenters
        dissenters = [
            s.metadata.get("agent_name", "unknown") 
            for s in signals 
            if s.action != action and s.confidence > 0.5
        ]
        
        return {
            "action": action,
            "confidence": confidence,
            "size_pct": size_pct,
            "consensus": consensus,
            "dissenters": dissenters,
        }
    
    def _hierarchical_aggregate(
        self, 
        signals: list[AgentSignal], 
        risk_approved: bool, 
        risk_warnings: list
    ) -> dict:
        """Hierarchical aggregation (coordinator decides)."""
        if not signals:
            return {"action": "hold", "confidence": 0, "size_pct": 0, "consensus": 0, "dissenters": []}
        
        # Priority order: Technical > Fundamental > Sentiment
        priority = {"technical": 3, "fundamental": 2, "sentiment": 1}
        
        # Sort by priority and confidence
        sorted_signals = sorted(
            signals,
            key=lambda s: (
                priority.get(s.metadata.get("agent_role", ""), 0),
                s.confidence
            ),
            reverse=True
        )
        
        # Primary signal from highest priority
        primary = sorted_signals[0]
        action = primary.action
        confidence = primary.confidence
        size_pct = primary.size_pct
        
        # Check agreement
        agreements = sum(1 for s in signals if s.action == action)
        consensus = agreements / len(signals)
        
        # If strong disagreement, reduce confidence
        if consensus < 0.5:
            confidence *= consensus
        
        # Risk adjustments
        if not risk_approved:
            action = "hold"
            size_pct = 0
        elif risk_warnings:
            size_pct *= 0.5
        
        dissenters = [s.metadata.get("agent_name") for s in signals if s.action != action]
        
        return {
            "action": action,
            "confidence": confidence,
            "size_pct": size_pct,
            "consensus": consensus,
            "dissenters": dissenters,
        }
    
    def _pipeline_aggregate(
        self, 
        signals: list[AgentSignal], 
        risk_approved: bool, 
        risk_warnings: list
    ) -> dict:
        """Pipeline aggregation (sequential filtering)."""
        # Stage 1: Technical analysis
        tech_signals = [s for s in signals if s.metadata.get("agent_role") == "technical"]
        if not tech_signals:
            return {"action": "hold", "confidence": 0, "size_pct": 0, "consensus": 0, "dissenters": []}
        
        tech_action = tech_signals[0].action
        tech_conf = tech_signals[0].confidence
        
        # Stage 2: Fundamental confirmation
        fund_signals = [s for s in signals if s.metadata.get("agent_role") == "fundamental"]
        fund_confirm = any(s.action == tech_action for s in fund_signals) if fund_signals else True
        
        # Stage 3: Sentiment alignment
        sent_signals = [s for s in signals if s.metadata.get("agent_role") == "sentiment"]
        sent_align = any(s.action == tech_action for s in sent_signals) if sent_signals else True
        
        if not fund_confirm or not sent_align:
            # Conflicting signals -> hold
            action = "hold"
            confidence = 0.3
            size_pct = 0
        else:
            action = tech_action
            confidence = tech_conf
            size_pct = tech_signals[0].size_pct
            
            # Boost confidence if all agree
            if fund_confirm and sent_align:
                confidence = min(0.9, confidence * 1.2)
        
        # Risk
        if not risk_approved:
            action = "hold"
            size_pct = 0
        elif risk_warnings:
            size_pct *= 0.5
        
        return {
            "action": action,
            "confidence": confidence,
            "size_pct": size_pct,
            "consensus": 1.0 if (fund_confirm and sent_align) else 0.3,
            "dissenters": [],
        }


class ConsensusSwarm(CoordinatorAgent):
    """Swarm that requires consensus among agents."""
    
    def _aggregate(self, symbol: str, signals: list[AgentSignal], market_data: dict) -> SwarmSignal:
        """Override to enforce strict consensus."""
        self.config.mode = SwarmMode.CONSENSUS
        self.config.min_consensus = 0.75
        return super()._aggregate(symbol, signals, market_data)


class CompetitiveSwarm(CoordinatorAgent):
    """Swarm where best signal wins (competitive)."""
    
    def _aggregate(self, symbol: str, signals: list[AgentSignal], market_data: dict) -> SwarmSignal:
        """Override for competitive mode."""
        if not signals:
            return SwarmSignal(
                swarm_id=f"swarm_{datetime.utcnow().timestamp()}",
                symbol=symbol,
                final_action="hold",
                final_confidence=0.0,
                final_size_pct=0.0,
                agent_signals=[],
            )
        
        # Select best signal by confidence * track record
        best_signal = max(
            signals,
            key=lambda s: s.confidence * self._get_agent_performance(s.metadata.get("agent_name"))
        )
        
        # Risk check
        risk_signals = [s for s in signals if s.metadata.get("agent_role") == "risk"]
        risk_approved = True
        if risk_signals and self.config.risk_override:
            risk_action = risk_signals[0].metadata.get("risk_action", "approve")
            if risk_action == "reject":
                risk_approved = False
        
        action = best_signal.action if risk_approved else "hold"
        size_pct = best_signal.size_pct if risk_approved else 0
        
        return SwarmSignal(
            swarm_id=f"swarm_{datetime.utcnow().timestamp()}",
            symbol=symbol,
            final_action=action,
            final_confidence=best_signal.confidence,
            final_size_pct=size_pct,
            agent_signals=signals,
            risk_approved=risk_approved,
            consensus_score=best_signal.confidence,
            dissenting_agents=[
                s.metadata.get("agent_name") for s in signals if s != best_signal
            ],
            metadata={"mode": "competitive", "winner": best_signal.metadata.get("agent_name")},
        )
    
    def _get_agent_performance(self, agent_name: str) -> float:
        """Get historical performance multiplier for agent."""
        # Simple: return 1.0 for all (would track actual performance)
        return 1.0


# Signal aggregation utilities
def majority_vote(signals: list[AgentSignal]) -> tuple[str, float]:
    """Simple majority vote."""
    votes = {}
    for s in signals:
        votes[s.action] = votes.get(s.action, 0) + s.confidence
    
    if not votes:
        return "hold", 0.0
    
    winner = max(votes, key=votes.get)
    total = sum(votes.values())
    return winner, votes[winner] / total


def weighted_average(signals: list[AgentSignal], weights: dict[str, float]) -> dict:
    """Weighted average of signals."""
    action_weights = {}
    size_weights = {}
    total_weight = 0
    
    for s in signals:
        w = weights.get(s.metadata.get("agent_role", ""), 1.0) * s.confidence
        action_weights[s.action] = action_weights.get(s.action, 0) + w
        size_weights[s.action] = size_weights.get(s.action, 0) + s.size_pct * w
        total_weight += w
    
    if total_weight == 0:
        return {"action": "hold", "confidence": 0, "size_pct": 0}
    
    best_action = max(action_weights, key=action_weights.get)
    
    return {
        "action": best_action,
        "confidence": action_weights[best_action] / total_weight,
        "size_pct": size_weights[best_action] / action_weights[best_action] if action_weights[best_action] > 0 else 0,
    }