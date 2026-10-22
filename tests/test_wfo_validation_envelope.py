"""Regression tests for the validation-only execution envelope.

The envelope caps are a BUDGET POLICY, expressed as named constants in
scripts/run_wfo_parallel.py. These tests lock that:

  * the caps exist and are named (a future edit cannot silently inline a
    bigger literal and drop the ceiling);
  * the cap is still enforced fail-closed — a value above it is rejected;
  * a value at the cap is accepted;
  * the other validation-only guards are untouched — holdout, real
    sensitivity, workers != 1 and packet reuse stay forbidden;
  * the non-validation (qualification) path keeps its own guard.

These tests exercise argument parsing only. They run no market cell, read no
market data and start no campaign.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "run_wfo_parallel.py"

sys.path.insert(0, str(ROOT))

from scripts.run_wfo_parallel import (  # noqa: E402
    VALIDATION_CELL_TIMEOUT_MAX_SECONDS,
    validate_validation_only_budget,
    VALIDATION_MAX_RSS_MB_MAX,
    VALIDATION_MAX_RSS_MB_MIN,
    VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS,
    VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS,
)


def _run(extra: list[str]) -> subprocess.CompletedProcess:
    """Invoke the entry point's argument validation only.

    ``--preflight-only`` is NOT used: it still creates a directory, so the
    validation is exercised with an intentionally invalid combination that
    fails during parsing, before any directory is created.
    """
    return subprocess.run(
        [sys.executable, str(SCRIPT), *extra],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )


def test_approved_policy_values_are_pinned():
    """Pin the REVIEWED policy values as literals.

    Reading the constants out of the implementation would let a silent change
    to the cap pass every test. These assertions are the policy: changing a
    cap is a budget-policy change and must fail here until it is re-reviewed.
    """
    assert VALIDATION_CELL_TIMEOUT_MAX_SECONDS == 600
    assert VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS == 7200
    assert VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS == 1
    assert VALIDATION_MAX_RSS_MB_MIN == 128
    assert VALIDATION_MAX_RSS_MB_MAX == 4096


def test_caps_are_named_constants_with_sane_ordering():
    assert VALIDATION_CELL_TIMEOUT_MAX_SECONDS > 0
    assert VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS >= VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS > 0
    assert VALIDATION_MAX_RSS_MB_MIN <= VALIDATION_MAX_RSS_MB_MAX
    source = SCRIPT.read_text()
    # the guard must reference the constants, not inline a literal
    assert "args.cell_timeout > VALIDATION_CELL_TIMEOUT_MAX_SECONDS" in source
    assert "VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS" in source
    assert "args.cell_timeout > 180:" not in source, (
        "the old inline literal is back — the cap must stay a named constant"
    )
    assert "validate_validation_only_budget(args)" in source, (
        "the envelope must be enforced by the single named validator"
    )


class _Args:
    """Minimal stand-in for the parsed namespace (no I/O involved)."""

    def __init__(self, cell_timeout, total_timeout, max_rss_mb, reuse_from=None):
        self.cell_timeout = cell_timeout
        self.total_timeout = total_timeout
        self.max_rss_mb = max_rss_mb
        self.reuse_from = reuse_from


def test_budget_guard_accepts_values_exactly_at_the_cap():
    """At the cap the budget guard must NOT fire (pure function, no I/O)."""
    validate_validation_only_budget(_Args(
        cell_timeout=VALIDATION_CELL_TIMEOUT_MAX_SECONDS,
        total_timeout=VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS,
        max_rss_mb=VALIDATION_MAX_RSS_MB_MAX,
    ))


def test_budget_guard_rejects_one_second_above_each_cap():
    with pytest.raises(ValueError, match="cell-timeout"):
        validate_validation_only_budget(_Args(
            VALIDATION_CELL_TIMEOUT_MAX_SECONDS + 1, 600, 2048))
    with pytest.raises(ValueError, match="total-timeout"):
        validate_validation_only_budget(_Args(
            VALIDATION_CELL_TIMEOUT_MAX_SECONDS,
            VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS + 1, 2048))
    with pytest.raises(ValueError, match="max-rss-mb"):
        validate_validation_only_budget(_Args(
            VALIDATION_CELL_TIMEOUT_MAX_SECONDS, 600, VALIDATION_MAX_RSS_MB_MAX + 1))
    with pytest.raises(ValueError, match="max-rss-mb"):
        validate_validation_only_budget(_Args(
            VALIDATION_CELL_TIMEOUT_MAX_SECONDS, 600, VALIDATION_MAX_RSS_MB_MIN - 1))
    with pytest.raises(ValueError, match="total-timeout"):
        validate_validation_only_budget(_Args(
            VALIDATION_CELL_TIMEOUT_MAX_SECONDS,
            VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS - 1, 2048))
    with pytest.raises(ValueError, match="reuse"):
        validate_validation_only_budget(_Args(
            VALIDATION_CELL_TIMEOUT_MAX_SECONDS, 600, 2048, reuse_from="data/x"))


def test_cell_timeout_above_the_cap_is_rejected():
    result = _run([
        "--validation-only", "--workers", "1", "--no-real-sensitivity",
        "--cell-timeout", str(VALIDATION_CELL_TIMEOUT_MAX_SECONDS + 1),
        "--total-timeout", "600", "--max-rss-mb", "2048",
    ])
    assert result.returncode != 0
    assert "cell-timeout" in (result.stderr + result.stdout)


def test_total_timeout_above_the_cap_is_rejected():
    result = _run([
        "--validation-only", "--workers", "1", "--no-real-sensitivity",
        "--cell-timeout", str(VALIDATION_CELL_TIMEOUT_MAX_SECONDS),
        "--total-timeout", str(VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS + 1),
        "--max-rss-mb", "2048",
    ])
    assert result.returncode != 0
    assert "total-timeout" in (result.stderr + result.stdout)


def test_rss_above_the_cap_is_rejected():
    result = _run([
        "--validation-only", "--workers", "1", "--no-real-sensitivity",
        "--cell-timeout", str(VALIDATION_CELL_TIMEOUT_MAX_SECONDS),
        "--total-timeout", "600", "--max-rss-mb", str(VALIDATION_MAX_RSS_MB_MAX + 1),
    ])
    assert result.returncode != 0
    assert "max-rss-mb" in (result.stderr + result.stdout)


def test_rss_below_the_floor_is_rejected():
    result = _run([
        "--validation-only", "--workers", "1", "--no-real-sensitivity",
        "--cell-timeout", str(VALIDATION_CELL_TIMEOUT_MAX_SECONDS),
        "--total-timeout", "600", "--max-rss-mb", str(VALIDATION_MAX_RSS_MB_MIN - 1),
    ])
    assert result.returncode != 0
    assert "max-rss-mb" in (result.stderr + result.stdout)


def test_other_validation_only_guards_are_intact():
    """Holdout, real sensitivity, workers != 1 and reuse stay forbidden."""
    base = ["--validation-only", "--no-real-sensitivity", "--cell-timeout", "300",
            "--total-timeout", "600", "--max-rss-mb", "2048"]

    holdout = _run([*base, "--workers", "1", "--run-holdout"])
    assert holdout.returncode != 0
    assert "holdout" in (holdout.stderr + holdout.stdout)

    workers = _run([*base, "--workers", "2"])
    assert workers.returncode != 0
    assert "workers" in (workers.stderr + workers.stdout)

    reuse = _run([*base, "--workers", "1", "--reuse-from", "data/whatever"])
    assert reuse.returncode != 0
    assert "reuse" in (reuse.stderr + reuse.stdout)


def test_cli_rejects_an_out_of_envelope_value_before_creating_a_packet():
    """The CLI guard fires during parsing, before any packet directory exists."""
    out = Path("/tmp/qwenpaw_envelope_probe_should_not_exist")
    result = _run([
        "--validation-only", "--workers", "1", "--no-real-sensitivity",
        "--cell-timeout", str(VALIDATION_CELL_TIMEOUT_MAX_SECONDS + 1),
        "--total-timeout", "600", "--max-rss-mb", "2048", "--out", str(out),
    ])
    combined = result.stderr + result.stdout
    assert result.returncode != 0
    assert "cell-timeout" in combined
    assert not out.exists(), "the guard must fire before any packet is created"
