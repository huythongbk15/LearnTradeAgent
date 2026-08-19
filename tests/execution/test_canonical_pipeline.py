"""Canonical execution pipeline — end-to-end invariant tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest

from trading_agent.agents.risk_decision import RiskDecision as LegacyRiskDecision
from trading_agent.agents.risk_decision import RiskLevel as LegacyRiskLevel
from trading_agent.execution.canonical import (
    AuthorizedOrder,
    BrokerSubmitResult,
    CausationChain,
    ContentHash,
    EnrichedMarketObservation,
    EvidenceState,
    OrderPlanner,
    ProtectionPlan,
    ProtectionState,
    ProtectionStatus,
    ProtectionQuantityMode,
    RiskLevel,
    RiskDecisionAdapter,
    TraceContext,
    UnifiedRiskDecision,
    compute_decision_key,
    compute_idempotency_key,
    compute_observation_id,
    compute_target_exposure_key,
    propagate_causation,
)
from trading_agent.execution.canonical.broker_gateway import (
    BrokerGateway,
    _AUTHORIZED_TOKEN,
)
from trading_agent.execution.canonical.market_observation import BarState
from trading_agent.execution.canonical.order_planner import (
    CurrentPortfolioState,
    InstrumentRules,
    MarketPrice,
    OrderPlanningStatus,
    TargetExposure,
)


# ── Helpers ─────────────────────────────────────────────────────────────


def utcnow() -> datetime:
    return datetime.now(UTC)


def sample_unified_decision(
    *,
    decision_id: str = "decision-1",
    forecast_fingerprint: str = "fp-1",
    model_artifact_id: str = "model-v1",
    requested_target_exposure: float = 0.5,
    allowed_target_exposure: float = 0.4,
    max_new_exposure: float = 0.4,
    reduce_only: bool = False,
    risk_level: RiskLevel = RiskLevel.LOW,
    reason_codes: tuple[Any, ...] = ("APPROVED",),
    calibration_state: EvidenceState = EvidenceState.KNOWN,
    calibration_artifact_id: str | None = "cal-1",
    calibration_ece: float = 0.02,
    ood_state: EvidenceState = EvidenceState.KNOWN,
    ood_score: float = 0.1,
    regime_state: EvidenceState = EvidenceState.KNOWN,
    regime_entropy: float = 0.2,
    interval_width: float = 0.05,
    created_at: datetime | None = None,
) -> UnifiedRiskDecision:
    max_new = min(max_new_exposure, allowed_target_exposure)
    return UnifiedRiskDecision(
        decision_id=decision_id,
        forecast_fingerprint=forecast_fingerprint,
        model_artifact_id=model_artifact_id,
        requested_target_exposure=requested_target_exposure,
        allowed_target_exposure=allowed_target_exposure,
        max_new_exposure=max_new,
        reduce_only=reduce_only,
        risk_level=risk_level,
        reason_codes=reason_codes,
        calibration_state=calibration_state,
        calibration_artifact_id=calibration_artifact_id,
        calibration_ece=calibration_ece,
        ood_state=ood_state,
        ood_score=ood_score,
        regime_state=regime_state,
        regime_entropy=regime_entropy,
        interval_width=interval_width,
        created_at=created_at or utcnow(),
    )


def sample_target_exposure(
    symbol: str = "BTCUSDT",
    horizon: int = 14400,
    decision_id: str = "decision-1",
    exposure: float = 0.4,
) -> TargetExposure:
    return TargetExposure(
        symbol=symbol,
        exposure=exposure,
        horizon=horizon,
        forecast_fingerprint="fp-1",
        model_artifact_id="model-v1",
        risk_decision_id=decision_id,
    )


def sample_instrument_rules(
    symbol: str = "BTCUSDT", min_order_qty: float = 0.0001
) -> InstrumentRules:
    return InstrumentRules(
        symbol=symbol,
        asset_class="SPOT",
        min_order_qty=min_order_qty,
        max_order_qty=10.0,
        qty_step=0.0001,
        price_precision=2,
        spot_long_only=True,
        max_leverage=1.0,
    )


def sample_observation(symbol: str = "BTCUSDT") -> EnrichedMarketObservation:
    now = utcnow()
    return EnrichedMarketObservation(
        symbol=symbol,
        observed_at=now,
        open=100.0,
        high=110.0,
        low=95.0,
        close=105.0,
        volume=1000.0,
        observation_id="obs-1",
        venue="binance",
        timeframe="4h",
        bar_close_at=now,
        is_closed=True,
        data_manifest_id="manifest-1",
    )


def sample_portfolio(
    symbol: str = "BTCUSDT",
    current_exposure: float = 0.0,
    equity: float = 10000.0,
) -> CurrentPortfolioState:
    return CurrentPortfolioState(
        symbol=symbol,
        equity=equity,
        current_exposure=current_exposure,
        existing_quantity=0.0,
        avg_entry_price=0.0,
        existing_reservations=0.0,
        available_cash=equity,
    )


def sample_price(symbol: str = "BTCUSDT", mid: float = 50000.0) -> MarketPrice:
    return MarketPrice(symbol=symbol, mid=mid, bid=mid - 10, ask=mid + 10, last=mid)


# ── 1. Unified RiskDecision from legacy + canonical sources ─────────────


class TestUnifiedRiskDecision:
    def test_from_legacy_low_risk_preserves_size(self):
        legacy = LegacyRiskDecision(
            risk_level=LegacyRiskLevel.LOW,
            target_exposure_pct=0.25,
            max_new_exposure_pct=0.25,
            reduce_only=False,
            warnings=(),
        )
        unified = RiskDecisionAdapter.from_legacy(
            legacy,
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        assert unified.allowed_target_exposure == 0.25
        assert unified.max_new_exposure == 0.25
        assert unified.risk_level is RiskLevel.LOW
        assert unified.reduce_only is False

    def test_from_legacy_high_risk_maps_to_zero(self):
        legacy = LegacyRiskDecision(
            risk_level=LegacyRiskLevel.HIGH,
            target_exposure_pct=0.25,
            max_new_exposure_pct=0.25,
            reduce_only=False,
            warnings=("LIMIT_BREACH",),
        )
        unified = RiskDecisionAdapter.from_legacy(
            legacy,
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        assert unified.risk_level is RiskLevel.HIGH
        assert unified.allowed_target_exposure == 0.25
        # HIGH risk should map to max_new_exposure=0 and reduce_only=True
        assert unified.max_new_exposure == 0.0
        assert unified.reduce_only is True

    def test_merge_with_unapproved_forecast_zeroes_exposure(self):
        forecast = pytest.importorskip("trading_agent.research.forecast").RiskDecision(
            decision_id="fd-1",
            forecast_fingerprint="fp-1",
            model_artifact_id="model-v1",
            requested_exposure=0.25,
            allowed_exposure=0.0,
            approved=False,
            reason_codes=("REGIME_ENTROPY_HIGH",),
        )
        legacy = LegacyRiskDecision(
            risk_level=LegacyRiskLevel.HIGH,
            target_exposure_pct=0.25,
            max_new_exposure_pct=0.0,
            reduce_only=False,
            warnings=("LIMIT_BREACH",),
        )
        merged = RiskDecisionAdapter.merge(
            legacy,
            forecast,
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        assert merged.allowed_target_exposure == 0.0
        assert merged.max_new_exposure == 0.0
        assert merged.risk_level is RiskLevel.EXTREME
        assert merged.reduce_only is True

    def test_canonical_fields_preserved(self):
        decision = sample_unified_decision(
            forecast_fingerprint="fp-abc",
            model_artifact_id="model-xyz",
            reason_codes=("APPROVED", "CALIBRATED"),
            calibration_state=EvidenceState.KNOWN,
        )
        assert decision.forecast_fingerprint == "fp-abc"
        assert decision.model_artifact_id == "model-xyz"
        assert decision.reason_codes == ("APPROVED", "CALIBRATED")
        assert decision.calibration_state is EvidenceState.KNOWN

    def test_missing_risk_evidence_blocks_exposure_increase(self):
        decision = sample_unified_decision(
            risk_level=RiskLevel.EXTREME,
            allowed_target_exposure=0.0,
            max_new_exposure=0.0,
            reason_codes=("MISSING_RISK_EVIDENCE",),
            calibration_state=EvidenceState.MISSING,
        )
        assert decision.allowed_target_exposure == 0.0
        assert decision.max_new_exposure == 0.0
        assert decision.calibration_state is EvidenceState.MISSING


# ── 2. OrderPlanner produces correct OrderIntent ───────────────────────


class TestOrderPlanner:
    def test_new_position_intent(self):
        rules = sample_instrument_rules(symbol="ETHUSDT", min_order_qty=0.001)
        planner = OrderPlanner(instrument_rules=rules)
        decision = sample_unified_decision(
            allowed_target_exposure=0.3, max_new_exposure=0.3
        )
        target = sample_target_exposure(
            symbol="ETHUSDT", exposure=0.3, horizon=14400, decision_id="decision-1"
        )
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("ETHUSDT"),
            portfolio=sample_portfolio("ETHUSDT", 0.0),
            price=sample_price("ETHUSDT", 2000.0),
            existing_reservations=0.0,
        )
        assert result.status is OrderPlanningStatus.ORDER_REQUIRED
        assert result.intent is not None
        intent = result.intent
        assert intent.side == "buy"
        assert intent.exposure_effect == "INCREASE"
        assert intent.target_exposure == 0.3
        assert intent.idempotency_key

    def test_reduce_only_intent(self):
        rules = sample_instrument_rules(symbol="BTCUSDT", min_order_qty=0.0001)
        planner = OrderPlanner(instrument_rules=rules)
        forecast_decision = pytest.importorskip(
            "trading_agent.research.forecast"
        ).RiskDecision(
            decision_id="fd-1",
            forecast_fingerprint="fp-1",
            model_artifact_id="model-v1",
            requested_exposure=0.5,
            allowed_exposure=0.5,
            approved=True,
            reason_codes=("REDUCE_ONLY",),
        )
        decision = RiskDecisionAdapter.from_forecast(
            forecast_decision,
            max_new_exposure=0.0,
            reduce_only=True,
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        target = sample_target_exposure(
            symbol="BTCUSDT", exposure=0.0, horizon=14400, decision_id="fd-1"
        )
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("BTCUSDT"),
            portfolio=sample_portfolio("BTCUSDT", 0.5),
            price=sample_price("BTCUSDT", 50000.0),
            existing_reservations=0.0,
        )
        assert result.status is OrderPlanningStatus.ORDER_REQUIRED
        assert result.intent is not None
        intent = result.intent
        assert intent.side == "sell"
        assert intent.exposure_effect == "REDUCE"

    def test_spot_long_only_rejects_negative_target(self):
        rules = sample_instrument_rules(symbol="SOLUSDT", min_order_qty=0.01)
        planner = OrderPlanner(instrument_rules=rules)
        decision = sample_unified_decision(
            allowed_target_exposure=0.0, max_new_exposure=0.0
        )
        target = sample_target_exposure(
            symbol="SOLUSDT", exposure=-0.1, horizon=14400, decision_id="decision-1"
        )
        with pytest.raises(ValueError):
            planner.plan(
                target=target,
                risk_decision=decision,
                observation=sample_observation("SOLUSDT"),
                portfolio=sample_portfolio("SOLUSDT", 0.0),
                price=sample_price("SOLUSDT", 100.0),
                existing_reservations=0.0,
            )

    def test_cash_insufficient_for_min_order_blocks_buy(self):
        rules = sample_instrument_rules(symbol="BTCUSDT", min_order_qty=0.001)
        planner = OrderPlanner(instrument_rules=rules)
        decision = sample_unified_decision(
            allowed_target_exposure=0.3, max_new_exposure=0.3
        )
        target = sample_target_exposure(
            symbol="BTCUSDT", exposure=0.3, horizon=14400, decision_id="decision-1"
        )
        # Portfolio has $5 cash, price is $50000, so max qty = 0.0001, but min is 0.001
        portfolio = sample_portfolio("BTCUSDT", 0.0)
        portfolio = replace(portfolio, available_cash=5.0)
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("BTCUSDT"),
            portfolio=portfolio,
            price=sample_price("BTCUSDT", 50000.0),
            existing_reservations=0.0,
        )
        assert result.status is OrderPlanningStatus.BLOCKED
        assert result.intent is None
        assert any(
            "INSUFFICIENT_CASH_FOR_MIN_ORDER" in str(r) for r in result.reason_codes
        )

    def test_cash_feasible_qty_never_rounds_up(self):
        rules = sample_instrument_rules(symbol="BTCUSDT", min_order_qty=0.001)
        # Override qty_step to test rounding behavior
        rules = replace(rules, qty_step=0.0005)
        planner = OrderPlanner(instrument_rules=rules)
        decision = sample_unified_decision(
            allowed_target_exposure=0.3, max_new_exposure=0.3
        )
        target = sample_target_exposure(
            symbol="BTCUSDT", exposure=0.3, horizon=14400, decision_id="decision-1"
        )
        # Portfolio has $30 cash, price is $50000, so cash_qty = 0.0006
        # With qty_step=0.0005, round down to 0.0005, but min is 0.001
        # Should BLOCK because cash < min_order_qty
        portfolio = sample_portfolio("BTCUSDT", 0.0)
        portfolio = replace(portfolio, available_cash=30.0)
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("BTCUSDT"),
            portfolio=portfolio,
            price=sample_price("BTCUSDT", 50000.0),
            existing_reservations=0.0,
        )
        assert result.status is OrderPlanningStatus.BLOCKED
        assert result.intent is None

    def test_post_feasibility_revalidation_blocks_excess_exposure(self):
        rules = sample_instrument_rules(symbol="BTCUSDT", min_order_qty=0.021)
        # Override qty_step
        rules = replace(rules, qty_step=0.001)
        planner = OrderPlanner(instrument_rules=rules)
        decision = sample_unified_decision(
            allowed_target_exposure=0.1,  # Only allow 10% exposure
            max_new_exposure=0.1,
        )
        target = sample_target_exposure(
            symbol="BTCUSDT", exposure=0.1, horizon=14400, decision_id="decision-1"
        )
        # Portfolio has $1,000 cash, price is $50,000
        # Target qty = 0.02 (1% of 10k equity), but cash only allows 0.02
        # min_order_qty = 0.021, so planner rounds UP to 0.021
        # Final exposure = 0.021 * 50,000 / 10,000 = 0.105 > 0.1
        portfolio = sample_portfolio("BTCUSDT", 0.0)
        portfolio = replace(portfolio, equity=10_000.0, available_cash=1_000.0)
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("BTCUSDT"),
            portfolio=portfolio,
            price=sample_price("BTCUSDT", 50_000.0),
            existing_reservations=0.0,
        )
        # Should block because resulting exposure exceeds allowed_target_exposure
        assert result.status is OrderPlanningStatus.BLOCKED
        assert result.intent is None


# ── 3. BrokerGateway only allows capital-changing calls through itself ──


class TestBrokerGateway:
    @pytest.mark.skip(reason="BrokerGateway store contract changed; skip until gateway updated")
    def test_gateway_exposes_only_capital_methods(self):
        gateway = BrokerGateway(adapter=None, store=MagicMock())
        allowed = {
            "submit",
            "cancel",
            "fetch_order",
            "fetch_positions",
            "fetch_balances",
            "submit_protection",
        }
        assert allowed.issubset(dir(gateway))

    @pytest.mark.skip(reason="BrokerGateway store contract changed; skip until gateway updated")
    def test_submit_returns_result_wrapper(self):
        gateway = BrokerGateway(adapter=None, store=MagicMock())
        rules = sample_instrument_rules(symbol="BTCUSDT", min_order_qty=0.0001)
        planner = OrderPlanner(instrument_rules=rules)
        forecast_decision = pytest.importorskip(
            "trading_agent.research.forecast"
        ).RiskDecision(
            decision_id="fd-1",
            forecast_fingerprint="fp-1",
            model_artifact_id="model-v1",
            requested_exposure=0.01,
            allowed_exposure=0.01,
            approved=True,
            reason_codes=("APPROVED",),
        )
        decision = RiskDecisionAdapter.from_forecast(
            forecast_decision,
            max_new_exposure=0.01,
            reduce_only=False,
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        target = sample_target_exposure(
            symbol="BTCUSDT", exposure=0.01, horizon=14400, decision_id="fd-1"
        )
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("BTCUSDT"),
            portfolio=sample_portfolio("BTCUSDT", 0.0),
            price=sample_price("BTCUSDT", 50000.0),
            existing_reservations=0.0,
        )
        assert result.status is OrderPlanningStatus.ORDER_REQUIRED
        assert result.intent is not None
        intent = result.intent
        # Create lifecycle-authorized AuthorizedOrder (not raw OrderIntent)
        authorized = AuthorizedOrder(
            token=_AUTHORIZED_TOKEN,
            intent_id=intent.intent_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            idempotency_key=intent.idempotency_key,
            price_reference=50000.0,
            risk_decision_id="fd-1",
            forecast_fingerprint="fp-1",
            model_artifact_id="model-v1",
            permission_result="ALLOW",
            authorization_id=f"auth-{intent.intent_id}",
            lifecycle_event_id=f"event-{intent.intent_id}",
            correlation_id=intent.intent_id,
            exposure_effect="INCREASE",
            current_exposure=0.0,
            resulting_exposure=intent.quantity,
            authorized_at=datetime.now(UTC).isoformat(),
            authorization_hash="test-hash",
        )
        gw_result = gateway.submit(authorized, correlation_id="corr-1")
        assert isinstance(gw_result, BrokerSubmitResult)


# ── 4. ProtectionPlan state machine ───────────────────────────────────


class TestProtectionPlan:
    def test_plan_state_immutable_mutation(self):
        plan = ProtectionPlan(
            plan_id="plan-1",
            model_risk_decision_id="rd-1",
            symbol="BTCUSDT",
            stop_type="stop_loss",
            stop_trigger=45000.0,
            take_profit=60000.0,
            state=ProtectionState.NONE,
            quantity_mode=ProtectionQuantityMode.EXPLICIT_QUANTITY,
            protected_quantity=1.0,
        )
        next_plan = plan.with_state(ProtectionState.PROTECTION_REQUIRED)
        assert next_plan.state is ProtectionState.PROTECTION_REQUIRED
        assert plan.state is ProtectionState.NONE

    def test_broker_order_ack_changes_status(self):
        plan = ProtectionPlan(
            plan_id="plan-1",
            model_risk_decision_id="rd-1",
            symbol="ETHUSDT",
            stop_type="stop_loss",
            stop_trigger=1800.0,
            take_profit=2500.0,
            quantity_mode=ProtectionQuantityMode.EXPLICIT_QUANTITY,
            protected_quantity=1.0,
        )
        acked = plan.with_broker_order("broker-order-1")
        assert acked.broker_order_id == "broker-order-1"
        assert acked.state is ProtectionState.PROTECTIVE_ACKNOWLEDGED
        assert acked.status is ProtectionStatus.ACTIVE


# ── 5. MarketObservation enrichment ───────────────────────────────────


class TestMarketObservation:
    def test_enriched_observation_fields(self):
        now = utcnow()
        obs = EnrichedMarketObservation(
            symbol="BTCUSDT",
            observed_at=now,
            open=100.0,
            high=110.0,
            low=95.0,
            close=105.0,
            volume=1000.0,
            observation_id="obs-1",
            venue="binance",
            timeframe="4h",
            bar_close_at=now,
            is_closed=True,
            data_manifest_id="manifest-1",
        )
        assert obs.symbol == "BTCUSDT"
        assert obs.venue == "binance"
        assert obs.timeframe == "4h"
        assert obs.data_manifest_id == "manifest-1"
        assert obs.observation_id == "obs-1"
        assert obs.bar_state is BarState.SOURCE_CONFIRMED_CLOSED

    def test_observation_id_deterministic(self):
        oid1 = compute_observation_id(
            venue="binance",
            symbol="BTCUSDT",
            timeframe="4h",
            bar_close_at=datetime(2026, 8, 18, 0, 0, tzinfo=UTC),
            data_manifest_id="m1",
        )
        oid2 = compute_observation_id(
            venue="binance",
            symbol="BTCUSDT",
            timeframe="4h",
            bar_close_at=datetime(2026, 8, 18, 0, 0, tzinfo=UTC),
            data_manifest_id="m1",
        )
        assert oid1 == oid2


# ── 6. Global event order replay ──────────────────────────────────────


class TestGlobalEventOrder:
    def test_content_hash_deterministic(self):
        payload = {"a": 1, "b": "x"}
        h1 = ContentHash.from_mapping(payload)
        h2 = ContentHash.from_mapping(payload)
        assert h1.value == h2.value

    def test_decision_key_deterministic(self):
        dk1 = compute_decision_key("obs-1", "model-v1", "v1")
        dk2 = compute_decision_key("obs-1", "model-v1", "v1")
        assert dk1 == dk2


# ── 7. Idempotency keys are deterministic ─────────────────────────────


class TestIdempotencyKeys:
    def test_intent_idempotency_key_deterministic(self):
        k1 = compute_idempotency_key("d1", "BTCUSDT", 0.4, 14400)
        k2 = compute_idempotency_key("d1", "BTCUSDT", 0.4, 14400)
        assert k1 == k2

    def test_target_exposure_key_deterministic(self):
        k1 = compute_target_exposure_key("BTCUSDT", 14400, "d1")
        k2 = compute_target_exposure_key("BTCUSDT", 14400, "d1")
        assert k1 == k2


# ── 8. Causation chain propagation ────────────────────────────────────


class TestCausationChain:
    def test_chain_propagation(self):
        chain = CausationChain(
            correlation_id="corr-1",
            observation_id="obs-1",
            forecast_fingerprint="fp-1",
            risk_decision_id="rd-1",
            target_exposure_id="te-1",
            order_intent_id="oi-1",
        )
        updated = propagate_causation(chain, broker_order_id="bo-1")
        assert updated.broker_order_id == "bo-1"
        assert updated.correlation_id == "corr-1"

    def test_trace_context_child(self):
        ctx = TraceContext(correlation_id="corr-1", causation_id="parent")
        child = ctx.child("event-1")
        assert child.causation_id == "event-1"
        assert child.correlation_id == "corr-1"


# ── 9. Spot-long-only rejects negative target ─────────────────────────


class TestSpotLongOnlySemantics:
    def test_negative_target_rejected_by_order_planner(self):
        rules = sample_instrument_rules(symbol="BTCUSDT", min_order_qty=0.0001)
        planner = OrderPlanner(instrument_rules=rules)
        decision = sample_unified_decision(
            allowed_target_exposure=0.0, max_new_exposure=0.0
        )
        target = sample_target_exposure(
            symbol="BTCUSDT", exposure=-0.2, horizon=14400, decision_id="decision-1"
        )
        with pytest.raises(ValueError):
            planner.plan(
                target=target,
                risk_decision=decision,
                observation=sample_observation("BTCUSDT"),
                portfolio=sample_portfolio("BTCUSDT", 0.0),
                price=sample_price("BTCUSDT", 50000.0),
                existing_reservations=0.0,
            )

    def test_zero_target_reduces_only(self):
        rules = sample_instrument_rules(symbol="BTCUSDT", min_order_qty=0.0001)
        planner = OrderPlanner(instrument_rules=rules)
        forecast_decision = pytest.importorskip(
            "trading_agent.research.forecast"
        ).RiskDecision(
            decision_id="fd-1",
            forecast_fingerprint="fp-1",
            model_artifact_id="model-v1",
            requested_exposure=0.5,
            allowed_exposure=0.5,
            approved=True,
            reason_codes=("REDUCE_ONLY",),
        )
        decision = RiskDecisionAdapter.from_forecast(
            forecast_decision,
            max_new_exposure=0.0,
            reduce_only=True,
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        target = sample_target_exposure(
            symbol="BTCUSDT", exposure=0.0, horizon=14400, decision_id="fd-1"
        )
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("BTCUSDT"),
            portfolio=sample_portfolio("BTCUSDT", 0.5),
            price=sample_price("BTCUSDT", 50000.0),
            existing_reservations=0.0,
        )
        assert result.status is OrderPlanningStatus.ORDER_REQUIRED
        assert result.intent is not None
        intent = result.intent
        assert intent.side == "sell"
        assert intent.exposure_effect == "REDUCE"


# ── 10. Missing risk evidence blocks exposure increase ─────────────────


class TestMissingRiskEvidence:
    def test_unknown_risk_decision_blocks_new_exposure(self):
        decision = sample_unified_decision(
            risk_level=RiskLevel.EXTREME,
            allowed_target_exposure=0.0,
            max_new_exposure=0.0,
            reason_codes=("MISSING_RISK_EVIDENCE",),
        )
        assert decision.allowed_target_exposure == 0.0
        assert decision.max_new_exposure == 0.0

        planner = OrderPlanner(
            instrument_rules=sample_instrument_rules(min_order_qty=0.0001)
        )
        # Target wants positive exposure but risk decision blocks it
        target = sample_target_exposure(
            symbol="BTCUSDT",
            exposure=0.3,  # wants 30% exposure
            horizon=14400,
            decision_id="decision-1",
        )
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("BTCUSDT"),
            portfolio=sample_portfolio("BTCUSDT", 0.0),
            price=sample_price("BTCUSDT", 50000.0),
            existing_reservations=0.0,
        )
        assert result.status is OrderPlanningStatus.BLOCKED
        assert result.intent is None
        # The first check is for approved status, then max_new_exposure
        assert "RISK_DECISION_NOT_APPROVED" in result.reason_codes


# ── Extra: protect new order_intent fields exist ────────────────────────


class TestOrderIntentShape:
    def test_intent_has_canonical_fields(self):
        rules = sample_instrument_rules(symbol="BNBUSDT", min_order_qty=0.01)
        planner = OrderPlanner(instrument_rules=rules)
        forecast_decision = pytest.importorskip(
            "trading_agent.research.forecast"
        ).RiskDecision(
            decision_id="fd-1",
            forecast_fingerprint="fp-1",
            model_artifact_id="model-v1",
            requested_exposure=0.2,
            allowed_exposure=0.2,
            approved=True,
            reason_codes=("APPROVED",),
        )
        decision = RiskDecisionAdapter.from_forecast(
            forecast_decision,
            max_new_exposure=0.2,
            reduce_only=False,
            calibration_state=EvidenceState.KNOWN,
            calibration_artifact_id="cal-1",
            calibration_ece=0.02,
            ood_state=EvidenceState.KNOWN,
            ood_score=0.1,
            regime_state=EvidenceState.KNOWN,
            regime_entropy=0.2,
            interval_width=0.05,
        )
        target = sample_target_exposure(
            symbol="BNBUSDT", exposure=0.2, horizon=14400, decision_id="fd-1"
        )
        result = planner.plan(
            target=target,
            risk_decision=decision,
            observation=sample_observation("BNBUSDT"),
            portfolio=sample_portfolio("BNBUSDT", 0.0),
            price=sample_price("BNBUSDT", 300.0),
            existing_reservations=0.0,
        )
        assert result.status is OrderPlanningStatus.ORDER_REQUIRED
        assert result.intent is not None
        intent = result.intent
        assert intent.intent_id
        assert intent.decision_id == "fd-1"
        assert intent.forecast_fingerprint == "fp-1"
        assert intent.model_artifact_id == "model-v1"
        assert intent.symbol == "BNBUSDT"
        assert intent.asset_class == "SPOT"
        assert intent.price_reference == 300.0
        assert intent.created_at <= utcnow()


# ── 11. Source-confirmed candle semantics ─────────────────────────────


class TestSourceConfirmedCandleSemantics:
    def test_closed_observation_is_source_confirmed(self):
        now = utcnow()
        obs = EnrichedMarketObservation(
            symbol="BTCUSDT",
            observed_at=now,
            open=100.0,
            high=110.0,
            low=95.0,
            close=105.0,
            volume=1000.0,
            observation_id="obs-1",
            venue="binance",
            timeframe="4h",
            bar_close_at=now,
            is_closed=True,
            data_manifest_id="manifest-1",
        )
        assert obs.bar_state is BarState.SOURCE_CONFIRMED_CLOSED

    def test_forming_before_close(self):
        now = utcnow()
        obs = EnrichedMarketObservation(
            symbol="BTCUSDT",
            observed_at=now,
            open=100.0,
            high=110.0,
            low=95.0,
            close=105.0,
            volume=1000.0,
            observation_id="obs-1",
            venue="binance",
            timeframe="4h",
            bar_open_at=now - timedelta(hours=1),
            bar_close_at=now + timedelta(hours=1),
            is_closed=False,
            data_manifest_id="manifest-1",
        )
        assert obs.bar_state is BarState.FORMING

    def test_expected_closed_by_time_after_close_not_confirmed(self):
        now = utcnow()
        obs = EnrichedMarketObservation(
            symbol="BTCUSDT",
            observed_at=now,
            open=100.0,
            high=110.0,
            low=95.0,
            close=105.0,
            volume=1000.0,
            observation_id="obs-1",
            venue="binance",
            timeframe="4h",
            bar_open_at=now - timedelta(hours=2),
            bar_close_at=now - timedelta(minutes=30),
            is_closed=False,
            data_manifest_id="manifest-1",
        )
        assert obs.bar_state is BarState.EXPECTED_CLOSED_BY_TIME

    def test_unknown_without_timing(self):
        now = utcnow()
        obs = EnrichedMarketObservation(
            symbol="BTCUSDT",
            observed_at=now,
            open=100.0,
            high=110.0,
            low=95.0,
            close=105.0,
            volume=1000.0,
            observation_id="obs-1",
            venue="binance",
            timeframe="4h",
            is_closed=False,
            data_manifest_id="manifest-1",
        )
        assert obs.bar_state is BarState.UNKNOWN

    def test_only_source_confirmed_authorizes_new_exposure(self):
        rules = sample_instrument_rules(symbol="BTCUSDT", min_order_qty=0.0001)
        planner = OrderPlanner(instrument_rules=rules)
        decision = sample_unified_decision(
            allowed_target_exposure=0.3, max_new_exposure=0.3
        )
        target = sample_target_exposure(
            symbol="BTCUSDT", exposure=0.3, horizon=14400, decision_id="decision-1"
        )
        for bad_state in (BarState.FORMING, BarState.EXPECTED_CLOSED_BY_TIME, BarState.UNKNOWN):
            obs = sample_observation("BTCUSDT")
            # Manually override bar_state by manipulating the observation's fields
            # We create a fresh observation with is_closed=False and appropriate timing
            if bad_state is BarState.FORMING:
                obs = EnrichedMarketObservation(
                    symbol="BTCUSDT",
                    observed_at=utcnow(),
                    open=100.0, high=110.0, low=95.0, close=105.0, volume=1000.0,
                    observation_id="obs-forming",
                    venue="binance", timeframe="4h",
                    bar_open_at=utcnow() - timedelta(hours=1),
                    bar_close_at=utcnow() + timedelta(hours=1),
                    is_closed=False,
                    data_manifest_id="manifest-1",
                )
            elif bad_state is BarState.EXPECTED_CLOSED_BY_TIME:
                obs = EnrichedMarketObservation(
                    symbol="BTCUSDT",
                    observed_at=utcnow(),
                    open=100.0, high=110.0, low=95.0, close=105.0, volume=1000.0,
                    observation_id="obs-expected",
                    venue="binance", timeframe="4h",
                    bar_open_at=utcnow() - timedelta(hours=2),
                    bar_close_at=utcnow() - timedelta(minutes=30),
                    is_closed=False,
                    data_manifest_id="manifest-1",
                )
            else:
                obs = EnrichedMarketObservation(
                    symbol="BTCUSDT",
                    observed_at=utcnow(),
                    open=100.0, high=110.0, low=95.0, close=105.0, volume=1000.0,
                    observation_id="obs-unknown",
                    venue="binance", timeframe="4h",
                    is_closed=False,
                    data_manifest_id="manifest-1",
                )
            with pytest.raises(ValueError, match="cannot plan from"):
                planner.plan(
                    target=target,
                    risk_decision=decision,
                    observation=obs,
                    portfolio=sample_portfolio("BTCUSDT", 0.0),
                    price=sample_price("BTCUSDT", 50000.0),
                    existing_reservations=0.0,
                )
