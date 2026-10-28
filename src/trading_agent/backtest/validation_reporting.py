"""Validation-only evidence coverage, independent of launcher/process execution.

The launcher supplies its protocol/native/audit collaborators explicitly so
public compatibility adapters retain their call-time dependency semantics.
This verifier never writes execution timing or grants qualification.
"""

from collections import Counter
import json
from pathlib import Path
import uuid

from trading_agent.backtest.tournament import EvaluationArtifact


def verify_validation_only_coverage(
    spec, result, protocol, folds, out_root, *,
    verify_protocol, verify_native_report, audit_trial_history,
):
    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.backtest.selection_eligibility import verify_validation_reports
    from trading_agent.backtest.nested_wfo import InnerSelectionFreeze
    verify_protocol(spec, protocol, folds)
    if (result.outer_results or result.final_holdout or result.passes_hard_gates
            or [r.fold_id for r in result.inner_results] != [f.fold_id for f in folds]):
        raise ValueError("validation fold coverage or prohibited OOS/holdout mismatch")
    observed, reports, decisions = [], [], []
    # --- Pre-flight: classify FAILED cells as terminal INCOMPLETE ---
    # Never crash on a missing report; never grant coverage PASS when a cell
    # did not produce a report. Cell COMPLETED reports are still verified
    # fail-closed below (missing/hash-mismatch -> ValueError).
    #
    # Artifact lives under validation_source["artifact"] (production structure),
    # not candidate.get("artifact") — reading the wrong location would flag a
    # valid COMPLETED cell as FAILED.
    failed_cells = []
    completed_by_fold = {}
    attempted_rows = []
    for fold, inner in zip(folds, result.inner_results):
        if (inner.val_start, inner.val_end) != (fold.inner_val_start, fold.inner_val_end):
            raise ValueError("validation fold window mismatch")
        fold_completed = []
        for candidate in inner.candidate_metrics or []:
            source = candidate.get("validation_source") or {}
            artifact_dict = source.get("artifact")
            rp = source.get("report_path", "")
            sha = source.get("report_sha256", "")
            try:
                artifact = EvaluationArtifact.from_dict(artifact_dict)
            except (KeyError, TypeError, ValueError, AttributeError) as exc:
                raise ValueError("validation artifact is missing or malformed") from exc
            artifact_status = artifact.status
            if artifact_status not in {"COMPLETED", "FAILED"}:
                raise ValueError("unexpected validation artifact status")
            cost = next((c for c in spec.cost_scenarios
                         if c.name == candidate.get("cost_scenario")), None)
            if cost is None or any(getattr(artifact, key) != getattr(spec, key)
                                   for key in ("strategy_id", "symbol", "timeframe")):
                raise ValueError("validation artifact subject/cost mismatch")
            attempted_rows.append({
                "cell_id": artifact.cell_id, "params": candidate.get("params"),
                "cost": cost.to_dict(), "simulation_window": list(artifact.simulation_window),
                "measurement_window": list(artifact.measurement_window),
            })
            exec_control = dict(artifact.execution_control or {})
            if artifact_status == "FAILED":
                failed_cells.append(
                    {
                        "fold_id": fold.fold_id,
                        "val_window": [fold.inner_val_start, fold.inner_val_end],
                        "cell_id": artifact.cell_id,
                        "params": candidate.get("params"),
                        "cost_scenario": candidate.get("cost_scenario"),
                        "report_path": rp,
                        "report_sha256": sha,
                        "artifact_status": artifact_status,
                        "execution_control": exec_control,
                        "failure_reasons": list(artifact.failure_reasons or []),
                        "reason": "cell did not produce a validation report (timeout or execution failure)",
                    }
                )
            else:
                if not rp or not sha:
                    raise ValueError("completed validation source lacks report binding")
                fold_completed.append(candidate)
        completed_by_fold[fold.fold_id] = fold_completed

    if Counter(map(content_hash, attempted_rows)) != Counter(map(content_hash, protocol["expected_inner_cells"])):
        raise ValueError("missing, duplicate or unexpected validation cells")

    if failed_cells:
        # Even in a mixed packet, COMPLETED reports must still be verified
        # fail-closed — a FAILED cell must not cover a corrupted COMPLETED report.
        for fold, inner in zip(folds, result.inner_results):
            fold_completed = completed_by_fold[fold.fold_id]
            if fold_completed:
                verify_validation_reports(
                    protocol["selection_contract"], fold_completed, root=out_root,
                    validation_window=(fold.inner_val_start, fold.inner_val_end),
                    expected_spec=spec, expected_commit=protocol["source"]["code_commit"],
                    scope_token=(protocol.get("execution") or {}).get("scope_token"),
                )
                for candidate in fold_completed:
                    artifact = EvaluationArtifact.from_dict(candidate["validation_source"]["artifact"])
                    verify_native_report(protocol, artifact, out_root)
                    control = artifact.execution_control
                    if (control.get("timeout_seconds") != protocol["execution"]["cell_timeout"]
                            or control.get("max_retries") != 0 or control.get("attempts_made") != 1
                            or control.get("isolation") != "spawned_process"
                            or control.get("outcome") != "completed"):
                        raise ValueError("validation execution controls differ from protocol")
        completed_cells = sum(len(v) for v in completed_by_fold.values())
        incomplete = {
            "status": "INCOMPLETE",
            "scope": "inner_validation_only_not_qualification",
            "evidence_class": spec.evidence_class,
            "protocol_fingerprint": protocol["fingerprint"],
            "reason": "failed_cells",
            "failed_cells": failed_cells,
            "completed_cells": completed_cells,
            "folds": [{"fold_id": f.fold_id, "decision": None} for f in folds],
            "live_authorized": False,
        }
        return incomplete

    for fold, inner in zip(folds, result.inner_results):
        if (inner.val_start, inner.val_end) != (fold.inner_val_start, fold.inner_val_end):
            raise ValueError("validation fold window mismatch")
        # Verify report attribution (this is the actual purpose of verify_validation_reports)
        verify_validation_reports(
            protocol["selection_contract"], inner.candidate_metrics, root=out_root,
            validation_window=(fold.inner_val_start, fold.inner_val_end), expected_spec=spec,
            expected_commit=protocol["source"]["code_commit"],
            scope_token=(protocol.get("execution") or {}).get("scope_token"),
        )
        # Re-compute expected decision from candidates (tamper-proof)
        from trading_agent.backtest.selection_eligibility import assess_validation_candidates
        expected_decision = assess_validation_candidates(protocol["selection_contract"], inner.candidate_metrics)
        expected_decision_with_fold = {**expected_decision, "fold_id": fold.fold_id}
        # Read the decision from the file (written by nested_wfo via assess_validation_candidates)
        saved = json.loads((out_root / f"selection_decision_{fold.fold_id}.json").read_bytes())
        # Compare ignoring fields added by nested_wfo after assess_validation_candidates
        def strip_augmented_fields(d):
            if not isinstance(d, dict):
                return d
            result = {}
            for k, v in d.items():
                if k in ("report_sha256", "report_path"):
                    continue
                if isinstance(v, list):
                    result[k] = [strip_augmented_fields(item) for item in v]
                elif isinstance(v, dict):
                    result[k] = strip_augmented_fields(v)
                else:
                    result[k] = v
            return result
        if strip_augmented_fields(saved) != strip_augmented_fields(expected_decision_with_fold):
            raise ValueError("validation selection decision differs from raw evidence")
        decision = expected_decision_with_fold
        selected = decision.get("selected")
        expected_best = {**selected["params"], "cost_scenario": selected["cost_scenario"]} if selected else {}
        if inner.best_params != expected_best:
            raise ValueError("validation best params differ from eligibility contract")
        freezes = [f for f in result.inner_selection_freezes if f.fold_id == fold.fold_id]
        if len(freezes) != int(selected is not None):
            raise ValueError("missing, duplicate or unexpected validation freeze")
        for freeze in freezes:
            path = out_root / "inner_selection_freezes" / f"{freeze.freeze_id.removeprefix('sha256:')}.json"
            raw = json.loads(path.read_bytes())
            parsed = InnerSelectionFreeze.from_dict(raw)
            if (raw["freeze_id"] != parsed.freeze_id or parsed.to_dict() != freeze.to_dict()
                    or parsed.best_params != expected_best or parsed.inner_val_start != fold.inner_val_start
                    or parsed.inner_val_end != fold.inner_val_end):
                raise ValueError("invalid validation freeze")
        decisions.append({"fold_id": fold.fold_id, "decision": decision["decision"]})
        for candidate in inner.candidate_metrics:
            source = candidate["validation_source"]
            artifact = EvaluationArtifact.from_dict(source["artifact"])
            control = artifact.execution_control
            verify_native_report(protocol, artifact, out_root)
            if (control.get("timeout_seconds") != protocol["execution"]["cell_timeout"]
                    or control.get("max_retries") != 0 or control.get("attempts_made") != 1
                    or control.get("isolation") != "spawned_process" or control.get("outcome") != "completed"):
                raise ValueError("validation execution controls differ from protocol")
            observed.append({"cell_id": artifact.cell_id, "params": candidate["params"],
                "cost": next(c.to_dict() for c in spec.cost_scenarios if c.name == candidate["cost_scenario"]),
                "simulation_window": list(artifact.simulation_window),
                "measurement_window": list(artifact.measurement_window)})
            reports.append({"path": source["report_path"], "sha256": source["report_sha256"]})
    if Counter(map(content_hash, observed)) != Counter(map(content_hash, protocol["expected_inner_cells"])):
        raise ValueError("missing, duplicate or unexpected validation cells")
    # Verify raw report SHA256 matches actual file content
    import hashlib
    for report_info in reports:
        report_path = Path(report_info["path"])
        if report_path.exists():
            actual_sha = hashlib.sha256(report_path.read_bytes()).hexdigest()
            expected_sha = report_info["sha256"]
            if actual_sha != expected_sha:
                raise ValueError(f"Report SHA256 mismatch: {report_path} actual={actual_sha} expected={expected_sha}")
    audit = audit_trial_history(spec, protocol, folds)
    if audit["conflicts"] or audit["current_context_rows"] != len(observed):
        raise ValueError("canonical validation trial accounting incomplete or conflicting")
    return {"status": "PASS", "scope": "inner_validation_only_not_qualification",
            "evidence_class": spec.evidence_class,
            "protocol_fingerprint": protocol["fingerprint"], "inner_cells": len(observed),
            "folds": decisions, "reports": reports, "outer_folds": 0, "sensitivity_cells": 0,
            "trial_counts": result.trial_counts, "live_authorized": False}


def publish_validation_result(
    spec, result, protocol, folds, out_root, *, verify_coverage, write_json,
):
    """Publish existing validation receipts after native execution.

    Coverage verification remains authoritative; no qualification is granted.
    Collaborators are passed by the launcher to retain call-time test seams.
    """
    # Write cell_execution receipts for each validation cell (per-cell terminal receipts)
    cell_exec_dir = out_root / "cell_execution"
    cell_exec_dir.mkdir(parents=True, exist_ok=True)

    # Verify raw report SHA256 and write receipts
    import hashlib
    for fold, inner in zip(folds, result.inner_results):
        for candidate in inner.candidate_metrics:
            source = candidate["validation_source"]
            report_path = Path(source["report_path"])
            if report_path.exists():
                raw_bytes = report_path.read_bytes()
                report_sha256 = hashlib.sha256(raw_bytes).hexdigest()

                # Verify SHA256 matches cached value if available
                if "report_sha256" in source and source["report_sha256"] != report_sha256:
                    raise ValueError(f"Report SHA256 mismatch for {report_path}: cached={source['report_sha256']} actual={report_sha256}")

                # Write cell_execution receipt
                exec_dir = cell_exec_dir / uuid.uuid4().hex
                exec_dir.mkdir(parents=True, exist_ok=False)
                write_json(exec_dir / "started.json", {
                    "cell_id": candidate.get("cell_id", source.get("cell_id", "unknown")),
                    "strategy_id": spec.strategy_id,
                    "symbol": spec.symbol,
                    "timeframe": spec.timeframe,
                    "params": candidate["params"],
                    "cost": next((c.to_dict() for c in spec.cost_scenarios if c.name == candidate["cost_scenario"]), {}),
                    "validation_window": {"start": inner.val_start, "end": inner.val_end},
                    "report_sha256": report_sha256,
                    "report_path": str(report_path),
                    "status": "COMPLETED",
                    "live_authorized": False,
                    "scope": "validation_receipt_not_qualification",
                })
                write_json(exec_dir / "result.json", {
                    "report_sha256": report_sha256,
                    "report_path": str(report_path),
                    "status": "COMPLETED",
                    "metrics": candidate.get("val_metrics", {}),
                    "live_authorized": False,
                })

    coverage = verify_coverage(spec, result, protocol, folds, out_root)

    if coverage.get("status") == "INCOMPLETE":
        # Terminal INCOMPLETE: summary must not claim VALIDATION_COMPLETE.
        # Keep fold_rows from candidate metrics so the receipt is coherent
        # with terminal_INCOMPLETE.json, but verdict is INCOMPLETE.
        fold_rows = []
        for fold, inner in zip(folds, result.inner_results):
            candidates = inner.candidate_metrics or []
            trades = [
                (c.get("val_metrics") or {}).get("total_trades")
                for c in candidates
            ]
            decisions = [c.get("decision") for c in candidates]
            fold_rows.append({
                "fold_id": fold.fold_id,
                "inner_val_start": fold.inner_val_start,
                "inner_val_end": fold.inner_val_end,
                "decisions": decisions,
                "validation_trades": trades,
                "selected": "SELECT" in decisions,
            })
        write_json(out_root / "terminal_INCOMPLETE.json", coverage)
        write_json(out_root / "exact_coverage.json", coverage)
        write_json(out_root / "parallel_canonical_summary.json", {
            "strategy": spec.strategy_id, "symbol": spec.symbol, "timeframe": spec.timeframe,
            "evidence_class": spec.evidence_class,
            "verdict": "VALIDATION_INCOMPLETE", "validation_only": True, "passes_hard_gates": False,
            "qualification_status": "NOT_QUALIFIED_VALIDATION_ONLY",
            "all_folds_selected": False,
            "sample_complete": False,
            "protocol_fingerprint": protocol["fingerprint"], "aggregate_metrics": result.aggregate_metrics,
            "trial_counts": result.trial_counts,
            "fold_decisions": coverage["folds"],
            "validation_fold_rows": fold_rows, "live_authorized": False,
        })
        return result

    # Write inner_selection_freezes (already written by nested_wfo, but verify here)
    freeze_dir = out_root / "inner_selection_freezes"
    # Directory may not exist if all decisions are ABSTAIN (no freezes written)
    # Create it if missing for consistent structure
    if not freeze_dir.exists():
        freeze_dir.mkdir(parents=True, exist_ok=True)

    # Remove existing files first (tests reuse temp directories)
    for path in [out_root / "exact_coverage.json", out_root / "parallel_canonical_summary.json"]:
        if path.exists():
            path.unlink()
    write_json(out_root / "exact_coverage.json", coverage)
    fold_rows = []
    for fold, inner in zip(folds, result.inner_results):
        candidates = inner.candidate_metrics or []
        trades = [
            (c.get("val_metrics") or {}).get("total_trades")
            for c in candidates
        ]
        decisions = [c.get("decision") for c in candidates]
        fold_rows.append({
            "fold_id": fold.fold_id,
            "inner_val_start": fold.inner_val_start,
            "inner_val_end": fold.inner_val_end,
            "decisions": decisions,
            "validation_trades": trades,
            "selected": "SELECT" in decisions,
        })
    all_selected = bool(fold_rows) and all(row["selected"] for row in fold_rows)
    from trading_agent.backtest.selection_eligibility import verify_selection_contract
    verify_selection_contract(protocol["selection_contract"])
    minimum_trades = protocol["selection_contract"]["min_validation_trades"]
    sample_complete = bool(fold_rows) and all(
        bool(row["validation_trades"]) and all(
            type(trades) is int and trades >= minimum_trades
            for trades in row["validation_trades"]
        )
        for row in fold_rows
    )
    write_json(out_root / "parallel_canonical_summary.json", {
        "strategy": spec.strategy_id, "symbol": spec.symbol, "timeframe": spec.timeframe,
        "evidence_class": spec.evidence_class,
        "verdict": "VALIDATION_COMPLETE", "validation_only": True, "passes_hard_gates": False,
        # EXECUTION completeness is not QUALIFICATION. These three fields make
        # the difference explicit so VALIDATION_COMPLETE can never be read as
        # "qualified" or as "sample sufficient".
        "qualification_status": (
            "NOT_QUALIFIED_VALIDATION_ONLY" if not all_selected
            else ("SAMPLE_COMPLETE_BUT_NOT_QUALIFIED" if sample_complete
                  else "INCOMPLETE_SAMPLE")
        ),
        "all_folds_selected": all_selected,
        "sample_complete": sample_complete,
        "protocol_fingerprint": protocol["fingerprint"], "aggregate_metrics": result.aggregate_metrics,
        "trial_counts": result.trial_counts, "fold_decisions": coverage["folds"],
        "validation_fold_rows": fold_rows, "live_authorized": False,
    })
    return result
