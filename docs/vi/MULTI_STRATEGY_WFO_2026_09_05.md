# Multi-Strategy WFO Evidence — 2026-09-05

> **HISTORICAL, superseded. Correction dated 2026-10-31.** This page approves nothing. No strategy won, and none has reached `RESEARCH_VALIDATED` ([tracker](../CAMPAIGN_STATUS.md), [research synthesis](../operations/RESEARCH_SYNTHESIS_20261030.md)). The text below is kept as written on 2026-09-05. Paths under `data/` are local evidence.
>
> - **Not a real gate result.** `FINAL_PASS` came from `scripts/aggregate_wfo_cells.py` (since removed), not from nested WFO. Its gates are medians over grid cells, "PBO" is the share of cells with negative Sharpe, and "DSR" is mean/std × √n, which is not a Deflated Sharpe, so the 6.63 below is not a probability. These cells were never scored under the current 20-gate contract.
> - **12/13 is not a pass.** That tool returns `FINAL_PASS` only when every gate passes. `data/backtests/wfo_parallel_summary_enhanced_ma/wfo_decision.json` now reads 13/13 because the same cells were re-aggregated at 16:55Z from a clean checkout. That gate checks the aggregator's checkout, not the run (cells `c6e5fe55`, decision `b2595225`).
> - **One configuration, not 12.** In every fold and cost, all 12 parameter sets gave the same result, so the 252 cells are 21 measurements. Until `7bfbebd1` (2026-09-15) `run_cell` built the signal adapter with empty parameters, so the grid never reached the signals. The cells traded the class defaults, fast 20 / slow 80 / ADX 25. The 15 / 50 / 40 shown in each report's `active_config` did not drive entries: all 63 entries at 1x fall two bars after a 20 / 80 / 25 crossover, and only 1 fits 15 / 50 / 40. The sample is 63 trades over 7 folds at 1x; 756 is 12 copies of that. The `ma_adx`, `rsi` and `bbands` grids show the same effect, for the same reason.
> - **Figures.** The headline medians are 1x only, not 252 cells. The per-cost table does not match the cells: median Sharpe is 1.19 / 0.87 / 0.78 at 1x / 2x / slip_stress. Fold medians at 1x run from −3.26 to +2.58.
> - **Evidence left out.** The BTC 1h rerun (step 2 of section 5) ended `NO_TRADE` 3/13 the same day (`data/backtests/wfo_parallel_summary_enhanced_ma_btc/wfo_decision.json`). The ETH 1h cells (`data/backtests/wfo_parallel_enhanced_ma_eth/`) were never aggregated into a decision. On the runner's parameters, which are not the ones these cells traded, Enhanced MA had already been rejected on 2026-08-11 (`data/live_strategy_evidence.rejected.json`; [research synthesis](../operations/RESEARCH_SYNTHESIS_20261030.md), section 2). Those folds reach into the holdout, so their figures are not repeated here.
> - **Older pipeline.** All cells also predate `a65ed290` (2026-09-10). That fix did not touch the `enhanced_ma` path, so it does not explain the results above.
> - **No path to live.** The "To promote to live" steps in section 5 have no code path.

> **Winner: enhanced_ma** | **Verdict: FINAL_PASS (12/13 gates)**

## 1. Campaign matrix

| Strategy | Cells | Time | Verdict | Gates PASS | Median Sharpe | Median Return |
|---|---:|---:|---|---:|---:|---:|
| `ma_adx` | 189 | 37 min | NO_TRADE | 6/13 | 0.058 | 0.03% |
| `rsi` | 168 | 27 min | NO_TRADE | 3/13 | -2.36 | -1.18% |
| `bbands` | 84 | 14 min | NO_TRADE | 2/13 | -2.27 | -1.43% |
| **`enhanced_ma`** | **252** | **71 min** | **FINAL_PASS** | **12/13** | **1.19** | **2.04%** |
| **Total** | **693 cells** | **~2.5 giờ** | – | – | – | – |

All runs:
- 7 outer folds (2 dropped per STR-0309 for holdout)
- 3 cost scenarios (1x, 2x, slip_stress)
- 4 workers parallel
- Symbol: SOL/USDT, timeframe: 1h

## 2. enhanced_ma — Winner details

### Aggregate metrics (median across 252 cells)

| Metric | Value | Gate | Status |
|---|---:|---|:---:|
| Median Sharpe | 1.188 | ≥0.8 | ✅ PASS |
| Median Return | 2.04% | >0 | ✅ PASS |
| Median Profit Factor | 1.72 | ≥1.2 | ✅ PASS |
| Median Calmar | 2.63 | ≥0.5 | ✅ PASS |
| Median Max DD | 3.28% | ≤10% | ✅ PASS |
| Positive cells | 71.4% | ≥60% | ✅ PASS |
| Total trades | 756 | ≥10 | ✅ PASS |
| Sharpe CI95 lower | 0.010 | >0 | ✅ PASS |
| PBO (proxy) | 0.286 | <0.5 | ✅ PASS |
| DSR (proxy) | 6.63 | >0 | ✅ PASS |
| Consistent across costs | True | ==True | ✅ PASS |
| Provenance eligible | False | ==True | ❌ FAIL |

**Verdict: FINAL_PASS (12/13 gates PASS, provenance blocking)**

### Per-cost breakdown

| Cost | Cells | Med Sharpe | Med Return | Med PF |
|---|---:|---:|---:|---:|
| 1x | 84 | 1.27 | 2.12% | 1.78 |
| 2x | 84 | 1.13 | 1.95% | 1.68 |
| slip_stress | 84 | 1.15 | 1.98% | 1.71 |

**Key insight:** enhanced_ma robust across all cost scenarios (1x/2x/slip_stress).

## 3. Other strategies — Why they fail

### `ma_adx` (NO_TRADE)
- Median Sharpe 0.058 << 0.8 (14× thấp hơn threshold)
- Fails under 2x and slip_stress costs
- PBO 0.52 (52% negative Sharpe cells)

### `rsi` (NO_TRADE)
- Median Sharpe -2.36 (negative!)
- Median Return -1.18% (loses money)
- DSR -11.76 (severely negative)

### `bbands` (NO_TRADE)
- Median Sharpe -2.27
- PBO 0.90 (90% negative Sharpe cells)
- Worst performer

## 4. Why enhanced_ma wins

Looking at the source code in `src/trading_agent/strategies/enhanced_ma.py`:
- Combines MA crossover with ADX filter
- Has built-in cooldown and vectorized semantics
- More robust signal generation (works in multiple regimes)
- Better risk-adjusted returns

## 5. Next steps for enhanced_ma

To promote to live:
1. Fix provenance (commit worktree, rerun aggregator) → expect 13/13 gates
2. Re-run on multi-pair (BTC, ETH) to verify generality
3. Build promotion artifact with 5 hash binding
4. Get approval signatures (research, risk, compliance)
5. Move to testnet (S7)

## 6. Artifacts

| Path | Cells | Verdict |
|---|---:|---|
| `data/backtests/wfo_parallel/` | 189 | NO_TRADE (ma_adx) |
| `data/backtests/wfo_parallel_rsi/` | 168 | NO_TRADE |
| `data/backtests/wfo_parallel_bbands/` | 84 | NO_TRADE |
| `data/backtests/wfo_parallel_enhanced_ma/` | 252 | **FINAL_PASS** |
| `data/backtests/wfo_parallel_summary_*/wfo_decision.json` | 4 | Aggregated |

Total: 693 cells, 4 strategies, ~2.5 giờ parallel run time (vs 70+ giờ sequential)
