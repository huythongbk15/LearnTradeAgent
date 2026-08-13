"""Unified order permission gate.

Centralizes safety checks so every order path uses the same decision
logic instead of scattered, potentially contradicting checks.

Outputs:
- ALLOW
- REDUCE_ONLY
- BLOCK

plus deterministic reason codes for audit/observability.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from trading_agent.agents.risk_decision import RiskDecision
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionHealth,
    ExposureEffect,
    TrustedPrice,
)


class OrderPermission(str, Enum):
    ALLOW = "ALLOW"
    REDUCE_ONLY = "REDUCE_ONLY"
    BLOCK = "BLOCK"


class PermissionReason(str, Enum):
    # Normal allow
    NORMAL = "normal"
    REDUCE_ONLY = "reduce_only"
    # Blocks
    KILL_SWITCH_INCREASE = "kill_switch_increase"
    KILL_SWITCH_NEUTRAL = "kill_switch_neutral"
    MANUAL_BLOCKED = "manual_blocked"
    PROTECTION_GAP = "protection_gap"
    STALE_MARKET_DATA = "stale_market_data"
    RECONCILIATION_UNRESOLVED = "reconciliation_unresolved"
    HIGH_RISK_NEW_EXPOSURE = "high_risk_new_exposure"
    INSUFFICIENT_INVENTORY = "insufficient_inventory"
    UNKNOWN_BROKER_STATE = "unknown_broker_state"


@dataclass(frozen=True)
class PermissionResult:
    permission: OrderPermission
    reason: PermissionReason
    detail: str = ""

    def allowed(self) -> bool:
        return self.permission != OrderPermission.BLOCK

    def reduce_only(self) -> bool:
        return self.permission == OrderPermission.REDUCE_ONLY


@dataclass(frozen=True)
class PermissionContext:
    execution_health: ExecutionHealth
    exposure_effect: ExposureEffect
    risk_decision: RiskDecision | None = None
    trusted_price: TrustedPrice | None = None
    max_price_age_seconds: float = 60.0
    reconciliation_state: str = "none"
    protection_state: str = "none"
    manual_blocked: bool = False
    free_inventory: float = 0.0
    order_size: float = 0.0
    order_side: str = "buy"
    broker_state: str | None = None
    known_broker_states: Sequence[str] = (
        "open",
        "closed",
        "canceled",
        "rejected",
        "partial",
    )


def evaluate_order_permission(ctx: PermissionContext) -> PermissionResult:
    """Evaluate whether an order should be allowed, reduce-only, or blocked."""

    # 1. Manual unresolved state blocks new exposure.
    if ctx.manual_blocked or ctx.execution_health == ExecutionHealth.MANUAL_BLOCKED:
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.MANUAL_BLOCKED,
            "manual intervention unresolved",
        )

    # 2. Protection gap blocks new exposure.
    if ctx.execution_health == ExecutionHealth.PROTECTION_GAP:
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.PROTECTION_GAP,
            "protection gap active",
        )

    # 3. Stale/untrusted market data blocks any order.
    if ctx.trusted_price is None or not ctx.trusted_price.is_fresh(
        ctx.max_price_age_seconds
    ):
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.STALE_MARKET_DATA,
            "no trusted fresh price",
        )

    # 4. Reconciliation unresolved blocks new exposure.
    if ctx.reconciliation_state == "started":
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.RECONCILIATION_UNRESOLVED,
            "reconciliation in progress",
        )

    # 5. Unknown broker state -> BLOCK (never silently normalize).
    if ctx.broker_state is not None and ctx.broker_state not in ctx.known_broker_states:
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.UNKNOWN_BROKER_STATE,
            f"unknown broker state '{ctx.broker_state}'",
        )

    # 6. Risk decision enforcement.
    risk = ctx.risk_decision or RiskDecision()
    if ctx.exposure_effect == ExposureEffect.INCREASE:
        if risk.max_new_exposure_pct <= 0 or risk.reduce_only:
            return PermissionResult(
                OrderPermission.BLOCK,
                PermissionReason.HIGH_RISK_NEW_EXPOSURE,
                f"risk={risk.risk_level.value} blocks new exposure",
            )
    if ctx.exposure_effect == ExposureEffect.NEUTRAL:
        if risk.max_new_exposure_pct <= 0 or risk.reduce_only:
            return PermissionResult(
                OrderPermission.BLOCK,
                PermissionReason.KILL_SWITCH_NEUTRAL,
                "neutral exposure not allowed under current risk decision",
            )

    # 7. Inventory guard for sells.
    if ctx.order_side == "sell" and ctx.order_size > ctx.free_inventory + 1e-9:
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.INSUFFICIENT_INVENTORY,
            f"sell {ctx.order_size} > free inventory {ctx.free_inventory}",
        )

    # 8. Reduce-only policy.
    if ctx.exposure_effect == ExposureEffect.REDUCE or risk.reduce_only:
        return PermissionResult(
            OrderPermission.REDUCE_ONLY,
            PermissionReason.REDUCE_ONLY,
            "reduce-only order permitted",
        )

    return PermissionResult(
        OrderPermission.ALLOW,
        PermissionReason.NORMAL,
        "order allowed",
    )


__all__ = [
    "OrderPermission",
    "PermissionReason",
    "PermissionResult",
    "PermissionContext",
    "evaluate_order_permission",
]
