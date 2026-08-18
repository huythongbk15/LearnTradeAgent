"""
Execution & Risk Management Layer (Phase 3).

Modules:
- types.py              — Order, Trade, Position dataclasses
- paper_exchange        — Simulated exchange (no real API)
- engine.py             — Unified execution engine (paper/live)
- portfolio.py          — Balance + P&L tracking
- position.py           — Open position management
- risk_controller.py    — Stop-loss, max DD, circuit breaker
- canonical/            — Canonical execution pipeline
  - legacy_adapter.py   — Legacy signal → canonical risk decision adapter
  - broker_gateway.py   — Capital-changing boundary
  - runner_adapter.py   — Legacy runner wrapper
  - order_planner.py    — Order planning and sizing
  - permission.py       — Permission checks
  - protection.py       — Protective order plans
  - market_observation.py — Enriched market observations
  - risk_decision.py    — Unified risk decision
"""
