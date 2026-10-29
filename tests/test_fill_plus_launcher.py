"""Real launcher processes and native simulated fills; synthetic prices only."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("arm,context,expected", [
    ("A0", "up", 1), ("A0", "down", 1),
    ("PLUS", "up", 1), ("PLUS", "down", 0), ("PLUS", "missing", 0),
])
def test_real_launcher_plus_context_and_fill_cycle(tmp_path, arm, context, expected):
    env = dict(os.environ)
    env.pop("TRADING_FROZEN_RESEARCH_DATA", None)
    result = subprocess.run([
        sys.executable, "scripts/run_wfo_parallel.py", "--fill-cycle-synthetic",
        "--fill-cycle-arm", arm, "--fill-cycle-btc-context", context,
        "--out", str(tmp_path),
    ], cwd=ROOT, env=env, capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stderr[-3000:] + result.stdout[-3000:]
    summary = json.loads((tmp_path / "summary.json").read_text())
    report = json.loads((tmp_path / "report.json").read_text())
    assert summary["arm"] == arm
    assert summary["btc_context"] == context
    assert summary["evidence_class"] == "SYNTHETIC_TEST_ONLY"
    assert summary["market_run"] is False and summary["live_authorized"] is False
    assert summary["qualification_status"] == "NOT_QUALIFIED"
    assert report["total_trades"] == expected
    assert len(report["trades"]) == expected
    assert report["open_positions"] == 0 and report["open_orders"] == 0
    assert report["measured_accounting"] is not None
    if expected:
        trade = report["trades"][0]
        assert datetime.fromisoformat(trade["exit_time"]) - datetime.fromisoformat(
            trade["entry_time"],
        ) == timedelta(hours=24)
        assert summary["status"] == "SYNTHETIC_FILL_CYCLE_OBSERVED"
    else:
        assert summary["status"] == "SYNTHETIC_CONTEXT_BLOCK_OBSERVED"


def test_fill_cycle_evidence_cannot_be_overwritten(tmp_path):
    from trading_agent.backtest.fill_cycle_synthetic import run_fill_cycle_synthetic

    path = tmp_path / "report.json"
    path.write_text("preserved evidence")
    with pytest.raises(ValueError, match="already contains evidence"):
        run_fill_cycle_synthetic(tmp_path, arm="PLUS", btc_context="up")
    assert path.read_text() == "preserved evidence"


@pytest.mark.parametrize("flag,value", [
    ("--fill-cycle-arm", "PLUS"), ("--fill-cycle-btc-context", "up"),
])
def test_synthetic_selectors_cannot_enter_market_launcher(monkeypatch, tmp_path, flag, value):
    from scripts.run_wfo_parallel import main

    monkeypatch.setattr(sys, "argv", [
        "run_wfo_parallel.py", flag, value, "--out", str(tmp_path),
    ])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2
    assert list(tmp_path.iterdir()) == []


def test_plus_launcher_refuses_frozen_market_binding(monkeypatch, tmp_path):
    from scripts.run_wfo_parallel import main

    monkeypatch.setenv("TRADING_FROZEN_RESEARCH_DATA", '{"path":"market.parquet"}')
    monkeypatch.setattr(sys, "argv", [
        "run_wfo_parallel.py", "--fill-cycle-synthetic", "--fill-cycle-arm", "PLUS",
        "--fill-cycle-btc-context", "up", "--out", str(tmp_path),
    ])
    with pytest.raises(SystemExit) as raised:
        main()
    assert raised.value.code == 2
    assert list(tmp_path.iterdir()) == []
