"""Acceptance bookkeeping regression checks; no evidence gates are relaxed."""

import copy
import json
import os
from types import SimpleNamespace

import pytest

from scripts import run_acceptance_ac01_15 as module


@pytest.mark.parametrize("mode", ["fresh", "stale", "missing"])
def test_only_fresh_raw_evidence_can_certify_a_check(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(module, "EVIDENCE", tmp_path / "raw")
    module.EVIDENCE.mkdir()
    raw = module.EVIDENCE / "ac02_evidence.json"
    out = tmp_path / "out"
    out.mkdir()
    if mode == "stale":
        raw.write_text('{"stale": true}')
        os.utime(raw, (1, 1))
    def execute(*a, **kw):
        if mode == "fresh":
            raw.write_text('{"fresh": true}')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, "run", execute)
    result = module.run_check("ac02", ["synthetic"], raw.name, out)
    assert result["returncode"] == (0 if mode == "fresh" else 2)
    assert (out / raw.name).exists() is (mode == "fresh")
    assert bool(result["evidence_sha256"]) is (mode == "fresh")


@pytest.mark.parametrize("changed", [False, True])
def test_suite_binds_dirty_source_and_fails_if_identity_changes(tmp_path, monkeypatch, changed):
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "EVIDENCE", tmp_path / "raw")
    module.EVIDENCE.mkdir()
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw: SimpleNamespace(stdout="b" * 40))
    initial = {"source": {"tree_fingerprint": "a" * 64, "worktree_dirty": True},
               "environment": {"synthetic": True}, "runtime_config": {}}
    final = copy.deepcopy(initial)
    if changed:
        final["source"]["tree_fingerprint"] = "c" * 64
    identities = iter([initial, final])
    monkeypatch.setattr(module, "verification_identity", lambda: next(identities))
    def check(label, argv, evidence_name, out):
        log = out / f"{label}.log"
        log.write_text("synthetic bookkeeping only")
        return {"id": label.upper(), "returncode": 0, "log": str(log)}
    monkeypatch.setattr(module, "run_check", check)
    assert module._main() == int(changed)
    packet = next((tmp_path / "data/acceptance_runs").iterdir())
    result = json.loads((packet / "verification.json").read_text())
    assert result["identity_stable"] is (not changed)
    assert result["status"] == ("FAIL" if changed else "PASS")
    assert json.loads((packet / "identity_before.json").read_text()) == initial
