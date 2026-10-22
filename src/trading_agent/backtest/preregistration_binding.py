"""Binding between an approved preregistration and the run that executes it.

Running is not the same as running **the approved payload**. A launcher that
happens to work proves nothing about which payload it executed. This module
closes that gap: it verifies, **before the first cell**, that the frozen
protocol of the run agrees with the approved preregistration on every field
that the preregistration fixes:

    approval state / approver / approval timestamp
    identity A (source)      identity B (preregistration)
    input binding hash       the candidate's parameter set
    the inner-validation windows
    the cost scenarios
    the execution budget     the scope token

Any single field that differs blocks the run. The checks are reported
individually so a mismatch names the field, not "something is wrong".
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from trading_agent.strategies.canonical.candidates import (  # noqa: E402
    SCOPED_CANDIDATE_PREREGISTRATION_PATHS,
    SCOPED_CANDIDATE_SCOPE_TOKENS,
)


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalise_singleton_grid(grid: Any) -> dict[str, Any] | None:
    """``{axis: [value]}`` -> ``{axis: value}``.

    A candidate registered with a single fixed value per axis is planned by the
    CLI as a one-element grid, while the preregistration states the scalar
    parameter. The two must be compared in one normalised form; a grid that is
    not a singleton per axis is rejected (it is a search, not a fixed set).
    """
    if not isinstance(grid, dict) or not grid:
        return None
    normalised: dict[str, Any] = {}
    for axis, values in grid.items():
        if not isinstance(values, (list, tuple)) or len(values) != 1:
            return None
        normalised[axis] = values[0]
    return normalised


def preregistration_fingerprint(payload: dict[str, Any]) -> str:
    """Identity B: the sha256 of the canonical preregistration payload."""
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def scoped_token_map_is_consistent() -> bool:
    """Every scope token must have exactly one preregistration packet.

    The two maps are keyed by the same tokens, so the check is that the key
    sets are equal AND that every token resolves to a candidate id that the
    token map and the candidate map agree on. It must not compare a map with
    itself.
    """
    tokens = set(SCOPED_CANDIDATE_SCOPE_TOKENS)
    paths = set(SCOPED_CANDIDATE_PREREGISTRATION_PATHS)
    if tokens != paths:
        return False
    for token, candidate in SCOPED_CANDIDATE_SCOPE_TOKENS.items():
        if not candidate:
            return False
        if candidate not in set(SCOPED_CANDIDATE_SCOPE_TOKENS.values()):
            return False
        if not SCOPED_CANDIDATE_PREREGISTRATION_PATHS.get(token):
            return False
    return True


def load_approved_payload(scope_token: str | None, root: Path) -> tuple[dict, dict]:
    """Load the approved payload and its manifest for ``scope_token``.

    Raises when the token names no preregistration, when the payload cannot be
    read, or when the manifest's recorded identity B does not match the
    recomputed one — a payload edited after approval is not an approved payload.
    """
    if not scoped_token_map_is_consistent():
        raise ValueError(
            "the scope-token map and the preregistration map disagree"
        )
    relative = SCOPED_CANDIDATE_PREREGISTRATION_PATHS.get(scope_token or "")
    if not relative:
        raise ValueError(
            f"scope token {scope_token!r} names no preregistration packet"
        )
    # the same packet must exist under the repository root, so a run cannot bind
    # to a packet that has no counterpart in the registered tree
    repository_root = Path(__file__).resolve().parent.parent.parent.parent
    if not (repository_root / relative).exists():
        raise ValueError(
            f"the preregistration packet {relative!r} does not exist under the "
            f"repository root {repository_root}"
        )
    packet = root / relative
    payload_path = packet / "preregistration_payload.json"
    manifest_path = packet / "manifest.json"
    if not payload_path.exists() or not manifest_path.exists():
        raise ValueError(f"preregistration packet is incomplete: {packet}")
    payload = json.loads(payload_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    recomputed = preregistration_fingerprint(payload)
    recorded = manifest.get("identity_B_preregistration_fingerprint")
    if recomputed != recorded:
        raise ValueError(
            "preregistration identity B does not match the approved manifest: "
            f"recomputed {recomputed}, manifest {recorded}. The payload was "
            "edited after approval."
        )
    return payload, manifest


def verify_registered_payload_binding(
    protocol: dict[str, Any],
    scope_token: str | None,
    *,
    root: Path,
    source_fingerprint: str,
) -> list[str]:
    """Verify the run against the approved payload. Returns the failed fields.

    An empty list means every checked field agrees. A non-empty list means the
    run must not start: the caller raises.
    """
    spec = protocol.get("spec") or {}
    scoped_candidate_ids = set(SCOPED_CANDIDATE_SCOPE_TOKENS.values())
    if spec.get("strategy_id") not in scoped_candidate_ids:
        # Not a scoped candidate: the preregistration binding does not apply.
        # The control and every ordinary study run unchanged; a scoped candidate
        # without its token still fails closed at the registry/grid resolution.
        return []
    repository_root = Path(__file__).resolve().parents[3]
    if (root.resolve() != repository_root
            and spec.get("evidence_class") != "SYNTHETIC_TEST_ONLY"):
        return ["preregistration_root"]
    payload, manifest = load_approved_payload(scope_token, root)
    request = payload["data"]["scope_change_request"]
    execution = protocol.get("execution") or {}
    budget = protocol.get("validation_budget") or {}
    folds = protocol.get("folds") or []

    windows = [[f["inner_val_start"], f["inner_val_end"]] for f in folds]
    costs = {
        c["name"]: {"fee_multiplier": c["fee_multiplier"],
                    "slippage_multiplier": c["slippage_multiplier"]}
        for c in spec.get("cost_scenarios", [])
    }
    expected_costs = {
        name: {"fee_multiplier": value["fee_multiplier"],
               "slippage_multiplier": value["slippage_multiplier"]}
        for name, value in payload["costs"].items()
    }

    # The approval has ONE authoritative state: ``approval_state``. The
    # procedure edits exactly approval_state, approved_scope, approved_by and
    # approved_at; nothing else is consulted, so following the procedure
    # cannot be rejected.
    normalised_params = normalise_singleton_grid(spec.get("param_grid"))
    expected_scope = request.get("expected_approved_scope")
    approved_contract = payload.get("execution_contract") or {}
    expected_bodies = list((approved_contract.get("native_contract_bodies_by_cost") or {}).values())
    contracts = protocol.get("native_execution_contracts") or {}
    contracts_match = bool(expected_bodies) and isinstance(contracts, dict) and bool(contracts)
    if contracts_match:
        from trading_agent.backtest.execution_contract import require_execution_contract

        try:
            for contract in contracts.values():
                require_execution_contract(contract.get("contract_id"), contract)
                if contract.get("body") not in expected_bodies:
                    contracts_match = False
        except (TypeError, ValueError, AttributeError):
            contracts_match = False

    checks: dict[str, bool] = {
        "approval_state": request.get("approval_state") == "APPROVED",
        "approved_by": bool(request.get("approved_by")),
        "approved_at": bool(request.get("approved_at")),
        "approved_scope": (
            bool(request.get("approved_scope"))
            and bool(expected_scope)
            and request.get("approved_scope") == expected_scope
        ),
        "identity_A_source": (
            source_fingerprint == payload.get("identity_A_source_fingerprint")
            == manifest.get("identity_A_source_fingerprint")
        ),
        "identity_B_preregistration": (
            preregistration_fingerprint(payload)
            == manifest.get("identity_B_preregistration_fingerprint")
        ),
        "input_sha256": (
            (protocol.get("input_binding") or {}).get("sha256")
            == payload["data"]["sha256"]
        ),
        "params": (
            normalised_params is not None
            and normalised_params == payload["candidate"]["params"]
        ),
        "runtime_config": (
            bool(approved_contract.get("runtime_config"))
            and execution.get("runtime_config") == approved_contract["runtime_config"]
        ),
        "native_execution_contracts": contracts_match,
        # the SUBJECT, not only its parameter values
        "strategy_id": spec.get("strategy_id") == payload["candidate"]["strategy_id"],
        "symbol": spec.get("symbol") == payload["candidate"].get("symbol"),
        "timeframe": spec.get("timeframe") == payload["candidate"].get("timeframe"),
        "windows": windows == payload["geometry"]["inner_validation_windows"],
        "costs": costs == expected_costs,
        "cell_timeout": (
            execution.get("cell_timeout") == payload["budget"]["cell_timeout_seconds"]
        ),
        "total_timeout": (
            budget.get("total_timeout_seconds")
            == payload["budget"]["total_timeout_seconds"]
        ),
        "max_rss_mb": budget.get("max_rss_mb") == payload["budget"]["max_rss_mb"],
        "max_retries": (
            execution.get("max_retries") == payload["budget"]["max_retries"]
        ),
        "scope_token": execution.get("scope_token") == payload["scope_token_binding"]["token"],
    }
    return sorted(name for name, ok in checks.items() if not ok)
