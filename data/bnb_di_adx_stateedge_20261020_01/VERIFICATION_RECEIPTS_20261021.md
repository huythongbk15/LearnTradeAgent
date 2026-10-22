# Verification receipts — 2026-10-21 (engineering verification of the ledger correction)

Every command ran through `scripts/qwenpaw_control/controlled_exec.py` with a
timeout and a **unique** result file. Synthetic only: no market data, no market
cell, no holdout. A pytest process belonging to another workspace was running
throughout and was **not** stopped — it is a confounder for every timing below.

**Source identity under test** (read from the packet the integration produced, so
it is the source that actually executed):

```
tree_fingerprint  e4866a1c2423613df3b55ddcebb76b92095b83cea943f320c8d00abfbd71c836
dirty_diff_sha256 (identical in the packet and in the working tree)
```

The packet's `research_protocol.json` source hash equals the working tree's
source hash, so the receipts below belong to that source.

## Tranche 1 — fast (cap 240 s, used 84.6 s + 55.1 s final AC re-run)

| Step | Command | Result file | rc | Result | Elapsed |
|---|---|---|---:|---|---:|
| T0 | `pytest -o addopts="" tests/test_attempt_ledger_snapshot.py` | `/tmp/tr1_t0_attempt_ledger_snapshot.json` | 0 | **4 passed** | 3.4 s |
| T1–T5, T6b | `pytest -o addopts="" tests/test_ledger_and_binding_invariants.py` (first run) | `/tmp/tr1_invariants.json` | 1 | 1 failed, 13 passed | 2.9 s |
| T1–T5, T6b | same file, after fixing **my own** test defect (the failure was not injected, so the write could not fail) | `/tmp/tr1_invariants_r2.json` | 0 | **15 passed** | 2.9 s |
| T6 | `pytest -o addopts="" tests/test_di_adx_state_edge_launcher_integration.py -k l1` | `/tmp/tr1_t6_launcher_plan.json` | 0 | **2 passed** (five frozen windows + 15 cells; the CLI without `--val-months 6` differs) | 14.0 s |
| T8 | `scripts/run_acceptance_ac01_15.py` | `/tmp/tr1_t8_acceptance.json` | 0 | AC01–AC15 **ALL PASS** | 61.4 s |
| T8 (final) | `scripts/run_acceptance_ac01_15.py` **after** the preregistration-root seam | `/tmp/tr1_t8_acceptance_final.json` | 0 | AC01–AC15 **ALL PASS** (all 15 AC blocks rc=0) | 55.1 s |

The earlier AC run (before the seam) is **not** claimed for the final source; it
was re-run on the final source.

## Tranche 2 — one launcher integration (cap 400 s, used 481.6 s — **over the tranche cap by 81.6 s**)

| Attempt | Result file | rc | Outcome | Elapsed |
|---|---|---:|---|---:|
| 1 | `/tmp/tr2_t7_launcher_validation.json` | 1 | gate refused the run: `approval_state, approved_at, approved_by, approved_scope, costs, identity_A_source, input_sha256, total_timeout, windows` — **the binding gate working as designed** (the registered payload is PENDING and is not the synthetic one) | 4.2 s |
| 2 | `/tmp/tr2_t7_launcher_validation_r2.json` | 1 | same refusal — the preregistration root was resolved at **import** time, so the synthetic root was ignored | 4.8 s |
| 3 | `/tmp/tr2_t7_launcher_validation_r3.json` | 1 | gate passed, worker ran 309.6 s, the synthetic cell **hit its 300 s cell timeout** → FAILED → no report → worker refused to certify (`validation report is unavailable`) → WORKER_ERROR | 309.6 s |
| 4 | `/tmp/tr2_t7_launcher_validation_r4.json` | 1 | gate refused: `windows` — the synthetic fold geometry did not match the declared windows | 4.1 s |
| 5 | `/tmp/tr2_t7_launcher_validation_r5.json` | 0 | **1 passed** — launcher integration completes with terminal coverage | 158.9 s |

Attempts 1, 2 and 4 were my own test/seam defects (root resolution timing, a
guessed window set); attempt 3 was a genuine product behaviour (a timeout must
not yield a valid completion) that also consumed the budget. The final attempt
was made cheap by making the synthetic bars **flat before the validation
window**, so the cell writes orders only where the selection contract looks.

## What the passing integration (attempt 5) proves

Packet: `/tmp/pytest-of-huythong/pytest-4599/test_l2_validation_launcher_co0/packet_run`

| Invariant | Evidence |
|---|---|
| The scope token reaches registry/adapter/simulator | the cell ran in a spawned process with `strategy_id=di_adx_state_edge`, `params={adx_period:14, adx_threshold:25.0}` and produced BUY/SELL fills of BNB/USDT |
| The terminal receipts are produced | `execution_finished.json` status **COMPLETED**, exit 0, elapsed 154.5 s, sampled group peak RSS 706 MiB, `live_authorized: false`; `exact_coverage.json` status **PASS** (1 fold) |
| Executed inventory matches planned | `cell_timings.jsonl`: 1 planned cell, `attempts_made: 1`, `max_retries: 0`, `workers: 1`, `execution_path: fresh`; the ledger row shows `attempts: 1` |
| The ledger reflects the real execution | `cell_ledger/<key>.json`: `attempt_outcomes: ["COMPLETED"]`, `outcome: COMPLETED`, `artifact_status: COMPLETED`, `elapsed_seconds: 149.6`, `registry_counted: null`, `registry_accounting_status: UNKNOWN_NOT_RECONCILED` |
| The report carries the right subject | the cell id and params are the candidate's ADX 14 / threshold 25 at cost `1x`; the report path is under the cell's own attempt directory |
| `VALIDATION_COMPLETE` does **not** mean SELECT / qualified / live | the summary carries `verdict: VALIDATION_COMPLETE`, `passes_hard_gates: false`, `qualification_status: NOT_QUALIFIED_VALIDATION_ONLY`, `all_folds_selected: false`, `sample_complete: true`, `live_authorized: false`; the fold's own decision is **ABSTAIN** with 35 validation trades and Sharpe −10.00 (`VALIDATION_SHARPE_NOT_ABOVE_FLOOR`) — enough trades, but not selected |
| A timeout yields **no** valid completion | attempt 3: the cell FAILED at 300.78 s, `execution_finished.json` = WORKER_ERROR, no coverage or summary written, `live_authorized: false`, the selection decision stayed ABSTAIN with `report_path: nonexistent/report.json` |
| Source attribution is not "unknown" | the candidate's strategy code SHA is carried through the scoped `_compute_strategy_code_sha` path; the protocol's `source` hash matches the tested tree |

## A note on the numbers 35 and 35-39 (they are different quantities)

- **"35-39 state episodes per 1080-bar window"** — a measurement of the
  candidate's *signal opportunity* on the frozen pre-holdout prefix, an upper
  bound on entries. It is **not** a trade count and carries no economic claim.
- **"35 BUY actions -> 30 fills -> 28 closed trades"** — the **control's**
  `ma_adx` funnel across the five measured windows at 2x (from
  `../bnb_native_review_20261020_01/RESULTS.md`).
- **"35 validation trades"** in the synthetic integration above — the
  **synthetic** fold's trade count, and the reason its decision was ABSTAIN
  (Sharpe below the floor). On real bars the candidate's trade count is
  **UNKNOWN**.

## Supplementary verification round — after independent review #1 (rev18 fixes)

Approved as a separate bounded envelope (420 s); **used 290.3 s**. Every command
ran through `controlled_exec` with a timeout and a unique result file; synthetic
only; no market data, no market cell, no holdout.

| # | Command | Result file | rc | Result | Elapsed |
|---|---|---|---:|---|---:|
| S1 | `pytest -o addopts="" tests/test_validation_only_workflow.py` | `/tmp/sup1_validation_only_workflow.json` | 0 | **29 passed** — the frozen control's path and the 7 previously broken tests work again (finding F1) | 98.1 s |
| S2 | `pytest -o addopts="" tests/test_ledger_and_binding_invariants.py tests/test_attempt_ledger_snapshot.py` | `/tmp/sup2_invariants_r2.json` | 0 | **20 passed** — includes a new regression that a **non-scoped** study is not blocked by the binding | 4.6 s |
| S3 | `pytest -o addopts="" tests/test_di_adx_state_edge_launcher_integration.py -k l1` | `/tmp/sup3_launcher_plan.json` | 0 | **2 passed** — the five frozen windows and 15 cells through the registered command | 17.8 s |
| S4 | `scripts/run_acceptance_ac01_15.py`, **stdout + source identity + run directory re-captured into the packet** | `/tmp/sup4_acceptance.json` | 0 | AC01–AC15 **ALL PASS**; captured source `30283d31…` **== the frozen A**; run dir copied to `acceptance_run/` (finding F7) | 50.0 s |
| S5 | `pytest -o addopts="" tests/test_di_adx_state_edge_launcher_integration.py -k l2` | `/tmp/sup5_launcher_integration.json` | 0 | **1 passed** — the launcher integration completes with terminal coverage | 119.8 s |

A first attempt at S2 failed on 4 tests: my test helper built a protocol whose
`spec` carried no `strategy_id`, so the binding correctly treated it as a
non-scoped study and skipped. The helper was corrected to carry the subject
(and a regression was added asserting that a non-scoped study is not blocked).

**What the supplementary round closed**: F1 (control path + 7 tests), F2/F3
(approval verifier + sealed assertion hashes), F4/F5/F6 (subject binding,
root pinning, gate on the qualification path), F16/F17 (ledger accounting
fields, token-map consistency), F18 (fault-test framing), and **F7** (the
acceptance evidence is now sealed inside the packet **on identity A**).

**CLOSED 2026-10-21 (F9)**: the native packet's episode/funnel measurements were
re-executed on the hash-verified frozen pre-holdout input under the current
source, and **all seventeen quantities quoted in the native `RESULTS.md` were
reproduced exactly** (0 mismatches). Budget 30 s, spend 20.0 s, no retry; the
old report was kept and only its caveat block was rewritten afterwards. The
record is `../bnb_native_review_20261020_01/F9_REEXECUTION_DIFF.json` and the
new outputs are in `../bnb_native_review_20261020_01/reexecution_20261021/`.

## Budget accounting

| Tranche | Cap | Cap source | Used | Deviation |
|---|---:|---|---:|---|
| Tranche 1 | 240 s | **my proposal** (`TEST_PLAN_FOR_APPROVAL.md`) | 84.6 s | — |
| Tranche 1 final AC re-run | (inside tranche 1) | as above | 55.1 s | tranche 1 total 139.7 s |
| Tranche 2 | 400 s | **my proposal**; the payload registers `budget.final_review_round.cap_seconds = 600` and `budget.supplementary_review_budget.registered_test_seconds = 240` — the 400 s tranche-2 split is a self-imposed proposal, not a registered policy | 481.6 s | **over the proposal by 81.6 s** |
| **Total** | **640 s** | **granted in chat by the operator, not recorded on disk** | **621.3 s** | within the granted total, over the tranche-2 split |

**Timing provenance.** The elapsed figures in the tables above are the wall clock
observed around each `controlled_exec` invocation, which includes process
start-up. The authoritative per-command figure is the `elapsed_sec` field inside
the cited result file, which is ~0.15–0.45 s lower (e.g. T0 3.4 s wall vs
3.06 s in the result file; T7 158.9 s wall vs 158.45 s in the result file).
Both are given so a reader can reconcile row by row.

**Not done:** no market run, no market data read, no holdout, no OOS, no
sensitivity, no live/paper, no download, no push, no merge. No operator approval
was written; `approval_state` remains `PENDING_OPERATOR_APPROVAL`.
