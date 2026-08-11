#!/usr/bin/env python3
"""Append-only audit retention utilities (P1.2).

The live runner writes an append-only JSONL audit trail (each line fsynced
before the next write).  This tool provides the local retention half of P1.2:

  * archive  — gzip the current audit trail into an immutable dated archive,
               record a SHA-256 manifest, then truncate the live file.
  * prune    — delete archives older than N days (default 90).
  * verify   — confirm every line of a trail/archive is well-formed JSON,
               non-empty, and (for the live file) mode 0600.

Off-host retention (uploading archives to object storage / an independent
audit host) is a separate deployment step; `archive` is designed so the
produced ``.jsonl.gz`` artifact is the unit that gets shipped.

Usage:
  python scripts/audit_retention.py archive data/execution/binance_live_audit.jsonl
  python scripts/audit_retention.py prune --archive-dir data/execution/archive --days 90
  python scripts/audit_retention.py verify data/execution/binance_live_audit.jsonl
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

ARCHIVE_FILENAME_RE = re.compile(r"^audit-(\d{8})\.jsonl\.gz$")
DEFAULT_ARCHIVE_DIR = "data/execution/archive"
MANIFEST_NAME = "manifest.json"


def _line_hash(line: bytes) -> str:
    return hashlib.sha256(line).hexdigest()


def _manifest_path(archive_dir: Path) -> Path:
    return archive_dir / MANIFEST_NAME


def _load_manifest(archive_dir: Path) -> dict:
    path = _manifest_path(archive_dir)
    if not path.exists():
        return {"version": 1, "archives": {}}
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _save_manifest(archive_dir: Path, manifest: dict) -> None:
    path = _manifest_path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.chmod(path, 0o600)
        os.write(
            fd,
            (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode("utf-8"),
        )
        os.fsync(fd)
    finally:
        os.close(fd)


def archive_trail(audit_path: Path, archive_dir: Path) -> Path:
    """Gzip the live trail into an immutable dated archive and truncate it."""
    audit_path = Path(audit_path)
    if not audit_path.exists() or audit_path.stat().st_size == 0:
        print(f"nothing to archive: {audit_path} is empty/missing")
        return None  # type: ignore[return-value]

    verify_trail(audit_path)
    archive_dir = Path(archive_dir)
    archive_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    archive_path = archive_dir / f"audit-{stamp}.jsonl.gz"
    suffix = 0
    while archive_path.exists():
        suffix += 1
        archive_path = archive_dir / f"audit-{stamp}-{suffix}.jsonl.gz"

    # Read, hash and compress in one pass.
    sha = hashlib.sha256()
    line_count = 0
    with open(audit_path, "rb") as src, gzip.open(archive_path, "wb") as dst:
        for raw in src:
            if not raw.strip():
                continue
            sha.update(raw)
            dst.write(raw)
            line_count += 1

    manifest = _load_manifest(archive_dir)
    manifest["archives"][archive_path.name] = {
        "sha256": sha.hexdigest(),
        "lines": line_count,
        "created_at": datetime.now(UTC).isoformat(),
    }
    _save_manifest(archive_dir, manifest)

    # Truncate the live file only after the archive is safely on disk.
    with open(audit_path, "w", encoding="utf-8") as handle:
        handle.truncate(0)
    print(f"archived {line_count} line(s) -> {archive_path}")
    print(f"live trail truncated: {audit_path}")
    return archive_path


def prune_archives(archive_dir: Path, days: int) -> int:
    """Delete dated archives older than ``days``; returns count removed."""
    archive_dir = Path(archive_dir)
    if not archive_dir.exists():
        print(f"no archive dir: {archive_dir}")
        return 0
    cutoff = datetime.now(UTC).date() - timedelta(days=days)
    manifest = _load_manifest(archive_dir)
    removed = 0
    for path in sorted(archive_dir.glob("audit-*.jsonl.gz")):
        match = ARCHIVE_FILENAME_RE.match(path.name)
        if not match:
            continue
        stamp = datetime.strptime(match.group(1), "%Y%m%d").date()
        if stamp < cutoff:
            path.unlink(missing_ok=True)
            manifest["archives"].pop(path.name, None)
            removed += 1
            print(f"pruned {path.name} (older than {days}d)")
    _save_manifest(archive_dir, manifest)
    return removed


def verify_trail(audit_path: Path) -> int:
    """Check every line is well-formed JSON; returns line count."""
    audit_path = Path(audit_path)
    if not audit_path.exists():
        raise SystemExit(f"audit trail not found: {audit_path}")
    mode = audit_path.stat().st_mode & 0o777
    if mode != 0o600:
        print(f"WARNING: {audit_path} mode is {oct(mode)}, expected 0600")
    count = 0
    with open(audit_path, "r", encoding="utf-8") as handle:
        for lineno, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                json.loads(line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"corrupt audit line {lineno} in {audit_path}: {exc}"
                )
            count += 1
    print(f"verified {count} line(s) in {audit_path}")
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_archive = sub.add_parser("archive", help="gzip trail into archive dir")
    p_archive.add_argument("audit_file")
    p_archive.add_argument("--archive-dir", default=DEFAULT_ARCHIVE_DIR)

    p_prune = sub.add_parser("prune", help="delete archives older than N days")
    p_prune.add_argument("--archive-dir", default=DEFAULT_ARCHIVE_DIR)
    p_prune.add_argument("--days", type=int, default=90)

    p_verify = sub.add_parser("verify", help="validate JSONL trail")
    p_verify.add_argument("audit_file")

    args = parser.parse_args()
    if args.command == "archive":
        archive_trail(Path(args.audit_file), Path(args.archive_dir))
    elif args.command == "prune":
        prune_archives(Path(args.archive_dir), args.days)
    elif args.command == "verify":
        verify_trail(Path(args.audit_file))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
