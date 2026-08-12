#!/usr/bin/env python3
"""Off-host audit retention (P1.2, off-host half).

Ships immutable audit archives (produced by ``audit_retention.py archive``)
to an independent audit host / object store and verifies their checksums
there, so a compromise of the trading host cannot silently edit or destroy
the audit trail.

Only archives listed in the local SHA-256 manifest are shipped; after a
successful remote checksum match the archive name is recorded in
``.offhost_state.json`` inside the archive dir.

Transports:
  * ``--method dir``   — copy to a mounted remote directory (NFS, S3-FUSE,
                         rsync target, Synology share, ...). Verifies the
                         remote copy SHA-256 locally. Safe and testable.
  * ``--method ssh``   — scp to ``--ssh-host`` and verify the remote SHA-256
                         with ``ssh sha256sum``. Requires ssh/scp binaries.

Usage:
  python scripts/audit_retention.py archive data/execution/binance_live_audit.jsonl
  python scripts/audit_ship_offhost.py --archive-dir data/execution/archive \
      --method dir --remote-dir /mnt/audit-host/archive --dry-run
  python scripts/audit_ship_offhost.py --archive-dir data/execution/archive \
      --method ssh --ssh-host audit@host --remote-dir /var/lib/audit/archive
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

MANIFEST_NAME = "manifest.json"
STATE_NAME = ".offhost_state.json"


def load_manifest(archive_dir: Path) -> dict:
    path = archive_dir / MANIFEST_NAME
    if not path.exists():
        raise SystemExit(f"no manifest at {path} — run audit_retention.py archive first")
    return json.loads(path.read_text(encoding="utf-8"))


def load_state(archive_dir: Path) -> dict:
    path = archive_dir / STATE_NAME
    if not path.exists():
        return {"version": 1, "shipped": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save_state(archive_dir: Path, state: dict) -> None:
    path = archive_dir / STATE_NAME
    path.write_text(json.dumps(state, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def plan_ship(archive_dir: Path, *, force: bool) -> list[tuple[str, str, str, int]]:
    """Return [(archive_name, remote_sha256, local_sha256, lines)] to ship."""
    manifest = load_manifest(archive_dir)
    state = load_state(archive_dir)
    planned: list[tuple[str, str, str, int]] = []
    for name, meta in sorted(manifest.get("archives", {}).items()):
        local_path = archive_dir / name
        if not local_path.exists():
            print(f"WARNING: manifest lists missing archive {name}")
            continue
        local_sha = hashlib.sha256(local_path.read_bytes()).hexdigest()
        if local_sha != meta.get("sha256"):
            raise SystemExit(f"local archive {name} does not match manifest checksum")
        if not force and name in state.get("shipped", {}):
            continue  # already shipped and verified
        planned.append((name, meta.get("sha256", ""), local_sha, meta.get("lines", 0)))
    return planned


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ship_dir(archives: list[tuple[str, str, str, int]], archive_dir: Path, remote_dir: Path) -> None:
    remote_dir.mkdir(parents=True, exist_ok=True)
    for name, _remote_sha, _local_sha, _lines in archives:
        src = archive_dir / name
        dst = remote_dir / name
        shutil.copy2(src, dst)
        if _sha256_file(dst) != _local_sha:
            dst.unlink(missing_ok=True)
            raise SystemExit(f"remote copy {dst} failed checksum verification")
        print(f"shipped+verified {name} -> {dst}")


def ship_ssh(archives: list[tuple[str, str, str, int]], archive_dir: Path, ssh_host: str, remote_dir: str) -> None:
    for name, _remote_sha, _local_sha, _lines in archives:
        src = archive_dir / name
        run = subprocess.run(
            ["scp", "-q", str(src), f"{ssh_host}:{remote_dir}/{name}"],
            capture_output=True, text=True,
        )
        if run.returncode != 0:
            raise SystemExit(f"scp {name} failed: {run.stderr.strip()}")
        check = subprocess.run(
            ["ssh", ssh_host, "sha256sum", f"{remote_dir}/{name}"],
            capture_output=True, text=True,
        )
        if check.returncode != 0:
            raise SystemExit(f"ssh checksum {name} failed: {check.stderr.strip()}")
        remote_sha = check.stdout.split()[0]
        if remote_sha != _local_sha:
            raise SystemExit(f"remote checksum mismatch for {name}: {remote_sha}")
        print(f"shipped+verified {name} -> {ssh_host}:{remote_dir}/{name}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", default="data/execution/archive")
    parser.add_argument("--method", choices=["dir", "ssh"], required=True)
    parser.add_argument("--remote-dir", required=True)
    parser.add_argument("--ssh-host", default="")
    parser.add_argument("--force", action="store_true", help="re-ship already shipped archives")
    parser.add_argument("--dry-run", action="store_true", help="print plan only")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    archive_dir = Path(args.archive_dir)
    if args.method == "ssh" and not args.ssh_host:
        raise SystemExit("--ssh-host is required with --method ssh")

    planned = plan_ship(archive_dir, force=args.force)
    if not planned:
        print("nothing to ship (all manifest archives already shipped+verified)")
        return 0
    for name, _, local_sha, lines in planned:
        marker = "[dry-run]" if args.dry_run else ""
        print(f"{marker} would ship {name} ({lines} lines, {local_sha[:16]}...)")

    if args.dry_run:
        return 0
    if args.method == "dir":
        ship_dir(planned, archive_dir, Path(args.remote_dir))
    else:
        ship_ssh(planned, archive_dir, args.ssh_host, args.remote_dir)

    state = load_state(archive_dir)
    for name, remote_sha, _local_sha, lines in planned:
        state.setdefault("shipped", {})[name] = {
            "sha256": remote_sha,
            "lines": lines,
            "shipped_at": datetime.now(UTC).isoformat(),
        }
    save_state(archive_dir, state)
    print(f"state updated: {archive_dir / STATE_NAME}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())