"""ProtectionPlan — stop/take-profit plan generated before submission/fill.

Protection orders are generated BEFORE submission where possible and tracked
through a finite state machine so the execution pipeline knows exactly when
a position is protected.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ProtectionState(str, Enum):
    """Lifecycle states for a protection plan."""

    NONE = "NONE"
    PROTECTION_REQUIRED = "PROTECTION_REQUIRED"
    PROTECTIVE_SUBMITTING = "PROTECTIVE_SUBMITTING"
    PROTECTIVE_ACKNOWLEDGED = "PROTECTIVE_ACKNOWLEDGED"
    PROTECTED = "PROTECTED"


class ProtectionStatus(str, Enum):
    """Operational status of the protective order on the broker."""

    ABSENT = "absent"
    PENDING = "pending"
    ACTIVE = "active"
    TRIGGERED = "triggered"
    CANCELED = "canceled"
    FAILED = "failed"


class ProtectionQuantityMode(str, Enum):
    """How protective order quantity is specified."""

    EXPLICIT_QUANTITY = "EXPLICIT_QUANTITY"
    CLOSE_POSITION = "CLOSE_POSITION"


@dataclass(frozen=True)
class ProtectionPlan:
    """Stop/take-profit plan attached to an order intent.

    Generated BEFORE submission/fill where possible.  The BrokerGateway is
    the ONLY component that may submit protective orders.
    """

    # ── Identity ────────────────────────────────────────────────────────
    plan_id: str
    model_risk_decision_id: str  # links to risk decision
    symbol: str

    # ── Protection rules ────────────────────────────────────────────────
    stop_type: str  # "stop_loss" | "trailing_stop" | "stop_limit"
    stop_trigger: float | None = None  # price trigger
    take_profit: float | None = None
    trailing_rule: dict[str, Any] | None = (
        None  # {"activation_pct": ..., "trail_pct": ...}
    )

    # ── State machine ───────────────────────────────────────────────────
    state: ProtectionState = ProtectionState.NONE
    status: ProtectionStatus = ProtectionStatus.ABSENT
    broker_order_id: str | None = None

    # ── Quantity semantics ──────────────────────────────────────────────
    quantity_mode: ProtectionQuantityMode = ProtectionQuantityMode.EXPLICIT_QUANTITY
    protected_quantity: float = 0.0  # required when mode == EXPLICIT_QUANTITY

    # ── Audit ───────────────────────────────────────────────────────────
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.plan_id:
            raise ValueError("plan_id is required")
        if not self.model_risk_decision_id:
            raise ValueError("model_risk_decision_id is required")
        if not self.symbol:
            raise ValueError("symbol is required")
        if not self.stop_type:
            raise ValueError("stop_type is required")
        if self.stop_trigger is not None and self.stop_trigger <= 0.0:
            raise ValueError("stop_trigger must be positive when set")
        if self.take_profit is not None and self.take_profit <= 0.0:
            raise ValueError("take_profit must be positive when set")
        if self.quantity_mode == ProtectionQuantityMode.EXPLICIT_QUANTITY:
            if not math.isfinite(self.protected_quantity) or self.protected_quantity <= 0:
                raise ValueError(
                    "EXPLICIT_QUANTITY requires protected_quantity > 0"
                )

    def with_state(self, state: ProtectionState) -> ProtectionPlan:
        """Return a new plan with updated state."""
        return ProtectionPlan(
            plan_id=self.plan_id,
            model_risk_decision_id=self.model_risk_decision_id,
            symbol=self.symbol,
            stop_type=self.stop_type,
            stop_trigger=self.stop_trigger,
            take_profit=self.take_profit,
            trailing_rule=self.trailing_rule,
            state=state,
            status=self.status,
            broker_order_id=self.broker_order_id,
            quantity_mode=self.quantity_mode,
            protected_quantity=self.protected_quantity,
            created_at=self.created_at,
            updated_at=datetime.now(UTC),
            metadata=self.metadata,
        )

    def with_broker_order(self, broker_order_id: str) -> ProtectionPlan:
        """Return a new plan with broker order id set."""
        return ProtectionPlan(
            plan_id=self.plan_id,
            model_risk_decision_id=self.model_risk_decision_id,
            symbol=self.symbol,
            stop_type=self.stop_type,
            stop_trigger=self.stop_trigger,
            take_profit=self.take_profit,
            trailing_rule=self.trailing_rule,
            state=ProtectionState.PROTECTIVE_ACKNOWLEDGED,
            status=ProtectionStatus.ACTIVE,
            broker_order_id=broker_order_id,
            quantity_mode=self.quantity_mode,
            protected_quantity=self.protected_quantity,
            created_at=self.created_at,
            updated_at=datetime.now(UTC),
            metadata=self.metadata,
        )


__all__ = [
    "ProtectionState",
    "ProtectionStatus",
    "ProtectionPlan",
]
