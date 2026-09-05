# WFO Parallel Evidence — 2026-09-05

> **Status:** 189 cells completed in 37 phút (vs ~14 giờ sequential) — **22× speedup**

## 1. Campaign metadata

| Field | Value |
|---|---|
| Strategy | `ma_adx` |
| Symbol | `SOL/USDT` |
| Timeframe | 1h |
| Param combos | 9 (fast_ma × slow_ma × adx_period × adx_threshold) |
| Cost scenarios | 3 (1x, 2x, slip_stress) |
| Outer folds | 7 (2 dropped per STR-0309) |
| **Total cells** | **189** (9 × 3 × 7) |
| Workers | 6 (out of 12 cores) |
| Run time | 2224s (~37 phút) |
| Throughput | 0.085 cells/s |
| Speedup vs sequential | **~22×** |
| Commit SHA | `5dcbab5` (clean working tree) |
| Evidence class | `REAL_MARKET` |

## 2. Coverage by cost scenario

| Cost | Cells | Per-folds |
|---|---:|---:|
| 1x | 63 | 9 params × 7 folds |
| 2x | 63 | 9 params × 7 folds |
| slip_stress | 63 | 9 params × 7 folds |

## 3. Performance

| Metric | Sequential | Parallel (6 workers) |
|---|---|---|
| Cells | 168 (medium) | 189 (full) |
| Time | ~14h | 37 min |
| Throughput | ~0.003 cells/s | 0.085 cells/s |
| Speedup | 1× | **~22×** |

## 4. Bug fix during run

- `gap_exceptions_path` was being passed to `FullSystemSimulator.__init__()` which doesn't accept this argument
- Fixed in `src/trading_agent/backtest/tournament.py:1147-1158` (removed invalid kwarg)
- All 189 cells completed after fix

## 5. Comparison with WFO medium (2026-09-04)

| Aspect | WFO medium | WFO parallel |
|---|---|---|
| Spec | 9 params × 2 costs | 9 params × 3 costs |
| Cells | 168 | 189 |
| Time | 14h sequential | 37 min parallel |
| Verdict | NO_TRADE | (TBD via wfo_summary) |
| Provenance | worktree_dirty=1.0 | (TBD) |

## 6. Artifacts

| Path | Description |
|---|---|
| `data/backtests/wfo_parallel/ma_adx__SOLUSDT__1h__*` | 189 per-cell report.json + execution/ |
| `data/wfo/experiments_parallel.sqlite3` | Trial registry |
| `/tmp/wfo_parallel_full.log` | Full run log |

## 7. Next steps

- Run `run_wfo_minimal.py` with proper sequential pipeline to generate `wfo_decision.json` with gates + holdout (or build aggregator from wfo_parallel cells)
- Verify provenance_eligible improves (was 0.0 in medium run due to dirty worktree)
- Commit evidence artifacts + summary
