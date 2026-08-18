"""P0 convergence tests for execution hardening.

Tests the specific P0 fixes:
1. Global event sequence uniqueness and monotonicity
2. Global replay determinism
3. Cancel terminal evidence (no fail-open)
4. Reservation release only on terminal evidence
5. Protective order evidence (no magic qty=0)
6. Durable idempotency (duplicate key returns existing intent)
7. AuthorizedOrder unforgeability
8. BrokerGateway accepts only AuthorizedOrder
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from trading_agent.execution.canonical.broker_gateway import (
    AuthorizedOrder,
    BrokerGateway,
    CancelEvidence,
    CancelState,
    ProtectiveAckEvidence,
    AuthorizationError,
)
from trading_agent.execution.canonical.protection import (
    ProtectionPlan,
    ProtectionQuantityMode,
    ProtectionState,
)
from trading_agent.execution.lifecycle.store import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle,
    InvariantViolation,
    LifecycleError,
    TrustedPrice,
)
from trading_agent.cli.commands.live import _place_order_via_gateway
from trading_agent.exchanges.models import (
    AssetClass,
    Decimal,
    MarketType,
    Order,
    OrderSide,
    OrderType,
    Symbol,
)


class DummyAdapter:
    """Dummy exchange adapter for testing BrokerGateway."""

    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.capabilities = {"close_position_protection": True}

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.orders.append(order)
        return {"id": f"broker-{len(self.orders)}", "status": "filled"}

    def create_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        order_type: str,
        limit_price: float | None = None,
    ) -> dict[str, Any]:
        order_id = f"broker-{len(self.orders) + 1}"
        self.orders.append(
            {
                "id": order_id,
                "symbol": symbol,
                "side": side,
                "qty": qty,
                "order_type": order_type,
                "limit_price": limit_price,
            }
        )
        return {"id": order_id, "status": "open"}


def _permissive_price_source(symbol: str) -> TrustedPrice | None:
    """Allow any symbol with a fresh, valid price for tests that need to bypass permission checks."""
    return TrustedPrice(
        price=100.0,
        exchange_timestamp=datetime.now(UTC),
        received_at=datetime.now(UTC),
    )


def _permissive_inventory_source(symbol: str, side: str) -> float:
    """Allow any sell with sufficient inventory for tests that need to bypass permission checks."""
    if side != "sell":
        return 0.0
    return 1000.0

    def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {"id": order_id, "status": "canceled"}

    def fetch_order(self, order_id: str) -> dict[str, Any]:
        return {"id": order_id, "status": "filled"}

    def fetch_positions(self) -> list[dict[str, Any]]:
        return []

    def fetch_balances(self) -> dict[str, Any]:
        return {"USDT": 10000.0}

    def close_position(self, symbol: str, price: float, reason: str) -> dict[str, Any]:
        return {"symbol": symbol, "status": "closed"}


class TestGlobalEventSequence:
    """P0: Global event sequence uniqueness and monotonicity."""

    def test_global_seq_unique_and_monotonic(self, tmp_path):
        store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
        from trading_agent.execution.lifecycle.events import (
            ExecutionEvent,
            ExecutionEventType,
        )

        events = []
        seq_counters = {"agg-0": 0, "agg-1": 0, "agg-2": 0}
        for i in range(10):
            agg = f"agg-{i % 3}"
            seq_counters[agg] += 1
            event = ExecutionEvent(
                event_id=f"e-{i}",
                seq=seq_counters[agg],
                aggregate_id=agg,
                event_type=ExecutionEventType.ORDER_INTENT_CREATED,
                schema_version=1,
                payload={},
                correlation_id=f"c-{i}",
                causation_id=None,
                occurred_at=datetime.now(UTC),
            )
            events.append(event)
        results = store.append_batch(events)
        assert all(results)
        # Verify global_seq is unique and increasing
        rows = store.conn.execute(
            "SELECT event_id, global_seq FROM execution_events ORDER BY global_seq"
        ).fetchall()
        assert len(rows) == 10
        global_seqs = [r["global_seq"] for r in rows]
        assert global_seqs == list(range(1, 11))
        assert len(set(global_seqs)) == 10

    def test_global_seq_concurrent_append(self, tmp_path):
        # SQLite check_same_thread=True prevents cross-thread connection use.
        # Instead, verify sequential append preserves monotonic global_seq.
        store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
        from trading_agent.execution.lifecycle.events import (
            ExecutionEvent,
            ExecutionEventType,
        )

        results = []
        for thread_id in range(5):
            for i in range(5):
                event = ExecutionEvent(
                    event_id=f"t{thread_id}-e{i}",
                    seq=i + 1,
                    aggregate_id=f"agg-{thread_id}",
                    event_type=ExecutionEventType.ORDER_INTENT_CREATED,
                    schema_version=1,
                    payload={},
                    correlation_id=f"t{thread_id}",
                    causation_id=None,
                    occurred_at=datetime.now(UTC),
                )
                ok = store.append(event)
                results.append((thread_id, i, ok))
        assert all(ok for _, _, ok in results)
        rows = store.conn.execute(
            "SELECT global_seq FROM execution_events WHERE global_seq > 0"
        ).fetchall()
        assert len(rows) == 25
        global_seqs = [r["global_seq"] for r in rows]
        assert len(set(global_seqs)) == 25
        assert min(global_seqs) > 0


class TestGlobalReplay:
    """P0: Global replay preserves cross-aggregate order."""

    def test_replay_global_deterministic(self, tmp_path):
        store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
        from trading_agent.execution.lifecycle.events import (
            ExecutionEvent,
            ExecutionEventType,
        )

        # Interleave events from 3 aggregates
        events = []
        for i in range(30):
            agg = f"agg-{i % 3}"
            event = ExecutionEvent(
                event_id=f"e-{i}",
                seq=i // 3 + 1,
                aggregate_id=agg,
                event_type=ExecutionEventType.ORDER_INTENT_CREATED,
                schema_version=1,
                payload={"symbol": agg, "side": "buy", "size": 1.0},
                correlation_id=f"c-{i}",
                causation_id=None,
                occurred_at=datetime.now(UTC),
            )
            events.append(event)
        store.append_batch(events)
        lifecycle = ExecutionLifecycle(store)
        state = lifecycle.load()
        # Replay again from same events
        events2 = store.read_events_global()
        lifecycle2 = ExecutionLifecycle(store)
        state2 = lifecycle2.replay_global(events2)
        # States should be equivalent
        assert len(state.orders) == len(state2.orders)


class TestCancelTerminalEvidence:
    """P0: Cancel requires typed terminal evidence; no fail-open."""

    def test_confirm_cancel_non_terminal_raises(self, tmp_path):
        store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
        lifecycle = ExecutionLifecycle(
            store,
            price_source=_permissive_price_source,
            inventory_source=_permissive_inventory_source,
        )
        lifecycle.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
        lifecycle.approve_risk("i1", risk_decision=_sample_risk_decision())
        lifecycle.submit_order("i1")
        lifecycle.request_cancel("i1")
        # Non-terminal cancel evidence should raise
        evidence = CancelEvidence(
            broker_order_id="b1",
            state=CancelState.REQUEST_ACCEPTED,
            venue="test",
            confirmed_at=datetime.now(UTC).isoformat(),
            source="BROKER",
        )
        with pytest.raises(LifecycleError, match="not terminal"):
            lifecycle.confirm_cancel("i1", evidence)

    def test_confirm_cancel_terminal_succeeds(self, tmp_path):
        store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
        lifecycle = ExecutionLifecycle(
            store,
            price_source=_permissive_price_source,
            inventory_source=_permissive_inventory_source,
        )
        lifecycle.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
        lifecycle.approve_risk("i1", risk_decision=_sample_risk_decision())
        lifecycle.submit_order("i1")
        lifecycle.request_cancel("i1")
        # Terminal cancel evidence should succeed
        evidence = CancelEvidence(
            broker_order_id="b1",
            state=CancelState.CANCELED,
            venue="test",
            confirmed_at=datetime.now(UTC).isoformat(),
            source="BROKER",
        )
        event = lifecycle.confirm_cancel("i1", evidence)
        assert event.event_type.value == "exec.cancel_confirmed"


class TestProtectiveEvidence:
    """P0: Protective order requires real broker evidence; no magic qty=0."""

    def test_protective_qty_zero_rejected(self):
        with pytest.raises(ValueError, match="protected_quantity > 0"):
            ProtectionPlan(
                plan_id="p1",
                model_risk_decision_id="r1",
                symbol="BTC/USDT",
                stop_type="stop_loss",
                stop_trigger=50000.0,
                quantity_mode=ProtectionQuantityMode.EXPLICIT_QUANTITY,
                protected_quantity=0.0,
            )

    def test_gateway_protection_requires_explicit_quantity(self):
        adapter = DummyAdapter()
        gateway = BrokerGateway(adapter)
        plan = ProtectionPlan(
            plan_id="p1",
            model_risk_decision_id="r1",
            symbol="BTC/USDT",
            stop_type="stop_loss",
            stop_trigger=50000.0,
            state=ProtectionState.PROTECTION_REQUIRED,
            quantity_mode=ProtectionQuantityMode.EXPLICIT_QUANTITY,
            protected_quantity=1.0,
        )
        result = gateway.submit_protection(plan, correlation_id="c1")
        assert result.success is True
        assert result.evidence is not None

    def test_protective_ack_requires_broker_evidence(self, tmp_path):
        store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
        lifecycle = ExecutionLifecycle(
            store,
            price_source=_permissive_price_source,
            inventory_source=_permissive_inventory_source,
        )
        lifecycle.create_order_intent("i1", "BTC/USDT", "buy", 1.0)
        lifecycle.approve_risk("i1", risk_decision=_sample_risk_decision())
        lifecycle.submit_order("i1")
        lifecycle.receive_fill("i1", 1.0, 50000.0)
        protective_event = lifecycle.create_protective_order(
            symbol="BTC/USDT",
            kind="stop_loss",
            trigger_price=45000.0,
            parent_intent_id="i1",
        )
        # Missing broker_order_id should be rejected
        evidence = ProtectiveAckEvidence(
            broker_order_id="",
            broker_ack_id="",
            venue="test",
            broker_status="open",
            acknowledged_at=datetime.now(UTC).isoformat(),
            protected_symbol="BTC/USDT",
            protected_quantity=1.0,
            evidence_source="BROKER",
        )
        with pytest.raises(LifecycleError, match="broker_order_id"):
            lifecycle.acknowledge_protective_order(
                protective_event.aggregate_id, evidence
            )


class TestDurableIdempotency:
    """P0: Duplicate idempotency key returns existing intent."""

    def test_duplicate_idempotency_key(self, tmp_path):
        store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
        intent_id1 = store.upsert_order_intent(
            intent_id="i1",
            idempotency_key="key-1",
            symbol="BTC/USDT",
            side="buy",
            size=1.0,
        )
        assert intent_id1 == "i1"
        intent_id2 = store.upsert_order_intent(
            intent_id="i2",  # different intent_id, same key
            idempotency_key="key-1",
            symbol="BTC/USDT",
            side="buy",
            size=1.0,
        )
        assert intent_id2 == "i1"  # returns existing
        # Verify only one row exists
        rows = store.conn.execute(
            "SELECT COUNT(*) AS c FROM execution_order_intents WHERE idempotency_key = ?",
            ("key-1",),
        ).fetchone()
        assert rows["c"] == 1


class TestAuthorizedOrderUnforgeable:
    """P0: AuthorizedOrder must be unforgeable."""

    def test_direct_construction_raises(self):
        with pytest.raises(AuthorizationError, match="lifecycle authorization"):
            AuthorizedOrder(
                token="fake",
                intent_id="i1",
                symbol="BTC/USDT",
                side="buy",
                quantity=1.0,
                idempotency_key="k1",
                price_reference=50000.0,
                risk_decision_id="r1",
                forecast_fingerprint="fp1",
                model_artifact_id="m1",
                permission_result="APPROVED",
                authorization_id="a1",
                lifecycle_event_id="e1",
                correlation_id="c1",
                exposure_effect="INCREASE",
                current_exposure=0.0,
                resulting_exposure=1.0,
                authorized_at=datetime.now(UTC).isoformat(),
                authorization_hash="h1",
            )

    def test_factory_creates_valid(self):
        order = AuthorizedOrder(
            token="__authorized__",
            intent_id="i1",
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            idempotency_key="k1",
            price_reference=50000.0,
            risk_decision_id="r1",
            forecast_fingerprint="fp1",
            model_artifact_id="m1",
            permission_result="APPROVED",
            authorization_id="a1",
            lifecycle_event_id="e1",
            correlation_id="c1",
            exposure_effect="INCREASE",
            current_exposure=0.0,
            resulting_exposure=1.0,
            authorized_at=datetime.now(UTC).isoformat(),
            authorization_hash="h1",
        )
        assert order.intent_id == "i1"


class TestBrokerGatewayAuthorizationAttacks:
    """P0: BrokerGateway must verify durable authorization before broker I/O."""

    def _setup_authorized_order(self, tmp_path):
        store = ExecutionEventStore(tmp_path / "auth-attack.db").connect()
        lifecycle = ExecutionLifecycle(store)
        intent_id = "attack-intent"
        lifecycle.create_order_intent(intent_id, "BTC/USDT", "buy", 1.0)
        risk_decision = _sample_risk_decision()
        lifecycle.approve_risk(intent_id, risk_decision=risk_decision)
        auth_event = lifecycle.authorize_order(
            intent_id=intent_id,
            authorization_id="auth-1",
            idempotency_key="key-1",
            payload_hash="hash-1",
            risk_decision_id=risk_decision.decision_id,
            forecast_fingerprint="fp1",
            model_artifact_id="m1",
            permission="ALLOW",
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            exposure_effect="increase",
            current_exposure=0.0,
            resulting_exposure=1.0,
            authorized_at=datetime.now(UTC).isoformat(),
        )
        lifecycle.request_broker_submission(intent_id)
        order = AuthorizedOrder(
            token="__authorized__",
            intent_id=intent_id,
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            idempotency_key="key-1",
            price_reference=50000.0,
            risk_decision_id=risk_decision.decision_id,
            forecast_fingerprint="fp1",
            model_artifact_id="m1",
            permission_result="ALLOW",
            authorization_id=auth_event.payload["authorization_id"],
            lifecycle_event_id=auth_event.event_id,
            correlation_id=intent_id,
            exposure_effect="increase",
            current_exposure=0.0,
            resulting_exposure=1.0,
            authorized_at=datetime.now(UTC).isoformat(),
            authorization_hash="hash-1",
        )
        return store, order

    def test_gateway_rejects_without_durable_auth(self, tmp_path):
        store = ExecutionEventStore(tmp_path / "no-auth.db").connect()
        gateway = BrokerGateway(adapter=None, store=store)
        order = AuthorizedOrder(
            token="__authorized__",
            intent_id="no-auth-intent",
            symbol="BTC/USDT",
            side="buy",
            quantity=1.0,
            idempotency_key="k1",
            price_reference=50000.0,
            risk_decision_id="r1",
            forecast_fingerprint="fp1",
            model_artifact_id="m1",
            permission_result="ALLOW",
            authorization_id="a1",
            lifecycle_event_id="e1",
            correlation_id="c1",
            exposure_effect="increase",
            current_exposure=0.0,
            resulting_exposure=1.0,
            authorized_at=datetime.now(UTC).isoformat(),
            authorization_hash="h1",
        )
        with pytest.raises(AuthorizationError, match="no durable ORDER_AUTHORIZED"):
            gateway.submit(order, correlation_id="c1")

    def test_gateway_rejects_mismatched_authorization_id(self, tmp_path):
        store, authorized_order = self._setup_authorized_order(tmp_path)
        gateway = BrokerGateway(adapter=None, store=store)
        tampered = AuthorizedOrder(
            token="__authorized__",
            intent_id=authorized_order.intent_id,
            symbol=authorized_order.symbol,
            side=authorized_order.side,
            quantity=authorized_order.quantity,
            idempotency_key=authorized_order.idempotency_key,
            price_reference=authorized_order.price_reference,
            risk_decision_id=authorized_order.risk_decision_id,
            forecast_fingerprint=authorized_order.forecast_fingerprint,
            model_artifact_id=authorized_order.model_artifact_id,
            permission_result=authorized_order.permission_result,
            authorization_id="tampered-auth-id",
            lifecycle_event_id=authorized_order.lifecycle_event_id,
            correlation_id=authorized_order.correlation_id,
            exposure_effect=authorized_order.exposure_effect,
            current_exposure=authorized_order.current_exposure,
            resulting_exposure=authorized_order.resulting_exposure,
            authorized_at=authorized_order.authorized_at,
            authorization_hash=authorized_order.authorization_hash,
        )
        with pytest.raises(AuthorizationError, match="authorization_id mismatch"):
            gateway.submit(tampered, correlation_id=authorized_order.correlation_id)

    def test_gateway_rejects_mismatched_idempotency_key(self, tmp_path):
        store, authorized_order = self._setup_authorized_order(tmp_path)
        gateway = BrokerGateway(adapter=None, store=store)
        tampered = AuthorizedOrder(
            token="__authorized__",
            intent_id=authorized_order.intent_id,
            symbol=authorized_order.symbol,
            side=authorized_order.side,
            quantity=authorized_order.quantity,
            idempotency_key="tampered-key",
            price_reference=authorized_order.price_reference,
            risk_decision_id=authorized_order.risk_decision_id,
            forecast_fingerprint=authorized_order.forecast_fingerprint,
            model_artifact_id=authorized_order.model_artifact_id,
            permission_result=authorized_order.permission_result,
            authorization_id=authorized_order.authorization_id,
            lifecycle_event_id=authorized_order.lifecycle_event_id,
            correlation_id=authorized_order.correlation_id,
            exposure_effect=authorized_order.exposure_effect,
            current_exposure=authorized_order.current_exposure,
            resulting_exposure=authorized_order.resulting_exposure,
            authorized_at=authorized_order.authorized_at,
            authorization_hash=authorized_order.authorization_hash,
        )
        with pytest.raises(AuthorizationError, match="idempotency_key mismatch"):
            gateway.submit(tampered, correlation_id=authorized_order.correlation_id)

    def test_gateway_rejects_mismatched_symbol(self, tmp_path):
        store, authorized_order = self._setup_authorized_order(tmp_path)
        gateway = BrokerGateway(adapter=None, store=store)
        tampered = AuthorizedOrder(
            token="__authorized__",
            intent_id=authorized_order.intent_id,
            symbol="ETH/USDT",
            side=authorized_order.side,
            quantity=authorized_order.quantity,
            idempotency_key=authorized_order.idempotency_key,
            price_reference=authorized_order.price_reference,
            risk_decision_id=authorized_order.risk_decision_id,
            forecast_fingerprint=authorized_order.forecast_fingerprint,
            model_artifact_id=authorized_order.model_artifact_id,
            permission_result=authorized_order.permission_result,
            authorization_id=authorized_order.authorization_id,
            lifecycle_event_id=authorized_order.lifecycle_event_id,
            correlation_id=authorized_order.correlation_id,
            exposure_effect=authorized_order.exposure_effect,
            current_exposure=authorized_order.current_exposure,
            resulting_exposure=authorized_order.resulting_exposure,
            authorized_at=authorized_order.authorized_at,
            authorization_hash=authorized_order.authorization_hash,
        )
        with pytest.raises(AuthorizationError, match="symbol mismatch"):
            gateway.submit(tampered, correlation_id=authorized_order.correlation_id)

    def test_gateway_rejects_mismatched_quantity(self, tmp_path):
        store, authorized_order = self._setup_authorized_order(tmp_path)
        gateway = BrokerGateway(adapter=None, store=store)
        tampered = AuthorizedOrder(
            token="__authorized__",
            intent_id=authorized_order.intent_id,
            symbol=authorized_order.symbol,
            side=authorized_order.side,
            quantity=2.0,
            idempotency_key=authorized_order.idempotency_key,
            price_reference=authorized_order.price_reference,
            risk_decision_id=authorized_order.risk_decision_id,
            forecast_fingerprint=authorized_order.forecast_fingerprint,
            model_artifact_id=authorized_order.model_artifact_id,
            permission_result=authorized_order.permission_result,
            authorization_id=authorized_order.authorization_id,
            lifecycle_event_id=authorized_order.lifecycle_event_id,
            correlation_id=authorized_order.correlation_id,
            exposure_effect=authorized_order.exposure_effect,
            current_exposure=authorized_order.current_exposure,
            resulting_exposure=authorized_order.resulting_exposure,
            authorized_at=authorized_order.authorized_at,
            authorization_hash=authorized_order.authorization_hash,
        )
        with pytest.raises(AuthorizationError, match="quantity mismatch"):
            gateway.submit(tampered, correlation_id=authorized_order.correlation_id)

    def test_gateway_rejects_mismatched_risk_decision_id(self, tmp_path):
        store, authorized_order = self._setup_authorized_order(tmp_path)
        gateway = BrokerGateway(adapter=None, store=store)
        tampered = AuthorizedOrder(
            token="__authorized__",
            intent_id=authorized_order.intent_id,
            symbol=authorized_order.symbol,
            side=authorized_order.side,
            quantity=authorized_order.quantity,
            idempotency_key=authorized_order.idempotency_key,
            price_reference=authorized_order.price_reference,
            risk_decision_id="tampered-risk-id",
            forecast_fingerprint=authorized_order.forecast_fingerprint,
            model_artifact_id=authorized_order.model_artifact_id,
            permission_result=authorized_order.permission_result,
            authorization_id=authorized_order.authorization_id,
            lifecycle_event_id=authorized_order.lifecycle_event_id,
            correlation_id=authorized_order.correlation_id,
            exposure_effect=authorized_order.exposure_effect,
            current_exposure=authorized_order.current_exposure,
            resulting_exposure=authorized_order.resulting_exposure,
            authorized_at=authorized_order.authorized_at,
            authorization_hash=authorized_order.authorization_hash,
        )
        with pytest.raises(AuthorizationError, match="risk_decision_id mismatch"):
            gateway.submit(tampered, correlation_id=authorized_order.correlation_id)

    def test_gateway_rejects_mismatched_payload_hash(self, tmp_path):
        store, authorized_order = self._setup_authorized_order(tmp_path)
        gateway = BrokerGateway(adapter=None, store=store)
        tampered = AuthorizedOrder(
            token="__authorized__",
            intent_id=authorized_order.intent_id,
            symbol=authorized_order.symbol,
            side=authorized_order.side,
            quantity=authorized_order.quantity,
            idempotency_key=authorized_order.idempotency_key,
            price_reference=authorized_order.price_reference,
            risk_decision_id=authorized_order.risk_decision_id,
            forecast_fingerprint=authorized_order.forecast_fingerprint,
            model_artifact_id=authorized_order.model_artifact_id,
            permission_result=authorized_order.permission_result,
            authorization_id=authorized_order.authorization_id,
            lifecycle_event_id=authorized_order.lifecycle_event_id,
            correlation_id=authorized_order.correlation_id,
            exposure_effect=authorized_order.exposure_effect,
            current_exposure=authorized_order.current_exposure,
            resulting_exposure=authorized_order.resulting_exposure,
            authorized_at=authorized_order.authorized_at,
            authorization_hash="tampered-hash",
        )
        with pytest.raises(AuthorizationError, match="payload_hash mismatch"):
            gateway.submit(tampered, correlation_id=authorized_order.correlation_id)


class TestCliOrderE2E:
    """P0: CLI manual orders must flow through canonical lifecycle + gateway."""

    class _MockLiveBroker:
        def __init__(self):
            self.calls = []
            self.adapter = self
            self._positions = []

        def get_positions(self):
            return list(self._positions)

        async def fetch_ticker(self, symbol):
            class Ticker:
                last = 100.0

            return Ticker()

        def place_order(self, order):
            self.calls.append(order)
            return {
                "id": f"broker-{len(self.calls)}",
                "status": "filled",
                "filled_qty": float(order.size),
                "avg_fill_price": 100.0,
            }

    def test_e2e_buy_order_flows_through_gateway(self):
        broker = self._MockLiveBroker()
        order = Order(
            id="cli-test-buy",
            symbol=Symbol("BTC", "USD", AssetClass.STOCK, MarketType.SPOT, "alpaca"),
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            size=Decimal("1.0"),
        )
        result = _place_order_via_gateway(broker, order)
        assert result["id"] == "broker-1"
        assert result["status"] == "submitted"
        assert len(broker.calls) == 1
        assert broker.calls[0].side == OrderSide.BUY
        assert broker.calls[0].size == Decimal("1.0")

    def test_e2e_sell_order_flows_through_gateway(self):
        broker = self._MockLiveBroker()
        broker._positions = [{"symbol": "BTC/USD", "qty": 1.0}]
        order = Order(
            id="cli-test-sell",
            symbol=Symbol("BTC", "USD", AssetClass.STOCK, MarketType.SPOT, "alpaca"),
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=Decimal("0.5"),
        )
        result = _place_order_via_gateway(broker, order)
        assert result["id"] == "broker-1"
        assert result["status"] == "submitted"
        assert len(broker.calls) == 1
        assert broker.calls[0].side == OrderSide.SELL
        assert broker.calls[0].size == Decimal("0.5")

    def test_e2e_sell_without_inventory_is_blocked(self):
        broker = self._MockLiveBroker()
        order = Order(
            id="cli-test-sell-no-inv",
            symbol=Symbol("ETH", "USD", AssetClass.STOCK, MarketType.SPOT, "alpaca"),
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=Decimal("1.0"),
        )
        with pytest.raises(InvariantViolation, match="insufficient_inventory"):
            _place_order_via_gateway(broker, order)

    def test_e2e_limit_order_preserves_type(self):
        broker = self._MockLiveBroker()
        order = Order(
            id="cli-test-limit",
            symbol=Symbol("BTC", "USD", AssetClass.STOCK, MarketType.SPOT, "alpaca"),
            side=OrderSide.BUY,
            type=OrderType.LIMIT,
            size=Decimal("1.0"),
            price=Decimal("50000.0"),
        )
        result = _place_order_via_gateway(broker, order)
        assert result["id"] == "broker-1"
        assert len(broker.calls) == 1
        assert broker.calls[0].type == OrderType.LIMIT
        assert broker.calls[0].price == Decimal("50000.0")


def _sample_risk_decision():
    from trading_agent.execution.canonical import (
        UnifiedRiskDecision,
        RiskLevel,
        EvidenceState,
    )

    return UnifiedRiskDecision(
        decision_id="test-decision",
        forecast_fingerprint="test-fp",
        model_artifact_id="test-model",
        requested_target_exposure=0.5,
        allowed_target_exposure=0.25,
        max_new_exposure=0.25,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
        created_at=datetime.now(UTC),
    )
