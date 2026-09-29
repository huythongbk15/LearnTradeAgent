# Tracing the seven missing evidence sets

Follows `COMMIT_FILTER_AND_SIGNOFF_RECHECK.md`, which found that seven
strategies in `docs/STRATEGY_SIGNOFF_P2PHASE4.md` have no cells on disk,
so the figures the sign-off quotes for them cannot be checked from this
repository. This traces what happened to them.

## Search method

Four sources, in order of what would preserve the data:

| source | result |
|---|---|
| `data/backtests/**/{strategy}__*` | absent for all seven |
| git history (`--diff-filter=A`) | **never committed** — 0 adds for any of them |
| campaign `summary.json` files | **5 of 7 documented**, cells deleted |
| `cross_asset/`, `tournament_state/` | present, but not WFO evidence |

The git result matters: the data was never in the repository, so there is
no revision to recover it from.

## What was found: summaries survived, cells did not

The `fast_wfo*` family kept its `summary.json` and lost every cell
directory:

| campaign | runs | strategy | cells in summary | cells on disk |
|---|---|---|---|---|
| `fast_wfo` | 4 | `trend_pullback` | **5,184** | 0 |
| `fast_wfo_rs_btc` | 1 | `regime_switching` | 144 | 0 |
| `fast_wfo_rs_eth` | 1 | `regime_switching` | 144 | 0 |
| `fast_wfo_rs_bnb` | 1 | `regime_switching` | 144 | 0 |
| `fast_wfo_rs_xrp` | 1 | `regime_switching` | 144 | 0 |
| `fast_wfo_regime`, `fast_wfo_{BNB,BTC,ETH,XRP}`, `fast_wfo_full`, `fast_wfo_test` | — | — | — | 0 and no summary |

**5,760 cells are documented in summaries and absent from disk.**

`fast_wfo_rs_btc/summary.json` retains the full aggregate for
`regime_switching`:

```json
{"strategy": "regime_switching", "symbol": "BTC/USDT", "timeframe": "1h",
 "param_combos": 16, "n_folds": 9, "total_cells": 144,
 "verdict": "FAIL", "passes_hard_gates": false,
 "aggregate_metrics": {"median_test_sharpe": 0.176,
                       "median_test_return_pct": 1.74,
                       "median_oos_trades": 15.0,
                       "median_max_dd_pct": -17.53,
                       "n_passing_folds": 9, "n_no_trade_folds": 0}}
```

That matches the sign-off's `regime_switching` row exactly (Sharpe 0.176,
+1.74%, 15 trades/fold, FAIL). **So the sign-off figures came from a real
run whose artifacts were later deleted**, not from a run that never
executed. That distinction matters: the numbers are traceable, and their
provenance is a summary file rather than per-cell evidence.

## The two categories are not the same

| category | strategies | status |
|---|---|---|
| summary survives, cells deleted | `regime_switching`, `trend_pullback` | figures traceable to a real run |
| cross-asset result, never WFO | `stat_arbitrage_ls`, `stat_arbitrage_lo` | `cross_asset/*.json` present, 400 bytes each — result-level only |
| nothing found | `ma_crossover`, `ma_adx_regime`, `ensemble_ma_adx` | no WFO artifact, no summary, not in git |

`stat_arbitrage_ls` is the sign-off's **second APPROVED** strategy, and
its evidence is a single 400-byte JSON in `cross_asset/` with no
per-symbol or per-fold breakdown. The sign-off quotes "4-symbol universe
(BTC/ETH/BNB/XRP), 31783 hourly bars, Sharpe 1.18, return +24.85%, 31
trades" — none of which can be recomputed from what is on disk.

## Two of the three unresolved have a signature consistent with the bug

`ma_adx_regime` "8 trades/fold" and `ensemble_ma_adx` "6 trades/fold" are
quoted in the sign-off with no artifact anywhere. Under
`a65ed29000`'s first bug, a strategy whose params failed schema validation
raised `ParamValidationError` and produced no cell — which is exactly what
an absent artifact looks like. `volatility_breakout`'s clean-artifact row
reads "4/9 no-trade folds" with the same shape.

This remains circumstantial: no summary was written for those three, so
there is nothing to confirm it. But it is the same signature the confirmed
bug produced, in the same window.

## What this changes

**`trend_pullback` — 5,184 cells, the largest evidence set in the
repository — has no cells.** It is a real loss, not a bookkeeping gap. The
sign-off ranks it third with `HOLDOUT_FAILED` and a REVIEW flag, so its
removal from disk does not change the recommendation, but it removes the
largest body of evidence from being re-examinable.

**Two of the three APPROVED strategies rest on result-level summaries
rather than per-cell evidence.** `enhanced_ma` on pre-fix cells,
`stat_arbitrage_ls` on a 400-byte JSON. Neither can be re-derived, and the
coverage invariant added in `54906f6` cannot be applied to either, because
it needs the requested set and neither recorded it.

**Five of the seven are traceable to real runs.** The sign-off is not
fabricated — it cites runs that happened. What is missing is the ability
to check any of them, which is the same failure the commit filter found
from the other direction: evidence that cannot be reproduced is not
evidence the gate can act on.

## Practical consequence

The sign-off's **ranking** is defensible on traceability: every row maps
to either a summary or a result file, and the five that were reviewed as
most likely to have dropped cells are the ones with no artifacts at all,
which is consistent with the bug rather than contradicting it.

The sign-off's **recommendation** is not. Both approved strategies are
supported by artifacts that cannot be re-run, re-derived, or checked for
coverage. On the evidence available in this repository, nothing currently
qualifies for promotion — which is the same conclusion the empty promotion
store already encodes.

## What would recover the data

Not git: it was never committed. The only route is re-running the
campaigns, which is what `COMMIT_FILTER_AND_SIGNOFF_RECHECK.md` already
recommends for `enhanced_ma` on the fixed pipeline. The coverage invariant
now in place would record the request list, so a repeat of the
`fast_wfo` family would be auditable in a way this one is not.

Whether the cell directories were deleted deliberately — a cleanup to save
disk, or an overwrite by a later run into the same path — is not
recoverable from here. `fast_wfo_rs_btc/summary.json` is dated 2026-09-16
and `wfo_parallel` 2026-09-05, so the deletion happened after both.
