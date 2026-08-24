# Authoritative Promotion Binding

## Overview

Promotion binding connects the **Research** pipeline to **Runtime** execution through
content-addressed strategy artifacts. This document describes the binding mechanism
that ensures only validated, promoted strategies reach the execution engine.

## Architecture

```
┌─────────────────────┐
│   Research Pipeline │
│  (Training + Eval)  │
└──────────┬──────────┘
           │
           │ 1. Strategy trained & evaluated
           │ 2. Metrics computed (Sharpe, DSR, PBO, etc.)
           │ 3. Promotion criteria validated
           ▼
┌─────────────────────┐
│  Promotion Engine   │
│  └─► StrategyArtifact
└──────────┬──────────┘
           │
           │ 4. Artifact stored (code_sha + param_hash)
           │ 5. Manifest generated for operator visibility
           ▼
┌─────────────────────┐
│  Promotion Binding  │ ◄── THIS LAYER
│  RuntimeStrategyResol│
└──────────┬──────────┘
           │
           │ 6. Resolve → Strategy instance
           │ 7. Verify integrity & drift
           │ 8. Apply environment constraints
           ▼
┌─────────────────────┐
│  Execution Engine   │
│  + Authority Chain  │
└─────────────────────┘
```

## Key Components

### StrategyArtifact
Content-addressed artifact containing:
- `code_sha`: Hash of strategy code
- `parameter_hash`: Hash of canonical parameters
- `metadata`: Parameters + execution_model_version
- `artifact_id`: Deterministic ID = hash(code_sha, parameter_hash)

### PromotedStrategyManifest
Operator-visible record of promotion:
- Human-readable strategy name
- Promotion stage (testnet/shadow/canary/production)
- Parameters (for audit)
- Metadata (actor, timestamp, metrics)

### RuntimeStrategyResolver
Bridge component that:
1. Maps strategy names to concrete classes
2. Instantiates Strategy with artifact parameters
3. Verifies parameter hash integrity (drift detection)
4. Applies environment constraints (exposure caps, allowed symbols)

## Environment Mapping

| Environment | Symbol Restriction | Timeframe Restriction | Drift Handling | Reload Behavior |
|-------------|-------------------|----------------------|----------------|-----------------|
| RESEARCH | Unlimited | Unlimited | Warning | Hot Reload |
| PAPER | Top 50 symbols | 1d, 4h, 1h | Warning | Hot Reload |
| TESTNET | Top 20 symbols | 1d, 4h | Block | Hot Reload |
| SHADOW | All disabled | All disabled | N/A | Cold Start |
| CANARY | Top 10 symbols | 1d only | Block + Alert | Hot Reload |
| PRODUCTION | Top 5 symbols | 1d, 4h | Block + Kill Switch | Manual Reload |

## Fail-Closed Behavior

If any check fails, the binding returns `None`:
- **Artifact not found**: `ResolutionOutcome.ARTIFACT_NOT_FOUND`
- **Parameter drift**: `ResolutionOutcome.PARAMETER_DRIFT`
- **Symbol not allowed**: `ResolutionOutcome.SYMBOL_NOT_ALLOWED`
- **Timeframe not supported**: `ResolutionOutcome.TIMEFRAME_NOT_SUPPORTED`
- **Instantiation failed**: `ResolutionOutcome.INSTANTIATION_FAILED`

## API Usage

```python
from trading_agent.authority import RuntimeStrategyResolver, Environment
from trading_agent.authority.loader import PromotedStrategy

# Create resolver with config
resolver = RuntimeStrategyResolver(config)

# Resolve a promoted strategy
strategy = resolver.resolve(promoted_strategy)

if strategy is None:
    # Check why it failed
    outcome = resolver.last_outcome  # See ResolutionOutcome enum
    logger.error(f"Resolution failed: {outcome}")
    return

# Set up authority chain
decision_input = DecisionInput(
    strategy=strategy,
    signal=signal,
    ...
)
```

## Tests

See `tests/authority/test_resolver.py` and `tests/authority/test_promotion_binding.py`.