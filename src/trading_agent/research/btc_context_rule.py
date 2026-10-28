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
PLUS_ENTRY_RULE_ID = "H-OWN24-PLUS-BTCRETURN-01"
PLUS_ARM = "PLUS"
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


def rule_fingerprint_plus() -> str:
    payload = {
        "id": PLUS_ENTRY_RULE_ID,
        "base_rule": ENTRY_RULE_ID,
        "plus_predicate": "btc_simple_return_24_strictly_positive",
        "missing_plus_returns_hold": True,
        "hold_bars": HOLD_BARS,
        "fill_delay_bars": FILL_DELAY_BARS,
        "threshold": ENTRY_THRESHOLD,
        "shared_across_targets": SHARED_ACROSS_TARGETS,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


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
    if arm == PLUS_ARM:
        return _positive(own_return) and _plus_context(context)
    raise ValueError("arm must be A0, A1, A2 or PLUS")


def _plus_context(features: dict[str, FeatureValue]) -> bool:
    """PLUS adds only BTC 24-bar return > 0. Missing BTC return => HOLD."""
    return _positive(_value(features, "btc_simple_return_24"))


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
