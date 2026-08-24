"""Regression tests for cancel/fill races and protective STOP fidelity."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from trading_agent.execution.canonical.adapters import (
    BrokerCancelRequest,
    BrokerOrderRequest,
    CancelState as AdapterCancelState,
    PaperExecutionAdapter,
)
from trading_agent.execution.canonical.broker_gateway import (
    CancelEvidence,
    CancelState as LifecycleCancelState,
)
from trading_agent.execution.lifecycle import (
    ExecutionEventStore,
    ExecutionEventType,
    ExecutionLifecycle,
    IntentStatus,
    LifecycleError,
    PortfolioRiskSnapshot,
    TrustedPrice,
)
from trading_agent.execution.paper_exchange import PaperExchange
from trading_agent.execution.types import OrderSide
from trading_agent.exchanges.models import (
    AssetClass,
    MarketType,
    OrderType,
    Symbol,
)


def _btc_symbol() -> Symbol:
    return Symbol("BTC", "USDT", AssetClass.CRYPTO, MarketType.SPOT, "paper")


def _paper_adapter(tmp_path) -> PaperExecutionAdapter:
    state_dir = tmp_path / "paper-state"
    state_dir.mkdir()
    exchange = PaperExchange(
        initial_balance=100_000.0,
        commission=0.0,
        slippage=0.0,
        state_dir=state_dir,
        telemetry=None,
    )
    exchange.update_prices({"BTC/USDT": 50_000.0})
    return PaperExecutionAdapter(exchange)


def _submitted_sell_lifecycle(tmp_path) -> ExecutionLifecycle:
    inventory = {"BTC/USDT": 1.0}
    store = ExecutionEventStore(tmp_path / "lifecycle.db").connect()

    def price_source(_symbol: str) -> TrustedPrice:
        now = datetime.now(UTC)
        return TrustedPrice(
            price=100.0,
            exchange_timestamp=now,
            received_at=now,
            sequence_id=1,
        )

    def portfolio_source(symbol: str) -> PortfolioRiskSnapshot:
        return PortfolioRiskSnapshot(
            symbol=symbol,
            position_quantity=inventory[symbol],
            available_quantity=inventory[symbol],
            equity=100_000.0,
            available_cash=100_000.0,
            observed_at=datetime.now(UTC),
            source="test",
        )

    lifecycle = ExecutionLifecycle(
        store,
        price_source=price_source,
        portfolio_source=portfolio_source,
        inventory_source=lambda symbol, _side: inventory[symbol],
    )
    lifecycle.create_order_intent("sell-1", "BTC/USDT", "sell", 1.0)
    lifecycle.approve_risk("sell-1")
    lifecycle.submit_order("sell-1", exchange_order_id="paper-sell-1")
    return lifecycle


def test_cancel_after_fill_reports_filled_not_canceled(tmp_path):
    adapter = _paper_adapter(tmp_path)
    submitted = adapter.submit_order(
        BrokerOrderRequest(
            intent_id="market-buy-1",
            symbol=_btc_symbol(),
            side=OrderSide.BUY,
            quantity=Decimal("0.01"),
            order_type=OrderType.MARKET,
            idempotency_key="market-buy-1",
        )
    )

    assert submitted.broker_order_id is not None
    canceled = adapter.request_cancel(
        BrokerCancelRequest(broker_order_id=submitted.broker_order_id)
    )

    assert canceled.state is AdapterCancelState.FILLED
    assert canceled.state is not AdapterCancelState.CANCELED
    assert canceled.raw_response["status"] == "filled"


def test_stop_submit_fetch_round_trip_preserves_type_and_trigger(tmp_path):
    adapter = _paper_adapter(tmp_path)
    stop_price = Decimal("49000.25")
    submitted = adapter.submit_order(
        BrokerOrderRequest(
            intent_id="protective-stop-1",
            symbol=_btc_symbol(),
            side=OrderSide.SELL,
            quantity=Decimal("0.01"),
            order_type=OrderType.STOP,
            stop_price=stop_price,
            idempotency_key="protective-stop-1",
        )
    )

    assert submitted.broker_order_id is not None
    fetched = adapter.fetch_order(submitted.broker_order_id)

    assert fetched.order_type is OrderType.STOP
    assert fetched.stop_price == stop_price
    assert fetched.raw_response["type"] == "stop_loss"
    assert Decimal(str(fetched.raw_response["stop_price"])) == stop_price


def test_lifecycle_accepts_fill_while_cancel_is_requested(tmp_path):
    lifecycle = _submitted_sell_lifecycle(tmp_path)
    lifecycle.request_cancel("sell-1", reason="replace protection")

    event = lifecycle.receive_fill("sell-1", size=1.0, price=100.0)

    assert event.event_type is ExecutionEventType.FILL_RECEIVED
    assert lifecycle.order("sell-1").status is IntentStatus.FILLED
    assert lifecycle.active_sell_reservations("BTC/USDT") == pytest.approx(0.0)


def test_lifecycle_rejects_filled_cancel_evidence_until_fill_is_recorded(tmp_path):
    lifecycle = _submitted_sell_lifecycle(tmp_path)
    lifecycle.request_cancel("sell-1", reason="replace protection")
    evidence = CancelEvidence(
        broker_order_id="paper-sell-1",
        state=LifecycleCancelState.FILLED,
        venue="paper",
        confirmed_at=datetime.now(UTC).isoformat(),
        source="BROKER",
    )

    with pytest.raises(LifecycleError, match="receive_fill"):
        lifecycle.confirm_cancel("sell-1", evidence)

    assert lifecycle.order("sell-1").status is IntentStatus.CANCEL_REQUESTED
    assert lifecycle.active_sell_reservations("BTC/USDT") == pytest.approx(1.0)
