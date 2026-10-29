"""Real simulator integration on isolated synthetic frames, no market inputs."""
from datetime import UTC, datetime, timedelta

import pytest

from trading_agent.backtest.fill_cycle_synthetic import synthetic_frame
from trading_agent.strategies.own24_thinned_fill import Own24ThinnedFillStrategy, accepts_entry


def build_simulator(tmp_path, numerator, denominator):
    from scripts.full_system_backtest import FullSystemSimulator
    return FullSystemSimulator(
        fresh=True, symbol="ETH/USDT", timeframe="1h",
        state_dir=tmp_path / "execution", report_path=tmp_path / "report.json",
        run_id="synthetic-thinning", data_manifest_id="synthetic-thinning",
        gap_policy="reject", source_df_override=synthetic_frame(),
        execution_cycle_strategy=Own24ThinnedFillStrategy(
            pair="ETH/USDT", seed=42, numerator=numerator, denominator=denominator),
        execution_cycle_synthetic_only=True,
    )


@pytest.mark.parametrize("numerator,denominator", [(1, 1), (1, 2), (0, 1)])
def test_real_fills_hash_timing_expiry_and_accounting(tmp_path, numerator, denominator):
    sim = build_simulator(tmp_path, numerator, denominator)
    try:
        report = sim.run(start=240, end=280)
        trades = report["trades"]
        if numerator:
            assert len(trades) == 1, "nonzero fill required; no vacuous accounting pass"
            trade = trades[0]
            assert trade["entry_order_id"]
            assert trade["reason"] == "signal"
            assert (datetime.fromisoformat(trade["exit_time"])
                    - datetime.fromisoformat(trade["entry_time"])) == timedelta(hours=24)
        else:
            assert not trades
        buys = [r for r in report["execution_funnel"]["records"] if r["action"] == "BUY"]
        if numerator:
            assert buys
        else:
            assert not buys
        for record in buys:
            signal_bar = record["execution_bar"] - 2
            stamp = sim.df["timestamp"][signal_bar]
            assert accepts_entry(42, "ETH/USDT", stamp, numerator, denominator)
        assert report["open_positions"] == 0 and report["open_orders"] == 0
        assert report["measured_accounting"] is not None
        assert abs(report["cost_attribution"]["reconciliation_error"]) < 1e-6
    finally:
        sim.engine.store.close()


def test_control_cannot_bypass_native_cooldown(tmp_path):
    sim = build_simulator(tmp_path, 1, 1)
    sim.risk._cooldown_until = sim.df["timestamp"][240].replace(tzinfo=UTC) + timedelta(hours=40)
    try:
        report = sim.run(start=240, end=280)
        assert report["total_trades"] == 0
        assert report["execution_funnel"]["actions"].get("BUY", 0) == 0
        assert report["open_positions"] == 0 and report["open_orders"] == 0
    finally:
        sim.engine.store.close()


@pytest.mark.parametrize("emergency_close_fails", [False, True])
def test_control_preserves_native_breaker_and_reducing_exit(tmp_path, monkeypatch, emergency_close_fails):
    sim = build_simulator(tmp_path, 1, 1)
    original = sim.risk.check_all
    triggered = []
    if emergency_close_fails:
        monkeypatch.setattr(sim.engine, "close_all", lambda reason: [])
    def check():
        if not triggered and sim.engine.exchange.get_position(sim.symbol) is not None:
            equity = sim.engine.exchange.get_total_equity()
            sim.risk._peak_equity = equity / (1 - sim.risk.max_drawdown_pct * 1.01)
            triggered.append(True)
        return original()
    monkeypatch.setattr(sim.risk, "check_all", check)
    try:
        report = sim.run(start=240, end=280)
        assert triggered and report["total_trades"] == 1
        trade = report["trades"][0]
        assert trade["reason"] == ("signal" if emergency_close_fails else "circuit_breaker")
        if emergency_close_fails:
            assert (datetime.fromisoformat(trade["exit_time"])
                    - datetime.fromisoformat(trade["entry_time"])) == timedelta(hours=24)
        buys = [r for r in report["execution_funnel"]["records"] if r["action"] == "BUY"]
        assert len(buys) == 1
        assert report["open_positions"] == 0 and report["open_orders"] == 0
    finally:
        sim.engine.store.close()
