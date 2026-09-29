# Spread gates applied per symbol — enhanced_ma


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

Applying the five gates added in `eeea331` to the 252 on-disk cells per
symbol (7 windows × 12 params × 3 cost scenarios), instead of the 104-cell
BTC-only campaign in `WFO_CAMPAIGN_RESULT.md`.

Script: `scripts/apply_spread_gates.py`. Thresholds are copied from
`nested_wfo.py`, not re-derived.

## Method, and why it favours the strategy

The gates in `nested_wfo.py` read `WFOOuterResult`, where each fold
contributes one row — the params the inner selection carried out. On disk a
cell is a (param × window) pair, so a fold is mapped to a test window and
the cell that cleared cost best within that window is the one an inner
selection would have taken. That takes each window's **best** cell rather
than its median, so a window counts as clearing cost if any parameter
combination in it did.

If the gates still fail under that reading, no reading is more favourable.

## Result: all three symbols fail, on the same two gates

| symbol | scenario | clearing windows | median trades | verdict |
|---|---|---|---|---|
| SOL | 1x | 3/7 (42.9%) | 10.0 | **FAIL** |
| SOL | 2x | 2/7 (28.6%) | 10.0 | **FAIL** |
| SOL | slip_stress | 2/7 (28.6%) | 10.0 | **FAIL** |
| ETH | 1x | 2/7 (28.6%) | 10.0 | **FAIL** |
| ETH | 2x | 2/7 (28.6%) | 10.0 | **FAIL** |
| ETH | slip_stress | 2/7 (28.6%) | 10.0 | **FAIL** |
| BTC | 1x | 1/7 (14.3%) | 9.0 | **FAIL** |
| BTC | 2x | 1/7 (14.3%) | 9.0 | **FAIL** |
| BTC | slip_stress | 1/7 (14.3%) | 9.0 | **FAIL** |

Per-gate detail is stable across symbols:

- `zero_trade_fold_pct_le_50` — **ok** everywhere (0.0%; every window traded)
- `median_trades_per_trading_fold_ge_20` — **FAIL** everywhere (9–10 vs 20)
- `cost_clearing_fold_pct_ge_50` — **FAIL** everywhere (14.3%–42.9% vs 50%)
- `cost_clearing_single_window_le_60pct` — ok on SOL/ETH, **FAIL** on BTC
  (100% of its single clearing window is one window)
- `median_return_clears_cost_floor` — **ok** everywhere (4.0%–9.2%)

## Reading

**The symbol does change the picture, and the gates catch that.** SOL clears
3 of 7 windows where BTC clears 1, and SOL's clearing windows are spread
across three different test windows (33.3% from the largest) while BTC's
single clearing window is 100% of its total. That is exactly the
distinction `cost_clearing_single_window_le_60pct` was written to draw,
and it is why running the campaign on BTC alone understated the strategy:
BTC is the worst case, and the finding generalised from it.

**But SOL does not pass either**, and it fails on the same two gates:

1. **Trade count.** Median 10 trades per window against a floor of 20. At
   10 trades the strategy pays 3.2% in round trips before earning
   anything. Its median clearing return of 4.0% is a real margin, but it
   rests on 10 trades a window.
2. **Spread.** At best 3 of 7 windows clear cost. The gate asks for half.
   The edge is present in some windows and absent in more of them.

The `median_return_clears_cost_floor` gate passing on all nine
symbol/scenario combinations is the strongest positive signal here: where
this strategy does trade, it clears its costs with room to spare. The
problem is frequency, not magnitude.

## Consequence for the quarantine decision

`enhanced_ma` does not qualify for promotion under the gates now in place,
on any symbol tested. The quarantine of the 43 policy stores stands, and
this run strengthens rather than weakens it: the strategies in those stores
were built with scores that do not survive attribution (`8870e03`), and the
best of them fails the promotion criteria on its own measured evidence.

What this does **not** establish is that the registry is empty of
potential. It establishes that:

- `enhanced_ma` is defensible on SOL and ETH and should be re-measured at
  a frequency that clears the trade-count floor, and
- the other 15 strategies still carry the sign-off verdicts in
  `docs/STRATEGY_SIGNOFF_P2PHASE4.md`, of which 11 were already measured
  and 5 were rejected outright.

## Next measurement that would actually be informative

Not another campaign on `enhanced_ma`. The binding constraint is trades
per window (10 against a floor of 20), which is a property of the
strategy's signal frequency on 1h bars, not of the campaign length. It
would be answered by a higher timeframe, where the same signal produces
fewer, larger holds — the one axis none of the on-disk campaigns varied.
