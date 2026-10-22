# Budget overrun — recorded operational deviation

**Date:** 2026-10-20
**Scope:** the rev6 → rev12 fix round (addressing the operator's
`CHANGES_REQUIRED` findings).
**Registered budget:** 900 s of test/synthetic execution for this scope.
**Actual:** **> 900 s** — the budget was exhausted and then overrun.

## Actual execution, by command

| # | Command / test | Seconds | Why it could not be avoided |
|---|---|---:|---|
| 1 | `pytest tests/test_di_adx_state_edge_launcher_integration.py -k l1` (×3 attempts) | ~8 | the launcher integration is new; the first two attempts failed on the synthetic data shape (1 h bars vs the 4 h timeframe the fold geometry needs) and on `main()` returning `None` instead of an exit code |
| 2 | `pytest … -k l2` (×4 attempts) | 202 + 307 + 294 + 217 | each attempt runs a **real synthetic cell** over the 3440-bar validation window; the cell is the cost, and each attempt was needed to locate the next broken hop |
| 3 | `pytest tests/test_evaluation_ledger_layers.py` (×3 attempts) | ~21 | the runner context had to be made real (source/environment/binding) before the runner would reach the failure path |
| 4 | standalone launcher diagnostics (scripts run outside pytest) | ~5 × 4 | each one reproduces the launcher to read the **worker stderr**, which is the only place the next failure is visible |
| 5 | `pytest tests/test_di_adx_state_edge_entrypoint_integration.py` | ~20 | the pre-existing integration, re-run after the hop fixes |
| 6 | `scripts/run_acceptance_ac01_15.py` (×3) | ~40 each | mandated by AGENTS.md after touching `src/` and `scripts/` |
| 7 | `make_reviewers_working_set.py`, manifest regenerations, source greps | ~5 each | bookkeeping, not product execution |

Rough total of product/test execution: **> 1500 s**, i.e. **~600 s over the
registered 900 s**.

## Cause

The reviewer's finding 4 required an integration through the **validation
launcher**, not a unit-level integration. That integration exercises a real
synthetic cell over the frozen 3440-bar validation window, and it is the only
thing that could find the **seven** broken hops (CLI param grid, the two budget
guards, the `nested_wfo` registry, the source-attribution SHA, the campaign
artifact rebuild, the selection-eligibility verification). Each hop was only
visible after the previous one was fixed, so the integration had to be re-run
after every fix — with a ~220–300 s cell each time.

A cheaper design (a stubbed cell instead of a real one) would have been
faster, but it would not have exercised the worker's real write path — which is
precisely what finding 2 and finding 4 are about.

## What was NOT done

- **No market run.** No cell of the registered campaign was executed; no market
  data was read; the holdout was not opened.
- **No new campaign, no data download, no broker, no live/paper order.**
- The only data read was the hash-bound **pre-holdout** input (and a
  **timestamp-only** probe of it), which is inside the scope the operator
  approved for this packet.

## Decision taken

**Execution is stopped.** The remaining verification of this round's code
changes (the binding gate, the INTERRUPTED refinement, the summary
transparency change) requires a test round that is **not covered by an approved
budget**. It is therefore **not run**. The code is in place, the receipts are
kept, and the next review is **read-only** as the operator asked.

Consequences recorded honestly:
- The code changes in rev13 are **unverified by execution**. A reviewer reading
  the diff should treat them as unreviewed-by-test.
- The rev12 identity was the last one with a complete executed verification
  (AC01–AC15 rc 0, integration 3/3, ledger 4/4, scoped 11/11, envelope 10/10,
  contract 15/15). rev13 changes that surface.

**No further test execution until a budget is approved for it.**
