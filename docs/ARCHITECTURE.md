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
│   StrategyRuntime → DecisionAuthority → Allocator → ExposureAuthority   │
│   (src/trading_agent/authority)                                         │
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
  cli/                       — CLI entrypoint (Click), command groups
  authority/                 — DecisionAuthority, ExposureAuthority, allocator,
                               RuntimeStrategyResolver, PromotionStateStore
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

## Risk decision semantics

- `RiskDecision` tách `max_position_size_pct` thành:
  - `target_exposure_pct`: mục tiêu exposure tổng thể.
  - `max_new_exposure_pct`: tối đa exposure mới được tạo bởi lệnh này.
  - `reduce_only`: bắt buộc exit khi rủi ro cao.
- HIGH/EXTREME risk → `max_new_exposure_pct=0`, `reduce_only=true`.
- Risk manager vẫn trả về `AgentMessage` nhưng có thêm các trường mới để order gate sử dụng.

## Config secrets

- `EffectiveConfig` merge `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` từ ENV trước validation.
- Paper mode có thể bỏ Telegram nếu policy cho phép.
- Non-paper mode thiếu Telegram credentials → fail-closed.

## Unified order permission

- `evaluate_order_permission()` là single gate cho mọi order path.
- Output: `ALLOW`, `REDUCE_ONLY`, hoặc `BLOCK` + reason codes.
- Kiểm tra: kill switch, stale price, manual block, protection gap, reconciliation, inventory, unknown broker state.

## Canonical forecast and execution boundary (2026-08-17)

The production-intent contract is now explicit:

```text
MarketObservation
  -> ForecastStrategy (broker-free)
  -> frozen Forecast (model artifact + calibration/OOD/interval provenance)
  -> ForecastRiskPolicy
  -> deterministic RiskDecision
  -> TargetExposure
  -> authoritative OrderPermission
  -> execution lifecycle / environment adapter
```

This is the target contract. As of 2026-10-31 it is not how the system runs.
Research builds signals through `canonical_signal_series`, which calls the
adapter's `forecast`, then fills them in `FullSystemSimulator` at k+2 on the
tested timeframe, by default with a 5 % stop and a 25 % requested target. The
Binance runner computes Enhanced MA state in `compute_state`; its promoted path
calls `StrategyRuntime.execute`, which calls the strategy's signal method
directly and produces no frozen `Forecast`. It runs every 2 h, reads the last
closed 1h bar, and uses a 2×ATR trail, weights of 4/3/3 % and a $100 order cap.
The two paths share strategy classes, not behaviour, and the difference has not
been measured on real data. No strategy receives a broker handle, and an
unsupported adapter raises rather than silently returning no orders.

`execution.permission` is the authoritative order gate for normal orders, smart
routing, lifecycle submission and safe exits. Sell inventory is reserved
transactionally across lifecycle instances; partial fills reduce the reservation;
cancel/reject terminal states release only the remainder. Kill switch, stale data,
reconciliation and unknown broker state block risk increase while permitting only
provably reduce-only exits.

## Research evidence boundary

- `research/forecast.py`: immutable observation/forecast/risk/target contracts.
- `research/promotion.py`: eight-stage, no-skip, content-addressed promotion ladder. Exercised only by tests: no code in `src` or `scripts` builds evidence for it or calls `promote`.
- `research/trials.py`: append-only WAL experiment/evaluation registry and effective
  trial-count derivation.
- `alpha_research/feature_store.py`: content-addressed feature artifacts.
- `research/calibration.py`: train-only calibrators, conformal intervals and
  monotone uncertainty sizing.
- `research/drift.py`: reference-only distribution and execution-quality drift.
- `execution/simulator/calibration_provenance.py`: immutable synthetic/testnet/
  shadow/live calibration datasets and profiles.

Boolean integrity claims do not qualify for canonical promotion. Synthetic
simulator output stays `HEURISTIC`; only captured exchange observations may become
`EMPIRICAL`. Full methodology: [`RESEARCH_METHODOLOGY.md`](RESEARCH_METHODOLOGY.md).

Chi tiết gates: [`LIVE_TRADING_TODO.md`](LIVE_TRADING_TODO.md) · Maturity: [`CAPABILITY_MATRIX.md`](CAPABILITY_MATRIX.md)
