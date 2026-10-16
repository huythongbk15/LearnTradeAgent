"""Synthetic history and funding safety proofs, not market qualification."""
import math
from datetime import UTC, datetime

import polars as pl
import pytest

from trading_agent.strategies.canonical.candidates import (
    FIRST_WAVE_DESCRIPTORS,
    build_legacy_candidate,
    campaign_history_contract,
    resolved_warmup_bars,
)
from trading_agent.strategies.funding_carry import FundingCarryStrategy


def frame(n=650):
    prices = [100 + i * .01 + math.sin(i * .19) * (1 + i / 200) for i in range(n)]
    return pl.DataFrame({'open': prices, 'close': prices,
                         'high': [p + 1 for p in prices],
                         'low': [p - 1 for p in prices], 'volume': [100.] * n})


@pytest.mark.parametrize('sid', ['ensemble_ma_adx', 'ma_adx_regime'])
@pytest.mark.parametrize('lookback', [252, 400])
def test_regime_bounded_history_matches_full_prefix(sid, lookback):
    params = {'regime_lookback': lookback}
    descriptor, strategy = build_legacy_candidate(sid, params)
    data = frame()
    full = strategy.compute_indicators(data)
    bounded = strategy.compute_indicators(data.tail(descriptor.warmup_bars + 1))
    assert bounded['atr_pctl'][-1] is not None
    assert bounded['atr_pctl'][-1] == pytest.approx(full['atr_pctl'][-1])
    assert strategy.generate_signals(bounded)[-1] == strategy.generate_signals(full)[-1]


@pytest.mark.parametrize('sid', ['ensemble_ma_adx', 'ma_adx_regime'])
def test_history_contract_covers_every_grid_value(sid):
    grid = {'regime_lookback': [252, 400]}
    history = campaign_history_contract(sid, grid)
    assert history['warmup_bars'] >= resolved_warmup_bars(sid, {'regime_lookback': 400})


def test_funding_requires_real_input_instead_of_synthetic_fallback():
    with pytest.raises(ValueError, match='funding_rate'):
        FundingCarryStrategy().compute_indicators(frame(40))


def test_funding_future_observation_never_backfills_past():
    data = frame(40).with_columns(pl.Series('funding_rate', [None] * 25 + [-.001] * 15))
    strategy = FundingCarryStrategy({'fr_lookback_bars': 3})
    full = strategy.compute_indicators(data)
    prefix = strategy.compute_indicators(data.head(25))
    assert full['fr_filled'][:25].to_list() == prefix['fr_filled'].to_list() == [None] * 25
    assert strategy.generate_signals(full)[:25].to_list() == [0] * 25


def test_funding_forward_fill_uses_only_previous_published_rate():
    data = frame(5).with_columns(pl.Series('funding_rate', [None, .001, None, -.001, None]))
    result = FundingCarryStrategy().compute_indicators(data)
    assert result['fr_filled'].to_list() == [None, .001, .001, -.001, -.001]


@pytest.mark.parametrize('sid', sorted(FIRST_WAVE_DESCRIPTORS))
def test_default_candidate_signals_do_not_change_when_future_is_appended(sid):
    _, strategy = build_legacy_candidate(sid)
    data = frame().with_columns(pl.lit(.0001).alias('funding_rate'))
    prefix = data.head(450)
    before = strategy.generate_signals(strategy.compute_indicators(prefix)).to_list()
    # A new instance avoids conflating future leakage with retained model state.
    _, fresh = build_legacy_candidate(sid)
    after = fresh.generate_signals(fresh.compute_indicators(data)).head(450).to_list()
    assert before == after


@pytest.mark.parametrize('sid', ['stat_arbitrage_ls', 'cross_sectional_momentum_ls'])
def test_named_long_short_candidate_negative_signal_is_reduce_only(sid):
    from trading_agent.authority.config import Environment
    from trading_agent.authority.resolver import StrategyRuntime

    _, strategy = build_legacy_candidate(sid)
    # Extreme sell conditions, no broker/account access or order submission.
    if sid == 'stat_arbitrage_ls':
        data = frame(100).with_columns(
            pl.when(pl.int_range(pl.len()) == 99).then(200.).otherwise(pl.col('close')).alias('close'))
    else:
        prices = [200. - i for i in range(100)]
        data = pl.DataFrame({'open': prices, 'high': prices, 'low': prices,
                             'close': prices, 'volume': [100.] * 100})
    runtime = StrategyRuntime(strategy=strategy, artifact_id='synthetic-not-promoted',
                              strategy_name=sid, symbol='BTC/USDT', timeframe='4h',
                              environment=Environment.RESEARCH, parameters={'target_exposure_pct': .25},
                              promoted_at=datetime.now(UTC), promotion_stage='synthetic_test_only')
    output = runtime.execute(data, portfolio_state=None)
    assert output.signal == 'SELL'
    assert output.metadata['reduce_only'] is True
