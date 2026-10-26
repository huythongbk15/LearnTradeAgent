"""Injected indicator routing; no claim of OHLCV realizability or market alpha."""
import json
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.backtest.tournament import EvaluationCellSpec, SCENARIO_BASE, _bridge_runtime_factory, canonical_signal_series
from trading_agent.strategies.canonical.candidates import build_parameterized_adapter
from trading_agent.strategies.rsi_adx_regime_filter import RsiAdxRegimeFilterStrategy


@pytest.mark.parametrize('case', ['range_to_trend', 'trend_to_range', 'switch_no_exit', 'range_control', 'trend_control', 'stop_on_switch'])
def test_branch_lifecycle_native(tmp_path, monkeypatch, case):
    from scripts.full_system_backtest import FullSystemSimulator
    times = [datetime(2020, 1, 1, tzinfo=UTC) + timedelta(hours=i) for i in range(155)]
    starts_range = case not in {'trend_to_range', 'trend_control'}
    switches = case not in {'range_control', 'trend_control'}
    rows = []
    for i, time in enumerate(times):
        is_range = starts_range if i < 130 or not switches else not starts_range
        rsi = 20.0 if starts_range and i == 120 else 50.0
        if case in {'trend_to_range', 'range_control'} and i == 130:
            rsi = 80.0
        fast = 101.0 if (not starts_range and i >= 120) or (starts_range and i < 130) else 99.0
        if case == 'trend_control' and i >= 130:
            fast = 99.0
        if case in {'switch_no_exit', 'stop_on_switch'}:
            fast = 101.0
        rows.append({'time': time, 'rsi': rsi, '_adx': 10.0 if is_range else 30.0,
                     '_ma_fast': fast, '_ma_slow': 100.0})
    indicators = pl.DataFrame(rows)
    def injected_indicators(self, df):
        key = 'time' if 'time' in df.columns else 'timestamp'
        values = indicators if key == 'time' else indicators.rename({'time': key})
        return df.join(values, on=key, how='left')
    monkeypatch.setattr(RsiAdxRegimeFilterStrategy, 'compute_indicators', injected_indicators)
    frame = pl.DataFrame({'time': times, 'open': [100.0]*155, 'close': [100.0]*155,
                          'high': [100.2]*155,
                          'low': [90.0 if case == 'stop_on_switch' and i == 132 else 99.8 for i in range(155)],
                          'volume': [1000.0]*155})
    strategy = RsiAdxRegimeFilterStrategy()
    full = strategy.generate_signals(strategy.compute_indicators(frame)).to_list()
    exits = case not in {'switch_no_exit', 'stop_on_switch'}
    assert full[120] == 1
    assert full[130] == (-1 if exits else 0)
    descriptor, adapter = build_parameterized_adapter('rsi_adx_regime_filter', {})
    canonical = canonical_signal_series(adapter, frame, warmup_bars=descriptor.warmup_bars,
                                        symbol='BTC/USDT', end_index=154)
    assert canonical[121] == 1
    assert canonical[131] == (-1 if exits else 0)
    spec = EvaluationCellSpec(strategy_id='rsi_adx_regime_filter', symbol='BTC/USDT', timeframe='1h', params={}, cost_scenario=SCENARIO_BASE)
    sim = FullSystemSimulator(symbol='BTC/USDT', timeframe='1h', strategy_name='rsi_adx_regime_filter',
        source_df_override=frame.rename({'time': 'timestamp'}), data_manifest_id='d'*64,
        signal_series=canonical, runtime_factory=_bridge_runtime_factory(spec, adapter, descriptor),
        state_dir=tmp_path/'execution', report_path=tmp_path/'report.json', funnel_enabled=True)
    report = sim.run()
    actions = [r for r in report['execution_funnel']['records'] if r.get('action') in {'BUY', 'SELL'}]
    assert any(r['action'] == 'BUY' and r['execution_bar'] == 122 for r in actions)
    if exits:
        assert len(report['trades']) == 1
        trade = report['trades'][0]
        assert trade['reason'] == 'signal'
        assert datetime.fromisoformat(trade['entry_time']) == times[122]
        assert datetime.fromisoformat(trade['exit_time']) == times[132]
        assert trade['entry_price'] == pytest.approx(100*1.0005)
        assert trade['exit_price'] == pytest.approx(100*.9995)
    elif case == 'stop_on_switch':
        assert len(report['trades']) == 1
        assert report['trades'][0]['reason'] == 'stop_loss'
    else:
        assert not report['trades']
        assert not any(r['action'] == 'SELL' for r in actions)
        assert report['measured_accounting']['accounting_basis'] != 'closed_trades_only'
    assert report.get('measured_accounting_error') is None
    (tmp_path/'branch_trace.json').write_text(json.dumps({'case': case, 'indicator_injection': True,
        'entry_branch': 'RSI' if starts_range else 'MA', 'switches': switches,
        'actions': actions, 'trades': report['trades']}, indent=2, default=str))
