# R01: Adapter LegacyDataFrameAdapter + CanonicalRegistry Parity - COMPLETE

## Executive Summary
**Status: PASS** ✅

Both verification checks pass with 100% parity between the canonical registry adapter path and the legacy strategy path.

## Verification Results

### 1. Signal Generation Parity (r01_signal_parity.py)
- **Test**: Compare signals from `build_parameterized_adapter()` (canonical registry) vs `build_legacy_candidate()` (FullSystemSimulator path)
- **Data**: 1000 synthetic bars (seed=7), enhanced_ma strategy with golden S0 parameters
- **Result**: **100% match** - 1000/1000 bars identical
- **Signal distribution**: 987 zeros, 3 buys (1), 10 sells (-1) - identical on both paths

### 2. Equity Curve Parity (r01_equity_parity.py)
- **Test**: Run FullSystemSimulator with (a) canonical signals injected vs (b) legacy strategy generating signals internally
- **Data**: Same 1000 synthetic bars, same parameters
- **Result**: **100% match** - All metrics identical to 4 decimal places
  - Final equity: $10,303.96358519897 (both)
  - Total return: 3.0396% (both)
  - Sharpe: 7.6753 (both)
  - Max drawdown: 0.6918% (both)
  - Total trades: 2 (both)
  - Win rate: 50.0% (both)
  - Profit factor: 0.4770 (both)
- **Equity curve**: Point-by-point identical ($0.00 max difference)

## Technical Details

### Golden S0 Parameters Verified
```python
{
    "fast_period": 15,
    "slow_period": 50,
    "adx_threshold": 40.0,
    "atr_sl_mult": 2.0,
    "atr_tp_mult": 3.0,
    "target_exposure_pct": 0.25,
}
```

### Paths Verified Equivalent
| Component | Canonical Registry Path | Legacy Strategy Path |
|-----------|------------------------|---------------------|
| Strategy descriptor | `build_default_registry().describe("enhanced_ma")` | `build_legacy_candidate("enhanced_ma", params)` |
| Signal generation | `LegacyDataFrameAdapter._strategy.generate_signals()` | `LegacyDataFrameAdapter._strategy.generate_signals()` |
| Execution | `FullSystemSimulator(signal_series=canonical_signals)` | `FullSystemSimulator()` (generates internally) |
| Risk/Authority chain | Identical (injected signals go through same engine) | Identical |

### Key Insight
The `LegacyDataFrameAdapter` in `src/trading_agent/strategies/canonical/adapter.py` wraps the exact same legacy `Strategy` instance that `build_legacy_candidate()` creates. Both paths ultimately call:
```python
legacy_strategy.compute_indicators(df)
legacy_strategy.generate_signals(df_with_indicators)
```

The canonical registry path just adds a thin `ForecastStrategy` wrapper for the research interface, but the underlying signal generation is byte-identical.

## Reports Generated
- `data/backtests/r01_parity/r01_signal_parity_report.json` - Signal comparison details
- `data/backtests/r01_parity/r01_equity_parity_report.json` - Full equity/metrics comparison

## Conclusion
**R01 COMPLETE**: The LegacyDataFrameAdapter + CanonicalRegistry produces identical trading decisions (signals) and identical financial outcomes (equity curves, metrics) as the legacy strategy path used in the golden S0 fixture. The adapter is a true zero-overhead wrapper with no behavioral deviation.

## Next Steps
Proceed to R02 (if applicable) or continue with the handover plan.