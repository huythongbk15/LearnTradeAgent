# Review 2 — BLOCKED: current tree differs from frozen rev19

Date: 2026-10-22 (Asia/Saigon).

Direct source/artifact review. The reviewer authored an earlier ledger fix;
this is not a clean-context independent review of that earlier contribution.
No shell/Python/tests/AC/market execution, approval or source change occurred.
This report is outside the three sealed packets; no manifests were refreshed.

## Verified by filesystem reads and SHA256

- Read AGENTS.md, first-review findings, binding/ledger modules and launcher
  binding call sites on validation and qualification paths.
- Rehashed 77 + 11 + 13 entries of the three rev19 manifests.
- All nine sealed assertion-file hashes match current files.
- Recomputed A from 480 src/scripts Python files using the sorted path-to-hash
  map and compact JSON specified in capture_campaign_source/content_hash.
  Only strings occur in this map: no float serialization discrepancy applies.
  Git commit and dirty-diff SHA were not independently recomputed.

Expected rev19 A:
`30283d31a24aa20455d0c82863b8b4f0ab1aba3e33ded38d5266e10447db7cf0`

Observed A:
`c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74`

Seven manifest entries mismatch:

| Packet | Entry |
|---|---|
| bnb_di_adx_stateedge_20261020_01 | acceptance_receipt.json |
| bnb_di_adx_stateedge_20261020_01 | make_reviewers_working_set.py |
| bnb_di_adx_stateedge_20261020_01 | pre_approval_payload.json |
| bnb_di_adx_stateedge_20261020_01 | revisions/rev11/README.md |
| bnb_di_adx_stateedge_prereg_20261020_01 | PREREGISTRATION.md |
| bnb_di_adx_stateedge_prereg_20261020_01 | pre_approval_payload.json |
| bnb_di_adx_stateedge_prereg_20261020_01 | preregistration_payload.json |

All 13 listed native-review entries match. Current payload version is 14;
approval is PENDING_OPERATOR_APPROVAL. Stored AC evidence references expected A,
not the changed current tree. Current B was not recomputed using Python.

## Source observations, not runtime acceptance

Singleton-grid normalization and subject strategy/symbol/timeframe checks are
present. Binding is called before the launcher branches to validation or
qualification and again in the validation worker; non-scoped studies bypass
the scoped binding. Ledger derives unique attempt snapshots, protects its
accounting fields from extra context, leaves registry accounting UNKNOWN, and
publishes with atomic replacement and fsync. This does not certify the whole
execution chain on the changed source.

## Required handoff

1. Identify current source/payload/evidence delta from rev19. Preserve user and
   other-agent edits. Do not refresh manifests merely to hide mismatch.
2. Retain intentional changes as a new revision with preserved old evidence;
   explain unavailable overwritten history without fabricating recovery.
3. Verify regressions and AC needed by the actual delta in a recorded bounded
   budget. Do not attribute old PASS results to new source.
4. F9 remains open: if native analysis results support the run rationale,
   rerun the three scripts on the approved input into fresh outputs and diff.
5. Freeze/review final A/B/C, then obtain operator scope/run approval. No market
   run is authorized by this report.

Execution blocker: TOOL_UNAVAILABLE for execute_shell_command. AGENTS.md forbids
substituting another shell tool. File reads/hashes were possible; git, Python,
tests, AC and F9 executions were not attempted.
