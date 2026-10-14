#!/usr/bin/env python3
"""Run the AC01-AC15 evidence checks without overwriting prior evidence."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = Path("/tmp")
PYTHON = ROOT / ".venv" / "bin" / "python"
if not PYTHON.exists():
    PYTHON = Path(sys.executable)


def run_check(label: str, argv: list[str], evidence_name: str, out: Path) -> dict[str, object]:
    log = out / f"{label}.log"
    started_ns = time.time_ns()
    with log.open("w", encoding="utf-8") as stream:
        result = subprocess.run(argv, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
    source = EVIDENCE / evidence_name
    # AC01's old producer wrote an unversioned /tmp artifact; do not copy it
    # after pytest, since it may describe a different run/revision.
    evidence_sha = None
    rc = result.returncode
    if label != "ac01":
        if source.exists() and source.stat().st_mtime_ns >= started_ns:
            shutil.copy2(source, out / evidence_name)
            evidence_sha = hashlib.sha256((out / evidence_name).read_bytes()).hexdigest()
        elif rc == 0:
            rc = 2  # A stale /tmp file cannot certify this execution.
    return {"id": label.upper(), "returncode": rc, "worker_returncode": result.returncode,
            "log": str(log), "evidence_sha256": evidence_sha}


def verification_identity():
    from trading_agent.backtest.campaign_execution import execution_environment, simulator_runtime_config
    from trading_agent.backtest.campaign_integrity import capture_campaign_source

    return {"source": capture_campaign_source(ROOT), "environment": execution_environment(),
            "runtime_config": simulator_runtime_config(),
            "test_sources_sha256": {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                                    for path in sorted((ROOT / "tests").rglob("*.py"))}}


def _main() -> int:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S_utc")
    out = ROOT / "data" / "acceptance_runs" / f"ac01_15_{stamp}"
    previous, current = out / "previous", out / "current"
    previous.mkdir(parents=True)
    current.mkdir()
    before = verification_identity()
    (out / "identity_before.json").write_text(json.dumps(before, indent=2))

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
    results.append(run_check("ac01", [str(PYTHON), "-m", "pytest", "-o", "addopts=", "-q", "tests/test_ac01_prefix_contract.py"], "ac01_evidence.json", current))
    after = verification_identity()
    (out / "identity_after.json").write_text(json.dumps(after, indent=2))
    stable = before == after
    (out / "verification.json").write_text(json.dumps({"identity_stable": stable,
        "status": "PASS" if stable and all(r["returncode"] == 0 for r in results[1:]) else "FAIL",
        "checks": results[1:], "scope": "engineering_acceptance_not_market_qualification",
        "live_authorized": False}, indent=2))

    summary = out / "SUMMARY.txt"
    with summary.open("w", encoding="utf-8") as stream:
        stream.write(f"Integrated AC01-AC15 run\nRevision: {git_head}\nDirectory: {out}\n\n")
        stream.write(f"Source fingerprint: {before['source']['tree_fingerprint']}\nIdentity stable: {stable}\n\n")
        for result in results[1:]:
            stream.write(f"{result['id']}: {'PASS' if result['returncode'] == 0 else 'FAIL'} (rc={result['returncode']})\n")
    print(summary.read_text(encoding="utf-8"), end="")
    for result in results[1:]:
        log_path = Path(str(result["log"]))
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        print(f"\n--- {result['id']} (rc={result['returncode']}) ---")
        print("\n".join(lines[-10:]))
    return 0 if stable and all(int(result["returncode"]) == 0 for result in results[1:]) else 1


def main() -> int:
    # Producers share /tmp names. Serialize the suite so two local runs cannot
    # certify each other's raw outputs. OS releases the lock on owner exit.
    from trading_agent.backtest.campaign_execution import _cell_lock
    with _cell_lock(ROOT / "data/acceptance_runs/.ac01_15.lock"):
        return _main()


if __name__ == "__main__":
    raise SystemExit(main())
