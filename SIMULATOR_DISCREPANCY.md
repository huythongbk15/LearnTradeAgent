# Simulator vs standard backtest — resolved

## Symptom

`test_simulator_vs_standard` failed on BTC/USDT 1d, 500 bars,
`ma_crossover(50/200)`, matching cost assumptions:

| | standard engine | simulator |
|---|---|---|
| total_return_pct | 44.34 | 420.86 |
| total_trades | 0 | 1 |
| max_drawdown_pct | -9.93 | 24.91 |

The assertion was that the simulator must not beat the plain engine by more
than 5%. It exceeded by 376 points.

The test could not run at all between 2026-08-24 and 2026-09-29:
`backtest_sim/__init__.py` listed `run_simulator_backtest` in `__all__`
without importing it, so pytest aborted collection for the whole `ci-fast`
shard and 1,287 tests never executed. Restoring the import in `804c6ca`
made this visible for the first time.

## Cause: the comparison measured position sizing, not fill realism

`BacktestEngine` defaults to `fixed_position_pct=0.10`.
`_create_entry_order` in the simulator spends `self._cash * 0.95`. On a
window where the strategy takes one entry and holds it 299 days through a
6x move, that alone is a 9.5x difference in exposure.

Matching the parameter settles it:

```
engine fixed_position_pct=0.10 ->  44.34%
engine fixed_position_pct=0.95 -> 421.18%
simulator                      -> 420.86%     (0.32pp apart)
```

Reconstructed by hand:

```
engine :  89,995 + 10,005 x 49,841.45/9,172.13 = 144,362  -> +44.33%
sim    :   5,000 + 95,000 x 49,841.45/9,211.79 = 519,008  -> +419.01%
```

Both engines are correct. The test asserted on two different
configurations.

## There is no lookahead bias

Worth stating because it was suspected mid-investigation and the suspicion
was wrong. The engine uses `previous_signal = signals[i - 1]` and fills at
`open_prices[i]`, so a signal on bar 199 fills at the open of bar 200.
Verified directly: entry 9,172.13 equals `open[200] x (1 + slippage)`.

The 0.43% gap between the two engines' entry prices is `open[200]` against
`close[200]` — a choice of reference price within the same next bar, not a
timing difference.

## A real defect was found on the way

`total_trades` counted only closed positions, so the single trade — open
across 299 bars, carrying +44,335 USD — was reported as zero trades beside
a 44% return. A profitable run read as no trading at all.

`total_trades` now counts every trade. `win_rate` and `profit_factor` stay
on closed trades, since an open position has no realised outcome, and a new
`open_trades` field reports the split.

## Changes

- `src/trading_agent/execution/backtest_sim/__init__.py` — import
  `run_simulator_backtest`, restoring the export the module split dropped
- `src/trading_agent/backtest/engine.py` — `total_trades` counts open
  positions; new `open_trades`
- `tests/test_execution_simulator.py` — set `fixed_position_pct=0.95` so the
  comparison holds position sizing constant

## State

11/11 simulator tests pass. `scripts/diagnose_simulator_discrepancy.py` and
`scripts/trace_simulator_fills.py` reproduce the comparison side by side if
either engine changes again.

No historical measurement needs re-running as a result: the discrepancy was
in how the two were compared, not in either one's arithmetic.

## One thing this exposed

The investigation twice reached a confident wrong answer — first that
`total_trades` was merely a reporting quirk worth leaving alone, then that
the engine had a lookahead bias. Both were settled by running the number
rather than reading the code: the first by inspecting the trade object, the
second by comparing the entry price against `open[200]` and `close[200]`.
Neither would have been caught by reading more carefully.