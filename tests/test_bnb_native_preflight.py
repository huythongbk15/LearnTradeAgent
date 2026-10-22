"""Synthetic-only singleton preflight isolation and scope checks."""
import hashlib
import json
from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from scripts import preflight_bnb_native_validation as adapter


def test_fixed_spec_never_uses_default_grid():
    spec = adapter.fixed_spec()
    assert (spec.strategy_id, spec.symbol, spec.timeframe) == ("ma_adx", "BNB/USDT", "4h")
    assert spec.param_grid == {"fast_period":[20],"slow_period":[80],"adx_period":[14],"adx_threshold":[25]}
    assert [c.name for c in spec.cost_scenarios] == ["1x", "2x", "slip_stress"]
    assert (spec.train_months, spec.val_months, spec.test_months, spec.step_months) == (12,6,3,3)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    from scripts import run_wfo_parallel as worker
    from trading_agent.alpha_research import holdout
    monkeypatch.setattr(adapter, "ROOT", tmp_path)
    data = tmp_path / "data"
    data.mkdir()
    path = data / "fake.parquet"
    times = [datetime(2023,1,1)+timedelta(hours=i*4) for i in range(6795)]
    pl.DataFrame({"timestamp":times}).write_parquet(path)
    manifest = data / "research_manifest.json"
    manifest.write_text('{"fixture": true}')
    monkeypatch.setattr(adapter, "DATASET", path)
    monkeypatch.setattr(adapter, "DATASET_SHA", hashlib.sha256(path.read_bytes()).hexdigest())
    monkeypatch.setattr(adapter, "MANIFEST_SHA", hashlib.sha256(manifest.read_bytes()).hexdigest())
    monkeypatch.setattr(holdout, "load_manifest", lambda p: {})
    monkeypatch.setattr(holdout, "holdout_window", lambda m: (datetime(2030,1,1,tzinfo=UTC),None))
    seen = {}
    def prepare(spec, root, **kwargs):
        seen.update(kwargs, spec=spec, binding=json.loads(__import__("os").environ["TRADING_FROZEN_RESEARCH_DATA"]))
        return {"fingerprint":"a"*64,"expected_inner_cells":[{}]*15}, [None]*5
    monkeypatch.setattr(worker, "prepare_parallel_protocol", prepare)
    return data / "packet", seen, path


def test_preflight_pins_singleton_budget_and_never_executes(fixture, monkeypatch):
    root, seen, path = fixture
    monkeypatch.delenv("TRADING_FROZEN_RESEARCH_DATA", raising=False)
    adapter.preflight(root)
    assert seen["workers"] == 1 and seen["cell_timeout"] == 90
    assert seen["validation_budget"] == {"total_timeout_seconds":1800,"max_rss_mb":2048}
    assert seen["binding"]["path"] == str(path.resolve())
    assert seen["spec"].param_grid["adx_threshold"] == [25]
    assert "TRADING_FROZEN_RESEARCH_DATA" not in __import__("os").environ
    result = json.loads((root / "preflight_terminal.json").read_text())
    assert result["cells_executed"] == 0 and result["status"] == "PREFLIGHT_PASS_EXECUTION_NOT_AUTHORIZED"
    assert not list(root.rglob("report.json"))


def test_changed_data_rejected_before_output(fixture):
    root, seen, path = fixture
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="dataset identity"):
        adapter.preflight(root)
    assert not root.exists() and not seen


def test_existing_packet_not_modified(fixture):
    root, seen, path = fixture
    root.mkdir()
    marker = root / "keep.txt"
    marker.write_text("old")
    with pytest.raises(FileExistsError):
        adapter.preflight(root)
    assert marker.read_text() == "old" and not seen


def test_failure_retains_terminal_and_restores_binding(fixture, monkeypatch):
    from scripts import run_wfo_parallel as worker
    root, seen, path = fixture
    monkeypatch.delenv("TRADING_FROZEN_RESEARCH_DATA", raising=False)
    def fail(*args, **kwargs):
        raise ValueError("quality rejected")
    monkeypatch.setattr(worker, "prepare_parallel_protocol", fail)
    with pytest.raises(ValueError, match="quality rejected"):
        adapter.preflight(root)
    assert "TRADING_FROZEN_RESEARCH_DATA" not in __import__("os").environ
    assert json.loads((root / "preflight_terminal.json").read_text())["status"] == "PREFLIGHT_FAILED_DO_NOT_EXECUTE"


def test_changed_binding_sha_never_reaches_prepare(fixture, monkeypatch):
    from contextlib import contextmanager
    from trading_agent.data import storage
    root, seen, path = fixture
    @contextmanager
    def changed(*args, **kwargs):
        yield {"sha256":"b"*64}
    monkeypatch.setattr(storage, "frozen_research_data", changed)
    with pytest.raises(ValueError, match="dataset changed before freeze"):
        adapter.preflight(root)
    assert not seen
    terminal = json.loads((root / "preflight_terminal.json").read_text())
    assert terminal["cells_executed"] == 0 and terminal["status"] == "PREFLIGHT_FAILED_DO_NOT_EXECUTE"


def test_manifest_change_during_prepare_is_terminal_failure(fixture, monkeypatch):
    from scripts import run_wfo_parallel as worker
    root, seen, path = fixture
    original = worker.prepare_parallel_protocol
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        (adapter.ROOT / "data/research_manifest.json").write_text('{"changed":true}')
        return result
    monkeypatch.setattr(worker, "prepare_parallel_protocol", changed)
    with pytest.raises(ValueError, match="manifest changed during freeze"):
        adapter.preflight(root)
    assert json.loads((root / "preflight_terminal.json").read_text())["status"] == "PREFLIGHT_FAILED_DO_NOT_EXECUTE"
