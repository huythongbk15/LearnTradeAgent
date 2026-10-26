"""Admission only: no market input, parameter selection or qualification."""
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from trading_agent.strategies.trend_pullback import TrendPullbackStrategy


def test_signal_contract_and_exit_not_blocked_by_entry_filters():
    # Indicator fixture isolates each documented Boolean entry condition.
    strategy = TrendPullbackStrategy()
    frame = pl.DataFrame({'close': [99., 101.], 'ma_fast': [100., 100.],
        'ma_slow': [95., 95.], 'adx': [30., 30.], 'volume': [1000., 3000.],
        'vol_sma': [1000., 1000.]})
    assert strategy.generate_signals(frame).to_list() == [-1, 1]
    for col, value in [('ma_slow', 102.), ('adx', 20.), ('volume', 1000.)]:
        changed = frame.with_columns(pl.lit(value).alias(col))
        assert strategy.generate_signals(changed)[-1] != 1
    # Weak ADX/volume must not veto an exit once price is below the fast MA.
    exit_frame = frame.with_columns(pl.lit(90.).alias('close'),
        pl.lit(0.).alias('adx'), pl.lit(0.).alias('volume'))
    assert strategy.generate_signals(exit_frame).to_list() == [-1, -1]


def _frame():
    prices = [100 + .2*i for i in range(190)]
    prices[140] = 124.
    for i in range(153, 190):
        prices[i] = 127. - .2*(i-153)
    return pl.DataFrame({'time': [datetime(2020, 1, 1, tzinfo=UTC)+timedelta(hours=i) for i in range(190)],
        'open': prices, 'close': prices, 'high': [p+.2 for p in prices],
        'low': [p-.2 for p in prices], 'volume': [5000. if i == 141 else 1000. for i in range(190)]})


def test_actual_indicator_fixture_is_causal_and_nonvacuous():
    strategy = TrendPullbackStrategy()
    frame = _frame()
    signals = strategy.generate_signals(strategy.compute_indicators(frame)).to_list()
    assert signals[141] == 1
    assert signals[153] == -1
    assert signals.count(1) == 1
    # A radically different suffix cannot change the earlier entry.
    changed = frame.with_row_index('i').with_columns(
        pl.when(pl.col('i') > 150).then(900.).otherwise(pl.col('close')).alias('close'))
    changed_signals = strategy.generate_signals(strategy.compute_indicators(changed)).to_list()
    assert changed_signals[:151] == signals[:151]


def test_canonical_native_trade_and_two_bar_timing(tmp_path):
    from scripts.full_system_backtest import FullSystemSimulator
    from trading_agent.backtest.tournament import EvaluationCellSpec, SCENARIO_BASE, _bridge_runtime_factory, canonical_signal_series
    from trading_agent.strategies.canonical.candidates import build_parameterized_adapter

    frame = _frame()
    strategy = TrendPullbackStrategy()
    full = strategy.generate_signals(strategy.compute_indicators(frame)).to_list()
    descriptor, adapter = build_parameterized_adapter('trend_pullback', {})
    canonical = canonical_signal_series(adapter, frame, warmup_bars=descriptor.warmup_bars,
                                        symbol='BTC/USDT', end_index=189)
    for j in range(descriptor.warmup_bars+1, 189):
        assert canonical[j] == full[j-1]
    assert canonical[142] == 1
    spec = EvaluationCellSpec(strategy_id='trend_pullback', symbol='BTC/USDT',
                              timeframe='1h', params={}, cost_scenario=SCENARIO_BASE)
    report = FullSystemSimulator(symbol='BTC/USDT', timeframe='1h', strategy_name='trend_pullback',
        source_df_override=frame.rename({'time': 'timestamp'}), data_manifest_id='d'*64,
        signal_series=canonical, runtime_factory=_bridge_runtime_factory(spec, adapter, descriptor),
        state_dir=tmp_path/'execution', report_path=tmp_path/'report.json', funnel_enabled=True).run()
    assert len(report['trades']) == 1, 'must have a real closed trade'
    trade = report['trades'][0]
    assert trade['reason'] == 'signal'
    assert datetime.fromisoformat(trade['entry_time']) == frame['time'][143]
    assert datetime.fromisoformat(trade['exit_time']) == frame['time'][155]
    assert trade['entry_price'] == pytest.approx(frame['open'][143]*1.0005, rel=1e-8)
    assert trade['exit_price'] == pytest.approx(frame['open'][155]*.9995, rel=1e-8)
    actions = [r for r in report['execution_funnel']['records'] if r.get('action') in {'BUY', 'SELL'}]
    assert any(r['action'] == 'BUY' and r['execution_bar'] == 143 for r in actions)
    for r in actions:
        assert full[r['execution_bar']-2] == {'BUY': 1, 'SELL': -1}[r['action']]
    assert report.get('measured_accounting_error') is None
    assert report['active_config']['protection']['stop_loss']['enabled'] is True


def test_flat_prices_have_no_buy_and_no_future_state():
    frame = _frame().with_columns(pl.lit(100.).alias('open'), pl.lit(100.).alias('close'),
                                 pl.lit(100.).alias('high'), pl.lit(100.).alias('low'))
    strategy = TrendPullbackStrategy()
    values = strategy.compute_indicators(frame)
    assert not values['adx'].tail(100).is_nan().any()
    assert 1 not in strategy.generate_signals(values).to_list()
