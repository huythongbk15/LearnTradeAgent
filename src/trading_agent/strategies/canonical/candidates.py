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
from pathlib import Path

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


def _descriptor(
    strategy_id: str,
    code_sha: str,
    warmup_bars: int,
) -> StrategyDescriptor:
    return StrategyDescriptor(
        strategy_id=strategy_id,
        semantic_version="1.0.0",
        code_sha=code_sha,
        parameters_schema={
            "type": "object",
            "additionalProperties": True,
        },
        required_features=("ohlcv_window",),
        horizon_bars=1,  # decision on closed bar, execute next open
        warmup_bars=warmup_bars,
        supported_symbols=_TEN_SYMBOLS,
        research_only=True,
    )


ENHANCED_MA_DESCRIPTOR = _descriptor(
    "enhanced_ma", _ENHANCED_MA_SHA, _ENHANCED_MA_WARMUP
)
MA_ADX_DESCRIPTOR = _descriptor("ma_adx", _ENHANCED_MA_SHA, _ENHANCED_MA_WARMUP)
MA_VOL_TARGET_DESCRIPTOR = _descriptor(
    "ma_vol_target", _ENHANCED_MA_SHA, _ENHANCED_MA_WARMUP
)
RSI_DESCRIPTOR = _descriptor("rsi", _RSI_SHA, _RSI_WARMUP)
BBANDS_DESCRIPTOR = _descriptor("bbands", _BBANDS_SHA, _BBANDS_WARMUP)

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


def _adapter_factory(desc: StrategyDescriptor, strategy_cls, warmup_bars: int):
    """Build a registry factory producing a research-only legacy adapter."""

    def factory() -> LegacyDataFrameAdapter:
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
