"""Fast fail-closed smoke gate for the canonical execution path."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from trading_agent.execution.canonical import (
    AuthorizationError,
    BrokerGateway,
    EvidenceState,
    PaperExecutionAdapter,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.canonical.adapters import (
    BrokerSubmitFact,
    BrokerSubmitState,
)
from trading_agent.execution.lifecycle import ExecutionEventStore
from trading_agent.execution.lifecycle.lifecycle import (
    ExecutionLifecycle,
    InvariantViolation,
    PortfolioRiskSnapshot,
    TrustedPrice,
)
from trading_agent.execution.paper_exchange import PaperExchange


SYMBOL = "BTC/USDT"
PRICE = 50_000.0


def _price_source(symbol: str) -> TrustedPrice | None:
    if symbol != SYMBOL:
        return None
    now = datetime.now(UTC)
    return TrustedPrice(price=PRICE, exchange_timestamp=now, received_at=now)


def _portfolio_source(symbol: str) -> PortfolioRiskSnapshot | None:
    if symbol != SYMBOL:
        return None
    return PortfolioRiskSnapshot(
        symbol=symbol,
        position_quantity=0.0,
        available_quantity=0.0,
        equity=10_000.0,
        available_cash=10_000.0,
        observed_at=datetime.now(UTC),
        source="smoke",
    )


def _risk_decision(intent_id: str) -> UnifiedRiskDecision:
    return UnifiedRiskDecision(
        decision_id=f"risk-{intent_id}",
        forecast_fingerprint=f"forecast-{intent_id}",
        model_artifact_id="smoke-model-v1",
        requested_target_exposure=0.05,
        allowed_target_exposure=0.05,
        max_new_exposure=0.05,
        reduce_only=False,
        risk_level=RiskLevel.LOW,
        reason_codes=("SMOKE_APPROVED",),
        calibration_state=EvidenceState.KNOWN,
        calibration_artifact_id="smoke-calibration",
        calibration_ece=0.0,
        ood_state=EvidenceState.KNOWN,
        ood_score=0.0,
        regime_state=EvidenceState.KNOWN,
        regime_entropy=0.0,
        interval_width=0.0,
        created_at=datetime.now(UTC),
    )


def _authorized_order(lifecycle: ExecutionLifecycle, intent_id: str) -> str:
    lifecycle.create_order_intent(
        intent_id,
        SYMBOL,
        "buy",
        0.01,
        idempotency_key=intent_id,
    )
    lifecycle.approve_risk(intent_id, risk_decision=_risk_decision(intent_id))
    event = lifecycle.authorize_order(intent_id, idempotency_key=intent_id)
    lifecycle.request_broker_submission(intent_id)
    return str(event.payload["authorization_id"])


class _UnknownAdapter:
    capabilities: dict[str, bool] = {}

    def submit_order(self, request) -> BrokerSubmitFact:
        return BrokerSubmitFact(
            state=BrokerSubmitState.UNKNOWN,
            broker_order_id=None,
            client_order_id=request.idempotency_key,
            venue="smoke",
            broker_status="unknown",
            observed_at=datetime.now(UTC),
            error="simulated ambiguous transport result",
            raw_response={},
        )


def _check_missing_portfolio(root: Path) -> None:
    store = ExecutionEventStore(root / "missing-portfolio.db").connect()
    lifecycle = ExecutionLifecycle(store, price_source=_price_source)
    lifecycle.create_order_intent("missing-portfolio", SYMBOL, "buy", 0.01)
    lifecycle.approve_risk(
        "missing-portfolio",
        risk_decision=_risk_decision("missing-portfolio"),
    )
    try:
        lifecycle.authorize_order(
            "missing-portfolio",
            idempotency_key="missing-portfolio",
        )
    except InvariantViolation:
        return
    raise AssertionError("authorization did not fail closed without portfolio evidence")


def _check_paper_flow(root: Path) -> None:
    store = ExecutionEventStore(root / "paper-events.db").connect()
    lifecycle = ExecutionLifecycle(
        store,
        price_source=_price_source,
        portfolio_source=_portfolio_source,
    )
    exchange = PaperExchange(
        initial_balance=10_000.0,
        commission=0.0,
        slippage=0.0,
        state_dir=root / "paper-state",
    )
    exchange.update_prices({SYMBOL: PRICE})
    gateway = BrokerGateway(PaperExecutionAdapter(exchange), store)

    try:
        gateway.submit(
            "not-a-durable-authorization",
            correlation_id="unknown-authorization-smoke",
        )
    except AuthorizationError:
        pass
    else:
        raise AssertionError("gateway accepted an unknown authorization ID")

    intent_id = "paper-fill"
    authorization_id = _authorized_order(lifecycle, intent_id)
    result = gateway.submit(authorization_id, correlation_id=intent_id)
    if result.state != BrokerSubmitState.FILLED or not result.broker_order_id:
        raise AssertionError(f"paper order was not fill-confirmed: {result}")
    lifecycle.submit_order(intent_id, exchange_order_id=result.broker_order_id)
    lifecycle.record_broker_submit_result(intent_id, result)
    if lifecycle.state.order(intent_id).status.value != "filled":
        raise AssertionError("fill evidence did not reach lifecycle state")


def _check_unknown_requires_reconciliation(root: Path) -> None:
    store = ExecutionEventStore(root / "unknown-events.db").connect()
    lifecycle = ExecutionLifecycle(
        store,
        price_source=_price_source,
        portfolio_source=_portfolio_source,
    )
    intent_id = "ambiguous-submit"
    authorization_id = _authorized_order(lifecycle, intent_id)
    result = BrokerGateway(_UnknownAdapter(), store).submit(
        authorization_id,
        correlation_id=intent_id,
    )
    if result.state != BrokerSubmitState.UNKNOWN:
        raise AssertionError("ambiguous submit was silently promoted to success")
    lifecycle.record_broker_submit_result(intent_id, result)
    order = lifecycle.state.order(intent_id)
    if order.status.value != "manual" or lifecycle.state.reconciliation.value != "started":
        raise AssertionError("UNKNOWN submit did not require manual reconciliation")


def main() -> int:
    original_cwd = Path.cwd()
    with tempfile.TemporaryDirectory(prefix="trading-p0-") as tmp:
        root = Path(tmp)
        try:
            os.chdir(root)
            _check_missing_portfolio(root)
            _check_paper_flow(root)
            _check_unknown_requires_reconciliation(root)
        finally:
            os.chdir(original_cwd)
    print("P0 smoke passed: durable auth, trusted risk data, fill evidence, UNKNOWN fail-closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
