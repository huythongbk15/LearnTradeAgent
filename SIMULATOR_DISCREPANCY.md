# Simulator vs standard backtest — investigation

## Symptom

`tests/test_execution_simulator.py::TestSimulatorBacktestEngine::test_simulator_vs_standard`
failed on BTC/USDT 1d, 500 bars, `ma_crossover(50/200)`, matching cost
assumptions:

| | standard engine | simulator |
|---|---|---|
| total_return_pct | 44.34 | 420.86 |
| total_trades | 0 | 1 |
| max_drawdown_pct | -9.93 | 24.91 |
| win_rate | 0.00 | 1.00 |

The assertion is that the simulator must not beat the plain engine by more
than 5%. It exceeds by 376 points.

This test could not run at all between 2026-08-24 and 2026-09-29:
`backtest_sim/__init__.py` listed `run_simulator_backtest` in `__all__`
without importing it, so pytest aborted collection for the entire `ci-fast`
shard and 1,287 tests never executed. Restoring the import in `804c6ca`
made this failure visible for the first time.

## What the data actually is

```
bars          : 500 daily
raw signals   : 1  (index 199, value +1)
entry close   : 9,170.28
final close   : 49,841.45
gross return  : +443.51%
```

One entry, held to the end of the window. MA 50/200 needs 200 bars of
warm-up, so the first 300 bars produce no signal at all.

## Finding 1 — total_trades ignored positions still open (fixed)

`BacktestEngine` counted only `closed_trades`, so the single trade — open
across 299 bars, carrying +44,335 USD — was invisible:

```python
trades           : 1
is_open          : True
pnl_abs          : 44,335
bars_held        : 299
total_trades     : 0     # <- before
```

A strategy holding a position through the final bar executes a real round
trip and its P&L is in the equity curve, yet every report said "0 trades"
with a 44% return. `total_trades` now counts all trades; `win_rate` and
`profit_factor` stay on closed trades only, because an open position has no
realised outcome. A new `open_trades` field reports the split.

This affects every campaign metric that filters on trade count, and it is
how a profitable run reads as no trading at all.

## Finding 2 — the simulator is right and the engine is wrong here

Simulator's 420.86% is arithmetically correct:

```
gross 443.51%  -  cost  ~0.5%  ≈  net 443%
```

with `total_fees` 305.80 and `total_slippage` 185.92 — 491.72 USD, which is
0.49% of a 100k account. The standard engine's 44.34% is the same trade
under a different mark: its equity curve ends at 144,335, i.e. the position
*is* marked to market, so the two numbers contradict each other inside the
same engine.

An unresolved 22-point gap remains: 443% gross against 420.86% net is 22
points, and the 491.72 USD of cost only explains 0.49 of it. The difference
is likely fill timing or slippage modelling inside the simulator, but that
has not been traced to a line of code and is not claimed here.

## What was changed

- `src/trading_agent/backtest_sim/__init__.py` — import `run_simulator_backtest`
  (restores the export the module split dropped)
- `src/trading_agent/backtest/engine.py` — `total_trades` counts open
  positions; new `open_trades` field; win rate and profit factor unchanged

## What was not

`test_simulator_vs_standard` still fails. The assertion embeds an
assumption that the simulator is the pessimistic side of the comparison,
and on this data it is the accurate one. Two ways to close it:

1. Find the remaining 22 points in the simulator's fill accounting and make
   the two agree to a stated tolerance.
2. Rewrite the assertion to compare mark-to-market equity rather than
   assuming an ordering, since a simulator that models slippage and fees
   honestly can legitimately beat an engine that under-counts.

Option 1 first: a 10x disagreement between two engines on identical input
is worth understanding rather than asserting away. Until then the test
stays red deliberately — tuning the tolerance would hide the exact defect
that five weeks of silence concealed.

## Reproduction

```bash
.venv/bin/python scripts/diagnose_simulator_discrepancy.py
```

Prints both engines side by side, the raw signal count, and the implied
entry and exit prices.