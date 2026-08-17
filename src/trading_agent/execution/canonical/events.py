"""Global event order, idempotency keys, and content hashes.

Cross-aggregate global ordering is allocated by SQLite at append time
(see ``ExecutionEventStore.append``).  This module provides deterministic
content hashes for idempotency and key derivation for the canonical pipeline.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Mapping


def _sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _json_canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


# ── Content hashes ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ContentHash:
    """Deterministic SHA-256 content hash."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or len(self.value) != 64:
            raise ValueError("ContentHash.value must be a 64-char hex string")
        try:
            int(self.value, 16)
        except ValueError as exc:
            raise ValueError("ContentHash.value must be hex") from exc

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> ContentHash:
        canonical = _json_canonical(
            {str(k): v for k, v in sorted(payload.items(), key=lambda p: str(p[0]))}
        )
        return cls(_sha256_hex(canonical.encode("utf-8")))

    @classmethod
    def from_bytes(cls, data: bytes) -> ContentHash:
        return cls(_sha256_hex(data))


# ── Observation idempotency key ─────────────────────────────────────────


@dataclass(frozen=True)
class ObservationId:
    """Deterministic idempotency key for a market observation.

    observation_id = hash(venue, symbol, timeframe, bar_close, data_manifest)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("ObservationId.value must be non-empty")

    @staticmethod
    def compute(
        *,
        venue: str,
        symbol: str,
        timeframe: str,
        bar_close_at: datetime,
        data_manifest_id: str,
    ) -> ObservationId:
        if bar_close_at.tzinfo is None:
            raise ValueError("bar_close_at must be timezone-aware")
        payload = {
            "venue": str(venue),
            "symbol": str(symbol),
            "timeframe": str(timeframe),
            "bar_close_at": bar_close_at.isoformat(),
            "data_manifest_id": str(data_manifest_id),
        }
        return ObservationId(ContentHash.from_mapping(payload).value)


# ── Decision key ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class DecisionKey:
    """Deterministic idempotency key for a risk decision.

    decision_key = hash(observation_id, model_artifact_id, strategy_version)
    """

    value: str

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("DecisionKey.value must be non-empty")

    @staticmethod
    def compute(
        *,
        observation_id: str,
        model_artifact_id: str,
        strategy_version: str,
    ) -> DecisionKey:
        payload = {
            "observation_id": str(observation_id),
            "model_artifact_id": str(model_artifact_id),
            "strategy_version": str(strategy_version),
        }
        return DecisionKey(ContentHash.from_mapping(payload).value)


# ── Order intent idempotency key ────────────────────────────────────────


@dataclass(frozen=True)
class IdempotencyKeys:
    """Deterministic idempotency keys for order-level deduplication.

    intent idempotency_key = hash(decision_id, symbol, target_exposure)
    target_exposure_key   = hash(symbol, horizon, decision_id)
    """

    intent_idempotency_key: str
    target_exposure_key: str

    def __post_init__(self) -> None:
        if not self.intent_idempotency_key:
            raise ValueError("intent_idempotency_key must be non-empty")
        if not self.target_exposure_key:
            raise ValueError("target_exposure_key must be non-empty")

    @staticmethod
    def compute(
        *,
        decision_id: str,
        symbol: str,
        target_exposure: float,
        horizon: int,
    ) -> IdempotencyKeys:
        intent_payload = {
            "decision_id": str(decision_id),
            "symbol": str(symbol),
            "target_exposure": float(target_exposure),
        }
        target_payload = {
            "symbol": str(symbol),
            "horizon": int(horizon),
            "decision_id": str(decision_id),
        }
        return IdempotencyKeys(
            intent_idempotency_key=ContentHash.from_mapping(intent_payload).value,
            target_exposure_key=ContentHash.from_mapping(target_payload).value,
        )


# ── Convenience aliases ─────────────────────────────────────────────────


def compute_observation_id(
    venue: str,
    symbol: str,
    timeframe: str,
    bar_close_at: datetime,
    data_manifest_id: str,
) -> str:
    return ObservationId.compute(
        venue=venue,
        symbol=symbol,
        timeframe=timeframe,
        bar_close_at=bar_close_at,
        data_manifest_id=data_manifest_id,
    ).value


def compute_decision_key(
    observation_id: str,
    model_artifact_id: str,
    strategy_version: str,
) -> str:
    return DecisionKey.compute(
        observation_id=observation_id,
        model_artifact_id=model_artifact_id,
        strategy_version=strategy_version,
    ).value


def compute_idempotency_key(
    decision_id: str,
    symbol: str,
    target_exposure: float,
    horizon: int,
) -> str:
    return IdempotencyKeys.compute(
        decision_id=decision_id,
        symbol=symbol,
        target_exposure=target_exposure,
        horizon=horizon,
    ).intent_idempotency_key


def compute_target_exposure_key(
    symbol: str,
    horizon: int,
    decision_id: str,
) -> str:
    return IdempotencyKeys.compute(
        decision_id=decision_id,
        symbol=symbol,
        target_exposure=0.0,
        horizon=horizon,
    ).target_exposure_key


__all__ = [
    "ContentHash",
    "ObservationId",
    "DecisionKey",
    "IdempotencyKeys",
    "compute_observation_id",
    "compute_decision_key",
    "compute_idempotency_key",
    "compute_target_exposure_key",
]
