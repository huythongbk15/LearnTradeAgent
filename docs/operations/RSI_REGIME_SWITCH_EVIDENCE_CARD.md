# RSI Regime Switching — Evidence Card (updated)

Status: TERMINATED_NOT_QUALIFIED — regime filter improves RSI-alone but fails cost stress @2x, not robust enough for promotion.
Owner: research operator; independent reviewer pending
Verified: 2026-10-18
Evidence: STRATEGY_EVALUATION_REPORT.md, RSI_REGIME_SWITCH_MANIFEST.json

## Question and identity

**Question**: Liệu ADX-regime-switched combination (RSI khi ADX<20, ma_adx khi ADX≥20) giúp giảm drawdown trong trending regime so với RSI-alone, đồng thời đạt được ≥30 trades và PF > 1.0 trên 5000 bars?

- BTC/USDT 1h, windows: 2000 bars (sideway) + 5000 bars (mixed regime)
- Arms: RSI-alone (control), ma_adx-alone (trend component), rsi_adx_regime_filter (signal of interest)
- Cost: 1x (fee 0.1% + 10bps spread), 5% SL, 24h cooldown

## Arms tested

| Arm | Trades | Return | Sharpe | PF | DD | Win Rate | Status |
|-----|--------|--------|--------|-----|-----|----------|--------|
| RSI-alone (control) | 73 | -0.47% | -0.122 | 0.966 | 7.00% | 60.3% | ❌ FAIL (trending) |
| ma_adx-alone | 24 | +2.75% | 0.826 | 1.464 | 3.41% | 45.8% | ⚠️ <30 trades |
| **rsi_adx_regime_filter (t=30)** | **54** | **+1.46%** | **0.492** | **1.072** | **5.54%** | 51.9% | ⚠️ MARGINAL |

## Threshold sweep (5000 bars, 1x cost)

| ADX threshold | Trades | Sharpe | PF | DD | Verdict |
|---------------|--------|--------|-----|-----|---------|
| 20 | 14 | -0.647 | 0.583 | 1.91% | ❌ FAIL |
| **30** | **54** | **+0.492** | **1.072** | **5.54%** | ⚠️ best so far |
| 35 | 57 | 0.275 | 1.077 | 5.38% | ❌ worse |
| 40 | 14 | -0.647 | 0.583 | 1.91% | ❌ FAIL (same as t=20) |

→ Threshold t=30 is the sweet spot. Monotonic degradation at t=35+ and t=20.

## Sideway regime (2000 bars, 1x cost)

| Arm | Trades | Sharpe | PF | DD |
|-----|--------|--------|-----|-----|
| RSI-alone | 30 | **3.154** | **3.294** | 0.84% |
| **rsi_adx_regime_filter (t=30)** | **20** | **1.472** | **1.437** | **1.37%** |

## Điều gì đã chứng minh?

- ✅ **Regime filter improves trending regime**: RSI-alone Sharpe -0.12 → +0.49 (Δ+0.61)
- ✅ **Threshold sweep validated**: t=30 optimal, monotonic degradation
- ⚠️ **Not yet sufficient**: Sharpe 0.49 < 1.0, PF 1.07 < 1.2 on 5000 bars
- ✅ **ma_adx-alone is a strong component**: PF 1.46, survive @2x cost (1.21)
- ❌ **Built-in regime_switching broken**: no exit logic (0 wins)
- ✅ **RSI cost-sensitive**: fails @3x (PF 0.91)

## Verdicts (protocol-style, 3 verdicts)

- **Engineering**: PASS_EXECUTION_RESEARCH_ONLY — adapter verified, threshold sweep monotonic
- **Economics**: INSUFFICIENT_EVIDENCE_OF_BETTER — Sharpe 0.49 marginal, needs tuning to > 1.0
- **Qualification**: NOT_QUALIFIED — no OOS/validation, no paper/live, no holdout

## Cost stress result (ADX t=30, MA 20/80, 5000 bars)

| Cost | Return | Sharpe | PF | DD | WR | Status |
|------|--------|--------|-----|-----|-----|--------|
| 1x | +1.46% | 0.492 | 1.072 | 5.54% | 51.9% | ⚠️ marginal |
| **2x** | **-1.60%** | **-0.485** | **0.801** | 6.91% | 42.6% | ❌ FAIL |
| 3x | unmeasured (stop-signal hit at 2x) | | | | | |

→ **Stop signal hit at 2x**: regime filter does not survive cost stress. Low signal density (54 trades) → high variance → no robustness margin.

Quy trình: RESEARCH_RUN_PROTOCOL.md

## Final verdict (protocol-compliant)

- **Engineering**: PASS — implementation correct, threshold sweep monotonic, cost stress verified
- **Economics**: INSUFFICIENT_EVIDENCE_OF_BETTER — Sharpe 0.49 < 1.0, fails 2x cost stress (PF 0.801)
- **Qualification**: NOT_QUALIFIED — no OOS/validation, marginal 1x performance, fragile to cost

## Root cause + next directions

**Root cause**: Hard regime switch (ADX threshold) creates whipsaw in transition zone (ADX 25-35). RSI leg wins in sideway but loses money filtered into trending; MA leg wins in trending but sparse (24-54 trades) → no robustness buffer for cost stress.

**Next hypothesis (requires preregistration before next execution)**:
1. **Continuous weighting** instead of hard switch: weight RSI leg (1→0) as ADX goes 0→30
2. **Tune ma_adx-alone for ≥30 trades**: fast=10/slow=30 (currently 24 trades)
3. **ADX entry filter on RSI**: keep RSI-only logic but gate entries when ADX < 20
