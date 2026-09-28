# Real WFO campaign — enhanced_ma on BTC/USDT 1h (partial run)

41 backtest cells measured before the run was stopped deliberately
(~1h05m for one strategy). Data: `data/wfo_real_campaign/`.

Config: train 9m / val 3m / test 3m, 3+ outer folds, param grid
fast_period [10,20,30] × slow_period [60,80,120] = 9 combos,
`evidence_class=REAL_MARKET`.

## Aggregate

| metric | median | mean | min | max |
|---|---|---|---|---|
| return % | 0.000 | 0.630 | -4.059 | 6.191 |
| sharpe | 0.000 | 0.294 | -3.263 | 3.793 |
| trades | 10 | 8.5 | 0 | 20 |
| max DD % | 2.584 | 2.070 | 0.000 | 5.304 |

## After costs

Break-even is 0.32% per round trip, so a cell needs `ret > 0.32 × trades`
in percent units.

| trades | cells | median ret % | break-even % | clears |
|---|---|---|---|---|
| 0 | 14 | 0.000 | 0.00 | n/a — 34% of all cells never traded |
| 5–9 | 6 | 1.665 | 1.92 | no |
| 10–14 | 10 | 3.504 | 3.20 | **yes** |
| 15–20 | 11 | -1.196 | 4.80 | no |

7 of 41 cells clear their own cost, median surplus +1.09pp.

## The finding that matters

Those 7 are not spread across the campaign. Grouped by outer-fold window:

| window | cells | clearing cost | median ret % |
|---|---|---|---|
| w642b5c3b3225a95a | 9 | **5** | 4.832 |
| w78ba79e93f0b0fd1 | 9 | 1 | 0.708 |
| w0da54784faeebd68 | 9 | **0** | -1.988 |
| w66d826ec3a3de24c | 9 | **0** | 0.000 |
| w00ac6a3c9a8532aa | 1 | 1 | 5.625 |

**Five of the seven cost-clearing cells come from a single outer fold.**
The two full windows with nine cells each — the ones with enough
param-comparison structure to be informative — produced 5 and 0 winners.
The window at median 0.000% produced 0, and the one at -1.988% produced 0.

The apparent edge is a single favourable test window, not a property of the
strategy. This is the same failure mode ENHANCED_MA_FINDINGS.md identified
from the outside (1.2–1.5% exposure, flat ~98% of the time); here it shows
up from the inside as fold concentration.

## Two things worth carrying forward

1. **34% of cells never traded** (14/41 at 0 trades). Whatever this
   strategy's edge is, most of the grid never expresses it — consistent
   with the 9/9-fold figure being a low-drawdown artefact rather than a
   return figure.

2. **A unit error nearly repeated here.** The first pass of this analysis
   compared `ret` in percent against `trades × 0.0032` in fraction units
   and concluded 15/41 cleared cost. The correct comparison is
   `ret > trades × 0.32` in percent units, which gives 7/41. Same shape as
   the earlier silent-pass defects, and caught the same way: by checking
   the arithmetic against a known case rather than trusting the output.

## Decision

Do not scale to the other 16 strategies on this basis. One strategy over
one 21-month span has produced a fold-concentrated result, and a campaign
across the registry would cost ~17 hours to learn the same thing more
slowly.

What would justify scaling:

- more outer folds, so a single window cannot dominate, and
- a selection rule that requires the cost-clearing cells to be spread
  across folds rather than concentrated in one.

Both are configuration changes to the campaign, not new code.
