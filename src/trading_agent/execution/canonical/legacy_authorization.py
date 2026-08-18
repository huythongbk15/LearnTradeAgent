"""Minimal legacy runner authorization helper.

Legacy runners (``scripts/live_enhanced_ma*.py``) do not yet implement the
full canonical pipeline (risk → planner → permission → lifecycle).  To
avoid fabrication while still enabling migration, this helper requires
callers to provide explicit evidence fields before producing an
``AuthorizedOrder``.

This is NOT the canonical path — it is a temporary bridge.  Runners MUST
migrate to the full canonical pipeline before mainnet promotion.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime

from trading_agent.execution.canonical.broker_gateway import AuthorizedOrder


@dataclass(frozen=True)
class LegacyAuthorizationEvidence:
    """Evidence required to authorize a legacy runner order."""
    symbol: str
    side: str
    quantity: float
    price_reference: float
    signal_reason: str  # "ATR_TRAILING_STOP" | "PORTFOLIO_HALT" | "REBALANCE" | "STRATEGY_FLAT"
    strategy_version: str
    account_equity: float
    current_exposure: float
    idempotency_key: str
    correlation_id: str


class LegacyAuthorizationError(RuntimeError):
    """Raised when required evidence is missing or invalid."""


def authorize_legacy_order(evidence: LegacyAuthorizationEvidence) -> AuthorizedOrder:
    """Create an AuthorizedOrder from explicit runner evidence.

    This is a TEMPORARY bridge.  It enforces that the caller provides
    verifiable evidence fields; it does NOT perform full risk/planner/
    permission/lifecycle evaluation.
    """
    if not evidence.signal_reason:
        raise LegacyAuthorizationError("signal_reason is required")
    if evidence.quantity <= 0:
        raise LegacyAuthorizationError("quantity must be positive")
    if evidence.price_reference <= 0:
        raise LegacyAuthorizationError("price_reference must be positive")
    if evidence.account_equity <= 0:
        raise LegacyAuthorizationError("account_equity must be positive")
    if not evidence.idempotency_key:
        raise LegacyAuthorizationError("idempotency_key is required")
    if not evidence.correlation_id:
        raise LegacyAuthorizationError("correlation_id is required")

    now = datetime.now(UTC).isoformat()
    authorization_hash = _hash_evidence(evidence, now)

    return AuthorizedOrder.create(
        intent_id=f"legacy-{evidence.correlation_id}",
        symbol=evidence.symbol,
        side=evidence.side,
        quantity=evidence.quantity,
        idempotency_key=evidence.idempotency_key,
        price_reference=evidence.price_reference,
        risk_decision_id=f"legacy-risk-{evidence.correlation_id}",
        forecast_fingerprint=_signal_fingerprint(evidence),
        model_artifact_id="legacy_runner",
        permission_result="LEGACY_BRIDGE",
        authorization_id=f"legacy-auth-{evidence.correlation_id}",
        lifecycle_event_id=f"legacy-event-{evidence.correlation_id}",
        correlation_id=evidence.correlation_id,
        exposure_effect=_exposure_effect(evidence.side, evidence.current_exposure),
        current_exposure=evidence.current_exposure,
        resulting_exposure=_resulting_exposure(evidence),
        authorized_at=now,
        authorization_hash=authorization_hash,
    )


def _hash_evidence(evidence: LegacyAuthorizationEvidence, authorized_at: str) -> str:
    """Stable hash of evidence for audit."""
    blob = (
        f"{evidence.symbol}|{evidence.side}|{evidence.quantity}|"
        f"{evidence.price_reference}|{evidence.signal_reason}|"
        f"{evidence.strategy_version}|{evidence.account_equity}|"
        f"{evidence.current_exposure}|{evidence.idempotency_key}|"
        f"{authorized_at}"
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _signal_fingerprint(evidence: LegacyAuthorizationEvidence) -> str:
    """Stable fingerprint of the runner signal."""
    blob = (
        f"{evidence.symbol}|{evidence.side}|{evidence.quantity}|"
        f"{evidence.price_reference}|{evidence.signal_reason}|"
        f"{evidence.strategy_version}"
    ).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def _exposure_effect(side: str, current_exposure: float) -> str:
    if side.lower() == "buy":
        return "INCREASE" if current_exposure <= 0 else "INCREASE"
    return "REDUCE"


def _resulting_exposure(evidence: LegacyAuthorizationEvidence) -> float:
    if evidence.side.lower() == "buy":
        return evidence.current_exposure + evidence.quantity
    return evidence.current_exposure - evidence.quantity
