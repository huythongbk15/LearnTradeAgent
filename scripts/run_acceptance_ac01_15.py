#!/usr/bin/env python3
"""Run the AC01-AC15 evidence checks without overwriting prior evidence."""

from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path("/tmp")
PYTHON = ROOT / ".venv" / "bin" / "python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)


def run_check(label: str, argv: list[str], evidence_name: str, out: Path) -> dict[str, object]:
    log = out / f"{label}.log"
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    source = EVIDENCE / evidence_name
    # AC01's old producer wrote an unversioned /tmp artifact; do not copy it
    # after pytest, since it may describe a different run/revision.
    if label != "ac01" and source.exists():
        shutil.copy2(source, out / evidence_name)
    return {"id": label.upper(), "returncode": result.returncode, "log": str(log)}


def main() -> int:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
    out = ROOT / "data" / "acceptance_runs" / f"ac01_15_{stamp}"
    previous, current = out / "previous", out / "current"
    previous.mkdir(parents=True)
    current.mkdir()

    git_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    for number in range(1, 16):
        name = f"ac{number:02d}_evidence.json"
        old = EVIDENCE / name
        if old.exists():
            shutil.copy2(old, previous / name)

    results = [{"revision": git_head, "run_dir": str(out)}]
    for number in range(2, 16):
        label = f"ac{number:02d}"
        results.append(run_check(label, [str(PYTHON), f"scripts/evidence_{label}.py"], f"{label}_evidence.json", current))
    results.append(run_check("ac01", [str(PYTHON), "-m", "pytest", "-q", "tests/test_ac01_prefix_contract.py"], "ac01_evidence.json", current))

    summary = out / "SUMMARY.txt"
    with summary.open("w", encoding="utf-8") as stream:
        stream.write(f"Integrated AC01-AC15 run\nRevision: {git_head}\nDirectory: {out}\n\n")
        for result in results[1:]:
            stream.write(f"{result['id']}: {'PASS' if result['returncode'] == 0 else 'FAIL'} (rc={result['returncode']})\n")
    print(summary.read_text(encoding="utf-8"), end="")
    for result in results[1:]:
        log_path = Path(str(result["log"]))
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"\n--- {result['id']} (rc={result['returncode']}) ---")
        print("\n".join(lines[-10:]))
    return 0 if all(int(result["returncode"]) == 0 for result in results[1:]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
