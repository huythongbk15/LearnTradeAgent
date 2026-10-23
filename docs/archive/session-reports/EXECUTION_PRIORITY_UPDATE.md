# Execution Optimization Priority — Updated

## Status

| Priority | Task | LLM Contribution | System Impact | Status |
|----------|------|------------------|---------------|--------|
| P0 | T3A vol-based order type selection | `_select_order_type()` decision tree, bug fix in `provider()` | Market/Limit auto-selection per bar vol regime | Deployed (f9037ae) |
| P1 | T4A confidence scaling into risk policy | `confidence_adjustment` field on `RoutingDecision`, `_scale_confidence()` scaling | Exposure scaled by LLM confidence [0.5, 1.5] | Deployed (f3512f8) |
| P2 | T2C slippage per asset | Calibration from 59k bars \u00d7 3 assets, per-symbol config, T3A integration | Backtest accuracy ↑ (was 5bps flat) | Deployed |
| P3 | T5A daily data + cross-asset universe | `crawl_daily_data.py` + extended `t5a_cross_asset_diversification()` + `load_symbol_daily()` + `t2c_equity_slippage_calibration()` (T2C-1) | Diversification benefit 0.73 (>0.30), BTC-SPY corr -0.03, equity class calibrated | Deployed (T5A-1) |
