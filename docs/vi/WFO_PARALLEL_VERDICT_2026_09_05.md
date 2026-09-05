# WFO Parallel Final Verdict — 2026-09-05

> **Verdict: NO_TRADE** (6/13 hard gates PASS) | **Provenance: ELIGIBLE**

## 1. Campaign summary

| Field | Value |
|---|---|
| Strategy | `ma_adx` |
| Symbol | `SOL/USDT` |
| Timeframe | 1h |
| Cells | 189 (9 params × 3 costs × 7 folds) |
| Run time | 37 phút (parallel, 6 workers) |
| Speedup vs sequential | ~22× |
| Commit SHA | `3ad31df` (clean worktree) |
| Evidence class | `REAL_MARKET` |
| Provenance eligible | ✅ True |

## 2. Aggregate metrics (median across 189 cells)

| Metric | Value | Status |
|---|---:|---|
| Median Sharpe | 0.0575 | ⚠️ Low |
| Median Return | 0.030% | ✅ Positive |
| Median Profit Factor | 1.007 | ⚠️ ~1.0 |
| Median Calmar | 0.023 | ⚠️ Very low |
| Median Max DD | 3.72% | ✅ |
| Positive cells | 57.1% | ⚠️ Below 60% |
| Total trades | 486 | ✅ |
| Total net PnL | $3,067 | ✅ |

## 3. Per-cost breakdown

| Cost | Cells | Med Sharpe | Med Return | Med PF | Med Calmar | Pos % |
|---|---:|---:|---:|---:|---:|---:|
| 1x | 63 | 0.058 | 0.03% | 1.01 | 0.023 | 57.1% |
| 2x | 63 | -0.27 | -1.42% | 0.80 | -0.50 | 30.2% |
| slip_stress | 63 | -0.46 | -2.34% | 0.69 | -0.78 | 23.8% |

**Key insight:** Strategy only profitable at base cost (1x). Fails under any cost stress (2x or slip_stress median return is negative).

## 4. Hard gate evaluation (13 gates)

### PASS (6/13):
- ✅ `outer_oos_net_return_positive`: 0.030% > 0
- ✅ `outer_oos_max_drawdown_le_10pct`: 3.72% ≤ 10%
- ✅ `outer_oos_min_trades_ge_10`: 486 ≥ 10
- ✅ `outer_oos_calmar_positive`: 0.023 > 0
- ✅ `outer_oos_dsr_positive`: 2.45 > 0
- ✅ `outer_oos_provenance_eligible`: True == True

### FAIL (7/13):
- ❌ `outer_oos_sharpe_ge_080`: 0.058 < 0.8
- ❌ `outer_oos_profit_factor_ge_120`: 1.01 < 1.2
- ❌ `outer_oos_calmar_ge_050`: 0.023 < 0.5
- ❌ `outer_oos_positive_folds_ge_60pct`: 57.1% < 60%
- ❌ `outer_oos_sharpe_ci_lower_positive`: -0.38 < 0 (CI crosses 0)
- ❌ `outer_oos_pbo_lt_050`: 0.52 ≥ 0.5
- ❌ `outer_oos_consistent_across_costs`: False (2x + slip_stress fail)

## 5. Statistical hardening

| Metric | Value | Interpretation |
|---|---:|---|
| Sharpe 95% CI | [-0.38, 0.49] | Crosses 0 — not significant |
| PBO (proxy) | 0.524 | 52% of cells have negative Sharpe |
| DSR (proxy) | 2.45 | Positive but limited N |

## 6. Verdict rationale

**NO_TRADE** because:
1. Median Sharpe is far below the 0.8 threshold (0.058 vs 0.8 = 14× lower)
2. Cost consistency fails — strategy only works at base cost (1x), fails under 2x or slippage stress
3. PBO > 0.5 — more than half of cells have negative Sharpe
4. Sharpe CI crosses 0 — statistical significance not achieved

The strategy has positive expectancy at base cost but:
- Not robust to transaction costs
- Not robust to slippage
- Not statistically significant
- Low Calmar ratio (high volatility relative to return)

## 7. Comparison with WFO medium (2026-09-04)

| Metric | WFO medium | WFO parallel | Δ |
|---|---|---|---|
| Cells | 168 | 189 | +12% |
| Costs | 1x, 2x | 1x, 2x, slip_stress | +1 |
| Time | 14h | 37 min | -96% |
| Verdict | NO_TRADE | NO_TRADE | Consistent |
| Provenance | dirty (0) | clean (1) | ✅ Improved |
| Passes gates | 2/6 (legacy) | 6/13 | +4 |

## 8. Artifacts

| Path | Description |
|---|---|
| `data/backtests/wfo_parallel/` | 189 per-cell report.json + execution/ |
| `data/backtests/wfo_parallel_summary/wfo_decision.json` | Aggregated decision with verdict |
| `data/wfo/experiments_parallel.sqlite3` | Trial registry |
| `scripts/aggregate_wfo_cells.py` | Reusable aggregator |
| `docs/vi/WFO_PARALLEL_2026_09_05.md` | Run evidence |
| `docs/vi/WFO_MEDIUM_EVIDENCE_2026_09_04.md` | Previous run for comparison |

## 9. Next steps

- Accept NO_TRADE for `ma_adx` on SOL/USDT
- Try other strategies (rsi, enhanced_ma, bbands) using parallel runner
- Document this as baseline S3 evidence for future comparison
- Phase 6 Week 2: move to S4 (promotion binding) and S5 (adaptive re-eval)
