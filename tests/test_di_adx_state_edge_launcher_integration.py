"""Integration through the VALIDATION LAUNCHER path (CLI), not via helpers.

The reviewer required an integration that drives the launcher end to end:
the CLI parses the geometry, freezes the protocol, dispatches the worker, and
produces the terminal coverage receipts. The earlier integration called
``runner._run_measured(...)`` directly, which bypassed the CLI and
``QualificationCellRunner.__call__`` — and therefore could not catch the
registered command running the wrong geometry.

Two properties are proved here:

  L1  the CLI with the registered geometry produces EXACTLY the five frozen
      inner-validation windows and the 15 planned inner cells, and the same CLI
      with the DEFAULT ``--val-months`` produces different windows — so the pin
      in the registered command is load-bearing, not decorative.

  L2  the validation launcher completes: the worker runs the planned cells and
      the terminal receipts appear — ``execution_finished.json`` COMPLETED,
      ``parallel_canonical_summary.json`` verdict VALIDATION_COMPLETE,
      ``exact_coverage.json`` status PASS.

Synthetic data only: a generated parquet bound through the frozen-research-data
mechanism, so the real loader reads the synthetic file and no market data is
touched. No network, no campaign, no broker.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, ".")

from scripts import run_wfo_parallel as worker

SCOPE_TOKEN = "bnb_di_adx_stateedge_prereg_20261020_01"
CANDIDATE_ID = "di_adx_state_edge"
SYMBOL = "BNB/USDT"
TIMEFRAME = "4h"
BAR_HOURS = 4   # must match the timeframe the CLI is given (4h = 180 bars/month)
MONTH_BARS = 180

# The frozen inner-validation windows of the control (5 folds, 6-month
# validation, 3-month step). These are the windows the registered command must
# reproduce.
FROZEN_WINDOWS = [(2360, 3440), (2900, 3980), (3440, 4520),
                   (3980, 5060), (4520, 5600)]


def _synthetic_bars(bars: int, *, amplitude: float = 0.0055, leg: int = 15,
                    trade_from: int = 0) -> pl.DataFrame:
    """Gap-free 4h synthetic OHLCV (the interval the CLI is told to expect).

    A saw-tooth with 15-bar legs and ~8 % amplitude: the DI sign flips every
    leg, so the bull state turns on and off roughly every 30 bars, which is
    what a validation fold needs in order to produce enough trades to select a
    candidate (the selection contract requires 30).

    Bars before ``trade_from`` are FLAT: the cell still walks them (the
    simulation window starts at bar 0) but a flat price keeps ADX near zero, so
    the state never turns on and no order is written. Without that, the cell
    spends its whole budget on orders in the pre-validation region and hits the
    cell timeout, which makes the integration measure the timeout instead of the
    coverage.
    """
    rows = []
    price = 100.0
    base = datetime(2023, 1, 1)
    for i in range(bars):
        if i < trade_from:
            drift = 0.0
        else:
            drift = amplitude if (i // leg) % 2 == 0 else -amplitude
        price *= 1.0 + drift
        rows.append({
            "timestamp": base + timedelta(hours=BAR_HOURS * i),
            "open": price,
            "high": price * 1.002,
            "low": price * 0.998,
            "close": price,
            "volume": 1000.0 + (i % 17),
        })
    return pl.DataFrame(rows)


def _bind(tmp_path: Path, bars: int, monkeypatch, trade_from: int = 0) -> None:
    """Write the synthetic parquet and bind it as the frozen research input."""
    frame = _synthetic_bars(bars, trade_from=trade_from)
    path = tmp_path / "synthetic.parquet"
    frame.write_parquet(path)
    binding = {
        "path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "exchange": "binance", "symbol": SYMBOL, "timeframe": TIMEFRAME,
    }
    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", json.dumps(binding))



def _approved_synthetic_packet(tmp_path, monkeypatch, *, input_sha256, windows, costs,
                              cell_timeout, total_timeout, max_rss_mb):
    """Build a SYNTHETIC approved preregistration packet and point the
    launcher at it.

    The registered packet stays untouched and keeps its PENDING approval. The
    synthetic packet exists only for this integration, under a temporary
    preregistration root, so the binding gate is exercised on the real path
    with a real approval instead of being bypassed.
    """
    import json
    import shutil

    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    from trading_agent.backtest.preregistration_binding import (
        preregistration_fingerprint,
    )

    relative = "data/bnb_di_adx_stateedge_prereg_20261020_01"
    root = tmp_path / "prereg_root"
    destination = root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(Path(".") / relative, destination)

    payload = json.loads((destination / "preregistration_payload.json").read_text())
    request = payload["data"]["scope_change_request"]
    request["approval_state"] = "APPROVED"
    request["approved_scope"] = request["expected_approved_scope"]
    request["approved_by"] = "synthetic-integration-approver"
    request["approved_at"] = "2026-10-21T00:00:00+00:00"
    payload["data"]["sha256"] = input_sha256
    payload["geometry"]["inner_validation_windows"] = windows
    payload["costs"] = costs
    payload["budget"]["cell_timeout_seconds"] = cell_timeout
    payload["budget"]["total_timeout_seconds"] = total_timeout
    payload["budget"]["max_rss_mb"] = max_rss_mb
    payload["budget"]["max_retries"] = 0
    payload["identity_A_source_fingerprint"] = capture_campaign_source(
        Path(".").resolve()
    )["tree_fingerprint"]
    (destination / "preregistration_payload.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True)
    )
    manifest = json.loads((destination / "manifest.json").read_text())
    manifest["identity_B_preregistration_fingerprint"] = preregistration_fingerprint(payload)
    manifest["identity_A_source_fingerprint"] = payload["identity_A_source_fingerprint"]
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2))
    monkeypatch.setenv("TRADING_PREREGISTRATION_ROOT", str(root))
    return root

def _run_cli(monkeypatch, argv: list[str]) -> int:
    monkeypatch.setattr(sys, "argv", ["run_wfo_parallel.py", "--synthetic-test-only", *argv])
    return worker.main()


def _protocol_folds(out_root: Path) -> list[tuple[int, int]]:
    protocol = json.loads((out_root / "research_protocol.json").read_text())
    return [(f["inner_val_start"], f["inner_val_end"]) for f in protocol["folds"]]


def test_l1_cli_with_the_registered_geometry_reproduces_the_frozen_windows(
    tmp_path, monkeypatch,
):
    """L1 — the CLI itself must produce the five frozen windows and 15 cells."""
    _bind(tmp_path, 6795, monkeypatch)
    out_root = tmp_path / "packet"
    out_root.mkdir()
    _run_cli(monkeypatch, [
        "--strategy", CANDIDATE_ID, "--symbol", SYMBOL, "--timeframe", TIMEFRAME,
        "--cost", "all", "--workers", "1",
        "--cell-timeout", "60", "--total-timeout", "600", "--max-rss-mb", "2048",
        "--max-retries", "0", "--scope-token", SCOPE_TOKEN,
        "--train-months", "12", "--val-months", "6", "--test-months", "3",
        "--step-months", "3",
        "--preflight-only", "--out", str(out_root),
    ])
    # main() returns None on this path; the frozen protocol is the signal
    assert (out_root / "research_protocol.json").exists()

    protocol = json.loads((out_root / "research_protocol.json").read_text())
    windows = [(f["inner_val_start"], f["inner_val_end"]) for f in protocol["folds"]]
    assert windows == FROZEN_WINDOWS, (
        f"the CLI did not reproduce the frozen windows: {windows}"
    )
    assert len(protocol["expected_inner_cells"]) == 15, (
        f"expected 15 planned inner cells, got {len(protocol['expected_inner_cells'])}"
    )
    # the planned cells must cover the frozen windows
    for entry in protocol["expected_inner_cells"]:
        assert tuple(entry["measurement_window"]) in FROZEN_WINDOWS, entry


def test_l1_default_val_months_would_produce_different_windows(tmp_path, monkeypatch):
    """L1b — the pin is load-bearing: the CLI default does NOT give the frozen
    windows, so a registered command that omits ``--val-months 6`` is wrong."""
    _bind(tmp_path, 6795, monkeypatch)
    out_root = tmp_path / "packet_default"
    out_root.mkdir()
    _run_cli(monkeypatch, [
        "--strategy", CANDIDATE_ID, "--symbol", SYMBOL, "--timeframe", TIMEFRAME,
        "--cost", "all", "--workers", "1",
        "--cell-timeout", "60", "--total-timeout", "600", "--max-rss-mb", "2048",
        "--max-retries", "0", "--scope-token", SCOPE_TOKEN,
        "--train-months", "12", "--test-months", "3", "--step-months", "3",
        "--preflight-only", "--out", str(out_root),
    ])
    assert (out_root / "research_protocol.json").exists()
    windows = _protocol_folds(out_root)
    assert windows != FROZEN_WINDOWS, (
        "the default geometry happens to match the frozen windows — the pin "
        "would then be untested"
    )


@pytest.mark.slow
def test_l2_validation_launcher_completes_with_terminal_coverage(tmp_path, monkeypatch):
    """L2 — the launcher runs the planned cells and emits the terminal receipts."""
    # enough bars for exactly one 6-month validation fold (train 12m + purge +
    # embargo + val 6m), so the check stays a bounded synthetic run
    # flat before the validation window (starts at bar 2360), oscillatory inside
    # it, so the cell writes orders only where the selection contract looks
    _bind(tmp_path, 4500, monkeypatch, trade_from=2360)
    input_sha = hashlib.sha256((tmp_path / "synthetic.parquet").read_bytes()).hexdigest()
    _approved_synthetic_packet(
        tmp_path, monkeypatch,
        input_sha256=input_sha,
        windows=[[2360, 3440]],
        costs={"1x": {"fee_multiplier": 1.0, "slippage_multiplier": 1.0}},
        cell_timeout=600, total_timeout=1200, max_rss_mb=2048,
    )
    out_root = tmp_path / "packet_run"
    out_root.mkdir()
    _run_cli(monkeypatch, [
        "--strategy", CANDIDATE_ID, "--symbol", SYMBOL, "--timeframe", TIMEFRAME,
        "--cost", "1x", "--workers", "1",
        "--cell-timeout", "600", "--total-timeout", "1200", "--max-rss-mb", "2048",
        "--max-retries", "0", "--scope-token", SCOPE_TOKEN,
        "--train-months", "12", "--val-months", "6", "--test-months", "3",
        "--step-months", "3",
        "--validation-only", "--no-real-sensitivity", "--out", str(out_root),
    ])

    finished = json.loads((out_root / "execution_finished.json").read_text())
    assert finished["status"] == "COMPLETED", finished
    assert finished.get("live_authorized") is False

    summary = json.loads((out_root / "parallel_canonical_summary.json").read_text())
    assert summary["verdict"] == "VALIDATION_COMPLETE", summary
    assert summary["validation_only"] is True, summary
    assert summary["evidence_class"] == "SYNTHETIC_TEST_ONLY"

    coverage = json.loads((out_root / "exact_coverage.json").read_text())
    assert coverage["status"] == "PASS", coverage
    assert coverage["evidence_class"] == "SYNTHETIC_TEST_ONLY"
    assert coverage["protocol_fingerprint"] == summary["protocol_fingerprint"], coverage

    protocol = json.loads((out_root / "research_protocol.json").read_text())
    planned = len(protocol["expected_inner_cells"])
    executed = len(list(out_root.rglob("cell_execution/*/result.json")))
    assert executed == planned, f"{executed} results for {planned} planned cells"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
