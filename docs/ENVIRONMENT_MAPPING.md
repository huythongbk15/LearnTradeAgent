# Environment Mapping

## Regime-Specific Strategy Switching — Environment Configuration

### Regime Detection & Strategy Assignment

| Market Regime | Strategy | Description | Regime Confidence |
|---------------|----------|-------------|-------------------|
| TRENDING_UP | MA Crossover | Trend-following strategy | 0.6+ |
| TRENDING_DOWN | MA Crossover | Trend-following strategy | 0.6+ |
| SIDEWAYS | RSI | Mean-reversion strategy | 0.6+ |
| VOLATILE | BBANDS | Volatility-adaptive strategy | 0.6+ |
| UNKNOWN | HOLD | Conservative/no-trade | 0.5+ |

### Environment-Specific Configuration

| Environment | Regime Confidence Threshold | Symbol Restrictions | Timeframe Support | Exposure Limits | Drift Behavior |
|-------------|---------------------------|---------------------|-------------------|-----------------|----------------|
| RESEARCH | 0.5 | None | All | Unlimited | Warning only |
| PAPER | 0.6 | Top 50 symbols | 1m, 5m, 15m, 1h, 4h, 1d | 0.95 | Warning only |
| TESTNET | 0.6 | Top 20 symbols | 1h, 4h, 1d | 0.50 | Block if confidence < 0.8 |
| SHADOW | N/A | N/A | All | 0.0 | No execution |
| CANARY | 0.7 | Top 10 symbols | 1d only | 0.10 | Block if confidence < 0.5 |
| PRODUCTION | 0.8 | Top 5 symbols | 1d, 4h | 0.05 | Block + Kill Switch |

### Environment Configuration Details

#### Research Environment
- **Purpose**: Strategy research and development
- **Symbol Restrictions**: None (all symbols supported)
- **Timeframe**: All timeframes supported
- **Risk Controls**: Warning-only regime detection
- **Hot-Reload**: Enabled (strategy updates without restart)

#### Paper Environment
- **Purpose**: Pre-production validation
- **Symbol Restrictions**: Top 50 symbols by trading volume
- **Timeframe**: 1m, 5m, 15m, 1h, 4h, 1d
- **Risk Controls**: Warning-level regime detection
- **Hot-Reload**: Enabled with monitoring

#### Testnet Environment
- **Purpose**: Testnet validation with real market data
- **Symbol Restrictions**: Top 20 symbols by volume
- **Timeframe**: 1h, 4h, 1d
- **Risk Controls**: Regime detection blocks invalid regimes
- **Hot-Reload**: Enabled with drift monitoring

#### Shadow Environment
- **Purpose**: Simulated execution without market impact
- **Symbol Restrictions**: N/A (all symbols)
- **Timeframe**: All timeframes
- **Risk Controls**: No exposure limits
- **Hot-Reload**: Disabled (cold start only)

#### Canary Environment
- **Purpose**: Gradual production rollout
- **Symbol Restrictions**: Top 10 symbols
- **Timeframe**: 1d only
- **Risk Controls**: Reduced exposure caps
- **Drift Handling**: Block regime switching with alerts

#### Production Environment
- **Purpose**: Live trading with real capital
- **Symbol Restrictions**: Top 5 symbols (BTC, ETH, SOL, BNB, XRP)
- **Timeframe**: 1d, 4h
- **Risk Controls**: Full exposure limits, kill switch
- **Hot-Reload**: Manual only (requires restart)

---

### Regime Detection Confidence Levels

| Confidence Level | Meaning | Action |
|----------------|---------|--------|
| < 0.5 | High uncertainty | Strategy remains in current regime |
| 0.5 - 0.6 | Moderate confidence | Switch with caution |
| 0.6 - 0.8 | High confidence | Active regime switching |
| > 0.8 | Very high confidence | Full strategy switching |

---

### Regime Detection Confidence Thresholds

| Environment | Minimum Confidence | Maximum Confidence |
|-------------|---------------------|---------------------|
| RESEARCH | 0.5 | 1.0 |
| PAPER | 0.6 | 1.0 |
| TESTNET | 0.6 | 0.9 |
| SHADOW | 0.5 | 0.6 |
| CANARY | 0.7 | 0.8 |
| PRODUCTION | 0.8 | 0.95 |

---

## Regime Detection Workflow

1. **Feature Extraction**: Compute regime features from OHLCV data
2. **Probability Calculation**: Compute probability for each regime
3. **Confidence Calculation**: Normalize probabilities to sum to 1.0
4. **Regime Selection**: Select regime with highest probability
5. **Confidence Check**: Verify confidence meets minimum threshold
6. **Strategy Switching**: Select appropriate strategy for regime

### Regime Detection Output
```python
{
    "regime": "SIDEWAYS",
    "confidence": 0.58,
    "regime_probs": {
        "TRENDING_UP": 0.12,
        "TRENDING_DOWN": 0.12,
        "SIDEWAYS": 0.58,
        "VOLATILE": 0.18,
        "UNKNOWN": 0.05
    },
    "recommended_strategy": "rsi",
    "recommended_params": {
        "period": 14,
        "oversold": 35,
        "overbought": 65
    }
}
```

---

## Configuration Files

- `config.yaml`: Main configuration file
- `env.override`: Environment-specific overrides
- `PROJECT_MAP.md`: Symbol mapping reference

---

## Configuration Example

```yaml
# config.yaml
environment: paper
risk_profile: moderate
symbols:
  - BTC/USDT
  - ETH/USDT
  - BNB/USDT
  - SOL/USDT
  - ZEC/USDT
timeframe: 1d
```

---

## Configuration Loading

The resolver automatically loads configuration based on environment:

```python
config = AuthorityConfig.for_environment("paper")
resolver = RuntimeStrategyResolver(config)
```

Environment-specific defaults are set in `AuthorityConfig.for_environment()`.

---

## Configuration Loading Flow

1. Check for `config.yaml` in project root
2. Load environment-specific YAML file
3. Apply environment variable overrides (prefix: `TA_`)
4. Apply defaults for missing fields
5. Validate configuration
5. Return `AuthorityConfig` instance