"""Historical previews diagnose gates, never grant runtime order authority."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from trading_agent.execution.application import (
    CanonicalExecutionService,
    ExecutionBlockedError,
)
from trading_agent.execution.canonical import BrokerGateway, PaperExecutionAdapter
from trading_agent.execution.canonical.order_planner import OrderPlanningStatus
from trading_agent.execution.canonical.risk_decision import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.lifecycle import (
    ExecutionEventStore,
    ExecutionLifecycle,
    TrustedPrice,
)
from trading_agent.execution.lifecycle.lifecycle import ExecutionHealth, ExposureEffect
from trading_agent.execution.paper_exchange import PaperExchange
from trading_agent.execution.permission import (
    OrderPermission,
    PermissionContext,
    PermissionReason,
    PermissionResult,
    evaluate_order_permission,
    preview_historical_order_permission,
)

AS_OF = datetime(2024, 2, 1, tzinfo=UTC)


def context():
    # Unit fixture only: no fixture evidence is published as case evidence.
    risk = UnifiedRiskDecision(
        decision_id="unit-only",
        forecast_fingerprint="unit-only",
        model_artifact_id="unit-only",
        requested_target_exposure=0.25,
        allowed_target_exposure=0.25,
        max_new_exposure=0.25,
        reduce_only=False,
        risk_level=RiskLevel.HIGH,
        reason_codes=(),
        calibration_state=EvidenceState.UNKNOWN,
        calibration_artifact_id=None,
        calibration_ece=1,
        ood_state=EvidenceState.UNKNOWN,
        ood_score=1,
        regime_state=EvidenceState.UNKNOWN,
        regime_entropy=1,
        interval_width=1,
        created_at=AS_OF,
    )
    return PermissionContext(
        execution_health=ExecutionHealth.NORMAL,
        exposure_effect=ExposureEffect.INCREASE,
        risk_decision=risk,
        trusted_price=TrustedPrice(500, AS_OF, AS_OF),
        order_size=1,
    )


@pytest.mark.parametrize(
    "exchange_age,received_age,expected",
    [
        (0, 0, True),
        (60, 60, True),
        (60.001, 0, False),
        (0, 60.001, False),
        (-1, 0, False),
        (0, -1, False),
    ],
)
def test_price_freshness_at_exact_simulation_clock(
    exchange_age, received_age, expected
):
    quote = TrustedPrice(
        500,
        AS_OF - timedelta(seconds=exchange_age),
        AS_OF - timedelta(seconds=received_age),
    )
    assert quote.is_fresh_at(60, AS_OF) is expected
    assert not quote.is_fresh(60)  # Normal runtime still uses wall clock.


@pytest.mark.parametrize("limit", [0, -1, float("inf"), float("nan")])
def test_bad_freshness_limit_fails_closed(limit):
    assert not TrustedPrice(500, AS_OF, AS_OF).is_fresh_at(limit, AS_OF)


@pytest.mark.parametrize("field", ["reference", "exchange", "received"])
def test_naive_clock_fails_closed(field):
    values = {"reference": AS_OF, "exchange": AS_OF, "received": AS_OF}
    values[field] = AS_OF.replace(tzinfo=None)
    assert not TrustedPrice(500, values["exchange"], values["received"]).is_fresh_at(
        60, values["reference"]
    )


def test_preview_requires_aware_clock():
    with pytest.raises(ValueError, match="aware"):
        preview_historical_order_permission(
            context(), reference_time=AS_OF.replace(tzinfo=None)
        )


def test_unknown_evidence_and_kill_switch_remain_blocking():
    ctx = context()
    preview = preview_historical_order_permission(ctx, reference_time=AS_OF)
    assert preview.permission is OrderPermission.BLOCK
    assert preview.reason is PermissionReason.MISSING_CALIBRATION_EVIDENCE
    assert not isinstance(preview, PermissionResult)
    assert not hasattr(preview, "allowed")
    stopped = preview_historical_order_permission(
        replace(ctx, kill_switch_active=True), reference_time=AS_OF
    )
    assert stopped.reason is PermissionReason.KILL_SWITCH_INCREASE
    assert evaluate_order_permission(ctx).reason is PermissionReason.STALE_MARKET_DATA


@pytest.mark.parametrize(
    "state,reason",
    [
        ("ood_state", PermissionReason.MISSING_OOD_EVIDENCE),
        ("regime_state", PermissionReason.MISSING_REGIME_EVIDENCE),
    ],
)
def test_remaining_evidence_checks_are_not_skipped(state, reason):
    ctx = context()
    fixture = replace(
        ctx.risk_decision,
        calibration_state=EvidenceState.KNOWN,
        ood_state=EvidenceState.KNOWN,
        regime_state=EvidenceState.KNOWN,
    )
    fixture = replace(fixture, **{state: EvidenceState.UNKNOWN})
    preview = preview_historical_order_permission(
        replace(ctx, risk_decision=fixture), reference_time=AS_OF
    )
    assert preview.permission is OrderPermission.BLOCK and preview.reason is reason


def test_preview_does_not_change_global_runtime_clock():
    ctx = context()
    preview_historical_order_permission(ctx, reference_time=AS_OF)
    assert evaluate_order_permission(ctx).reason is PermissionReason.STALE_MARKET_DATA
    now = datetime.now(UTC)
    fresh = replace(ctx, trusted_price=TrustedPrice(500, now, now))
    assert (
        evaluate_order_permission(fresh).reason
        is PermissionReason.MISSING_CALIBRATION_EVIDENCE
    )


def test_even_allow_preview_cannot_submit_stale_runtime_context(tmp_path):
    ctx = context()
    fixture = replace(
        ctx.risk_decision,
        calibration_state=EvidenceState.KNOWN,
        ood_state=EvidenceState.KNOWN,
        regime_state=EvidenceState.KNOWN,
    )
    ctx = replace(ctx, risk_decision=fixture)
    preview = preview_historical_order_permission(ctx, reference_time=AS_OF)
    assert preview.permission is OrderPermission.ALLOW  # Unit fixture, not case.
    exchange = PaperExchange(state_dir=tmp_path / "paper", telemetry=None)
    store = ExecutionEventStore(str(tmp_path / "events.db")).connect()
    try:
        lifecycle = ExecutionLifecycle(store)
        service = CanonicalExecutionService(
            lifecycle=lifecycle,
            gateway=BrokerGateway(
                adapter=PaperExecutionAdapter(exchange),
                store=store,
                lifecycle=lifecycle,
            ),
        )
        planning = SimpleNamespace(
            status=OrderPlanningStatus.ORDER_REQUIRED,
            intent=SimpleNamespace(side="buy", quantity=1),
        )
        with pytest.raises(ExecutionBlockedError, match="stale_market_data"):
            service.submit_planned(
                planning=planning, risk_decision=fixture, permission_context=ctx
            )
        with pytest.raises(
            ExecutionBlockedError, match="previews/results are not authorization"
        ):
            service.submit_planned(
                planning=planning, risk_decision=fixture, permission_context=preview
            )
        with pytest.raises(TypeError, match="PermissionContext"):
            evaluate_order_permission(preview)
        with pytest.raises(TypeError, match="PermissionContext"):
            preview_historical_order_permission(preview, reference_time=AS_OF)
        assert not lifecycle.state.orders and not exchange.orders and store.count() == 0
    finally:
        store.close()


@pytest.mark.parametrize(
    "bad", [None, {}, SimpleNamespace(order_side="buy", order_size=1)]
)
def test_runtime_rejects_untyped_or_missing_permission_context(bad):
    with pytest.raises(TypeError, match="PermissionContext"):
        evaluate_order_permission(bad)
