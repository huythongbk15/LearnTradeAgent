"""Synthetic regressions for fill-cycle entry admission during native risk halts."""
from datetime import UTC, timedelta

from trading_agent.backtest.fill_cycle_synthetic import synthetic_frame
from trading_agent.strategies.own24_fill_context import Own24FillContextStrategy


def simulator(tmp_path):
    from scripts.full_system_backtest import FullSystemSimulator

    return FullSystemSimulator(
        fresh=True, symbol="ETH/USDT", timeframe="1h", state_dir=tmp_path / "execution",
        report_path=tmp_path / "report.json", run_id="synthetic-risk-halt",
        data_manifest_id="synthetic-risk-halt", gap_policy="reject",
        source_df_override=synthetic_frame(),
        execution_cycle_strategy=Own24FillContextStrategy({"arm": "A0"}),
        execution_cycle_synthetic_only=True,
    )


def test_fill_cycle_never_reenters_on_native_breaker_activation(tmp_path, monkeypatch):
    sim = simulator(tmp_path)
    original = sim.risk.check_all
    triggered = []

    def check():
        if not triggered and sim.engine.exchange.get_position(sim.symbol) is not None:
            equity = sim.engine.exchange.get_total_equity()
            sim.risk._peak_equity = equity / (1 - sim.risk.max_drawdown_pct * 1.01)
            triggered.append(True)
        return original()

    monkeypatch.setattr(sim.risk, "check_all", check)
    try:
        report = sim.run(start=240, end=280)
        assert triggered
        assert report["total_trades"] == 1
        trade = report["trades"][0]
        assert trade["reason"] == "circuit_breaker"
        assert trade["exit_time"] == "2024-01-11T02:00:00+00:00"
        buys = [r for r in report["execution_funnel"]["records"] if r["action"] == "BUY"]
        assert [r["execution_bar"] for r in buys] == [241]
        assert report["open_positions"] == 0 and report["open_orders"] == 0
    finally:
        sim.engine.store.close()


def test_fill_cycle_respects_native_cooldown_while_flat(tmp_path):
    sim = simulator(tmp_path)
    sim.risk._cooldown_until = sim.df["timestamp"][240].replace(tzinfo=UTC) + timedelta(hours=40)
    try:
        report = sim.run(start=240, end=280)
        assert report["total_trades"] == 0
        assert report["execution_funnel"]["actions"].get("BUY", 0) == 0
        assert report["open_positions"] == 0 and report["open_orders"] == 0
    finally:
        sim.engine.store.close()


def test_expired_position_can_still_reduce_during_native_halt(tmp_path, monkeypatch):
    sim = simulator(tmp_path)
    original = sim.risk.check_all
    triggered = []
    # Inject a failed emergency close; the surviving position must retain its
    # normal expiry exit even though new exposure is forbidden.
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
        assert trade["reason"] == "signal"
        assert trade["entry_time"] == "2024-01-11T01:00:00+00:00"
        assert trade["exit_time"] == "2024-01-12T01:00:00+00:00"
        assert report["open_positions"] == 0 and report["open_orders"] == 0
    finally:
        sim.engine.store.close()
