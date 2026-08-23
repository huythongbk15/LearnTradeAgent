"""Single application service for every capital-changing execution path.

The service is deliberately thin: the planner owns sizing, the permission
policy owns the final pre-authorization decision, the lifecycle owns durable
state, and the gateway owns broker I/O.  Runtime callers must not reproduce
this orchestration independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from trading_agent.execution.canonical.adapters import (
    BrokerSubmitFact,
    BrokerSubmitState,
)
from trading_agent.execution.canonical.broker_gateway import (
    BrokerGateway,
    BrokerSubmitResult,
    ProtectiveSubmitResult,
)
from trading_agent.execution.canonical.market_observation import (
    EnrichedMarketObservation,
)
from trading_agent.execution.canonical.order_planner import (
    CurrentPortfolioState,
    MarketPrice,
    OrderPlanner,
    OrderPlanningResult,
    OrderPlanningStatus,
)
from trading_agent.execution.canonical.risk_decision import UnifiedRiskDecision
from trading_agent.execution.lifecycle.events import ExecutionEvent
from trading_agent.execution.lifecycle.lifecycle import (
    EmergencyReduceRequest,
    ExecutionLifecycle,
)
from trading_agent.execution.permission import (
    PermissionContext,
    PermissionResult,
    evaluate_order_permission,
)
from trading_agent.research.forecast import TargetExposure


_BROKER_ACCEPTED_STATES = frozenset(
    {
        BrokerSubmitState.ACCEPTED,
        BrokerSubmitState.OPEN,
        BrokerSubmitState.PARTIALLY_FILLED,
        BrokerSubmitState.FILLED,
    }
)


class ExecutionBlockedError(RuntimeError):
    """Raised when canonical planning or permission refuses an order."""


@dataclass(frozen=True)
class ExecutionSubmission:
    """Broker fact and the durable lifecycle event derived from that fact."""

    intent_id: str
    result: BrokerSubmitResult
    broker_event: ExecutionEvent | None


class CanonicalExecutionService:
    """The one orchestration service used by all runtime execution callers."""

    def __init__(
        self,
        *,
        lifecycle: ExecutionLifecycle,
        gateway: BrokerGateway,
        planner: OrderPlanner | None = None,
    ) -> None:
        if not isinstance(lifecycle, ExecutionLifecycle):
            raise TypeError("lifecycle must be an ExecutionLifecycle")
        if not isinstance(gateway, BrokerGateway):
            raise TypeError("gateway must be a BrokerGateway")
        if planner is not None and not isinstance(planner, OrderPlanner):
            raise TypeError("planner must be an OrderPlanner")
        self.lifecycle = lifecycle
        self.gateway = gateway
        self.planner = planner
        # Crash recovery is mandatory at the application boundary.  Failure to
        # replay a corrupt/unmigrated log propagates and blocks broker I/O.
        self.lifecycle.load()

    def plan(
        self,
        *,
        target: TargetExposure,
        risk_decision: UnifiedRiskDecision,
        observation: EnrichedMarketObservation,
        portfolio: CurrentPortfolioState,
        price: MarketPrice,
        existing_reservations: float = 0.0,
    ) -> OrderPlanningResult:
        """Plan an order from canonical, provenance-bound pipeline inputs."""

        if self.planner is None:
            raise ExecutionBlockedError("no canonical OrderPlanner is configured")
        return self.planner.plan(
            target=target,
            risk_decision=risk_decision,
            observation=observation,
            portfolio=portfolio,
            price=price,
            existing_reservations=existing_reservations,
        )

    @staticmethod
    def evaluate_permission(context: PermissionContext) -> PermissionResult:
        """Evaluate the authoritative final permission policy."""

        return evaluate_order_permission(context)

    def submit_planned(
        self,
        *,
        planning: OrderPlanningResult,
        risk_decision: UnifiedRiskDecision,
        permission_context: PermissionContext,
        metadata: dict[str, Any] | None = None,
        correlation_id: str | None = None,
    ) -> ExecutionSubmission:
        """Authorize and submit one planner-produced intent durably."""

        if planning.status is not OrderPlanningStatus.ORDER_REQUIRED:
            raise ExecutionBlockedError(
                f"planner status {planning.status.value} cannot be submitted"
            )
        intent = planning.intent
        if intent is None:
            raise ExecutionBlockedError("planner returned no order intent")
        if permission_context.risk_decision is not risk_decision:
            raise ExecutionBlockedError(
                "permission context must carry the exact planned risk decision"
            )
        if permission_context.order_side.strip().lower() != intent.side:
            raise ExecutionBlockedError("permission side does not match planned intent")
        if abs(permission_context.order_size - intent.quantity) > 1e-12:
            raise ExecutionBlockedError("permission size does not match planned intent")

        permission = self.evaluate_permission(permission_context)
        if not permission.allowed():
            raise ExecutionBlockedError(
                f"order permission blocked: {permission.reason.value}: "
                f"{permission.detail}"
            )

        self.lifecycle.create_order_intent(
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            size=intent.quantity,
            idempotency_key=intent.idempotency_key,
        )
        self.lifecycle.approve_risk(
            intent_id=intent.intent_id,
            risk_decision=risk_decision,
        )
        authorization = self.lifecycle.authorize_order(
            intent_id=intent.intent_id,
            idempotency_key=intent.idempotency_key,
            metadata=metadata,
        )
        self.lifecycle.request_broker_submission(
            intent_id=intent.intent_id,
            claimed_by=intent.intent_id,
        )
        return self._submit_authorization(
            intent_id=intent.intent_id,
            authorization_id=str(authorization.payload["authorization_id"]),
            correlation_id=correlation_id or intent.intent_id,
        )

    def emergency_close(
        self,
        request: EmergencyReduceRequest,
        *,
        correlation_id: str | None = None,
    ) -> ExecutionSubmission:
        """Submit a proven long-only reduction through the same durable path."""

        authorization = self.lifecycle.emergency_reduce(request)
        return self._submit_authorization(
            intent_id=request.intent_id,
            authorization_id=str(authorization.payload["authorization_id"]),
            correlation_id=correlation_id or request.intent_id,
        )

    def emergency_protection(
        self,
        request: EmergencyReduceRequest,
        *,
        correlation_id: str | None = None,
    ) -> ProtectiveSubmitResult:
        """Place a lifecycle-authorized protective reduction and record its fact."""

        authorization = self.lifecycle.emergency_reduce(request)
        result = self.gateway.submit_protection(
            str(authorization.payload["authorization_id"]),
            correlation_id=correlation_id or request.intent_id,
        )
        if result.submission is not None:
            self.record_broker_fact(request.intent_id, result.submission)
        return result

    def record_broker_fact(
        self,
        intent_id: str,
        result: BrokerSubmitResult,
    ) -> ExecutionEvent | None:
        """Record a typed broker fact and its broker order identity."""

        if (
            result.broker_order_id
            and result.state in _BROKER_ACCEPTED_STATES
            and not self._has_exchange_order_id(intent_id)
        ):
            self.lifecycle.submit_order(
                intent_id=intent_id,
                exchange_order_id=result.broker_order_id,
            )
        return self.lifecycle.record_broker_submit_result(
            intent_id,
            self._as_broker_fact(intent_id, result),
        )

    def reconcile(
        self,
        *,
        intent_id: str,
        broker_fact: BrokerSubmitResult,
    ) -> ExecutionSubmission:
        """Apply a typed fact obtained by client-ID reconciliation."""

        event = self.lifecycle.record_reconciled_broker_submit_result(
            intent_id,
            self._as_broker_fact(intent_id, broker_fact),
        )
        return ExecutionSubmission(intent_id, broker_fact, event)

    def _submit_authorization(
        self,
        *,
        intent_id: str,
        authorization_id: str,
        correlation_id: str,
    ) -> ExecutionSubmission:
        result = self.gateway.submit(
            authorization_id,
            correlation_id=correlation_id,
        )
        event = self.record_broker_fact(intent_id, result)
        return ExecutionSubmission(intent_id, result, event)

    def _has_exchange_order_id(self, intent_id: str) -> bool:
        order = self.lifecycle.state.order(intent_id)
        return bool(order is not None and order.exchange_order_id)

    @staticmethod
    def _as_broker_fact(
        intent_id: str,
        result: BrokerSubmitResult,
    ) -> BrokerSubmitFact:
        raw = dict(result.raw_response or {})
        return BrokerSubmitFact(
            state=result.state or BrokerSubmitState.UNKNOWN,
            broker_order_id=result.broker_order_id,
            client_order_id=str(raw.get("client_order_id") or intent_id),
            venue=result.venue,
            broker_status=result.broker_status,
            observed_at=result.observed_at or datetime.now(UTC),
            error=result.error,
            raw_response=raw,
        )


__all__ = [
    "CanonicalExecutionService",
    "ExecutionBlockedError",
    "ExecutionSubmission",
]
