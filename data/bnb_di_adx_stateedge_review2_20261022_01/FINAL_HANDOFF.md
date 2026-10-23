# Final engineering handoff — rev20 — 2026-10-22

Status: VERIFIED_PENDING_EXTERNAL_REVIEW. Not READY_TO_RUN or QUALIFIED.

## Completed

- Executed 30 ledger/binding/envelope regressions; 31 control/CLI-plan tests;
  one complete launcher synthetic integration; all passed. No retries.
- Integrated AC01–AC15 ALL PASS on the current A. Targeted lint PASS.
- Reproduced F9 measured metrics: 59 raw crossovers / 41 unique passing signal
  bars; ADX25 episodes 35–39 (mean36.8); 2x/stress funnel 35 BUY -> 30 fills ->
  28 closed + 2 carry-out. 1x has only four completed windows, 29 -> 25 -> 24+1.
- Preserved three old manifests, seven drifted-current file copies, hashes of
  their expected/actual bytes, a full current source hash map and diff vs HEAD.
- Registered source already matched the current payload. Kept its decisions
  and PENDING approval unchanged, then sealed current evidence as rev20.
- Current receipts are independently hash-referenced under each manifest's
  evidence/review2_verification.json. Old acceptance_run/ remains historical;
  old PASS results have not been relabelled as current.

## Frozen identities

A: `c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74`

B: `1338572d57628e9ba1c29b655856547d1c945f4695ee7cc9aef0545c8ce88145`

C: rev20 in the three existing packets. See verification_index.json and
verified_source.json for full commit/dirty identity and receipt hashes.

Final production approval-binding verification returned rc3 as EXPECTED:
`BINDING COHERENT`, source A matches, assertions unchanged, B matches all three
manifests and every listed entry rehashes. rc3 means pending approval, not an
engineering test failure. Receipt: trading_review2_binding_final_20261022_01.json.

Exact historical rev19 source differences cannot be proven: the old freeze had
no sealed per-file source map. This limitation is disclosed, not repaired by
fabricating missing history. Current-source execution evidence supersedes old
source acceptance for engineering verification.

## Still requires another person/authority

1. Final independent review on A/B/C rev20. The present reviewer contributed an
   earlier ledger patch; this handoff is NOT clean-context independent approval.
2. Operator scope approval via the four documented fields, producing a new B;
   refresh manifests with preserved revisions and verify binding.
3. Separate explicit authorization for one market validation-only run:
   15 cells, 300s/cell, 5400s total, 1 worker, 2048MiB sampled RSS, zero retries.
   No outer OOS, sensitivity, holdout, paper, broker or live.

Do not rerun suites merely because approval fields change if source/assertion
equivalence is verified. Any substantive later source/payload change instead
requires affected verification/review again. Do not run market automatically.

No source/strategy/gate change was made in this completion round; only evidence
capture/revision metadata and tracker updates. Old control remains FAILED and
INCOMPLETE. F9 closes numerical reproduction, not strategy qualification.
