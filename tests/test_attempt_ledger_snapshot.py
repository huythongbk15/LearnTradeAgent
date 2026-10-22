"""Fast synthetic regressions for the derived evaluation ledger snapshot."""
import json

import pytest

from trading_agent.backtest import attempt_ledger as ledger


def _attempt(tmp_path, name, epoch, status):
    directory = tmp_path / "cell_execution" / name
    directory.mkdir(parents=True)
    (directory / "started.json").write_text(json.dumps({
        "started_at_epoch": epoch, "worker_pid": -1,
    }))
    (directory / "result.json").write_text(json.dumps({
        "artifact": {"status": status},
    }))
    return directory


def test_repeated_snapshot_and_cache_hit_do_not_duplicate_attempts(tmp_path):
    attempt = _attempt(tmp_path, "a", 1, "COMPLETED")
    target = tmp_path / "cell_ledger"
    for cache_hit in (False, True, True):
        path = ledger.write_ledger_row(
            target, "key", {}, [attempt, attempt],
            extra={"cache_hit": cache_hit},
        )
        row = json.loads(path.read_text())
        assert row["attempts"] == 1
        assert row["attempt_outcomes"] == ["COMPLETED"]
        assert row["registry_counted"] is None
        assert row["registry_accounting_status"] == "UNKNOWN_NOT_RECONCILED"


def test_new_attempt_updates_snapshot_without_readding_old_history(tmp_path):
    old = _attempt(tmp_path, "z-old", 1, "FAILED")
    new = _attempt(tmp_path, "a-new", 2, "COMPLETED")
    target = tmp_path / "cell_ledger"
    ledger.write_ledger_row(target, "key", {}, [old])
    path = ledger.write_ledger_row(target, "key", {}, [old, new])
    row = json.loads(path.read_text())
    assert row["attempts"] == 2
    assert row["attempt_outcomes"] == ["COMPLETED", "FAILED"]
    assert row["outcome"] == "COMPLETED"


def test_failed_atomic_replace_preserves_previous_snapshot(tmp_path, monkeypatch):
    attempt = _attempt(tmp_path, "a", 1, "COMPLETED")
    target = tmp_path / "cell_ledger"
    path = ledger.write_ledger_row(target, "key", {}, [attempt])
    before = path.read_bytes()

    def fail_replace(*args):
        raise OSError("synthetic publication failure")

    monkeypatch.setattr(ledger.os, "replace", fail_replace)
    with pytest.raises(OSError, match="publication failure"):
        ledger.write_ledger_row(target, "key", {}, [attempt])
    assert path.read_bytes() == before
    assert not list(target.glob("*.pending"))


def test_atomic_publication_fsyncs_file_and_directory(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ledger.os, "fsync", lambda fd: calls.append(fd))
    path = ledger.write_ledger_row(tmp_path / "cell_ledger", "key", {}, [])
    assert json.loads(path.read_text())["attempts"] == 0
    assert len(calls) == (2 if ledger.os.name == "posix" else 1)
