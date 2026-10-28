"""Frozen own-history entry for the context contrast. Not a market run.

A0 buys only when the pair's own 24-bar USDT return is positive. A1 also
requires BTC's 24-bar return to be positive and the other C1 values to be
present. A2 also requires the residual sum to be positive. A positive
residual with a falling USDT price does not buy. Native stop and k+2 fill
stay outside this function.
"""

from __future__ import annotations

import hashlib
import json

from trading_agent.research.btc_context_features import (
    BETA_WINDOW,
    RESIDUAL_SUM_WINDOW,
    FeatureValue,
)

ENTRY_RULE_ID = "H-OWN24-CONTEXT-01"
OWN_RETURN_BARS = 24
HOLD_BARS = 24
FILL_DELAY_BARS = 2
ENTRY_THRESHOLD = 0.0
SHARED_ACROSS_TARGETS = True
PERSONALIZATION_PROVEN = False
# Canonical windows are a fixed tail. The exit signal looks HOLD_BARS back
# to an entry that itself needs the full residual history.
CANONICAL_WARMUP_BARS = BETA_WINDOW + RESIDUAL_SUM_WINDOW + HOLD_BARS


def rule_fingerprint() -> str:
    payload = {
        "id": ENTRY_RULE_ID,
        "own_return": "simple_24_strictly_positive",
        "a1_gate": "btc_simple_return_24_strictly_positive_and_c1_available",
        "a2_gate": "residual_sum_24_strictly_positive_and_c2_available",
        "hold_bars": HOLD_BARS,
        "fill_delay_bars": FILL_DELAY_BARS,
        "threshold": ENTRY_THRESHOLD,
        "shared_across_targets": SHARED_ACROSS_TARGETS,
        "residual_is_not_a_usdt_buy": True,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _value(features: dict[str, FeatureValue], name: str) -> FeatureValue:
    try:
        return features[name]
    except KeyError as error:
        raise ValueError(f"missing feature {name}") from error


def _positive(feature: FeatureValue) -> bool:
    return feature.value is not None and feature.value > ENTRY_THRESHOLD


def _available(feature: FeatureValue) -> bool:
    return feature.value is not None


def entry_allowed(
    arm: str,
    own_return: FeatureValue,
    features: dict[str, FeatureValue] | None = None,
) -> bool:
    """True when a new long is allowed. An open position is not this function's job."""
    if own_return.name != "own_simple_return_24":
        raise ValueError("A0 must read the pair's own 24-bar return")
    if arm == "A0":
        return _positive(own_return)
    context = features or {}
    if arm == "A1":
        return _positive(own_return) and _a1_context(context)
    if arm == "A2":
        return _positive(own_return) and _a1_context(context) and _a2_context(context)
    raise ValueError("arm must be A0, A1 or A2")


def _a1_context(features: dict[str, FeatureValue]) -> bool:
    return (
        _available(_value(features, "btc_log_return_1"))
        and _positive(_value(features, "btc_simple_return_24"))
        and _available(_value(features, "btc_realized_vol_24"))
    )


def _a2_context(features: dict[str, FeatureValue]) -> bool:
    return (
        _available(_value(features, "beta_168_ex_current"))
        and _available(_value(features, "corr_168_ex_current"))
        and _positive(_value(features, "residual_sum_24"))
    )


# ── New predicate: PLUS (minimal BTC context) ──────────────────────────
# Distinct identity from H-OWN24-CONTEXT-01. PLUS adds exactly one condition
# over BASE: btc_simple_return_24 strictly positive. Missing BTC return =>
# PLUS HOLD. btc_log_return_1 / btc_realized_vol_24 are NOT part of this
# predicate, so they cannot create availability noise.
PLUS_ENTRY_RULE_ID = "H-OWN24-PLUS-BTCRETURN-01"
PLUS_ARM = "PLUS"


def _a1_minimal_context(features: dict[str, FeatureValue]) -> bool:
    """Only btc_simple_return_24 > 0. Missing key/value => not positive."""
    feature = features.get("btc_simple_return_24")
    return isinstance(feature, FeatureValue) and _positive(feature)


def entry_allowed_plus(
    own_return: FeatureValue,
    features: dict[str, FeatureValue] | None = None,
) -> bool:
    """PLUS entry: own 24h return > 0 AND btc 24h return > 0.

    own_return must be own_simple_return_24. features may omit btc keys;
    absence is treated as not-positive (HOLD), not as an error.
    """
    if own_return.name != "own_simple_return_24":
        raise ValueError("PLUS must read the pair's own 24-bar return")
    return _positive(own_return) and _a1_minimal_context(features or {})


def rule_fingerprint_plus() -> str:
    """Identity of the PLUS predicate. Changes if the predicate changes."""
    payload = {
        "id": PLUS_ENTRY_RULE_ID,
        "own_return": "simple_24_strictly_positive",
        "plus_gate": "btc_simple_return_24_strictly_positive_only",
        "btc_log_return_1_required": False,
        "btc_realized_vol_24_required": False,
        "missing_btc_return_holds": True,
        "hold_bars": HOLD_BARS,
        "fill_delay_bars": FILL_DELAY_BARS,
        "threshold": ENTRY_THRESHOLD,
        "shared_across_targets": SHARED_ACROSS_TARGETS,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
