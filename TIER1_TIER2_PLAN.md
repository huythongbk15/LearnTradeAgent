# TIER 1 & 2 IMPLEMENTATION PLAN

## TIER 1: CORE ENHANCEMENTS (Priority Order)

### 1. Alternative Data Module - ON-CHAIN METRICS
- [ ] `src/trading_agent/data/onchain.py` - Fetch on-chain data (Glassnode free, Blockchain.info, CoinGecko)
- [ ] Key metrics: MVRV, NUPL, Exchange Net Flow, Active Addresses, Hash Rate, Funding Rates
- [ ] Integration with strategy: Regime filter + signal confirmation
- [ ] Cache layer (24h TTL for on-chain)

### 2. Advanced Portfolio Risk Management
- [ ] `src/trading_agent/risk/portfolio_risk.py` - VaR, CVaR, Expected Shortfall
- [ ] Daily risk budget allocation per strategy/asset
- [ ] Drawdown controls: -10% → 50% lev, -15% → 25% lev, -20% → halt
- [ ] Correlation monitoring with alerts

### 3. Smart Execution Engine
- [ ] `src/trading_agent/execution/smart_router.py` - TWAP, VWAP, Iceberg
- [ ] Multi-venue price comparison (Binance, Bybit, OKX, Deribit)
- [ ] Slippage model from historical data
- [ ] Maker/Taker optimization

---

## TIER 2: RESEARCH & SCALING

### 4. Alpha Research Pipeline (AutoML)
- [ ] Feature store: 100+ technical/microstructure/on-chain features
- [ ] Purged CV + Combinatorial Purged K-Fold (no leakage)
- [ ] Optuna hyperparameter optimization
- [ ] Monthly tournament → paper → live promotion

### 5. Multi-Venue/Asset Expansion
- [ ] Futures/Perps: Basis trading, funding arb, calendar spreads
- [ ] Options: Vol selling, dispersion, gamma scalping
- [ ] DeFi: Delta-neutral LP, staking yield, lending
- [ ] DEX integration (Uniswap V3, Curve)

### 6. Infrastructure Hardening
- [ ] Multi-region deployment (SG primary, US/EU failover)
- [ ] Chaos engineering GameDays
- [ ] Latency optimization (<10ms tick-to-trade)
- [ ] DR testing quarterly

---

## IMMEDIATE NEXT (This Session)

Starting with **Tier 1.1: On-Chain Data Module** - highest alpha potential, lowest effort.