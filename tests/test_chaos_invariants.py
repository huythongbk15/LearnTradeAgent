"""Wave C — trading invariant chaos tests.

Each scenario injects one of the sixteen fault types from spec §13 into a
healthy execution lifecycle and proves every financial safety invariant
still holds.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from trading_agent.execution.canonical import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.chaos_invariants import (
    FaultType,
    check_invariants,
    run_chaos_scenario,
)
from trading_agent.execution.lifecycle import (
    ExecutionEventStore,
    ExecutionLifecycle,
    IntentStatus,
    LifecycleError,
    PortfolioRiskSnapshot,
    TrustedPrice,
)


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


@pytest.fixture
def healthy_lifecycle(tmp_path):
    """A lifecycle with fresh prices and no kill switch."""
    store = ExecutionEventStore(tmp_path / "chaos.db").connect()
    return ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )


@pytest.mark.parametrize("fault", list(FaultType))
def test_all_faults_preserve_invariants(tmp_path, fault):
    store = ExecutionEventStore(tmp_path / f"chaos_{fault.value}.db").connect()
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        inventory_source=lambda sym, side: 5.0,
    )
    result = run_chaos_scenario(lc, fault)
    # The scenario itself must not crash
    assert result.passed, (
        f"fault {fault.value} violated invariants: {result.violations}"
    )
    assert check_invariants(lc) == []


def test_timeout_before_ack_never_silently_filled(tmp_path):
    store = ExecutionEventStore(tmp_path / "t.db").connect()
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    result = run_chaos_scenario(lc, FaultType.TIMEOUT_BEFORE_ACK)
    assert result.passed
    order = lc.order("intent_chaos")
    assert order.status == IntentStatus.MANUAL  # explicit, not silent
    assert order.filled_size == 0.0


def test_disagreement_fills_capped_by_remaining(tmp_path):
    store = ExecutionEventStore(tmp_path / "d.db").connect()
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    run_chaos_scenario(lc, FaultType.REST_WS_DISAGREEMENT)
    order = lc.order("intent_chaos")
    assert order.filled_size == 1.0  # never exceeds remaining


def test_sequence_gap_leaves_no_partial_state(tmp_path):
    store = ExecutionEventStore(tmp_path / "s.db").connect()
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    run_chaos_scenario(lc, FaultType.SEQUENCE_GAP)
    assert store.integrity_check()["ok"] is True
    assert [e.seq for e in store.read_events("intent_chaos")] == [1]


def test_process_kill_between_submit_and_persist_no_phantom(tmp_path):
    store = ExecutionEventStore(tmp_path / "k.db").connect()
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    run_chaos_scenario(lc, FaultType.PROCESS_KILL_BETWEEN_SUBMIT_AND_PERSIST)
    order = lc.order("intent_chaos")
    assert order.status == IntentStatus.APPROVED  # no phantom SUBMITTED


def test_network_loss_cancel_not_falsely_confirmed(tmp_path):
    store = ExecutionEventStore(tmp_path / "n.db").connect()
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
    )
    run_chaos_scenario(lc, FaultType.NETWORK_LOSS)
    order = lc.order("intent_chaos")
    assert order.status == IntentStatus.CANCEL_REQUESTED  # never false-confirmed


def test_stale_market_blocks_entry_even_with_good_intent(tmp_path):
    store = ExecutionEventStore(tmp_path / "m.db").connect()
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: None,  # no fresh data
        portfolio_source=lambda s: PortfolioRiskSnapshot(
            symbol=s,
            position_quantity=0.0,
            available_quantity=0.0,
            equity=100_000.0,
            available_cash=100_000.0,
            observed_at=datetime.now(UTC),
            source="test",
        ),
    )
    result = run_chaos_scenario(lc, FaultType.STALE_MARKET_DATA)
    assert result.passed
    order = lc.order("intent_chaos")
    assert order.status == IntentStatus.APPROVED  # authorize blocked
    assert order.filled_size == 0.0


def test_reconciliation_unresolved_blocks_new_entries(tmp_path):
    from trading_agent.execution.lifecycle import InvariantViolation

    store = ExecutionEventStore(tmp_path / "r.db").connect()
    lc = ExecutionLifecycle(
        store,
        price_source=lambda s: TrustedPrice(
            price=100.0,
            exchange_timestamp=datetime.now(UTC),
            received_at=datetime.now(UTC),
        ),
        portfolio_source=lambda s: PortfolioRiskSnapshot(
            symbol=s,
            position_quantity=0.0,
            available_quantity=0.0,
            equity=100_000.0,
            available_cash=100_000.0,
            observed_at=datetime.now(UTC),
            source="test",
        ),
    )
    risk_decision = _sample_risk_decision()
    lc.start_reconciliation()
    # Intents may be drafted, but market entry (authorize) is gated.
    lc.create_order_intent("blocked2", "BTC/USDT", "buy", 1.0)
    lc.approve_risk("blocked2", risk_decision=risk_decision)
    with pytest.raises(LifecycleError):
        lc.authorize_order("blocked2", idempotency_key="blocked2")
    # Once resolved, entry is allowed again.
    lc.resolve_reconciliation()
    lc.authorize_order("blocked2", idempotency_key="blocked2")
    lc.request_broker_submission("blocked2", claimed_by="blocked2")
    lc.submit_order("blocked2", exchange_order_id="ex_1")
