"""Profiling tooling safety, not alpha/qualification tests."""
import json
import os
import sys
from pathlib import Path

import pytest

from scripts.strategy_research import profile_synthetic as profile


@pytest.mark.parametrize("kwargs", [
    {"bars": 299}, {"bars": 1201}, {"cell_timeout": 121},
    {"total_timeout": 651}, {"total_timeout": float("nan")},
    {"cell_timeout": float("inf")}, {"memory_mb": 127}, {"memory_mb": 4097},
])
def test_invalid_budget_has_no_output(tmp_path, kwargs):
    out = tmp_path / "new"
    with pytest.raises(ValueError):
        profile.run(out, **kwargs)
    assert not out.exists()


def test_existing_packet_never_overwritten(tmp_path):
    marker = tmp_path / "finished.json"
    marker.write_text("original")
    with pytest.raises(FileExistsError):
        profile.run(tmp_path)
    assert marker.read_text() == "original"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux supervisor")
def test_deadline_stops_actual_worker_and_keeps_logs(tmp_path):
    # A Python worker can take longer than the deadline just to start under
    # parallel test load; ``exec`` keeps the shell's pid for the sleep.
    result = profile.supervise(["sh", "-c", "echo $$; exec sleep 30"], tmp_path,
                               timeout=0.5, memory_mb=512)
    assert result["status"] == "TIMEOUT"
    assert result["wall_seconds"] < 3
    pid = int((tmp_path / "stdout.log").read_text().strip())
    assert not Path(f"/proc/{pid}").exists()
    assert result["attempts"] == 1


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux supervisor")
def test_memory_limit_stops_worker(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "_rss_mb", lambda pid: 600)
    result = profile.supervise([sys.executable, "-c", "import time; time.sleep(30)"],
                               tmp_path, timeout=5, memory_mb=512)
    assert result["status"] == "MEMORY_LIMIT"
    assert result["worker_exit_code"] != 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux supervisor")
def test_monitor_exception_stops_owned_worker(tmp_path, monkeypatch):
    def broken(pid):
        monkeypatch.setenv("PROFILE_TEST_PID", str(pid))
        raise RuntimeError("monitor failed")
    monkeypatch.setattr(profile, "_rss_mb", broken)
    with pytest.raises(RuntimeError, match="monitor failed"):
        profile.supervise([sys.executable, "-c", "import time; time.sleep(30)"],
                          tmp_path, timeout=5, memory_mb=512)
    assert not Path(f"/proc/{os.environ['PROFILE_TEST_PID']}").exists()


def test_failed_cells_retained_not_qualified(tmp_path, monkeypatch):
    monkeypatch.setattr(profile, "identity", lambda: {"source": "test"})
    monkeypatch.setattr(profile, "supervise", lambda *a, **k: {"status": "TIMEOUT", "attempts": 1})
    out = tmp_path / "new"
    assert profile.run(out) == 1
    final = json.loads((out / "finished.json").read_text())
    assert len(final["cells"]) == 5
    assert final["status"] == "FAIL"
    assert final["market_evaluations"] == 0
    assert not final["qualified"] and not final["live_authorized"]
    assert final["campaign_eta_seconds"] is None


def test_drift_has_incomplete_terminal_receipt(tmp_path, monkeypatch):
    snapshots = iter(({"source": "a"}, {"source": "b"}, {"source": "b"}))
    monkeypatch.setattr(profile, "identity", lambda: next(snapshots))
    out = tmp_path / "new"
    with pytest.raises(ValueError, match="drift"):
        profile.run(out)
    final = json.loads((out / "finished.json").read_text())
    assert final["status"] == "INCOMPLETE"
    assert final["identity_after"] != final["identity"]
    assert not final["cells"]
