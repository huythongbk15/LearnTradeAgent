# Commit filter applied, and the sign-off re-checked

Follows `CAMPAIGN_REPRODUCIBILITY_ROOT_CAUSE.md`, which traced the cross-
campaign disagreement to three WFO pipeline bugs fixed at `a65ed29000`
(2026-09-10). Every report on disk records its `commit_sha`, so the filter
is a lookup rather than an investigation.

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
`RESEARCH_EVIDENCE_REVIEW.md` comes from `wfo_parallel_enhanced_ma*`, all
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
