"""Canonical strategy SDK (Phase S1).

Public surface:

- :class:`StrategyDescriptor` — STR-0101 identity card.
- :class:`AbstainStrategy` — STR-0104 canonical NO_TRADE.
- :class:`LegacyDataFrameAdapter` — STR-0103 fail-closed bridge for legacy
  DataFrame strategies.
- :class:`CanonicalStrategyRegistry` — STR-0107 allowlisted, hash-verified
  registry; the only sanctioned way to obtain a strategy instance.
"""

from trading_agent.strategies.canonical.abstain import (
    ABSTAIN_ARTIFACT_ID,
    CANONICAL_ACTION_NO_TRADE,
    AbstainStrategy,
)
from trading_agent.strategies.canonical.adapter import (
    ACTION_BUY,
    ACTION_NO_TRADE,
    ACTION_SELL,
    OHLCV_WINDOW_FEATURE,
    LegacyAdapterError,
    LegacyDataFrameAdapter,
)
from trading_agent.strategies.canonical.candidates import (
    FIRST_WAVE_DESCRIPTORS,
    build_default_registry,
    build_legacy_candidate,
    build_parameterized_adapter,
)
from trading_agent.strategies.canonical.descriptor import (
    CONTRACT_VERSION,
    StrategyDescriptor,
)
from trading_agent.strategies.canonical.features import (
    CORE_FEATURE_SPECS,
    FEATURE_OHLCV_WINDOW,
    FeatureSpec,
    FeatureUnavailableError,
    build_ohlcv_window,
    validate_point_in_time,
)
from trading_agent.strategies.canonical.registry import (
    CanonicalStrategyRegistry,
    RegistryEntry,
    RegistryIntegrityError,
    UnknownStrategyError,
)
from trading_agent.strategies.canonical.state import (
    StrategyEventLedger,
    StrategyStateKey,
)

__all__ = [
    "ABSTAIN_ARTIFACT_ID",
    "ACTION_BUY",
    "ACTION_NO_TRADE",
    "ACTION_SELL",
    "CANONICAL_ACTION_NO_TRADE",
    "CONTRACT_VERSION",
    "CORE_FEATURE_SPECS",
    "FEATURE_OHLCV_WINDOW",
    "FIRST_WAVE_DESCRIPTORS",
    "OHLCV_WINDOW_FEATURE",
    "AbstainStrategy",
    "CanonicalStrategyRegistry",
    "FeatureSpec",
    "FeatureUnavailableError",
    "LegacyAdapterError",
    "LegacyDataFrameAdapter",
    "RegistryEntry",
    "RegistryIntegrityError",
    "StrategyDescriptor",
    "StrategyEventLedger",
    "StrategyStateKey",
    "UnknownStrategyError",
    "build_default_registry",
    "build_legacy_candidate",
    "build_parameterized_adapter",
    "build_ohlcv_window",
    "validate_point_in_time",
]
