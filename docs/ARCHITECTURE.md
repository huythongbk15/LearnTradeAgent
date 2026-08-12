# Architecture

> Mô tả kiến trúc theo **code thực tế** (không theo roadmap cũ). Source of truth:
> executable code → tests → CI evidence → validation evidence → docs.

## 5 planes

```
┌───────────────────────────────────────────────────────────────────────┐
│ RESEARCH PLANE                                                         │
│   Data → Features → Strategies → Backtest → Statistical Validation →   │
│   Research Evidence                                                     │
│   (src/trading_agent/data, strategies, backtest, ml, research)         │
│   Research Governance: StrategyArtifact, promotion lifecycle,          │
│   uncertainty gate, abstention codes, drift detection, trials          │
│   (src/trading_agent/research)                                         │
├───────────────────────────────────────────────────────────────────────┤
│ DECISION PLANE                                                          │
│   Strategies / Agents → Portfolio → Risk → Order Intent                 │
│   (src/trading_agent/agents, portfolio, risk)                           │
├───────────────────────────────────────────────────────────────────────┤
│ EXECUTION PLANE                                                         │
│   Order Planner → Broker Adapter → Order Lifecycle → Fill Ledger →      │
│   Reconciliation → Protective Orders                                    │
│   (src/trading_agent/execution, exchanges, live_safety)                 │
│   Execution Simulator V2 (event-driven, P&L attribution,               │
│   RealityGapReport) — src/trading_agent/execution/simulator            │
├───────────────────────────────────────────────────────────────────────┤
│ CONTROL PLANE                                                           │
│   Configuration → Release Gates → Kill Switch → Leader/Fencing → Audit  │
│   (config/, docs/LIVE_TRADING_TODO.md, execution/live_safety, cli)      │
├───────────────────────────────────────────────────────────────────────┤
│ OBSERVABILITY PLANE                                                     │
│   Logs → Metrics → Alerts → Incident Response                           │
│   (src/trading_agent/monitoring, webui, Telegram)                       │
└───────────────────────────────────────────────────────────────────────┘
```

## Boundary & data ownership

| Plane | Sở hữu | Không được làm |
| --- | --- | --- |
| Research | Data, features, strategies, backtest, statistical validation | Không gửi lệnh; không claim production edge |
| Decision | Order intent (không phải order) | Không gửi lệnh trực tiếp |
| Execution | Broker interaction, lifecycle, reconciliation, protective orders | Không dùng stale data; fail-closed khi không chắc chắn |
| Control | Config, gates, kill switch, audit | Không tự enable mainnet khi deploy |
| Observability | Logs, metrics, alerts | Alerting phải độc lập với trading runner |

## Module map (chính)

```
src/trading_agent/
  cli.py                     — CLI entrypoint (Click), command groups
  data/                      — CCXT fetch, validation, storage (Parquet/SQLite)
  backtest/                  — vectorized + event-driven engines, metrics
  strategies/                — ma_crossover, rsi, bbands, enhanced_ma, ensemble...
  agents/                    — Technical/Sentiment/Risk/Trader + orchestrator
  portfolio/                 — optimizer (BL/HRP/risk parity), rebalancer, attribution
  risk/                      — risk controller, sizing, circuit breaker
  execution/                 — paper exchange, live broker, live_safety (P0.x),
                               order lifecycle, reconciliation, protective orders
  execution/lifecycle/       — Wave C: event-sourced execution lifecycle
                               (14 events, SQLite append-only store, deterministic
                               replay, idempotency, seq validation, snapshot/restore)
  execution/chaos_invariants.py — 9 trading invariants + 16 fault injections
  execution/shadow.py        — Shadow Mainnet Mode (real data, NO order submission)
  execution/simulator/       — Execution Simulator V2: order book, fill/impact/fee
                               models, ledger, metrics, P&L attribution, reality gap
  research/                  — StrategyArtifact, promotion lifecycle, uncertainty
                               gate, abstention codes, drift detection, trials
  exchanges/                 — CCXT adapters, order router, WebSocket manager,
                               health monitor, DEX (uniswap/jupiter/pancake),
                               alpaca, oanda, futures/options
  ml/                        — regime detection (HMM/GMM), online learning, meta-learning
  infrastructure/            — multi_region, chaos, kubernetes helpers
  monitoring/                — metrics server, health checks
  scheduler/                 — (KHÔNG tồn tại — service scheduler đã bị xóa khỏi prod compose)
```

## Nguyên tắc fail-closed

1. Market data không đáng tin (stale, clock skew, sequence gap) → **không đặt entry order**.
2. Trạng thái order không xác định → coi như cần reconciliation, không tự suy diễn `open`.
3. Missing protective stop → chặn entry mới, cảnh báo.
4. Kill switch chặn entry mới nhưng **cho phép** risk-reducing exits.
5. Deploy thành công ≠ mainnet enabled.

Chi tiết gates: [`LIVE_TRADING_TODO.md`](LIVE_TRADING_TODO.md) · Maturity: [`CAPABILITY_MATRIX.md`](CAPABILITY_MATRIX.md)
