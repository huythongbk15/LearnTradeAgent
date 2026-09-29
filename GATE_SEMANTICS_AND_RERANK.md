# B and A: gate semantics settled, registry re-rank blocked

## B — what the gates mean

The open question was how many folds a spread claim needs, since the
binomial threshold inherits whatever sample a campaign happened to produce.

`scripts/spread_gate_power_analysis.py`:

| true clearing rate | folds needed | k/n |
|---|---|---|
| 55% | never | — |
| 60% | 37 | 23/37 |
| 65% | 14 | 10/14 |
| 70% | 9 | 7/9 |
| 75% | 7 | 6/7 |
| 80%+ | 4 | 4/4 |

Power of the gate at p≤0.10:

| n | 60% edge | 70% edge | 75% edge | 80% edge |
|---|---|---|---|---|
| 7 | 16% | 33% | 44% | 58% |
| 10 | 17% | 38% | 53% | 68% |
| 14 | 28% | 58% | 74% | 87% |
| 20 | 25% | 61% | 79% | 91% |
| 30 | 29% | 73% | 89% | 97% |
| 50 | 45% | 92% | 99% | 100% |

**Decision: p ≤ 0.20, with n as the stated binding constraint.**

`scripts/spread_gate_threshold_options.py` shows p is not the lever at
n=14 — 0.10, 0.15 and 0.20 all demand the same 10/14. p=0.20 earns its
place at n=10, where it asks 7/10 instead of 8/10 and lifts power for a
genuine 70% edge from 38% to 65%. p≤0.25 is the only setting that moves
n=14, but at n=7 it lets a 55% strategy pass 32% of the time, which
sabotages the chance comparison the test exists to make.

The honest caveat: **power remains the binding constraint, not p.** At
n=14 no available threshold reaches the 73–92% that n=30–50 give for a
70% edge. A 14-fold campaign is better than 7 and still underpowered. If
the spread claim is to carry weight, the answer is more history or longer
folds, not a threshold change.

## A — the re-rank cannot be done validly from the on-disk data

`scripts/rerank_registry.py` applies the four gates to all 9,742 base-scenario
cells across 9 strategies. Aggregated, the answer looks unambiguous:

| strategy | cells | windows | clearing | p | verdict |
|---|---|---|---|---|---|
| ma_adx | 795 | 32 | 16 (50.0%) | 0.570 | fail(1) |
| volatility_breakout | 189 | 18 | 7 (38.9%) | 0.881 | fail(1) |
| enhanced_ma | 321 | 20 | 7 (35.0%) | 0.942 | fail(1) |
| ma_vol_target | 98 | 20 | 5 (25.0%) | 0.994 | fail(1) |
| rsi | 795 | 62 | 12 (19.4%) | 1.000 | fail(1) |
| bbands | 314 | 30 | 9 (30.0%) | 0.992 | fail(1) |
| trend_pullback | 112 | 5 | 1 (20.0%) | 0.969 | not assessable |
| range_mean_reversion | 169 | 28 | 1 (3.6%) | 1.000 | fail(1) |
| funding_carry | 136 | 14 | 0 (0.0%) | 1.000 | fail(3) |

**0 of 9 clear all four gates.** Every failure is on the spread gate except
`funding_carry`, which fails three.

## But that table should not be trusted

Breaking the same cells down by which campaign produced them:

| strategy | campaign | cells | clearing |
|---|---|---|---|
| enhanced_ma | wfo_parallel_enhanced_ma (SOL) | 84 | 42.9% |
| enhanced_ma | wfo_parallel_enhanced_ma_eth | 84 | 28.6% |
| enhanced_ma | wfo_parallel_enhanced_ma_btc | 84 | 14.3% |
| enhanced_ma | wfo | 42 | 7.1% |
| rsi | s6_campaign | 44 | 22.7% |
| rsi | wfo | 516 | 1.2% |
| rsi | wfo_parallel_rsi | 56 | 0.0% |
| rsi | wfo_real_20260906 | 71 | 0.0% |
| bbands | wfo | 266 | 4.1% |
| bbands | wfo_parallel_bbands | 28 | 0.0% |

The same strategy on the same symbol, measured by different campaigns,
differs by **6.4x for enhanced_ma, 227x for rsi, 41x for bbands**.

`ma_adx`'s headline 50.0% — the best in the table — is an artifact of
pooling: its eight campaigns individually range from 0% to 42.9%, and the
aggregate lands on 50% only because a high-clearing campaign outweighs the
others in cell count.

**So the ranking above is not a ranking.** Pooling campaigns with different
fold structures, data windows, parameter grids and cost treatments produces
a number that reflects which campaigns happened to be included, not which
strategy is better. The "0 of 9 pass" conclusion happens to be robust —
every component is far from the threshold — but the ordering between them
carries no information, and `ma_adx` should not be read as the closest to
qualifying.

## What this actually establishes

The campaigns are **not reproducible against each other**. That is a
property of the measurement apparatus, and it sits upstream of every
strategy question asked so far: the 104-cell BTC campaign, the 852-cell
enhanced_ma review, and the sign-off's 252-cell SOL run are three
measurements that would not agree with each other if rerun under a
different campaign configuration.

This is the same failure shape as the rest of the investigation, one level
up. The earlier cases were a check that could pass without the thing being
true; this is a measurement that does not reproduce. Neither is fixable by
tuning.

## What follows

Re-ranking the registry is not available until the campaigns are made
comparable, and that is a different piece of work from any measurement
taken so far. The plausible causes are enumerable and checkable — fold
structure, data window, parameter grid, cost treatment, engine version —
and the spread above suggests they differ materially between campaigns that
were all described as WFO on the same symbol.

The first thing worth doing is not a campaign. It is finding out why six
measurements of one strategy disagree by two orders of magnitude, because
until that is answered, no campaign run — including the 14-fold SOL one —
produces a number that can be trusted.
