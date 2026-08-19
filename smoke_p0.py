"""Smoke tests to reproduce current P0 execution-safety failures.

This script exercises realistic minimal code paths and records failures
before any fixes are applied.
"""

from __future__ import annotations

import sys
import traceback
from datetime import UTC, datetime

# Use venv Python path
sys.path.insert(0, "/home/huythong/.qwenpaw/workspaces/trading/src")

results: list[dict] = []


def record(name: str, passed: bool, detail: str = "") -> None:
    results.append({"name": name, "passed": passed, "detail": detail})
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {name}: {detail}")


def test_execution_engine_init() -> None:
    """ExecutionEngine initialization."""
    try:
        from trading_agent.execution.engine import ExecutionEngine

        engine = ExecutionEngine()
        record("engine_init", True, "ExecutionEngine initialized")
    except Exception as exc:
        record("engine_init", False, f"{exc}\n{traceback.format_exc()}")


def test_engine_buy_path() -> None:
    """ExecutionEngine BUY path with synthetic signal."""
    try:
        from trading_agent.agents.base import AgentMessage
        from trading_agent.execution.engine import ExecutionEngine
        from trading_agent.execution.canonical import (
            EnrichedMarketObservation,
        )
        from trading_agent.execution.canonical.events import (
            compute_observation_id,
        )

        engine = ExecutionEngine()
        symbol = "BTC/USDT"
        price = 50_000.0

        # Build a minimal source-confirmed observation
        obs_id = compute_observation_id(
            venue="paper",
            symbol=symbol,
            timeframe="1h",
            bar_close_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            data_manifest_id="test-manifest",
        )
        observation = EnrichedMarketObservation(
            observation_id=obs_id,
            symbol=symbol,
            observed_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            open=price,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            volume=1.0,
            features={},
            venue="paper",
            source="test",
            timeframe="1h",
            bar_close_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            is_closed=True,
            data_manifest_id="test-manifest",
        )

        signal = AgentMessage(
            role="trader",
            signal="BUY",
            confidence=0.8,
            reasoning="test",
            details={"symbol": symbol},
        )
        orders = engine.execute_signal(signal, observation=observation)
        record("engine_buy_path", True, f"produced {len(orders)} orders")
    except Exception as exc:
        record("engine_buy_path", False, f"{exc}\n{traceback.format_exc()}")


def test_engine_sell_path() -> None:
    """ExecutionEngine SELL path with existing position."""
    try:
        from trading_agent.agents.base import AgentMessage
        from trading_agent.execution.engine import ExecutionEngine
        from trading_agent.execution.canonical import (
            EnrichedMarketObservation,
        )
        from trading_agent.execution.canonical.events import compute_observation_id

        engine = ExecutionEngine()
        symbol = "BTC/USDT"
        price = 50_000.0

        # Create a position first via BUY
        obs_id = compute_observation_id(
            venue="paper",
            symbol=symbol,
            timeframe="1h",
            bar_close_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            data_manifest_id="test-manifest",
        )
        observation = EnrichedMarketObservation(
            observation_id=obs_id,
            symbol=symbol,
            observed_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            open=price,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            volume=1.0,
            features={},
            venue="paper",
            source="test",
            timeframe="1h",
            bar_close_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            is_closed=True,
            data_manifest_id="test-manifest",
        )
        buy_signal = AgentMessage(
            role="trader",
            signal="BUY",
            confidence=0.8,
            reasoning="test",
            details={"symbol": symbol},
        )
        engine.execute_signal(buy_signal, observation=observation)

        # Now SELL
        sell_signal = AgentMessage(
            role="trader",
            signal="SELL",
            confidence=0.8,
            reasoning="test",
            details={"symbol": symbol},
        )
        orders = engine.execute_signal(sell_signal, observation=observation)
        record("engine_sell_path", True, f"produced {len(orders)} orders")
    except Exception as exc:
        record("engine_sell_path", False, f"{exc}\n{traceback.format_exc()}")


def test_engine_close_all() -> None:
    """ExecutionEngine close_all with a position."""
    try:
        from trading_agent.execution.engine import ExecutionEngine
        from trading_agent.execution.canonical import (
            EnrichedMarketObservation,
        )
        from trading_agent.execution.canonical.events import compute_observation_id
        from trading_agent.agents.base import AgentMessage

        engine = ExecutionEngine()
        symbol = "BTC/USDT"
        price = 50_000.0
        obs_id = compute_observation_id(
            venue="paper",
            symbol=symbol,
            timeframe="1h",
            bar_close_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            data_manifest_id="test-manifest",
        )
        observation = EnrichedMarketObservation(
            observation_id=obs_id,
            symbol=symbol,
            observed_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            open=price,
            high=price * 1.01,
            low=price * 0.99,
            close=price,
            volume=1.0,
            features={},
            venue="paper",
            source="test",
            timeframe="1h",
            bar_close_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            is_closed=True,
            data_manifest_id="test-manifest",
        )
        buy_signal = AgentMessage(
            role="trader",
            signal="BUY",
            confidence=0.8,
            reasoning="test",
            details={"symbol": symbol},
        )
        engine.execute_signal(buy_signal, observation=observation)
        orders = engine.close_all(reason="smoke_test")
        record("engine_close_all", True, f"produced {len(orders)} close orders")
    except Exception as exc:
        record("engine_close_all", False, f"{exc}\n{traceback.format_exc()}")


def test_broker_gateway_paper() -> None:
    """BrokerGateway with PaperExchange adapter."""
    try:
        from trading_agent.execution.canonical.broker_gateway import (
            BrokerGateway,
            AuthorizedOrder,
        )
        from trading_agent.execution.paper_exchange import PaperExchange
        from trading_agent.execution.lifecycle import ExecutionEventStore

        adapter = PaperExchange(exchange_name="paper", initial_balance=10_000.0)
        store = ExecutionEventStore(":memory:").connect()
        gateway = BrokerGateway(adapter=adapter, store=store)

        # Try to create an AuthorizedOrder directly (should fail or be unsafe)
        try:
            auth = AuthorizedOrder(
                token="__authorized__",
                intent_id="test-intent",
                symbol="BTC/USDT",
                side="buy",
                quantity=0.01,
                idempotency_key="test-idem",
                price_reference=50_000.0,
                risk_decision_id="test-risk",
                forecast_fingerprint="test-fp",
                model_artifact_id="test-model",
                permission_result="ALLOW",
                authorization_id="test-auth",
                lifecycle_event_id="test-event",
                correlation_id="test-corr",
                exposure_effect="increase",
                current_exposure=0.0,
                resulting_exposure=0.01,
                authorized_at=datetime.now(UTC).isoformat(),
                authorization_hash="test-hash",
            )
            record(
                "broker_gateway_paper",
                True,
                "AuthorizedOrder created directly (unsafe but works)",
            )
        except Exception as exc:
            record(
                "broker_gateway_paper", False, f"AuthorizedOrder creation failed: {exc}"
            )
    except Exception as exc:
        record("broker_gateway_paper", False, f"{exc}\n{traceback.format_exc()}")


def test_legacy_adapter() -> None:
    """LegacyDecisionAdapter BUY/SELL."""
    try:
        from trading_agent.execution.canonical.legacy_adapter import (
            LegacyDecisionAdapter,
        )
        from trading_agent.execution.canonical.events import compute_observation_id
        from trading_agent.execution.canonical.market_observation import (
            EnrichedMarketObservation,
        )
        from trading_agent.agents.base import AgentMessage

        adapter = LegacyDecisionAdapter()
        obs_id = compute_observation_id(
            venue="paper",
            symbol="BTC/USDT",
            timeframe="1h",
            bar_close_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            data_manifest_id="test-manifest",
        )
        observation = EnrichedMarketObservation(
            observation_id=obs_id,
            symbol="BTC/USDT",
            observed_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            open=50_000.0,
            high=51_000.0,
            low=49_000.0,
            close=50_000.0,
            volume=1.0,
            features={},
            venue="paper",
            source="test",
            timeframe="1h",
            bar_close_at=datetime(2026, 8, 18, 3, 0, 0, tzinfo=UTC),
            is_closed=True,
            data_manifest_id="test-manifest",
        )

        buy_signal = AgentMessage(
            role="trader",
            signal="BUY",
            confidence=0.8,
            reasoning="test",
            details={"symbol": "BTC/USDT"},
        )
        risk, target = adapter.adapt(buy_signal, observation)
        record(
            "legacy_adapter_buy",
            True,
            f"target={target.exposure}, approved={risk.approved}",
        )

        sell_signal = AgentMessage(
            role="trader",
            signal="SELL",
            confidence=0.8,
            reasoning="test",
            details={"symbol": "BTC/USDT"},
        )
        risk2, target2 = adapter.adapt(sell_signal, observation)
        record(
            "legacy_adapter_sell",
            True,
            f"target={target2.exposure}, approved={risk2.approved}",
        )
    except Exception as exc:
        record("legacy_adapter", False, f"{exc}\n{traceback.format_exc()}")


def test_cancel_race() -> None:
    """Cancel race: FILLED during cancel should not become CANCELED."""
    try:
        from trading_agent.execution.lifecycle import (
            ExecutionLifecycle,
            ExecutionEventStore,
        )
        from trading_agent.execution.canonical.broker_gateway import (
            CancelEvidence,
            CancelState,
        )

        store = ExecutionEventStore(":memory:").connect()
        lifecycle = ExecutionLifecycle(
            store,
            inventory_source=lambda symbol, side: 10.0,
        )

        intent_id = "cancel-race-test"
        lifecycle.create_order_intent(
            intent_id=intent_id, symbol="BTC/USDT", side="sell", size=0.01
        )
        lifecycle.approve_risk(intent_id=intent_id)
        lifecycle.authorize_order(
            intent_id=intent_id,
            authorization_id=f"auth-{intent_id}",
            idempotency_key=f"idem-{intent_id}",
            payload_hash="hash",
            risk_decision_id="risk",
            forecast_fingerprint="fp",
            model_artifact_id="model",
            permission="ALLOW",
            symbol="BTC/USDT",
            side="sell",
            quantity=0.01,
            exposure_effect="reduce",
            current_exposure=0.01,
            resulting_exposure=0.0,
            authorized_at=datetime.now(UTC).isoformat(),
        )
        lifecycle.request_broker_submission(intent_id=intent_id)
        lifecycle.submit_order(intent_id=intent_id)
        lifecycle.request_cancel(intent_id=intent_id, reason="test")
        # Simulate broker saying FILLED during cancel
        evidence = CancelEvidence(
            broker_order_id="broker-1",
            state=CancelState.FILLED,
            venue="paper",
            confirmed_at=datetime.now(UTC).isoformat(),
            source="BROKER",
            raw_response={"status": "FILLED"},
        )
        event = lifecycle.confirm_cancel(intent_id=intent_id, evidence=evidence)
        order_state = lifecycle.order(intent_id)
        if order_state and order_state.status.value == "filled":
            record("cancel_race_filled", True, "FILLED during cancel stays FILLED")
        else:
            record(
                "cancel_race_filled",
                False,
                f"status={order_state.status.value if order_state else 'missing'}",
            )
    except Exception as exc:
        record("cancel_race_filled", False, f"{exc}\n{traceback.format_exc()}")


def test_protective_ack() -> None:
    """Protective ACK validation."""
    try:
        from trading_agent.execution.lifecycle import (
            ExecutionLifecycle,
            ExecutionEventStore,
        )
        from trading_agent.execution.canonical.broker_gateway import (
            ProtectiveAckEvidence,
        )

        store = ExecutionEventStore(":memory:").connect()
        lifecycle = ExecutionLifecycle(
            store,
            inventory_source=lambda symbol, side: 10.0,
        )

        intent_id = "protective-test"
        lifecycle.create_order_intent(
            intent_id=intent_id, symbol="BTC/USDT", side="buy", size=0.01
        )
        lifecycle.approve_risk(intent_id=intent_id)
        lifecycle.authorize_order(
            intent_id=intent_id,
            authorization_id=f"auth-{intent_id}",
            idempotency_key=f"idem-{intent_id}",
            payload_hash="hash",
            risk_decision_id="risk",
            forecast_fingerprint="fp",
            model_artifact_id="model",
            permission="ALLOW",
            symbol="BTC/USDT",
            side="buy",
            quantity=0.01,
            exposure_effect="increase",
            current_exposure=0.0,
            resulting_exposure=0.01,
            authorized_at=datetime.now(UTC).isoformat(),
        )
        lifecycle.request_broker_submission(intent_id=intent_id)
        lifecycle.submit_order(intent_id=intent_id)
        lifecycle.receive_fill(intent_id=intent_id, size=0.01, price=50_000.0)
        protective_event = lifecycle.create_protective_order(
            symbol="BTC/USDT",
            kind="stop_loss",
            trigger_price=47_500.0,
            parent_intent_id=intent_id,
        )
        evidence = ProtectiveAckEvidence(
            broker_order_id="prot-1",
            broker_ack_id="ack-1",
            venue="paper",
            broker_status="open",
            acknowledged_at=datetime.now(UTC).isoformat(),
            protected_symbol="BTC/USDT",
            protected_quantity=0.01,
            evidence_source="BROKER",
            raw_response={},
        )
        ack_event = lifecycle.acknowledge_protective_order(
            protective_order_id=protective_event.aggregate_id,
            evidence=evidence,
        )
        record("protective_ack", True, "protective acknowledged")
    except Exception as exc:
        record("protective_ack", False, f"{exc}\n{traceback.format_exc()}")


def test_idempotency_retry() -> None:
    """Idempotency retry: same key should not create duplicate intent."""
    try:
        from trading_agent.execution.lifecycle import (
            ExecutionLifecycle,
            ExecutionEventStore,
        )

        store = ExecutionEventStore(":memory:").connect()
        lifecycle = ExecutionLifecycle(
            store,
            inventory_source=lambda symbol, side: 10.0,
        )
        idem_key = "idempotency-retry-key"
        lifecycle.create_order_intent(
            intent_id="intent-1",
            symbol="BTC/USDT",
            side="buy",
            size=0.01,
            idempotency_key=idem_key,
        )
        try:
            lifecycle.create_order_intent(
                intent_id="intent-2",
                symbol="BTC/USDT",
                side="buy",
                size=0.01,
                idempotency_key=idem_key,
            )
            record(
                "idempotency_retry",
                False,
                "duplicate intent created with same idempotency key",
            )
        except Exception as exc:
            if "duplicate idempotency_key" in str(exc):
                record("idempotency_retry", True, "duplicate rejected")
            else:
                record("idempotency_retry", False, f"unexpected error: {exc}")
    except Exception as exc:
        record("idempotency_retry", False, f"{exc}\n{traceback.format_exc()}")


def test_global_replay() -> None:
    """Global replay equality."""
    try:
        from trading_agent.execution.lifecycle import (
            ExecutionLifecycle,
            ExecutionEventStore,
        )

        store = ExecutionEventStore(":memory:").connect()
        lifecycle = ExecutionLifecycle(
            store,
            inventory_source=lambda symbol, side: 10.0,
        )
        intent_id = "replay-test"
        lifecycle.create_order_intent(
            intent_id=intent_id, symbol="BTC/USDT", side="buy", size=0.01
        )
        lifecycle.approve_risk(intent_id=intent_id)
        lifecycle.authorize_order(
            intent_id=intent_id,
            authorization_id=f"auth-{intent_id}",
            idempotency_key=f"idem-{intent_id}",
            payload_hash="hash",
            risk_decision_id="risk",
            forecast_fingerprint="fp",
            model_artifact_id="model",
            permission="ALLOW",
            symbol="BTC/USDT",
            side="buy",
            quantity=0.01,
            exposure_effect="increase",
            current_exposure=0.0,
            resulting_exposure=0.01,
            authorized_at=datetime.now(UTC).isoformat(),
        )
        events = store.read_events_global()
        replayed = lifecycle.replay(events)
        original = lifecycle.snapshot_state()
        # Basic check: same number of orders
        if len(replayed.orders) == len(original["orders"]):
            record("global_replay", True, f"replayed {len(replayed.orders)} orders")
        else:
            record(
                "global_replay",
                False,
                f"replayed {len(replayed.orders)} vs original {len(original['orders'])}",
            )
    except Exception as exc:
        record("global_replay", False, f"{exc}\n{traceback.format_exc()}")


if __name__ == "__main__":
    print("=" * 60)
    print("P0 SMOKE TEST — reproducing current failures")
    print("=" * 60)
    test_execution_engine_init()
    test_engine_buy_path()
    test_engine_sell_path()
    test_engine_close_all()
    test_broker_gateway_paper()
    test_legacy_adapter()
    test_cancel_race()
    test_protective_ack()
    test_idempotency_retry()
    test_global_replay()

    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(results)}")
    print("=" * 60)
    for r in results:
        if not r["passed"]:
            print(f"FAILED: {r['name']}: {r['detail'][:200]}")

    if failed > 0:
        sys.exit(1)
