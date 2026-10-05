"""Script-level tests for the multi-strategy campaign entrypoint.

These run the real entrypoint as a subprocess rather than importing it. The
gap being closed is precisely that nothing exercised the script: the plan,
producer and publisher chain was never driven end to end, which is how a
bundle could be complete and unpublished without anything failing.

Dry-run is the default, so these exercise plan freezing, refusal messages and
argument handling without running backtests. The publish path is covered
separately where it can be without a full campaign.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run_real_wfo_campaign.py"


def write_bars(path: Path, bars: int = 3600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    start = datetime(2024, 1, 1, tzinfo=UTC)
    frame = pl.DataFrame(
        {
            "timestamp": [start + timedelta(hours=i) for i in range(bars)],
            "open": [100.0 + i * 0.05 for i in range(bars)],
            "high": [101.0 + i * 0.05 for i in range(bars)],
            "low": [99.0 + i * 0.05 for i in range(bars)],
            "close": [100.4 + i * 0.05 for i in range(bars)],
            "volume": [10.0] * bars,
        }
    )
    frame.write_parquet(path)
    return path


def run_script(*args: str, timeout: int = 300) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def test_data_file_is_required(tmp_path: Path) -> None:
    # A plan without a data binding would not say what the campaign ran on,
    # so the entrypoint refuses rather than freezing a vague plan.
    result = run_script("--dry-run")
    assert result.returncode != 0
    assert "--data-file is required" in result.stdout + result.stderr


def test_missing_data_file_is_refused(tmp_path: Path) -> None:
    result = run_script("--dry-run", "--data-file", str(tmp_path / "nope.parquet"))
    assert result.returncode != 0
    assert "data file not found" in result.stdout + result.stderr


def test_dry_run_freezes_a_plan_for_every_strategy(tmp_path: Path) -> None:
    bars = write_bars(tmp_path / "bars.parquet")
    result = run_script(
        "--dry-run",
        "--data-file",
        str(bars),
        "--strategies",
        "enhanced_ma,ma_crossover",
        "--train-months",
        "1",
        "--val-months",
        "1",
        "--test-months",
        "1",
        "--n-outer",
        "2",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for strategy in ("enhanced_ma", "ma_crossover"):
        assert strategy in result.stdout
    assert result.stdout.count("frozen plan") == 2, (
        "each strategy needs its own frozen plan"
    )
    # Dry-run must not leave a plan behind on disk.
    assert not (ROOT / "data" / "wfo_real_campaign").exists() or not list(
        (ROOT / "data" / "wfo_real_campaign").glob("*/campaign_plan.json")
    )


def test_too_few_bars_is_refused_rather_than_campaigned(tmp_path: Path) -> None:
    # Running a plan with fewer bars than the folds need would silently
    # produce fewer folds than the plan promised.
    bars = write_bars(tmp_path / "small.parquet", bars=48)
    result = run_script(
        "--dry-run",
        "--data-file",
        str(bars),
        "--train-months",
        "9",
        "--val-months",
        "9",
        "--test-months",
        "9",
    )
    assert result.returncode != 0
    assert "too few" in (result.stdout + result.stderr)


def test_empty_strategy_list_is_refused(tmp_path: Path) -> None:
    bars = write_bars(tmp_path / "bars.parquet")
    result = run_script("--dry-run", "--data-file", str(bars), "--strategies", " , ")
    assert result.returncode != 0
    assert "no strategy requested" in result.stdout + result.stderr


def test_gate_set_override_is_read_from_disk(tmp_path: Path) -> None:
    # A campaign must be able to pre-declare its own thresholds; that is the
    # point of freezing them.
    bars = write_bars(tmp_path / "bars.parquet")
    gates = [
        {
            "gate_id": "custom_sharpe",
            "metric": "aggregate.median_sharpe",
            "comparison": ">",
            "threshold": 7.5,
            "unit": "ratio",
        }
    ]
    gate_file = tmp_path / "gates.json"
    gate_file.write_text(json.dumps(gates))
    result = run_script(
        "--dry-run",
        "--data-file",
        str(bars),
        "--gate-set",
        str(gate_file),
        "--train-months",
        "1",
        "--val-months",
        "1",
        "--test-months",
        "1",
        "--n-outer",
        "2",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "custom_sharpe" in result.stdout


@pytest.mark.parametrize("flag", ["--no-publish"])
def test_unknown_flag_is_rejected(tmp_path: Path, flag: str) -> None:
    bars = write_bars(tmp_path / "bars.parquet")
    result = run_script("--dry-run", "--data-file", str(bars), flag, "--bogus")
    assert result.returncode != 0