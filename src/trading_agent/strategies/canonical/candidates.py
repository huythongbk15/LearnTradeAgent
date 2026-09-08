"""Descriptors and default registry for the first deterministic candidates.

The five strategies named by the roadmap (``enhanced_ma``, ``ma_adx``,
``ma_vol_target``, ``rsi``, ``bbands``) are legacy DataFrame implementations;
they enter the canonical world exclusively through
:class:`LegacyDataFrameAdapter` and are therefore flagged
``research_only=True`` until parity against the golden S0 fixture is proven
(S1 exit gate).

``code_sha`` values are computed from the strategy implementation files at
import time — the registry verifies them against the actual files on disk,
so any tampering or stale build is blocked before a strategy can run.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from trading_agent.strategies.base import Strategy
from trading_agent.strategies.bbands import BBandsStrategy
from trading_agent.strategies.canonical.adapter import LegacyDataFrameAdapter
from trading_agent.strategies.canonical.descriptor import StrategyDescriptor
from trading_agent.strategies.canonical.registry import CanonicalStrategyRegistry
from trading_agent.strategies.enhanced_ma import (
    EnhancedMaCrossover,
    MaAdxCrossover,
    MaVolTargetCrossover,
)
from trading_agent.strategies.rsi import RsiStrategy

_STRATEGIES_DIR = Path(__file__).resolve().parents[1]


def _file_sha(name: str) -> str:
    return hashlib.sha256((_STRATEGIES_DIR / name).read_bytes()).hexdigest()


_ENHANCED_MA_SHA = _file_sha("enhanced_ma.py")
_RSI_SHA = _file_sha("rsi.py")
_BBANDS_SHA = _file_sha("bbands.py")

# Default parameter sets (mirror the legacy defaults) → warm-up bars.
_ENHANCED_MA_WARMUP = 80 + 14 + 6  # slow(80) + adx(14) + buffer
_RSI_WARMUP = 14 + 2
_BBANDS_WARMUP = 20 + 2

_TEN_SYMBOLS = (
    "ADA/USDT",
    "BNB/USDT",
    "BTC/USDT",
    "DOGE/USDT",
    "ETH/USDT",
    "NEAR/USDT",
    "SOL/USDT",
    "TRX/USDT",
    "XRP/USDT",
    "ZEC/USDT",
)

# ──────────────────────────────────────────────────────────────────────
# Parameter schemas (JSON Schema Draft 2020-12)
# ──────────────────────────────────────────────────────────────────────

_ENHANCED_MA_PARAMS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fast_period": {
            "type": "integer",
            "minimum": 2,
            "maximum": 200,
            "description": "Fast MA period (must be < slow_period)",
        },
        "slow_period": {
            "type": "integer",
            "minimum": 3,
            "maximum": 500,
            "description": "Slow MA period (must be > fast_period)",
        },
        "adx_period": {"type": "integer", "minimum": 2, "maximum": 100},
        "adx_threshold": {"type": "number", "minimum": 0.0, "maximum": 100.0},
        "require_close_above_slow": {"type": "boolean"},
        "momentum_period": {"type": "integer", "minimum": 0, "maximum": 100},
        "atr_period": {"type": "integer", "minimum": 2, "maximum": 100},
        "atr_sl_mult": {"type": "number", "minimum": 0.0, "maximum": 10.0},
        "atr_tp_mult": {"type": "number", "minimum": 0.0, "maximum": 10.0},
        "max_dd_pct": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "dd_cooldown_bars": {"type": "integer", "minimum": 0, "maximum": 500},
        "dd_recovery_pct": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "trailing_atr_mult": {"type": "number", "minimum": 0.0, "maximum": 10.0},
        "risk_per_trade": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": [],
    "allOf": [
        {
            "if": {"properties": {"fast_period": {}, "slow_period": {}}},
            "then": {"properties": {"slow_period": {"exclusiveMinimum": {"$data": "1/fast_period"}}}},
        }
    ],
}

_MA_ADX_PARAMS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fast_period": {"type": "integer", "minimum": 2, "maximum": 200},
        "slow_period": {"type": "integer", "minimum": 3, "maximum": 500},
        "adx_period": {"type": "integer", "minimum": 2, "maximum": 100},
        "adx_threshold": {"type": "number", "minimum": 0.0, "maximum": 100.0},
    },
    "required": [],
    "allOf": [
        {
            "if": {"properties": {"fast_period": {}, "slow_period": {}}},
            "then": {"properties": {"slow_period": {"exclusiveMinimum": {"$data": "1/fast_period"}}}},
        }
    ],
}

_MA_VOL_TARGET_PARAMS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "fast_period": {"type": "integer", "minimum": 2, "maximum": 200},
        "slow_period": {"type": "integer", "minimum": 3, "maximum": 500},
    },
    "required": [],
    "allOf": [
        {
            "if": {"properties": {"fast_period": {}, "slow_period": {}}},
            "then": {"properties": {"slow_period": {"exclusiveMinimum": {"$data": "1/fast_period"}}}},
        }
    ],
}

_RSI_PARAMS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "period": {"type": "integer", "minimum": 2, "maximum": 100},
        "oversold": {"type": "integer", "minimum": 1, "maximum": 49},
        "overbought": {"type": "integer", "minimum": 51, "maximum": 99},
    },
    "required": [],
    "allOf": [
        {
            "if": {"properties": {"oversold": {}, "overbought": {}}},
            "then": {"properties": {"overbought": {"exclusiveMinimum": {"$data": "1/oversold"}}}},
        }
    ],
}

_BBANDS_PARAMS_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "period": {"type": "integer", "minimum": 2, "maximum": 100},
        "std_dev": {"type": "number", "minimum": 0.5, "maximum": 5.0},
    },
    "required": [],
}


# ──────────────────────────────────────────────────────────────────────
# Parameter normalization / validation helpers
# ──────────────────────────────────────────────────────────────────────

_PARAM_DEFAULTS: dict[str, dict[str, Any]] = {
    "enhanced_ma": {
        "fast_period": 20,
        "slow_period": 80,
        "adx_period": 14,
        "adx_threshold": 25.0,
        "require_close_above_slow": False,
        "momentum_period": 0,
        "atr_period": 14,
        "atr_sl_mult": 2.0,
        "atr_tp_mult": 3.0,
        "max_dd_pct": 0.15,
        "dd_cooldown_bars": 0,
        "dd_recovery_pct": 0.03,
        "trailing_atr_mult": 0.0,
        "risk_per_trade": 0.02,
    },
    "ma_adx": {
        "fast_period": 20,
        "slow_period": 80,
        "adx_period": 14,
        "adx_threshold": 25.0,
    },
    "ma_vol_target": {
        "fast_period": 20,
        "slow_period": 80,
    },
    "rsi": {
        "period": 14,
        "oversold": 30,
        "overbought": 70,
    },
    "bbands": {
        "period": 20,
        "std_dev": 2.0,
    },
}

_PARAM_SCHEMAS: dict[str, dict[str, Any]] = {
    "enhanced_ma": _ENHANCED_MA_PARAMS_SCHEMA,
    "ma_adx": _MA_ADX_PARAMS_SCHEMA,
    "ma_vol_target": _MA_VOL_TARGET_PARAMS_SCHEMA,
    "rsi": _RSI_PARAMS_SCHEMA,
    "bbands": _BBANDS_PARAMS_SCHEMA,
}


class ParamValidationError(ValueError):
    """Raised when parameter validation fails (fail-closed)."""


def validate_params(strategy_id: str, params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate and normalize parameters for a strategy.

    - Rejects unknown keys (fail-closed)
    - Applies defaults for missing optional keys
    - Validates types, ranges, and cross-parameter constraints
    - Returns normalized dict with all keys present (requested + defaults)
    """
    if strategy_id not in _PARAM_SCHEMAS:
        raise ParamValidationError(f"Unknown strategy_id: {strategy_id}")

    schema = _PARAM_SCHEMAS[strategy_id]
    defaults = _PARAM_DEFAULTS[strategy_id]

    raw = dict(params or {})

    # 1. Reject unknown keys (fail-closed)
    allowed_keys = set(schema["properties"].keys())
    unknown = set(raw.keys()) - allowed_keys
    if unknown:
        raise ParamValidationError(
            f"Unknown parameter(s) for {strategy_id}: {sorted(unknown)}. "
            f"Allowed: {sorted(allowed_keys)}"
        )

    # 2. Apply defaults for missing keys
    normalized = {**defaults, **raw}

    # 3. Type coercion & range validation
    for key, spec in schema["properties"].items():
        value = normalized[key]
        expected_type = spec.get("type")

        if expected_type == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                try:
                    normalized[key] = int(value)
                    value = normalized[key]
                except (ValueError, TypeError):
                    raise ParamValidationError(
                        f"{strategy_id}.{key}: expected integer, got {type(value).__name__}"
                    )
            if "minimum" in spec and value < spec["minimum"]:
                raise ParamValidationError(
                    f"{strategy_id}.{key}: {value} < minimum {spec['minimum']}"
                )
            if "maximum" in spec and value > spec["maximum"]:
                raise ParamValidationError(
                    f"{strategy_id}.{key}: {value} > maximum {spec['maximum']}"
                )
            # Check exclusiveMinimum against another field
            if "exclusiveMinimum" in spec and isinstance(spec["exclusiveMinimum"], dict):
                ref = spec["exclusiveMinimum"].get("$data")
                if ref and ref.startswith("1/"):
                    other_key = ref[2:]
                    if value <= normalized[other_key]:
                        raise ParamValidationError(
                            f"{strategy_id}.{key}: {value} must be > {other_key} ({normalized[other_key]})"
                        )

        elif expected_type == "number":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                try:
                    normalized[key] = float(value)
                    value = normalized[key]
                except (ValueError, TypeError):
                    raise ParamValidationError(
                        f"{strategy_id}.{key}: expected number, got {type(value).__name__}"
                    )
            if "minimum" in spec and value < spec["minimum"]:
                raise ParamValidationError(
                    f"{strategy_id}.{key}: {value} < minimum {spec['minimum']}"
                )
            if "maximum" in spec and value > spec["maximum"]:
                raise ParamValidationError(
                    f"{strategy_id}.{key}: {value} > maximum {spec['maximum']}"
                )

        elif expected_type == "boolean":
            if not isinstance(value, bool):
                raise ParamValidationError(
                    f"{strategy_id}.{key}: expected boolean, got {type(value).__name__}"
                )

    # 4. Cross-parameter validation (fast < slow for MA strategies)
    if strategy_id in ("enhanced_ma", "ma_adx", "ma_vol_target"):
        fast = normalized.get("fast_period")
        slow = normalized.get("slow_period")
        if fast is not None and slow is not None and fast >= slow:
            raise ParamValidationError(
                f"{strategy_id}: fast_period ({fast}) must be < slow_period ({slow})"
            )
    if strategy_id == "rsi":
        oversold = normalized.get("oversold")
        overbought = normalized.get("overbought")
        if oversold is not None and overbought is not None and oversold >= overbought:
            raise ParamValidationError(
                f"{strategy_id}: oversold ({oversold}) must be < overbought ({overbought})"
            )

    return normalized


def compute_effective_params_hash(strategy_id: str, params: Mapping[str, Any] | None) -> str:
    """Compute content-addressed hash of effective (normalized) parameters.

    This is the canonical identity of a parameterized strategy — two configs
    that normalize to the same effective params will have the same hash.
    """
    normalized = validate_params(strategy_id, params)
    # Deterministic JSON encoding
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


def build_legacy_candidate(
    strategy_id: str,
    params: Mapping[str, Any] | None = None,
) -> tuple[StrategyDescriptor, Strategy]:
    """Build an exact parameterized legacy strategy after allowlist verification.

    Validates parameters against schema, applies defaults, and returns
    the descriptor with the effective params hash in the model_artifact_id.
    """
    registry = build_default_registry()
    descriptor = registry.describe(strategy_id)
    strategy_cls = _CANDIDATE_CLASSES[strategy_id]

    # Validate and normalize params (fail-closed)
    normalized = validate_params(strategy_id, params)
    effective_hash = compute_effective_params_hash(strategy_id, params)

    # Build strategy with normalized params
    strategy = strategy_cls(normalized)

    return descriptor, strategy


def build_parameterized_adapter(
    strategy_id: str,
    params: Mapping[str, Any] | None = None,
) -> tuple[StrategyDescriptor, LegacyDataFrameAdapter]:
    """Build a canonical research adapter bound to exact parameter content.

    Returns descriptor and adapter with model_artifact_id containing
    the effective params hash (canonical identity).
    """
    descriptor, strategy = build_legacy_candidate(strategy_id, params)
    effective_hash = compute_effective_params_hash(strategy_id, params)
    adapter = LegacyDataFrameAdapter(
        strategy,
        model_artifact_id=f"legacy.{strategy_id}.{effective_hash}",
        warmup_bars=_CANDIDATE_WARMUPS[strategy_id],
        horizon_bars=descriptor.horizon_bars,
        research_only=True,
        strategy_id=strategy_id,
    )
    return descriptor, adapter


def enumerate_param_grid(
    strategy_id: str,
    grid: Mapping[str, list[Any]],
) -> list[dict[str, Any]]:
    """Enumerate all parameter combinations from a grid.

    Validates each combination and returns list of normalized param dicts.
    """
    import itertools

    keys = list(grid.keys())
    values = [grid[k] for k in keys]
    combinations = []

    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        normalized = validate_params(strategy_id, params)
        combinations.append(normalized)

    return combinations


# ──────────────────────────────────────────────────────────────────────
# Descriptor builders with proper parameter schemas
# ──────────────────────────────────────────────────────────────────────

def _descriptor(
    strategy_id: str,
    code_sha: str,
    warmup_bars: int,
    params_schema: dict[str, Any],
) -> StrategyDescriptor:
    return StrategyDescriptor(
        strategy_id=strategy_id,
        semantic_version="1.0.0",
        code_sha=code_sha,
        parameters_schema=params_schema,
        required_features=("ohlcv_window",),
        horizon_bars=1,  # decision on closed bar, execute next open
        warmup_bars=warmup_bars,
        supported_symbols=_TEN_SYMBOLS,
        research_only=True,
    )


ENHANCED_MA_DESCRIPTOR = _descriptor(
    "enhanced_ma", _ENHANCED_MA_SHA, _ENHANCED_MA_WARMUP, _ENHANCED_MA_PARAMS_SCHEMA
)
MA_ADX_DESCRIPTOR = _descriptor(
    "ma_adx", _ENHANCED_MA_SHA, _ENHANCED_MA_WARMUP, _MA_ADX_PARAMS_SCHEMA
)
MA_VOL_TARGET_DESCRIPTOR = _descriptor(
    "ma_vol_target", _ENHANCED_MA_SHA, _ENHANCED_MA_WARMUP, _MA_VOL_TARGET_PARAMS_SCHEMA
)
RSI_DESCRIPTOR = _descriptor("rsi", _RSI_SHA, _RSI_WARMUP, _RSI_PARAMS_SCHEMA)
BBANDS_DESCRIPTOR = _descriptor("bbands", _BBANDS_SHA, _BBANDS_WARMUP, _BBANDS_PARAMS_SCHEMA)

#: All first-wave candidate descriptors, keyed by strategy_id.
FIRST_WAVE_DESCRIPTORS: dict[str, StrategyDescriptor] = {
    d.strategy_id: d
    for d in (
        ENHANCED_MA_DESCRIPTOR,
        MA_ADX_DESCRIPTOR,
        MA_VOL_TARGET_DESCRIPTOR,
        RSI_DESCRIPTOR,
        BBANDS_DESCRIPTOR,
    )
}

_CANDIDATE_CLASSES: dict[str, type[Strategy]] = {
    "enhanced_ma": EnhancedMaCrossover,
    "ma_adx": MaAdxCrossover,
    "ma_vol_target": MaVolTargetCrossover,
    "rsi": RsiStrategy,
    "bbands": BBandsStrategy,
}

_CANDIDATE_WARMUPS = {
    "enhanced_ma": _ENHANCED_MA_WARMUP,
    "ma_adx": _ENHANCED_MA_WARMUP,
    "ma_vol_target": _ENHANCED_MA_WARMUP,
    "rsi": _RSI_WARMUP,
    "bbands": _BBANDS_WARMUP,
}


def _adapter_factory(desc: StrategyDescriptor, strategy_cls, warmup_bars: int):
    """Build a registry factory producing a research-only legacy adapter."""

    def factory() -> LegacyDataFrameAdapter:
        # Default params (will be overridden by param-specific adapter)
        return LegacyDataFrameAdapter(
            strategy_cls(),
            model_artifact_id=f"legacy.{desc.strategy_id}.v1",
            warmup_bars=warmup_bars,
            horizon_bars=desc.horizon_bars,
            research_only=True,
            strategy_id=desc.strategy_id,
        )

    return factory


def build_default_registry() -> CanonicalStrategyRegistry:
    """Registry with the five first-wave candidates pre-registered."""
    registry = CanonicalStrategyRegistry()
    for desc, cls, source_cls, warmup in (
        (
            ENHANCED_MA_DESCRIPTOR,
            EnhancedMaCrossover,
            EnhancedMaCrossover,
            _ENHANCED_MA_WARMUP,
        ),
        (MA_ADX_DESCRIPTOR, MaAdxCrossover, MaAdxCrossover, _ENHANCED_MA_WARMUP),
        (
            MA_VOL_TARGET_DESCRIPTOR,
            MaVolTargetCrossover,
            MaVolTargetCrossover,
            _ENHANCED_MA_WARMUP,
        ),
        (RSI_DESCRIPTOR, RsiStrategy, RsiStrategy, _RSI_WARMUP),
        (BBANDS_DESCRIPTOR, BBandsStrategy, BBandsStrategy, _BBANDS_WARMUP),
    ):
        registry.register(
            desc,
            _adapter_factory(desc, cls, warmup),
            code_source=source_cls,
        )
    return registry


__all__ = [
    "BBANDS_DESCRIPTOR",
    "ENHANCED_MA_DESCRIPTOR",
    "FIRST_WAVE_DESCRIPTORS",
    "MA_ADX_DESCRIPTOR",
    "MA_VOL_TARGET_DESCRIPTOR",
    "RSI_DESCRIPTOR",
    "ParamValidationError",
    "build_default_registry",
    "build_legacy_candidate",
    "build_parameterized_adapter",
    "compute_effective_params_hash",
    "enumerate_param_grid",
    "validate_params",
]
