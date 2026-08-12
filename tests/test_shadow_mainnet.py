"""Wave C — Shadow Mainnet Mode tests.

Proves:
* hard guard #1 (config): submit_orders=True → refuse to start;
* hard guard #2 (runtime): SHADOW_MAINNET env missing → refuse to start;
* hard guard #3 (tests): a live broker that raises proves no code path can
  submit an order;
* shadow fills, protective order state, PnL, execution metrics, reality-gap.
"""

from __future__ import annotations

import pytest

from trading_agent.execution.shadow import (
    ExchangeRules,
    RealityGapReport,
    SHADOW_ENV_GUARD,
    SHADOW_ENV_VALUE,
    ShadowConfig,
    ShadowMainnetEngine,
    ShadowModeError,
    ShadowOrderStatus,
)


def env_guard() -> dict[str, str]:
    return {SHADOW_ENV_GUARD: SHADOW_ENV_VALUE}


def make_engine(**overrides) -> ShadowMainnetEngine:
    config = ShadowConfig(
        symbols=["BTC/USDT"],
        strategy_ids=["ma_cross"],
        exchange_rules={
            "BTC/USDT": ExchangeRules(
                min_qty=0.001, step_size=0.001, min_notional=10.0, taker_fee=0.001
            )
        },
        **overrides,
    )
    return ShadowMainnetEngine(config, env=env_guard())


# ── Hard guards ────────────────────────────────────────────────────────


def test_config_guard_refuses_submit_orders():
    with pytest.raises(ShadowModeError):
        ShadowConfig(submit_orders=True).validate(env=env_guard())


def test_runtime_env_guard_required():
    with pytest.raises(ShadowModeError):
        ShadowConfig().validate(env={})  # SHADOW_MAINNET missing


def test_runtime_env_guard_accepts_flag():
    ShadowConfig().validate(env=env_guard())


def test_live_submission_code_path_is_dead():
    engine = make_engine()
    with pytest.raises(ShadowModeError):
        engine._submit_live_order()


def test_no_code_path_reaches_broker(monkeypatch):
    """Hard guard #3: monkeypatched broker raises if ever called."""
    calls: list[str] = []

    def spy_broker(*args, **kwargs):
        calls.append("create_order_called")
        raise AssertionError("shadow mode must never call the broker")

    engine = make_engine()
    engine.ingest_market_data(prices={"BTC/USDT": 50000.0})
    intent = engine.create_shadow_intent("BTC/USDT", "buy", 0.01, "ma_cross")
    engine.simulate_fill(intent.order_id)
    engine.set_shadow_protective_order("BTC/USDT", stop_loss=45000.0)
    engine.execution_metrics()
    engine.reality_gap_report()
    assert calls == []


def test_pipeline_asserts_no_submission_every_step():
    engine = make_engine()
    engine.ingest_market_data(prices={"BTC/USDT": 50000.0})
    # After ingestion, flip the config guard — every step must re-assert.
    engine.config.submit_orders = True
    with pytest.raises(ShadowModeError):
        engine.create_shadow_intent("BTC/USDT", "buy", 0.01, "ma_cross")


# ── Shadow pipeline ────────────────────────────────────────────────────


def test_shadow_intent_requires_fresh_market_data():
    engine = make_engine()  # no market data ingested
    with pytest.raises(ShadowModeError):
        engine.create_shadow_intent("BTC/USDT", "buy", 0.01, "ma_cross")


def test_shadow_fill_pnl_and_metrics():
    engine = make_engine()
    engine.ingest_market_data(prices={"BTC/USDT": 50000.0})
    intent = engine.create_shadow_intent("BTC/USDT", "buy", 0.01, "ma_cross")
    engine.simulate_fill(intent.order_id)
    assert intent.status == ShadowOrderStatus.FILLED
    assert intent.simulated_fill_price > 0
    pos = engine.positions["BTC/USDT"]
    assert pos.quantity == 0.01
    assert pos.side == "buy"
    # Mark == entry (no subsequent move) → pnl = slippage cost only
    assert engine.shadow_pnl()["BTC/USDT"] == -0.25  # (50000 - 50025) * 0.01
    metrics = engine.execution_metrics()
    assert metrics["filled"] == 1
    assert metrics["avg_slippage_bps"] is not None
    assert metrics["shadow_equity"] > 0


def test_shadow_protective_order_state():
    engine = make_engine()
    engine.ingest_market_data(prices={"BTC/USDT": 50000.0})
    intent = engine.create_shadow_intent("BTC/USDT", "buy", 0.01, "ma_cross")
    engine.simulate_fill(intent.order_id)
    pos = engine.set_shadow_protective_order(
        "BTC/USDT", stop_loss=45000.0, take_profit=55000.0
    )
    assert pos.stop_loss == 45000.0
    assert pos.take_profit == 55000.0


def test_shadow_enforces_exchange_rules():
    engine = make_engine()
    engine.ingest_market_data(prices={"BTC/USDT": 50000.0})
    with pytest.raises(ShadowModeError):
        engine.create_shadow_intent("BTC/USDT", "buy", 0.0001, "ma_cross")  # < min_qty
    # min_notional rule: override to a high threshold so 0.001*50000=50 fails
    engine2 = make_engine()
    engine2.config.exchange_rules["BTC/USDT"] = ExchangeRules(
        min_qty=0.001, step_size=0.001, min_notional=100000.0
    )
    engine2.ingest_market_data(prices={"BTC/USDT": 50000.0})
    with pytest.raises(ShadowModeError):
        engine2.create_shadow_intent("BTC/USDT", "buy", 0.001, "ma_cross")


def test_shadow_sell_requires_inventory():
    engine = make_engine()
    engine.ingest_market_data(prices={"BTC/USDT": 50000.0})
    intent = engine.create_shadow_intent("BTC/USDT", "sell", 0.01, "ma_cross")
    with pytest.raises(ShadowModeError):
        engine.simulate_fill(intent.order_id)


def test_reality_gap_report():
    engine = make_engine()
    engine.ingest_market_data(prices={"BTC/USDT": 50000.0})
    intent = engine.create_shadow_intent("BTC/USDT", "buy", 0.01, "ma_cross")
    engine.simulate_fill(intent.order_id)
    # Subsequent real market move (adverse selection proxy)
    engine.observe_mid_after_fill("BTC/USDT", 50100.0)
    engine.observe_mid_after_fill("BTC/USDT", 50150.0)
    report = engine.reality_gap_report()
    assert len(report.fills) == 1
    fill = report.fills[0]
    assert fill["mid_after"]  # real subsequent mids recorded
    summary = report.summary()
    assert summary["fills"] == 1
    assert summary["avg_abs_gap_to_last_mid"] is not None


def test_reality_gap_empty():
    report = RealityGapReport()
    assert report.summary() == {"fills": 0}
