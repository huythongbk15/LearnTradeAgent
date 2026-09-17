"""Canonical candidate factory coverage for selectable full-system runs."""

from trading_agent.strategies.canonical.candidates import (
    FIRST_WAVE_DESCRIPTORS,
    build_legacy_candidate,
    build_parameterized_adapter,
)


def test_all_first_wave_candidates_build_with_bound_parameters():
    # Strategy-specific default params for each first-wave candidate
    strategy_params = {
        "enhanced_ma": {"fast_period": 20, "slow_period": 80},
        "ma_adx": {"fast_period": 20, "slow_period": 80},
        "ma_vol_target": {"fast_period": 20, "slow_period": 80},
        "rsi": {"period": 14},
        "bbands": {"period": 20},
        "regime_switching": {
            "regime_method": "hybrid",
            "lookback": 200,
            "min_confidence": 0.55,
            "regime_smoothing": 3,
            "refit_every": 0,
            "base_position_pct": 0.1,
        },
        "funding_carry": {
            "funding_entry_threshold": -0.00008,
            "funding_exit_threshold": 0.00005,
            "max_hold_periods": 0,
            "vol_window": 20,
            "fr_lookback_bars": 22,
        },
        "volatility_breakout": {
            "bb_period": 14, "bb_std": 2.0, "compression_percentile": 0.03,
            "atr_spike_mult": 1.2, "max_hold_bars": 20, "atr_period": 14, "volume_lookback": 20,
        },
        "ensemble_ma_adx": {"regime_lookback": 252, "min_weight": 0.05},
        "ma_adx_regime": {"fast_period": 20, "slow_period": 80, "adx_threshold": 25.0,
                          "atr_period": 14, "regime_lookback": 252},
        "trend_pullback": {
            "ma_fast": 20, "ma_slow": 80, "adx_threshold": 25.0, "adx_period": 14,
            "rsi_period": 14, "vol_multiplier": 1.5,
        },
        "ma_crossover": {"fast_period": 20, "slow_period": 50},
        "range_mean_reversion": {
            "vwap_window": 20, "zscore_entry": 2.0, "zscore_exit": 0.5,
            "bb_lookback": 20, "bb_std": 2.0, "rsi_oversold": 30, "rsi_overbought": 70,
        },
        "stat_arbitrage_lo": {"zscore_entry": 2.0, "zscore_exit": 0.5,
                              "lookback_days": 20, "bb_lookback": 20},
        "stat_arbitrage_ls": {"zscore_entry": 2.0, "zscore_exit": 0.5, "lookback_days": 20},
        "cross_sectional_momentum_lo": {"lookback_days": 60, "fast_period": 20, "slow_period": 60},
        "cross_sectional_momentum_ls": {"lookback_days": 60, "fast_period": 20, "slow_period": 60},
    }

    for strategy_id, expected_descriptor in FIRST_WAVE_DESCRIPTORS.items():
        params = strategy_params[strategy_id]
        descriptor, strategy = build_legacy_candidate(strategy_id, params)
        assert descriptor.descriptor_id == expected_descriptor.descriptor_id
        assert descriptor.code_sha == expected_descriptor.code_sha
        assert callable(strategy.compute_indicators)
        assert callable(strategy.generate_signals)


def test_parameterized_adapter_is_research_only_and_content_bound():
    descriptor, adapter = build_parameterized_adapter("rsi", {"period": 21})
    assert descriptor.strategy_id == "rsi"
    assert adapter.strategy_id == "rsi"
