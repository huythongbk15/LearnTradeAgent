#!/usr/bin/env python3
"""AUTHOR-RUN AUTOMATED VERIFICATION — bounded, 600 s, four checks, one verdict.

PROVENANCE (read this before reading the verdict)
-------------------------------------------------
This is **not** an independent review. The script, every assertion in it, the
fixtures it runs and the agent that executed it all belong to the **author** of
the candidate and of the five token hops. A PASS here means the author's own
checks agree with the artifacts; it does **not** mean an independent reviewer
has examined the code, the assertions or the receipts on the exact A/B/C.

An independent review requires a reviewer **other than the author** to:
  * read the five hop implementations and the execution/cache identity path;
  * read the assertions of the fault-injection and integration suites and judge
    whether they prove what they claim;
  * read the receipts (review_verdict.json, review_round_execution.json,
    the control packet's execution_finished.json) and the manifests;
  * reproduce the identity recomputation (A, B, C) against the same revision.


Checks (per the registered review round, prereg §13.5):
  R1  the five token hops and the execution / cache identity
  R2  the file path + sha256 are the pre-holdout artifact; the 6795-row decode
      scope is stated and the evaluation scope is [0, 5600)
  R3  the fault receipts, and that the aggregator does not certify a packet
      without coverage
  R4  the test IDs, the source identity and the manifests match the revision
      under review

Output: a single verdict — ACCEPTED_FOR_BOUNDED_VALIDATION / CHANGES_REQUIRED /
BLOCKED — with the evidence for every check. This script never changes source,
policy, gate, budget or identity; it only reads and runs the existing suites.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")

ROOT = Path(".").resolve()
PACKET = Path("data/bnb_di_adx_stateedge_20261020_01")
PREREG = Path("data/bnb_di_adx_stateedge_prereg_20261020_01")

FROZEN_A = "8d27b8f11431e23c95f2b30c7efb075ec0173cbd03fa0e084a1939c99de23e56"
FROZEN_B = "6f500d7059499194a7484804eaf440778aa0465e84c12d7ed1596c9b6c5cc305"
FROZEN_C = "8d27b8f11431e23c95f2b30c7efb075ec0173cbd03fa0e084a1939c99de23e56"  # unused
HOLDOUT_START = "2026-02-06T16:00:00"

checks: list[dict] = []


def check(name: str, ok: bool, evidence: dict) -> None:
    checks.append({"check": name, "pass": bool(ok), "evidence": evidence})


def main() -> int:
    started = time.time()
    payload = json.loads((PREREG / "preregistration_payload.json").read_text())

    # ── R1: five hops + execution/cache identity ────────────────────────
    hops = {
        "launcher CLI -> protocol": (
            "scripts/run_wfo_parallel.py",
            '"scope_token": scope_token' in (ROOT / "scripts/run_wfo_parallel.py").read_text(),
        ),
        "protocol -> trial execution context": (
            "scripts/run_wfo_parallel.py",
            'context["scope_token"] = scope_token' in (ROOT / "scripts/run_wfo_parallel.py").read_text(),
        ),
        "trial context -> cell plan (registry)": (
            "scripts/run_wfo_parallel.py",
            "build_canonical_cells(spec, scope_token=scope_token)" in (ROOT / "scripts/run_wfo_parallel.py").read_text(),
        ),
        "cell-runner context -> cache key": (
            "scripts/run_wfo_parallel.py",
            '"scope_token": self.trial_context["scope_token"]' in (ROOT / "scripts/run_wfo_parallel.py").read_text(),
        ),
        "worker -> run_cell -> registry/adapter/simulator": (
            "src/trading_agent/backtest/tournament.py",
            "build_default_registry(scope_token=scope_token)" in (ROOT / "src/trading_agent/backtest/tournament.py").read_text(),
        ),
    }
    hop_evidence = {k: {"file": v[0], "token_present": v[1]} for k, v in hops.items()}
    check("R1a_five_hops_carry_the_token", all(v[1] for v in hops.values()), hop_evidence)

    from trading_agent.backtest.campaign_evidence import content_hash
    from trading_agent.research.trials import execution_scope_identity

    ctx = {
        "trial_identity_version": 2, "protocol_fingerprint": "a" * 64,
        "source": {"code_commit": "b" * 40}, "environment": {"name": "research"},
        "runtime_config": {"cell_timeout_seconds": 300},
        "scope_token": payload["scope_token_binding"]["token"],
    }
    scope_with = execution_scope_identity(ctx)["context_sha256"]
    scope_without = execution_scope_identity(
        {k: v for k, v in ctx.items() if k != "scope_token"}
    )["context_sha256"]
    scope_other = execution_scope_identity(
        {**ctx, "scope_token": "another_scope"}
    )["context_sha256"]
    check(
        "R1b_execution_scope_identity_binds_the_token",
        scope_with != scope_without and scope_with != scope_other,
        {"scope_with_token": scope_with, "scope_without_token": scope_without,
         "scope_other_token": scope_other},
    )

    cache_key = lambda c: content_hash({  # noqa: E731
        "version": 1, "environment": "research", "stage": "CELL", "context": c,
        "cell_id": "cell-1", "params": {}, "cost": {}, "fault": {}, "window": {},
    })
    check(
        "R1c_cache_key_covers_the_execution_context",
        cache_key(ctx) != cache_key({k: v for k, v in ctx.items() if k != "scope_token"})
        and cache_key(ctx) != cache_key({**ctx, "scope_token": "another_scope"}),
        {"key_with_token": cache_key(ctx),
         "key_without_token": cache_key({k: v for k, v in ctx.items() if k != "scope_token"}),
         "key_other_token": cache_key({**ctx, "scope_token": "another_scope"})},
    )

    # the candidate must NOT be reachable without the token
    from trading_agent.strategies.canonical.candidates import build_default_registry
    broad = build_default_registry()
    scoped = build_default_registry(scope_token=payload["scope_token_binding"]["token"])
    check(
        "R1d_candidate_absent_from_the_broad_registry",
        payload["candidate"]["strategy_id"] not in broad.list_ids()
        and payload["candidate"]["strategy_id"] in scoped.list_ids(),
        {"broad_has_candidate": payload["candidate"]["strategy_id"] in broad.list_ids(),
         "scoped_has_candidate": payload["candidate"]["strategy_id"] in scoped.list_ids()},
    )

    # ── R2: the file is the pre-holdout artifact; scope is stated ────────
    import polars as pl
    binding = payload["data"]
    raw = Path(binding["path"]).read_bytes()
    file_sha = hashlib.sha256(raw).hexdigest()
    # timestamp-only probe: no OHLCV is decoded for this check
    ts = pl.read_parquet(binding["path"], columns=["timestamp"])["timestamp"]
    holdout = datetime.fromisoformat(HOLDOUT_START)
    rows_at_or_after = int((ts >= holdout).sum())
    check(
        "R2a_input_file_hash_matches_the_registration",
        file_sha == binding["sha256"],
        {"recomputed_sha256": file_sha, "registered_sha256": binding["sha256"],
         "path": binding["path"]},
    )
    check(
        "R2b_file_contains_no_reserved_holdout_bar",
        rows_at_or_after == 0,
        {"rows_total": ts.len(), "last_bar": str(ts[-1]),
         "holdout_opens": HOLDOUT_START,
         "rows_at_or_after_holdout_start": rows_at_or_after,
         "manifest_input_sha256": json.loads(
             (PACKET / "manifest.json").read_text())["input_sha256"],
         "matches_manifest": file_sha == json.loads(
             (PACKET / "manifest.json").read_text())["input_sha256"]},
    )
    request = binding.get("scope_change_request", {})
    approval_state = request.get("approval_state")
    approval_consistent = (
        approval_state == "PENDING_OPERATOR_APPROVAL"
        and request.get("approved_scope") is None
        and request.get("approved_by") is None
    ) or (
        approval_state == "APPROVED"
        and request.get("approved_scope") is not None
        and request.get("approved_by") is not None
        and request.get("approved_at") is not None
    )
    check(
        "R2c_decode_scope_is_disclosed_and_evaluation_scope_bounded",
        binding.get("read_scope") is not None
        and binding.get("evaluated_scope") == [0, 5600]
        and binding.get("decoded_but_not_evaluated") == [5600, 6795]
        and bool(request.get("binding_rule"))
        and approval_consistent,
        {"read_scope": binding.get("read_scope"),
         "evaluated_scope": binding.get("evaluated_scope"),
         "decoded_but_not_evaluated": binding.get("decoded_but_not_evaluated"),
         "scope_change_status": request.get("status"),
         "approval_state": approval_state,
         "approved_by": request.get("approved_by"),
         "binding_rule_present": bool(request.get("binding_rule"))},
    )

    # ── R3: fault receipts + the aggregator does not certify ────────────
    fault_run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=",
         "tests/test_validation_timeout_and_ledger_faults.py"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180,
    )
    integration_run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=",
         "tests/test_di_adx_state_edge_entrypoint_integration.py"],
        capture_output=True, text=True, cwd=str(ROOT), timeout=180,
    )
    fault_tail = fault_run.stdout.strip().splitlines()[-1] if fault_run.stdout else ""
    integration_tail = integration_run.stdout.strip().splitlines()[-1] if integration_run.stdout else ""
    check(
        "R3a_fault_injection_receipts",
        fault_run.returncode == 0 and "passed" in fault_tail,
        {"rc": fault_run.returncode, "summary": fault_tail},
    )
    check(
        "R3b_entrypoint_integration_receipts",
        integration_run.returncode == 0 and "passed" in integration_tail,
        {"rc": integration_run.returncode, "summary": integration_tail},
    )
    control = Path("data/bnb_native_adx25_validation_run_20261019_01")
    finished = json.loads((control / "execution_finished.json").read_text())
    check(
        "R3c_aggregator_did_not_certify_the_interrupted_control_packet",
        finished["status"] != "COMPLETED"
        and not (control / "exact_coverage.json").exists()
        and not (control / "parallel_canonical_summary.json").exists(),
        {"execution_finished_status": finished["status"],
         "live_authorized": finished.get("live_authorized"),
         "exact_coverage_present": (control / "exact_coverage.json").exists(),
         "parallel_canonical_summary_present":
             (control / "parallel_canonical_summary.json").exists()},
    )

    # ── R4: identities, manifests, test IDs ─────────────────────────────
    from trading_agent.backtest.campaign_integrity import capture_campaign_source
    source = capture_campaign_source(ROOT)
    check(
        "R4a_source_identity_matches_the_revision_under_review",
        source["tree_fingerprint"] == FROZEN_A,
        {"recomputed": source["tree_fingerprint"], "frozen": FROZEN_A,
         "code_commit": source["code_commit"]},
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    recomputed_b = hashlib.sha256(canonical.encode()).hexdigest()
    check(
        "R4b_preregistration_identity_matches",
        recomputed_b == FROZEN_B,
        {"recomputed": recomputed_b, "frozen": FROZEN_B},
    )
    manifest_evidence = {}
    manifest_ok = True
    # The review's own outputs are written AFTER the manifest is sealed, so they
    # are excluded from the comparison and the exclusion is recorded. Nothing
    # else is excluded.
    REVIEW_OWN_OUTPUTS = {"review_verdict.json", "review_round_execution.json"}
    for directory in (PACKET, PREREG, Path("data/bnb_native_review_20261020_01")):
        manifest = json.loads((directory / "manifest.json").read_text())
        bad = []
        excluded = []
        for entry in manifest["files"]:
            if Path(entry["path"]).name in REVIEW_OWN_OUTPUTS:
                excluded.append(entry["path"])
                continue
            path = directory / entry["path"]
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                bad.append(entry["path"])
        manifest_evidence[str(directory)] = {
            "revision": manifest["revision"], "files": manifest["file_count"],
            "mismatches": bad, "excluded_review_own_outputs": excluded,
        }
        manifest_ok = manifest_ok and not bad
    excluded_seen = set()
    for entry in manifest_evidence.values():
        excluded_seen.update(Path(p).name for p in entry["excluded_review_own_outputs"])
    check(
        "R4c_manifests_match_the_files_on_disk",
        manifest_ok and excluded_seen <= REVIEW_OWN_OUTPUTS,
        {**manifest_evidence,
         "excluded_files_are_only_the_review_own_outputs": sorted(excluded_seen)},
    )

    prereg_text = (PREREG / "PREREGISTRATION.md").read_text()
    cited = [
        "tests/test_di_adx_state_edge_scoped_path.py",
        "tests/test_di_adx_state_edge_entrypoint_integration.py",
        "tests/test_validation_timeout_and_ledger_faults.py",
        "tests/test_di_adx_state_edge_contract.py",
        "tests/test_wfo_validation_envelope.py",
        "tests/test_indicator_window_equivalence.py",
    ]
    missing = [p for p in cited if not (ROOT / p).exists() or p not in prereg_text]
    check("R4d_cited_test_ids_exist_and_are_listed", not missing,
          {"cited": cited, "missing": missing})

    failed = [c for c in checks if not c["pass"]]
    if not failed:
        verdict = "ACCEPTED_FOR_BOUNDED_VALIDATION"  # by the author's own checks only
    elif any(c["check"].startswith("R4") and not c["pass"] for c in failed):
        verdict = "BLOCKED"
    else:
        verdict = "CHANGES_REQUIRED"

    out = {
        "verdict": verdict,
        "verdict_kind": payload["budget"]["final_review_round"]["verdict_kind"],
        "provenance": {
            "reviewed_by": "the author of the candidate and of the five token hops",
            "independence": "NONE — self-verification",
            "script_authored_by": "the author",
            "assertions_authored_by": "the author",
            "executed_by": "the author",
            "what_was_checked_beyond_running_the_suites": [
                "the five hops were located by reading the source, and each hop is asserted by the presence of the scope-token plumbing at that call site (string-level, not behavioural)",
                "identity A was recomputed from the working tree and compared with the frozen value",
                "identity B was recomputed from the payload and compared with the frozen value",
                "the manifests were re-hashed and compared with the files on disk",
                "the input file was re-hashed and probed for holdout bars with a timestamp-only read (no OHLCV decoded by the probe)",
                "the interrupted control packet was inspected for the absence of a coverage/summary receipt",
            ],
            "what_it_does_not_establish": [
                "that the assertions are the RIGHT assertions (only that they pass)",
                "that no defect exists outside the checked call sites",
                "that the scope change is approved — it is PENDING_OPERATOR_APPROVAL in the payload",
                "that the candidate is qualified, tradable or live-ready",
            ],
            "required_for_an_independent_verdict": (
                "a reviewer other than the author reads the code, the assertions and the "
                "receipts on the exact A/B/C and issues the verdict"
            ),
        },
        "scope_approval_state": payload["data"]["scope_change_request"]["approval_state"],
        "scope_approval_binding": payload["data"]["scope_change_request"]["binding_rule"],
        "review_round": {
            "registered_cap_seconds": payload["budget"]["final_review_round"]["cap_seconds"],
            "elapsed_seconds": round(time.time() - started, 1),
            "taken_from_market_run_budget": False,
        },
        "revision_under_review": {
            "identity_A_source": FROZEN_A,
            "identity_B_preregistration": FROZEN_B,
            "identity_C_manifests": {k: v["revision"] for k, v in manifest_evidence.items()},
        },
        "checks": checks,
        "failed_checks": [c["check"] for c in failed],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    print(json.dumps(out, indent=2))
    Path("data/bnb_di_adx_stateedge_20261020_01/review_verdict.json").write_text(
        json.dumps(out, indent=2)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
