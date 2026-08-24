# Regime-Specific Strategy Switching — Summary

## Completed Tasks

### 1. Regime-Specific Strategy Switching (Mean-Reversion for SIDEWAYS)

**New Module:** `src/trading_agent/online_learning/regime_switch.py`

#### Strategies Implemented:

| Strategy | Description | Regime Mapping |
|----------|-------------|----------------|
| `RegimeSwitchStrategy` | Hard switch between strategies based on detected regime | TRENDING → MA Crossover (trend-following)<br>SIDEWAYS → RSI (mean-reversion)<br>VOLATILE → BBands (volatility-adaptive)<br>UNKNOWN → HOLD |
| `MultiRegimeStrategy` | Soft blend: weights multiple strategies by regime probability | Weights strategies by P(regime) |

#### Key Features:
- **Fail-closed**: If regime confidence < threshold (default 0.6), stays in previous strategy
- **Hot-reloadable**: Regime detector updates every N bars (default 20)
- **Configurable**: Custom strategy maps and params per regime
- **Auditable**: Regime and active strategy recorded in signal metadata

#### Exports added to `online_learning/__init__.py`:
```python
RegimeSwitchStrategy
MultiRegimeStrategy
RegimeSwitchSignal
create_regime_switch_strategy
create_multi_regime_strategy
REGIME_STRATEGY_MAP
REGIME_STRATEGY_PARAMS
```

### 2. Simulator Stress Tests — Full PAPER_ELIGIBLE Set

**Tested 7 pairs across 1d and 4h timeframes:**

| Symbol | TF | Standard Sharpe/Ret% | Simulator Sharpe/Ret% | Trades | Fees | Slippage |
|--------|-----|---------------------|----------------------|--------|------|----------|
| BNB/USDT | 1d | 1.83 / 29.1% | 0.01 / 28.2% | 55 | $7,156 | $23,442 |
| ZEC/USDT | 1d | 5.54 / 128.5% | 0.03 / 1,223.4% | 29 | $8,614 | $66,427 |
| DOGE/USDT | 1d | 4.43 / 253.3% | 0.02 / 85.8% | 52 | $9,572 | $35,713 |
| TRX/USDT | 1d | 2.65 / 13.4% | 0.01 / 89.7% | 54 | $7,810 | $34,084 |
| ZEC/USDT | 4h | 1.63 / 21.3% | 0.01 / -2.4% | 98 | $7,206 | $79,408 |
| DOGE/USDT | 4h | 0.74 / 18.7% | -0.01 / -82.6% | 325 | $14,293 | $106,109 |
| NEAR/USDT | 1d | 1.20 / 4.5% | 0.01 / 27.1% | 15 | $1,737 | $8,188 |

**Key Insight:** High-frequency strategies (4h, 300+ trades) are destroyed by fees/slippage — critical for mainnet gating.

**RegimeSwitchStrategy on Major Pairs (1d):**
| Symbol | Standard | Simulator |
|--------|----------|-----------|
| BTC/USDT | 3.24 / 75% | 0.02 / 64% |
| ETH/USDT | 2.47 / 54% | 0.01 / 45% |
| BNB/USDT | 2.25 / 38% | 0.01 / 25% |
| SOL/USDT | 1.11 / 4% | 0.01 / -10% |

### 3. Simulator Calibration Against V2 Testnet Fills

**Calibrated defaults from `data/simulated_execution_report.json` (Execution Simulator V2):**

| Parameter | Old Default | Calibrated | V2 Reference |
|-----------|-------------|------------|--------------|
| `impact_coefficient` | 0.1 | **0.02** | 1.0 (different depth model) |
| `base_latency_ms` | 50 | **20** | 20 ms |
| `latency_jitter_ms` | 20 | **10** | — |
| `maker_fee_bps` | 1 | **2** | 2 bps (Binance spot) |
| `taker_fee_bps` | 5 | **5** | 5 bps |
| `base_slippage_bps` | 2 | **5** | 4.4 bps |
| `partial_fill_prob` | 0.1 | **0.0** | 0% |

**Critical Fixes:**
1. **Timeframe-aware impact**: Added `bar_duration_hours` to `OrderBookSnapshot`; impact now uses volume over bar duration (not hourly)
2. **Base currency ADV**: Fixed `_compute_impact` to convert `volume_24h` (quote) → base currency before participation calc
3. **OrderBookSnapshot extended**: Added `bar_duration_hours: float = 1.0`

### 4. Test Suite Status

```
990 passed, 10 skipped
```
All execution, backtest, regime detection, and simulator tests pass.

---

## Regime Detection Performance

On BTC/USDT 1d (1324 bars):
- **TRENDING_UP**: 11.8%
- **TRENDING_DOWN**: 11.8%  
- **SIDEWAYS**: 58.8% ← Dominant regime
- **VOLATILE**: 11.8%
- **UNKNOWN**: 5.9%

**Result:** RegimeSwitchStrategy correctly uses RSI (mean-reversion) for majority SIDEWAYS regime, avoiding the "0 trades in SIDEWAYS" failure of trend-following strategies.

---

## Next Steps

1. **Run regime switch + simulator combined** on all PAPER_ELIGIBLE pairs
2. **Portfolio-level stress test**: Equal-risk allocation with regime-switch strategies
3. **Calibrate simulator further** with real testnet fills (when available)
4. **Milestone B**: Wire Authority Foundation into live execution loop