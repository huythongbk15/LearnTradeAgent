# WFO Medium Evidence Report — 2026-09-04

> **Verdict: NO_TRADE** | **Spec:** `ma_adx` × `SOL/USDT` × `1h` × `{1x, 2x cost}` × 9 params × 7 folds

## 1. Campaign metadata

| Field | Value |
|---|---|
| Strategy | `ma_adx` |
| Symbol | `SOL/USDT` |
| Timeframe | 1h |
| Param combos | 9 (fast_ma × slow_ma × adx_period × adx_threshold) |
| Cost scenarios | 2 (1x base, 2x stress) |
| Outer folds | 7 (2 dropped per STR-0309 for holdout overlap) |
| Total cells | 168 (91 cost 1x + 70 cost 2x + 7 slip_stress) |
| Sensitivity re-runs | cost_2x + slippage_stress + drop_best_trade + delay_1_bar + parameter_neighbors |
| Search family | `s3_wfo_medium:8dbf9c8e2e86318163eeeab9` |
| Evidence class | `REAL_MARKET` |
| Study manifest | `sha256:c903164d4ba66ed04378cf08646fb5dfb5c12eae5fd70deddd232406944064f3` |
| Run timestamp | 2026-09-04 22:54 |

## 2. Aggregate OOS metrics (7 outer folds)

| Metric | Value | Threshold | Status |
|---|---|---|---|
| Median Sharpe | 0.0575 | ≥ 0.8 | ❌ FAIL |
| Mean Sharpe | 0.0199 | – | – |
| Median return | 0.03% | > 0 | ✅ PASS |
| Mean return | 0.78% | – | – |
| Total trades | 54 | – | – |
| Positive folds | 57.1% (4/7) | ≥ 60% | ❌ FAIL |
| Median profit factor | 1.007 | ≥ 1.2 | ❌ FAIL |
| Median max DD | 3.72% | ≤ 10% | ✅ PASS |
| Median Calmar | 0.023 | ≥ 0.5 | ❌ FAIL |
| Total OOS PnL | $549.34 | – | – |

## 3. Per-fold breakdown

| Fold | Test window | Sharpe | Return % | Trades | PF | Max DD % |
|---|---|---:|---:|---:|---:|---:|
| fold_000 | 11200-13360 | -1.435 | -2.59 | 8 | 0.43 | 4.43 |
| fold_001 | 13360-15520 | 1.589 | 3.74 | 11 | 2.46 | 3.05 |
| fold_002 | 15520-17680 | 2.100 | 4.43 | 7 | 3.60 | 3.72 |
| fold_003 | 17680-19840 | 1.028 | 3.03 | 4 | 2.45 | 5.76 |
| fold_004 | 19840-22000 | 0.058 | 0.03 | 10 | 1.01 | 5.29 |
| fold_005 | 22000-24160 | -0.074 | -0.15 | 7 | 0.95 | 2.71 |
| fold_006 | 24160-26320 | -3.126 | -2.99 | 7 | 0.09 | 3.28 |

**Distribution:** 3 positive folds (001, 002, 003), 1 marginal (004), 3 negative (000, 005, 006).

## 4. Hard gate evaluation

| Gate | Observed | Threshold | Verdict |
|---|---:|---:|:---:|
| `outer_oos_net_return_positive` | 0.030% | > 0 | ✅ PASS |
| `outer_oos_sharpe_ge_080` | 0.058 | ≥ 0.8 | ❌ FAIL |
| `outer_oos_profit_factor_ge_120` | 1.007 | ≥ 1.2 | ❌ FAIL |
| `outer_oos_max_drawdown_le_10pct` | 3.72% | ≤ 10% | ✅ PASS |
| `outer_oos_calmar_ge_050` | 0.023 | ≥ 0.5 | ❌ FAIL |
| `outer_oos_positive_folds_ge_60pct` | 57.1% | ≥ 60% | ❌ FAIL |

**Result: 2/6 PASS → not promotable.**

## 5. Multi-dimensional backing (STR-0307)

### By regime
| Regime | Trades | Net PnL | Win rate | PF |
|---|---:|---:|---:|---:|
| trending | 51 | +$638.37 | 39.2% | 1.33 |
| ranging | 3 | -$89.03 | 33.3% | 0.62 |

### By year
| Year | Trades | Net PnL | Win rate | PF |
|---|---:|---:|---:|---:|
| 2024 | 26 | +$557.67 | 46.2% | 1.64 |
| 2025 | 28 | -$8.33 | 32.1% | 0.99 |

### By volatility bucket
| Bucket | Trades | Net PnL | Win rate | PF |
|---|---:|---:|---:|---:|
| low_vol | 13 | -$326.96 | 23.1% | 0.36 |
| mid_vol | 23 | +$40.21 | 34.8% | 1.05 |
| high_vol | 18 | +$836.09 | 55.6% | 2.04 |

**Insight:** Strategy chỉ profitable trong high_vol trending conditions. Cần regime filter.

## 6. Sensitivity analysis

### Cost stress (cost_2x)
- Median return: -0.47% (vs baseline +0.03%) → **strategy fails under realistic 2x cost**
- Median PF: 0.84 (vs 1.01)

### Slippage stress
- Median return: -0.58% → **fails under slippage stress**
- Median PF: 0.81

### Drop best trade
- Total after dropping best: -$1,274 → **strategy PnL is concentrated in few lucky trades**

### Delay-1-bar
- Return: 0.0%, trades: 0 → **signal fully stale when delayed 1 bar** (causality failure)

### Parameter neighbors
- Best params consistently `fast_ma=10, slow_ma=40` but neighbors underperform significantly → **fragile parameter selection**

## 7. Verdict

**`NO_TRADE`** — `ma_adx` strategy on SOL/USDT 1h fails 4/6 hard gates:
1. Sharpe too low (0.058 < 0.8)
2. Profit factor too low (1.007 < 1.2)
3. Calmar too low (0.023 < 0.5)
4. Positive folds rate below threshold (57.1% < 60%)

Plus sensitivity analysis shows the strategy:
- Doesn't survive realistic cost/slippage stress
- Has stale signals (delay-1-bar = 0 trades)
- Concentrated PnL in few outliers
- Fragile to parameter perturbations

**This is a fail-closed verdict, not a bug.** The pipeline correctly rejected an insufficient strategy.

## 8. Trial provenance

| Field | Value |
|---|---|
| Inner validation trials | 126 |
| Outer OOS trials | 7 |
| Total trial runs | 133 |
| Unique experiments | 18 |
| Search family | `s3_wfo_medium:8dbf9c8e2e86318163eeeab9` |
| Effective trial count | 18 (conservative, no empirical correlation matrix) |

## 9. Artifacts

| Path | Size | Purpose |
|---|---:|---|
| `data/backtests/wfo_medium/wfo_decision.json` | 6.3 MB | Full WFO result tree |
| `data/backtests/wfo_medium/ma_adx__SOLUSDT__1h__*` | 168 dirs | Per-cell report.json + execution/ |
| `data/backtests/wfo_medium/study_manifests/c903164...json` | – | Frozen study manifest |
| `data/wfo/experiments_medium.sqlite3` | – | Trial registry (append-only) |

## 10. Next steps

- ❌ **Do not promote** `ma_adx` to live with this evidence
- 🔄 Re-run with different strategy (e.g., `enhanced_ma`, `rsi`)
- 🔄 Or scope: shorter time window, different timeframe (4h), more pairs
- 🔄 Or accept NO_TRADE and document as known limitation
