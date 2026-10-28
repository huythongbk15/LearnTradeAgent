"""Preregisterable inner-validation eligibility; not promotion authorization.

This module is intentionally not wired into legacy WFO ranking yet. Callers
must freeze the contract in a new campaign plan before evaluating candidates.
No OOS metrics are accepted or used by this API.
"""

import hashlib
import json
import math
from pathlib import Path
from collections.abc import Mapping, Sequence


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite(value):
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def freeze_selection_contract(
    *, min_validation_trades: int, min_validation_sharpe: float
):
    """Thresholds are mandatory: this library must not choose research policy."""
    if type(min_validation_trades) is not int or min_validation_trades < 1:
        raise ValueError("min_validation_trades must be a positive integer")
    if not _finite(min_validation_sharpe) or min_validation_sharpe < 0:
        raise ValueError("min_validation_sharpe must be finite and nonnegative")
    body = {
        "version": 1,
        "metric_scope": "inner_validation_only",
        "min_validation_trades": min_validation_trades,
        "min_validation_sharpe": float(min_validation_sharpe),
        "sharpe_comparison": ">",
        "tie_break": "canonical_params_cost_ascending",
        "no_eligible_action": "ABSTAIN",
        "invalid_candidate_action": "REJECT",
    }
    return {
        **body,
        "fingerprint": hashlib.sha256(_canonical(body).encode()).hexdigest(),
    }


def verify_selection_contract(contract):
    if not isinstance(contract, Mapping):
        raise ValueError("selection contract must be a mapping")
    expected = freeze_selection_contract(
        min_validation_trades=contract.get("min_validation_trades"),
        min_validation_sharpe=contract.get("min_validation_sharpe"),
    )
    if dict(contract) != expected:
        raise ValueError("selection contract differs from registered definitions")


def assess_validation_candidates(contract, candidates: Sequence[Mapping]):
    """Return deterministic reasons and SELECT/ABSTAIN, never live approval.

    Input shape matches WFO candidate_metrics: params, cost_scenario,
    val_metrics with total_trades and sharpe. Top-level val_sharpe and any OOS
    values cannot substitute for measured validation metrics. Source/report
    attribution remains the integrating caller's responsibility.
    """
    verify_selection_contract(contract)
    rows = []
    identities = set()
    eligible = []
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            raise ValueError("candidate must be a mapping")
        params = candidate.get("params")
        cost = candidate.get("cost_scenario")
        if (
            not isinstance(params, Mapping)
            or not params
            or not isinstance(cost, str)
            or not cost
        ):
            raise ValueError("candidate needs nonempty params and cost_scenario")
        identity = _canonical({"params": dict(params), "cost_scenario": cost})
        if identity in identities:
            raise ValueError("duplicate candidate identity")
        identities.add(identity)
        # JSON snapshot: decisions must not retain caller-owned mutable params.
        snapshot = json.loads(identity)
        metrics = candidate.get("val_metrics")
        metrics = metrics if isinstance(metrics, Mapping) else {}
        trades = metrics.get("total_trades")
        sharpe = metrics.get("sharpe")
        reasons = []
        if type(trades) is not int or trades < 0:
            reasons.append("INVALID_VALIDATION_TRADE_COUNT")
        elif trades == 0:
            reasons.append("NO_VALIDATION_TRADES")
        elif trades < contract["min_validation_trades"]:
            reasons.append("INSUFFICIENT_VALIDATION_TRADES")
        if not _finite(sharpe):
            reasons.append("INVALID_VALIDATION_SHARPE")
        elif sharpe <= contract["min_validation_sharpe"]:
            reasons.append("VALIDATION_SHARPE_NOT_ABOVE_FLOOR")
        row = {
            **snapshot,
            "candidate_key": identity,
            "eligible": not reasons,
            "reasons": reasons,
            "validation_trades": trades
            if type(trades) is int and trades >= 0
            else None,
            "validation_sharpe": float(sharpe) if _finite(sharpe) else None,
        }
        rows.append(row)
        if not reasons:
            eligible.append(row)
    rows.sort(key=lambda row: row["candidate_key"])
    eligible.sort(key=lambda row: (-row["validation_sharpe"], row["candidate_key"]))
    return {
        "contract_fingerprint": contract["fingerprint"],
        "decision": "SELECT" if eligible else "ABSTAIN",
        "selected": eligible[0] if eligible else None,
        "candidates": rows,
        "live_authorized": False,
    }


def annotate_candidate_decisions(candidates, decision) -> None:
    """Copy a SELECT onto the winning candidate metrics.

    Assessment returns a separate object. The validation summary treats a fold
    as selected only when a candidate carries decision SELECT. Leaving the
    metrics untouched publishes all_folds_selected false after a real SELECT.
    """
    selected = decision.get("selected") if isinstance(decision, Mapping) else None
    if not selected:
        return
    matched = False
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        if (
            candidate.get("params") == selected.get("params")
            and candidate.get("cost_scenario") == selected.get("cost_scenario")
        ):
            candidate["decision"] = "SELECT"
            matched = True
    if not matched:
        raise ValueError("selected candidate is missing from the fold metrics")


def verify_validation_reports(
    contract,
    candidates,
    *,
    root,
    validation_window,
    expected_spec=None,
    expected_commit=None,
    expected_manifest_sha=None,
    expected_simulation_window=None,
    scope_token=None,
):
    """Fail closed on absent/changed/out-of-scope source reports.

    The caller must bind these candidate records to actual WFO artifacts and
    the frozen plan. This is report attribution, not an authenticity signature.
    """
    root = Path(root).resolve()
    for candidate in candidates:
        source = candidate.get("validation_source")
        if not isinstance(source, Mapping):
            raise ValueError("candidate lacks validation source")
        if not isinstance(source.get("report_path"), str) or not source["report_path"]:
            raise ValueError("validation source lacks report path")
        path = (root / source["report_path"]).resolve()
        if not path.is_relative_to(root):
            raise ValueError("validation report escapes campaign root")
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise ValueError("validation report is unavailable") from error
        if hashlib.sha256(raw).hexdigest() != source.get("report_sha256"):
            raise ValueError("validation report hash mismatch")
        try:
            report = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise ValueError("validation report is not valid JSON") from error
        if not isinstance(report, Mapping) or not isinstance(
            report.get("metrics"), Mapping
        ):
            raise ValueError("validation report lacks metrics mapping")
        if report.get("measurement_window") != list(validation_window):
            raise ValueError("validation report window mismatch")
        metrics = report.get("metrics", {})
        if expected_spec is not None:
            from trading_agent.backtest.tournament import (
                EvaluationArtifact,
                EvaluationCellSpec,
                _artifact_from_report,
            )
            from trading_agent.strategies.canonical.candidates import (
                build_default_registry,
            )

            try:
                artifact = EvaluationArtifact.from_dict(source["artifact"])
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(
                    "validation artifact is malformed or has a content hash mismatch"
                ) from error
            cost = next(
                (
                    cost
                    for cost in expected_spec.cost_scenarios
                    if cost.name == candidate["cost_scenario"]
                ),
                None,
            )
            if cost is None:
                raise ValueError("candidate cost scenario is outside frozen WFO")
            cell = EvaluationCellSpec(
                strategy_id=expected_spec.strategy_id,
                symbol=expected_spec.symbol,
                timeframe=expected_spec.timeframe,
                params=candidate["params"],
                cost_scenario=cost,
            )
            if expected_commit is not None and artifact.commit_sha != expected_commit:
                raise ValueError("validation revision differs from campaign source")
            if (
                expected_manifest_sha is not None
                and artifact.data_manifest_sha != expected_manifest_sha
            ):
                raise ValueError(
                    "validation data-quality identity differs from outer artifact"
                )
            if (
                artifact.status != "COMPLETED"
                or artifact.cell_id != cell.cell_id
                or artifact.params_hash != cell.params_hash
                or artifact.cost_scenario != cost.name
                or artifact.commission != cost.commission
                or artifact.slippage != cost.slippage
                or artifact.fault_profile != cell.fault.name
                or type(artifact.signal_delay_bars) is not int
                or artifact.signal_delay_bars != 0
                or any(
                    getattr(artifact, key) != getattr(cell, key)
                    for key in ("strategy_id", "symbol", "timeframe")
                )
                or artifact.measurement_window != tuple(validation_window)
                or Path(artifact.report_path).resolve() != path
            ):
                raise ValueError("validation artifact identity mismatch")
            simulation = artifact.simulation_window
            if (
                not isinstance(simulation, tuple)
                or len(simulation) != 2
                or any(type(value) is not int for value in simulation)
                or not 0 <= simulation[0] <= validation_window[0]
                or simulation[1] != validation_window[1]
                or (expected_simulation_window is not None
                    and simulation != tuple(expected_simulation_window))
            ):
                raise ValueError(
                    "validation simulation window exceeds declared validation boundary"
                )
            rebuilt = _artifact_from_report(
                cell,
                build_default_registry(scope_token=scope_token).describe(cell.strategy_id),
                path.parent,
                report,
                artifact.data_manifest_sha,
                measurement_window=artifact.measurement_window,
                simulation_window=artifact.simulation_window,
                signal_delay_bars=artifact.signal_delay_bars,
            )
            if (
                rebuilt.status != "COMPLETED"
                or rebuilt.metrics != artifact.metrics
                or rebuilt.execution_health != artifact.execution_health
                or dict(artifact.metrics) != candidate["val_metrics"]
            ):
                raise ValueError(
                    "validation report does not reconstruct completed artifact"
                )
        for key in ("total_trades", "sharpe"):
            if metrics.get(key) != candidate.get("val_metrics", {}).get(key):
                raise ValueError("validation report metrics differ from candidate")
    return assess_validation_candidates(contract, candidates)
