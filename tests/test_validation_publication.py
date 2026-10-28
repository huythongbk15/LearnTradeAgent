"""Synthetic-only characterization of terminal validation publication."""

import hashlib
import json
from types import SimpleNamespace

import pytest

from trading_agent.backtest.validation_reporting import publish_validation_result
from trading_agent.backtest.selection_eligibility import freeze_selection_contract


def publication_case(tmp_path, decision="SELECT", trades=30):
    report = tmp_path / "report.json"
    report.write_text('{"fixture": true}')
    candidate = {
        "cell_id": "fixture-cell",
        "params": {"period": 14},
        "cost_scenario": "1x",
        "decision": decision,
        "val_metrics": {"total_trades": trades},
        "validation_source": {
            "report_path": str(report),
            "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
        },
    }
    spec = SimpleNamespace(
        strategy_id="fixture", symbol="SYNTH/TEST", timeframe="4h",
        evidence_class="SYNTHETIC_DIAGNOSTIC",
        cost_scenarios=[SimpleNamespace(name="1x", to_dict=lambda: {"name": "1x"})],
    )
    fold = SimpleNamespace(fold_id="fold_000", inner_val_start=10, inner_val_end=20)
    result = SimpleNamespace(
        inner_results=[SimpleNamespace(val_start=10, val_end=20, candidate_metrics=[candidate])],
        aggregate_metrics={"fixture": True}, trial_counts={"raw": 1},
    )
    protocol = {
        "fingerprint": "fixture-protocol",
        "selection_contract": freeze_selection_contract(
            min_validation_trades=30, min_validation_sharpe=0,
        ),
    }
    return spec, result, protocol, [fold], candidate


def recorder():
    writes = []

    def write(path, payload):
        # Snapshot, so a later in-place mutation cannot disguise the output.
        writes.append((path.name, json.loads(json.dumps(payload))))

    return writes, write


@pytest.mark.parametrize("decision,trades,status,selected,sample", [
    ("SELECT", 30, "SAMPLE_COMPLETE_BUT_NOT_QUALIFIED", True, True),
    ("ABSTAIN", 0, "NOT_QUALIFIED_VALIDATION_ONLY", False, False),
    ("SELECT", 0, "INCOMPLETE_SAMPLE", True, False),
    ("ABSTAIN", 1, "NOT_QUALIFIED_VALIDATION_ONLY", False, False),
    ("ABSTAIN", 29, "NOT_QUALIFIED_VALIDATION_ONLY", False, False),
    ("ABSTAIN", 30, "NOT_QUALIFIED_VALIDATION_ONLY", False, True),
    ("ABSTAIN", None, "NOT_QUALIFIED_VALIDATION_ONLY", False, False),
    ("ABSTAIN", True, "NOT_QUALIFIED_VALIDATION_ONLY", False, False),
    ("ABSTAIN", 30.0, "NOT_QUALIFIED_VALIDATION_ONLY", False, False),
])
def test_complete_publication_preserves_execution_vs_qualification(
    tmp_path, decision, trades, status, selected, sample,
):
    spec, result, protocol, folds, _ = publication_case(tmp_path, decision, trades)
    writes, writer = recorder()
    coverage = {"status": "PASS", "folds": [{"fold_id": "fold_000", "decision": decision}]}
    calls = []

    def verify(*args):
        calls.append(args)
        return coverage

    returned = publish_validation_result(
        spec, result, protocol, folds, tmp_path, verify_coverage=verify, write_json=writer,
    )
    assert returned is result
    assert calls == [(spec, result, protocol, folds, tmp_path)]
    assert [name for name, _ in writes] == [
        "started.json", "result.json", "exact_coverage.json", "parallel_canonical_summary.json",
    ]
    summary = writes[-1][1]
    assert summary["verdict"] == "VALIDATION_COMPLETE"
    assert summary["qualification_status"] == status
    assert summary["all_folds_selected"] is selected
    assert summary["sample_complete"] is sample
    assert summary["passes_hard_gates"] is False
    assert summary["live_authorized"] is False
    assert summary["validation_fold_rows"][0]["validation_trades"] == [trades]
    assert writes[2][1] == coverage


def test_engine_candidate_shape_needs_an_explicit_select_stamp(tmp_path):
    """Real WFO metrics have no decision key. The stamp is what the summary reads."""
    from trading_agent.backtest.selection_eligibility import (
        annotate_candidate_decisions,
        assess_validation_candidates,
    )

    spec, result, protocol, folds, candidate = publication_case(tmp_path, trades=53)
    del candidate["decision"]
    candidate["val_metrics"] = {"total_trades": 53, "sharpe": 0.32}
    rejected = {
        **candidate,
        "cost_scenario": "2x",
        "val_metrics": {"total_trades": 53, "sharpe": -1.2},
    }
    result.inner_results[0].candidate_metrics = [candidate, rejected]
    writes, writer = recorder()
    publish_validation_result(
        spec, result, protocol, folds, tmp_path,
        verify_coverage=lambda *args: {"status": "PASS", "folds": []},
        write_json=writer,
    )
    assert writes[-1][1]["all_folds_selected"] is False

    decision = assess_validation_candidates(protocol["selection_contract"], result.inner_results[0].candidate_metrics)
    assert decision["decision"] == "SELECT"
    annotate_candidate_decisions(result.inner_results[0].candidate_metrics, decision)
    assert candidate["decision"] == "SELECT"
    assert "decision" not in rejected
    writes, writer = recorder()
    publish_validation_result(
        spec, result, protocol, folds, tmp_path,
        verify_coverage=lambda *args: {"status": "PASS", "folds": []},
        write_json=writer,
    )
    summary = writes[-1][1]
    assert summary["all_folds_selected"] is True
    assert summary["sample_complete"] is True
    assert summary["qualification_status"] == "SAMPLE_COMPLETE_BUT_NOT_QUALIFIED"
    assert summary["passes_hard_gates"] is False
    assert summary["validation_fold_rows"][0]["decisions"] == ["SELECT", None]


@pytest.mark.parametrize("counts,minimum,expected", [
    ([30], 31, False),
    ([31], 31, True),
    ([30, 29], 30, False),
    ([], 30, False),
])
def test_sample_completeness_uses_frozen_threshold_for_every_candidate(
    tmp_path, counts, minimum, expected,
):
    spec, result, protocol, folds, candidate = publication_case(tmp_path)
    protocol["selection_contract"] = freeze_selection_contract(
        min_validation_trades=minimum, min_validation_sharpe=0,
    )
    result.inner_results[0].candidate_metrics = [
        {**candidate, "val_metrics": {"total_trades": count}} for count in counts
    ]
    writes, writer = recorder()
    publish_validation_result(
        spec, result, protocol, folds, tmp_path,
        verify_coverage=lambda *args: {"status": "PASS", "folds": []},
        write_json=writer,
    )
    assert writes[-1][1]["sample_complete"] is expected
    assert writes[-1][1]["passes_hard_gates"] is False
    assert writes[-1][1]["live_authorized"] is False


def test_incomplete_publication_never_claims_complete_or_selected(tmp_path):
    spec, result, protocol, folds, candidate = publication_case(tmp_path)
    # A failed cell has no report; preserve that observation, do not invent one.
    candidate["validation_source"]["report_path"] = str(tmp_path / "missing.json")
    coverage = {"status": "INCOMPLETE", "reason": "failed_cells",
                "failed_cells": [{"cell_id": "fixture-cell"}],
                "folds": [{"fold_id": "fold_000", "decision": None}]}
    writes, writer = recorder()
    assert publish_validation_result(
        spec, result, protocol, folds, tmp_path,
        verify_coverage=lambda *args: coverage, write_json=writer,
    ) is result
    assert [name for name, _ in writes] == [
        "terminal_INCOMPLETE.json", "exact_coverage.json", "parallel_canonical_summary.json",
    ]
    assert writes[0][1] == writes[1][1] == coverage
    summary = writes[-1][1]
    assert summary["verdict"] == "VALIDATION_INCOMPLETE"
    assert summary["qualification_status"] == "NOT_QUALIFIED_VALIDATION_ONLY"
    assert summary["sample_complete"] is False
    assert summary["all_folds_selected"] is False
    assert summary["live_authorized"] is False


def test_report_hash_failure_prevents_receipts_and_coverage(tmp_path):
    spec, result, protocol, folds, candidate = publication_case(tmp_path)
    candidate["validation_source"]["report_sha256"] = "wrong"
    writes, writer = recorder()

    def forbidden(*args):
        pytest.fail("coverage must not run after raw-report hash failure")

    with pytest.raises(ValueError, match="Report SHA256 mismatch"):
        publish_validation_result(
            spec, result, protocol, folds, tmp_path,
            verify_coverage=forbidden, write_json=writer,
        )
    assert writes == []


def test_coverage_failure_does_not_publish_terminal_success(tmp_path):
    spec, result, protocol, folds, _ = publication_case(tmp_path)
    writes, writer = recorder()
    failure = ValueError("mixed packet has corrupt completed evidence")

    def reject(*args):
        raise failure

    with pytest.raises(ValueError) as caught:
        publish_validation_result(
            spec, result, protocol, folds, tmp_path,
            verify_coverage=reject, write_json=writer,
        )
    assert caught.value is failure
    # Historical order retained: per-cell receipts precede the coverage gate.
    assert [name for name, _ in writes] == ["started.json", "result.json"]
