"""Event-sourced execution lifecycle (Wave C — Execution State & Resilience)."""

from trading_agent.execution.lifecycle.events import (
    EVENT_SCHEMA_VERSION,
    EventValidationError,
    ExecutionEvent,
    ExecutionEventType,
    make_event,
    validate_event,
)
from trading_agent.execution.lifecycle.store import (
    ExecutionEventStore,
    SequenceGapError,
    Snapshot,
    SnapshotIntegrityError,
    snapshot_checksum,
)
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle,
    IntentStatus,
    InvariantViolation,
    LifecycleError,
    LifecycleState,
    LIVE_STATUSES,
    OrderState,
    ProtectiveOrderState,
    ReconciliationState,
)

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "EventValidationError",
    "ExecutionEvent",
    "ExecutionEventType",
    "ExecutionEventStore",
    "ExecutionLifecycle",
    "IntentStatus",
    "InvariantViolation",
    "LifecycleError",
    "LifecycleState",
    "LIVE_STATUSES",
    "OrderState",
    "ProtectiveOrderState",
    "ReconciliationState",
    "SequenceGapError",
    "Snapshot",
    "SnapshotIntegrityError",
    "make_event",
    "snapshot_checksum",
    "validate_event",
]
