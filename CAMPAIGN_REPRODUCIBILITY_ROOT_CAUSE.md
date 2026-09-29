# Why the campaigns disagree — root cause

`GATE_SEMANTICS_AND_RERANK.md` found the same strategy measured by
different campaigns differing by up to 227x, and concluded the campaigns
are not reproducible. This traces where that comes from.

## Controlled comparison

Grouping cells by (strategy, symbol, `data_manifest_id`) holds the data
constant — the manifest is a content hash, so identical manifests mean
byte-identical OHLCV. Any remaining variation is code or configuration.

| strategy | symbol | commit | date | commit subject | clearing |
|---|---|---|---|---|---|
| ma_adx | SOL/USDT | `7de50ac75d` | 08-31 | STR-0208 per-cell timeout/retry | 4/144 = 2.8% |
| ma_adx | SOL/USDT | `ee3048caa2` | 09-04 | mypy cleanup batch 6 | 6/66 = 9.1% |
| ma_adx | SOL/USDT | `5dcbab533c` | 09-05 | fix: ruff auto-fix | 27/67 = **40.3%** |
| rsi | BTC/USDT | `5d0e477b28` | — | — | 1/78 = 1.3% |
| rsi | BTC/USDT | `f8dda19c83` | — | — | 3/23 = 13.0% |
| funding_carry | BTC/USDT | `b1a12b2cec` | — | — | 6/79 = 7.6% |
| funding_carry | BTC/USDT | `b0033ef8ca` | — | — | 9/48 = 18.8% |

`ma_adx` on SOL, same data, **14x** spread. A mypy cleanup and a ruff
auto-fix account for a 3.2x and a 4.4x step between them.

## The cause

`a65ed29000`, 2026-09-10: *"fix: Workstream B — 3 critical bugs in WFO
pipeline"*.

1. `full_system_backtest.py` injected `atr_sl_mult` / `atr_tp_mult` into
   `ma_adx` params, which are not in that strategy's schema. This raised
   `ParamValidationError`, so those cells **never ran** — the campaign
   reported a clean run while silently dropping strategies.
2. `run_wfo_parallel.py` read `result.verdict`, which `WFOResult` does not
   have, so the verdict came from the wrong object.
3. `nested_wfo.py` had no single-cell fallback, so cells failed without
   a recorded reason.

None of these are cosmetic. Bug 1 means campaigns before 2026-09-10
measured **fewer strategies than they claimed**. A run that reports
`completed: 189, failed: 0` while a strategy was never invoked is
reporting a count, not a result.

## Why lint fixes moved the numbers

The commits between 08-31 and 09-05 were mypy and ruff cleanups. They
should not affect trading results. The plausible explanation is that they
touched the same files the campaign code lived in, and a cleanup that
reordered an import or renamed a symbol could have changed which branch
a cell took, or made a previously-raising path return a value. Whatever
the mechanism, the consequence is the same: **the pipeline's behaviour
was not stable across commits, and nothing detected that.**

This is the same failure shape as the rest of the investigation, at the
level of the harness. A campaign is supposed to be a reproducible
measurement; these were not reproducible, and the run summary looked
healthy throughout.

## What this invalidates

Every cross-campaign comparison made in this session, and the sign-off
that depends on one:

| artifact | commits spanned | status |
|---|---|---|
| `WFO_CAMPAIGN_RESULT.md` (104 cells, BTC) | single commit | internally consistent |
| `RESEARCH_EVIDENCE_REVIEW.md` (852 cells) | 09-05 to 09-21, per symbol | mixed-commits, symbol-separated |
| `SPREAD_GATES_BY_SYMBOL.md` | per symbol, single commit each | internally consistent per symbol |
| `GATE_SEMANTICS_AND_RERANK.md` re-rank | **9 commits pooled** | **invalid** |
| `docs/STRATEGY_SIGNOFF_P2PHASE4.md` | spans the bug window | **needs re-check** |

The per-symbol and per-commit analyses hold. The pooled re-rank does not,
which is why the ordering in it was called out as carrying no information
— this is why.

## The sign-off needs re-checking specifically

It ranks 16 strategies and is the document that says `enhanced_ma` is the
only approved candidate. If its cells span the 09-10 bug window, some of
its 252 cells may not have run at all, and the strategies that were
silently dropped are the ones that would have changed the ranking. The
`volatility_breakout` row already reads "Sharpe=0.00, 1 trade/fold (4/9
no-trade folds)" and `ensemble_ma_adx` "6 trades/fold" — both consistent
with cells that did not execute rather than strategies that underperform.

## What follows

1. **Establish which campaigns predate the fix.** Anything before
   `a65ed29000` needs re-running or an explicit exclusion. The manifest
   and commit are recorded in every report, so this is a filter, not an
   investigation.
2. **Re-check the sign-off against that filter.** It is the document the
   whole strategy pool rests on.
3. **Add a campaign-level invariant**: the number of strategies requested
   must equal the number that produced cells, or the run fails. Bug 1 was
   silent precisely because no such check existed.
4. **Only then** revisit the re-rank, on single-commit data.

The 14-fold SOL campaign is not the next step. It would run on a pipeline
whose cross-version stability was never established, and would produce a
number indistinguishable from the ones already known not to reproduce.
