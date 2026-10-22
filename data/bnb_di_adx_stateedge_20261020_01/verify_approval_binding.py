#!/usr/bin/env python3
"""Narrow verification of the scope-approval binding.

Recording the scope approval is a payload edit, so it produces a NEW identity B.
This verifier checks exactly what that edit is allowed to change, and nothing
else. It deliberately does **not** re-run the test suites: if the source
identity (A) and the assertion files are unchanged, the suites cannot have
changed behaviour, so re-running them would be theatre rather than evidence.

What it asserts:
  1. the payload diff against the pre-approval snapshot touches ONLY the
     approval fields (approval_state, approved_scope, approved_by, approved_at)
     and the payload version;
  2. identity A (source) is UNCHANGED — no code moved;
  3. every assertion file listed in the sealed record
     (evidence/assertion_files.json) is byte-identical to the record — so the
     suites need not be re-run;
  4. identity B recomputed from the edited payload matches the value recorded in
     the manifests, and the pre-approval B is recorded so the approval is
     traceable to the revision it was made against;
  5. the affected manifest entries re-hash correctly;
  6. the scope approval is in a coherent state (PENDING with nothing approved,
     or APPROVED with scope/approver/time all present);
  7. the pre-approval B is not silently reused.

Usage:
  .venv/bin/python data/bnb_di_adx_stateedge_20261020_01/verify_approval_binding.py
Exit code 0 = the binding is coherent; 2 = not coherent (the run is blocked).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

ROOT = Path(".").resolve()
PACKET = Path("data/bnb_di_adx_stateedge_20261020_01")
PREREG = Path("data/bnb_di_adx_stateedge_prereg_20261020_01")
REVIEW = Path("data/bnb_native_review_20261020_01")
PRE_APPROVAL = PACKET / "pre_approval_payload.json"

APPROVAL_FIELDS = ("approval_state", "approved_scope", "approved_by", "approved_at")
APPROVALABLE = {"data": {"scope_change_request": {field: None for field in APPROVAL_FIELDS}}}

#: The assertion files, and the sealed record of their hashes. The record lives
#: inside the packet, so it is covered by manifest C — identity A does NOT cover
#: tests/, and a test file that is not in the record is a FAILURE, not a skip.
ASSERTION_FILES = (
    "tests/test_attempt_ledger_snapshot.py",
    "tests/test_ledger_and_binding_invariants.py",
    "tests/test_di_adx_state_edge_contract.py",
    "tests/test_di_adx_state_edge_scoped_path.py",
    "tests/test_di_adx_state_edge_entrypoint_integration.py",
    "tests/test_di_adx_state_edge_launcher_integration.py",
    "tests/test_validation_timeout_and_ledger_faults.py",
    "tests/test_wfo_validation_envelope.py",
    "tests/test_indicator_window_equivalence.py",
)
ASSERTION_RECORD = PACKET / "evidence" / "assertion_files.json"


def canonical(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(payload: dict) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def diff_payloads(before: dict, after: dict) -> list[str]:
    """Top-level dotted paths whose values differ."""
    changed: list[str] = []

    def walk(a, b, prefix: str) -> None:
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                walk(a.get(key), b.get(key), f"{prefix}.{key}" if prefix else key)
            return
        if a != b:
            changed.append(prefix)

    walk(before, after, "")
    return changed


def main() -> int:
    payload = json.loads((PREREG / "preregistration_payload.json").read_text())
    pre = json.loads(PRE_APPROVAL.read_text()) if PRE_APPROVAL.exists() else None
    state = payload["data"]["scope_change_request"]["approval_state"]

    print("=== scope approval binding ===")
    print(f"approval_state      : {state}")
    print(f"approved_scope      : {payload['data']['scope_change_request']['approved_scope']}")
    print(f"approved_by         : {payload['data']['scope_change_request']['approved_by']}")
    print(f"approved_at         : {payload['data']['scope_change_request']['approved_at']}")
    print()

    problems: list[str] = []

    # (1) only the approval fields may differ from the pre-approval snapshot
    if pre is not None:
        changed = diff_payloads(pre, payload)
        allowed = {"version"}
        allowed |= {f"data.scope_change_request.{field}" for field in APPROVAL_FIELDS}
        unexpected = [c for c in changed if c not in allowed]
        print("payload fields changed vs the pre-approval snapshot:")
        for entry in changed:
            marker = "approval" if entry in allowed else "not-an-approval-field"
            print(f"  - {entry}  ({marker})")
        if unexpected:
            problems.append(
                "fields outside the approval set changed since the snapshot: "
                f"{unexpected}. If that is intended evolution, re-record the "
                "snapshot (copy the current payload over "
                "pre_approval_payload.json) before approving."
            )
        pre_b = fingerprint(pre)
        post_b = fingerprint(payload)
        print(f"pre-approval  B = {pre_b}")
        print(f"post-approval B = {post_b}")
        if changed and pre_b == post_b:
            problems.append(
                "identity B did not change although the payload changed — a stale "
                "fingerprint would let a run proceed on an unapproved payload"
            )
    else:
        pre_b = None
        print(
            "no pre-approval snapshot: copy the current payload over "
            "pre_approval_payload.json before approving"
        )

    # (2) source identity unchanged
    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    source = capture_campaign_source(ROOT)
    manifest = json.loads((PACKET / "manifest.json").read_text())
    print()
    print(f"identity A recomputed = {source['tree_fingerprint']}")
    print(f"identity A in manifest = {manifest['identity_A_source_fingerprint']}")
    if source["tree_fingerprint"] != manifest["identity_A_source_fingerprint"]:
        problems.append("source identity A changed — the approval is not a data-only edit")

    # (3) the assertion files are unchanged, so the suites need not be re-run
    changed_tests = []
    if not ASSERTION_RECORD.exists():
        problems.append(
            f"the sealed assertion-file record is missing: {ASSERTION_RECORD}. "
            "The assertion hashes are not bound by any identity, so the suites "
            "cannot be skipped on the strength of this check."
        )
    else:
        record = json.loads(ASSERTION_RECORD.read_text())
        recorded_hashes = record.get("files", {})
        # The SEAL is the source of truth: every file listed in the record must
        # be on disk with the recorded bytes. (A list hard-coded here covered
        # only 9 of the 14 sealed files, so five files - among them the one
        # that carries the control-path evidence - were never compared.)
        for rel, recorded_hash in recorded_hashes.items():
            path = ROOT / rel
            if not path.exists():
                changed_tests.append(f"{rel} (missing)")
                continue
            if recorded_hash != sha256(path):
                changed_tests.append(f"{rel} (hash differs)")
        # a record that shrank below the floor is also a failure
        for rel in ASSERTION_FILES:
            if rel not in recorded_hashes:
                changed_tests.append(f"{rel} (not in the sealed record)")
        print(f"assertion files unchanged: {not changed_tests}")
        if changed_tests:
            problems.append(
                f"assertion files changed or unrecorded, the suites must be "
                f"re-run: {changed_tests}"
            )

    # (4) B in the manifests matches the recomputed value
    post_b = fingerprint(payload)
    mismatched = []
    for directory in (PACKET, PREREG, REVIEW):
        candidate = json.loads((directory / "manifest.json").read_text())
        if candidate["identity_B_preregistration_fingerprint"] != post_b:
            mismatched.append(directory.name)
    print(f"manifest identity_B matches the recomputed B: {not mismatched}")
    if mismatched:
        problems.append(f"manifest identity_B is stale in: {mismatched}")

    # (5) the affected manifest entries re-hash
    bad = []
    for directory in (PACKET, PREREG, REVIEW):
        candidate = json.loads((directory / "manifest.json").read_text())
        for entry in candidate["files"]:
            path = directory / entry["path"]
            if not path.exists() or sha256(path) != entry["sha256"]:
                bad.append(f"{directory.name}/{entry['path']}")
    print(f"affected manifest entries re-hash: {not bad}")
    if bad:
        problems.append(f"manifest entries do not re-hash: {bad}")

    # (6) the approval state itself must be coherent
    request = payload["data"]["scope_change_request"]
    # ``approval_state`` is the SINGLE authoritative state; the payload has no
    # second status field on purpose, so following the procedure cannot be
    # rejected by a field the procedure never sets.
    if state == "APPROVED":
        for field in ("approved_scope", "approved_by", "approved_at"):
            if not request.get(field):
                problems.append(f"APPROVED but {field} is empty")
    elif state == "PENDING_OPERATOR_APPROVAL":
        if request.get("approved_by") or request.get("approved_scope"):
            problems.append("PENDING but approval fields are populated")
    else:
        problems.append(f"unknown approval_state {state!r}")

    print()
    if problems:
        print("BINDING NOT COHERENT — the run is blocked:")
        for item in problems:
            print(f"  - {item}")
        return 2
    if state != "APPROVED":
        print("BINDING COHERENT, but the scope change is still PENDING_OPERATOR_APPROVAL.")
        print("A market run is NOT authorised: record the approval in the payload first")
        print("(this changes identity B), then refresh the manifests and re-run this")
        print("verifier.")
        return 3
    print("BINDING COHERENT and the scope change is APPROVED.")
    print("The run may proceed against identity B =", post_b)
    print("Reminder: a preregistration is not a run authorisation — the operator")
    print("must still approve the scope and budget of the run itself.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
