"""Event-sourced execution lifecycle (Wave C — Execution State & Resilience)."""

from trading_agent.execution.lifecycle.events import (
    EVENT_SCHEMA_VERSION,
    EventValidationError,
    ExecutionEvent,
    ExecutionEventType,
    make_event,
    validate_event,
)
from trading_agent.execution.lifecycle.lifecycle import (
    LIVE_STATUSES,
    ExecutionHealth,
    ExecutionLifecycle,
    ExposureEffect,
    IntentStatus,
    InvariantViolation,
    LifecycleError,
    LifecycleState,
    OrderState,
    ProtectionState,
    ProtectiveOrderState,
    ReconciliationState,
    TrustedPrice,
)
from trading_agent.execution.lifecycle.store import (
    ExecutionEventStore,
    SequenceGapError,
    Snapshot,
    SnapshotIntegrityError,
    snapshot_checksum,
)

__all__ = [
    "EVENT_SCHEMA_VERSION",
    "EventValidationError",
    "ExecutionEvent",
    "ExecutionEventType",
    "ExecutionEventStore",
    "ExecutionLifecycle",
    "ExposureEffect",
    "ExecutionHealth",
    "IntentStatus",
    "InvariantViolation",
    "LifecycleError",
    "LifecycleState",
    "LIVE_STATUSES",
    "OrderState",
    "ProtectiveOrderState",
    "ProtectionState",
    "ReconciliationState",
    "SequenceGapError",
    "Snapshot",
    "SnapshotIntegrityError",
    "TrustedPrice",
    "make_event",
    "snapshot_checksum",
    "validate_event",
]
