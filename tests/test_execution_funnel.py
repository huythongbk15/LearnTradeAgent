"""Synthetic loop wiring/parity tests; fake broker, real simulator run loop."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import polars as pl
import pytest

from trading_agent.backtest.execution_funnel import export_funnel, isolate_funnel


def row(bar, terminal="no_signal", action="HOLD"):
    return dict(execution_bar=bar, decision_bar=bar - 1, action=action,
                terminal=terminal, runtime_called=False, orders=[],
                position_before=bar > 0, position_after=True)


def test_measurement_isolation_and_close_carry():
    records = [row(i) for i in range(6)]
    records[0]["orders"] = [{"order_id": "warmup-secret"}]
    trades = [dict(id="carry", reason="stop_loss", metadata={"simulation": {
        "entry_bar_index": 0, "exit_bar_index": 3}})]
    original = deepcopy(records)
    full = export_funnel(records, trades, start=0, end=6)
    measured = isolate_funnel(full, trades, start=2, end=4)
    assert [r["execution_bar"] for r in measured["records"]] == [2, 3]
    assert "warmup-secret" not in str(measured)
    assert measured["records"][0]["decision_precedes_scope"]
    assert measured["carry_in"] and measured["carry_out"]
    assert measured["closes"][0]["carry_in"]
    assert measured["close_reasons"] == {"stop_loss": 1}
    assert sum(measured["terminals"].values()) == measured["bars"] == 2
    assert records == original
    assert isolate_funnel(None, [], start=0, end=1) is None


def run_loop(monkeypatch, *, enabled, signals, alerts=None, blocked=False, stop=False, freq=1):
    from scripts.full_system_backtest import FullSystemSimulator

    count = len(signals) + 1
    position = SimpleNamespace(is_active=False, quantity=0.0)
    fills, closes = [], []
    equity = [10000.0]

    def update(prices):
        if stop and list(prices.values())[0] < 95 and position.is_active:
            position.is_active, position.quantity = False, 0.0
            closes.append(dict(id="stop", reason="stop_loss", metadata={"simulation": {
                "entry_bar_index": 1, "exit_bar_index": 2}}))
            equity[0] -= 5

    exchange = SimpleNamespace(get_position=lambda symbol: position,
        get_total_equity=lambda: equity[0], update_prices=update, flush_state=lambda: None)

    def execute(runtime, data, observation):
        if blocked:
            engine.last_strategy_execution = {"result": "exposure_blocked", "authority_block_reason": "fixture_cap"}
            return []
        side = "buy" if signals[data.height - 1] > 0 else "sell"
        position.is_active, position.quantity = side == "buy", float(side == "buy")
        fills.append((side, data.height, float(data['close'][-1])))
        engine.last_strategy_execution = {"result": "submitted"}
        if side == "sell":
            closes.append(dict(id="close", reason="signal", metadata={"simulation": {
                "entry_bar_index": 1, "exit_bar_index": data.height}}))
        return [SimpleNamespace(id=f"order-{len(fills)}", side=SimpleNamespace(value=side),
                                status=SimpleNamespace(value="filled"), filled_amount=1.0, amount=1.0)]

    engine = SimpleNamespace(exchange=exchange, update_prices=update, execute_strategy=execute,
                             last_strategy_execution={})
    sim = FullSystemSimulator.__new__(FullSystemSimulator)
    sim.df = pl.DataFrame({"timestamp": [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(count)],
        "open": [100.0] * count, "high": [101.0] * count,
        "low": [90.0 if stop and i == 2 else 100.0 for i in range(count)],
        "close": [100.0] * count, "volume": [1.0] * count})
    sim.signals = signals + [0]
    sim.symbol, sim.timeframe, sim.exchange = "BTC/USDT", "1h", "binance"
    sim.timeframe_delta = timedelta(hours=1)
    sim.gap_policy, sim.gap_exceptions = "record", ()
    sim.engine, sim.adaptive_enabled = engine, False
    risk_alerts = iter(alerts or [[] for _ in range(count)])
    sim.risk = SimpleNamespace(check_all=lambda: next(risk_alerts))
    sim._breaker_active = False
    sim.strategy_runtime = SimpleNamespace(strategy_name="fixture")
    sim.strategy_name = "fixture"
    sim.data_manifest_id, sim.feature_artifact_id = "d" * 64, "f" * 64
    sim._funnel_records = [] if enabled else None
    sim.signal_log, sim.trade_log, sim.equity_curve, sim.circuit_breakers = [], [], [], []
    sim._entry_state = {}
    sim._update_position_excursion = lambda *a, **k: None
    sim._stamp_new_trades = lambda *a, **k: None
    sim._attach_entry_context = lambda *a, **k: None
    sim._report = lambda: {
        "fills": fills, "closes": closes, "equity": sim.equity_curve,
        "funnel": export_funnel(sim._funnel_records, closes, start=0, end=count) if enabled else None}
    return sim.run(freq=freq)


@pytest.mark.parametrize("kwargs, expected", [
    ({"signals": [1, 1, -1, -1, 0]}, {"already_long", "flat_exit", "no_signal", "runtime_submitted"}),
    ({"signals": [1, 1, -1], "blocked": True}, {"runtime_exposure_blocked", "flat_exit"}),
    ({"signals": [1, 1, -1], "alerts": [[], ["Cooldown"], [], []]}, {"cooldown"}),
    ({"signals": [1, 1, -1], "alerts": [[], ["CIRCUIT BREAKER ACTIVE"], [], []]}, {"circuit_breaker"}),
    ({"signals": [1, 1, -1], "freq": 2}, {"cadence"}),
    ({"signals": [1, 0, -1], "stop": True}, {"flat_exit"}),
])
def test_real_loop_observability_is_passive(monkeypatch, kwargs, expected):
    on = run_loop(monkeypatch, enabled=True, **kwargs)
    off = run_loop(monkeypatch, enabled=False, **kwargs)
    assert {k: v for k, v in on.items() if k != "funnel"} == {k: v for k, v in off.items() if k != "funnel"}
    funnel = on["funnel"]
    assert expected <= set(funnel["terminals"])
    assert sum(funnel["terminals"].values()) == funnel["bars"]
    assert sum(funnel["actions"].values()) == funnel["bars"]
    if kwargs.get("stop"):
        assert funnel["close_reasons"] == {"stop_loss": 1}
    if kwargs.get("blocked"):
        assert any(r.get("authority_reason") == "fixture_cap" for r in funnel["records"])
    if not kwargs.get("blocked"):
        assert on["fills"], "parity cannot pass solely on an empty trading scenario"


def test_real_paper_engine_parity(tmp_path):
    """Real authority/lifecycle/paper engine; injected synthetic OHLCV only."""
    import math
    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.backtest.tournament import (
        EvaluationCellSpec, _bridge_runtime_factory, canonical_signal_series,
    )
    from trading_agent.strategies.canonical.candidates import build_parameterized_adapter

    count = 270
    prices = [100 + 4 * math.sin(i / 6) for i in range(count)]
    frame = pl.DataFrame({
        "timestamp": [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(count)],
        "open": prices, "close": prices, "high": [p * 1.001 for p in prices],
        "low": [p * .999 for p in prices], "volume": [100.0] * count})
    params = {"fast_period": 3, "slow_period": 9}
    descriptor, adapter = build_parameterized_adapter("ma_vol_target", params)
    signals = canonical_signal_series(adapter, frame.rename({"timestamp": "time"}),
        warmup_bars=descriptor.warmup_bars, symbol="BTC/USDT")
    assert any(signals)
    spec = EvaluationCellSpec("ma_vol_target", "BTC/USDT", "1h", params)
    reports = []
    for enabled in (True, False):
        sim = FullSystemSimulator(
            symbol="BTC/USDT", timeframe="1h", source_df_override=frame,
            data_manifest_id="d" * 64, strategy_name="ma_vol_target",
            strategy_params_override=params, signal_series=signals,
            runtime_factory=_bridge_runtime_factory(spec, adapter, descriptor),
            state_dir=tmp_path / str(enabled) / "execution",
            report_path=tmp_path / str(enabled) / "report.json",
            funnel_enabled=enabled,
        )
        from scripts.full_system_backtest import describe_execution_contract
        declaration = describe_execution_contract(symbol="BTC/USDT", timeframe="1h",
            strategy_name="ma_vol_target", parameters=params,
            commission=sim.engine.exchange.commission, slippage=sim.engine.exchange.slippage)
        assert declaration == sim._active_config()["execution_contract"]
        sim.expected_execution_contract_id = declaration["contract_id"]
        reports.append(sim.run())
    on, off = reports
    from trading_agent.backtest.execution_contract import require_execution_contract
    contract = on["active_config"]["execution_contract"]
    require_execution_contract(contract["contract_id"], off["active_config"]["execution_contract"])
    assert on["total_trades"] > 0
    assert on["metrics"] == off["metrics"]
    assert on["equity_curve"] == off["equity_curve"]
    def economics(report):
        return [(t["side"], t["entry_price"], t["exit_price"], t["quantity"], t["pnl"]) for t in report["trades"]]
    assert economics(on) == economics(off)
    assert off["execution_funnel"] is None
    assert on["execution_funnel"]["closed_trades"] == on["total_trades"]
    assert on["execution_funnel"]["returned_orders"] > 0
    assert all(o["intent_ids"] for r in on["execution_funnel"]["records"] for o in r["orders"])


def test_tournament_exports_only_measured_funnel(tmp_path, monkeypatch):
    import json
    import math
    import scripts.full_system_backtest as simulator
    from trading_agent.backtest import tournament

    count = 270
    prices = [100 + 4 * math.sin(i / 6) for i in range(count)]
    frame = pl.DataFrame({
        "timestamp": [datetime(2024, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(count)],
        "open": prices, "close": prices, "high": [p * 1.001 for p in prices],
        "low": [p * .999 for p in prices], "volume": [100.0] * count})
    loads = []

    def synthetic_loader(*args, **kwargs):
        loads.append(args)
        return frame

    monkeypatch.setattr(tournament, "load_ohlcv", synthetic_loader)
    monkeypatch.setattr(simulator, "load_ohlcv", lambda *a, **k: pytest.fail("unexpected real loader"))
    spec = tournament.EvaluationCellSpec("ma_vol_target", "BTC/USDT", "1h",
                                         {"fast_period": 3, "slow_period": 9})
    tournament.run_cell(spec, out_root=tmp_path, start=0, end=count,
        measurement_start=210, measurement_end=count, _use_multiprocessing=False)
    assert len(loads) == 1
    reports = list(tmp_path.rglob("report.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    funnel = report["execution_funnel"]
    assert funnel["window"] == [210, count]
    assert funnel["bars"] == count - 210
    assert all(210 <= r["execution_bar"] < count for r in funnel["records"])
    assert sum(funnel["terminals"].values()) == count - 210
    assert report["signals_seen"] == 0  # legacy field remains unchanged
    assert funnel["runtime_calls"] > 0
    assert funnel["closed_trades"] == report["total_trades"] > 0
