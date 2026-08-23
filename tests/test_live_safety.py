from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.execution.canonical import (
    EvidenceState,
    RiskLevel,
    UnifiedRiskDecision,
)
from trading_agent.execution.lifecycle import TrustedPrice
from trading_agent.execution.live_safety import (
    LIVE_CONFIRMATION,
    DuplicateOrderError,
    LiveExecutionLock,
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
    account_fingerprint,
    append_live_audit_event,
    configured_entry_lock_reason,
    make_order_key,
    require_execution_authorization,
    sign_strategy_evidence,
    strategy_fingerprint,
    validate_fresh_quote,
    validate_order_book_depth,
    validate_order_risk,
    validate_spread,
    validate_strategy_evidence,
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


def _trusted_price() -> TrustedPrice:
    now = datetime.now(UTC)
    return TrustedPrice(price=100.0, exchange_timestamp=now, received_at=now)


def test_mainnet_execution_requires_both_confirmations():
    base = {
        "TRADING_EXECUTION_ENABLED": "true",
        "TRADING_MODE": "live",
        "TRADING_LIVE_CONFIRMATION": LIVE_CONFIRMATION,
    }
    with pytest.raises(LiveSafetyError, match="confirm-live"):
        require_execution_authorization(
            execute=True, testnet=False, cli_confirmation=None, env=base
        )
    require_execution_authorization(
        execute=True,
        testnet=False,
        cli_confirmation=LIVE_CONFIRMATION,
        env=base,
    )


def test_kill_switch_overrides_all_execution_gates():
    env = {
        "TRADING_KILL_SWITCH": "true",
        "TRADING_EXECUTION_ENABLED": "true",
        "TRADING_MODE": "testnet",
    }
    with pytest.raises(LiveSafetyError, match="KILL_SWITCH"):
        require_execution_authorization(
            execute=True, testnet=True, cli_confirmation=None, env=env
        )


def test_entry_kill_switch_keeps_the_authorized_exit_path_available():
    env = {
        "TRADING_ENTRY_KILL_SWITCH": "true",
        "TRADING_EXECUTION_ENABLED": "true",
        "TRADING_MODE": "testnet",
    }
    require_execution_authorization(
        execute=True, testnet=True, cli_confirmation=None, env=env
    )
    assert configured_entry_lock_reason(env) == "TRADING_ENTRY_KILL_SWITCH is active"
    assert configured_entry_lock_reason({"TRADING_ENTRY_KILL_SWITCH": "false"}) is None


def test_corrupt_state_fails_closed(tmp_path):
    state_path = tmp_path / "risk.json"
    state_path.write_text("not json", encoding="utf-8")
    with pytest.raises(LiveSafetyError, match="corrupt live risk state"):
        LiveRiskStateStore(state_path)


def test_daily_loss_lock_persists_across_reload(tmp_path):
    state_path = tmp_path / "risk.json"
    limits = LiveRiskLimits(max_daily_loss_pct=0.02)
    store = LiveRiskStateStore(state_path)
    now = datetime(2026, 8, 10, 0, 0, tzinfo=UTC)
    assert store.observe_equity(1000, limits, now=now) is None
    reason = store.observe_equity(979, limits, now=now + timedelta(hours=1))
    assert reason and "daily loss" in reason
    assert LiveRiskStateStore(state_path).state.locked_reason == reason


def test_order_reservation_is_idempotent(tmp_path):
    store = LiveRiskStateStore(tmp_path / "risk.json")
    store.reserve_order("lta-order")
    with pytest.raises(DuplicateOrderError):
        store.reserve_order("lta-order")


def test_order_ledger_persists_terminal_and_unfinished_states(tmp_path):
    state_path = tmp_path / "risk.json"
    store = LiveRiskStateStore(state_path)
    store.reserve_order(
        "pending",
        symbol="BTC/USDT",
        side="BUY",
        quantity=0.1,
        signal_timestamp=datetime(2026, 8, 10, tzinfo=UTC),
    )
    assert set(store.unfinished_orders()) == {"pending"}
    store.update_order("pending", status="submitted")
    store.update_order("pending", status="acknowledged")
    store.update_order(
        "pending",
        status="filled",
        exchange_order_id="123",
        filled_quantity=0.1,
        average_fill_price=100.0,
        quote_cost=10.0,
        fees={"USDT": 0.01, "BNB": 0.001},
        trade_ids=["trade-1", "trade-2"],
        exchange_status="closed",
    )
    reloaded = LiveRiskStateStore(state_path)
    assert reloaded.unfinished_orders() == {}
    record = reloaded.state.order_ledger["pending"]
    assert record["exchange_order_id"] == "123"
    assert record["quote_cost"] == pytest.approx(10.0)
    assert record["fees"] == {"USDT": 0.01, "BNB": 0.001}
    assert record["trade_ids"] == ["trade-1", "trade-2"]
    assert record["exchange_status"] == "closed"
    assert [event["status"] for event in record["status_history"]] == [
        "reserved",
        "submitted",
        "acknowledged",
        "filled",
    ]


def test_order_fill_accounting_cannot_move_backwards(tmp_path):
    store = LiveRiskStateStore(tmp_path / "risk.json")
    store.reserve_order("order", quantity=1.0)
    store.update_order("order", status="submitted")
    store.update_order("order", status="acknowledged")
    store.update_order(
        "order",
        status="partial",
        filled_quantity=0.5,
        quote_cost=50.0,
        fees={"USDT": 0.05},
        trade_ids=["trade-1"],
    )
    with pytest.raises(LiveSafetyError, match="filled quantity cannot decrease"):
        store.update_order("order", status="partial", filled_quantity=0.4)
    with pytest.raises(LiveSafetyError, match="quote cost cannot decrease"):
        store.update_order("order", status="partial", quote_cost=49.0)
    with pytest.raises(LiveSafetyError, match="cannot decrease"):
        store.update_order(
            "order",
            status="partial",
            fees={"USDT": 0.04},
        )


def test_order_ledger_pruning_never_discards_an_uncertain_intent(tmp_path):
    store = LiveRiskStateStore(tmp_path / "risk.json")
    timestamp = datetime(2026, 8, 10, tzinfo=UTC).isoformat()
    for index in range(999):
        key = f"manual-{index}"
        store.state.reserved_orders[key] = timestamp
        store.state.order_ledger[key] = {"status": "manual_intervention"}
    store.state.reserved_orders["old-terminal"] = timestamp
    store.state.order_ledger["old-terminal"] = {"status": "filled"}
    store.reserve_order("new-intent")
    assert "old-terminal" not in store.state.order_ledger
    assert "manual-0" in store.state.order_ledger
    assert "new-intent" in store.state.order_ledger
    assert len(store.state.order_ledger) == 1000


def test_protective_order_state_preserves_active_during_replacement(tmp_path):
    state_path = tmp_path / "risk.json"
    store = LiveRiskStateStore(state_path)
    store.observe_position_risk(
        "BTC/USDT",
        quantity=0.1,
        observed_high=100.0,
        atr=5.0,
        atr_multiplier=2.0,
    )
    first = store.reserve_protective_order(
        "BTC/USDT",
        quantity=0.1,
        stop_price=90.0,
    )
    active = store.activate_pending_protective_order(
        "BTC/USDT",
        exchange_order_id="stop-1",
    )
    assert active["client_order_id"] == first["client_order_id"]

    second = store.reserve_protective_order(
        "BTC/USDT",
        quantity=0.1,
        stop_price=92.0,
    )
    replacing = LiveRiskStateStore(state_path).protective_order_state("BTC/USDT")
    assert replacing["active"]["exchange_order_id"] == "stop-1"
    assert replacing["pending"]["client_order_id"] == second["client_order_id"]
    assert second["client_order_id"] != first["client_order_id"]

    store.abandon_pending_protective_order("BTC/USDT")
    retained = store.protective_order_state("BTC/USDT")
    assert retained["active"]["exchange_order_id"] == "stop-1"
    assert retained["pending"] is None


def test_controlled_dust_is_signed_and_cleared_by_confirmed_protection(tmp_path):
    state_path = tmp_path / "risk.json"
    key = "dust-state-integrity-key-with-more-than-32-characters"
    store = LiveRiskStateStore(state_path, integrity_key=key)
    store.observe_position_risk(
        "BTC/USDT",
        quantity=0.04,
        observed_high=100.0,
        atr=5.0,
        atr_multiplier=2.0,
    )
    dust = store.mark_position_dust(
        "BTC/USDT",
        quantity=0.04,
        estimated_notional=4.0,
        reason="minimum_notional",
    )
    assert dust["status"] == "controlled_dust"
    reloaded = LiveRiskStateStore(state_path, integrity_key=key)
    assert reloaded.protective_order_state("BTC/USDT")["dust"][
        "estimated_notional"
    ] == pytest.approx(4.0)

    reloaded.reserve_protective_order(
        "BTC/USDT",
        quantity=0.04,
        stop_price=90.0,
    )
    reloaded.activate_pending_protective_order(
        "BTC/USDT",
        exchange_order_id="stop-1",
    )
    assert reloaded.protective_order_state("BTC/USDT")["dust"] is None


def test_dust_limit_is_hard_capped():
    assert (
        LiveRiskLimits.from_env({"LIVE_MAX_DUST_USD": "5"}).max_dust_notional_usd == 5
    )
    with pytest.raises(LiveSafetyError, match="LIVE_MAX_DUST_USD"):
        LiveRiskLimits.from_env({"LIVE_MAX_DUST_USD": "11"})


def test_mainnet_canary_profile_enforces_hard_caps():
    limits = LiveRiskLimits.for_profile(
        "mainnet-canary",
        {
            "LIVE_MAX_ORDER_USD": "1000",
            "LIVE_MAX_ORDER_EQUITY_PCT": "0.5",
            "LIVE_MAX_SYMBOL_PCT": "0.5",
            "LIVE_MAX_GROSS_EXPOSURE_PCT": "0.9",
            "LIVE_MIN_CASH_RESERVE_PCT": "0.1",
            "LIVE_MAX_DAILY_LOSS_PCT": "0.1",
            "LIVE_MAX_DRAWDOWN_PCT": "0.2",
        },
    )
    assert limits.max_order_notional_usd == pytest.approx(25.0)
    assert limits.max_order_equity_pct == pytest.approx(0.0025)
    assert limits.max_symbol_exposure_pct == pytest.approx(0.05)
    assert limits.max_gross_exposure_pct == pytest.approx(0.10)
    assert limits.min_cash_reserve_pct == pytest.approx(0.80)
    assert limits.max_daily_loss_pct == pytest.approx(0.005)
    assert limits.max_drawdown_pct == pytest.approx(0.02)
    with pytest.raises(LiveSafetyError, match="exceeds.*2.50"):
        validate_order_risk(
            side="BUY",
            notional_usd=3.0,
            equity=1_000.0,
            cash=1_000.0,
            current_symbol_notional=0.0,
            gross_exposure=0.0,
            limits=limits,
            locked_reason=None,
            risk_decision=_sample_risk_decision(),
            trusted_price=_trusted_price(),
        )


def test_signed_state_blocks_unapproved_risk_limit_increase(tmp_path):
    store = LiveRiskStateStore(tmp_path / "risk.json")
    canary = LiveRiskLimits.for_profile("mainnet-canary", {})
    normal = LiveRiskLimits.for_profile("mainnet-normal", {})
    initialized = store.bind_risk_limits(profile="mainnet-canary", limits=canary)
    assert initialized["previous_limits"] == {}
    with pytest.raises(LiveSafetyError, match="explicit confirmation"):
        store.bind_risk_limits(profile="mainnet-normal", limits=normal)
    changed = store.bind_risk_limits(
        profile="mainnet-normal",
        limits=normal,
        approve_increase=True,
    )
    assert changed["risk_increases"]
    assert store.state.risk_profile == "mainnet-normal"


def test_signed_state_detects_tampering_and_context_mismatch(tmp_path):
    state_path = tmp_path / "risk.json"
    key = "state-integrity-key-with-more-than-32-characters"
    store = LiveRiskStateStore(state_path, integrity_key=key)
    context = {
        "account": account_fingerprint(exchange="binance-mainnet", api_key="api-key-a"),
        "strategy": strategy_fingerprint(
            strategy="enhanced_ma",
            params={"fast": 20},
            allocations={"BTC/USDT": 0.2},
        ),
        "symbols": ["BTC/USDT"],
    }
    store.bind_context(**context)
    with pytest.raises(LiveSafetyError, match="different account"):
        store.bind_context(**{**context, "account": "different"})

    payload = json.loads(state_path.read_text(encoding="utf-8"))
    payload["peak_equity"] = 999_999
    state_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(LiveSafetyError, match="integrity check failed"):
        LiveRiskStateStore(state_path, integrity_key=key)


def test_local_audit_log_is_structured_and_durable(tmp_path):
    audit_path = tmp_path / "execution.jsonl"
    now = datetime(2026, 8, 10, tzinfo=UTC)
    append_live_audit_event(
        audit_path,
        "heartbeat",
        {"mode": "testnet"},
        now=now,
    )
    event = json.loads(audit_path.read_text(encoding="utf-8"))
    assert event["event"] == "heartbeat"
    assert event["timestamp"] == now.isoformat()
    assert event["details"] == {"mode": "testnet"}


def test_execution_lock_rejects_concurrent_runner(tmp_path):
    lock_path = tmp_path / "live.lock"
    with LiveExecutionLock(lock_path):
        with pytest.raises(LiveSafetyError, match="another live runner"):
            with LiveExecutionLock(lock_path):
                pass
    with LiveExecutionLock(lock_path):
        pass


def test_buy_order_limits_and_risk_reducing_sell():
    limits = LiveRiskLimits(max_order_notional_usd=100)
    with pytest.raises(LiveSafetyError, match="exceeds"):
        validate_order_risk(
            side="BUY",
            notional_usd=101,
            equity=1000,
            cash=800,
            current_symbol_notional=0,
            gross_exposure=0,
            limits=limits,
            locked_reason=None,
            risk_decision=_sample_risk_decision(),
            trusted_price=_trusted_price(),
        )
    validate_order_risk(
        side="SELL",
        notional_usd=200,
        equity=800,
        cash=100,
        current_symbol_notional=200,
        gross_exposure=700,
        limits=limits,
        locked_reason="daily loss breached",
        risk_decision=_sample_risk_decision(
            risk_level=RiskLevel.HIGH, max_new_exposure=0.0, reduce_only=True
        ),
    )
    with pytest.raises(LiveSafetyError, match="locked"):
        validate_order_risk(
            side="BUY",
            notional_usd=10,
            equity=800,
            cash=100,
            current_symbol_notional=0,
            gross_exposure=0,
            limits=limits,
            locked_reason="TRADING_ENTRY_KILL_SWITCH is active",
            risk_decision=_sample_risk_decision(),
            trusted_price=_trusted_price(),
        )


def test_stale_and_divergent_quotes_are_rejected():
    limits = LiveRiskLimits(max_quote_age_seconds=10, max_price_deviation_pct=0.01)
    now = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
    with pytest.raises(LiveSafetyError, match="stale"):
        validate_fresh_quote(
            signal_price=100,
            quote_price=100,
            quote_timestamp=now - timedelta(seconds=11),
            limits=limits,
            now=now,
        )
    with pytest.raises(LiveSafetyError, match="deviation"):
        validate_fresh_quote(
            signal_price=100,
            quote_price=102,
            quote_timestamp=now,
            limits=limits,
            now=now,
        )


def test_wide_spread_and_thin_order_book_are_rejected():
    limits = LiveRiskLimits(
        max_spread_pct=0.002,
        max_book_slippage_pct=0.003,
        min_book_depth_multiple=1.25,
    )
    with pytest.raises(LiveSafetyError, match="spread"):
        validate_spread(bid=100.0, ask=101.0, limits=limits)
    now = datetime(2026, 8, 10, tzinfo=UTC)
    with pytest.raises(LiveSafetyError, match="depth"):
        validate_order_book_depth(
            side="BUY",
            quantity=1.0,
            bids=[(99.9, 2.0)],
            asks=[(100.0, 1.1)],
            book_timestamp=now,
            limits=limits,
            now=now,
        )
    vwap = validate_order_book_depth(
        side="BUY",
        quantity=1.0,
        bids=[(99.9, 2.0)],
        asks=[(100.0, 0.5), (100.1, 1.0)],
        book_timestamp=now,
        limits=limits,
        now=now,
    )
    assert vwap == pytest.approx(100.05)


def test_client_order_id_is_stable_and_side_specific():
    candle = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    first = make_order_key(symbol="BTC/USDT", side="BUY", candle_timestamp=candle)
    second = make_order_key(symbol="BTC/USDT", side="BUY", candle_timestamp=candle)
    sell = make_order_key(symbol="BTC/USDT", side="SELL", candle_timestamp=candle)
    assert first == second
    assert first != sell
    assert len(first) <= 36


def _strategy_evidence(now: datetime) -> dict:
    first_start = now - timedelta(days=6 * 90)
    folds = []
    for index in range(6):
        start = first_start + timedelta(days=index * 90)
        end = start + timedelta(days=90)
        folds.append(
            {
                "start": start.isoformat(),
                "end": end.isoformat(),
                "bars": 90 * 24,
                "sharpe": 0.7,
                "return_pct": 1.0,
                "max_drawdown_pct": 5.0,
                "trades": 4,
            }
        )
    return {
        "version": 1,
        "strategy": "enhanced_ma",
        "strategy_params": {"fast_period": 20, "slow_period": 80, "adx_threshold": 40},
        "generated_at": now.isoformat(),
        "data_end": (now - timedelta(hours=1)).isoformat(),
        "allocations": {"BTC/USDT": 0.2},
        "costs": {"commission_bps": 10, "slippage_bps": 5, "spread_bps": 2},
        "symbols": {"BTC/USDT": {"allocation": 0.2, "folds": folds}},
        "portfolio": {"folds": [dict(fold) for fold in folds]},
    }


def test_strategy_evidence_must_pass_every_symbol(tmp_path):
    now = datetime(2026, 8, 10, tzinfo=UTC)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_strategy_evidence(now)), encoding="utf-8")
    summary = validate_strategy_evidence(
        evidence_path,
        expected_symbols=["BTC/USDT"],
        expected_params={"fast_period": 20, "slow_period": 80, "adx_threshold": 40},
        expected_allocations={"BTC/USDT": 0.2},
        now=now,
    )
    assert summary["BTC/USDT"]["median_sharpe"] == pytest.approx(0.7)


def test_strategy_evidence_rejects_negative_oos(tmp_path):
    now = datetime(2026, 8, 10, tzinfo=UTC)
    evidence = _strategy_evidence(now)
    for fold in evidence["symbols"]["BTC/USDT"]["folds"]:
        fold["sharpe"] = -0.1
        fold["return_pct"] = -1.0
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(LiveSafetyError, match="Sharpe"):
        validate_strategy_evidence(
            evidence_path,
            expected_symbols=["BTC/USDT"],
            expected_params=evidence["strategy_params"],
            expected_allocations={"BTC/USDT": 0.2},
            now=now,
        )


def test_strategy_evidence_rejects_stale_market_data(tmp_path):
    now = datetime(2026, 8, 10, tzinfo=UTC)
    evidence = _strategy_evidence(now)
    evidence["data_end"] = (now - timedelta(hours=7)).isoformat()
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(LiveSafetyError, match="data_end is stale"):
        validate_strategy_evidence(
            evidence_path,
            expected_symbols=["BTC/USDT"],
            expected_params=evidence["strategy_params"],
            expected_allocations={"BTC/USDT": 0.2},
            now=now,
        )


def test_strategy_evidence_rejects_live_allocation_mismatch(tmp_path):
    now = datetime(2026, 8, 10, tzinfo=UTC)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_strategy_evidence(now)), encoding="utf-8")
    with pytest.raises(LiveSafetyError, match="allocation does not match"):
        validate_strategy_evidence(
            evidence_path,
            expected_symbols=["BTC/USDT"],
            expected_params={"fast_period": 20, "slow_period": 80, "adx_threshold": 40},
            expected_allocations={"BTC/USDT": 0.1},
            now=now,
        )


def test_strategy_evidence_signature_and_build_are_bound(tmp_path):
    now = datetime(2026, 8, 10, tzinfo=UTC)
    key = "evidence-integrity-key-with-more-than-32-characters"
    evidence = _strategy_evidence(now)
    evidence["build_sha"] = "0123456789abcdef"
    signed = sign_strategy_evidence(evidence, key)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(signed), encoding="utf-8")
    validate_strategy_evidence(
        evidence_path,
        expected_symbols=["BTC/USDT"],
        expected_params=evidence["strategy_params"],
        expected_allocations={"BTC/USDT": 0.2},
        expected_build_sha="0123456789abcdef",
        integrity_key=key,
        now=now,
    )
    signed["symbols"]["BTC/USDT"]["folds"][0]["return_pct"] = 999
    evidence_path.write_text(json.dumps(signed), encoding="utf-8")
    with pytest.raises(LiveSafetyError, match="integrity check failed"):
        validate_strategy_evidence(
            evidence_path,
            expected_symbols=["BTC/USDT"],
            expected_params=evidence["strategy_params"],
            expected_allocations={"BTC/USDT": 0.2},
            expected_build_sha="0123456789abcdef",
            integrity_key=key,
            now=now,
        )
