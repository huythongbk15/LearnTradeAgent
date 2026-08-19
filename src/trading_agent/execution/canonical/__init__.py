"""Canonical execution pipeline — unified types and invariants.

This package replaces ad-hoc execution paths with a single, auditable
pipeline:

    MarketObservation -> Forecast -> RiskDecision -> TargetExposure
        -> OrderIntent -> BrokerOrder -> Fill -> ProtectiveOrder

Every capital-changing call MUST flow through :class:`BrokerGateway`.
"""

from __future__ import annotations

from trading_agent.execution.canonical.causation import (
    CausationChain,
    TraceContext,
    propagate_causation,
)
from trading_agent.execution.canonical.events import (
    ContentHash,
    DecisionKey,
    IdempotencyKeys,
    ObservationId,
    compute_decision_key,
    compute_idempotency_key,
    compute_observation_id,
    compute_target_exposure_key,
)
from trading_agent.execution.canonical.legacy_adapter import (
    LegacyDecisionAdapter,
)
from trading_agent.execution.canonical.market_observation import (
    EnrichedMarketObservation,
)
from trading_agent.execution.canonical.order_planner import (
    OrderIntent,
    OrderPlanner,
    OrderPlanningResult,
    OrderPlanningStatus,
    AdjustmentReason,
    CurrentPortfolioState,
    InstrumentRules,
    MarketPrice,
)
from trading_agent.execution.canonical.protection import (
    ProtectionPlan,
    ProtectionState,
    ProtectionStatus,
    ProtectionQuantityMode,
)
from trading_agent.execution.canonical.risk_decision import (
    RiskDecisionAdapter,
    RiskLevel,
    UnifiedRiskDecision,
    EvidenceState,
)
from trading_agent.execution.canonical.broker_gateway import (
    AuthorizedOrder,
    BrokerGateway,
    BrokerSubmitResult,
    CancelResult,
    ProtectiveSubmitResult,
    CancelState,
    CancelEvidence,
    ProtectiveAckEvidence,
    AuthorizationError,
    ExchangeAdapter,
)
from trading_agent.execution.canonical.runner_adapter import CanonicalBrokerAdapter

__all__ = [
    # Risk decision
    "UnifiedRiskDecision",
    "RiskDecisionAdapter",
    "RiskLevel",
    "EvidenceState",
    # Legacy adapter
    "LegacyDecisionAdapter",
    # Market observation
    "EnrichedMarketObservation",
    # Order planning
    "OrderPlanner",
    "OrderIntent",
    "OrderPlanningResult",
    "OrderPlanningStatus",
    "AdjustmentReason",
    "CurrentPortfolioState",
    "InstrumentRules",
    "MarketPrice",
    # Broker gateway
    "BrokerGateway",
    "AuthorizedOrder",
    "BrokerSubmitResult",
    "CancelResult",
    "ProtectiveSubmitResult",
    "CancelState",
    "CancelEvidence",
    "ProtectiveAckEvidence",
    "AuthorizationError",
    "ExchangeAdapter",
    "CanonicalBrokerAdapter",
    # Protection
    "ProtectionPlan",
    "ProtectionState",
    "ProtectionStatus",
    "ProtectionQuantityMode",
    # Idempotency / content hashes
    "ContentHash",
    "DecisionKey",
    "IdempotencyKeys",
    "ObservationId",
    "compute_observation_id",
    "compute_decision_key",
    "compute_idempotency_key",
    "compute_target_exposure_key",
    # Causation
    "TraceContext",
    "CausationChain",
    "propagate_causation",
]
