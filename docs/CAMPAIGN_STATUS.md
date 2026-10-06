# CAMPAIGN STATUS — authoritative tracker

Single source of truth for the measured-accounting and campaign-publication
work. Supersedes the per-session `*_TODO.md` files, which are gitignored and
were fragmenting the state across ~60 files.

## How to read the status column

Three levels, deliberately not collapsed:

| Level | Means | Evidence required |
|---|---|---|
| **IMPLEMENTED** | Code exists and is committed | commit sha |
| **TESTED** | A named suite was run and passed, at a stated count | suite name + count + date |
| **ACCEPTED** | Independent evidence exists that the thing does what it claims | acceptance run, published bundle, or review sign-off |

A `- [x]` in an old TODO meant all three at once, which is how "implemented"
was previously reported as "verified". Nothing here is ACCEPTED unless it
says so.

## Status

| # | Item | Status | Commit | Test evidence | Acceptance evidence |
|---|---|---|---|---|---|
| 1 | Measured cost per fill (spread / slippage / impact / commission) | **TESTED** | `bb8c795` | `tests/test_fill_cost_components.py` 10 pass; 37 pass across simulator group | none |
| 2 | Partial fill + carry + open inventory accounting | **TESTED** | `70ecb51` | `tests/test_open_inventory_accounting.py` 13 pass; 50 pass across accounting group | none |
| 3 | Carried inventory priced in campaign evidence | **TESTED** | `f1173e6` | `tests/test_campaign_open_inventory.py` 7 pass; 523 pass across campaign/accounting/tournament/holdout group | none |
| 4 | Accounting regression snapshot + seed fix | **TESTED** | `31cee3e` | `tests/test_accounting_regression_snapshot.py` 6 pass; 63 pass across simulator group | none |
| 5 | Frozen campaign plan + native bundle producer | **TESTED** | `d357347` | `tests/test_campaign_plan_producer.py` + `tests/test_run_real_wfo_campaign_script.py` 20 pass; 27 pass | none |
| 6 | Real campaign executed through producer → validator → publication | **TESTED** | `3511b40` | BTC/USDT 4h `ma_crossover` pilot ran end-to-end (2 workers, 2 folds); gate refused carry as a valid pilot output; real publish left to full run | gate refusal accepted as valid; see carry fix below |
| 7 | AC01–AC15 acceptance suite | **ACCEPTED** | `d357347` | see below | `data/acceptance_runs/ac01_15_20261005_082339_utc/` |
| 8 | Independent review | not started | — | — | — |
| 9 | Testnet / soak | **BLOCKED on operator** | — | — | requires `LIVE_ACCOUNT_ID` |

## AC01–AC15 acceptance run

Run: `data/acceptance_runs/ac01_15_20261005_082339_utc/`
Revision under test: `d35734702a309d088995aa6e6b9258ecb1bcc6c0`

**15/15 PASS (rc=0)** — AC01 through AC15, each with its own log and evidence
JSON in `current/`, plus `previous/` retained for comparison and a
`SUMMARY.txt` naming the revision. AC01 is re-run last as a regression after
AC02–AC15, and passed there too.

This is the acceptance evidence for items 1–5: the run happened at the commit
that carries the fill-cost split, the open-inventory contract, the campaign
evidence changes, the regression snapshot and the new producer.

## Full-suite baseline

Last full run: **2083 pass, 3 skipped, 7 failed**.

All 7 failures also fail on a clean tree (`HEAD`, worktree stashed) and are
not caused by this work:

- `tests/test_r04_scope_lock_campaign.py` — 4 failures, strategy catalog scope
  (`allows_strategy('rsi')` is False). The other three in that file cascade
  from the same scope fixture.
- `tests/test_short_side_backtest.py` — 3 failures, short-side entry/exit.

Verified by stashing the working tree and re-running each file: identical
failure counts.

## Known defects not addressed

- **mypy, `execution/backtest_sim/models.py`** — 5 errors at `HEAD`, all in
  `_process_limit_order`, where `order.price` is `float | None` and is
  compared and multiplied without a guard. Pre-existing. Fixing it requires
  deciding what a limit order with no price means, which is a modelling
  decision, not a typing fix.
- **mypy, `backtest/campaign_evidence.py`** — 4 errors at `HEAD`
  (`finite(object)`, `dict | None` access). Pre-existing, count unchanged.
- **`LIVE_ACCOUNT_ID`** unset. `docs/operations/LIVE_READINESS_AGENT_PLAN.md`
  requires the operator-verified stable venue account UID, explicitly not the
  API key or a run id. The live runner refuses even a dry run without it.

## Definition of done for this line of work

1. A campaign runs end to end on local data through the new chain.
2. A schema-v2 bundle is published atomically and re-validates from disk.
3. AC01–AC15 pass with the run recorded.
4. An independent review reads the bundle against its frozen plan.
5. Only then: operator-authorised testnet/soak.