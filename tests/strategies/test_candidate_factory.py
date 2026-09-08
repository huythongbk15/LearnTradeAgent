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
