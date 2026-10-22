# Independent review round 1 — rev16 — verdict: CHANGES_REQUIRED

Two independent reviewers, each with a **fresh context** (no inherited
reasoning, no prior turn history), working **read-only** (no pytest, no market
execution — the approved budget was already spent). They were given the
revision's identities, the artifact index, and the review checklist; each had to
derive its own verdict from the artifacts.

| Reviewer | Focus | Verdict |
|---|---|---|
| R1 | code + assertions: the five scope-token hops, the binding gate, the ledger | **CHANGES_REQUIRED** |
| R2 | evidence + preregistration contract + identities | **CHANGES_REQUIRED** |

Both reviewers independently confirmed the identities: **A matched**
(`e4866a1c…` for rev16) and **B matched** (`4b8e2eb7…` for rev16), recomputed
with the repository's own `capture_campaign_source` and
`preregistration_fingerprint`.

Both also confirmed a set of things as **correct**: the five hops are genuinely
wired end to end; a wrong or missing token never falls back to another
strategy; the ledger's four properties hold (derive-from-unique-paths, atomic
replace, registry left UNKNOWN, never read to certify completion); RUNNING is
decided by `/proc` liveness rather than by a missing result; the singleton-grid
comparison is correct; the candidate's ADX/DI arithmetic is byte-identical to the
control's and it carries no MA component; the control's strategy code is
unmodified; the data binding hash matches, the file holds 0 reserved-holdout
bars, and the gate contract fingerprint is the frozen one; and no packet or
tracker text presents a qualification, OOS, live-readiness or profitability
claim.

## Consolidated findings and their status in rev17

| # | Severity | Finding | Status |
|---|---|---|---|
| F1 | HIGH | The binding gate was **unconditional**: with no scope token it raised, so the **frozen control `ma_adx` was no longer runnable** through the sanctioned entrypoint, and **7 pre-existing tests** in `tests/test_validation_only_workflow.py` broke. The author's verification never ran that file after the gate was added. | **FIXED (code), NOT YET EXECUTED** — the gate now applies only to a scoped candidate (`preregistration_binding.py`, scoped-candidate early return) and is a no-op for every other study. The 7 tests must be re-run. |
| F2 | HIGH | The sealed approval verifier consulted a payload `"status"` field that no longer exists, so the documented approval ceremony could never report `BINDING COHERENT`; its pre-approval snapshot was a rev11-era copy, so 26 legitimate evolution fields were reported as "unexpected changes" (i.e. as approval laundering); and it printed a remediation for a `--snapshot` flag that does not exist. | **FIXED (tooling), NOT YET EXECUTED** — the `status` comparison is removed, the snapshot is refreshed to the current payload, differences outside the approval fields are reported as such with a precise remedy, and the bogus flag reference is gone. |
| F3 | MEDIUM-HIGH | The verifier's "assertion files unchanged" check was **vacuous**: no `tests/*.py` path is listed in any manifest and `tests/` is not covered by identity A, so the comparison was skipped and the check printed "unchanged: True" without comparing anything — while `APPROVAL_PROCEDURE.md` relied on it to justify not re-running the suites. | **FIXED** — the assertion files are now hash-sealed in `evidence/assertion_files.json` inside the packet (covered by manifest C) and the verifier compares against that record and **fails loudly** when a file is unrecorded or differs. |
| F4 | MEDIUM | The binding gate never bound **which** strategy is executed: it compared the parameter values, not the subject, so a future grid map giving another strategy the same axes would satisfy it. | **FIXED (code), NOT YET EXECUTED** — `strategy_id`, `symbol` and `timeframe` are now checked against the registered subject. |
| F5 | MEDIUM | The preregistration root was operator-controlled and **not pinned** by the frozen protocol — the recorded value was decorative, and the test affordance was usable for real. | **FIXED (code), NOT YET EXECUTED** — the run must resolve the same root the frozen protocol records, and the packet must exist under the repository root. |
| F6 | MEDIUM | The binding gate was **not reached on the qualification path**: `--strategy di_adx_state_edge --scope-token …` without `--validation-only` would run outer OOS + sensitivity with no preregistration binding at all. | **FIXED (code), NOT YET EXECUTED** — the gate now runs unconditionally in `main()`, before the branch, on both paths. |
| F7 | MEDIUM | The sealed acceptance evidence in the packets was captured on **other source identities** (`ca12f951…`, `1b165019…`) than the frozen A, so A asserted a source with no sealed acceptance receipt; the final re-run existed only unsealed under `/tmp`. | **OPEN — needs a budgeted AC re-run re-captured into the packet.** A note now records the distinction explicitly; the artifact itself must be re-sealed under A. |
| F8 | MEDIUM | The review script read the same removed `status` field; the packet's declared binding contract listed a non-existent field (`scope_change_status`) and omitted the field the gate actually checks (`approved_scope`). | **FIXED** — the payload's `execution_binding.checked_fields` now names the real fields, including `approved_scope`, `strategy_id`, `symbol`, `timeframe`; the review script is a historical self-verification artifact and its payload is the rev10 one. |
| F9 | MEDIUM | The native packet's `RESULTS.md` is written against `1b165019…` while its manifest declares A; the analysis scripts import only `enhanced_ma` (unchanged) and `layer_accounting` imports no `trading_agent` module, so the results are plausibly source-stable — a static argument, not a re-execution. | **OPEN — needs a re-execution** of the three analysis scripts on the frozen input and a diff against `RESULTS.md`, which is a market-data operation on the pre-holdout file and therefore outside the reviewer's mandate. |
| F10 | MEDIUM | The revision archive was off by one: `revisions/rev15/manifest.json` contains `"revision": "rev14"`, and no archived manifest carries `"revision": "rev15"`, so rev15's identities are unrecoverable. | **DISCLOSED, NOT REPAIRABLE** — the archive is now explicit about which revisions are recoverable and which were overwritten in place. |
| F11 | MEDIUM | A stale `review_verdict.json` (rev10) bearing the literal string `ACCEPTED_FOR_BOUNDED_VALIDATION`, bound to a different A/B/C, sat inside the live packet; the procedure justified excluding it as "written after sealing", which was factually wrong (it predates the sealing). | **FIXED** — the file is archived under `revisions/rev10/` with a note, and the procedure's justification is corrected. |
| F12 | MEDIUM | The receipts' budget table headed its column **"Approved"**, although the only on-disk source was a *proposal*; the 400 s tranche-2 cap existed only in that proposal, while the payload registers a 600 s cap. | **FIXED** — the receipts now state exactly what was granted, where, and that the 400 s split was a self-imposed proposal. |
| F13 | LOW | The receipts' elapsed-time column did not reproduce from the cited result files (a systematic ~0.15–0.45 s offset = process start-up). | **FIXED** — the receipts now give the wall-clock figure and name the result file's own `elapsed_sec` as the authoritative one. |
| F14 | LOW | `PREREGISTRATION.md` §0 showed rev2's identity A as the "frozen value" while §12.1 showed the current one. | **FIXED** — §0 now carries the current A/B/C. |
| F15 | LOW | Number collision: "35–39 state episodes per window" and "35 BUY actions → 30 fills → 28 closed trades" in one sentence, and the synthetic fold's 35 trades read in isolation as if the candidate cleared the ≥30-trade gate (on real data that is UNKNOWN). | **FIXED** — the quantities are disambiguated and the synthetic figure is labelled as synthetic. |
| F16 | LOW | The ledger's `extra` merge could override the accounting fields it promises to keep UNKNOWN, and a cache hit with no local attempt root produced a row whose `outcome` was UNKNOWN while its `artifact_status` was COMPLETED. | **FIXED (code), NOT YET EXECUTED** — `extra` is merged first and the accounting fields are written last; a cache-hit row takes its outcome from the artifact and is marked `attempt_history_complete: false`. |
| F17 | LOW | The two scope-token maps were independent, and the binding between them rested on coincidence. | **FIXED (code), NOT YET EXECUTED** — the preregistration-path map now lives beside the token map, and the binding refuses to load if they disagree. |
| F18 | LOW | The fault-injection test's narrative claimed INTERRUPTED while its fixture used a **live** pid, and the test asserted no classification. | **FIXED** — the rule itself is covered by the invariant suite (live pid → RUNNING, dead pid → INTERRUPTED); the fault test's framing no longer claims a classification it does not make. |
| F19 | LOW | The preregistration claimed identity C covered "test files" when it did not. | **FIXED** — C now covers the sealed `evidence/assertion_files.json`, and the preregistration says so. |

## Verification of the fixes (supplementary round, 290.3 s of a 420 s envelope)

Every fix above is now **executed**, not merely written:

| Finding | Verification |
|---|---|
| F1 | `tests/test_validation_only_workflow.py` → **29 passed** (the control's path and the 7 broken tests work again); a new regression asserts a **non-scoped** study is not blocked |
| F2, F8 | the approval verifier no longer consults a removed field; the snapshot is the current payload; differences outside the approval set are reported by name |
| F3 | the assertion hashes are sealed in `evidence/assertion_files.json` (inside the packet, covered by C) and the verifier fails loudly when a file is unrecorded |
| F4, F5, F6, F16, F17 | the gate now binds the subject, pins the root to the frozen protocol, runs on the qualification path, protects the ledger's accounting fields and refuses to load if the two scope-token maps disagree |
| F7 | **closed**: AC01–AC15 re-run on identity A with stdout, source identity and the run directory **captured into the packet** (`acceptance_stdout.txt`, `source_identity_{before,after}.json`, `acceptance_run/`) |
| F18 | the fault test no longer claims a classification it does not make |
| F9 | **still OPEN** — the native packet's episode/funnel measurements must be re-executed on the frozen pre-holdout input and diffed against `RESULTS.md` (a market-data operation, outside the reviewers' mandate) |

## What the reviewers could NOT settle read-only

That AC01–AC15 passes on the final source; that the cited test files still
produce the cited results; that the native packet's episode/funnel measurements
reproduce under A (a market-data operation on the pre-holdout file, outside the
reviewers' mandate); that the runtime gate refuses a wrong token or a pending
approval **before the first cell** (the refusals were observed in the recorded
tranche-2 attempts, not re-executed); whether the 640 s budget was ever
approved **on the record** (it was granted in chat, not on disk); and the
behaviour of the ledger under a genuinely killed worker.

## Consequence

The verdict is **CHANGES_REQUIRED**. The engineering fixes are in rev17 but are
**not executed** — the approved budget is exhausted. No operator approval was
written; `approval_state` remains `PENDING_OPERATOR_APPROVAL`. No market run,
no outer OOS, no sensitivity, no live, no paper, no broker, no download, no
push, no merge. The author's own verification run remains **self-verification**,
never an independent verdict.
