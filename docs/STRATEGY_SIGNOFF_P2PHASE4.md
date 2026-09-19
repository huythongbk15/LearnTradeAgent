# P2 Phase 4 — Strategy Sign-off Review List (16 strategies)

> **Purpose:** RiskManager sign-off checklist for all 16 active tournament pool strategies.
> **Pool basis:** `FIRST_WAVE_DESCRIPTORS` minus `funding_carry` (archived as S5/NO_TRADE).
> **Evidence sources:** WFO campaigns (SOL/USDT 1h), cross-asset backtests (4-symbol universe),
> S6 campaign, and prior phase results.

---

## Summary Table

| # | Strategy | Type | Evidence | Sharpe | Ret | Max DD | Trades | Verdict | RiskManager |
|---|----------|------|----------|-------:|-----:|-------:|-------:|---------|-------------|
| 1 | `enhanced_ma` | MA+ADX trend | WFO SOL 1h (252 cells) + ETH 1h | **1.19** | +2.04% | -3.28% | 756 | FINAL_PASS (12/13 gates) | ✅ **APPROVED** — best performer |
| 2 | `stat_arbitrage_ls` | Stat arb (long/short) | Cross-asset (31783 bars) | **1.18** | +24.85% | -5.69% | 31 | — | ✅ **APPROVED** — robust LS |
| 3 | `trend_pullback` | MA+ADX pullback | WFO HOLDOUT_FAILED | 1.15–1.27 | varies | — | — | HOLD (provenance) | ⚠️ **REVIEW** — good Sharpe, holdout failed |
| 4 | `stat_arbitrage_lo` | Stat arb (long only) | Cross-asset | 0.49 | +8.36% | -4.50% | 10 | — | ⚠️ **CONDITIONAL** — low trade count |
| 5 | `cross_sectional_momentum_lo` | Cross-sectional MO | Cross-asset (LO) | 0.40 | +2.84% | -67.41% | 145 | — | ❌ **REJECT** — high DD |
| 6 | `cross_sectional_momentum_ls` | Cross-sectional MO (LS) | Cross-asset (CSMO LS) | 0.44 | +7.42% | -48.16% | 173 | — | ⚠️ **MONITOR** — DD concern |
| 7 | `volatility_breakout` | Volatility breakout | WFO BTC/USDT 1h (9 folds, 216 cells) | 0.00 | 0.00% | -9.14% | 1 | FAIL | Sharpe=0.00, 1 trade/fold (4/9 no-trade folds) |
| 8 | `ensemble_ma_adx` | Ensemble MA+ADX | WFO BTC/USDT 1h (9 folds, 36 cells) | -0.2349 | -1.79% | -11.58% | 6 | FAIL | negative Sharpe, 6 trades/fold |
| 9 | `ma_adx_regime` | Regime-aware MA | WFO BTC/USDT 1h (9 folds, 162 cells) | 0.6740 | +4.12% | -11.41% | 8 | FAIL | best Sharpe (0.674), positive return, 8 trades/fold < 30 threshold; retains on cross-asset basis |
| 10 | `ma_crossover` | Simple MA crossover | WFO BTC/USDT 1h (9 folds, 81 cells) | -0.3445 | -2.55% | -14.34% | 15 | FAIL | negative Sharpe, 15 trades/fold |
| 11 | `range_mean_reversion` | Mean reversion (RSI+BB) | WFO BTC/USDT 1h (9 folds, 576 cells) | -0.0343 | -0.44% | -21.94% | 7 | FAIL | near-zero Sharpe, high DD, 7 trades/fold |
| 12 | `regime_switching` | Regime switching | WFO BTC/USDT 1h (9 folds, 144 cells) | 0.1760 | +1.74% | -17.53% | 15 | FAIL | positive return, low Sharpe (0.176), 15 trades/fold; slow per-cell (20s) |
| 13 | `ma_vol_target` | MA + vol targeting | WFO BTC/USDT 1h (9 folds, 36 cells) | 0.2273 | +1.72% | -13.41% | 18 | FAIL | positive Sharpe/return but low trade count (18/fold); consider vol-attenuated sizing |
| 14 | `ma_adx` | MA+ADX crossover | WFO SOL 1h (189 cells) | 0.058 | +0.03% | — | — | NO_TRADE (6/13) | ❌ **REJECT** — no edge |
| 15 | `rsi` | Momentum (RSI) | WFO SOL 1h (168 cells) | -2.36 | -1.18% | — | — | NO_TRADE (3/13) | ❌ **REJECT** — negative edge |
| 16 | `bbands` | Volatility (Bollinger) | WFO SOL 1h (84 cells) | -2.27 | -1.43% | — | — | NO_TRADE (2/13) | ❌ **REJECT** — no edge |

> `funding_carry` excluded from pool — archived as S5/NO_TRADE, Sharpe 1.293 (not robust).

---

## Detailed Evidence

### Tier A — Approved for live promotion candidates

#### 1. `enhanced_ma`
- **Evidence:** WFO campaign on SOL/USDT 1h, 252 cells, 7 outer folds × 3 cost scenarios × 12 params
- **Source:** `docs/vi/MULTI_STRATEGY_WFO_2026_09_05.md`, `data/backtests/wfo_parallel/`
- **Metrics (median):** Sharpe 1.19, return +2.04%, PF 1.72, Calmar 2.63, max DD 3.28%
- **PBO proxy:** 0.286 (< 0.5 ✅), **DSR proxy:** 6.63 (> 0 ✅)
- **Cost robustness:** Sharpe 1.27 (1x) / 1.13 (2x) / 1.15 (slip_stress) — consistent
- **Gates:** 12/13 PASS (provenance blocking)
- **RiskManager recommendation:** ✅ APPROVED — only strategy passing majority gates

#### 2. `stat_arbitrage_ls`
- **Evidence:** Cross-asset backtest, 4-symbol universe (BTC/ETH/BNB/XRP), 31783 hourly bars
- **Source:** `data/backtests/cross_asset/stat_arbitrage_ls.json`
- **Metrics:** Sharpe 1.18, return +24.85%, max DD -5.69%, 31 trades
- **RiskManager recommendation:** ✅ APPROVED — strong risk-adjusted return in LS configuration

#### 3. `trend_pullback`
- **Evidence:** WFO campaign, HOLDOUT_FAILED verdict, best params: ma_fast=20, ma_slow=50/80/120, adx_threshold=15-25, vol_multiplier=0.5-1.0
- **Source:** `docs/vi/MULTI_STRATEGY_WFO_2026_09_05.md` (referenced in Phase 2 archive seq 42737)
- **Metrics:** Sharpe 1.15–1.27 across cost scenarios (1x/2x/slip_stress)
- **PBO deflation:** 0.76 — high deflation factor indicates overfitting risk
- **RiskManager recommendation:** ⚠️ REVIEW — Sharpe positive but PBO deflation 0.76 suggests
  overfitting; holdout failed. Needs parameter re-validation.

### Tier B — Conditional / Monitor

#### 4. `stat_arbitrage_lo`
- Sharpe 0.49, return +8.36%, max DD -4.50%, **10 trades** over 31783 bars
- RiskManager note: trade count too low for statistical significance

#### 5. `cross_sectional_momentum_lo`
- Sharpe 0.40, return +2.84%, max DD **-67.41%**, 145 trades
- RiskManager note: excessive drawdown — reject until DD control improved

#### 6. `cross_sectional_momentum_ls`
- Sharpe 0.44, return +7.42%, max DD -48.16%, 173 trades
- RiskManager note: high DD but viable in LS config — monitor under tight DD cap

### Tier C — Pending evidence

Strategies 7–13 have no dedicated WFO or cross-asset evidence yet.
Need to run backtests before sign-off.

### Tier D — Rejected (NO_TRADE)

| Strategy | Sharpe | Return | Gates | Reason |
|----------|-------:|-------:|-------|--------|
| `ma_adx` | 0.058 | +0.03% | 6/13 | No statistical edge |
| `rsi` | -2.36 | -1.18% | 3/13 | Negative Sharpe |
| `bbands` | -2.27 | -1.43% | 2/13 | Negative Sharpe |

---

## Approval Chain (5 roles required)

| Role | Sign-off | Date | Notes |
|------|----------|------|-------|
| 1. Research Lead | ⬜ | — | Verify provenance + evidence sufficiency |
| 2. RiskManager | ⬜ | — | Validate risk metrics + DD caps |
| 3. Compliance | ⬜ | — | Regulatory suitability check |
| 4. Execution Lead | ⬜ | — | Execution quality + slippage tolerance |
| 5. Product Owner | ⬜ | — | Final go/no-go |

> **TOURNAMENT_SHADOW_MODE=0** (live promotion/demotion) activates only after all 5 signatures.
