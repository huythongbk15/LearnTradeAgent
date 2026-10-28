"""Call-time compatibility at the Linux supervision boundary."""

import pytest

from scripts import run_wfo_parallel as launcher
from trading_agent.backtest import validation_supervisor as supervisor


def test_supervisor_adapter_forwards_current_collaborators(monkeypatch, tmp_path):
    callbacks = [object(), object(), object()]
    monkeypatch.setattr(launcher, "_validation_group_rss_mb", callbacks[0])
    monkeypatch.setattr(launcher, "_wait_for_all_reaped", callbacks[1])
    monkeypatch.setattr(launcher, "_validation_group_pids", callbacks[2])
    monkeypatch.setattr(launcher, "ROOT", tmp_path)
    calls = []
    expected = {"status": "TIMEOUT", "worker_exit_code": -9}

    def supervise(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(supervisor, "_supervise_validation", supervise)
    command = ["synthetic-worker"]
    assert launcher._supervise_validation(
        command, tmp_path, total_timeout_seconds=3, max_rss_mb=128,
    ) is expected
    assert calls == [((command, tmp_path), {
        "total_timeout_seconds": 3, "max_rss_mb": 128, "cwd": tmp_path,
        "group_rss_mb": callbacks[0], "wait_for_all_reaped": callbacks[1],
        "group_pids": callbacks[2],
    })]


def test_wait_adapter_resolves_pid_reader_at_call_time(monkeypatch):
    reader = object()
    monkeypatch.setattr(launcher, "_validation_group_pids", reader)
    calls = []

    def wait(group, deadline, *, group_pids):
        calls.append((group, deadline, group_pids))
        return False

    monkeypatch.setattr(supervisor, "_wait_for_all_reaped", wait)
    assert launcher._wait_for_all_reaped(123, 45) is False
    assert calls == [(123, 45, reader)]


def test_cleanup_failure_is_not_converted_to_completed(monkeypatch, tmp_path):
    error = RuntimeError("owned session did not stop")

    def reject(*args, **kwargs):
        raise error

    monkeypatch.setattr(supervisor, "_supervise_validation", reject)
    with pytest.raises(RuntimeError) as caught:
        launcher._supervise_validation([], tmp_path, total_timeout_seconds=1, max_rss_mb=128)
    assert caught.value is error


def test_non_linux_refused_before_creating_worker_logs(monkeypatch, tmp_path):
    monkeypatch.setattr(supervisor.sys, "platform", "win32")
    with pytest.raises(RuntimeError, match="requires Linux"):
        launcher._supervise_validation([], tmp_path, total_timeout_seconds=1, max_rss_mb=128)
    assert not (tmp_path / "worker_stdout.log").exists()
    assert not (tmp_path / "worker_stderr.log").exists()
