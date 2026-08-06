# TIER 1 & 2 IMPLEMENTATION PLAN

## TIER 1: CORE ENHANCEMENTS (Priority Order)

### 1. Alternative Data Module - ON-CHAIN METRICS ✅
- [x] `src/trading_agent/data/onchain.py` - Fetch on-chain data (CoinGecko, Binance Futures)
- [x] Key metrics: Funding rates, Open Interest, CVD, Market cap/turnover (CoinGecko)
- [x] Integration with strategy: Risk-off score computation
- [x] Cache layer (1h TTL on-chain, 10min funding)

### 2. Advanced Portfolio Risk Management ✅
- [x] `src/trading_agent/risk/portfolio_risk.py` - VaR, CVaR (Historical + Parametric)
- [x] Daily risk budget allocation per strategy/asset (Euler CVaR decomposition)
- [x] Drawdown controls: -5%→75%, -10%→50%, -15%→25%, -20%→halt
- [x] Correlation monitoring with breach alerts (>0.8 threshold)

### 3. Smart Execution Engine ✅
- [x] `src/trading_agent/execution/smart_router.py` - TWAP, VWAP, Iceberg, Adaptive
- [x] Slippage model (sqrt impact: k * sqrt(qty/ADV) * σ)
- [x] Calibration from historical fills
- [x] Participation rate limits

---

## TIER 2: RESEARCH & SCALING

### 4. Alpha Research Pipeline (AutoML) ✅
- [x] Feature store: parquet-backed with versioning
- [x] Alpha library: 40+ factors (momentum, volatility, microstructure, volume, mean-reversion)
- [x] Alpha evaluator: IC, ICIR, turnover, decay, correlation analysis
- [x] AutoML: combinatorial search over alpha combos + Optuna optimization
- [x] Monthly tournament framework (paper → live promotion ready)

### 5. Multi-Venue/Asset Expansion 🔄 PARTIAL
- [x] Futures/Perps: Funding rate & OI data (Binance)
- [x] Options provider: `src/trading_agent/data/options_provider.py` (Deribit)
- [ ] Options strategies: Vol selling, dispersion, gamma scalping
- [ ] DeFi: Delta-neutral LP, staking yield, lending
- [ ] DEX integration (Uniswap V3, Curve)

### 6. Infrastructure Hardening 🔄 PARTIAL
- [x] Chaos engineering framework: `src/trading_agent/infrastructure/chaos/`
- [ ] Multi-region deployment (SG primary, US/EU failover)
- [ ] Latency optimization (<10ms tick-to-trade)
- [ ] DR testing quarterly

---

## TIER 3: STRATEGY EVOLUTION (Future)

### 7. Ensemble / Regime-Switching
- [ ] Meta-learning: online strategy selection (Exp3, UCB)
- [ ] Regime detection: HMM / K-means on volatility+trend
- [ ] Dynamic allocation: trend-following ↔ mean-reversion

### 8. Advanced Sizing
- [ ] Kelly-optimal sizing (with drawdown constraint)
- [ ] CPPI / TIPP for capital protection
- [ ] Risk parity across strategy sleeves

### 9. Live Trading Readiness
- [ ] Order management: partial fills, reject handling, reconciliation
- [ ] Real-time P&L + Greeks monitoring
- [ ] Automated failover + alerting