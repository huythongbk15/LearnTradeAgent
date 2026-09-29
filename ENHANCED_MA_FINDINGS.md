# enhanced_ma across BTC/USDT 1h windows — verdict


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

Promoted default params (MA 20/80 + ADX filter, ATR SL/TP), canonical
Forecast contract, long-only oracle, 32bps round trip.

| window | period | long | short leg | buy & hold | edge vs hold | trades | exposure |
|---|---|---|---|---|---|---|---|
| bear_2022 | 2022-04..2023-03 | -11.92% | -11.08% | -40.15% | **+28.23pp** | 51 | 1.5% |
| recovery_2023 | 2023-08..2024-07 | -7.46% | -6.65% | **+151.89%** | -159.35pp | 41 | 1.3% |
| bull_2023H2 | 2023-11..2024-10 | -4.98% | -5.23% | +84.05% | -89.03pp | 38 | 1.3% |
| bull_early_2024 | 2024-09..2025-08 | -7.64% | -12.58% | +105.54% | -113.19pp | 36 | 1.2% |
| sideways_2024H2 | 2025-06..2026-05 | -8.21% | -13.97% | -28.03% | +19.82pp | 38 | 1.3% |

**Bull windows beating hold: 0/2.**

## What this means

1. **No alpha in any condition.** The strategy loses in all five windows on
   the long leg. It "beats" hold only where hold loses, and only by losing
   less.

2. **The short leg is worse.** In a -40% bear it returns -11.08%, i.e. it
   is not positioned to profit from the decline either. A genuine bearish
   edge would show a large positive short return in bear_2022.

3. **Exposure is 1.2-1.5%.** The strategy is flat ~98% of the time. Its
   9/9 WFO folds measured a *low-drawdown* profile, not an alpha profile.
   The folds are real; they just certify "rarely holds", not "holds well".

4. **The -7% to -12% across all windows** comes from ~40 trades x 32bps
   round-trip cost, i.e. roughly -0.2% per trade of pure drag with no
   offsetting edge — the same cost-drag structure identified in
   AC14_LOSS_DIAGNOSIS.md, now confirmed on a promoted strategy rather
   than a hand-picked one.

## Implication for the registry

`enhanced_ma` at 9/9 folds is a **defensive filter, not a source of
returns**. Promoting it as an incumbent on the strength of folds alone
confuses "consistently small drawdown" with "consistently profitable".

The router abstaining in both bull windows (evidence_ac14_windows.py) is
therefore doing the right thing for the wrong reason: it is not protecting
edge, it is declining to trade a strategy that has none.

## What to do

- The selection score must separate *low drawdown* from *positive expectancy*.
  `median_max_dd_pct` is being rewarded where `median_oos_return_pct` should be.
- Before any further promotion, check `median_oos_return_pct` on the stored
  policies: enhanced_ma scored 0.02 (2%) while passing 9/9 folds. A 2%
  expected return cannot survive 32bps x 40 trades.
- A cost-realistic promotion gate is required: expected edge per trade must
  exceed round-trip cost, or the policy should be marked no-trade.
