"""Causation chain — trace propagation across the execution pipeline.

Every execution event carries ``correlation_id`` and ``causation_id`` so
the full end-to-end path can be reconstructed:

    observation_id -> forecast_fingerprint -> risk_decision_id
        -> target_exposure -> order_intent_id -> broker_order_id
        -> fill -> protective_order
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True)
class TraceContext:
    """Immutable tracing metadata attached to every execution event."""

    correlation_id: str
    causation_id: str | None = None
    parent_event_id: str | None = None

    def child(self, parent_event_id: str) -> TraceContext:
        """Derive a child trace context for a downstream event."""
        return TraceContext(
            correlation_id=self.correlation_id,
            causation_id=parent_event_id,
            parent_event_id=parent_event_id,
        )


@dataclass(frozen=True)
class CausationChain:
    """End-to-end execution trace.

    The chain links every canonical identifier produced by the pipeline so
    auditors can replay the full decision path in order.
    """

    correlation_id: str
    observation_id: str
    forecast_fingerprint: str
    risk_decision_id: str
    target_exposure_id: str
    order_intent_id: str
    broker_order_id: str | None = None
    fill_event_id: str | None = None
    protective_order_id: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_audit_trail(self) -> list[tuple[str, str]]:
        """Return ordered (key, value) pairs for serialization / display."""
        return [
            ("correlation_id", self.correlation_id),
            ("observation_id", self.observation_id),
            ("forecast_fingerprint", self.forecast_fingerprint),
            ("risk_decision_id", self.risk_decision_id),
            ("target_exposure_id", self.target_exposure_id),
            ("order_intent_id", self.order_intent_id),
            ("broker_order_id", self.broker_order_id or ""),
            ("fill_event_id", self.fill_event_id or ""),
            ("protective_order_id", self.protective_order_id or ""),
        ]


def propagate_causation(
    chain: CausationChain,
    *,
    broker_order_id: str | None = None,
    fill_event_id: str | None = None,
    protective_order_id: str | None = None,
) -> CausationChain:
    """Return a new chain with downstream identifiers filled in."""
    return CausationChain(
        correlation_id=chain.correlation_id,
        observation_id=chain.observation_id,
        forecast_fingerprint=chain.forecast_fingerprint,
        risk_decision_id=chain.risk_decision_id,
        target_exposure_id=chain.target_exposure_id,
        order_intent_id=chain.order_intent_id,
        broker_order_id=broker_order_id or chain.broker_order_id,
        fill_event_id=fill_event_id or chain.fill_event_id,
        protective_order_id=protective_order_id or chain.protective_order_id,
        created_at=chain.created_at,
        metadata=chain.metadata,
    )


__all__ = ["TraceContext", "CausationChain", "propagate_causation"]
