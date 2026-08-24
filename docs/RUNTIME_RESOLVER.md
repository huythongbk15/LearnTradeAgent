# RuntimeStrategyResolver

## API Reference

### Class: RuntimeStrategyResolver

**Location**: `src/trading_agent/authority/resolver.py`

The `RuntimeStrategyResolver` bridges promoted strategy artifacts with executable Strategy
instances at runtime.

---

### Constructor

```python
def __init__(self, config: AuthorityConfig)
```

**Parameters:**
- `config`: AuthorityConfig — Contains environment settings, exposure limits, supported symbols

**Returns:** `RuntimeStrategyResolver` instance

---

### Methods

#### `resolve(promoted: PromotedStrategy) -> Strategy | None`

Primary entry point for converting a promoted artifact into an executable Strategy.

**Args:**
- `promoted`: PromotedStrategy instance (contains artifact + manifest)

**Returns:**
- `Strategy` instance on success
- `None` on failure (check `last_outcome` attribute for reason)

**Example:**
```python
resolver = RuntimeStrategyResolver(config)
strategy = resolver.resolve(promoted_strategy)
if strategy:
    engine.set_strategy(strategy)
```

---

#### `get_strategy_class(strategy_type: StrategyType) -> type[Strategy]`

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

| Strategy Name | StrategyType | Class | Module |
|--------------|--------------|-------|--------|
| ma_crossover | MA_CROSSOVER | MaCrossover | strategies/ma_crossover.py |
| rsi | RSI | RsiStrategy | strategies/rsi.py |
| bbands | BBANDS | BBandsStrategy | strategies/bbands.py |
| adaptive | ADAPTIVE_MA | AdaptiveStrategy | online_learning/adaptive_strategy.py |
| regime | REGIME_SWITCH | RegimeSwitchStrategy | online_learning/regime_switch.py |
| multi | MULTI_REGIME | MultiRegimeStrategy | online_learning/regime_switch.py |

---

## Error Handling

The resolver follows fail-closed semantics:

| Error Condition | `ResolutionOutcome` |
|----------------|---------------------|
| Strategy name not in mapping | `STRATEGY_NAME_NOT_SUPPORTED` |
| Artifact not in store | `ARTIFACT_NOT_FOUND` |
| Parameter hash mismatch | `PARAMETER_DRIFT` |
| Symbol not in allowed list | `SYMBOL_NOT_ALLOWED` |
| Timeframe not supported | `TIMEFRAME_NOT_SUPPORTED` |
| Exception during instantiation | `INSTANTIATION_FAILED` |

---

## Environment Binding

The resolver applies environment-specific constraints:

```python
def _apply_env_constraints(
    params: dict, promoted: PromotedStrategy, env: Environment
) -> dict:
    # Symbol restrictions
    if "symbol" in params:
        validate_symbol(params["symbol"], env)

    # Timeframe restrictions
    if "timeframe" in params:
        validate_timeframe(params["timeframe"], env)

    # Risk cap adjustments
    if "target_exposure_pct" in params:
        params["target_exposure_pct"] = min(
            params["target_exposure_pct"], config.exposure.max_single_strategy_exposure
        )

    return params
```

---

## Drift Detection

Parameter drift occurs when live parameters differ from artifact parameter_hash.

**Detection mechanism:**
```python
def _has_param_drift(self, expected_hash: str, artifact_id: str) -> bool:
    current_hash = compute_parameter_hash(artifact)
    return current_hash != expected_hash
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
3. **Environment Tests**: Symbols/timeframes validated per environment
4. **Edge Cases**: Missing/malformed artifacts, unsupported strategies