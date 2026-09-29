# Real WFO campaign — enhanced_ma on BTC/USDT 1h


> **Data provenance caveat, added 2026-09-28 after the commit filter.**
> All campaigns behind this document were run **before** `a65ed29000`, which
> fixed three WFO pipeline bugs — one of them silently dropped strategies
> whose params failed schema validation. See
> `COMMIT_FILTER_AND_SIGNOFF_RECHECK.md` and
> `CAMPAIGN_REPRODUCIBILITY_ROOT_CAUSE.md`.
>
> Results here are internally consistent (one commit per campaign, no
> pooling), so comparisons *within* a campaign are meaningful. The absolute
> numbers cannot be cited as current-pipeline evidence, and any ordering
> between them may partly reflect which cells were dropped rather than
> which strategy traded better. Re-measure on the fixed pipeline before
> relying on this.

104 measured backtest cells across 8 of 10 outer folds. Run stopped
deliberately before completion: the question the campaign was commissioned
to answer was already answered, and two more folds could not reverse it.

Data: `data/wfo_real_campaign/` (gitignored, runtime artifacts).
Campaign length: ~10 hours for one strategy.

Config: train 9m / val 3m / test 3m / step 3m, expanding window,
param grid fast_period [10,20,30] × slow_period [60,80,120] = 9 combos,
`evidence_class=REAL_MARKET`. Fold count is data-driven
(`_get_fold_indices` iterates until data runs out), so the 9-10 folds were
always going to exist; the earlier 4-fold run was stopped early, not
limited by configuration.

## Aggregate over 104 cells

| metric | median | mean | min | max |
|---|---|---|---|---|
| return % | 0.000 | 0.281 | -4.825 | 7.451 |
| sharpe | 0.000 | 0.052 | -4.935 | 4.042 |
| trades | 6 | 6.4 | 0 | 21 |
| max DD % | 1.505 | 1.720 | 0.000 | 5.588 |

**49% of cells (51/104) never traded at all.** Median trades is 6.

## After costs

Round trip is 0.32%, so a cell needs `return% > 0.32 × trades`.

- **10 of 104 cells (10%)** clear their own cost
- median surplus among those: **+1.57pp**

## The four full param-grid windows

These are the informative ones — 9 cells each means params were actually
compared, rather than a single config being run in isolation.

| window | cells | clearing cost | median return % |
|---|---|---|---|
| `w642b5c3b3225a95a` | 9 | **5** | 4.832 |
| `w78ba79e93f0b0fd1` | 9 | 1 | 0.708 |
| `w0da54784faeebd68` | 9 | **0** | -1.988 |
| `w66d826ec3a3de24c` | 9 | **0** | 0.000 |

Winners across all windows: `w642b5c` 5, `w76808c` 2, then 1 each in three
others.

## Conclusion

**The edge is not a property of the strategy. It is one test window.**

Half of all cost-clearing cells sit in a single window, and the two
full-grid windows with median 0.000% and -1.988% produced zero winners
between them. The spread between windows (-1.988% to 4.832% median) is
larger than any difference attributable to parameter choice within a
window.

This is consistent with the independent finding in
`ENHANCED_MA_FINDINGS.md`, which measured the same strategy from the
outside across five windows and found it loses on the long leg in all of
them, with 1.2–1.5% exposure. Here the same fact appears from the inside
as fold concentration: the strategy is flat most of the time (49% of cells
never trade, median 6 trades), and what does trade lands in whichever
window happened to be favourable.

The `enhanced_ma` policy carries 9/9 WFO folds and selection_score 0.5 in
the quarantined stores. Those folds were real folds — but they measure
*low drawdown*, not returns. A strategy that stays flat 99% of the time
has a low drawdown by construction, and a fold-pass criterion that counts
that as success is the defect now demonstrated end to end.

## Consequences for the registry

One strategy out of seventeen, over ten months of expanding windows and
104 cells, yields 10 cost-clearing cells concentrated in one fold. There is
no evidence that the other sixteen differ, and no basis for assuming they
do. Scaling the campaign to the full registry would cost roughly 17×10
hours to learn the same thing.

## What would be needed before running this again

1. **A selection rule that requires edge to be spread across folds.** A
   single favourable window should not be able to qualify a policy. This is
   a gate change, not a campaign parameter.
2. **A minimum trade count that reflects the cost floor.** `min_trades_per_fold`
   is currently 10, while median trades is 6 and 49% of cells are 0. A
   policy whose folds average 6 trades has 1.9% of cost to overcome before
   earning anything.
3. **Drop the fold count as a promotion criterion**, or pair it with a
   return requirement. 9/9 folds is satisfiable by a strategy that does
   not trade.

None of these require new infrastructure. They are thresholds and a gate.
