"""P1.2: correlation IDs and append-only audit retention."""
from __future__ import annotations

import gzip
import json
import os
import sys

import pytest

SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
if SCRIPTS not in sys.path:
    sys.path.insert(0, SCRIPTS)

import audit_retention as retention
from trading_agent.execution.correlation import (
    bind_run_correlation,
    get_correlation_id,
    new_correlation_id,
    run_correlation,
    set_correlation_id,
)
from trading_agent.execution.live_safety import (
    append_live_audit_event,
)


def test_audit_event_is_tagged_with_correlation_id(tmp_path):
    audit = tmp_path / "audit.jsonl"
    cid = bind_run_correlation()
    append_live_audit_event(str(audit), "run_started", {"cycle": 1})
    with open(audit, encoding="utf-8") as handle:
        event = json.loads(handle.readline())
    assert event["correlation_id"] == cid


def test_audit_event_without_correlation_has_no_tag(tmp_path):
    audit = tmp_path / "audit.jsonl"
    set_correlation_id("")
    append_live_audit_event(str(audit), "untagged", {})
    with open(audit, encoding="utf-8") as handle:
        event = json.loads(handle.readline())
    assert "correlation_id" not in event


def test_run_correlation_context_restores_previous_binding():
    set_correlation_id("outer")
    with run_correlation("inner"):
        assert get_correlation_id() == "inner"
    assert get_correlation_id() == "outer"


def test_new_correlation_id_is_unique():
    assert new_correlation_id() != new_correlation_id()


def test_archive_roundtrip_and_manifest(tmp_path):
    audit = tmp_path / "audit.jsonl"
    append_live_audit_event(str(audit), "a", {})
    append_live_audit_event(str(audit), "b", {"n": 2})
    archive_dir = tmp_path / "archive"
    archived = retention.archive_trail(audit, archive_dir)
    assert archived is not None
    assert archived.exists()
    with gzip.open(archived, "rt", encoding="utf-8") as handle:
        lines = [line for line in handle if line.strip()]
    assert len(lines) == 2
    manifest = retention._load_manifest(archive_dir)
    assert archived.name in manifest["archives"]
    assert manifest["archives"][archived.name]["lines"] == 2
    assert audit.stat().st_size == 0  # live trail truncated


def test_archive_refuses_corrupt_trail(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"a": 1}\nnot-json\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="corrupt"):
        retention.archive_trail(audit, tmp_path / "archive")


def test_prune_removes_only_old_archives(tmp_path):
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()
    fresh = archive_dir / "audit-20990101.jsonl.gz"
    old = archive_dir / "audit-20200101.jsonl.gz"
    fresh.write_bytes(b"x")
    old.write_bytes(b"x")
    removed = retention.prune_archives(archive_dir, days=90)
    assert removed == 1
    assert fresh.exists()
    assert not old.exists()


def test_verify_reports_line_count_and_corruption(tmp_path):
    audit = tmp_path / "audit.jsonl"
    audit.write_text('{"a": 1}\n{"b": 2}\n', encoding="utf-8")
    assert retention.verify_trail(audit) == 2
    audit.write_text('{"a": 1}\nbroken\n', encoding="utf-8")
    with pytest.raises(SystemExit, match="corrupt"):
        retention.verify_trail(audit)
