from __future__ import annotations

import os
import subprocess
import sys
from types import SimpleNamespace


SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import live_cron_runner


def test_child_failure_is_propagated(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["live_cron_runner.py"])
    monkeypatch.setattr(
        live_cron_runner.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=7, stdout="failed", stderr=""),
    )
    monkeypatch.setattr(live_cron_runner, "send_telegram", lambda text: True)
    assert live_cron_runner.main() == 7


def test_child_timeout_is_failure(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["live_cron_runner.py"])

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], 180, output="partial", stderr="")

    monkeypatch.setattr(live_cron_runner.subprocess, "run", timeout)
    monkeypatch.setattr(live_cron_runner, "send_telegram", lambda text: True)
    assert live_cron_runner.main() == 124
