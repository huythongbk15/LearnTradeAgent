"""Adversarial contracts for authoritative permission and SELL reservations."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_agent.execution.lifecycle import (
    ExecutionEventStore,
    ExecutionEventType,
    ExecutionHealth,
    ExecutionLifecycle,
    ExposureEffect,
    IntentStatus,
    InvariantViolation,
    PortfolioRiskSnapshot,
    ReconciliationState,
    TrustedPrice,
)
from trading_agent.execution.permission import (
    OrderPermission,
    PermissionContext,
    PermissionReason,
    evaluate_order_permission,
)
from trading_agent.execution.canonical.broker_gateway import CancelEvidence, CancelState


def fresh_price() -> TrustedPrice:
    now = datetime.now(UTC)
    return TrustedPrice(
        price=100.0,
        exchange_timestamp=now,
        received_at=now,
        sequence_id=1,
    )


@pytest.fixture
def store(tmp_path):
    return ExecutionEventStore(tmp_path / "hardening.db").connect()


def reducing_context(**overrides) -> PermissionContext:
    values = {
        "execution_health": ExecutionHealth.NORMAL,
        "exposure_effect": ExposureEffect.REDUCE,
        "trusted_price": fresh_price(),
        "inventory_state": "known",
        "free_inventory": 2.0,
        "authorized_sellable_inventory": 2.0,
        "order_side": "sell",
        "order_size": 1.0,
    }
    values.update(overrides)
    return PermissionContext(**values)


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        (
            {
                "execution_health": ExecutionHealth.MANUAL_BLOCKED,
                "manual_blocked": True,
            },
            PermissionReason.MANUAL_BLOCKED,
        ),
        (
            {"execution_health": ExecutionHealth.PROTECTION_GAP},
            PermissionReason.PROTECTION_GAP,
        ),
        (
            {
                "execution_health": ExecutionHealth.RECONCILING,
                "reconciliation_state": "started",
            },
            PermissionReason.RECONCILIATION_UNRESOLVED,
        ),
        (
            {"data_trust": "untrusted", "trusted_price": None},
            PermissionReason.STALE_MARKET_DATA,
        ),
        (
            {"kill_switch_active": True},
            PermissionReason.KILL_SWITCH_INCREASE,
        ),
    ],
)
def test_degraded_state_preserves_provably_safe_reduction(overrides, reason):
    result = evaluate_order_permission(reducing_context(**overrides))
    assert result.permission == OrderPermission.REDUCE_ONLY
    assert result.reason == reason


def test_unknown_inventory_or_broker_blocks_claimed_reduction():
    unknown_inventory = evaluate_order_permission(
        reducing_context(inventory_state="unknown")
    )
    assert unknown_inventory.permission == OrderPermission.BLOCK
    assert unknown_inventory.reason == PermissionReason.UNKNOWN_INVENTORY_STATE

    unknown_broker = evaluate_order_permission(reducing_context(broker_state="mystery"))
    assert unknown_broker.permission == OrderPermission.BLOCK
    assert unknown_broker.reason == PermissionReason.UNKNOWN_BROKER_STATE


def build_lifecycle(
    store, inventory, *, price_source=fresh_price, portfolio_source=None
):
    if portfolio_source is None:

        def portfolio_source(symbol):
            return PortfolioRiskSnapshot(
                symbol=symbol,
                position_quantity=0.0,
                available_quantity=inventory.get(symbol, 0.0),
                equity=100_000.0,
                available_cash=100_000.0,
                observed_at=datetime.now(UTC),
                source="test",
            )

    return ExecutionLifecycle(
        store,
        price_source=lambda symbol: price_source(),
        portfolio_source=portfolio_source,
        inventory_source=lambda symbol, side: inventory[symbol],
    )


def create_approved_sell(lifecycle, intent_id: str, quantity: float) -> None:
    lifecycle.create_order_intent(intent_id, "BTC/USDT", "sell", quantity)
    lifecycle.approve_risk(intent_id)


def test_lifecycle_uses_same_permission_semantics_for_manual_safe_exit(store):
    inventory = {"BTC/USDT": 2.0}
    lifecycle = build_lifecycle(store, inventory, price_source=lambda: None)
    lifecycle.require_manual_intervention("unknown-order", "broker ownership unknown")

    create_approved_sell(lifecycle, "safe-exit", 1.0)
    lifecycle.submit_order("safe-exit", exchange_order_id="exit-1")

    assert lifecycle.order("safe-exit").status == IntentStatus.SUBMITTED
    assert lifecycle.active_sell_reservations("BTC/USDT") == pytest.approx(1.0)


def test_two_sells_cannot_reserve_same_inventory(store):
    inventory = {"BTC/USDT": 1.0}
    lifecycle = build_lifecycle(store, inventory)
    create_approved_sell(lifecycle, "sell-1", 0.7)
    create_approved_sell(lifecycle, "sell-2", 0.4)

    lifecycle.submit_order("sell-1", exchange_order_id="ex-1")
    with pytest.raises(InvariantViolation, match="insufficient_inventory"):
        lifecycle.submit_order("sell-2", exchange_order_id="ex-2")

    assert lifecycle.active_sell_reservations("BTC/USDT") == pytest.approx(0.7)
    assert lifecycle.order("sell-2").status == IntentStatus.APPROVED


def test_two_lifecycle_instances_share_transactional_sell_lock(tmp_path):
    database = tmp_path / "shared-execution.db"
    store_one = ExecutionEventStore(database).connect()
    store_two = ExecutionEventStore(database).connect()
    inventory = {"BTC/USDT": 1.0}
    lifecycle_one = build_lifecycle(store_one, inventory)
    lifecycle_two = build_lifecycle(store_two, inventory)

    create_approved_sell(lifecycle_one, "sell-one", 0.7)
    create_approved_sell(lifecycle_two, "sell-two", 0.4)
    lifecycle_one.submit_order("sell-one", exchange_order_id="ex-one")

    with pytest.raises(
        InvariantViolation,
        match="active_sell_reservations_never_exceed_authorized_inventory",
    ):
        lifecycle_two.submit_order("sell-two", exchange_order_id="ex-two")

    assert store_one.active_sell_reservations("BTC/USDT") == pytest.approx(0.7)
    assert store_two.active_sell_reservations("BTC/USDT") == pytest.approx(0.7)
    assert lifecycle_two.order("sell-two").status == IntentStatus.APPROVED


def test_partial_fill_uses_reservation_when_free_balance_changes(store):
    inventory = {"BTC/USDT": 1.0}
    lifecycle = build_lifecycle(store, inventory)
    create_approved_sell(lifecycle, "sell", 1.0)
    lifecycle.submit_order("sell", exchange_order_id="ex")

    inventory["BTC/USDT"] = 0.0
    lifecycle.receive_fill("sell", 0.4, 100.0)

    order = lifecycle.order("sell")
    assert order.filled_size == pytest.approx(0.4)
    assert order.remaining_reserved_quantity == pytest.approx(0.6)
    assert lifecycle.active_sell_reservations("BTC/USDT") == pytest.approx(0.6)


@pytest.mark.parametrize("terminal", ["cancel", "reject"])
def test_cancel_or_reject_releases_remaining_reservation(store, terminal):
    inventory = {"BTC/USDT": 1.0}
    lifecycle = build_lifecycle(store, inventory)
    create_approved_sell(lifecycle, "sell", 0.8)
    lifecycle.submit_order("sell", exchange_order_id="ex")
    lifecycle.receive_fill("sell", 0.3, 100.0)

    if terminal == "cancel":
        lifecycle.request_cancel("sell")
        lifecycle.confirm_cancel(
            "sell",
            CancelEvidence(
                broker_order_id="ex",
                state=CancelState.CANCELED,
                venue="paper",
                confirmed_at=datetime.now(UTC).isoformat(),
                source="BROKER",
            ),
        )
        assert lifecycle.order("sell").status == IntentStatus.CANCELED
    else:
        lifecycle.reject_order("sell", reason="broker rejected")
        assert lifecycle.order("sell").status == IntentStatus.REJECTED

    assert lifecycle.order("sell").remaining_reserved_quantity == pytest.approx(0.0)
    assert lifecycle.active_sell_reservations("BTC/USDT") == pytest.approx(0.0)


def test_restart_and_duplicate_replay_reconstruct_one_reservation(store):
    inventory = {"BTC/USDT": 1.0}
    lifecycle = build_lifecycle(store, inventory)
    create_approved_sell(lifecycle, "sell", 0.75)
    lifecycle.submit_order("sell", exchange_order_id="ex")
    events = store.read_events()

    restarted = build_lifecycle(store, inventory)
    restarted.load()
    assert restarted.active_sell_reservations("BTC/USDT") == pytest.approx(0.75)

    restarted.replay(events + events)
    assert restarted.active_sell_reservations("BTC/USDT") == pytest.approx(0.75)


def test_event_dispatch_is_exhaustive_and_one_handler_per_event_type():
    handlers = ExecutionLifecycle._EVENT_HANDLERS
    assert set(handlers) == set(ExecutionEventType)
    assert len(handlers) == len(ExecutionEventType)


def test_protection_locked_inventory_is_not_sellable(store):
    # Inventory source exposes only quantity not already locked by protection.
    inventory = {"BTC/USDT": 0.2}
    lifecycle = build_lifecycle(store, inventory)
    lifecycle.create_order_intent("sell", "BTC/USDT", "sell", 0.3)
    lifecycle.approve_risk("sell")
    with pytest.raises(InvariantViolation, match="insufficient_inventory"):
        lifecycle.submit_order("sell", exchange_order_id="ex")


def test_reconciliation_state_is_projected_by_event_handler(store):
    lifecycle = ExecutionLifecycle(store)
    lifecycle.start_reconciliation()
    assert lifecycle.state.reconciliation == ReconciliationState.STARTED
    assert lifecycle.state.execution_health == ExecutionHealth.RECONCILING


class _RecordingExchange:
    def __init__(self):
        self.calls = []

    async def create_order(self, symbol, side, qty, **kwargs):
        self.calls.append((symbol, side, qty, kwargs))
        return {"price": 100.0, "filled": qty}
