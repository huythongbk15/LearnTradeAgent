# Timeframe probe: the higher-timeframe hypothesis is backwards

Proposed: the binding constraint is `median_trades_per_trading_fold_ge_20`,
so moving to 4h or 1d should relieve it, since higher timeframes mean
fewer, larger holds that amortise the 32bps round trip.

Measured entry frequency before running any campaign
(`scripts/probe_timeframe_trade_frequency.py`), enhanced_ma at its
promoted default params (MA 20/80), entries per 3-month window:

| symbol | 1h | 4h | 1d |
|---|---|---|---|
| BTC_USDT | **11.57** | 3.21 | 0.58 |
| ETH_USDT | **11.64** | 3.29 | 0.58 |
| SOL_USDT | **13.50** | 3.29 | 0.57 |

Gate floor: 20 per trading window.

| TF | share of floor (BTC) |
|---|---|
| 1h | 58% |
| 4h | 16% |
| 1d | 3% |

## Why

MA 20/80 is expressed in **bars**, not in time. Moving to 4h stretches the
slow period from 80 hours (3.3 days) to 320 hours (13.3 days), so the
crossover that fires roughly every 100 bars fires every 400 hours instead
of every 100. A 3-month window holds 2,270 bars at 1h and 567 at 4h, so
the same window sees about a quarter of the crossovers.

The cost side does improve — 3.3 trades per window pay 1.06% in round
trips rather than 3.7% — but the gate that is failing is not the cost
gate. It is the trade-count gate, and higher timeframes move it further
from the line, not closer.

## What this rules out

The proposed next step — a 4h campaign — would have cost roughly ten hours
to confirm something measurable in thirty seconds. It would have produced
a strategy at 16% of the trade-count floor and failed the same gate the
1h campaign failed, with a worse number.

## What it leaves

Three readings of `median_trades_per_trading_fold_ge_20` are possible, and
the data does not yet distinguish them:

1. **It is a statistical floor.** Below ~20 trades a fold's return is too
   noisy to trust, so a low-frequency strategy is correctly excluded and
   1h is simply the wrong timeframe for this registry. Under this reading
   the gate is right and `enhanced_ma` does not belong in the pool.

2. **It is a turnover proxy.** The floor stands in for "enough activity to
   estimate the cost drag", in which case 20 is arbitrary — the actual
   requirement is that median return exceed 0.32% × median trades, which
   `median_return_clears_cost_floor` already checks and which passed on
   all nine symbol/scenario combinations in `SPREAD_GATES_BY_SYMBOL.md`.

3. **It is calibrated to 1h.** The number may have been set from 1h
   campaigns and never re-derived, in which case comparing a 1d strategy
   against a 1h-derived floor is a category error.

Reading 2 is the one the evidence favours: the strategy clears its own cost
floor everywhere it trades, and fails only on a count that has no stated
justification in the gate. Reading 1 remains defensible, and the choice
between them is a research decision, not a measurement one — no amount of
backtesting settles what a promotion criterion should mean.

## What is worth measuring

Not another campaign. If reading 2 is adopted, the useful measurement is
whether the spread gate holds on a symbol where `enhanced_ma` is closer to
the floor, since SOL cleared 3 of 7 windows against BTC's 1 of 7. If
reading 1 is adopted, `enhanced_ma` is out and the next subject is the
strategies the sign-off left unranked.
