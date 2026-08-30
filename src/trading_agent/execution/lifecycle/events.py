"""Execution lifecycle events — the append-only audit vocabulary.

Wave C (Execution State & Resilience) — event-sourced execution state.

Every lifecycle transition of an order intent / order / protective order /
reconciliation is recorded as one immutable event.  State is never mutated
in place across restarts: it is reconstructed by deterministic replay of
this event log (see ``lifecycle.ExecutionLifecycle``).

The vocabulary follows the production spec:

    OrderIntentCreated, RiskApproved, OrderSubmitted, BrokerAcknowledged,
    PartialFillReceived, FillReceived, FeeBooked, CancelRequested,
    CancelConfirmed, ProtectiveOrderCreated, ProtectiveOrderReplaced,
    ReconciliationStarted, ReconciliationResolved, ManualInterventionRequired
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

EVENT_SCHEMA_VERSION = 1

# Event types that must carry a broker/exchange order reference.
_ORDER_EVENTS = {
    "ORDER_SUBMITTED",
    "ORDER_REJECTED",
    "BROKER_ACKNOWLEDGED",
    "BROKER_STATE_UNKNOWN",
    "LOCAL_SUBMISSION_FAILED",
    "PARTIAL_FILL_RECEIVED",
    "FILL_RECEIVED",
    "FEE_BOOKED",
    "CANCEL_REQUESTED",
    "CANCEL_CONFIRMED",
}


class ExecutionEventType(str, Enum):
    """The full execution lifecycle event vocabulary."""

    ORDER_INTENT_CREATED = "exec.order_intent_created"
    RISK_APPROVED = "exec.risk_approved"
    ORDER_AUTHORIZED = "exec.order_authorized"
    BROKER_SUBMISSION_REQUESTED = "exec.broker_submission_requested"
    BROKER_IO_STARTED = "exec.broker_io_started"
    ORDER_SUBMITTED = "exec.order_submitted"
    ORDER_REJECTED = "exec.order_rejected"
    BROKER_ACKNOWLEDGED = "exec.broker_acknowledged"
    BROKER_STATE_UNKNOWN = "exec.broker_state_unknown"
    BROKER_STATE_RECONCILED = "exec.broker_state_reconciled"
    LOCAL_SUBMISSION_FAILED = "exec.local_submission_failed"
    PARTIAL_FILL_RECEIVED = "exec.partial_fill_received"
    FILL_RECEIVED = "exec.fill_received"
    FEE_BOOKED = "exec.fee_booked"
    CANCEL_REQUESTED = "exec.cancel_requested"
    CANCEL_CONFIRMED = "exec.cancel_confirmed"
    PROTECTIVE_ORDER_CREATED = "exec.protective_order_created"
    PROTECTIVE_ORDER_ACKNOWLEDGED = "exec.protective_order_acknowledged"
    PROTECTIVE_ORDER_REPLACED = "exec.protective_order_replaced"
    RECONCILIATION_STARTED = "exec.reconciliation_started"
    RECONCILIATION_RESOLVED = "exec.reconciliation_resolved"
    MANUAL_INTERVENTION_REQUIRED = "exec.manual_intervention_required"


@dataclass(frozen=True)
class ExecutionEvent:
    """An immutable execution lifecycle event.

    Attributes
    ----------
    event_id:
        Globally-unique id.  Duplicate event ids are ignored on replay —
        this is the idempotency boundary (e.g. a WS duplicate).
    event_type:
        One of :class:`ExecutionEventType`.
    aggregate_id:
        The order-intent / order / protective-order this event belongs to.
    seq:
        Monotonic per-aggregate sequence.  Gaps are rejected on replay.
    occurred_at:
        When the fact happened (UTC, timezone-aware).
    schema_version:
        Event schema version for forward-compatible audits.
    correlation_id / causation_id:
        Tracing links (same correlation across a trade, causation = parent event).
    payload:
        Type-specific fields (validated by :func:`validate_event`).
    """

    event_id: str
    event_type: ExecutionEventType
    aggregate_id: str
    seq: int
    occurred_at: datetime
    schema_version: int = EVENT_SCHEMA_VERSION
    correlation_id: str | None = None
    causation_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    ingested_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    global_seq: int = 0

    def to_row(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "seq": self.seq,
            "aggregate_id": self.aggregate_id,
            "event_type": self.event_type.value,
            "schema_version": self.schema_version,
            "payload": _json_dumps(self.payload),
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "occurred_at": self.occurred_at.isoformat(),
            "ingested_at": self.ingested_at.isoformat(),
            "global_seq": self.global_seq,
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> "ExecutionEvent":
        try:
            event_type = ExecutionEventType(row["event_type"])
        except ValueError:
            raise UnknownEventTypeError(
                f"unknown event_type={row['event_type']!r} for event_id={row['event_id']!r}"
            )
        return cls(
            event_id=row["event_id"],
            seq=int(row["seq"]),
            aggregate_id=row["aggregate_id"],
            event_type=event_type,
            schema_version=int(row["schema_version"]),
            payload=_json_loads(row["payload"]),
            correlation_id=row.get("correlation_id"),
            causation_id=row.get("causation_id"),
            occurred_at=_parse_dt(row["occurred_at"]),
            ingested_at=_parse_dt(row.get("ingested_at") or row["occurred_at"]),
            global_seq=int(row.get("global_seq") or 0),
        )


def make_event(
    event_type: ExecutionEventType | str,
    aggregate_id: str,
    seq: int,
    *,
    payload: dict[str, Any] | None = None,
    occurred_at: datetime | None = None,
    correlation_id: str | None = None,
    causation_id: str | None = None,
) -> ExecutionEvent:
    """Build a validated execution event."""
    etype = (
        event_type
        if isinstance(event_type, ExecutionEventType)
        else ExecutionEventType(event_type)
    )
    event = ExecutionEvent(
        event_id=str(uuid.uuid4()),
        event_type=etype,
        aggregate_id=aggregate_id,
        seq=int(seq),
        occurred_at=occurred_at or datetime.now(UTC),
        correlation_id=correlation_id,
        causation_id=causation_id,
        payload=dict(payload or {}),
    )
    validate_event(event)
    return event


class EventValidationError(ValueError):
    """Raised when an execution event is malformed."""


class UnknownEventTypeError(ValueError):
    """Raised when an event has an unknown/unsupported event_type."""

    pass


def validate_event(event: ExecutionEvent) -> None:
    """Validate structural invariants of an execution event."""
    if event.seq < 0:
        raise EventValidationError(f"negative seq {event.seq}")
    if event.schema_version != EVENT_SCHEMA_VERSION:
        raise EventValidationError(
            f"unsupported schema version {event.schema_version} "
            f"(expected {EVENT_SCHEMA_VERSION})"
        )
    if event.occurred_at.tzinfo is None:
        raise EventValidationError("occurred_at must be timezone-aware")
    name = event.event_type.name
    if name in _ORDER_EVENTS:
        order_id = event.payload.get("order_id")
        if not order_id:
            raise EventValidationError(f"{name} requires payload['order_id']")
    if name == "PARTIAL_FILL_RECEIVED":
        _require_positive_decimal(event, "size")
        _require_positive_decimal(event, "price")
    if name == "FILL_RECEIVED":
        _require_positive_decimal(event, "size")
        _require_positive_decimal(event, "price")
    if name == "FEE_BOOKED":
        _require_positive_decimal(event, "fee")


def _require_positive_decimal(event: ExecutionEvent, key: str) -> None:
    value = event.payload.get(key)
    if value is None:
        raise EventValidationError(f"{event.event_type.name} requires payload['{key}']")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise EventValidationError(f"{key} must be numeric, got {value!r}") from exc
    if not number > 0:
        raise EventValidationError(f"{key} must be positive, got {value!r}")


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, default=str, sort_keys=True)


def _json_loads(value: str) -> dict[str, Any]:
    import json

    return json.loads(value)


def _parse_dt(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


__all__ = [
    "EVENT_SCHEMA_VERSION",
    "ExecutionEventType",
    "ExecutionEvent",
    "EventValidationError",
    "make_event",
    "validate_event",
]
