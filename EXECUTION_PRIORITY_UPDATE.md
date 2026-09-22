# Execution Optimization Priority — Updated

## Status

| Priority | Task | LLM Contribution | System Impact | Status |
|----------|------|------------------|---------------|--------|
| P0 | T3A vol-based order type selection | `_select_order_type()` decision tree, bug fix in `provider()` | Market/Limit auto-selection per bar vol regime | Deployed (f9037ae) |
| P1 | T4A confidence scaling into risk policy | `confidence_adjustment` field on `RoutingDecision`, `_scale_confidence()` scaling | Exposure scaled by LLM confidence [0.5, 1.5] | Deployed (f3512f8) |
| P2 | T2C slippage per asset | (T3A covers half — limit pricing is static 0.5%) | Backtest accuracy | Planned |
| P3 | T5A daily data + cross-asset universe | Eval fails (corr 0.85, needs daily+equities) | Diversification gap | Planned |
