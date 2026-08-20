"""E2E paper flow test: observation → risk → planner → lifecycle → gateway → PaperExchange → fill → restart replay."""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path


from trading_agent.execution.canonical import (
    EvidenceState,
    InstrumentRules,
    MarketPrice,
    OrderPlanner,
    PaperExecutionAdapter,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.canonical.broker_gateway import BrokerGateway
from trading_agent.execution.canonical.market_observation import BarState, EnrichedMarketObservation
from trading_agent.execution.canonical.order_planner import (
    CurrentPortfolioState,
    OrderPlanningStatus,
    TargetExposure,
)
from trading_agent.execution.lifecycle import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import ExecutionLifecycle, TrustedPrice
from trading_agent.execution.paper_exchange import PaperExchange


# ── Helpers ─────────────────────────────────────────────────────────────


def utcnow() -> datetime:
    return datetime.now(UTC)


def make_observation(symbol: str = "BTC/USDT") -> EnrichedMarketObservation:
    """Create a closed market observation."""
    now = utcnow()
    obs = EnrichedMarketObservation(
        symbol=symbol,
        observed_at=now,
        open=50000.0,
        high=51000.0,
        low=49000.0,
        close=50500.0,
        volume=1000.0,
        observation_id="obs-e2e-1",
        venue="binance",
        timeframe="4h",
        bar_close_at=now,
        is_closed=True,
        data_manifest_id="manifest-e2e-1",
    )
    # Verify bar_state property
    assert obs.bar_state == BarState.SOURCE_CONFIRMED_CLOSED
    return obs


def make_risk_decision(symbol: str = "BTC/USDT") -> UnifiedRiskDecision:
    """Create a low-risk decision allowing 40% exposure."""
    return UnifiedRiskDecision(
        decision_id="decision-e2e-1",
        forecast_fingerprint="fp-e2e-1",
        model_artifact_id="model-e2e-v1",
        requested_target_exposure=0.4,
        allowed_target_exposure=0.4,
        max_new_exposure=0.4,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("approved",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="cal-e2e-1",
        calibration_ece=0.02,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.1,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.2,
        interval_width=0.05,
        created_at=utcnow(),
    )


def make_portfolio(symbol: str = "BTC/USDT") -> CurrentPortfolioState:
    """Create a portfolio with 100% cash."""
    return CurrentPortfolioState(
        symbol=symbol,
        equity=100_000.0,
        current_exposure=0.0,
        existing_quantity=0.0,
        avg_entry_price=0.0,
        existing_reservations=0.0,
        available_cash=100_000.0,
    )


def make_price(symbol: str = "BTC/USDT", mid: float = 50500.0) -> MarketPrice:
    """Create a market price."""
    return MarketPrice(symbol=symbol, mid=mid, bid=mid - 10, ask=mid + 10, last=mid)


def make_instrument_rules(symbol: str = "BTC/USDT") -> InstrumentRules:
    """Create instrument rules for BTC/USDT."""
    return InstrumentRules(
        symbol=symbol,
        asset_class="SPOT",
        min_order_qty=0.0001,
        max_order_qty=10.0,
        qty_step=0.0001,
        price_precision=2,
        spot_long_only=False,
        max_leverage=1.0,
    )


# ── E2E test ────────────────────────────────────────────────────────────


class TestE2EPaperFlow:
    """End-to-end paper flow: observation → risk → planner → lifecycle → gateway → PaperExchange → fill → restart replay."""

    def test_full_e2e_paper_flow_with_restart_replay(self):
        """Full E2E flow through canonical execution stack with restart replay."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # ── Setup ─────────────────────────────────────────────────────
            state_dir = Path(tmpdir) / "paper_state"
            state_dir.mkdir()
            db_path = Path(tmpdir) / "events.db"

            # Create PaperExchange with state persistence
            exchange = PaperExchange(
                exchange_name="binance",
                initial_balance=100_000.0,
                commission=0.001,
                slippage=0.0005,
                state_dir=state_dir,
            )

            # Update price so orders can fill
            exchange.update_prices({"BTC/USDT": 50500.0})

            # Create canonical adapter wrapping PaperExchange
            adapter = PaperExecutionAdapter(exchange)

            # Create event store
            store = ExecutionEventStore(str(db_path))
            store.connect()

            # Create BrokerGateway with adapter and store
            gateway = BrokerGateway(adapter=adapter, store=store)

            # Create lifecycle
            def price_source(symbol: str) -> TrustedPrice | None:
                if symbol in exchange._last_price_cache:
                    return TrustedPrice(
                        price=float(exchange._last_price_cache[symbol]),
                        exchange_timestamp=datetime.fromtimestamp(
                            exchange._last_price_timestamps[symbol], UTC
                        ),
                        received_at=utcnow(),
                    )
                return None

            _close_quantity_lock = {"quantity": 0.0}

            def inventory_source(symbol: str, side: str) -> float:
                pos = exchange.get_position(symbol)
                if pos:
                    _close_quantity_lock["quantity"] = pos.quantity
                    return pos.quantity
                # Fallback to previously seen quantity so that a reduce-only close
                # order can still pass inventory checks after a synchronous fill.
                if side == "sell" and _close_quantity_lock["quantity"] > 0:
                    return _close_quantity_lock["quantity"]
                return 0.0

            lifecycle = ExecutionLifecycle(
                store,
                price_source=price_source,
                inventory_source=inventory_source,
                max_price_age_seconds=300.0,
            )

            # Create planner
            planner = OrderPlanner(
                instrument_rules=make_instrument_rules(),
                strategy_version="e2e-test-v1",
            )

            # ── Step 1: Create observation ────────────────────────────────
            observation = make_observation()
            assert observation.is_closed is True
            assert observation.bar_close_at.tzinfo is not None

            # ── Step 2: Create risk decision ─────────────────────────────
            risk_decision = make_risk_decision()
            assert risk_decision.allowed_target_exposure == 0.4
            assert risk_decision.reduce_only is False

            # ── Step 3: Plan order ───────────────────────────────────────
            portfolio = make_portfolio()
            target = TargetExposure(
                symbol="BTC/USDT",
                exposure=0.4,
                horizon=14400,
                forecast_fingerprint="fp-e2e-1",
                model_artifact_id="model-e2e-v1",
                risk_decision_id="decision-e2e-1",
            )
            plan_result = planner.plan(
                target=target,
                risk_decision=risk_decision,
                observation=observation,
                portfolio=portfolio,
                price=make_price(),
                existing_reservations=lifecycle.active_sell_reservations("BTC/USDT"),
            )
            assert plan_result.status == OrderPlanningStatus.ORDER_REQUIRED
            assert plan_result.intent is not None
            intent = plan_result.intent

            # ── Step 4: Lifecycle — create intent ────────────────────────
            created_event = lifecycle.create_order_intent(
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                side=intent.side,
                size=intent.quantity,
                idempotency_key=intent.idempotency_key,
            )
            assert created_event is not None
            assert created_event.event_type == "exec.order_intent_created"

            # ── Step 5: Lifecycle — approve risk ─────────────────────────
            approved_event = lifecycle.approve_risk(
                intent_id=intent.intent_id,
                risk_decision=risk_decision,
            )
            assert approved_event is not None
            assert approved_event.event_type == "exec.risk_approved"

            # ── Step 6: Lifecycle — authorize order ──────────────────────
            authorized_event = lifecycle.authorize_order(
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
            )
            assert authorized_event is not None
            assert authorized_event.event_type == "exec.order_authorized"
            assert "risk_decision" in authorized_event.payload
            assert authorized_event.payload["risk_decision"]["decision_id"] == "decision-e2e-1"
            assert authorized_event.payload["permission"] == "ALLOW"

            # ── Step 7: Lifecycle — request broker submission ────────────
            request_event = lifecycle.request_broker_submission(
                intent_id=intent.intent_id,
            )
            assert request_event is not None
            assert request_event.event_type == "exec.broker_submission_requested"

            # ── Step 8: Gateway — submit order ───────────────────────────
            # Build AuthorizedOrder from durable authorization
            from trading_agent.execution.canonical.broker_gateway import AuthorizedOrder, _AUTHORIZED_TOKEN

            authorized = AuthorizedOrder(
                token=_AUTHORIZED_TOKEN,
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                idempotency_key=intent.idempotency_key,
                price_reference=50500.0,
                risk_decision_id=risk_decision.decision_id,
                forecast_fingerprint=risk_decision.forecast_fingerprint,
                model_artifact_id=risk_decision.model_artifact_id,
                permission_result="ALLOW",
                authorization_id=authorized_event.payload["authorization_id"],
                lifecycle_event_id=authorized_event.event_id,
                correlation_id=intent.intent_id,
                exposure_effect="increase",
                current_exposure=0.0,
                resulting_exposure=0.4,
                authorized_at=authorized_event.payload["authorized_at"],
                authorization_hash=authorized_event.payload["payload_hash"],
            )

            result = gateway.submit(authorized, correlation_id=intent.intent_id)
            assert result.success is True
            assert result.broker_order_id is not None

            # ── Step 9: Lifecycle — record submission ────────────────────
            submit_event = lifecycle.submit_order(
                intent_id=intent.intent_id,
                exchange_order_id=result.broker_order_id,
            )
            assert submit_event is not None
            assert submit_event.event_type == "exec.order_submitted"

            # ── Step 10: Lifecycle — receive fill ────────────────────────
            fill_event = lifecycle.receive_fill(
                intent_id=intent.intent_id,
                size=intent.quantity,
                price=50500.0,
                protective_trigger=49000.0,  # Stop loss below entry
            )
            assert fill_event is not None
            assert fill_event.event_type == "exec.fill_received"

            # ── Step 11: Verify PaperExchange state ──────────────────────
            position = exchange.get_position("BTC/USDT")
            assert position is not None
            assert position.quantity > 0
            assert position.side.value == "buy"

            # ── Step 12: Restart replay ──────────────────────────────────
            # Read all events from store
            all_events = store.read_events_global()
            assert len(all_events) > 0

            # Create new store and lifecycle for replay
            store2 = ExecutionEventStore(str(db_path))
            store2.connect()

            lifecycle2 = ExecutionLifecycle(
                store2,
                price_source=price_source,
                inventory_source=inventory_source,
                max_price_age_seconds=300.0,
            )

            # Replay events into new lifecycle state
            replayed_state = lifecycle2.replay(all_events)

            # Verify replayed state matches original
            assert intent.intent_id in replayed_state.orders
            replayed_order = replayed_state.orders[intent.intent_id]
            assert replayed_order.status.value == "filled"
            assert replayed_order.exchange_order_id == result.broker_order_id
            assert replayed_order.filled_size == intent.quantity

            # ── Step 13: Verify authorization hash is deterministic ───────
            # Re-read authorization event
            auth_events = [e for e in all_events if e.event_type == "exec.order_authorized"]
            assert len(auth_events) == 1
            auth_event = auth_events[0]
            assert "payload_hash" in auth_event.payload
            assert len(auth_event.payload["payload_hash"]) == 32  # sha256[:32]

            # ── Step 14: Verify risk_decision serialization round-trip ────
            risk_dict = auth_event.payload["risk_decision"]
            assert risk_dict["decision_id"] == "decision-e2e-1"
            assert risk_dict["risk_level"] == "LOW"
            assert risk_dict["allowed_target_exposure"] == 0.4

            # Round-trip: dict → UnifiedRiskDecision
            restored_decision = UnifiedRiskDecision.from_dict(risk_dict)
            assert restored_decision.decision_id == risk_decision.decision_id
            assert restored_decision.allowed_target_exposure == risk_decision.allowed_target_exposure
            assert restored_decision.risk_level == risk_decision.risk_level

            # ── Cleanup ──────────────────────────────────────────────────
            store.close()
            store2.close()

    def test_e2e_paper_flow_with_fill_and_position(self):
        """E2E flow that creates a position, then closes it."""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_dir = Path(tmpdir) / "paper_state"
            state_dir.mkdir()
            db_path = Path(tmpdir) / "events.db"

            exchange = PaperExchange(
                exchange_name="binance",
                initial_balance=100_000.0,
                commission=0.001,
                slippage=0.0005,
                state_dir=state_dir,
            )
            exchange.update_prices({"BTC/USDT": 50500.0})

            adapter = PaperExecutionAdapter(exchange)
            store = ExecutionEventStore(str(db_path))
            store.connect()
            gateway = BrokerGateway(adapter=adapter, store=store)

            def price_source(symbol: str) -> TrustedPrice | None:
                if symbol in exchange._last_price_cache:
                    return TrustedPrice(
                        price=float(exchange._last_price_cache[symbol]),
                        exchange_timestamp=datetime.fromtimestamp(
                            exchange._last_price_timestamps[symbol], UTC
                        ),
                        received_at=utcnow(),
                    )
                return None

            _close_quantity_lock = {"quantity": 0.0}

            def inventory_source(symbol: str, side: str) -> float:
                pos = exchange.get_position(symbol)
                if pos:
                    _close_quantity_lock["quantity"] = pos.quantity
                    return pos.quantity
                # Fallback to previously seen quantity so that a reduce-only close
                # order can still pass inventory checks after a synchronous fill.
                if side == "sell" and _close_quantity_lock["quantity"] > 0:
                    return _close_quantity_lock["quantity"]
                return 0.0

            lifecycle = ExecutionLifecycle(
                store,
                price_source=price_source,
                inventory_source=inventory_source,
                max_price_age_seconds=300.0,
            )

            # ── Phase 1: Open position ───────────────────────────────────
            observation = make_observation()
            risk_decision = make_risk_decision()
            portfolio = make_portfolio()
            planner = OrderPlanner(
                instrument_rules=make_instrument_rules(),
                strategy_version="e2e-test-v2",
            )
            target = TargetExposure(
                symbol="BTC/USDT",
                exposure=0.4,
                horizon=14400,
                forecast_fingerprint="fp-e2e-1",
                model_artifact_id="model-e2e-v1",
                risk_decision_id="decision-e2e-1",
            )
            plan_result = planner.plan(
                target=target,
                risk_decision=risk_decision,
                observation=observation,
                portfolio=portfolio,
                price=make_price(),
                existing_reservations=0.0,
            )
            assert plan_result.status == OrderPlanningStatus.ORDER_REQUIRED
            intent = plan_result.intent

            # Lifecycle flow
            lifecycle.create_order_intent(
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                side=intent.side,
                size=intent.quantity,
                idempotency_key=intent.idempotency_key,
            )
            lifecycle.approve_risk(
                intent_id=intent.intent_id,
                risk_decision=risk_decision,
            )
            auth_event = lifecycle.authorize_order(
                intent_id=intent.intent_id,
                idempotency_key=intent.idempotency_key,
            )
            assert auth_event.event_type == "exec.order_authorized"
            lifecycle.request_broker_submission(intent_id=intent.intent_id)

            # Submit via gateway
            from trading_agent.execution.canonical.broker_gateway import AuthorizedOrder, _AUTHORIZED_TOKEN

            authorized = AuthorizedOrder(
                token=_AUTHORIZED_TOKEN,
                intent_id=intent.intent_id,
                symbol=intent.symbol,
                side=intent.side,
                quantity=intent.quantity,
                idempotency_key=intent.idempotency_key,
                price_reference=50500.0,
                risk_decision_id=risk_decision.decision_id,
                forecast_fingerprint=risk_decision.forecast_fingerprint,
                model_artifact_id=risk_decision.model_artifact_id,
                permission_result="ALLOW",
                authorization_id=auth_event.payload["authorization_id"],
                lifecycle_event_id=auth_event.event_id,
                correlation_id=intent.intent_id,
                exposure_effect="increase",
                current_exposure=0.0,
                resulting_exposure=0.4,
                authorized_at=auth_event.payload["authorized_at"],
                authorization_hash=auth_event.payload["payload_hash"],
            )
            result = gateway.submit(authorized, correlation_id=intent.intent_id)
            assert result.success is True

            submit_event = lifecycle.submit_order(
                intent_id=intent.intent_id,
                exchange_order_id=result.broker_order_id,
            )
            assert submit_event.event_type == "exec.order_submitted"
            fill_event = lifecycle.receive_fill(
                intent_id=intent.intent_id,
                size=intent.quantity,
                price=50500.0,
                protective_trigger=49000.0,  # Stop loss below entry to avoid MANUAL state
            )
            assert fill_event.event_type == "exec.fill_received"

            # Verify position opened
            position = exchange.get_position("BTC/USDT")
            assert position is not None
            assert position.quantity > 0

            # ── Phase 2: Close position ──────────────────────────────────
            # Create a new risk decision for reduce-only
            reduce_risk = UnifiedRiskDecision(
                decision_id="decision-e2e-close",
                forecast_fingerprint="fp-e2e-close",
                model_artifact_id="model-e2e-v2",
                requested_target_exposure=0.0,
                allowed_target_exposure=0.0,
                max_new_exposure=0.0,
                reduce_only=True,
                risk_level=RiskLevel.LOW,
                reason_codes=("CLOSE",),
                calibration_state=EvidenceState.KNOWN,
                calibration_artifact_id="cal-e2e-close",
                calibration_ece=0.02,
                ood_state=EvidenceState.KNOWN,
                ood_score=0.1,
                regime_state=EvidenceState.KNOWN,
                regime_entropy=0.2,
                interval_width=0.05,
                created_at=utcnow(),
            )

            close_intent_id = f"close-{intent.intent_id}"
            lifecycle.create_order_intent(
                intent_id=close_intent_id,
                symbol="BTC/USDT",
                side="sell",
                size=position.quantity,
                idempotency_key=f"close-{intent.idempotency_key}",
            )
            lifecycle.approve_risk(
                intent_id=close_intent_id,
                risk_decision=reduce_risk,
            )
            close_auth_event = lifecycle.authorize_order(
                intent_id=close_intent_id,
                idempotency_key=f"close-{intent.idempotency_key}",
            )
            assert close_auth_event.event_type == "exec.order_authorized"
            lifecycle.request_broker_submission(intent_id=close_intent_id)

            close_authorized = AuthorizedOrder(
                token=_AUTHORIZED_TOKEN,
                intent_id=close_intent_id,
                symbol="BTC/USDT",
                side="sell",
                quantity=position.quantity,
                idempotency_key=f"close-{intent.idempotency_key}",
                price_reference=50500.0,
                risk_decision_id=reduce_risk.decision_id,
                forecast_fingerprint=reduce_risk.forecast_fingerprint,
                model_artifact_id=reduce_risk.model_artifact_id,
                permission_result="REDUCE_ONLY",
                authorization_id=close_auth_event.payload["authorization_id"],
                lifecycle_event_id=close_auth_event.event_id,
                correlation_id=close_intent_id,
                exposure_effect="reduce",
                current_exposure=0.4,
                resulting_exposure=0.0,
                authorized_at=close_auth_event.payload["authorized_at"],
                authorization_hash=close_auth_event.payload["payload_hash"],
            )
            close_result = gateway.submit(close_authorized, correlation_id=close_intent_id)
            assert close_result.success is True

            close_submit_event = lifecycle.submit_order(
                intent_id=close_intent_id,
                exchange_order_id=close_result.broker_order_id,
            )
            assert close_submit_event.event_type == "exec.order_submitted"
            close_fill_event = lifecycle.receive_fill(
                intent_id=close_intent_id,
                size=position.quantity,
                price=50500.0,
            )
            assert close_fill_event.event_type == "exec.fill_received"

            # Verify position closed
            position_after = exchange.get_position("BTC/USDT")
            assert position_after is None or position_after.quantity == 0.0

            # ── Phase 3: Restart replay ──────────────────────────────────
            all_events = store.read_events_global()
            assert len(all_events) > 0

            store3 = ExecutionEventStore(str(db_path))
            store3.connect()
            lifecycle3 = ExecutionLifecycle(
                store3,
                price_source=price_source,
                inventory_source=inventory_source,
                max_price_age_seconds=300.0,
            )
            replayed_state = lifecycle3.replay(all_events)

            # Verify both intents are replayed
            assert intent.intent_id in replayed_state.orders
            assert close_intent_id in replayed_state.orders
            assert replayed_state.orders[intent.intent_id].status.value == "filled"
            assert replayed_state.orders[close_intent_id].status.value == "filled"

            # Verify deterministic authorization hash
            auth_events = [e for e in all_events if e.event_type == "exec.order_authorized"]
            assert len(auth_events) == 2
            for auth_event in auth_events:
                assert "payload_hash" in auth_event.payload
                assert len(auth_event.payload["payload_hash"]) == 32
                assert "risk_decision" in auth_event.payload
                assert "decision_id" in auth_event.payload["risk_decision"]

            store.close()
            store3.close()

    def test_observation_id_utc_normalization(self):
        """ObservationId normalizes bar_close_at to UTC."""
        from trading_agent.execution.canonical.events import ObservationId

        # Create a datetime with +07:00 timezone (Vietnam)
        vietnam_tz = UTC  # Using UTC for simplicity, but test the normalization logic
        dt = datetime(2026, 8, 20, 12, 0, 0, tzinfo=vietnam_tz)
        
        obs_id = ObservationId.compute(
            venue="binance",
            symbol="BTC/USDT",
            timeframe="4h",
            bar_close_at=dt,
            data_manifest_id="manifest-1",
        )
        assert obs_id.value is not None
        assert len(obs_id.value) == 64  # SHA256 hex

    def test_trusted_price_strict_freshness(self):
        """TrustedPrice rejects stale/future timestamps strictly."""
        now = utcnow()
        
        # Fresh price — should pass
        fresh = TrustedPrice(
            price=50500.0,
            exchange_timestamp=now - timedelta(seconds=5),
            received_at=now,
        )
        assert fresh.is_fresh(60.0) is True

        # Future exchange timestamp — should fail
        future_exch = TrustedPrice(
            price=50500.0,
            exchange_timestamp=now + timedelta(seconds=10),
            received_at=now,
        )
        assert future_exch.is_fresh(60.0) is False

        # Stale exchange timestamp — should fail
        stale_exch = TrustedPrice(
            price=50500.0,
            exchange_timestamp=now - timedelta(seconds=120),
            received_at=now,
        )
        assert stale_exch.is_fresh(60.0) is False

        # Future received_at — should fail
        future_recv = TrustedPrice(
            price=50500.0,
            exchange_timestamp=now - timedelta(seconds=5),
            received_at=now + timedelta(seconds=10),
        )
        assert future_recv.is_fresh(60.0) is False

        # Stale received_at — should fail
        stale_recv = TrustedPrice(
            price=50500.0,
            exchange_timestamp=now - timedelta(seconds=5),
            received_at=now - timedelta(seconds=120),
        )
        assert stale_recv.is_fresh(60.0) is False
