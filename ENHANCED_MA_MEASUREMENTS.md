# enhanced_ma — all measurements

Every measurement of the registry's highest-scoring strategy, consolidated
from six documents written across one session. Each subsection names its
origin and what superseded it.

> **Provenance.** The `wfo_parallel_enhanced_ma*` campaigns behind sections
> 2–4 ran **before** `a65ed29000`, which fixed three WFO pipeline bugs, one
> of which silently dropped strategies whose params failed schema
> validation. Within-campaign comparisons are internally consistent (one
> commit each, no pooling) so relative readings hold; the absolute numbers
> cannot be cited as current-pipeline evidence. See
> `CAMPAIGN_REPRODUCIBILITY_ROOT_CAUSE.md`.

---

## 1. Why a 1h strategy loses money

`ENHANCED_MA_MEASUREMENTS.md §1`

ma_crossover(10/20) is **+11.19% frictionless** and **-45.00% at real
costs**, against **-30.25% buy-and-hold**. The signal carries roughly 41pp
of alpha and 32bps of round-trip cost erases it. Break-even cost is
**4.8bps**; median hold is **1 hour**; the strategy trades ~440 times over
8,200 bars.

A timeframe sweep disproved the tidy conclusion — only 1h has positive
frictionless edge:

| TF | with 32bps | frictionless | Sharpe | trades |
|---|---|---|---|---|
| 1h | -46.44% | **+10.75%** | -5.045 | 227 |
| 4h | -33.48% | **-17.05%** | -7.567 | 69 |
| 1d | -16.46% | -13.74% | -6.206 | 10 |

Two independent causes, not one: 1h is cost-limited, 4h/1d have no edge at
all in this window.

---

## 2. The strategy has no alpha in any window

`ENHANCED_MA_MEASUREMENTS.md §2`

| window | long | short leg | buy & hold | edge |
|---|---|---|---|---|
| bear_2022 | -11.92% | -11.08% | -40.15% | +28.23pp |
| recovery_2023 | -7.46% | -6.65% | +151.89% | -159.35pp |
| bull_2023H2 | -4.98% | -5.23% | +84.05% | -89.03pp |
| bull_early_2024 | -7.64% | -12.58% | +105.54% | -113.19pp |
| sideways_2024H2 | -8.21% | -13.97% | -28.03% | +19.82pp |

Bull windows beating hold: **0/2**. The strategy loses the long leg in all
five and the short leg is worse everywhere — **-11.08% even in the -40%
bear**. Exposure is **1.2–1.5%**: flat ~98% of the time.

Its 9/9 WFO fold pass is real, and measures **low drawdown, not returns**.
49% of its cells never opened a position.

---

## 3. A 104-cell run, and the edge sits in one fold

`ENHANCED_MA_MEASUREMENTS.md §3`

| metric | median | mean | min | max |
|---|---|---|---|---|
| return % | 0.000 | 0.281 | -4.825 | 7.451 |
| trades | 6 | 6.4 | 0 | 21 |

**51/104 cells (49%) never traded.** After costs, 10/104 (10%) clear their
own round trip, median surplus +1.57pp — but by window:

| window | cells | clearing | median return |
|---|---|---|---|
| `w642b5c3b3225a95a` | 9 | **5** | 4.832% |
| `w78ba79e93f0b0fd1` | 9 | 1 | 0.708% |
| `w0da54784faeebd68` | 9 | **0** | -1.988% |
| `w66d826ec3a3de24c` | 9 | **0** | 0.000% |

Half of all cost-clearing cells come from one window. The two full-grid
windows at 0.000% and -1.988% produced zero winners between them.

---

## 4. The symbol matters — and the sign-off already said so

`ENHANCED_MA_MEASUREMENTS.md §4` · `ENHANCED_MA_MEASUREMENTS.md §4`

852 cells across nine symbols. Median return and Sharpe by symbol:

| symbol | cells | median ret % | median trades | median Sharpe |
|---|---|---|---|---|
| SOLUSDT | 256 | **1.548** | 10 | **1.056** |
| ETHUSDT | 256 | 0.595 | 10 | 0.430 |
| **BTCUSDT** | **311** | **-1.026** | 9 | **-0.906** |

The campaign in section 3 used **BTC, one of the worst symbols measured**,
and generalised from it. `docs/STRATEGY_SIGNOFF_P2PHASE4.md` approved the
strategy on **SOL** evidence and never claimed BTC.

Applying the spread gates per symbol, all three fail on the same gate:

| symbol | clearing (1x) | p | verdict |
|---|---|---|---|
| SOL | 3/7 | 0.7734 | FAIL |
| ETH | 2/7 | 0.9375 | FAIL |
| BTC | 1/7 | 0.9922 | FAIL |

`zero_trade_fold_pct`, `single_window_le_60pct` and
`median_return_clears_cost_floor` pass on all three. Where the strategy
does trade it clears its costs with margin — the constraint is frequency,
not magnitude.

**This document's own process error:** the sign-off ranked all 16
strategies and named the evidence source for each. The 10-hour campaign was
launched without reading it. Its "next steps" were later marked superseded
— the 852 cells are all pre-fix, the per-symbol gate run was done, and the
campaign recommendation was overtaken by the determinism check.

---

## 5. Higher timeframes make it worse, not better

`ENHANCED_MA_MEASUREMENTS.md §5`

Entries per 3-month window, measured before running any campaign:

| symbol | 1h | 4h | 1d |
|---|---|---|---|
| BTC | 11.57 | 3.21 | 0.58 |
| ETH | 11.64 | 3.29 | 0.58 |
| SOL | 13.50 | 3.29 | 0.57 |

Against a floor of 20: **1h = 58%, 4h = 16%, 1d = 3%**. MA 20/80 is
expressed in bars, so a higher timeframe stretches the slow period from 80
hours to 320 and a 3-month window holds 567 bars instead of 2,270. The
proposed 4h campaign would have cost ~10 hours to produce a strategy at
16% of the floor, failing the same gate.

---

## What the six documents established together

The strategy is not a fraud and not a winner. It is a **low-drawdown
filter that does not capture returns**, whose apparent edge is a function
of which symbol and which window you happened to look at.

The 9/9 folds, the selection_score 0.5 and the promotion it carried were
all consistent with a strategy that rarely holds a position. That is what
the 2,690 fabricated policies and the 4 attributable ones in
`POLICY_RETURN_AUDIT.md` are the same story at a larger scale: the
promotion machinery was measuring the wrong thing throughout.
