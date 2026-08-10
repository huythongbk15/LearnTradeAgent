from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest

from trading_agent.execution.live_safety import (
    LIVE_CONFIRMATION,
    DuplicateOrderError,
    LiveRiskLimits,
    LiveRiskStateStore,
    LiveSafetyError,
    make_order_key,
    require_execution_authorization,
    validate_fresh_quote,
    validate_order_risk,
    validate_strategy_evidence,
)


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


def test_client_order_id_is_stable_and_side_specific():
    candle = datetime(2026, 8, 10, 10, 0, tzinfo=UTC)
    first = make_order_key(symbol="BTC/USDT", side="BUY", candle_timestamp=candle)
    second = make_order_key(symbol="BTC/USDT", side="BUY", candle_timestamp=candle)
    sell = make_order_key(symbol="BTC/USDT", side="SELL", candle_timestamp=candle)
    assert first == second
    assert first != sell
    assert len(first) <= 36


def _strategy_evidence(now: datetime) -> dict:
    folds = [
        {"sharpe": 0.7, "return_pct": 1.0, "max_drawdown_pct": 5.0, "trades": 4}
        for _ in range(6)
    ]
    return {
        "version": 1,
        "strategy": "enhanced_ma",
        "strategy_params": {"fast_period": 20, "slow_period": 80, "adx_threshold": 40},
        "generated_at": now.isoformat(),
        "data_end": (now - timedelta(days=1)).isoformat(),
        "costs": {"commission_bps": 10, "slippage_bps": 5},
        "symbols": {"BTC/USDT": {"folds": folds}},
    }


def test_strategy_evidence_must_pass_every_symbol(tmp_path):
    now = datetime(2026, 8, 10, tzinfo=UTC)
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_strategy_evidence(now)), encoding="utf-8")
    summary = validate_strategy_evidence(
        evidence_path,
        expected_symbols=["BTC/USDT"],
        expected_params={"fast_period": 20, "slow_period": 80, "adx_threshold": 40},
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
            now=now,
        )
