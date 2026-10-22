# Regime-Switched Strategy Design — Proposal

## Problem Statement

Từ các experiments (tổng hợp trong STRATEGY_EVALUATION_REPORT.md):
- **RSI**: +Sharpe trong sideway (2000 bars: Sharpe 3.15), **-Sharpe trong trending** (5000 bars: -0.12)
- **ma_adx**: +PF trong trending (5000 bars: PF 1.46), **ít trades** (24 trades, below 30 gate)
- **regime_switching**: broken (no exit logic, 0 trades)

**Root cause**: Mean-reversion strategies (RSI, BBands) thua lỗ khi có trending regime breakout. Trend-following strategies (ma_adx) work trong trending nhưng signal density thấp.

## Proposed Solution: ADX-Filtered Regime Switching

Kết hợp RSI (range) + ma_adx (trend) thông qua ADX regime detector.

### Regime Logic

```
ADX(14) < 20  → RANGE regime    → Activate RSI p14 (os=30, ob=70)
ADX(14) > 35  → TREND regime    → Activate ma_adx (fast=20, slow=80, adx_threshold=30)
ADX(14) 20-35 → TRANSITION zone → Cash/flat (neither strategy)
```

### Rationale

| Regime | ADX range | Why this strategy | Evidence |
|--------|-----------|-------------------|----------|
| Range | < 20 | Mean-reversion works when price oscillates | RSI 2000 bars: Sharpe 3.15, PF 3.29 |
| Transition | 20-35 | High uncertainty, both strategies risky | Mixed signals — cash is safest |
| Trend | > 35 | Trend-following captures momentum | ma_adx 5000 bars: Sharpe 0.83, PF 1.46 |

### Expected Performance

| Metric | Target | Rationale |
|--------|--------|-----------|
| Trades | 50-70/2000 bars | RSI(30) + ma_adx(24) combined |
| Sharpe | 1.5-2.5 | RSI high in sideway, ma_adx stable in trend |
| PF | 2.0-3.0 | Both strategies PF > 1.4 in their regimes |
| Regime robustness | ✅ Pass all | Switches to appropriate strategy per regime |

### Cost Management

| Cost level | Strategy combination | Expected PF |
|------------|---------------------|-------------|
| 1x (baseline) | RSI(range) + ma_adx(trend) | 2.5-3.5 |
| 2x (realistic live) | Same combo | 1.7-2.2 |
| 3x (stress) | Same combo | 1.0-1.3 |

### Implementation Parameters

```python
# Regime filter
ADX period: 14
Range regime:  ADX < 20
Trend regime:  ADX > 35
Transition zone: ADX 20-35 (flat)

# RSI (range regime)
period=14, oversold=30, overbought=70, risk_per_trade=0.02

# ma_adx (trend regime)
fast_period=20, slow_period=80, adx_period=14, adx_threshold=30
risk_per_trade=0.02

# Shared
max_dd_pct: 0.15 (15%)
cooldown_bars: 24
base_position_pct: 0.20
```

## Validation Plan

### Phase 1: Regime stability test
1. Chạy regime-switched strategy trên 2000 bars (sideway) — expect RSI-dominated
2. Chạy trên 5000 bars (mixed) — expect ma_adx-dominated trades during trend
3. So sánh với RSI-only: nếu regime-switched có Sharpe > 0.5 ở 5000 bars (vs RSI -0.12) → regime filter work

### Phase 2: Cost stress (2x, 3x)
Chạy trên cả 2 windows để chứng minh cost robustness

### Phase 3: WFO validation-only
Chạy full grid WFO validation-only với regime-switched strategy để chứng minh outer OOS stability

## Risk Controls

1. **Position sizing**: Risk 2%/trade, 20% base position — giới hạn drawdown
2. **Cooldown**: 24h giữa trades cùng strategy
3. **DD breaker**: Flat all positions nếu portfolio DD > 15%
4. **Regime lag**: Transition zone 20-35 ADX tránh whipsaw (25% of range)

## Files referenced
- Strategy definitions: `src/trading_agent/strategies/`
- Tournament runner: `scripts/run_strategy_tournament.py`
- WFO runner: `scripts/run_wfo_parallel.py`
- Evidence contract: `docs/operations/CAMPAIGN_EVIDENCE_CONTRACT.md`
