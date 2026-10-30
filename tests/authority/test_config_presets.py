"""Tests for the AuthorityConfig environment presets."""

from __future__ import annotations

import pytest

from trading_agent.authority.config import AuthorityConfig, Environment


@pytest.mark.parametrize("env", list(Environment), ids=lambda env: env.value)
def test_preset_round_trips_through_model_validate(env: Environment):
    # for_environment assigns after construction, which skips the validators.
    preset = AuthorityConfig.for_environment(env)

    assert AuthorityConfig.model_validate(preset.model_dump()) == preset
