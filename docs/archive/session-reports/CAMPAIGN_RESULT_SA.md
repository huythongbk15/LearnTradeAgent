# Campaign result — stat_arbitrage_lo (first candidate clearing both bars)

`scripts/build_campaign_policy.py --strategy stat_arbitrage_lo`
BTC/USDT 1d · train/val/test/step 12/2/2/2 · 32 folds · window 61 bar

```
median sharpe   -0.009
median return   -0.029%
total trades    45 across 28 folds
folds trading   20 of 28   ← density barrier cleared
folds positive    7 of 28 (25%)
gate requires    19 of 32 (59%)
hard gates      FAIL, 15 of them
activated       0
```

## What this separates

Trade frequency and edge had been treated as one constraint. This strategy
satisfies the frequency one and still fails, so they are independent:

| constraint | stat_arbitrage_lo | verdict |
|---|---|---|
| enough trades per fold | 5/window, 98% folds non-empty | **clears** |
| enough folds for the gate | 32 folds, needs 19 | **clears** |
| clears costs above chance | 7/28 = 25% | **fails** |

The density constraint was necessary but not sufficient.

## The finding

`probe_daily_tradeability.py` measured a **70.7% win rate** — the highest
anywhere in this session. Here that produces a median fold return of
-0.029%, with only 25% of folds positive.

Winning 70% of the time and losing money means losses exceed wins:

```
worst fold  -7.58%
best fold   +5.30%
median      -0.029%
```

The campaign log shows the mechanism: the drawdown circuit breaker fires at
15.2% repeatedly, and protective stops trigger on adverse moves. Wins are
cut small by the stop; losses run to the same stop. The asymmetry is the
defect.

This is the first item in the registry whose failure is *not* "no edge".
It is a risk-management problem: sizing, stop placement, or an
asymmetry-aware objective. That is actionable in a way that "the signal
does not work" is not.

## What follows

1. Measure the payoff distribution directly — average win against average
   loss. A 70% hit rate with negative expectancy means the ratio is under
   0.71 (win/loss), which is a sizing and stop question, not a signal one.
2. Do not widen the search to other strategies yet. This one has a real
   hit rate and a fixable asymmetry; a broad sweep would hide that under
   more no-edge results.
3. `funding_carry` remains unmeasured — it needs `funding_rate` merged into
   the daily parquet via `scripts/merge_funding_rates.py`.
