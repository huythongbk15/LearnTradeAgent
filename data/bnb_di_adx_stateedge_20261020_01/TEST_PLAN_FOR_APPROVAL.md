# Test plan for approval — proposed, NOT executed

No execution budget is approved. This is the list I propose to run, with the
time each step is expected to take, so the operator can approve a bounded
budget. The design point is the reviewer's: **the schema and ledger defects are
caught by fast unit tests**, so the 220–290 s launcher integration is needed
**once**, at the end, not repeatedly.

Every step aborts the tranche at the first failure. Nothing is retried.

## Tranche 1 — fast (target ≈ 60 s, cap 240 s)

**T0 runs first** (`tests/test_attempt_ledger_snapshot.py`, 4 tests, ~2 s): the
ledger correction of 2026-10-21 is **not** covered by any earlier result —
repeated snapshot / cache hit must not duplicate attempts, a newly added attempt
must update the snapshot, a failed atomic replace must preserve the previous row,
and publication must fsync file and directory.

| # | Test | What it catches | Est. |
|---|---|---|---|
| T1 | unit: `normalise_singleton_grid` — a one-element grid reduces to its scalar; a two-element grid is rejected; the binding's `params` check passes for the registered grid and fails for a changed one | **P1-1** (the grid/scalar mismatch that blocked a correct run) | <1 s |
| T2 | unit: the binding's approval fields — each of `approval_state`, `approved_scope`, `approved_by`, `approved_at` missing or wrong ⇒ that field is named; all four correct ⇒ no failure. Also: a payload whose recomputed B no longer matches the manifest is refused | **P1-2** (two authoritative states; `approved_scope` unchecked; the laundering path) | <1 s |
| T3 | unit: `classify_attempt` — result + COMPLETED ⇒ COMPLETED; result + failed artifact ⇒ FAILED; start without result **and the worker alive** ⇒ RUNNING; start without result **and the worker gone** ⇒ INTERRUPTED; a missing result alone must never be read as "the process died" | **P2-1** (a live attempt misread as INTERRUPTED) | <2 s |
| T4 | unit: the **derive-and-replace** snapshot — the same attempt set published twice (fresh and cache hit) yields the same counts and no duplicated history; a newly added attempt updates the snapshot and selects the newest outcome; **two evaluations with different cache keys are not joined**; a failing write lands in `cell_ledger/_ledger_errors.jsonl`; an unreconciled registry stays `registry_counted: null` / `UNKNOWN_NOT_RECONCILED` | **P2-2**, plus the two invariants the 2026-10-21 regressions do not yet cover (different cache keys; the durable error log) | <3 s |
| T5 | unit: the producer writes `started_at_epoch` in the worker request record and in the runner's `execution_started.json`; the ordering uses it and never the directory name | **P2-1** (the field the worker never wrote) | <1 s |
| T6 | launcher **plan** level: `--preflight-only` with the registered geometry ⇒ exactly the five frozen validation windows `[2360,3440) [2900,3980) [3440,4520) [3980,5060) [4520,5600)`, 15 inner cells (5 folds × 3 costs), params ADX 14/25, one worker, zero retries, no outer/sensitivity/holdout evaluation; the same CLI **without** `--val-months 6` ⇒ different windows | the geometry blocker, through the registered command rather than a hand-built WFOSpec | ~5 s |
| T6b | binding unit: each of `params`, `input hash`, `source identity`, `windows`, `costs`, `retries`, `cell/total timeout`, `RSS` and `scope token` altered ⇒ that field is named and the run is refused **before the first cell**; a **pending** approval cannot start a cell; the gate is reached from the launcher **and** the worker, not only through one unit-tested helper | the binding itself, beyond the happy path | <2 s |
| T8 | `scripts/run_acceptance_ac01_15.py` | the mandated acceptance evidence on the **final source at the moment of the run** — rev12's and rev14's AC results do **not** transfer, and the manifests are rewritten only in Phase D, after this passes | ~45 s |

## Tranche 2 — one full launcher run (target ≈ 250 s, cap 400 s)

| # | Test | What it covers | Est. |
|---|---|---|---|
| T7 | launcher **validation-only** end to end on synthetic data: `execution_finished.json` COMPLETED, summary `VALIDATION_COMPLETE` with `qualification_status` / `all_folds_selected` / `sample_complete`, `exact_coverage.json` PASS, executed cells == planned cells, the evaluation-level ledger row present with its attempt history | the chain the unit tests cannot reach: the binding gate firing on the real path, the terminal coverage, the ledger as written by the real runner | ~250 s |

## Budget asked for

| Tranche | Target | Cap |
|---|---:|---:|
| Tranche 1 (T0, T1–T6, T6b, T8) | ~60 s | **240 s** |
| Tranche 2 (T7) | ~250 s | **400 s** |
| **Total** | ~310 s | **640 s** |

Both tranches are **synthetic**; no market data is read, no market cell is run,
the holdout is not opened, and the registered campaign is not started.

If Tranche 1 fails, Tranche 2 is not run: the failure is fixed first, which is
the behaviour that caused the previous overrun.

## Explicitly out of scope for this budget

- Any market run of the registered campaign.
- Any re-run of the full test suite (only the files listed above).
- Any change to the strategy, the gates, the risk policy or the scope.

## Not covered by this plan

- The four out-of-scope paths that still resolve the registry without the token
  (sensitivity arm, promotion gate, calibration, live admission). They are
  recorded in the payload as gaps for their own stages and are **not** exercised
  here — they must fail clearly for this candidate, and no test of them is
  proposed in this tranche.
- The statistical correctness of the DSR trial-count method, which remains the
  reviewer's open judgement call.
