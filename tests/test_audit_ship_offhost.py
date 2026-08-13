"""Tests for off-host audit retention shipping (P1.2)."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path

import pytest

from scripts.audit_ship_offhost import (
    load_manifest,
    plan_ship,
    save_state,
    ship_dir,
)


def _make_archive(archive_dir: Path, name: str, content: bytes) -> str:
    archive_dir.mkdir(parents=True, exist_ok=True)
    path = archive_dir / name
    with gzip.open(path, "wb") as handle:
        handle.write(content)
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest_path = archive_dir / "manifest.json"
    manifest = (
        json.loads(manifest_path.read_text())
        if manifest_path.exists()
        else {"archives": {}}
    )
    manifest["archives"][name] = {
        "sha256": sha,
        "lines": 2,
        "created_at": "2026-08-12T00:00:00+00:00",
    }
    manifest_path.write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    return sha


def test_plan_ships_unsent_archive(tmp_path):
    archive_dir = tmp_path / "archive"
    _make_archive(archive_dir, "audit-20260812.jsonl.gz", b'{"a":1}\n{"b":2}\n')
    planned = plan_ship(archive_dir, force=False)
    assert len(planned) == 1
    name, _remote_sha, local_sha, lines = planned[0]
    assert name == "audit-20260812.jsonl.gz"
    assert lines == 2
    assert len(local_sha) == 64


def test_plan_skips_already_shipped(tmp_path):
    archive_dir = tmp_path / "archive"
    _make_archive(archive_dir, "audit-20260812.jsonl.gz", b'{"a":1}\n')
    save_state(
        archive_dir,
        {"version": 1, "shipped": {"audit-20260812.jsonl.gz": {"sha256": "x"}}},
    )
    assert plan_ship(archive_dir, force=False) == []
    planned = plan_ship(archive_dir, force=True)
    assert len(planned) == 1


def test_plan_rejects_manifest_mismatch(tmp_path):
    archive_dir = tmp_path / "archive"
    _make_archive(archive_dir, "audit-20260812.jsonl.gz", b'{"a":1}\n')
    # Tamper with the archive after manifesting
    (archive_dir / "audit-20260812.jsonl.gz").write_bytes(b"tampered")
    with pytest.raises(SystemExit, match="does not match manifest"):
        plan_ship(archive_dir, force=False)


def test_ship_dir_copies_and_verifies(tmp_path):
    archive_dir = tmp_path / "archive"
    remote_dir = tmp_path / "remote"
    _make_archive(archive_dir, "audit-20260812.jsonl.gz", b'{"a":1}\n{"b":2}\n')
    planned = plan_ship(archive_dir, force=True)
    ship_dir(planned, archive_dir, remote_dir)
    assert (remote_dir / "audit-20260812.jsonl.gz").exists()
    manifest = load_manifest(archive_dir)
    assert (
        manifest["archives"]["audit-20260812.jsonl.gz"]["sha256"]
        == hashlib.sha256(
            (remote_dir / "audit-20260812.jsonl.gz").read_bytes()
        ).hexdigest()
    )


def test_ship_dir_detects_corrupt_remote(tmp_path, monkeypatch):
    archive_dir = tmp_path / "archive"
    remote_dir = tmp_path / "remote"
    _make_archive(archive_dir, "audit-20260812.jsonl.gz", b'{"a":1}\n')
    planned = plan_ship(archive_dir, force=True)

    def corrupt_copy(src, dst):
        Path(dst).write_bytes(b"corrupted-on-copy")

    monkeypatch.setattr("shutil.copy2", corrupt_copy)
    with pytest.raises(SystemExit, match="checksum"):
        ship_dir(planned, archive_dir, remote_dir)
    assert not (remote_dir / "audit-20260812.jsonl.gz").exists()
