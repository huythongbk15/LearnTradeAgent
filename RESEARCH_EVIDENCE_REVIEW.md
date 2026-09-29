# Strategy research evidence that existed and was not used

`docs/STRATEGY_SIGNOFF_P2PHASE4.md` ranks all 16 registry strategies. It
was available the whole time and the WFO campaign in
`WFO_CAMPAIGN_RESULT.md` ignored it, re-running `enhanced_ma` on
BTC/USDT — the one strategy already shown to have no edge.

## What the sign-off says

| # | Strategy | Evidence | Sharpe | Ret | Trades | Verdict |
|---|---|---|---|---|---|---|
| 1 | `enhanced_ma` | WFO **SOL/USDT** 1h, 252 cells | 1.19 | +2.04% | **756** | FINAL_PASS 12/13 |
| 2 | `stat_arbitrage_ls` | cross-asset 4 symbols | 1.18 | +24.85% | 31 | APPROVED |
| 3 | `trend_pullback` | WFO, **HOLDOUT_FAILED** | 1.15–1.27 | varies | — | REVIEW (PBO 0.76) |
| 4–6 | — | — | — | — | — | see doc |
| 7 | `volatility_breakout` | WFO BTC 9 folds | 0.00 | 0.00% | 1 | FAIL |
| 8 | `ensemble_ma_adx` | WFO BTC 9 folds | -0.235 | -1.79% | 6 | FAIL |
| 9 | `ma_adx_regime` | WFO BTC 9 folds | 0.674 | +4.12% | 8 | FAIL (below 30-trade bar) |
| 10 | `ma_crossover` | WFO BTC 9 folds | -0.345 | -2.55% | 15 | FAIL |
| 11 | `range_mean_reversion` | WFO BTC 9 folds | -0.034 | -0.44% | 7 | FAIL |
| 12 | `regime_switching` | WFO BTC 9 folds | 0.176 | +1.74% | 15 | FAIL |
| 13 | `ma_vol_target` | WFO BTC 9 folds | 0.227 | +1.72% | 18 | FAIL (low count) |
| 14 | `ma_adx` | WFO SOL 189 cells | 0.058 | +0.03% | — | NO_TRADE 6/13 |
| 15 | `rsi` | WFO SOL 168 cells | -2.36 | -1.18% | — | NO_TRADE 3/13 |
| 16 | `bbands` | WFO SOL 84 cells | -2.27 | -1.43% | — | NO_TRADE 2/13 |

The sign-off had already run 11 of 16 strategies through WFO and rejected
most of them. Running a campaign on `enhanced_ma` without reading it repeated
work and, worse, picked the strategy whose BTC/USDT behaviour the sign-off
does not cover.

## The 852 real cells that were on disk

`data/backtests/**/enhanced_ma__*/report.json` — 852 measured cells across
nine symbols, never analysed in this session.

| symbol | cells | median return % | median trades | median Sharpe | clearing cost |
|---|---|---|---|---|---|
| SOLUSDT | 256 | 1.548 | 10 | 1.056 | 87 |
| ETHUSDT | 256 | 0.595 | 10 | 0.430 | 72 |
| TRXUSDT | 4 | 0.293 | 8 | 0.118 | 0 |
| XRPUSDT | 4 | 0.424 | 3 | 0.102 | 0 |
| NEARUSDT | 5 | -3.075 | 6 | -0.517 | 2 |
| ZECUSDT | 4 | -1.470 | 5 | -0.235 | 0 |
| **BTCUSDT** | **311** | **-1.026** | **9** | **-0.906** | **41** |
| BNBUSDT | 4 | -4.145 | 126 | -0.394 | 0 |
| DOGEUSDT | 4 | -15.345 | 52 | -0.866 | 0 |
| **total** | **852** | -0.315 | 10 | — | 202 (24%) |

## What this changes

**The strategy is not uniformly bad. The symbol decides.**

- SOL: +1.548% median, Sharpe 1.056, 87/256 cells clear cost
- ETH: +0.595% median, Sharpe 0.430, 72/256 clear cost
- BTC: **-1.026% median, Sharpe -0.906**, 41/311 clear cost

BTC — the symbol every WFO campaign in `WFO_CAMPAIGN_RESULT.md` used — is
one of the worst. The sign-off approved `enhanced_ma` on SOL evidence and
never claimed BTC. My campaign measured BTC and concluded "no edge", which
is true for BTC and false as a statement about the strategy.

The sign-off's "756 trades" is also inconsistent with the 10 trades median
here; that figure is a total across the SOL campaign, not a per-fold
median, so the two numbers are not directly comparable — but the 1.19
Sharpe it reports is consistent with the 1.056 median measured on SOL.

**24% of 852 cells clear their own cost**, against 10% in the BTC-only
campaign. The new spread gates added in `eeea331` would still apply — but
they should be evaluated per symbol, since 87/256 on SOL is a different
proposition from 41/311 on BTC.

## The actual error

Not the choice of symbol, and not the gates. The error was procedural: a
sign-off document existed, ranked every strategy, and named the evidence
source for each, and the campaign was launched without reading it. Ten
hours of compute answered a question the repository had already answered
narrower and better.

This is the same shape as the earlier silent passes in this chain — a step
that could proceed without the thing that would have caught it being
skipped. The new gates check the strategy; nothing checked that the
research had already been consulted.

## What to do before spending more compute

1. Treat the 852 cells as the primary evidence and drop the BTC-only
   campaign from the record, or keep it explicitly as a BTC finding.
2. Run the spread gates in `eeea331` against SOL and ETH separately. If
   `enhanced_ma` passes on SOL, the registry is not empty and the
   quarantine decision needs revisiting.
3. Only then decide whether to campaign the strategies the sign-off left
   unmeasured, rather than re-measuring one it already ranked.
