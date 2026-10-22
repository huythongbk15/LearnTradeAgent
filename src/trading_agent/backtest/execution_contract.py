"""Native simulator execution identity, not a qualification certificate.

Source/data provenance remains bound by the existing campaign protocol. Legacy
reports without this identity are unknown, never implicitly equivalent.
"""
from __future__ import annotations

import hashlib
import json
import re


def validate_expected_contract_id(value: str | None) -> None:
    if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)):
        raise ValueError("expected execution contract must be a lowercase SHA256")


def native_execution_contract(*, symbol, timeframe, strategy, protection, sizing,
    risk, cost_model, authority, instrument_rules, cadence=1):
    """Capture configured semantics, not an observed fixed effective exposure."""
    if type(cadence) is not int or cadence < 1:
        raise ValueError("execution cadence must be a positive integer")
    blocks = {"strategy": strategy, "protection": protection, "sizing": sizing,
        "risk": risk, "cost_model": cost_model, "authority": authority,
        "instrument_rules": instrument_rules}
    if not symbol or not timeframe or any(not isinstance(v, dict) or not v for v in blocks.values()):
        raise ValueError("incomplete native execution contract")
    body = {"schema_version": 1, "engine": "FullSystemSimulator_authority_PaperExchange_v1",
        "scope": "base_engine_semantics_fault_profile_bound_separately",
        "symbol": symbol, "timeframe": timeframe, "cadence_bars": cadence,
        "signal_timing": "canonical_i_minus_1_to_open_i",
        "price_path": "open_then_low_then_close",
        "protective_stop": "current_price_trigger_with_slippage_not_prior_low_next_open",
        "cooldown_trigger": "risk_circuit_breaker_not_every_sell_fill",
        "accounting": "cash_plus_inventory_marked_at_close_no_forced_liquidation", **blocks}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    # Return detached JSON: callers cannot mutate contract through input aliases.
    return {"contract_id": hashlib.sha256(encoded.encode()).hexdigest(),
        "body": json.loads(encoded)}


def require_execution_contract(expected: str, actual: dict) -> None:
    validate_expected_contract_id(expected)
    if expected is None or not isinstance(actual, dict) or set(actual) != {"body", "contract_id"}:
        raise ValueError("missing execution contract")
    body = actual.get("body")
    if not isinstance(body, dict) or type(body.get("schema_version")) is not int or body.get("schema_version") != 1:
        raise ValueError("unknown execution contract")
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    measured = hashlib.sha256(encoded.encode()).hexdigest()
    if actual.get("contract_id") != measured or expected != measured:
        raise ValueError("execution contract mismatch")


def require_frozen_native_evidence(contracts, cell_id, report, artifact):
    """Check frozen policy against raw AND derived evidence without changing cell ID."""
    if not isinstance(contracts, dict) or cell_id not in contracts:
        raise ValueError("unregistered frozen native execution contract")
    frozen = contracts[cell_id]
    if not isinstance(frozen, dict):
        raise ValueError("missing frozen native execution contract")
    expected = frozen.get("contract_id")
    require_execution_contract(expected, frozen)
    config = report.get("active_config")
    if not isinstance(config, dict):
        raise ValueError("missing raw native execution contract")
    require_execution_contract(expected, config.get("execution_contract"))
    if (artifact.cell_id != cell_id
            or artifact.execution_health.get("execution_contract_id") != expected):
        raise ValueError("artifact frozen native execution contract mismatch")
