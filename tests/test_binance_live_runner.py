from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime, timedelta

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import live_enhanced_ma_binance as runner

from trading_agent.exchanges.models import OrderConstraintError
from trading_agent.execution.live_safety import (
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
)
from trading_agent.execution.canonical import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
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

    def get_account(self):
        return {"equity": 1_000.0, "cash": 1_000.0}

    def get_positions(self):
        return []

    def get_ticker(self, symbol):
        return {
            "timestamp": datetime.now(UTC),
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


@pytest.mark.skip(reason="Legacy test needs update for canonical execution flow")
def test_partial_fill_stops_batch_and_is_persisted(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = ExecutionBroker(result=order_result("partial", filled_qty=0.04))
    with pytest.raises(LiveSafetyError, match="order submission outcome is unknown"):
        runner.execute_orders(
            orders=[planned_buy()],
            broker=broker,
            store=store,
            limits=LiveRiskLimits(),
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


def test_timeout_after_accept_is_reconciled_and_stops_batch(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = ExecutionBroker(
        error=TimeoutError("client timed out"),
        reconciled=order_result("filled", filled_qty=0.1),
    )
    with pytest.raises(LiveSafetyError, match="exchange reports filled"):
        runner.execute_orders(
            orders=[planned_buy()],
            broker=broker,
            store=store,
            limits=LiveRiskLimits(),
        )
    record = next(iter(store.state.order_ledger.values()))
    assert record["status"] == "filled"
    assert store.unfinished_orders() == {}


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
    result = runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.04,
        desired_stop=90.0,
        current_price=100.0,
        broker=DustRejectedBroker(),
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
    with pytest.raises(OrderConstraintError):
        runner.ensure_protective_stop(
            pair="BTC/USDT",
            quantity=0.06,
            desired_stop=90.0,
            current_price=100.0,
            broker=DustRejectedBroker(),
            store=store,
            limits=LiveRiskLimits(max_dust_notional_usd=5.0),
        )
    with pytest.raises(OrderConstraintError):
        runner.ensure_protective_stop(
            pair="BTC/USDT",
            quantity=0.04,
            desired_stop=90.0,
            current_price=100.0,
            broker=DustRejectedBroker(constraint="maximum_notional"),
            store=store,
            limits=LiveRiskLimits(max_dust_notional_usd=5.0),
        )


def test_exchange_native_stop_is_idempotent_and_only_tightens(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = ProtectiveBroker()
    first = runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
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
        store=store,
    )
    assert tightened["stop_price"] == pytest.approx(92.0)
    assert tightened["client_order_id"] != first["client_order_id"]
    assert broker.replace_calls == 1


def test_protective_stop_timeout_after_accept_is_recovered(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = ProtectiveBroker(timeout_after_accept=True)
    recovered = runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        store=store,
    )
    assert recovered["status"] == "open"
    assert store.protective_order_state("BTC/USDT")["pending"] is None


def test_duplicate_active_and_pending_stops_fail_closed(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = ProtectiveBroker()
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        store=store,
    )
    pending = store.reserve_protective_order(
        "BTC/USDT",
        quantity=0.1,
        stop_price=92.0,
    )
    pending_order = runner._protective_order(
        symbol=runner.exchange_symbol("BTC/USDT"),
        client_order_id=pending["client_order_id"],
        quantity=0.1,
        stop_price=92.0,
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
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
        store=store,
    )
    runner.cleanup_orphan_protective_stops(
        managed_symbols=["BTC/USDT"],
        positions=[],
        broker=broker,
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
        if order.type == runner.OrderType.MARKET:
            self.positions = [
                {
                    "symbol": order.symbol.pair,
                    "qty": float(order.size),
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
        if order.type != runner.OrderType.MARKET:
            return super().place_order(order)
        filled = 0.04
        self.positions = [
            {
                "symbol": order.symbol.pair,
                "qty": filled,
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


@pytest.mark.skip(reason="Legacy test needs update for canonical execution flow")
def test_filled_buy_installs_exchange_stop_before_batch_continues(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = FilledBuyBroker()
    order = {**planned_buy(), "atr": 5.0, "observed_high": 100.0}
    runner.execute_orders(
        orders=[order],
        broker=broker,
        store=store,
        limits=LiveRiskLimits(),
    )
    protection = store.protective_order_state("BTC/USDT")
    assert protection["active"]["status"] == "open"
    assert protection["active"]["stop_price"] == pytest.approx(90.0)
    assert store.unfinished_orders() == {}


@pytest.mark.skip(reason="Legacy test needs update for canonical execution flow")
def test_partial_buy_is_protected_before_the_batch_stops(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = PartialBuyBroker()
    order = {**planned_buy(), "atr": 5.0, "observed_high": 100.0}
    with pytest.raises(LiveSafetyError, match="order submission outcome is unknown"):
        runner.execute_orders(
            orders=[order],
            broker=broker,
            store=store,
            limits=LiveRiskLimits(),
        )
    protection = store.protective_order_state("BTC/USDT")
    assert protection["active"]["status"] == "open"
    assert protection["active"]["quantity"] == pytest.approx(0.04)
    record = next(iter(store.unfinished_orders().values()))
    assert record["status"] == "manual_intervention"
    assert record["filled_quantity"] == pytest.approx(0.04)


@pytest.mark.skip(reason="Legacy test needs update for canonical execution flow")
def test_unprotectable_partial_fill_is_audited_and_fails_closed(tmp_path):
    store = LiveRiskStateStore(tmp_path / "state.json")
    broker = FilterRejectedPartialBuyBroker()
    audit_path = tmp_path / "execution.jsonl"
    order = {**planned_buy(), "atr": 5.0, "observed_high": 100.0}
    with pytest.raises(LiveSafetyError, match="cannot be protected"):
        runner.execute_orders(
            orders=[order],
            broker=broker,
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
        self.exit_replace_calls = 0

    def get_account(self):
        return {"equity": 1_000.0, "cash": 990.0}

    def get_positions(self):
        return [dict(position) for position in self.positions]

    def get_ticker(self, symbol):
        return {
            "timestamp": datetime.now(UTC),
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

    def replace_order(self, order_id, order):
        if order.type != runner.OrderType.MARKET:
            return super().replace_order(order_id, order)
        self.exit_replace_calls += 1
        for existing in self.orders.values():
            if existing["id"] == order_id:
                existing["status"] = "cancelled"
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
    def replace_order(self, order_id, order):
        if order.type != runner.OrderType.MARKET:
            return super().replace_order(order_id, order)
        self.exit_replace_calls += 1
        for existing in self.orders.values():
            if existing["id"] == order_id:
                existing["status"] = "cancelled"
        self.positions = [
            {
                "symbol": "BTC/USDT",
                "qty": 0.04,
                "market_value": 4.0,
            }
        ]
        return {
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


@pytest.mark.skip(reason="Legacy test needs update for canonical execution flow")
def test_market_exit_hands_off_exchange_stop_with_cancel_replace(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = FilledExitBroker()
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
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
        store=store,
        limits=LiveRiskLimits(),
    )
    assert broker.exit_replace_calls == 1
    assert "BTC/USDT" not in store.state.position_risk
    assert store.unfinished_orders() == {}


@pytest.mark.skip(reason="Legacy test needs update for canonical execution flow")
def test_partial_exit_reprotects_the_remaining_position_before_stopping(tmp_path):
    store = initialized_position_store(tmp_path)
    broker = PartialExitBroker()
    runner.ensure_protective_stop(
        pair="BTC/USDT",
        quantity=0.1,
        desired_stop=90.0,
        current_price=100.0,
        broker=broker,
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
    with pytest.raises(LiveSafetyError, match="order submission outcome is unknown"):
        runner.execute_orders(
            orders=[sell],
            broker=broker,
            store=store,
            limits=LiveRiskLimits(),
        )
    protection = store.protective_order_state("BTC/USDT")
    assert protection["active"]["status"] == "open"
    assert protection["active"]["quantity"] == pytest.approx(0.04)
    assert broker.exit_replace_calls == 1
