"""Strategies backed by heavy stacks register on first lookup, not at import.

Covers the behavior the live runners rely on (see
tests/test_runtime_import_boundary.py for what they load): lookups by name,
listing, fail-closed unknown names and source-hash verification are unchanged.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from trading_agent.authority.config import AuthorityConfig, Environment
from trading_agent.authority.resolver import RuntimeStrategyResolver, StrategyType
from trading_agent.strategies.base import (
    LazyStrategyClass,
    _LAZY_STRATEGY_MODULES,
    get_strategy,
    list_strategies,
)
from trading_agent.strategies.canonical import build_default_registry
from trading_agent.strategies.canonical.candidates import (
    REGIME_SWITCHING_DESCRIPTOR,
    _STRATEGIES_DIR,
    _adapter_factory,
    build_legacy_candidate,
)
from trading_agent.strategies.canonical.registry import (
    CanonicalStrategyRegistry,
    RegistryIntegrityError,
)


@pytest.mark.parametrize("name", sorted(_LAZY_STRATEGY_MODULES))
def test_lazy_name_resolves_to_its_registered_class(name):
    cls = get_strategy(name)
    assert isinstance(cls, type)
    assert cls.__module__ == _LAZY_STRATEGY_MODULES[name]
    assert name in list_strategies()


def test_unknown_name_still_fails_closed_and_lists_lazy_names():
    with pytest.raises(KeyError) as excinfo:
        get_strategy("no_such_strategy")
    for name in _LAZY_STRATEGY_MODULES:
        assert name in str(excinfo.value)


def test_lazy_class_constructs_and_hashes_the_same_file():
    from trading_agent.strategies.regime_switching import RegimeSwitchingStrategy

    lazy = LazyStrategyClass("trading_agent.strategies.regime_switching", "RegimeSwitchingStrategy")
    assert lazy.resolve() is RegimeSwitchingStrategy
    assert isinstance(lazy(), RegimeSwitchingStrategy)
    assert lazy.source_path == _STRATEGIES_DIR / "regime_switching.py"


@pytest.mark.parametrize(
    ("strategy_type", "module"),
    [
        (StrategyType.REGIME_SWITCHING, "trading_agent.strategies.regime_switching"),
        (StrategyType.ONLINE_LEARNING, "trading_agent.strategies.online_learning_strategy"),
    ],
)
def test_resolver_returns_real_classes_for_lazy_types(strategy_type, module):
    resolver = RuntimeStrategyResolver(AuthorityConfig(environment=Environment.RESEARCH))
    cls = resolver.get_strategy_class(strategy_type)
    assert isinstance(cls, type) and cls.__module__ == module


def test_regime_switching_candidate_builds_a_real_strategy():
    from trading_agent.strategies.regime_switching import RegimeSwitchingStrategy

    descriptor, strategy = build_legacy_candidate("regime_switching")
    assert descriptor.strategy_id == "regime_switching"
    assert isinstance(strategy, RegimeSwitchingStrategy)
    adapter = build_default_registry().get("regime_switching", environment=Environment.RESEARCH)[1]
    assert isinstance(adapter._strategy, RegimeSwitchingStrategy)


def test_path_code_source_is_still_hash_verified():
    lazy = LazyStrategyClass("trading_agent.strategies.regime_switching", "RegimeSwitchingStrategy")
    factory = _adapter_factory(REGIME_SWITCHING_DESCRIPTOR, lazy, 200)
    tampered = replace(REGIME_SWITCHING_DESCRIPTOR, code_sha="0" * 64)
    with pytest.raises(RegistryIntegrityError, match="code_sha mismatch"):
        CanonicalStrategyRegistry().register(
            tampered, factory, code_source=_STRATEGIES_DIR / "regime_switching.py"
        )
    CanonicalStrategyRegistry().register(
        REGIME_SWITCHING_DESCRIPTOR, factory, code_source=_STRATEGIES_DIR / "regime_switching.py"
    )
