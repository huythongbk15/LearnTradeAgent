"""Canonical strategy descriptor (STR-0101).

A :class:`StrategyDescriptor` is the immutable identity card of a canonical
strategy under the ``ForecastStrategy.forecast(MarketObservation) -> Forecast``
contract.  It carries everything the runtime and research layers need to
decide *whether* a strategy may run: identity, version, code hash, parameter
schema, feature requirements, timing (horizon / warm-up) and symbol scope.

Design constraints inherited from Phase S0:

- Content-addressed: ``descriptor_id`` is a sha256 over every substantive
  field, mirroring ``StrategyArtifact.artifact_id``.
- Frozen and deeply immutable (mappings are frozen recursively).
- Fail-closed validation at construction time — a malformed descriptor can
  never enter the registry.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping

#: Version of the canonical forecast contract this descriptor binds to.
CONTRACT_VERSION = "forecast.v1"

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_FEATURE_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_SYMBOL_RE = re.compile(r"^[A-Z0-9]+/[A-Z0-9]+$")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {
                str(key): _freeze(item)
                for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            }
        )
    return value


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in sorted(value.items())}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    return value


@dataclass(frozen=True)
class StrategyDescriptor:
    """Immutable identity of one canonical strategy implementation.

    Attributes:
        strategy_id: Canonical registry key (lowercase snake_case), e.g.
            ``"enhanced_ma"``.
        semantic_version: Strict MAJOR.MINOR.PATCH.
        code_sha: sha256 hex digest of the strategy source file(s).  The
            registry verifies this against the actual module on load.
        parameters_schema: JSON Schema describing accepted parameters.
        required_features: Exact feature names the strategy reads from
            ``MarketObservation.features`` (point-in-time availability is a
            producer obligation — STR-0105).
        horizon_bars: Forecast horizon in bars (> 0).
        warmup_bars: Bars of history required before the first forecast
            (>= 0).
        supported_symbols: Optional whitelist of ``BASE/QUOTE`` symbols;
            empty means "any instrument rule that admits the symbol".
        contract_version: Forecast contract binding; must equal
            :data:`CONTRACT_VERSION` unless explicitly overridden by tests.
        research_only: True when the implementation has NOT passed parity
            testing (STR-0103); research-only descriptors are blocked from
            paper/testnet/production environments by the registry.
    """

    strategy_id: str
    semantic_version: str
    code_sha: str
    parameters_schema: Mapping[str, Any] = field(default_factory=dict)
    required_features: tuple[str, ...] = ()
    horizon_bars: int = 1
    warmup_bars: int = 0
    supported_symbols: tuple[str, ...] = ()
    contract_version: str = CONTRACT_VERSION
    research_only: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.strategy_id, str) or not re.fullmatch(
            r"[a-z][a-z0-9_]*", self.strategy_id
        ):
            raise ValueError(
                f"strategy_id must be lowercase snake_case, got {self.strategy_id!r}"
            )
        if not _SEMVER_RE.fullmatch(self.semantic_version or ""):
            raise ValueError(
                f"semantic_version must be MAJOR.MINOR.PATCH, "
                f"got {self.semantic_version!r}"
            )
        if not _HEX64_RE.fullmatch(self.code_sha or ""):
            raise ValueError("code_sha must be 64 lowercase hex chars")
        for feature in self.required_features:
            if not _FEATURE_RE.fullmatch(feature):
                raise ValueError(
                    f"feature names must be snake_case tokens, got {feature!r}"
                )
        if self.horizon_bars <= 0:
            raise ValueError("horizon_bars must be positive")
        if self.warmup_bars < 0:
            raise ValueError("warmup_bars cannot be negative")
        for symbol in self.supported_symbols:
            if not _SYMBOL_RE.fullmatch(symbol):
                raise ValueError(
                    f"supported_symbols entries must look like BASE/QUOTE, "
                    f"got {symbol!r}"
                )
        object.__setattr__(
            self, "required_features", tuple(sorted(set(self.required_features)))
        )
        object.__setattr__(
            self, "supported_symbols", tuple(sorted(set(self.supported_symbols)))
        )
        object.__setattr__(self, "parameters_schema", _freeze(self.parameters_schema))

    # ── Identity ────────────────────────────────────────────────────────
    @property
    def descriptor_id(self) -> str:
        """Content-addressed id (first 24 hex chars of sha256)."""
        payload = json.dumps(
            _jsonable(self.to_dict()), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def supports_symbol(self, symbol: str) -> bool:
        """Fail-closed symbol gate: empty allowlist admits nothing extra."""
        if not self.supported_symbols:
            return False
        return symbol in self.supported_symbols

    # ── Serialization ───────────────────────────────────────────────────
    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": self.strategy_id,
            "semantic_version": self.semantic_version,
            "code_sha": self.code_sha,
            "parameters_schema": _jsonable(self.parameters_schema),
            "required_features": list(self.required_features),
            "horizon_bars": self.horizon_bars,
            "warmup_bars": self.warmup_bars,
            "supported_symbols": list(self.supported_symbols),
            "contract_version": self.contract_version,
            "research_only": self.research_only,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "StrategyDescriptor":
        known = {
            "strategy_id",
            "semantic_version",
            "code_sha",
            "parameters_schema",
            "required_features",
            "horizon_bars",
            "warmup_bars",
            "supported_symbols",
            "contract_version",
            "research_only",
        }
        unexpected = set(payload) - known
        if unexpected:
            raise ValueError(f"unknown descriptor fields: {sorted(unexpected)}")
        return cls(
            strategy_id=payload["strategy_id"],
            semantic_version=payload["semantic_version"],
            code_sha=payload["code_sha"],
            parameters_schema=dict(payload.get("parameters_schema", {})),
            required_features=tuple(payload.get("required_features", ())),
            horizon_bars=int(payload.get("horizon_bars", 1)),
            warmup_bars=int(payload.get("warmup_bars", 0)),
            supported_symbols=tuple(payload.get("supported_symbols", ())),
            contract_version=payload.get("contract_version", CONTRACT_VERSION),
            research_only=bool(payload.get("research_only", False)),
        )
