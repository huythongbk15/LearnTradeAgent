# The evidence base does not survive its own audit

Two findings that were documented separately and belong together: the
commit filter that dated the damage, and the trace of the seven evidence
sets that are missing outright.

---

## 1. Which campaigns predate the fix

| campaign | pre-fix cells | post-fix | verdict |
|---|---|---|---|
| `wfo` | 2,030 | 192 | MIXED — needs per-cell filter |
| `multi_pair_1h` | 54 | 20 | MIXED |
| `wfo_full` | 0 | 519 | clean |
| `wfo_funding_carry_only` | 0 | 93 | clean |
| `wfo_parallel_canonical` | 0 | 21 | clean |
| `wfo_parallel_enhanced_ma` (SOL) | 252 | 0 | **UNUSABLE** |
| `wfo_parallel_enhanced_ma_eth` | 252 | 0 | **UNUSABLE** |
| `wfo_parallel_enhanced_ma_btc` | 252 | 0 | **UNUSABLE** |
| `wfo_parallel_rsi` | 168 | 0 | **UNUSABLE** |
| `wfo_parallel_bbands` | 84 | 0 | **UNUSABLE** |
| `wfo_parallel` | 189 | 0 | **UNUSABLE** |
| `wfo_real_20260906` | 197 | 0 | **UNUSABLE** |
| `s6_campaign` | 90 | 0 | **UNUSABLE** |
| `tournament` | 159 | 0 | **UNUSABLE** |
| `wfo_medium` | 168 | 0 | **UNUSABLE** |
| `wfo_minimal` | 35 | 0 | **UNUSABLE** |
| `acceptance_20260831` | 60 | 0 | **UNUSABLE** |

Totals: **4,527 pre-fix cells, ~1,268 post-fix.** Only three campaigns are
clean, and the largest of those (`wfo_full`, 519 cells) covers a different
set of strategies.

**The 852-cell `enhanced_ma` review is entirely pre-fix.** Every symbol in
`ENHANCED_MA_MEASUREMENTS.md §4` comes from `wfo_parallel_enhanced_ma*`, all
pre-fix. So the symbol comparison that reversed the "no edge" conclusion
rests on data produced by the pipeline that had the dropped-cell bug.

This does not make the comparison internally wrong — within one pre-fix
campaign the cells are mutually consistent, and the SOL/ETH/BTC spread was
a real difference in those artifacts. But it means none of those 852 cells
can be cited as evidence of current pipeline behaviour, and the ordering
between symbols may reflect which cells were dropped as much as which
strategy traded better.

## 2. Sign-off re-check

`docs/STRATEGY_SIGNOFF_P2PHASE4.md` ranks 16 strategies. Re-checking each
row's evidence against the filter:

| strategy | sign-off verdict | evidence status |
|---|---|---|
| `enhanced_ma` | FINAL_PASS 12/13, **APPROVED** | **ALL pre-fix** (852 cells) |
| `ma_adx` | NO_TRADE 6/13, REJECT | **ALL pre-fix** (778 cells) |
| `rsi` | NO_TRADE 3/13, REJECT | **ALL pre-fix** (1,621 cells) |
| `bbands` | NO_TRADE 2/13, REJECT | **ALL pre-fix** (752 cells) |
| `ma_vol_target` | FAIL (18 trades/fold) | MIXED (218 cells) |
| `trend_pullback` | REVIEW, holdout failed | clean (112 cells) |
| `volatility_breakout` | FAIL, Sharpe 0.00, 1 trade/fold | clean (203 cells) |
| `range_mean_reversion` | FAIL (−0.44%) | clean (183 cells) |
| `cross_sectional_momentum_lo` | FAIL (−67.41%) | clean (6 cells) |
| `stat_arbitrage_ls` | APPROVED, Sharpe 1.18 | **no cells on disk** |
| `stat_arbitrage_lo` | — | **no cells on disk** |
| `cross_sectional_momentum_ls` | — | **no cells on disk** |
| `ensemble_ma_adx` | FAIL, 6 trades/fold | **no cells on disk** |
| `ma_adx_regime` | FAIL, 8 trades/fold | **no cells on disk** |
| `ma_crossover` | FAIL (−2.55%) | **no cells on disk** |
| `regime_switching` | FAIL, 15 trades/fold | **no cells on disk** |

Two findings.

**The only APPROVED strategy, `enhanced_ma`, is supported entirely by
pre-fix cells.** The evidence for the sign-off's single recommendation
cannot be re-verified against the fixed pipeline, because no post-fix
campaign measured it. The three clean campaigns cover `wfo_full`,
`funding_carry` and canonical strategies — not `enhanced_ma`.

**Seven strategies have no cells on disk at all.** The sign-off quotes
figures for them — `ma_crossover` −2.55%, `ma_adx_regime` +4.12%,
`regime_switching` +1.74% — but the artifacts are absent. They may live in
an unarchived location or have been cleaned up; from this repository the
numbers are not checkable. Several of those quoted signatures are
consistent with cells that failed silently: `ma_adx_regime` "8
trades/fold", `regime_switching` "15 trades/fold",
`ensemble_ma_adx` "6 trades/fold", `volatility_breakout` "4/9 no-trade
folds".

## What the sign-off is now worth

The sign-off's **rejections** still carry weight. `rsi` and `bbands` are
rejected on 1,621 and 752 pre-fix cells, and rejecting a strategy on
evidence from a buggy pipeline is safe in one direction: the pipeline could
drop cells, but the ones that did run were real backtests. A strategy
measured that badly is not a strategy to keep.

The **approval** does not. `enhanced_ma` is the only strategy the document
recommends for live promotion, and its entire basis is pre-fix data that
cannot currently be reproduced. Everything found since — the cost-drag
diagnosis, the fold concentration, the 14x spread across commits — is
consistent with that evidence being unreliable rather than the strategy
being good.

## Revised position

| claim | standing |
|---|---|
| Execution core works | **Unaffected.** Chaos, fill/ledger, cancel, short-side, latency all pass against code paths that do not go through the campaign harness. |
| Registry has no promotable policy | **Holds.** The promotion store is empty and the gate refuses unattributable scores. |
| No strategy beats buy-and-hold | **Unproven, not disproven.** Every measurement supporting it is pre-fix or single-campaign. |
| `enhanced_ma` is the best candidate | **Withdrawn.** Sole approval rests on unreproducible pre-fix evidence. |
| 5 strategies are rejected outright | **Holds.** Rejection is the safe direction. |

The project is not further behind than this morning. It is *more precisely*
located: the execution core is sound, the evidence base is not, and the
gap between them is now named rather than assumed closed.

## Next

1. **Add the missing campaign invariant** — strategies requested must
   equal strategies that produced cells, or the run fails. This is cheap,
   and it is the check whose absence let bug 1 run silently across 4,527
   cells.
2. **Re-measure `enhanced_ma` on the fixed pipeline** before any further
   ranking work. It is the one strategy with an approval to lose, and the
   three clean campaigns give a template for a post-fix run.
3. **Locate the seven missing strategy evidence sets**, or mark their
   sign-off rows unverifiable. A sign-off that quotes figures with no
   artifacts is not a sign-off.

Item 1 is a small code change and unblocks everything else, because until
a campaign can be trusted to report what it ran, no campaign result means
anything.

---

## 2. Sign-off re-check

`docs/STRATEGY_SIGNOFF_P2PHASE4.md` ranks 16 strategies. Re-checking each
row's evidence against the filter:

| strategy | sign-off verdict | evidence status |
|---|---|---|
| `enhanced_ma` | FINAL_PASS 12/13, **APPROVED** | **ALL pre-fix** (852 cells) |
| `ma_adx` | NO_TRADE 6/13, REJECT | **ALL pre-fix** (778 cells) |
| `rsi` | NO_TRADE 3/13, REJECT | **ALL pre-fix** (1,621 cells) |
| `bbands` | NO_TRADE 2/13, REJECT | **ALL pre-fix** (752 cells) |
| `ma_vol_target` | FAIL (18 trades/fold) | MIXED (218 cells) |
| `trend_pullback` | REVIEW, holdout failed | clean (112 cells) |
| `volatility_breakout` | FAIL, Sharpe 0.00, 1 trade/fold | clean (203 cells) |
| `range_mean_reversion` | FAIL (−0.44%) | clean (183 cells) |
| `cross_sectional_momentum_lo` | FAIL (−67.41%) | clean (6 cells) |
| `stat_arbitrage_ls` | APPROVED, Sharpe 1.18 | **no cells on disk** |
| `stat_arbitrage_lo` | — | **no cells on disk** |
| `cross_sectional_momentum_ls` | — | **no cells on disk** |
| `ensemble_ma_adx` | FAIL, 6 trades/fold | **no cells on disk** |
| `ma_adx_regime` | FAIL, 8 trades/fold | **no cells on disk** |
| `ma_crossover` | FAIL (−2.55%) | **no cells on disk** |
| `regime_switching` | FAIL, 15 trades/fold | **no cells on disk** |

Two findings.

**The only APPROVED strategy, `enhanced_ma`, is supported entirely by
pre-fix cells.** The evidence for the sign-off's single recommendation
cannot be re-verified against the fixed pipeline, because no post-fix
campaign measured it. The three clean campaigns cover `wfo_full`,
`funding_carry` and canonical strategies — not `enhanced_ma`.

**Seven strategies have no cells on disk at all.** The sign-off quotes
figures for them — `ma_crossover` −2.55%, `ma_adx_regime` +4.12%,
`regime_switching` +1.74% — but the artifacts are absent. They may live in
an unarchived location or have been cleaned up; from this repository the
numbers are not checkable. Several of those quoted signatures are
consistent with cells that failed silently: `ma_adx_regime` "8
trades/fold", `regime_switching` "15 trades/fold",
`ensemble_ma_adx` "6 trades/fold", `volatility_breakout` "4/9 no-trade
folds".

## What the sign-off is now worth

The sign-off's **rejections** still carry weight. `rsi` and `bbands` are
rejected on 1,621 and 752 pre-fix cells, and rejecting a strategy on
evidence from a buggy pipeline is safe in one direction: the pipeline could
drop cells, but the ones that did run were real backtests. A strategy
measured that badly is not a strategy to keep.

The **approval** does not. `enhanced_ma` is the only strategy the document
recommends for live promotion, and its entire basis is pre-fix data that
cannot currently be reproduced. Everything found since — the cost-drag
diagnosis, the fold concentration, the 14x spread across commits — is
consistent with that evidence being unreliable rather than the strategy
being good.

## Revised position

| claim | standing |
|---|---|
| Execution core works | **Unaffected.** Chaos, fill/ledger, cancel, short-side, latency all pass against code paths that do not go through the campaign harness. |
| Registry has no promotable policy | **Holds.** The promotion store is empty and the gate refuses unattributable scores. |
| No strategy beats buy-and-hold | **Unproven, not disproven.** Every measurement supporting it is pre-fix or single-campaign. |
| `enhanced_ma` is the best candidate | **Withdrawn.** Sole approval rests on unreproducible pre-fix evidence. |
| 5 strategies are rejected outright | **Holds.** Rejection is the safe direction. |

The project is not further behind than this morning. It is *more precisely*
located: the execution core is sound, the evidence base is not, and the
gap between them is now named rather than assumed closed.

## Next

1. **Add the missing campaign invariant** — strategies requested must
   equal strategies that produced cells, or the run fails. This is cheap,
   and it is the check whose absence let bug 1 run silently across 4,527
   cells.
2. **Re-measure `enhanced_ma` on the fixed pipeline** before any further
   ranking work. It is the one strategy with an approval to lose, and the
   three clean campaigns give a template for a post-fix run.
3. **Locate the seven missing strategy evidence sets**, or mark their
   sign-off rows unverifiable. A sign-off that quotes figures with no
   artifacts is not a sign-off.

Item 1 is a small code change and unblocks everything else, because until
a campaign can be trusted to report what it ran, no campaign result means
anything.

---

# Tracing the seven missing evidence sets

Follows `EVIDENCE_BASE_AUDIT.md`, which found that seven
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
campaigns, which is what `EVIDENCE_BASE_AUDIT.md` already
recommends for `enhanced_ma` on the fixed pipeline. The coverage invariant
now in place would record the request list, so a repeat of the
`fast_wfo` family would be auditable in a way this one is not.

Whether the cell directories were deleted deliberately — a cleanup to save
disk, or an overwrite by a later run into the same path — is not
recoverable from here. `fast_wfo_rs_btc/summary.json` is dated 2026-09-16
and `wfo_parallel` 2026-09-05, so the deletion happened after both.
