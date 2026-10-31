# RuntimeStrategyResolver

> Status: CURRENT · Owner: Trading systems · Verified: 2026-10-31 against `src/trading_agent/authority/resolver.py`, except the drift scenarios list · Evidence: `tests/authority/test_resolver.py`
>
> Nothing is promoted, so the Binance runner locks entries and does not use this resolver today. See [System Overview](SYSTEM_OVERVIEW.md).

## API Reference

### Class: RuntimeStrategyResolver

**Location**: `src/trading_agent/authority/resolver.py`

The `RuntimeStrategyResolver` bridges promoted strategy artifacts with executable Strategy
instances at runtime.

---

### Constructor

```python
def __init__(self, config: AuthorityConfig, promotion_store: PromotionStateStore | None = None, artifact_store: Any | None = None)
```

Without `promotion_store`, resolution returns `None`. `resolve_for` also returns `None` without `artifact_store`; `resolve` then skips the integrity check with a warning (test mode).

### Methods

#### `resolve_for(symbol: str, timeframe: str, environment: Environment) -> StrategyRuntime | None`

Primary entry point. Reads the promotion store on every call, filters eligible artifacts by (symbol, timeframe) and takes the most recently updated one.

#### `resolve(promoted: PromotedStrategy, symbol: str, timeframe: str, environment: Environment) -> StrategyRuntime | None`

Resolves one given artifact. There is no `last_outcome` attribute: the reason for a `None` appears only in the log.

#### `list_bindings(environment: Environment | str) -> list[tuple[str, str]]`

Sorted (symbol, timeframe) pairs with at least one eligible artifact.

**Example:**
```python
resolver = RuntimeStrategyResolver(config, promotion_store=store, artifact_store=artifacts)
runtime = resolver.resolve_for("BTC/USDT", "1h", Environment.TESTNET)
if runtime is None:
    return  # fail closed; the reason is in the log
output = runtime.execute(market_data, portfolio_state)
```

---

#### `get_strategy_class(strategy_type: StrategyType) -> type[Strategy] | None`

Get strategy class by type enum.

**Args:**
- `strategy_type`: StrategyType enum value

**Returns:** Strategy class (not instance)

---

#### `get_strategy_class_by_name(strategy_name: str) -> type[Strategy] | None`

Get strategy class by string name.

**Args:**
- `strategy_name`: String name (e.g., "ma_crossover", "rsi")

**Returns:** Strategy class or None if not found

---

## Supported Strategies

| Strategy name | StrategyType | Class | Module | Resolves |
|---|---|---|---|---|
| ma_crossover | MA_CROSSOVER | MaCrossover | strategies/ma_crossover.py | yes |
| enhanced_ma | ENHANCED_MA | EnhancedMaCrossover | strategies/enhanced_ma.py | yes |
| ma_adx | MA_ADX | MaAdxCrossover | strategies/enhanced_ma.py | yes |
| ma_vol_target | MA_VOL_TARGET | MaVolTargetCrossover | strategies/enhanced_ma.py | yes |
| rsi | RSI | RsiStrategy | strategies/rsi.py | yes |
| bbands | BBANDS | BBandsStrategy | strategies/bbands.py | yes |
| regime_switching | REGIME_SWITCHING | RegimeSwitchingStrategy | strategies/regime_switching.py | yes |
| online_learning | ONLINE_LEARNING | OnlineLearningStrategy | strategies/online_learning_strategy.py | no: the constructor takes `config`, not `params`, so instantiation fails |

Any other name returns `None`. That covers 13 of the 20 canonical strategies, including `volatility_breakout`, `trend_pullback`, `liquidity_reversal`, `funding_carry` and `rsi_adx_regime_filter`. Research runs them through the canonical bridge, which the resolver does not use.

---

## Error Handling

The resolver returns `None` and logs the reason. There is no `ResolutionOutcome` enum.

| Condition | Result |
|---|---|
| No promotion store, no promotion record, or stage not compatible with the environment | `None` |
| Strategy name not in `_STRATEGY_MAP` | `None` (`Unknown strategy name`) |
| Artifact integrity check fails | `None`; the check is skipped with a warning when `resolve` runs without an artifact store |
| Parameter hash mismatch | `None` (`Parameter drift detected`) |
| Instantiation fails | `None` |
| `symbol` in params, not in `config.symbols`, environment not research | raises `ValueError` |
| `timeframe` in params, not in 1d, 4h, 1h, 15m, 5m, 1m | raises `ValueError` |

---

## Environment Binding

`_apply_env_constraints(params, promoted)` validates only. It checks `symbol` and `timeframe` when those keys are in the parameters. It applies no exposure cap and changes no parameter. Exposure limits are applied later by the authority chain.

---

## Drift Detection

Parameter drift occurs when the manifest parameters no longer hash to `artifact.parameter_hash`.

**Detection mechanism:**
```python
def _has_param_drift(self, promoted: PromotedStrategy) -> bool:
    manifest_hash = sha256_hex(canonical_params(promoted.manifest.parameters or {}))
    return manifest_hash != promoted.artifact.parameter_hash  # a hashing error also counts as drift
```

**Drift scenarios:**
- Manual parameter edits outside the promotion pipeline
- Environment variable overrides after promotion
- Race condition during hot-reload

---

## Testing

Run the test suite:
```bash
pytest tests/authority/test_resolver.py -v
```

### Test Categories

1. **Resolution Tests**: Valid artifacts resolve to strategies
2. **Drift Tests**: Tampered artifacts fail resolution
3. **Environment Tests**: Research symbol exemption and the supported timeframe set
4. **Edge Cases**: Missing/malformed artifacts, unsupported strategies