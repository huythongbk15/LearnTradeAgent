#!/usr/bin/env python3
"""Check relative markdown links resolve to existing files.

Deterministic, network-free link checker for CI. Only checks *local*
links (paths, not URLs/anchors/mailto). External URLs are ignored so the
check never flakes on transient network/rate-limit failures. Fenced code
blocks and inline code spans are skipped.

Usage:
    python scripts/check_md_links.py --check

Exit codes:
    0  all local links resolve
    1  at least one broken link (or file read error)
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

# Markdown inline link: [text](target "optional title")
LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
# Reference definition: [label]: target
REF_DEF_RE = re.compile(r"^\[([^\]]+)\]:\s*(\S+)")

IGNORE_DIRS = {
    ".git", ".pytest_cache", ".venv", "node_modules", "__pycache__",
    ".mypy_cache", ".ruff_cache", ".tox", ".idea", ".vscode", "media",
}
IGNORED_SUBSTRINGS = ("docs/archive/", "docs/wsl-guide/")


def iter_markdown_files(root: pathlib.Path, pattern: str) -> list[pathlib.Path]:
    tracked: set[str] = set()
    if (root / ".git").exists():
        try:
            r = subprocess.run(
                ["git", "-C", str(root), "ls-files"],
                capture_output=True, text=True, check=True, timeout=30,
            )
            tracked = {str((root / p).resolve()) for p in r.stdout.splitlines()}
        except Exception:
            tracked = set()

    files: list[pathlib.Path] = []
    for p in root.rglob(pattern):
        if any(part in IGNORE_DIRS for part in p.parts):
            continue
        if tracked and str(p.resolve()) not in tracked:
            continue
        files.append(p)
    return sorted(files)


def resolve_target(target: str, md_file: pathlib.Path, root: pathlib.Path) -> str | None:
    """Return resolved local path for a link target, or None if external."""
    t = target.strip()
    if not t or t.startswith(("#", "http://", "https://", "mailto:", "tel:", "ftp://", "www.")):
        return None
    path_part = t.split("#", 1)[0]  # strip anchor suffix
    path_part = path_part.split(" ", 1)[0]  # strip optional title
    if not path_part:
        return None
    if path_part.startswith("<") and path_part.endswith(">"):
        path_part = path_part[1:-1]
    if path_part.startswith("/"):
        # absolute-from-repo-root
        candidate = (root / path_part.lstrip("/")).resolve()
        return str(candidate) if candidate.exists() else str(candidate)
    candidate = (md_file.parent / path_part).resolve()
    if candidate.exists():
        return str(candidate)
    # also try repo-root-relative
    alt = (root / path_part).resolve()
    return str(alt) if alt.exists() else str(candidate)


def check_file(md_file: pathlib.Path, root: pathlib.Path) -> list[str]:
    text = md_file.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    broken: list[str] = []
    in_fence = False
    refs: dict[str, str] = {}

    # First pass: collect reference definitions outside code fences.
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = REF_DEF_RE.match(stripped)
        if m:
            refs[m.group(1).lower()] = m.group(2)

    # Second pass: inline links outside code fences, skipping code spans.
    in_fence = False
    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        # Mask inline code spans so `[x](y)` inside them is ignored.
        masked = re.sub(r"`[^`]*`", lambda m: " " * len(m.group(0)), line)
        for m in LINK_RE.finditer(masked):
            target = m.group(1)
            # Reference-style target: [label]
            if target.strip().startswith("[") and target.strip().endswith("]"):
                label = target.strip()[1:-1].lower()
                target = refs.get(label, "")
                if not target:
                    continue
            resolved = resolve_target(target, md_file, root)
            if resolved is not None and not pathlib.Path(resolved).exists():
                broken.append(
                    f"{md_file.relative_to(root)}:{lineno}: link '{target}' -> {resolved} NOT FOUND"
                )
    return broken


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", type=pathlib.Path, help="Repo root (default: cwd)")
    ap.add_argument("--glob", default="*.md", help="Glob pattern (default: *.md)")
    ap.add_argument("--check", action="store_true", help="CI mode: exit 1 on broken links")
    args = ap.parse_args()

    root = args.root.resolve()
    files = iter_markdown_files(root, args.glob)
    if not files:
        print(f"No markdown files found under {root} (glob={args.glob})")
        return 1

    broken: list[str] = []
    for f in files:
        broken.extend(check_file(f, root))

    broken = [b for b in broken if not any(s in b for s in IGNORED_SUBSTRINGS)]

    print(f"Checked {len(files)} markdown files under {root}")
    if broken:
        print(f"BROKEN LINKS ({len(broken)}):")
        for b in broken:
            print(f"  - {b}")
        return 1
    print("All local markdown links resolve. OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
