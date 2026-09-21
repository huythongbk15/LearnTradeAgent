# E2E SYSTEM TEST — Advanced Trading Evaluation & Order Optimization

## Pivot: Runtime optimization → Trade result quality + optimal execution

### Core Metrics (what matters):
1. **Shadow Sharpe per strategy** (300+ bars, historical)
2. **Fill rate + slippage** (simulated execution)
3. **Order type selection** (market vs limit vs TWAP vs VWAP)
4. **Position sizing effectiveness** (confidence_adjustment impact)
5. **Portfolio-level risk-adjusted returns** (cross-asset)

---

## Tier 1: Shadow P&L Analysis (strategy quality ranking)

### T1A: Strategy Sharpe ranking (300 bars)
- **Setup**: Run tournament on 300 historical BTC/USDT bars (shadow mode)
- **Metric**: Per-strategy shadow Sharpe, Sortino, max-DD
- **Expected**: enhanced_ma Sharpe > rsi > bbands
- **Assert**: `shadow_sharpe[enhanced_ma] > 1.0; shadow_sharpe[rsi] > 0.5`

### T1B: Strategy hit rate analysis
- **Setup**: Same 300 bars
- **Metric**: For each strategy, (bars with signal != 0) / (bars with return in same direction as signal)
- **Expected**: hit_rate > 0.45 (coin-flip threshold + alpha)
- **Assert**: `mean(hit_rate[strategy]) >= 0.45 for all strategies with > 20 signals`

### T1C: Strategy correlation matrix
- **Setup**: 300 bars shadow returns for all 7 pool strategies
- **Metric**: Pearson correlation between strategy shadow returns
- **Expected**: Enhanced_ma & trend_pullback correlated (>0.6); rsi & range_mean_reversion correlated (>0.5)
- **Assert**: `corr[enhanced_ma][trend_pullback] > 0.5; corr[bbands][ma_adx_regime] > 0.3`

---

## Tier 2: Execution Quality (slippage & fill rate)

### T2A: Market order slippage simulation
- **Setup**: 200 bars with simulated bid/ask spread = 0.1%
- **Metric**: `slippage = execution_price - mid_price` for each shadow signal
- **Expected**: Mean slippage ≤ 0.15% (half-spread + 50bps buffer)
- **Assert**: `mean(abs(slippage)) <= 0.0015; fill_rate == 1.0`

### T2B: Limit order fill rate by volatility regime
- **Setup**: Stratify 300 bars into high-vol (std > 75th pct) vs low-vol
- **Metric**: Fill rate when placing limit at mid ± 0.2%
- **Expected**: High-vol fill rate < 50%; low-vol fill rate > 70%
- **Assert**: `fill_rate[high_vol] < 0.5; fill_rate[low_vol] > 0.7`

### T2C: Slippage × volatility interaction
- **Setup**: Bin 300 bars by realized volatility into 5 quintiles
- **Metric**: Mean slippage per quintile
- **Expected**: Slippage scales with volatility (0.05% → 0.5%)
- **Assert**: `slippage[q5] > 3 * slippage[q1]` (monotonic increase)

---

## Tier 3: Order Type Selection Optimization

### T3A: Market vs Limit decision matrix
- **Setup**: 300 bars; evaluate hypothetical execution under 3 order types:
  - Market: immediate fill at spread midpoint ± 0.05%
  - Limit (mid+0.1%): fill rate ~90% in quiet, ~40% in volatile
  - TWAP (5-bar): minimal slippage, but delayed execution
- **Metric**: Expected fill cost = expected_slippage × fill_rate
- **Expected**: Limit optimal in low-vol regimes; market optimal in crisis
- **Assert**: `optimal_order_type[vol_quartile_1] == "limit"; optimal[quartile_5] == "market"`

### T3B: TWAP vs VWAP in trending market
- **Setup**: 100 bars of strong trend (20-period return > 5%)
- **Metric**: Execution price difference between TWAP and VWAP
- **Expected**: VWAP outperforms TWAP by ~0.2% in trend (follows volume flow)
- **Assert**: `mean(vwap_price - twap_price) > 0 in uptrend; < 0 in downtrend`

### T3B-variant: Iceberg order effectiveness
- **Setup**: Large order (5% portfolio equity) in illiquid window (volume < 25th pct)
- **Metric**: Market impact reduction with iceberg (20% visible) vs full size
- **Expected**: Iceberg reduces impact by ~60%
- **Assert**: `market_impact[iceberg] < 0.4 * market_impact[full]`

---

## Tier 4: Confidence Adjustment Impact

### T4A: Confidence scaling effectiveness (LLM ON vs OFF)
- **Setup**: 300 bars; compare shadow P&L with confidence=1.0 (LLM OFF)
  vs confidence from actual LLM enricher (historical replay)
- **Metric**: Portfolio Sharpe, max-DD, win rate
- **Expected**: LLM-adjusted Sharpe > baseline (confidence filtering removes bad trades)
- **Assert**: `sharpe[llm_on] > sharpe[llm_off]; max_dd[llm_on] < max_dd[llm_off]`

### T4B: Anomaly flag backtesting
- **Setup**: Identify bars where LLM enrichment flagged anomalies
  (cvd_price_divergence, funding_extreme, order_book_imbalance)
- **Metric**: Sharpe on flagged vs non-flagged bars
- **Expected**: Flagged bars show lower Sharpe for deterministic strategy
- **Assert**: `sharpe[flagged] < 0.5 * sharpe[non_flagged]`

### T4B-variant: Confidence clamp boundary stress
- **Setup**: Confidence clamped at [0.5, 1.5]; test edge values 0.49→0.50 and 1.51→1.50
- **Metric**: Position size at clamp vs unconstrained
- **Expected**: No position exceeds 1.5× baseline
- **Assert**: `position_size[max_confidence] == 1.5 * baseline; min == 0.5 * baseline`

---

## Tier 5: Portfolio-Level Optimization

### T5A: Cross-asset diversification benefit
- **Setup**: BTC + ETH + SOL (300 bars), independent routing per symbol
- **Metric**: Portfolio Sharpe vs individual asset Sharpe
- **Expected**: Portfolio Sharpe > mean(individual Sharpe) by ≥0.3
- **Assert**: `portfolio_sharpe > mean(asset_sharpes) + 0.3`

### T5A-variant: Correlation-adjusted position sizing
- **Setup**: When BTC & ETH correlation > 0.8, reduce same-direction exposure by 20%
- **Metric**: Portfolio volatility reduction
- **Expected**: Volatility ↓ 15% vs naive equal-weight
- **Assert**: `vol[corr_adj] < 0.85 * vol[naive]`

### T5B: Kill switch recovery quality
- **Setup**: 100 bars normal → kill switch triggers (Sharpe < -0.5) → 100 bars recovery
- **Metric**: Time to positive Sharpe after kill switch
- **Expected**: < 30 bars to recovery
- **Assert**: `bars_to_recovery <= 30; no positions during kill period`

---

## Tier 6: Walk-Forward Analysis

### T6A: Out-of-sample regime shift
- **Setup**: Train on 2020-2021 (bull regime); test on 2022 (bear regime)
- **Metric**: Strategy ranking stability (top-2 strategies persist)
- **Expected**: enhanced_ma and ma_adx_regime remain in top 3
- **Assert**: `top_3[train] ∩ top_3[test] >= 2`

### T6A-variant: Adaptive strategy retirement
- **Setup**: Monitor 300 bars; retire any strategy with Sharpe < 0 for 50 consecutive bars
- **Expected**: funding_carry auto-retired (as per STRATEGY_SIGNOFF_P2PHASE4.md)
- **Assert**: `strategies_retired >= 1; retired_strategy in (funding_carry, regime_switching)`

---

## Optimization Priority (trade-result focused)

### 🔥 O-TRADE-1: Confidence adjustment backtesting (2 days)
- **Goal**: Quantify LLM enrichment value on historical 300 bars
- **Method**: Replay with `confidence_adjustment=1.0` vs actual stored MarketContexts
- **Success**: LLM-adjusted Sharpe > baseline by ≥0.2

### ⚡ O-TRADE-2: Order type optimization (1.5 days)
- **Goal**: Build decision tree: vol_regime × liquidity × trend → optimal order type
- **Method**: Backtest market/limit/TWAP/VWAP/iceberg across 500 bars
- **Success**: Average execution cost reduced by ≥25% vs fixed market orders

### 📊 O-TRADE-3: Cross-asset position sizing (2 days)
- **Goal**: Correlation-aware position sizing (reduce when corr > threshold)
- **Method**: Backtest with/without correlation adjustment
- **Success**: Portfolio Sharpe ↑, max-DD ↓ 10–15%

### 🛡️ O-TRADE-4: Kill switch + recovery strategy (1 day)
- **Goal**: Define recovery protocol after kill switch triggers
- **Method**: Simulate crash → kill → recovery path
- **Success**: Recovery < 30 bars, no whipsaw re-entry

### 🎯 O-TRADE-5: Shadow Sharpe strategy ranking (1 day)
- **Goal**: Validate tournament strategy pool ranking on 1000 historical bars
- **Method**: Run full shadow simulation, rank by Sharpe/Sortino/max-DD
- **Success**: Enhanced_ma ≥ 1.0 Sharpe, consistent with P4 production state (Sharpe 4.93)
