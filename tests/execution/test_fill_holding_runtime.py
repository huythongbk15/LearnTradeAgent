"""Synthetic-only prototype. No market files, registry activation or account I/O."""
import hashlib
import math
import sys
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest

from trading_agent.authority.config import Environment
from trading_agent.authority.portfolio import PortfolioSnapshot
from trading_agent.authority.promotion_store import PromotionStateStore
from trading_agent.authority.resolver import StrategyRuntime
from trading_agent.execution.canonical.market_observation import EnrichedMarketObservation
from trading_agent.execution.canonical.adapters import BrokerSubmitState
from trading_agent.execution.canonical.order_planner import InstrumentRules
from trading_agent.execution.engine import ExecutionEngine
from trading_agent.execution.lifecycle.lifecycle import EmergencyReduceRequest
from trading_agent.authority.portfolio import ReconciliationState
from trading_agent.execution.position_cycle import PositionCycleRecord, PositionCycleView
from trading_agent.execution.types import OrderStatus
from trading_agent.research.artifact import PersistentArtifactStore, StrategyArtifact, canonical_params, sha256_hex
from trading_agent.research.promotion import ResearchPromotionEvent, ResearchStage
from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy, fill_policy_fingerprint

SYMBOL = "ETH/USDT"


class Clock(datetime):
    current = datetime(2025, 1, 1, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):
        return cls.current if tz is None else cls.current.astimezone(tz)


def entry_frame(at):
    count = 218
    prices = [1000.0] * (count - 2) + [1100.0, 1100.0]
    return pl.DataFrame({
        "timestamp": [at - timedelta(hours=count - i) for i in range(count)],
        "open": prices, "high": prices, "low": prices, "close": prices,
        "volume": [1.0] * count,
    })


def observation(at, number, price=1100.0):
    return EnrichedMarketObservation(
        symbol=SYMBOL, observed_at=at, bar_open_at=at - timedelta(hours=1),
        bar_close_at=at, is_closed=True, open=price, high=price, low=price,
        close=price, volume=1, observation_id=f"synthetic-{number}",
        venue="paper", timeframe="1h", data_manifest_id="synthetic-closed-frame-v1",
    )


@pytest.fixture
def setup(tmp_path, monkeypatch):
    Clock.current = Clock(2025, 1, 1, tzinfo=UTC)
    # Freeze the same clock throughout native paper/lifecycle/permission,
    # retaining the original freshness thresholds and closed-bar checks.
    for module in list(sys.modules.values()):
        if (getattr(module, "__name__", "").startswith("trading_agent.")
                and getattr(module, "datetime", None) is datetime):
            monkeypatch.setattr(module, "datetime", Clock)
    artifacts = PersistentArtifactStore(tmp_path / "artifacts")
    promotions = PromotionStateStore(tmp_path / "promotion.db")
    strategy = Own24FillContextStrategy({"arm": "A0"})
    metadata = {
        "symbol": SYMBOL, "timeframe": "1h", "parameters": {"arm": "A0"},
        "calibration_state": "KNOWN", "ood_state": "KNOWN", "regime_state": "KNOWN",
        "forecast_fingerprint": fill_policy_fingerprint(),
        "evidence_class": "SYNTHETIC_TEST_ONLY", "policy": fill_policy_fingerprint(),
    }
    artifact = StrategyArtifact(
        strategy_name=strategy.name,
        code_sha=hashlib.sha256(Path(sys.modules[strategy.__module__].__file__).read_bytes()).hexdigest(),
        data_manifest_sha="synthetic-closed-frame-v1",
        parameter_hash=sha256_hex(canonical_params({"arm": "A0"})),
        execution_model_version="fill-cycle-synthetic-v1", framework_version="test",
        metadata=metadata,
    )
    artifacts.add(artifact)
    promotions.upsert_from_event(ResearchPromotionEvent(
        subject_artifact_id=artifact.artifact_id, from_stage=ResearchStage.RESEARCH_VALIDATED,
        to_stage=ResearchStage.PAPER_ELIGIBLE, evidence_ids=("synthetic-only",),
        actor="synthetic-fixture", timestamp=Clock.current,
    ))
    runtime = StrategyRuntime(
        strategy=strategy, artifact_id=artifact.artifact_id, strategy_name=strategy.name,
        symbol=SYMBOL, timeframe="1h", environment=Environment.PAPER,
        parameters={"arm": "A0", "target_exposure_pct": 0.25},
        promoted_at=Clock.current, promotion_stage="paper_eligible", artifact_metadata=metadata,
    )

    def make_engine():
        return ExecutionEngine(
            exchange_name="paper", initial_capital=10000, commission=0.001, slippage=0.0005,
            instrument_rules=InstrumentRules(symbol=SYMBOL, min_order_qty=0.0001,
                max_order_qty=100, qty_step=0.0001, min_notional=10),
            promotion_store=promotions, artifact_store=artifacts,
            state_dir=tmp_path / "paper", event_store_path=tmp_path / "events.db",
            event_store_synchronous="NORMAL", disable_paper_telemetry=True,
            defer_paper_state_persistence=True,
        )

    engine = make_engine()
    yield engine, runtime, make_engine
    engine.store.close()


def execute(engine, runtime, at, number, frame=None, price=1100.0):
    Clock.current = at
    engine.update_prices({SYMBOL: price})
    return engine.execute_strategy(
        runtime, entry_frame(at) if frame is None else frame, observation(at, number, price),
    )


def test_native_fill_holds_until_24h_and_exit_ignores_missing_context(setup):
    engine, runtime, _ = setup
    start = Clock.current
    orders = execute(engine, runtime, start, 0)
    assert len(orders) == 1 and orders[0].status == OrderStatus.FILLED
    position = engine.exchange.get_position(SYMBOL)
    first = position.opened_at
    first_id = position.metadata["entry_order_ids"][0]
    assert first == start and first_id == orders[0].id
    assert execute(engine, runtime, first + timedelta(hours=23), 23, pl.DataFrame()) == []
    assert engine.exchange.get_position(SYMBOL).opened_at == first
    exit_orders = execute(engine, runtime, first + timedelta(hours=24), 24, pl.DataFrame())
    assert len(exit_orders) == 1
    assert engine.exchange.get_position(SYMBOL) is None
    trade = engine.exchange.trades[-1]
    assert trade.entry_order_id == first_id
    assert trade.exit_time - trade.entry_time == timedelta(hours=24)
    assert not engine.exchange.get_open_orders()


def test_rejected_entry_does_not_start_timer_delayed_fill_does(setup, monkeypatch):
    engine, runtime, _ = setup
    start = Clock.current
    fill = engine.exchange._fill_market_order

    def reject(order_id):
        engine.exchange.orders[order_id].status = OrderStatus.REJECTED

    monkeypatch.setattr(engine.exchange, "_fill_market_order", reject)
    rejected = execute(engine, runtime, start, 0)
    assert rejected and all(order.status == OrderStatus.REJECTED for order in rejected)
    assert all(order.filled_amount == 0 for order in rejected)
    assert engine.exchange.get_position(SYMBOL) is None
    monkeypatch.setattr(engine.exchange, "_fill_market_order", fill)
    delayed = start + timedelta(hours=3)
    assert len(execute(engine, runtime, delayed, 3)) == 1
    assert engine.exchange.get_position(SYMBOL).opened_at == delayed
    assert execute(engine, runtime, start + timedelta(hours=24), 24, pl.DataFrame()) == []
    assert len(execute(engine, runtime, delayed + timedelta(hours=24), 27, pl.DataFrame())) == 1


def test_stop_then_reentry_has_a_new_fill_cycle(setup):
    engine, runtime, _ = setup
    start = Clock.current
    execute(engine, runtime, start, 0)
    first_id = engine.exchange.get_position(SYMBOL).metadata["entry_order_ids"][0]
    Clock.current = start + timedelta(hours=1)
    engine.update_prices({SYMBOL: 1000.0})
    assert engine.exchange.get_position(SYMBOL) is None
    assert engine.exchange.trades[-1].reason == "stop_loss"
    later = start + timedelta(hours=2)
    assert len(execute(engine, runtime, later, 2)) == 1
    position = engine.exchange.get_position(SYMBOL)
    assert position.metadata["entry_order_ids"][0] != first_id
    assert position.opened_at == later
    assert execute(engine, runtime, start + timedelta(hours=24), 24, pl.DataFrame()) == []
    assert len(execute(engine, runtime, later + timedelta(hours=24), 26, pl.DataFrame())) == 1


def test_restart_recovers_fill_time_from_persisted_broker_position(setup):
    engine, runtime, make_engine = setup
    start = Clock.current
    execute(engine, runtime, start, 0)
    first = engine.exchange.get_position(SYMBOL).opened_at
    engine.exchange.flush_state()
    engine.store.close()
    restarted = make_engine()
    try:
        assert restarted.exchange.get_position(SYMBOL).opened_at == first
        assert len(execute(restarted, runtime, first + timedelta(hours=24), 24, pl.DataFrame())) == 1
        assert restarted.exchange.get_position(SYMBOL) is None
    finally:
        restarted.store.close()


def test_historical_snapshot_does_not_fall_back_to_live_fill_truth(setup, monkeypatch):
    engine, runtime, _ = setup
    def forbidden(*args, **kwargs):
        raise AssertionError("historical preparation must not read live position")
    monkeypatch.setattr(engine.exchange, "get_position", forbidden)
    snapshot = PortfolioSnapshot(equity=10000, available_cash=9000,
        positions={SYMBOL: 1}, symbol_exposures={SYMBOL: 0.1}, gross_exposure=0.1)
    prepared = engine._prepare_from_runtime(runtime, entry_frame(Clock.current),
        observation(Clock.current, 0), portfolio_snapshot=snapshot)
    assert prepared.prepare_status == "position_cycle_snapshot_unavailable"


def test_partial_entry_starts_at_first_fill_not_entry_cancel_time(setup, monkeypatch):
    engine, runtime, _ = setup
    start = Clock.current
    fill = engine.exchange._fill_market_order

    def partial(order_id):
        order = engine.exchange.orders[order_id]
        reference = engine.exchange._fresh_price(SYMBOL)
        engine.exchange._execute_fill(order, reference * (1 + engine.exchange.slippage),
            fill_amount=math.floor(order.amount / 2 / 0.0001) * 0.0001,
            reference_price=reference)

    monkeypatch.setattr(engine.exchange, "_fill_market_order", partial)
    entry = execute(engine, runtime, start, 0)[0]
    assert entry.status == OrderStatus.PARTIALLY_FILLED and entry.filled_amount > 0
    quantity = engine.exchange.get_position(SYMBOL).quantity
    assert engine.exchange.get_position(SYMBOL).opened_at == start
    monkeypatch.setattr(engine.exchange, "_fill_market_order", fill)
    # Resolve the unfilled remainder through the typed cancel/lifecycle path.
    Clock.current = start + timedelta(hours=2)
    engine.update_prices({SYMBOL: 1100.0})
    intent_id = next(k for k, v in engine.lifecycle.state.orders.items()
        if v.broker_order_id == entry.id)
    engine.lifecycle.request_cancel(intent_id, reason="synthetic_partial_entry_complete")
    evidence = engine.gateway.cancel(entry.id, correlation_id="partial-entry-cancel", symbol=SYMBOL).evidence
    engine.lifecycle.confirm_cancel(intent_id, evidence)
    assert execute(engine, runtime, start + timedelta(hours=23), 23, pl.DataFrame()) == []
    assert len(execute(engine, runtime, start + timedelta(hours=24), 24, pl.DataFrame())) == 1
    assert engine.exchange.get_position(SYMBOL) is None
    assert engine.exchange.trades[-1].quantity == pytest.approx(quantity)


def test_partial_exit_keeps_original_clock_and_residual_is_not_flat(setup):
    engine, runtime, _ = setup
    start = Clock.current
    execute(engine, runtime, start, 0)
    original = engine.exchange.get_position(SYMBOL).quantity
    Clock.current = start + timedelta(hours=6)
    engine.update_prices({SYMBOL: 1100.0})
    assert engine._cancel_resting_protection(SYMBOL)[0]
    # Local broker fixture creates a genuine partial exit; remaining inventory
    # is re-protected through the canonical service rather than discarded.
    engine.exchange.place_order(SYMBOL, "sell", amount=math.floor(original / 2 / 0.0001) * 0.0001)
    position = engine.exchange.get_position(SYMBOL)
    assert position and position.quantity > 0 and position.opened_at == start
    result = engine.execution_service.emergency_protection(EmergencyReduceRequest(
        intent_id="prot_partial_remainder_submit", symbol=SYMBOL, side="sell",
        quantity=position.quantity, reason="PROTECTIVE_STOP",
        metadata={"order_type": "stop", "stop_price": 1045.0},
    ))
    assert result.success
    assert execute(engine, runtime, start + timedelta(hours=23), 23, pl.DataFrame()) == []
    assert len(execute(engine, runtime, start + timedelta(hours=24), 24, pl.DataFrame())) == 1
    assert engine.exchange.get_position(SYMBOL) is None
    assert sum(t.quantity for t in engine.exchange.trades) == pytest.approx(original)
    assert sum(t.pnl for t in engine.exchange.trades) == pytest.approx(engine.exchange.get_balance() - 10000)


def test_missing_fill_identity_blocks_new_action_without_inventing_flat(setup):
    engine, runtime, _ = setup
    start = Clock.current
    execute(engine, runtime, start, 0)
    position = engine.exchange.get_position(SYMBOL)
    quantity = position.quantity
    position.metadata["entry_order_ids"] = "malformed-id-list"
    assert execute(engine, runtime, start + timedelta(hours=24), 24, pl.DataFrame()) == []
    assert engine.last_strategy_execution["prepare_status"] == "position_cycle_unknown"
    assert engine.exchange.get_position(SYMBOL).quantity == quantity


def test_reconciled_flat_allows_new_entry_after_residual_close(setup):
    """Residual reconciliation → flat confirmed → new BUY entry is allowed.

    Fail-closed residual accounting keeps the position open until the
    residual is genuinely closed. Once reconciled to flat with inventory
    known, the entry path re-opens and BUY is permitted on pair return > 0.
    This proves residual does not block BUY forever.
    """
    engine, runtime, _ = setup
    start = Clock.current
    execute(engine, runtime, start, 0)
    original = engine.exchange.get_position(SYMBOL).quantity

    # Partial exit leaves genuine residual (not flat).
    Clock.current = start + timedelta(hours=6)
    engine.update_prices({SYMBOL: 1100.0})
    assert engine._cancel_resting_protection(SYMBOL)[0]
    engine.exchange.place_order(SYMBOL, "sell", amount=math.floor(original / 2 / 0.0001) * 0.0001)
    position = engine.exchange.get_position(SYMBOL)
    assert position and position.quantity > 0 and position.opened_at == start
    residual_qty = position.quantity

    # Reconcile the residual by selling it outright → flat confirmed.
    engine.exchange.place_order(SYMBOL, "sell", amount=residual_qty)
    assert engine.exchange.get_position(SYMBOL) is None

    # Reconciled flat cycle: inventory known, quantity 0, no active fill cycle.
    record = PositionCycleRecord(quantity=0.0, inventory_known=True)
    snapshot = PortfolioSnapshot(
        equity=10000, available_cash=10000, positions={SYMBOL: 0.0},
        symbol_exposures={SYMBOL: 0.0}, gross_exposure=0.0,
        reconciliation_state=ReconciliationState.RECONCILED,
        position_cycles={SYMBOL: record},
    )
    Clock.current = start + timedelta(hours=30)
    engine.update_prices({SYMBOL: 1100.0})
    prepared = engine._prepare_from_runtime(
        runtime, entry_frame(Clock.current), observation(Clock.current, 30),
        portfolio_snapshot=snapshot,
    )
    assert prepared.prepare_status == "ok"
    assert prepared.signal == "BUY"  # pair return 1000 -> 1100 > 0, entry path open


def test_historical_cycle_snapshot_expires_without_reading_the_broker(setup, monkeypatch):
    engine, runtime, _ = setup

    def forbidden(*args, **kwargs):
        raise AssertionError("historical preparation must not read live position")

    monkeypatch.setattr(engine.exchange, "get_position", forbidden)
    start = Clock(2025, 1, 1, tzinfo=UTC)
    record = PositionCycleRecord(
        quantity=1.0, inventory_known=True, first_fill_at=start, entry_order_id="fill-1",
    )
    snapshot = PortfolioSnapshot(
        equity=10000, available_cash=8000, positions={SYMBOL: 1.0},
        symbol_exposures={SYMBOL: 0.11}, gross_exposure=0.11,
        reconciliation_state=ReconciliationState.RECONCILED,
        position_cycles={SYMBOL: record},
    )
    at = Clock(2025, 1, 2, tzinfo=UTC)
    Clock.current = at
    engine.update_prices({SYMBOL: 1100.0})
    prepared = engine._prepare_from_runtime(
        runtime, pl.DataFrame(), observation(at, 24), portfolio_snapshot=snapshot,
    )
    assert prepared.prepare_status == "ok"
    assert prepared.signal == "SELL"


def test_cycle_quantity_disagreement_is_rejected(setup, monkeypatch):
    engine, runtime, _ = setup
    monkeypatch.setattr(
        engine.exchange, "get_position",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("live read")),
    )
    start = Clock(2025, 1, 1, tzinfo=UTC)
    record = PositionCycleRecord(
        quantity=1.0, inventory_known=True, first_fill_at=start, entry_order_id="fill-1",
    )
    snapshot = PortfolioSnapshot(
        equity=10000, available_cash=8000, positions={SYMBOL: 2.0},
        symbol_exposures={SYMBOL: 0.2}, gross_exposure=0.2,
        position_cycles={SYMBOL: record},
    )
    prepared = engine._prepare_from_runtime(
        runtime, pl.DataFrame(), observation(start, 1), portfolio_snapshot=snapshot,
    )
    assert prepared.prepare_status == "position_cycle_unknown"


def test_filled_without_a_reported_quantity_keeps_the_legacy_fallback():
    filled = SimpleNamespace(
        state=BrokerSubmitState.FILLED, broker_order_id="broker-2", error=None, raw_response={},
    )
    partial = SimpleNamespace(
        state=BrokerSubmitState.PARTIALLY_FILLED, broker_order_id="broker-3",
        error=None, raw_response={},
    )
    assert ExecutionEngine._result_to_order(filled, SYMBOL, "buy", 1.5).filled_amount == 1.5
    partial_order = ExecutionEngine._result_to_order(partial, SYMBOL, "buy", 1.5)
    assert partial_order.status == OrderStatus.PARTIALLY_FILLED
    assert partial_order.filled_amount == 0


def test_policy_is_not_registered_or_precomputable():
    from trading_agent.strategies.canonical.candidates import build_default_registry
    with pytest.raises((KeyError, ValueError)):
        build_default_registry().describe("own24_fill_context")
    with pytest.raises(ValueError, match="precomputed"):
        Own24FillContextStrategy().generate_signals(pl.DataFrame())


def test_view_is_immutable_and_unknown_inventory_never_enters():
    at = datetime(2025, 1, 1, tzinfo=UTC)
    view = PositionCycleView(SYMBOL, 0, at, False)
    with pytest.raises(FrozenInstanceError):
        view.quantity = 1
    assert Own24FillContextStrategy().generate_signal_with_position(entry_frame(at), view)[-1] == 0


@pytest.mark.parametrize("hours", [0, 23, 23.999, 24, 25])
def test_exact_expiry_boundary_uses_fill_not_entry_opportunity(hours):
    start = datetime(2025, 1, 1, tzinfo=UTC)
    view = PositionCycleView(SYMBOL, 1, start + timedelta(hours=hours), True, start, "fill-1")
    assert Own24FillContextStrategy().generate_signal_with_position(pl.DataFrame(), view)[-1] == (-1 if hours >= 24 else 0)


@pytest.mark.parametrize("change", [
    {"quantity": float("nan")}, {"quantity": -1}, {"quantity": True},
    {"inventory_known": None}, {"entry_order_id": ""},
    {"first_fill_at": datetime(2025, 1, 2, tzinfo=UTC)},
])
def test_invalid_cycle_is_rejected(change):
    values = dict(symbol=SYMBOL, quantity=1, decision_at=datetime(2025, 1, 1, tzinfo=UTC),
        inventory_known=True, first_fill_at=datetime(2024, 12, 31, tzinfo=UTC), entry_order_id="fill")
    with pytest.raises(ValueError):
        PositionCycleView(**(values | change))


@pytest.mark.parametrize("state,status", [
    (BrokerSubmitState.ACCEPTED, OrderStatus.OPEN),
    (BrokerSubmitState.ACKNOWLEDGED, OrderStatus.OPEN),
    (BrokerSubmitState.OPEN, OrderStatus.OPEN),
    (BrokerSubmitState.UNKNOWN, OrderStatus.OPEN),
    (BrokerSubmitState.PARTIALLY_FILLED, OrderStatus.PARTIALLY_FILLED),
    (BrokerSubmitState.FILLED, OrderStatus.FILLED),
    (BrokerSubmitState.REJECTED, OrderStatus.REJECTED),
])
def test_order_wrapper_preserves_broker_fill_state_and_quantity(state, status):
    actual = 0.4 if state == BrokerSubmitState.PARTIALLY_FILLED else 0
    result = SimpleNamespace(state=state, broker_order_id="broker-1", error=None,
        raw_response={"filled_qty": actual, "avg_fill_price": 1100})
    order = ExecutionEngine._result_to_order(result, SYMBOL, "buy", 1)
    assert order.status == status
    assert order.filled_amount == actual
    assert order.avg_fill_price == 1100
