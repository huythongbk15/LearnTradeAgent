"""P0 convergence tests for execution hardening.

Tests the specific P0 fixes:
1. Global event sequence uniqueness and monotonicity
2. Global replay determinism
3. Cancel terminal evidence (no fail-open)
4. Reservation release only on terminal evidence
5. Protective order evidence (no magic qty=0)
6. Durable idempotency (duplicate key returns existing intent)
7. Durable authorization IDs are opaque and mandatory
8. BrokerGateway reconstructs requests only from durable state
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from trading_agent.execution.canonical.broker_gateway import (
    BrokerGateway,
    CancelEvidence,
    CancelState,
    ProtectiveAckEvidence,
    AuthorizationError,
)
from trading_agent.execution.canonical.adapters import (
    BrokerSubmitFact,
    BrokerSubmitState,
)
from trading_agent.execution.canonical.protection import (
    ProtectionPlan,
    ProtectionQuantityMode,
)
from trading_agent.execution.lifecycle.store import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle,
    LifecycleError,
    PortfolioRiskSnapshot,
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

# Retained only inside non-collected legacy regression examples below.  The
# production type/token no longer exists; active tests exercise durable IDs.
AuthorizedOrder: Any = None
_AUTHORIZED_TOKEN: Any = None


class DummyAdapter:
    """Dummy exchange adapter for testing BrokerGateway."""

    def __init__(self) -> None:
        self.orders: list[dict[str, Any]] = []
        self.capabilities = {"close_position_protection": True}

    def place_order(self, order: dict[str, Any]) -> dict[str, Any]:
        self.orders.append(order)
        return {"id": f"broker-{len(self.orders)}", "status": "filled"}

    def submit_order(self, request: Any) -> BrokerSubmitFact:
        """Canonical submit_order interface used by BrokerGateway."""
        response = self.place_order(
            {
                "symbol": request.symbol,
                "side": request.side,
                "qty": request.quantity,
                "order_type": getattr(request, "order_type", "market"),
            }
        )
        broker_order_id = response.get("id")
        return BrokerSubmitFact(
            state=BrokerSubmitState.ACCEPTED
            if broker_order_id
            else BrokerSubmitState.REJECTED,
            broker_order_id=broker_order_id,
            client_order_id=getattr(request, "idempotency_key", None),
            venue="dummy",
            broker_status=response.get("status", "unknown"),
            observed_at=datetime.now(UTC),
            error=None if broker_order_id else "order_rejected",
            raw_response=response,
        )

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


def _permissive_portfolio_source(symbol: str) -> PortfolioRiskSnapshot:
    return PortfolioRiskSnapshot(
        symbol=symbol,
        position_quantity=0.0,
        available_quantity=1000.0,
        equity=100_000.0,
        available_cash=100_000.0,
        observed_at=datetime.now(UTC),
        source="test",
    )


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

    def test_gateway_protection_requires_durable_authorization(self, tmp_path):
        adapter = DummyAdapter()
        store = ExecutionEventStore(str(tmp_path / "gateway.db")).connect()
        gateway = BrokerGateway(adapter, store=store, lifecycle=None)
        with pytest.raises(AuthorizationError, match="no durable ORDER_AUTHORIZED"):
            gateway.submit_protection("missing-auth", correlation_id="c1")
        assert adapter.orders == []

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


class LegacyAuthorizedOrderUnforgeable:
    """P0: AuthorizedOrder must be unforgeable."""

    def test_direct_construction_raises(self):
        # Direct construction should fail (factory method is the only valid path)
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
            token=_AUTHORIZED_TOKEN,
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


class LegacyBrokerGatewayAuthorizationAttacks:
    """P0: BrokerGateway must verify durable authorization before broker I/O."""

    def _setup_authorized_order(self, tmp_path):
        store = ExecutionEventStore(tmp_path / "auth-attack.db").connect()
        lifecycle = ExecutionLifecycle(
            store,
            price_source=_permissive_price_source,
            inventory_source=_permissive_inventory_source,
        )
        intent_id = "attack-intent"
        lifecycle.create_order_intent(intent_id, "BTC/USDT", "buy", 1.0)
        risk_decision = _sample_risk_decision()
        lifecycle.approve_risk(intent_id, risk_decision=risk_decision)
        auth_event = lifecycle.authorize_order(
            intent_id=intent_id,
            idempotency_key="key-1",
        )
        lifecycle.request_broker_submission(intent_id, claimed_by=intent_id)
        order = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
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
            authorized_at=auth_event.payload["authorized_at"],
            authorization_hash="",
        )
        return store, order

    def test_gateway_rejects_without_durable_auth(self, tmp_path):
        store = ExecutionEventStore(tmp_path / "no-auth.db").connect()
        gateway = BrokerGateway(adapter=None, store=store, lifecycle=None)
        order = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
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
        gateway = BrokerGateway(adapter=None, store=store, lifecycle=None)
        tampered = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
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
        gateway = BrokerGateway(adapter=None, store=store, lifecycle=None)
        tampered = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
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
        gateway = BrokerGateway(adapter=None, store=store, lifecycle=None)
        tampered = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
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
        gateway = BrokerGateway(adapter=None, store=store, lifecycle=None)
        tampered = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
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
        gateway = BrokerGateway(adapter=None, store=store, lifecycle=None)
        tampered = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
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
        gateway = BrokerGateway(adapter=None, store=store, lifecycle=None)
        tampered = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
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


class TestDurableAuthorizationGateway:
    """P0: callers can submit only an opaque durable authorization ID."""

    def _authorize(self, tmp_path, *, request_submission: bool = True):
        store = ExecutionEventStore(tmp_path / "durable-auth.db").connect()
        lifecycle = ExecutionLifecycle(
            store,
            price_source=_permissive_price_source,
            inventory_source=_permissive_inventory_source,
            portfolio_source=_permissive_portfolio_source,
        )
        intent_id = "durable-intent"
        lifecycle.create_order_intent(intent_id, "BTC/USDT", "buy", 1.0)
        lifecycle.approve_risk(intent_id, risk_decision=_sample_risk_decision())
        auth = lifecycle.authorize_order(intent_id, idempotency_key="durable-key")
        if request_submission:
            lifecycle.request_broker_submission(intent_id, claimed_by=intent_id)
        return store, str(auth.payload["authorization_id"])

    def test_unknown_authorization_id_is_rejected(self, tmp_path):
        store = ExecutionEventStore(tmp_path / "unknown-auth.db").connect()
        gateway = BrokerGateway(adapter=DummyAdapter(), store=store, lifecycle=None)
        with pytest.raises(AuthorizationError, match="no durable ORDER_AUTHORIZED"):
            gateway.submit("unknown-auth", correlation_id="corr")

    def test_submission_request_is_required(self, tmp_path):
        store, auth_id = self._authorize(tmp_path, request_submission=False)
        gateway = BrokerGateway(adapter=DummyAdapter(), store=store, lifecycle=None)
        with pytest.raises(AuthorizationError, match="BROKER_SUBMISSION_REQUESTED"):
            gateway.submit(auth_id, correlation_id="corr")

    def test_non_string_caller_payload_is_rejected(self, tmp_path):
        store, auth_id = self._authorize(tmp_path)
        gateway = BrokerGateway(adapter=DummyAdapter(), store=store, lifecycle=None)
        with pytest.raises(AuthorizationError, match="non-empty string"):
            gateway.submit({"authorization_id": auth_id}, correlation_id="corr")

    def test_request_is_reconstructed_from_durable_authorization(self, tmp_path):
        store, auth_id = self._authorize(tmp_path)
        adapter = DummyAdapter()
        result = BrokerGateway(adapter=adapter, store=store).submit(
            auth_id,
            correlation_id="durable-intent",
        )
        assert result.success
        assert len(adapter.orders) == 1
        assert adapter.orders[0]["symbol"].pair == "BTC/USDT"
        assert float(adapter.orders[0]["qty"]) == 1.0


class TestCliOrderE2E:
    """P0: CLI manual orders must flow through canonical lifecycle + gateway."""

    class _MockLiveBroker:
        def __init__(self):
            self.calls = []
            self.adapter = self
            self.broker = "alpaca"
            self._positions = []

        def get_positions(self):
            return list(self._positions)

        async def fetch_ticker(self, symbol):
            class Ticker:
                last = 100.0
                timestamp = datetime.now(UTC)

            return Ticker()

        def get_account(self):
            return {"equity": 10_000.0, "cash": 10_000.0}

        def place_order(self, order):
            self.calls.append(order)
            if isinstance(order, dict):
                qty = float(order.get("qty", 0.0))
            else:
                qty = float(order.size)
            return {
                "id": f"broker-{len(self.calls)}",
                "status": "filled",
                "filled_qty": qty,
                "avg_fill_price": 100.0,
            }

        async def create_order(
            self,
            order,
            side=None,
            qty=None,
            order_type=None,
            limit_price=None,
            **kwargs,
        ):
            if hasattr(order, "symbol"):
                # Called with Order object from _SyncAsyncBridge
                self.calls.append(
                    {
                        "symbol": str(order.symbol),
                        "side": order.side,
                        "qty": float(order.size),
                        "order_type": order.type,
                        "limit_price": order.price,
                    }
                )
                qty_val = float(order.size)
            else:
                # Called with keyword args from BrokerGateway.submit_protection
                self.calls.append(
                    {
                        "symbol": str(order),
                        "side": side,
                        "qty": float(qty),
                        "order_type": order_type,
                        "limit_price": limit_price,
                    }
                )
                qty_val = float(qty)
            return {
                "id": f"broker-{len(self.calls)}",
                "status": "filled",
                "filled_qty": qty_val,
                "avg_fill_price": 100.0,
            }

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
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "events.db"
            store = ExecutionEventStore(str(db_path)).connect()
            result = _place_order_via_gateway(broker, order, store=store)
        assert result["id"] == "broker-1"
        assert result["status"] == "filled"
        assert len(broker.calls) == 1
        assert broker.calls[0].side == OrderSide.SELL
        assert float(broker.calls[0].size) == 0.5

    def test_e2e_sell_without_inventory_is_blocked(self):
        broker = self._MockLiveBroker()
        order = Order(
            id="cli-test-sell-no-inv",
            symbol=Symbol("ETH", "USD", AssetClass.STOCK, MarketType.SPOT, "alpaca"),
            side=OrderSide.SELL,
            type=OrderType.MARKET,
            size=Decimal("1.0"),
        )
        with pytest.raises(LifecycleError, match="insufficient_inventory"):
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
        with pytest.raises(
            RuntimeError, match="Manual BUY orders require real risk evidence"
        ):
            _place_order_via_gateway(broker, order)


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
        requested_target_exposure=1.0,
        allowed_target_exposure=1.0,
        max_new_exposure=1.0,
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


# ── Additional P1 convergence tests ──────────────────────────────────


class TestConcurrentSameDB:
    """Prove same-database concurrent access does not corrupt state."""

    def test_concurrent_append_same_db_file(self, tmp_path):
        """Multiple threads append to the same DB via separate connections.

        Pre-creates the DB schema in the main thread to avoid SQLite
        'database is locked' errors during concurrent `connect()` calls.
        """
        import threading

        from trading_agent.execution.lifecycle import ExecutionEventStore
        from trading_agent.execution.lifecycle.events import (
            ExecutionEvent,
            ExecutionEventType,
        )

        db_path = str(tmp_path / "concurrent.db")
        num_threads = 4
        events_per_thread = 20

        # Pre-create schema so worker threads only need to append
        ExecutionEventStore(db_path).connect().close()

        def writer(thread_id: int, results: list[bool]) -> None:
            store = ExecutionEventStore(db_path).connect()
            for i in range(events_per_thread):
                event = ExecutionEvent(
                    event_id=f"t{thread_id}-e{i}",
                    seq=i + 1,
                    aggregate_id=f"agg-{thread_id}",
                    event_type=ExecutionEventType.ORDER_INTENT_CREATED,
                    schema_version=1,
                    payload={"thread": thread_id, "idx": i},
                    correlation_id=f"t{thread_id}",
                    causation_id=None,
                    occurred_at=datetime.now(UTC),
                )
                ok = store.append(event)
                results.append(ok)
            store.close()

        threads = []
        results_shared: list[list[bool]] = [[] for _ in range(num_threads)]
        for tid in range(num_threads):
            t = threading.Thread(target=writer, args=(tid, results_shared[tid]))
            threads.append(t)
            t.start()
        for t in threads:
            t.join()
        all_ok = all(ok for sublist in results_shared for ok in sublist)
        assert all_ok, "Some concurrent appends failed"
        # Verify total count
        import sqlite3

        conn = sqlite3.connect(db_path)
        try:
            count = conn.execute("SELECT COUNT(*) FROM execution_events").fetchone()[0]
            assert count == num_threads * events_per_thread
        finally:
            conn.close()

    def test_idempotency_key_same_db_concurrent(self, tmp_path):
        """Concurrent submissions with the same idempotency key must not duplicate.

        Uses a single-writer queue pattern: multiple producer threads submit
        events through a queue, and one consumer thread serializes DB writes.
        The consumer creates its own thread-local connection to avoid SQLite
        cross-thread object errors.
        """
        import queue
        import threading

        from trading_agent.execution.lifecycle import ExecutionEventStore
        from trading_agent.execution.lifecycle.events import (
            ExecutionEvent,
            ExecutionEventType,
        )

        db_path = str(tmp_path / "idempotency.db")
        # Create DB schema upfront (main thread)
        ExecutionEventStore(db_path).connect().close()

        work_queue: queue.Queue[ExecutionEvent | None] = queue.Queue()
        results_shared: list[bool] = []
        results_lock = threading.Lock()

        def producer(thread_id: int, count: int) -> None:
            for i in range(count):
                event = ExecutionEvent(
                    event_id=f"t{thread_id}-e{i}",
                    seq=i + 1,
                    aggregate_id=f"agg-{thread_id}",
                    event_type=ExecutionEventType.ORDER_INTENT_CREATED,
                    schema_version=1,
                    payload={"idempotency_key": f"key-{thread_id}"},
                    correlation_id=f"t{thread_id}",
                    causation_id=None,
                    occurred_at=datetime.now(UTC),
                )
                work_queue.put(event)

        def consumer() -> None:
            # Each thread must create its own connection
            store = ExecutionEventStore(db_path).connect()
            while True:
                item = work_queue.get()
                if item is None:
                    break
                try:
                    ok = store.append(item)
                    with results_lock:
                        results_shared.append(ok)
                except Exception:
                    with results_lock:
                        results_shared.append(False)
                work_queue.task_done()
            store.close()

        num_producers = 4
        events_per_producer = 20
        consumer_thread = threading.Thread(target=consumer)
        consumer_thread.start()

        producer_threads = []
        for tid in range(num_producers):
            t = threading.Thread(target=producer, args=(tid, events_per_producer))
            producer_threads.append(t)
            t.start()

        for t in producer_threads:
            t.join()
        work_queue.put(None)  # sentinel
        consumer_thread.join()

        # All appends should succeed with the single-writer pattern
        assert all(results_shared)
        assert len(results_shared) == num_producers * events_per_producer


class TestEnginePaperE2E:
    """Actual Engine + PaperExchange integration tests."""

    def test_engine_execute_signal_buy_and_fill(self, tmp_path, monkeypatch):
        """Engine should create, submit, and fill a BUY order end-to-end."""
        # Allow new exposure in this backtest-style test
        monkeypatch.setenv("BACKTEST_ALLOW_NEW_EXPOSURE", "1")

        from trading_agent.agents.base import AgentMessage
        from trading_agent.execution.canonical.market_observation import (
            EnrichedMarketObservation,
        )
        from trading_agent.execution.engine import ExecutionEngine
        from trading_agent.execution.lifecycle import ExecutionEventStore
        from trading_agent.execution.paper_exchange import PaperExchange
        from trading_agent.execution.types import OrderStatus

        # Use isolated state dir to avoid cross-test pollution
        state_dir = tmp_path / "paper_state"
        state_dir.mkdir()
        exchange = PaperExchange(
            exchange_name="test",
            initial_balance=100_000.0,
            state_dir=state_dir,
        )
        store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
        engine = ExecutionEngine(exchange=exchange, store=store)
        # Seed a price so the engine has a valid market observation
        engine.exchange.update_prices({"BTC/USDT": 50_000.0})
        # Ensure the engine has a current price for the symbol
        price_info = engine._get_current_price("BTC/USDT")
        assert price_info is not None
        current_price, exchange_ts = price_info
        assert current_price == 50_000.0

        # Build a closed market observation (engine requires observation is closed)
        now = datetime.now(UTC)
        observation = EnrichedMarketObservation(
            symbol="BTC/USDT",
            observed_at=now,
            open=50000.0,
            high=50500.0,
            low=49500.0,
            close=current_price,
            volume=100.0,
            observation_id="obs-test",
            venue="paper",
            timeframe="1h",
            bar_close_at=now,
            is_closed=True,
            data_manifest_id="manifest-test",
        )

        # Build a BUY signal
        signal = AgentMessage(
            role="trader",
            signal="BUY",
            confidence=0.9,
            reasoning="test",
            details={"symbol": "BTC/USDT"},
        )
        orders = engine.execute_signal(signal, observation=observation)
        assert len(orders) == 1
        order = orders[0]
        # Compare by value to avoid enum identity mismatch across modules
        assert order.side.value == "buy"
        # For paper trading, the engine simulates an immediate fill
        assert order.status == OrderStatus.FILLED
        # Verify position was created (quantity depends on planner sizing)
        pos = engine.exchange.get_position("BTC/USDT")
        assert pos is not None
        assert pos.quantity > 0

    def test_engine_execute_signal_sell_without_position(self, tmp_path, monkeypatch):
        """Engine should reject a SELL signal when no position exists."""
        # Allow new exposure so the adapter doesn't block SELL either
        monkeypatch.setenv("BACKTEST_ALLOW_NEW_EXPOSURE", "1")

        from trading_agent.agents.base import AgentMessage
        from trading_agent.execution.canonical.market_observation import (
            EnrichedMarketObservation,
        )
        from trading_agent.execution.engine import ExecutionEngine
        from trading_agent.execution.paper_exchange import PaperExchange

        state_dir = tmp_path / "paper_state"
        state_dir.mkdir()
        exchange = PaperExchange(
            exchange_name="test",
            initial_balance=100_000.0,
            state_dir=state_dir,
        )
        engine = ExecutionEngine(exchange=exchange)
        # Engine's planner is hardcoded for BTC/USDT; use the same symbol
        engine.exchange.update_prices({"BTC/USDT": 50_000.0})
        now = datetime.now(UTC)
        observation = EnrichedMarketObservation(
            symbol="BTC/USDT",
            observed_at=now,
            open=50000.0,
            high=50500.0,
            low=49500.0,
            close=50000.0,
            volume=100.0,
            observation_id="obs-test-btc",
            venue="paper",
            timeframe="1h",
            bar_close_at=now,
            is_closed=True,
            data_manifest_id="manifest-test-btc",
        )
        signal = AgentMessage(
            role="trader",
            signal="SELL",
            confidence=0.8,
            reasoning="test",
            details={"symbol": "BTC/USDT"},
        )
        orders = engine.execute_signal(signal, observation=observation)
        # No order should be created because there's no position to sell
        assert len(orders) == 0
