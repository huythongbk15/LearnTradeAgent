"""
ExecutionAuthority — Validates Intent → Lifecycle → BrokerGateway.

This is the THIRD authority in the chain. It is the FINAL GATE before any
order reaches the broker. No order can be submitted without passing here.

Responsibilities:
1. Validate Intent against InstrumentRules (qty step, precision, notional)
2. Validate Intent against PermissionContext (health, freshness, inventory)
3. Claim intent in ExecutionLifecycle (atomic, fail-closed)
4. Submit via BrokerGateway (PaperExecutionAdapter or live adapter)
5. Emit causation link for audit
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from trading_agent.authority.causation import CausationChain
from trading_agent.authority.config import AuthorityConfig, get_authority_config
from trading_agent.execution.canonical import (
    BrokerGateway,
    EnrichedMarketObservation,
    InstrumentRules,
    OrderIntent,
    OrderPlanner,
    ProtectionPlan,
)
from trading_agent.execution.canonical.broker_gateway import BrokerSubmitResult
from trading_agent.execution.canonical.order_planner import OrderPlanningStatus
from trading_agent.authority.decision import TargetExposure
from trading_agent.execution.lifecycle import ExecutionLifecycle
from trading_agent.execution.lifecycle.lifecycle import IntentStatus
from trading_agent.execution.permission import PermissionContext, evaluate_order_permission

logger = logging.getLogger(__name__)


# ── Input / Output ─────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ExecutionValidationInput:
    """Input to ExecutionAuthority."""

    intent: OrderIntent
    observation: EnrichedMarketObservation
    portfolio_state: Any  # CurrentPortfolioState (avoid circular import)
    price: Any  # MarketPrice
    instrument_rules: InstrumentRules
    existing_reservations: float = 0.0
    causation_chain: CausationChain | None = None


@dataclass(frozen=True, slots=True)
class ExecutionValidationOutput:
    """Output from ExecutionAuthority."""

    allowed: bool
    intent_status: IntentStatus
    broker_result: BrokerSubmitResult | None
    causation_chain: CausationChain
    reason: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ── ExecutionAuthority ─────────────────────────────────────────────────


class ExecutionAuthority:
    """
    Authority 3: Intent → Lifecycle → BrokerGateway (fail-closed).

    This is the EXCLUSIVE path to order submission. No exposure-increasing
    order can bypass this authority.

    Flow:
    1. Validate Intent against InstrumentRules (hard constraints)
    2. Build PermissionContext and evaluate (health, freshness, inventory)
    3. Claim intent in ExecutionLifecycle (atomic, prevents double-submit)
    4. Submit via BrokerGateway (adapter handles broker specifics)
    5. Record full causation chain
    """

    def __init__(
        self,
        lifecycle: ExecutionLifecycle,
        gateway: BrokerGateway,
        planner: OrderPlanner,
        config: AuthorityConfig | None = None,
    ):
        self.lifecycle = lifecycle
        self.gateway = gateway
        self.planner = planner
        self.config = config or get_authority_config()

    def execute(self, input_: ExecutionValidationInput) -> ExecutionValidationOutput:
        """Main execution entry point — fail-closed."""

        chain = input_.causation_chain
        if chain is None:
            from trading_agent.authority.causation import new_chain

            chain = new_chain(
                {
                    "authority": "ExecutionAuthority",
                    "symbol": input_.intent.symbol,
                    "side": input_.intent.side,
                    "quantity": input_.intent.quantity,
                }
            )

        try:
            # 1. Validate Intent against InstrumentRules
            rules_check = self._validate_instrument_rules(input_)
            if not rules_check[0]:
                return self._deny(chain, input_, rules_check[1], rules_check[2])

            # 2. Validate PermissionContext
            perm_check = self._validate_permission(input_)
            if not perm_check[0]:
                return self._deny(chain, input_, perm_check[1], perm_check[2])

            # 3. Claim intent in lifecycle (atomic)
            claim_result = self.lifecycle.claim_intent(
                intent=input_.intent,
                observation=input_.observation,
                portfolio_state=input_.portfolio_state,
                price=input_.price,
            )
            if claim_result.status != IntentStatus.CLAIMED:
                return self._deny(
                    chain,
                    input_,
                    f"Intent claim failed: {claim_result.status.value}",
                    ("claim_failed",),
                )

            # 4. Submit via BrokerGateway
            broker_result = self.gateway.submit(claim_result.intent_id)

            # 5. Build causation chain
            chain = chain.append(
                authority="ExecutionAuthority",
                inputs={
                    "intent_id": claim_result.intent_id,
                    "symbol": input_.intent.symbol,
                    "side": input_.intent.side,
                    "quantity": input_.intent.quantity,
                    "limit_price": input_.intent.limit_price,
                    "protection": (
                        {
                            "stop_loss": input_.intent.protection.stop_loss if input_.intent.protection else None,
                            "take_profit": input_.intent.protection.take_profit if input_.intent.protection else None,
                        }
                        if input_.intent.protection
                        else None
                    ),
                },
                outputs={
                    "claim_status": claim_result.status.value,
                    "broker_state": broker_result.submit_fact.state.value if broker_result.submit_fact else "none",
                    "broker_order_id": broker_result.submit_fact.order_id if broker_result.submit_fact else None,
                },
            )

            return ExecutionValidationOutput(
                allowed=True,
                intent_status=claim_result.status,
                broker_result=broker_result,
                causation_chain=chain,
                reason="Order submitted successfully",
                warnings=(),
            )

        except Exception as e:
            logger.error(f"ExecutionAuthority failed: {e}", exc_info=True)
            return self._deny(chain, input_, f"Internal error: {e}", ("internal_error",))

    # ── Validation: InstrumentRules ────────────────────────────────────

    def _validate_instrument_rules(
        self, input_: ExecutionValidationInput
    ) -> tuple[bool, str, tuple[str, ...]]:
        """Hard constraints from InstrumentRules — fail if violated."""
        rules = input_.instrument_rules
        intent = input_.intent
        warnings = []

        # Quantity step
        if intent.quantity < rules.min_order_qty - 1e-12:
            return (
                False,
                f"quantity {intent.quantity} < min_order_qty {rules.min_order_qty}",
                ("qty_below_min",),
            )

        # Max order qty
        if intent.quantity > rules.max_order_qty + 1e-12:
            return (
                False,
                f"quantity {intent.quantity} > max_order_qty {rules.max_order_qty}",
                ("qty_above_max",),
            )

        # Quantity step alignment
        remainder = intent.quantity % rules.qty_step
        if remainder > 1e-12 and abs(remainder - rules.qty_step) > 1e-12:
            return (
                False,
                f"quantity {intent.quantity} not aligned to qty_step {rules.qty_step}",
                ("qty_step_misaligned",),
            )

        # Price precision
        if intent.limit_price is not None:
            price_str = f"{intent.limit_price:.{rules.price_precision}f}"
            if abs(float(price_str) - intent.limit_price) > 1e-12:
                warnings.append(f"limit_price rounded to {rules.price_precision} decimals")

        # Notional limits
        notional = intent.quantity * (intent.limit_price or input_.price.mid)
        if notional < rules.min_notional - 1e-9:
            return (
                False,
                f"notional {notional:.2f} < min_notional {rules.min_notional}",
                ("notional_below_min",),
            )
        if notional > rules.max_notional + 1e-9:
            return (
                False,
                f"notional {notional:.2f} > max_notional {rules.max_notional}",
                ("notional_above_max",),
            )

        # Spot long-only
        if rules.spot_long_only and intent.side == "sell":
            # Allow sell only if reducing existing position (checked in permission)
            pass  # PermissionContext handles this

        return True, "", tuple(warnings)

    # ── Validation: PermissionContext ──────────────────────────────────

    def _validate_permission(
        self, input_: ExecutionValidationInput
    ) -> tuple[bool, str, tuple[str, ...]]:
        """Evaluate PermissionContext — fail if denied."""

        intent = input_.intent
        obs = input_.observation
        portfolio = input_.portfolio_state
        price = input_.price

        # Build trusted price from observation
        from trading_agent.execution.lifecycle import TrustedPrice

        trusted_price = TrustedPrice(
            price=price.mid,
            exchange_timestamp=obs.timestamp if hasattr(obs, "timestamp") else datetime.now(UTC),
            received_at=datetime.now(UTC),
        )

        # Determine exposure effect
        from trading_agent.execution.lifecycle.events import ExposureEffect

        exposure_effect = (
            ExposureEffect.INCREASE if intent.side == "buy" else ExposureEffect.REDUCE
        )

        # Build permission context
        perm_ctx = PermissionContext(
            execution_health=self.lifecycle.state.execution_health,
            exposure_effect=exposure_effect,
            risk_decision=None,  # Already validated upstream
            trusted_price=trusted_price,
            max_price_age_seconds=self.config.execution.max_price_age_seconds,
            reconciliation_state=self.lifecycle.state.reconciliation.value,
            protection_state=self.lifecycle.state.protection_state.get(
                intent.symbol, None
            ).value
            if self.lifecycle.state.protection_state.get(intent.symbol)
            else "none",
            manual_blocked=self.lifecycle.state.manual_blocked,
            kill_switch_active=False,  # TODO: wire from config
            data_trust="trusted",
            inventory_state="known",
            free_inventory=portfolio.available_cash if intent.side == "buy" else portfolio.available_quantity,
            authorized_sellable_inventory=portfolio.available_quantity,
            order_size=intent.quantity,
            order_side=intent.side,
            require_fresh_market_data=True,
            enforce_inventory=True,
            broker_state=None,
            draft=False,
        )

        permission = evaluate_order_permission(perm_ctx)

        if not permission.allowed:
            return False, permission.reason, (permission.reason,)

        return True, "", ()

    # ── Deny helper ────────────────────────────────────────────────────

    def _deny(
        self,
        chain: CausationChain,
        input_: ExecutionValidationInput,
        reason: str,
        warnings: tuple[str, ...],
    ) -> ExecutionValidationOutput:
        """Construct deny output."""

        chain = chain.append(
            authority="ExecutionAuthority",
            inputs={
                "intent_symbol": input_.intent.symbol,
                "intent_side": input_.intent.side,
                "intent_qty": input_.intent.quantity,
            },
            outputs={
                "allowed": False,
                "reason": reason,
            },
            metadata={"denied": True},
        )

        return ExecutionValidationOutput(
            allowed=False,
            intent_status=IntentStatus.PENDING,
            broker_result=None,
            causation_chain=chain,
            reason=reason,
            warnings=warnings,
        )


__all__ = [
    "ExecutionAuthority",
    "ExecutionValidationInput",
    "ExecutionValidationOutput",
]