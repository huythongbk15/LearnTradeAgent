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

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from trading_agent.execution.canonical import (
    EvidenceState,
    UnifiedRiskDecision,
)
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
    NORMAL = "normal"
    REDUCE_ONLY = "reduce_only"
    KILL_SWITCH_INCREASE = "kill_switch_increase"
    KILL_SWITCH_NEUTRAL = "kill_switch_neutral"
    MANUAL_BLOCKED = "manual_blocked"
    PROTECTION_GAP = "protection_gap"
    STALE_MARKET_DATA = "stale_market_data"
    RECONCILIATION_UNRESOLVED = "reconciliation_unresolved"
    HIGH_RISK_NEW_EXPOSURE = "high_risk_new_exposure"
    INSUFFICIENT_INVENTORY = "insufficient_inventory"
    UNKNOWN_INVENTORY_STATE = "unknown_inventory_state"
    UNKNOWN_BROKER_STATE = "unknown_broker_state"
    INVALID_ORDER = "invalid_order"
    MISSING_RISK_DECISION = "missing_risk_decision"
    MISSING_CALIBRATION_EVIDENCE = "missing_calibration_evidence"
    MISSING_OOD_EVIDENCE = "missing_ood_evidence"
    MISSING_REGIME_EVIDENCE = "missing_regime_evidence"
    STALE_RISK_EVIDENCE = "stale_risk_evidence"


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
    risk_decision: UnifiedRiskDecision | None = None
    trusted_price: TrustedPrice | None = None
    max_price_age_seconds: float = 60.0
    reconciliation_state: str = "none"
    protection_state: str = "none"
    manual_blocked: bool = False
    kill_switch_active: bool = False
    data_trust: str = "trusted"
    inventory_state: str = "known"
    free_inventory: float = 0.0
    authorized_sellable_inventory: float | None = None
    order_size: float = 0.0
    order_side: str = "buy"
    require_fresh_market_data: bool = True
    enforce_inventory: bool = True
    broker_state: str | None = None
    known_broker_states: Sequence[str] = (
        "open",
        "closed",
        "canceled",
        "rejected",
        "partial",
    )
    draft: bool = False  # True for intent creation (before risk approval)


def evaluate_order_permission(ctx: PermissionContext) -> PermissionResult:
    """Return one authoritative, fail-closed order permission decision."""

    side = ctx.order_side.strip().lower()
    if (
        side not in {"buy", "sell"}
        or not math.isfinite(ctx.order_size)
        or ctx.order_size <= 0
    ):
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.INVALID_ORDER,
            "order side must be buy|sell and size must be finite and positive",
        )

    if ctx.broker_state is not None and ctx.broker_state not in ctx.known_broker_states:
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.UNKNOWN_BROKER_STATE,
            f"unknown broker state '{ctx.broker_state}'",
        )

    inventory_known = ctx.inventory_state.strip().lower() == "known"
    if (
        ctx.enforce_inventory
        and ctx.exposure_effect == ExposureEffect.REDUCE
        and not inventory_known
    ):
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.UNKNOWN_INVENTORY_STATE,
            f"inventory state is {ctx.inventory_state!r}",
        )

    authorized = (
        ctx.free_inventory
        if ctx.authorized_sellable_inventory is None
        else ctx.authorized_sellable_inventory
    )
    if (
        ctx.enforce_inventory
        and side == "sell"
        and (
            not inventory_known
            or not math.isfinite(authorized)
            or ctx.order_size > authorized + 1e-9
        )
    ):
        reason = (
            PermissionReason.UNKNOWN_INVENTORY_STATE
            if not inventory_known or not math.isfinite(authorized)
            else PermissionReason.INSUFFICIENT_INVENTORY
        )
        return PermissionResult(
            OrderPermission.BLOCK,
            reason,
            f"sell {ctx.order_size} > authorized sellable inventory {authorized}",
        )

    safe_reduce = ctx.exposure_effect == ExposureEffect.REDUCE and inventory_known

    def degraded(reason: PermissionReason, detail: str) -> PermissionResult:
        if safe_reduce:
            return PermissionResult(OrderPermission.REDUCE_ONLY, reason, detail)
        return PermissionResult(OrderPermission.BLOCK, reason, detail)

    if ctx.manual_blocked or ctx.execution_health == ExecutionHealth.MANUAL_BLOCKED:
        return degraded(
            PermissionReason.MANUAL_BLOCKED, "manual intervention unresolved"
        )

    if (
        ctx.execution_health == ExecutionHealth.PROTECTION_GAP
        or ctx.protection_state in {"protection_gap", "protection_required", "unknown"}
    ):
        return degraded(PermissionReason.PROTECTION_GAP, "protection gap active")

    if ctx.require_fresh_market_data:
        price = ctx.trusted_price
        price_untrusted = (
            ctx.data_trust.strip().lower() != "trusted"
            or price is None
            or not isinstance(price, TrustedPrice)
            or not math.isfinite(price.price)
            or price.price <= 0
            or not price.is_fresh(ctx.max_price_age_seconds)
        )
        if price_untrusted:
            return degraded(
                PermissionReason.STALE_MARKET_DATA,
                "no trusted finite fresh price",
            )

    if (
        ctx.reconciliation_state == "started"
        or ctx.execution_health == ExecutionHealth.RECONCILING
    ):
        return degraded(
            PermissionReason.RECONCILIATION_UNRESOLVED,
            "reconciliation in progress",
        )

    if ctx.kill_switch_active:
        reason = (
            PermissionReason.KILL_SWITCH_NEUTRAL
            if ctx.exposure_effect == ExposureEffect.NEUTRAL
            else PermissionReason.KILL_SWITCH_INCREASE
        )
        return degraded(reason, "kill switch active")

    risk = ctx.risk_decision

    # Missing risk decision → BLOCK for INCREASE/NEUTRAL (fail-closed)
    # UNLESS draft=True (intent creation before risk approval)
    if ctx.exposure_effect in (ExposureEffect.INCREASE, ExposureEffect.NEUTRAL):
        if risk is None:
            if ctx.draft:
                # Draft mode: allow intent creation, submission will require risk
                pass
            else:
                reason = (
                    PermissionReason.MISSING_RISK_DECISION
                    if ctx.exposure_effect == ExposureEffect.INCREASE
                    else PermissionReason.KILL_SWITCH_NEUTRAL
                )
                detail = (
                    "no risk decision available for exposure increase"
                    if ctx.exposure_effect == ExposureEffect.INCREASE
                    else "no risk decision available for neutral exposure"
                )
                return PermissionResult(OrderPermission.BLOCK, reason, detail)
        else:
            if ctx.exposure_effect == ExposureEffect.INCREASE and (
                risk.allowed_target_exposure <= 1e-12
                or risk.max_new_exposure <= 1e-12
                or risk.reduce_only
            ):
                return PermissionResult(
                    OrderPermission.BLOCK,
                    PermissionReason.HIGH_RISK_NEW_EXPOSURE,
                    f"risk_level={risk.risk_level.value} max_new_exposure={risk.max_new_exposure} reduce_only={risk.reduce_only} blocks new exposure",
                )
            if ctx.exposure_effect == ExposureEffect.NEUTRAL and (
                risk.allowed_target_exposure <= 1e-12 or risk.max_new_exposure <= 1e-12
            ):
                # Neutral exposure is blocked only when risk params explicitly
                # forbid any exposure, NOT merely because reduce_only is set.
                return PermissionResult(
                    OrderPermission.BLOCK,
                    PermissionReason.KILL_SWITCH_NEUTRAL,
                    "neutral exposure not allowed under current risk decision",
                )

        # Evidence fail-closed for INCREASE: all evidence must be KNOWN
        if risk is not None and ctx.exposure_effect == ExposureEffect.INCREASE:
            if risk.calibration_state is not EvidenceState.KNOWN:
                return PermissionResult(
                    OrderPermission.BLOCK,
                    PermissionReason.MISSING_CALIBRATION_EVIDENCE,
                    f"calibration_state={risk.calibration_state.value}",
                )
            if risk.ood_state is not EvidenceState.KNOWN:
                return PermissionResult(
                    OrderPermission.BLOCK,
                    PermissionReason.MISSING_OOD_EVIDENCE,
                    f"ood_state={risk.ood_state.value}",
                )
            if risk.regime_state is not EvidenceState.KNOWN:
                return PermissionResult(
                    OrderPermission.BLOCK,
                    PermissionReason.MISSING_REGIME_EVIDENCE,
                    f"regime_state={risk.regime_state.value}",
                )

    if safe_reduce:
        return PermissionResult(
            OrderPermission.REDUCE_ONLY,
            PermissionReason.REDUCE_ONLY,
            "reduce-only order permitted",
        )

    if risk is not None and risk.reduce_only:
        return PermissionResult(
            OrderPermission.BLOCK,
            PermissionReason.HIGH_RISK_NEW_EXPOSURE,
            "risk decision requires a provably reducing order",
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
