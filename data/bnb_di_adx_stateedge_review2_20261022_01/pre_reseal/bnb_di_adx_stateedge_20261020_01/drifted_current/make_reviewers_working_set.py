#!/usr/bin/env python3
"""Generate the reviewer's working set from the artifacts themselves.

Every statement in the generated file is derived: file paths and line numbers
come from grepping the source, hashes come from the sealed manifests, and the
receipt list comes from the packet directory. Nothing here is the author's
prose about the work — it is a map to the primary material a reviewer other
than the author should read.

Read-only. Writes exactly one file into the packet.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(".").resolve()
PACKET = Path("data/bnb_di_adx_stateedge_20261020_01")
PREREG = Path("data/bnb_di_adx_stateedge_prereg_20261020_01")

HOP_FILES = [
    "scripts/run_wfo_parallel.py",
    "src/trading_agent/backtest/tournament.py",
    "scripts/full_system_backtest.py",
    "src/trading_agent/backtest/campaign_execution.py",
    "src/trading_agent/strategies/canonical/candidates.py",
]
ASSERTION_FILES = [
    "tests/test_di_adx_state_edge_contract.py",
    "tests/test_di_adx_state_edge_scoped_path.py",
    "tests/test_di_adx_state_edge_entrypoint_integration.py",
    "tests/test_validation_timeout_and_ledger_faults.py",
    "tests/test_wfo_validation_envelope.py",
    "tests/test_indicator_window_equivalence.py",
]
CANDIDATE_FILE = "src/trading_agent/strategies/di_adx_state_edge.py"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def hop_sites() -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for rel in HOP_FILES:
        path = ROOT / rel
        sites = []
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if "scope_token" in line or "SCOPED_CANDIDATE_SCOPE_TOKENS" in line \
                    or "resolve_scoped_candidate_id" in line:
                sites.append((number, line.strip()[:118]))
        out[rel] = sites
    return out


def test_sites() -> dict[str, list[tuple[int, str]]]:
    out: dict[str, list[tuple[int, str]]] = {}
    for rel in ASSERTION_FILES:
        path = ROOT / rel
        sites = []
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if re.match(r"\s*def (test_|test)", line):
                sites.append((number, line.strip().split("(")[0]))
        out[rel] = sites
    return out


def main() -> int:
    manifest = json.loads((PACKET / "manifest.json").read_text())
    prereg_manifest = json.loads((PREREG / "manifest.json").read_text())
    payload = json.loads((PREREG / "preregistration_payload.json").read_text())

    lines: list[str] = []
    add = lines.append
    add("# Reviewer's working set — `di_adx_state_edge`, revision under review")
    add("")
    add("Generated from the artifacts themselves: line numbers are grepped out of the")
    add("source, hashes come from the sealed manifests. **This is a map to primary")
    add("material, not a summary of it.** A reviewer other than the author should read")
    add("the files below, not this document.")
    add("")
    add("## Revision under review")
    add("")
    add("**This file states no identity.** The identities live in the manifests,")
    add("which are sealed per revision:")
    add("")
    add("- `data/bnb_di_adx_stateedge_20261020_01/manifest.json`")
    add("- `data/bnb_di_adx_stateedge_prereg_20261020_01/manifest.json`")
    add("- `data/bnb_native_review_20261020_01/manifest.json`")
    add("")
    add("A reviewer must read the identities from those manifests (or recompute")
    add("them) and must not take this file as identity evidence.")
    add("")
    add("## 1. The candidate implementation")
    add("")
    add(f"`{CANDIDATE_FILE}` — sha256 `{sha256(ROOT / CANDIDATE_FILE)}`")
    add("")
    add("## 2. The five token hops — every site that carries the scope token")
    add("")
    add("These are the sites the review must judge. The surrounding diff against")
    add("HEAD also contains unrelated 2026-10-19/20 work; the scope-token lines")
    add("below are the candidate's own surface.")
    add("")
    for rel, sites in hop_sites().items():
        add(f"### `{rel}`")
        add("")
        if not sites:
            add("_no scope-token site found_")
            add("")
            continue
        for number, text in sites:
            add(f"- L{number}: `{text}`")
        add("")

    add("## 3. The assertions")
    add("")
    add("Each file, then each test function it defines. The reviewer judges whether")
    add("these prove what the packet claims — not merely that they pass.")
    add("")
    for rel, sites in test_sites().items():
        path = ROOT / rel
        add(f"### `{rel}` — sha256 `{sha256(path)}`")
        add("")
        for number, text in sites:
            add(f"- L{number}: `{text}`")
        add("")

    add("## 3b. The new modules the next review must read in full")
    add("")
    for rel in ("src/trading_agent/backtest/attempt_ledger.py",
                "src/trading_agent/backtest/preregistration_binding.py"):
        path = ROOT / rel
        add(f"### `{rel}` — sha256 `{sha256(path)}`")
        add("")
        add("```python")
        add(path.read_text().rstrip())
        add("```")
        add("")

    add("## 3c. The seven hops — read these call sites")
    add("")
    add("| # | Site | What to check |")
    add("|---|---|---|")
    add("| 1 | `scripts/run_wfo_parallel.py` — `get_param_grid_for_strategy` + its caller | the scoped grid is reachable ONLY with the token; a scoped id without the token yields an empty grid |")
    add("| 2 | `scripts/run_wfo_parallel.py` — `prepare_parallel_protocol` budget guard | the same named caps as the CLI guard; no guard removed |")
    add("| 3 | `scripts/run_wfo_parallel.py` — `_verify_validation_protocol` budget guard | the same named caps; the producer and the verifier cannot disagree |")
    add("| 4 | `src/trading_agent/backtest/nested_wfo.py` — `run_nested_wfo` registry | the token comes from the execution context, so the worker resolves the candidate |")
    add("| 5 | `src/trading_agent/backtest/nested_wfo.py` — `_compute_strategy_code_sha` | the SHA must be the real one, NOT `\"unknown\"` — this is the source-attribution point |")
    add("| 6 | `src/trading_agent/backtest/campaign_execution.py` — artifact rebuild / validation | the registry is rebuilt with the token from the runner context |")
    add("| 7 | `src/trading_agent/backtest/selection_eligibility.py` — `verify_validation_reports` | the report attribution rebuild uses the token |")
    add("")
    add("Plus, new in this revision:")
    add("")
    add("| Site | What to check |")
    add("|---|---|")
    add("| `scripts/run_wfo_parallel.py` — `_verify_approved_payload_binding` | the binding gate runs in the launcher BEFORE the worker is spawned AND independently in the worker before its first cell; each checked field is named on mismatch |")
    add("| `scripts/run_wfo_parallel.py` — the summary writer | `qualification_status` / `all_folds_selected` / `sample_complete` / `validation_fold_rows` — `VALIDATION_COMPLETE` must not be readable as qualified or as sample-sufficient |")
    add("| `scripts/run_wfo_parallel.py` — the empty-grid guard | an unsupported strategy fails clearly instead of planning zero cells or falling back |")
    add("| `src/trading_agent/backtest/campaign_execution.py` — the `cell_ledger` write | one row per EVALUATION with the attempt count and the attempt outcomes |")
    add("")
    add("**Still token-less by design (out of the validation-only scope, listed in the")
    add("payload):** `wfo_sensitivity.py`, `research/selection_policy.py`,")
    add("`calibration/execution.py`, `calibration/packet.py`, `execution/live_admission.py`.")
    add("They must fail clearly for this candidate; they are not a fallback path.")
    add("")

    add("## 4. The receipts")
    add("")
    for rel in sorted(PACKET.glob("*.json")):
        if rel.name == "manifest.json":
            continue  # named in the revision section; its hash cannot be quoted here
        add(f"- `{rel}` — sha256 `{sha256(rel)}`")
    for rel in sorted(PREREG.glob("*.json")):
        add(f"- `{rel}` — sha256 `{sha256(rel)}`")
    add("")
    add("The three evidence manifests are named above by path; their hashes are")
    add("NOT repeated here, because a manifest cannot contain the hash of a")
    add("document that quotes the manifest's own hash. Take the identities")
    add("from the manifests themselves.")
    control = Path("data/bnb_native_adx25_validation_run_20261019_01")
    if control.exists():
        for name in ("execution_finished.json", "activation_scope.json"):
            path = control / name
            if path.exists():
                add(f"- `../bnb_native_adx25_validation_run_20261019_01/{name}` — "
                    f"sha256 `{sha256(path)}`")
    add("")

    add("## 5. What the author-run check round does and does not establish")
    add("")
    add("The check round is the author's own script, assertions and runner. Its PASS")
    add("is a self-verification. The receipts to read are listed above; the verdict")
    add("artifact itself carries the provenance block and the list of what it does")
    add("not establish.")
    add("")

    out = PACKET / "REVIEWERS_WORKING_SET.md"
    out.write_text("\n".join(lines) + "\n")
    print(f"wrote {out} ({len(lines)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
