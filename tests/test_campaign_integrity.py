"""Campaign integrity: a run must measure what it claimed to measure.

`a65ed29000` fixed three WFO pipeline bugs. The first injected
`atr_sl_mult` / `atr_tp_mult` into strategies whose parameter schema does
not define them, so those cells raised `ParamValidationError` and produced
nothing, while the campaign reported `completed: 189, failed: 0` — the
count was of artifacts written, not strategies asked for. Nothing compared
the two, which is why the failure was silent and why a sign-off could quote
figures for strategies with no artifacts at all.

These tests pin the check that closes it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from trading_agent.backtest.campaign_integrity import (
    verify_campaign_coverage,
)


def _cell(root: Path, strategy: str, report: bool = True) -> None:
    # Symbol without a slash: campaign cell directories are
    # `<strategy>__<SYMBOL><tf>__<cost>__...` and a slash would create a
    # nested directory, which is not how the real runners name them.
    d = root / f"{strategy}__SOLUSDT__1h__1x__pabc__w123"
    d.mkdir(parents=True, exist_ok=True)
    if report:
        (d / "report.json").write_text(json.dumps({"summary": {"total_return_pct": 1.0}}))


def test_full_coverage_passes(tmp_path):
    _cell(tmp_path, "alpha")
    _cell(tmp_path, "beta")
    report = verify_campaign_coverage(
        requested_strategies=["alpha", "beta"], out_root=tmp_path
    )
    assert report.ok
    assert report.produced == 2
    assert report.per_strategy == {"alpha": 1, "beta": 1}


def test_strategy_with_no_cells_is_reported(tmp_path):
    # The silent-drop case: beta was asked for and nothing was written for it.
    _cell(tmp_path, "alpha")
    report = verify_campaign_coverage(
        requested_strategies=["alpha", "beta"], out_root=tmp_path
    )
    assert not report.ok
    assert report.empty_strategies == ("beta",)
    assert "zero cells written" in report.summary()


def test_result_present_but_no_cells_is_empty_not_missing(tmp_path):
    _cell(tmp_path, "alpha")
    results = [
        {"strategy_id": "alpha", "status": "PASS"},
        {"strategy_id": "beta", "status": "PASS"},
    ]
    report = verify_campaign_coverage(
        requested_strategies=["alpha", "beta"], out_root=tmp_path, results=results
    )
    # beta returned PASS but wrote nothing — that is the bug-1 signature and
    # is deliberately distinguished from a strategy that never returned.
    assert report.empty_strategies == ("beta",)
    assert report.missing_strategies == ()


@pytest.mark.parametrize("status", ["ERROR", "FAILED", "TIMEOUT"])
def test_error_result_counts_as_missing(tmp_path, status):
    _cell(tmp_path, "alpha")
    results = [
        {"strategy_id": "alpha", "status": "PASS"},
        {"strategy_id": "beta", "status": status},
    ]
    report = verify_campaign_coverage(
        requested_strategies=["alpha", "beta"], out_root=tmp_path, results=results
    )
    assert report.missing_strategies == ("beta",)


def test_unknown_status_with_no_cells_is_still_empty(tmp_path):
    _cell(tmp_path, "alpha")
    results = [
        {"strategy_id": "alpha", "status": "PASS"},
        {"strategy_id": "beta", "status": "NO_TRADE"},
    ]
    report = verify_campaign_coverage(
        requested_strategies=["alpha", "beta"], out_root=tmp_path, results=results
    )
    # NO_TRADE is a verdict, not a failure, but it still owes cells.
    assert report.empty_strategies == ("beta",)
    assert report.missing_strategies == ()


def test_missing_output_root_is_not_a_pass(tmp_path):
    report = verify_campaign_coverage(
        requested_strategies=["alpha"], out_root=tmp_path / "does_not_exist"
    )
    assert not report.ok
    assert report.empty_strategies == ("alpha",)


def test_unrequested_cells_are_flagged(tmp_path):
    # Stale output from a previous run must not be mistaken for coverage.
    _cell(tmp_path, "alpha")
    _cell(tmp_path, "leftover")
    report = verify_campaign_coverage(
        requested_strategies=["alpha"], out_root=tmp_path
    )
    assert report.ok
    assert report.produced == 1
    assert any("leftover" in n for n in report.notes)


def test_real_campaign_directory_is_audited():
    """The 104-cell enhanced_ma run should pass this check.

    It ran a single strategy on a single pipeline, so the silent-drop
    failure mode did not apply — which is also why it is the one campaign
    whose numbers this repository can still cite.
    """
    root = Path("data/wfo_real_campaign")
    if not root.exists():
        pytest.skip("campaign artifacts not present")
    report = verify_campaign_coverage(
        requested_strategies=["enhanced_ma"], out_root=root
    )
    assert report.ok
    assert report.produced > 0
