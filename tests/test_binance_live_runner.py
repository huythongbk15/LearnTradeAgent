from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from dataclasses import replace

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import live_enhanced_ma_binance as runner

from trading_agent.exchanges.models import (
    Order,
    OrderConstraintError,
    OrderSide,
    OrderType,
    TimeInForce,
)
from trading_agent.execution.live_safety import (
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
    DuplicateOrderError,
)
from trading_agent.execution.canonical import (
    BrokerGateway,
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.canonical.adapters import LiveBrokerExecutionAdapter
from trading_agent.execution.canonical.order_planner import (
    OrderPlanner,
    InstrumentRules,
    CurrentPortfolioState,
    MarketPrice,
)
from trading_agent.execution.canonical.market_observation import (
    EnrichedMarketObservation,
)
from trading_agent.research.forecast import TargetExposure
from trading_agent.execution.permission import PermissionContext
from trading_agent.execution.lifecycle import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle,
    PortfolioRiskSnapshot,
    TrustedPrice,
    ExposureEffect,
    ExecutionHealth,
    InvariantViolation,
)


def test_allocations_are_not_normalized():
    allocations = runner.parse_allocations(
        "BTC/USDT,SOL/USDT", "20,10", LiveRiskLimits()
    )
    assert allocations == [("BTC/USDT", 0.2), ("SOL/USDT", 0.1)]


def test_risk_profile_is_bound_to_exchange_mode():
    testnet = runner.argparse.Namespace(testnet=True, profile=None)
    mainnet = runner.argparse.Namespace(testnet=False, profile=None)
    assert runner.resolve_trading_profile(testnet, {}) == "testnet"
    assert runner.resolve_trading_profile(mainnet, {}) == "mainnet-canary"
    with pytest.raises(LiveSafetyError, match="Testnet requires"):
        runner.resolve_trading_profile(
            runner.argparse.Namespace(testnet=True, profile="mainnet-normal"),
            {},
        )
    with pytest.raises(LiveSafetyError, match="do not match"):
        runner.resolve_trading_profile(
            runner.argparse.Namespace(testnet=False, profile="mainnet-canary"),
            {"LIVE_TRADING_PROFILE": "mainnet-normal"},
        )


def test_order_reconciliation_timeout_is_bounded():
    assert runner.order_reconciliation_timeout_seconds({}) == 20.0
    assert (
        runner.order_reconciliation_timeout_seconds(
            {
                "LIVE_ORDER_RECONCILE_TIMEOUT_SECONDS": "5",
            }
        )
        == 5.0
    )
    with pytest.raises(LiveSafetyError, match="between 1 and 120"):
        runner.order_reconciliation_timeout_seconds(
            {
                "LIVE_ORDER_RECONCILE_TIMEOUT_SECONDS": "0",
            }
        )


def test_canary_buy_decision_is_sliced_to_dynamic_order_cap():
    limits = LiveRiskLimits.for_profile("mainnet-canary")
    decisions = runner.build_decisions(
        allocations=[("BTC/USDT", 0.04)],
        states={
            "BTC/USDT": {
                "state": "LONG",
                "price": 100.0,
                "ma_fast": 110.0,
                "ma_slow": 100.0,
                "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
            }
        },
        positions=[],
        equity=10_000.0,
        locked_reason=None,
        limits=limits,
    )
    assert decisions[0]["action"] == "BUY"
    assert decisions[0]["qty"] * decisions[0]["signal_price"] == pytest.approx(
        25.0 / 1.01
    )


def test_allocations_cannot_exceed_gross_limit():
    with pytest.raises(LiveSafetyError, match="gross limit"):
        runner.parse_allocations(
            "BTC/USDT,SOL/USDT,AVAX/USDT", "20,20,11", LiveRiskLimits()
        )


def test_live_data_drops_forming_candle(monkeypatch):
    now_ms = int(datetime.now(UTC).timestamp() * 1000)
    current_hour = now_ms // 3_600_000 * 3_600_000
    old_start = current_hour - 150 * 3_600_000
    bars = [
        [old_start + index * 3_600_000, 100, 101, 99, 100, 1] for index in range(150)
    ]
    bars.append([now_ms - 1_000, 200, 201, 199, 200, 1])

    class FakeExchange:
        def fetch_ohlcv(self, symbol, timeframe, limit):
            return bars

    monkeypatch.setattr(runner.ccxt, "binance", lambda config: FakeExchange())
    frame = runner.get_recent_df("BTC/USDT")
    assert len(frame) == 150
    assert frame["close"].tail(1).item() == 100


def test_live_data_rejects_hourly_gap():
    now = datetime(2026, 8, 10, 12, 30, tzinfo=UTC)
    hour_ms = 3_600_000
    start = int((now - timedelta(hours=4, minutes=30)).timestamp() * 1_000)
    bars = [
        [start, 100, 101, 99, 100, 1],
        [start + 2 * hour_ms, 100, 101, 99, 100, 1],
    ]
    with pytest.raises(LiveSafetyError, match="gap"):
        runner.validate_live_hourly_bars(bars, symbol="BTC/USDT", now=now)


def test_live_data_rejects_stale_and_inconsistent_ohlc():
    now = datetime(2026, 8, 10, 12, 30, tzinfo=UTC)
    stale = int((now - timedelta(hours=4, minutes=30)).timestamp() * 1_000)
    with pytest.raises(LiveSafetyError, match="stale"):
        runner.validate_live_hourly_bars(
            [[stale, 100, 101, 99, 100, 1]],
            symbol="BTC/USDT",
            now=now,
        )
    recent = int((now - timedelta(hours=1, minutes=30)).timestamp() * 1_000)
    with pytest.raises(LiveSafetyError, match="inconsistent"):
        runner.validate_live_hourly_bars(
            [[recent, 100, 99, 98, 100, 1]],
            symbol="BTC/USDT",
            now=now,
        )


def test_market_data_failure_cancels_entire_batch(monkeypatch):
    monkeypatch.setattr(
        runner,
        "get_recent_df",
        lambda symbol: (_ for _ in ()).throw(RuntimeError(symbol)),
    )
    with pytest.raises(RuntimeError):
        runner.get_recent_df("BTC/USDT")


def test_old_long_is_not_sold_only_because_entry_left_replay_window():
    decisions = runner.build_decisions(
        allocations=[("BTC/USDT", 0.2)],
        states={
            "BTC/USDT": {
                "state": "FLAT",
                "price": 100.0,
                "ma_fast": 110.0,
                "ma_slow": 100.0,
                "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
            }
        },
        positions=[{"symbol": "BTC/USDT", "qty": 2.0}],
        equity=1_000.0,
        locked_reason=None,
    )
    assert decisions == []


def test_existing_long_exits_when_fast_ma_is_below_slow_ma():
    decisions = runner.build_decisions(
        allocations=[("BTC/USDT", 0.2)],
        states={
            "BTC/USDT": {
                "state": "FLAT",
                "price": 100.0,
                "ma_fast": 90.0,
                "ma_slow": 100.0,
                "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
            }
        },
        positions=[{"symbol": "BTC/USDT", "qty": 2.0}],
        equity=1_000.0,
        locked_reason=None,
    )
    assert decisions[0]["action"] == "SELL"
    assert decisions[0]["reason"] == "STRATEGY_FLAT"


def test_entry_lock_blocks_buys_but_preserves_strategy_exits():
    candle = datetime(2026, 8, 10, 10, tzinfo=UTC)
    entry_locked = "TRADING_ENTRY_KILL_SWITCH is active"
    blocked_buy = runner.build_decisions(
        allocations=[("BTC/USDT", 0.2)],
        states={
            "BTC/USDT": {
                "state": "LONG",
                "price": 100.0,
                "ma_fast": 110.0,
                "ma_slow": 100.0,
                "candle_timestamp": candle,
            }
        },
        positions=[],
        equity=1_000.0,
        locked_reason=None,
        entries_locked_reason=entry_locked,
    )
    assert blocked_buy == []

    permitted_exit = runner.build_decisions(
        allocations=[("BTC/USDT", 0.2)],
        states={
            "BTC/USDT": {
                "state": "FLAT",
                "price": 100.0,
                "ma_fast": 90.0,
                "ma_slow": 100.0,
                "candle_timestamp": candle,
            }
        },
        positions=[{"symbol": "BTC/USDT", "qty": 2.0}],
        equity=1_000.0,
        locked_reason=None,
        entries_locked_reason=entry_locked,
    )
    assert permitted_exit[0]["action"] == "SELL"
    assert permitted_exit[0]["reason"] == "STRATEGY_FLAT"


class ExecutionBroker:
    def __init__(self, *, result=None, error: Exception | None = None, reconciled=None):
        self.result = result
        self.error = error
        self.reconciled = reconciled
        self.place_calls = 0

    def get_account(self):
        return {"equity": 1_000.0, "cash": 1_000.0}

    def get_positions(self):
        return []

    def get_ticker(self, symbol):
        return {
            "timestamp": datetime.now(UTC),
            "received_at": datetime.now(UTC),
            "bid": 99.9,
            "ask": 100.0,
            "last": 100.0,
        }

    def get_order_book(self, symbol, limit=50):
        return {
            "timestamp": datetime.now(UTC),
            "bids": [(99.9, 10.0), (99.8, 10.0)],
            "asks": [(100.0, 10.0), (100.1, 10.0)],
        }

    def normalize_order_amount(self, symbol, amount, *, reference_price):
        return round(amount, 6)

    def place_order(self, order):
        self.place_calls += 1
        if self.error is not None:
            raise self.error
        return self.result

    def get_order_by_client_id(self, client_order_id, symbol):
        return self.reconciled


def _sample_risk_decision(
    *,
    risk_level: RiskLevel = RiskLevel.LOW,
    allowed_target_exposure: float = 0.25,
    max_new_exposure: float = 0.25,
    reduce_only: bool = False,
) -> UnifiedRiskDecision:
    return UnifiedRiskDecision(
        decision_id="test-decision",
        forecast_fingerprint="test-fp",
        model_artifact_id="test-model",
        requested_target_exposure=0.5,
        allowed_target_exposure=allowed_target_exposure,
        max_new_exposure=max_new_exposure,
        reduce_only=reduce_only,
        risk_level=risk_level,
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


def planned_buy():
    return {
        "market_symbol": "BTC/USDT",
        "action": "BUY",
        "qty": 0.1,
        "signal_price": 100.0,
        "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
        "reason": "test",
        "risk_decision": _sample_risk_decision(),
    }


def canonical_stack(
    tmp_path, broker, *, position_quantity: float = 0.1, dynamic_inventory=False
):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = ExecutionEventStore(tmp_path / "events.db").connect()

    def inventory_quantity(symbol):
        if dynamic_inventory:
            return sum(
                float(p["qty"])
                for p in broker.get_positions()
                if p["symbol"] == str(symbol)
            )
        return position_quantity

    def portfolio_source(symbol):
        quantity = inventory_quantity(symbol)
        account = broker.get_account()
        return PortfolioRiskSnapshot(
            symbol=str(symbol),
            position_quantity=quantity,
            available_quantity=quantity,
            equity=float(account["equity"]),
            available_cash=float(account["cash"]),
            observed_at=datetime.now(UTC),
            source="test",
        )

    lifecycle = ExecutionLifecycle(
        store,
        price_source=lambda symbol: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        inventory_source=lambda symbol, side: inventory_quantity(symbol),
        portfolio_source=portfolio_source,
    )
    lifecycle.load()
    gateway = BrokerGateway(
        adapter=LiveBrokerExecutionAdapter(broker),
        store=store,
        lifecycle=lifecycle,
    )
    return lifecycle, gateway


def canonical_planned_buy(broker, *, decision_id="test-decision"):
    """A local fixture using the real planner, never a promoted/live artifact."""
    order = planned_buy()
    risk = replace(order["risk_decision"], decision_id=decision_id)
    order["risk_decision"] = risk
    now = datetime.now(UTC)
    equity = float(broker.get_account()["equity"])
    planning = OrderPlanner(InstrumentRules(symbol="BTC/USDT")).plan(
        target=TargetExposure(
            symbol="BTC/USDT",
            exposure=10.0 / equity,
            horizon=1,
            forecast_fingerprint=risk.forecast_fingerprint,
            model_artifact_id=risk.model_artifact_id,
            risk_decision_id=risk.decision_id,
        ),
        risk_decision=risk,
        observation=EnrichedMarketObservation(
            symbol="BTC/USDT",
            observed_at=now,
            open=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            volume=10.0,
            is_closed=True,
            bar_open_at=order["candle_timestamp"],
            bar_close_at=order["candle_timestamp"] + timedelta(hours=1),
            observation_id="local-fixture-closed-bar",
            venue="local-fixture",
            timeframe="1h",
            source="local-fixture",
            data_manifest_id="local-fixture-data",
            feature_artifact_id="local-fixture-features",
        ),
        portfolio=CurrentPortfolioState(
            symbol="BTC/USDT",
            equity=equity,
            current_exposure=0.0,
            available_cash=equity,
        ),
        price=MarketPrice(symbol="BTC/USDT", mid=100.0, bid=99.9, ask=100.0),
        tolerance=0.0,
    )
    assert planning.requires_order
    context = PermissionContext(
        execution_health=ExecutionHealth.NORMAL,
        exposure_effect=ExposureEffect.INCREASE,
        risk_decision=risk,
        trusted_price=TrustedPrice(100.0, now, now),
        order_side="buy",
        order_size=planning.intent.quantity,
    )
    return {
        **order,
        "qty": planning.intent.quantity,
        "order_planning": planning,
        "permission_context": context,
    }


@pytest.mark.parametrize(
    "fault",
    [
        "missing_risk",
        "missing_planning",
        "missing_context",
        "wrong_model",
        "wrong_quantity",
        "different_context_risk",
        "draft",
        "missing_calibration",
        "stale_risk",
    ],
)
def test_preflight_rejects_incomplete_or_mismatched_admission_before_reservation(
    tmp_path, fault
):
    broker = ExecutionBroker()
    store = LiveRiskStateStore(tmp_path / "state.json")
    order = canonical_planned_buy(broker)
    if fault.startswith("missing_") and fault != "missing_calibration":
        key = {
            "missing_risk": "risk_decision",
            "missing_planning": "order_planning",
            "missing_context": "permission_context",
        }[fault]
        order.pop(key)
    elif fault in {"wrong_model", "wrong_quantity"}:
        intent = order["order_planning"].intent
        intent = replace(
            intent,
            **(
                {"model_artifact_id": "another-model"}
                if fault == "wrong_model"
                else {"quantity": 0.2}
            ),
        )
        order["order_planning"] = replace(order["order_planning"], intent=intent)
    elif fault == "different_context_risk":
        order["permission_context"] = replace(
            order["permission_context"], risk_decision=replace(order["risk_decision"])
        )
    elif fault == "draft":
        order["permission_context"] = replace(order["permission_context"], draft=True)
    else:
        risk = replace(
            order["risk_decision"],
            **(
                {"calibration_state": EvidenceState.MISSING, "calibration_ece": 0.1}
                if fault == "missing_calibration"
                else {"created_at": datetime.now(UTC) - timedelta(hours=2)}
            ),
        )
        order["risk_decision"] = risk
        order["permission_context"] = replace(
            order["permission_context"], risk_decision=risk
        )
    with pytest.raises(LiveSafetyError):
        runner.prepare_orders(
            decisions=[order],
            broker=broker,
            account=broker.get_account(),
            positions=[],
            limits=LiveRiskLimits(),
            locked_reason=None,
            store=store,
        )
    assert broker.place_calls == 0
    assert store.state.reserved_orders == {}


def test_build_preflight_submit_preserves_canonical_and_client_identity(tmp_path):
    broker = FilledBuyBroker()
    store = LiveRiskStateStore(tmp_path / "state.json")
    order = canonical_planned_buy(broker)
    admission = runner.LiveBuyAdmission(
        order["risk_decision"], order["order_planning"], order["permission_context"]
    )
    states = {
        "BTC/USDT": {
            "state": "LONG",
            "price": 100.0,
            "ma_fast": 101.0,
            "ma_slow": 100.0,
            "atr": 5.0,
            "recent_high": 100.0,
            "candle_timestamp": order["candle_timestamp"],
        }
    }
    decisions = runner.build_decisions(
        allocations=[("BTC/USDT", 0.25)],
        states=states,
        positions=[],
        equity=1000.0,
        locked_reason=None,
        limits=LiveRiskLimits(),
        buy_admissions={"BTC/USDT": admission},
    )
    prepared = runner.prepare_orders(
        decisions=decisions,
        broker=broker,
        account=broker.get_account(),
        positions=[],
        limits=LiveRiskLimits(),
        locked_reason=None,
        store=store,
    )
    lifecycle, gateway = runner.build_canonical_execution_stack(
        broker, event_store_path=str(tmp_path / "events.db")
    )
    runner.execute_orders(
        orders=prepared,
        broker=broker,
        store=store,
        limits=LiveRiskLimits(),
        lifecycle=lifecycle,
        gateway=gateway,
    )
    intent = order["order_planning"].intent
    record = store.state.order_ledger[intent.idempotency_key]
    assert record["canonical_intent_id"] == intent.intent_id
    assert record["client_order_id"] == intent.idempotency_key
    assert record["status"] == "filled"
    assert record["filled_quantity"] == pytest.approx(0.1)
    assert lifecycle.state.order(intent.intent_id) is not None
    assert store.protective_order_state("BTC/USDT")["active"][
        "quantity"
    ] == pytest.approx(0.1)
    restarted_store = LiveRiskStateStore(tmp_path / "state.json")
    another = canonical_planned_buy(broker, decision_id="another-decision-same-bar")
    with pytest.raises(DuplicateOrderError):
        runner.execute_orders(
            orders=[another],
            broker=broker,
            store=restarted_store,
            limits=LiveRiskLimits(),
            lifecycle=lifecycle,
            gateway=gateway,
        )


@pytest.mark.parametrize("fault", ["missing_quote_receipt", "missing_free_inventory"])
def test_runtime_stack_blocks_unknown_quote_or_inventory_before_broker_io(
    tmp_path, fault
):
    broker = FilledBuyBroker()
    if fault == "missing_quote_receipt":
        original_ticker = broker.get_ticker

        def missing_receipt(symbol):
            ticker = original_ticker(symbol)
            ticker.pop("received_at")
            return ticker

        broker.get_ticker = missing_receipt
    else:
        broker.positions = [{"symbol": "BTC/USDT", "qty": 0.1, "market_value": 10.0}]
    lifecycle, gateway = runner.build_canonical_execution_stack(
        broker, event_store_path=str(tmp_path / "events.db")
    )
    store = LiveRiskStateStore(tmp_path / "state.json")
    order = {**canonical_planned_buy(broker), "atr": 5.0, "observed_high": 100.0}
    with pytest.raises(InvariantViolation, match="trusted fresh price|portfolio"):
        runner.execute_orders(
            orders=[order],
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=LiveRiskLimits(),
        )
    assert broker.positions == (
        []
        if fault == "missing_quote_receipt"
        else [{"symbol": "BTC/USDT", "qty": 0.1, "market_value": 10.0}]
    )
    assert broker.place_calls == 0
    assert next(iter(store.state.order_ledger.values()))["status"] == "rejected"
    lifecycle.store.close()


def test_canonical_recovery_requires_context_and_uses_client_key(tmp_path):
    broker = PartialBuyBroker()
    store = LiveRiskStateStore(tmp_path / "state.json")
    order = {**canonical_planned_buy(broker), "atr": 5.0, "observed_high": 100.0}
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)
    with pytest.raises(LiveSafetyError, match="is partial; batch stopped"):
        runner.execute_orders(
            orders=[order],
            broker=broker,
            store=store,
            limits=LiveRiskLimits(),
            lifecycle=lifecycle,
            gateway=gateway,
            reconciliation_timeout_seconds=0,
        )
    intent = order["order_planning"].intent
    with pytest.raises(LiveSafetyError, match="canonical recovery context is missing"):
        runner.reconcile_unfinished_orders(broker=broker, store=store)
    broker.orders[intent.idempotency_key] = {
        **order_result("filled", filled_qty=0.1),
        "client_order_id": intent.idempotency_key,
        "id": "entry-partial-1",
    }
    broker.positions = [{"symbol": "BTC/USDT", "qty": 0.1, "market_value": 10.0}]
    restarted_store = LiveRiskStateStore(tmp_path / "state.json")
    recovered_lifecycle, recovered_gateway = canonical_stack(
        tmp_path, broker, dynamic_inventory=True
    )
    runner.reconcile_unfinished_orders(
        broker=broker,
        store=restarted_store,
        lifecycle=recovered_lifecycle,
        gateway=recovered_gateway,
    )
    assert restarted_store.unfinished_orders() == {}
    recovered = recovered_lifecycle.state.order(intent.intent_id)
    assert recovered.status.value == "filled"
    assert recovered.filled_size == pytest.approx(0.1)
    # Repeated cumulative lookup is idempotent, including after replay.
    runner._record_reconciled_submission(
        runner.CanonicalExecutionService(
            lifecycle=recovered_lifecycle, gateway=recovered_gateway
        ),
        intent.intent_id,
        broker.orders[intent.idempotency_key],
    )
    assert recovered.filled_size == pytest.approx(0.1)


def order_result(status: str, *, filled_qty: float) -> dict:
    return {
        "id": "exchange-1",
        "client_order_id": "ignored",
        "status": status,
        "symbol": "BTC/USDT",
        "side": "buy",
        "qty": 0.1,
        "filled_qty": filled_qty,
        "avg_fill_price": 100.0 if filled_qty else 0.0,
        "error": None,
    }


def test_partial_fill_stops_batch_and_is_persisted(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = PartialBuyBroker()
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)
    with pytest.raises(LiveSafetyError, match="is partial; batch stopped"):
        runner.execute_orders(
            orders=[
                {**canonical_planned_buy(broker), "atr": 5.0, "observed_high": 100.0}
            ],
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=LiveRiskLimits(),
            reconciliation_timeout_seconds=0,
        )
    record = next(iter(store.state.order_ledger.values()))
    assert record["status"] == "manual_intervention"
    assert record["filled_quantity"] == pytest.approx(0.04)
    assert [event["status"] for event in record["status_history"]] == [
        "reserved",
        "submitted",
        "acknowledged",
        "partial",
        "reconciling",
        "manual_intervention",
    ]


def test_buy_without_order_planner_output_is_blocked_before_broker_io(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = ExecutionBroker(
        error=TimeoutError("client timed out"),
        reconciled=order_result("filled", filled_qty=0.1),
    )
    lifecycle, gateway = canonical_stack(tmp_path, broker, position_quantity=0.0)
    with pytest.raises(LiveSafetyError, match="OrderPlanner output"):
        runner.execute_orders(
            orders=[planned_buy()],
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=LiveRiskLimits(),
        )
    assert broker.place_calls == 0
    assert store.state.order_ledger == {}


def test_unfinished_order_blocks_new_batch_when_exchange_cannot_find_it(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    store.reserve_order(
        "old-intent",
        symbol="BTC/USDT",
        side="BUY",
        quantity=0.1,
        signal_timestamp=datetime(2026, 8, 10, tzinfo=UTC),
    )
    with pytest.raises(LiveSafetyError, match="reconciliation blocked"):
        runner.reconcile_unfinished_orders(
            broker=ExecutionBroker(reconciled=None),
            store=store,
        )
    assert store.state.order_ledger["old-intent"]["status"] == "manual_intervention"


def test_non_terminal_order_polling_is_bounded_and_reaches_fill():
    responses = [
        order_result("open", filled_qty=0.0),
        order_result("partial", filled_qty=0.04),
        order_result("filled", filled_qty=0.1),
    ]

    class PollBroker:
        def get_order_by_client_id(self, client_order_id, symbol):
            return responses.pop(0)

    class FakeClock:
        def __init__(self):
            self.now = 0.0
            self.sleeps = []

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.sleeps.append(seconds)
            self.now += seconds

    clock = FakeClock()
    result, error = runner.poll_order_by_client_id(
        broker=PollBroker(),
        order_key="order-1",
        symbol=runner.exchange_symbol("BTC/USDT"),
        timeout_seconds=5.0,
        initial_delay_seconds=0.1,
        max_delay_seconds=0.2,
        sleep_fn=clock.sleep,
        monotonic_fn=clock.monotonic,
    )
    assert result["status"] == "filled"
    assert error == ""
    assert len(clock.sleeps) == 2
    assert all(0 < delay <= 0.2 for delay in clock.sleeps)


def test_unknown_exchange_status_is_preserved_and_requires_intervention(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    store.reserve_order("order-1", symbol="BTC/USDT", quantity=0.1)
    store.update_order("order-1", status="submitted")
    runner.persist_order_result(
        store,
        "order-1",
        {
            **order_result("unknown", filled_qty=0.04),
            "exchange_status": "pending_new_variant",
            "quote_cost": 4.0,
            "fees": {"USDT": 0.004, "BNB": 0.0001},
            "trade_ids": ["trade-1"],
        },
    )
    record = store.state.order_ledger["order-1"]
    assert record["status"] == "manual_intervention"
    assert record["exchange_status"] == "pending_new_variant"
    assert record["quote_cost"] == pytest.approx(4.0)
    assert record["fees"] == {"USDT": 0.004, "BNB": 0.0001}
    assert record["trade_ids"] == ["trade-1"]
    assert [event["status"] for event in record["status_history"]][-2:] == [
        "acknowledged",
        "manual_intervention",
    ]


def test_atr_trail_forces_exit_and_persists_peak(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    states = {
        "BTC/USDT": {
            "state": "LONG",
            "price": 90.0,
            "atr": 5.0,
            "recent_high": 110.0,
        }
    }
    runner.apply_atr_protection(
        states=states,
        positions=[{"symbol": "BTC/USDT", "qty": 0.1}],
        store=store,
    )
    assert states["BTC/USDT"]["state"] == "FLAT"
    assert states["BTC/USDT"]["atr_stop"] == pytest.approx(100.0)
    assert store.state.position_risk["BTC/USDT"]["peak_price"] == pytest.approx(110.0)
    _, widened_atr_stop = store.observe_position_risk(
        "BTC/USDT",
        quantity=0.1,
        observed_high=110.0,
        atr=20.0,
        atr_multiplier=2.0,
    )
    assert widened_atr_stop == pytest.approx(100.0)


class ProtectiveBroker:
    def __init__(self, *, timeout_after_accept=False):
        self.orders = {}
        self.next_id = 1
        self.timeout_after_accept = timeout_after_accept
        self.place_calls = 0
        self.replace_calls = 0
        self.cancel_calls = 0

    def get_account(self):
        return {"equity": 100_000.0, "cash": 100_000.0}

    def normalize_order_amount(self, symbol, amount, *, reference_price):
        return round(float(amount), 6)

    def _result(self, order, *, status="open"):
        result = {
            "id": f"stop-{self.next_id}",
            "client_order_id": order.client_order_id,
            "status": status,
            "symbol": order.symbol.pair,
            "side": order.side.value,
            "type": order.type.value,
            "qty": float(order.size),
            "filled_qty": 0.0,
            "avg_fill_price": 0.0,
            "stop_price": float(order.stop_price) if order.stop_price else None,
            "error": None,
        }
        self.next_id += 1
        self.orders[order.client_order_id] = result
        return result

    def place_order(self, order, evidence=None):
        self.place_calls += 1
        result = self._result(order)
        if self.timeout_after_accept:
            self.timeout_after_accept = False
            raise TimeoutError("accepted but response lost")
        return result

    def replace_order(self, order_id, order, evidence=None):
        self.replace_calls += 1
        for existing in self.orders.values():
            if existing["id"] == order_id:
                existing["status"] = "cancelled"
        return self._result(order)

    def get_order_by_client_id(self, client_order_id, symbol):
        result = self.orders.get(client_order_id)
        return dict(result) if result else None

    def cancel_order(self, order_id, symbol):
        self.cancel_calls += 1
        for existing in self.orders.values():
            if existing["id"] == order_id:
                existing["status"] = "cancelled"
                return True
        return False


class DustRejectedBroker(ProtectiveBroker):
    def __init__(self, constraint="minimum_notional"):
        super().__init__()
        self.constraint = constraint

    def normalize_order_amount(self, symbol, amount, *, reference_price):
        raise OrderConstraintError(
            "order is outside a deterministic exchange filter",
            constraint=self.constraint,
        )


def initialized_position_store(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    store.observe_position_risk(
        "BTC/USDT",
        quantity=0.1,
        observed_high=100.0,
        atr=5.0,
        atr_multiplier=2.0,
    )
    return store


def test_sell_capacity_counts_only_free_and_our_protective_reservation():
    position = {"qty": 0.1, "free_qty": 0.02, "locked_qty": 0.08}
    active = {"status": "open", "quantity": 0.05}
    assert runner.sellable_position_quantity(position, active) == pytest.approx(0.07)
    assert runner.sellable_position_quantity(position, None) == pytest.approx(0.02)
    with pytest.raises(LiveSafetyError, match="available balance"):
        runner.validate_sell_quantity_capacity(
            pair="BTC/USDT",
            requested_quantity=0.03,
            position=position,
            active_protective=None,
        )


def test_minimum_filter_remainder_is_persisted_as_controlled_dust(tmp_path):
    store = initialized_position_store(tmp_path)
    audit_path = tmp_path / "execution.jsonl"
    broker = DustRejectedBroker()
    lifecycle, gateway = canonical_stack(tmp_path, broker)
    result = runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.04,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
        limits=LiveRiskLimits(max_dust_notional_usd=5.0),
        audit_log_path=audit_path,
    )
    assert result["status"] == "controlled_dust"
    assert store.protective_order_state("BTC/USDT")["dust"][
        "estimated_notional"
    ] == pytest.approx(4.0)
    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["event"] == "position_dust_classified"
    assert event["details"]["context"] == "protective_stop"


def test_large_or_non_minimum_remainder_still_fails_closed(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = DustRejectedBroker()
    lifecycle, gateway = canonical_stack(tmp_path, broker)
    with pytest.raises(OrderConstraintError):
        runner.ensure_protective_stop(
            pair="BTC/USDT",
            quantity=0.06,
            desired_stop=90.0,
            current_price=100.0,
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=LiveRiskLimits(max_dust_notional_usd=5.0),
        )
    broker2 = DustRejectedBroker(constraint="maximum_notional")
    lifecycle2, gateway2 = canonical_stack(
        tmp_path / "second",
        broker2,
    )
    with pytest.raises(OrderConstraintError):
        runner.ensure_protective_stop(
            pair="BTC/USDT",
            quantity=0.04,
            desired_stop=90.0,
            current_price=100.0,
            broker=broker2,
            lifecycle=lifecycle2,
            gateway=gateway2,
            store=store,
            limits=LiveRiskLimits(max_dust_notional_usd=5.0),
        )


def test_exchange_native_stop_is_idempotent_and_only_tightens(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = ProtectiveBroker()
    lifecycle, gateway = canonical_stack(tmp_path, broker)
    first = runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    assert first["status"] == "open"
    assert first["stop_price"] == pytest.approx(90.0)

    unchanged = runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=89.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    assert unchanged["client_order_id"] == first["client_order_id"]
    assert broker.place_calls == 1
    assert broker.replace_calls == 0

    tightened = runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=92.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    assert tightened["stop_price"] == pytest.approx(92.0)
    assert tightened["client_order_id"] != first["client_order_id"]
    assert broker.replace_calls == 0
    assert broker.cancel_calls == 1
    assert broker.place_calls == 2


def test_protective_stop_timeout_after_accept_is_recovered(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = ProtectiveBroker(timeout_after_accept=True)
    lifecycle, gateway = canonical_stack(tmp_path, broker)
    recovered = runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    assert recovered["status"] == "open"
    assert store.protective_order_state("BTC/USDT")["pending"] is None


def test_duplicate_active_and_pending_stops_fail_closed(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = ProtectiveBroker()
    lifecycle, gateway = canonical_stack(tmp_path, broker)
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    pending = store.reserve_protective_order(
        "BTC/USDT",
        quantity=0.1,
        stop_price=92.0,
    )
    pending_order = Order(
        id="",
        symbol=runner.exchange_symbol("BTC/USDT"),
        client_order_id=pending["client_order_id"],
        side=OrderSide.SELL,
        type=OrderType.STOP,
        size=Decimal("0.1"),
        stop_price=Decimal("92.0"),
        time_in_force=TimeInForce.GTC,
    )
    broker._result(pending_order)
    with pytest.raises(LiveSafetyError, match="duplicate active protective"):
        runner.reconcile_protective_stop(
            pair="BTC/USDT",
            broker=broker,
            store=store,
        )


def test_orphan_protective_stop_is_cancelled_before_state_is_cleared(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = ProtectiveBroker()
    lifecycle, gateway = canonical_stack(tmp_path, broker)
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    runner.cleanup_orphan_protective_stops(
        managed_symbols=["BTC/USDT"],
        positions=[],
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    assert broker.cancel_calls == 1
    assert "BTC/USDT" not in store.state.position_risk


class FilledBuyBroker(ProtectiveBroker):
    def __init__(self):
        super().__init__()
        self.positions = []

    def get_account(self):
        return {"equity": 1_000.0, "cash": 1_000.0}

    def get_positions(self):
        return [dict(position) for position in self.positions]

    def get_ticker(self, symbol):
        return {
            "timestamp": datetime.now(UTC),
            "received_at": datetime.now(UTC),
            "bid": 99.9,
            "ask": 100.0,
            "last": 100.0,
        }

    def get_order_book(self, symbol, limit=50):
        return {
            "timestamp": datetime.now(UTC),
            "bids": [(99.9, 10.0)],
            "asks": [(100.0, 10.0)],
        }

    def place_order(self, order):
        if order.type == OrderType.MARKET:
            self.positions = [
                {
                    "symbol": order.symbol.pair,
                    "qty": float(order.size),
                    "free_qty": float(order.size),
                    "market_value": float(order.size) * 100.0,
                }
            ]
            return {
                "id": "entry-1",
                "client_order_id": order.client_order_id,
                "status": "filled",
                "symbol": order.symbol.pair,
                "side": order.side.value,
                "type": order.type.value,
                "qty": float(order.size),
                "filled_qty": float(order.size),
                "avg_fill_price": 100.0,
                "stop_price": None,
                "error": None,
            }
        return super().place_order(order)


class PartialBuyBroker(FilledBuyBroker):
    def place_order(self, order):
        if order.type != OrderType.MARKET:
            return super().place_order(order)
        filled = 0.04
        self.positions = [
            {
                "symbol": order.symbol.pair,
                "qty": filled,
                "free_qty": filled,
                "market_value": filled * 100.0,
            }
        ]
        return {
            "id": "entry-partial-1",
            "client_order_id": order.client_order_id,
            "status": "partial",
            "symbol": order.symbol.pair,
            "side": order.side.value,
            "type": order.type.value,
            "qty": float(order.size),
            "filled_qty": filled,
            "avg_fill_price": 100.0,
            "stop_price": None,
            "error": None,
        }


class FilterRejectedPartialBuyBroker(PartialBuyBroker):
    def __init__(self):
        super().__init__()
        self.normalization_calls = 0

    def normalize_order_amount(self, symbol, amount, *, reference_price):
        self.normalization_calls += 1
        if self.normalization_calls >= 3:
            raise ValueError("order notional is below market minimum")
        return super().normalize_order_amount(
            symbol,
            amount,
            reference_price=reference_price,
        )


def test_filled_buy_installs_exchange_stop_before_batch_continues(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = FilledBuyBroker()
    order = {**canonical_planned_buy(broker), "atr": 5.0, "observed_high": 100.0}
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)
    runner.execute_orders(
        orders=[order],
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
        limits=LiveRiskLimits(),
    )
    protection = store.protective_order_state("BTC/USDT")
    assert protection["active"]["status"] == "open"
    assert protection["active"]["stop_price"] == pytest.approx(90.0)
    assert store.unfinished_orders() == {}


def test_partial_buy_is_protected_before_the_batch_stops(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = PartialBuyBroker()
    order = {**canonical_planned_buy(broker), "atr": 5.0, "observed_high": 100.0}
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)
    with pytest.raises(LiveSafetyError, match="is partial; batch stopped"):
        runner.execute_orders(
            orders=[order],
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=LiveRiskLimits(),
            reconciliation_timeout_seconds=0,
        )
    protection = store.protective_order_state("BTC/USDT")
    assert protection["active"]["status"] == "open"
    assert protection["active"]["quantity"] == pytest.approx(0.04)
    record = next(iter(store.unfinished_orders().values()))
    assert record["status"] == "manual_intervention"
    assert record["filled_quantity"] == pytest.approx(0.04)


def test_unprotectable_partial_fill_is_audited_and_fails_closed(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = FilterRejectedPartialBuyBroker()
    audit_path = tmp_path / "execution.jsonl"
    order = {**canonical_planned_buy(broker), "atr": 5.0, "observed_high": 100.0}
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)
    with pytest.raises(LiveSafetyError, match="cannot be protected"):
        runner.execute_orders(
            orders=[order],
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=LiveRiskLimits(),
            audit_log_path=audit_path,
        )
    events = [json.loads(line) for line in audit_path.read_text().splitlines()]
    failed = next(
        event for event in events if event["event"] == "position_protection_failed"
    )
    assert failed["details"]["order_status"] == "partial"
    assert failed["details"]["remaining_quantity"] == pytest.approx(0.04)
    assert store.protective_order_state("BTC/USDT")["active"] is None


class FilledExitBroker(ProtectiveBroker):
    def __init__(self):
        super().__init__()
        self.positions = [
            {
                "symbol": "BTC/USDT",
                "qty": 0.1,
                "free_qty": 0.0,
                "locked_qty": 0.1,
                "market_value": 10.0,
            }
        ]
        self.exit_market_calls = 0

    def get_account(self):
        return {"equity": 1_000.0, "cash": 990.0}

    def get_positions(self):
        return [dict(position) for position in self.positions]

    def get_ticker(self, symbol):
        return {
            "timestamp": datetime.now(UTC),
            "received_at": datetime.now(UTC),
            "bid": 99.9,
            "ask": 100.0,
            "last": 100.0,
        }

    def get_order_book(self, symbol, limit=50):
        return {
            "timestamp": datetime.now(UTC),
            "bids": [(99.9, 10.0)],
            "asks": [(100.0, 10.0)],
        }

    def place_order(self, order):
        if order.type != OrderType.MARKET:
            return super().place_order(order)
        assert all(
            existing["status"] == "cancelled" for existing in self.orders.values()
        )
        self.exit_market_calls += 1
        self.positions = []
        return {
            "id": "exit-1",
            "client_order_id": order.client_order_id,
            "status": "filled",
            "symbol": order.symbol.pair,
            "side": order.side.value,
            "type": order.type.value,
            "qty": float(order.size),
            "filled_qty": float(order.size),
            "avg_fill_price": 99.9,
            "stop_price": None,
            "error": None,
        }


class PartialExitBroker(FilledExitBroker):
    def place_order(self, order):
        if order.type != OrderType.MARKET:
            return super().place_order(order)
        assert all(
            existing["status"] == "cancelled" for existing in self.orders.values()
        )
        self.exit_market_calls += 1
        self.positions = [
            {
                "symbol": "BTC/USDT",
                "qty": 0.04,
                "market_value": 4.0,
            }
        ]
        result = {
            "id": "exit-partial-1",
            "client_order_id": order.client_order_id,
            "status": "partial",
            "symbol": order.symbol.pair,
            "side": order.side.value,
            "type": order.type.value,
            "qty": float(order.size),
            "filled_qty": 0.06,
            "avg_fill_price": 99.9,
            "stop_price": None,
            "error": None,
        }
        self.orders[order.client_order_id] = result
        return dict(result)


class CancelRaceExitBroker(PartialExitBroker):
    def __init__(self, *, outcome):
        super().__init__()
        self.outcome = outcome

    def cancel_order(self, order_id, symbol):
        if order_id != "exit-partial-1":
            return super().cancel_order(order_id, symbol)
        if self.outcome == "unknown":
            self.cancel_calls += 1
            return False
        confirmed = super().cancel_order(order_id, symbol)
        for order in self.orders.values():
            if order["id"] == order_id:
                order["filled_qty"] = 0.1 if self.outcome == "filled" else 0.08
                if self.outcome == "filled":
                    order["status"] = "filled"
        self.positions = (
            []
            if self.outcome == "filled"
            else [{"symbol": "BTC/USDT", "qty": 0.02, "market_value": 2.0}]
        )
        return confirmed


@pytest.mark.parametrize("outcome", ["unknown", "additional_fill", "filled"])
def test_partial_exit_cancel_race_never_overlaps_inventory(tmp_path, outcome):
    store = initialized_position_store(tmp_path)
    broker = CancelRaceExitBroker(outcome=outcome)
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    sell = dict(
        market_symbol="BTC/USDT",
        action="SELL",
        qty=0.1,
        signal_price=100.0,
        candle_timestamp=datetime(2026, 8, 10, 10, tzinfo=UTC),
        atr=5.0,
        observed_high=100.0,
        reason="cancel-race-local-fixture",
    )
    args = dict(
        orders=[sell],
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
        limits=LiveRiskLimits(),
        reconciliation_timeout_seconds=0,
    )
    if outcome == "filled":
        runner.execute_orders(**args)
        assert store.unfinished_orders() == {}
        assert not store.protective_order_state("BTC/USDT").get("active")
    else:
        with pytest.raises(
            LiveSafetyError,
            match=(
                "residual exit cancellation unresolved"
                if outcome == "unknown"
                else "is cancelled"
            ),
        ):
            runner.execute_orders(**args)
    key = runner.make_order_key(
        symbol="BTC/USDT", side="SELL", candle_timestamp=sell["candle_timestamp"]
    )
    order = lifecycle.state.order(key)
    assert broker.exit_market_calls == 1
    if outcome == "unknown":
        assert order.remaining_reserved_quantity == pytest.approx(0.04)
        assert store.state.order_ledger[key]["status"] == "manual_intervention"
        assert not store.protective_order_state("BTC/USDT").get("active")
        assert (
            broker.place_calls == 1
        )  # Only the original stop, no overlapping replacement.
    else:
        assert order.remaining_reserved_quantity == pytest.approx(0.0)
        assert order.filled_size == pytest.approx(0.1 if outcome == "filled" else 0.08)
        if outcome == "additional_fill":
            assert store.protective_order_state("BTC/USDT")["active"][
                "quantity"
            ] == pytest.approx(0.02)


def test_market_exit_confirms_cancel_before_canonical_submit(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = FilledExitBroker()
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    sell = {
        "market_symbol": "BTC/USDT",
        "action": "SELL",
        "qty": 0.1,
        "signal_price": 100.0,
        "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
        "atr": 5.0,
        "observed_high": 100.0,
        "reason": "test-exit",
    }
    runner.execute_orders(
        orders=[sell],
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
        limits=LiveRiskLimits(),
    )
    assert broker.exit_market_calls == 1
    assert broker.cancel_calls == 1
    assert "BTC/USDT" not in store.state.position_risk
    assert store.unfinished_orders() == {}


def test_partial_exit_reprotects_the_remaining_position_before_stopping(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = PartialExitBroker()
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        lifecycle=lifecycle,
        gateway=gateway,
        store=store,
    )
    sell = {
        "market_symbol": "BTC/USDT",
        "action": "SELL",
        "qty": 0.1,
        "signal_price": 100.0,
        "candle_timestamp": datetime(2026, 8, 10, 10, tzinfo=UTC),
        "atr": 5.0,
        "observed_high": 100.0,
        "reason": "test-partial-exit",
    }
    with pytest.raises(LiveSafetyError, match="is cancelled; batch stopped"):
        runner.execute_orders(
            orders=[sell],
            broker=broker,
            lifecycle=lifecycle,
            gateway=gateway,
            store=store,
            limits=LiveRiskLimits(),
        )
    protection = store.protective_order_state("BTC/USDT")
    assert protection["active"]["status"] == "open"
    assert protection["active"]["quantity"] == pytest.approx(0.04)
    assert broker.exit_market_calls == 1
    assert broker.cancel_calls == 2


def test_restart_reconciliation_does_not_double_count_inventory(tmp_path):
    """A restarted process must reconcile one order into one position.

    test_canonical_recovery_requires_context_and_uses_client_key already
    asserts the lifecycle-side idempotency — filled_size stays 0.1 when the
    same cumulative lookup is applied twice. That is the intent in
    isolation.

    What is not asserted is the other half of the same claim: money. If a
    reconciliation pass applies a fill to inventory as well as to the
    intent, the second pass doubles the position while filled_size still
    reads 0.1, and every assertion on the intent keeps passing while the
    book silently drifts. This test reconciles twice across a store reload
    and checks the inventory and realised P&L agree with the venue's
    reported position rather than with themselves.
    """
    broker = PartialBuyBroker()
    store = LiveRiskStateStore(tmp_path / "state.json")
    order = {**canonical_planned_buy(broker), "atr": 5.0, "observed_high": 100.0}
    lifecycle, gateway = canonical_stack(tmp_path, broker, dynamic_inventory=True)

    with pytest.raises(LiveSafetyError, match="is partial; batch stopped"):
        runner.execute_orders(
            orders=[order],
            broker=broker,
            store=store,
            limits=LiveRiskLimits(),
            lifecycle=lifecycle,
            gateway=gateway,
            reconciliation_timeout_seconds=0,
        )
    intent = order["order_planning"].intent

    # The venue holds a partial fill and reports its own position.
    broker.orders[intent.idempotency_key] = {
        **order_result("filled", filled_qty=0.1),
        "client_order_id": intent.idempotency_key,
        "id": "entry-partial-1",
    }
    broker.positions = [{"symbol": "BTC/USDT", "qty": 0.1, "market_value": 10.0}]

    # First pass after restart.
    restarted_store = LiveRiskStateStore(tmp_path / "state.json")
    recovered_lifecycle, recovered_gateway = canonical_stack(
        tmp_path, broker, dynamic_inventory=True
    )
    runner.reconcile_unfinished_orders(
        broker=broker,
        store=restarted_store,
        lifecycle=recovered_lifecycle,
        gateway=recovered_gateway,
    )
    after_first = recovered_lifecycle.state.order(intent.intent_id)
    assert after_first.filled_size == pytest.approx(0.1)

    # Second pass over the same venue fact, after another reload.
    second_store = LiveRiskStateStore(tmp_path / "state.json")
    second_lifecycle, second_gateway = canonical_stack(
        tmp_path, broker, dynamic_inventory=True
    )
    runner.reconcile_unfinished_orders(
        broker=broker,
        store=second_store,
        lifecycle=second_lifecycle,
        gateway=second_gateway,
    )
    after_second = second_lifecycle.state.order(intent.intent_id)
    assert after_second.filled_size == pytest.approx(0.1)

    # The venue never reported more than 0.1, so nothing may conclude it did.
    venue_qty = sum(p["qty"] for p in broker.positions if p["symbol"] == "BTC/USDT")
    assert venue_qty == pytest.approx(0.1)
    assert after_second.filled_size <= venue_qty + 1e-9
    # Reconciliation must not resize the intent, and a partially filled
    # order must never report more filled than it authorised.
    final_order = second_lifecycle.state.order(intent.intent_id)
    assert final_order.size == pytest.approx(0.1)
    assert final_order.filled_size <= final_order.size + 1e-9
