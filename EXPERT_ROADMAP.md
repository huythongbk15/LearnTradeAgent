# EXPERT ASSESSMENT & ROADMAP
**Trading Agent System — Current State Analysis & Phased Execution Plan**
*Generated: 2026-08-24 | Status: Paper trading operational (3 pairs), 1022 tests passing*

---

## 🎯 EXECUTIVE SUMMARY

| Aspect | Current State | Target | Gap |
|--------|---------------|--------|-----|
| **Live Trading** | 3 pairs (BTC, SOL, AVAX) on Alpaca Paper | 10 pairs on Binance Spot Testnet → Mainnet | 7 pairs missing; Alpaca limitation |
| **Execution Engine** | Canonical pipeline operational (P0 hardening done) | Production-grade with full observability | Missing: reconciliation automation, kill-switch integration |
| **Risk Management** | PortfolioRiskManager (drawdown tiers, scaling) | Multi-layer: pre-trade, intra-trade, portfolio | Missing: real-time Greeks, correlation limits, stress testing |
| **Research Pipeline** | 30-stream audit, WFO, baselines complete | Continuous retraining + online learning | Missing: automated model promotion, drift detection |
| **Data Quality** | 0-null OHLCV gate enforced | Multi-source validation, gap filling | Missing: Binance/Alpaca cross-validation |
| **Observability** | Basic logging, Telegram alerts | Full metrics (Prometheus), tracing, dashboards | Missing: structured metrics, alerting rules |
| **CI/CD** | Tests pass, deploy blocked on SSH secrets | Automated staging → production | Missing: secrets config, canary deploy |

---

## 📊 CURRENT ARCHITECTURE ASSESSMENT

### ✅ STRENGTHS (What's Working)
1. **Canonical Execution Pipeline** — LegacyAdapter → Risk → Planner → Permission → Lifecycle → Gateway → PaperExchange
2. **P0 Execution Safety** — Atomic submission claims, UNKNOWN=OPEN, idempotency, snapshot/replay, 913 tests
3. **Instrument Registry** — 10 canonical pairs with reviewed constraints (qty step, precision, min notional)
4. **PaperExchange** — Telemetry isolation, deterministic fills, position tracking
5. **Research Pipeline** — 30 stream audit, WFO, holdout validation, regime labeling
6. **Test Coverage** — 1022 tests, property-based, chaos, contract tests

### ⚠️ CRITICAL GAPS (Blocking Mainnet)
| # | Gap | Impact | Effort |
|---|-----|--------|--------|
| 1 | **Alpaca only supports 3/10 pairs** | Cannot trade BNB, XRP, ZEC, DOGE, TRX, ADA, NEAR | Medium |
| 2 | **No automated reconciliation** | Drift between lifecycle state ↔ broker reality | Medium |
| 3 | **No kill-switch integration** | Emergency stop not wired to engine | Low |
| 4 | **No structured metrics/observability** | Cannot debug production issues | Medium |
| 5 | **No model promotion pipeline** | Research → Live is manual | High |
| 6 | **No correlation/portfolio risk** | Concentration risk unmanaged | Medium |
| 7 | **CI/CD deploy broken** | Cannot ship to staging/production | Low |

### 🔴 ARCHITECTURAL DEBT
- **Dual execution paths**: `live_enhanced_ma.py` uses legacy `_canonical_submit()` for SELL only; BUY blocked
- **No unified Engine factory** — Each pair needs own `ExecutionEngine` with `instrument_rules`
- **Signal generation decoupled from execution** — No feedback loop (PnL → signal adjustment)
- **Configuration scattered** — `live_config.py`, env vars, hardcoded params

---

## 🗺️ PHASED ROADMAP

---

### PHASE 0: FOUNDATION HARDENING (Week 1-2) — *Prerequisites for everything*

| Task | Owner | Deliverable | Acceptance |
|------|-------|-------------|------------|
| **P0.1** Binance Spot Testnet integration | Eng | `scripts/live_binance_testnet.py` with 10 pairs | All 10 pairs submit orders successfully |
| **P0.2** Unified Engine Factory | Eng | `ExecutionEngineFactory.create(pair)` | Single entry point for all pairs |
| **P0.3** Kill-switch wiring | Eng | `TRADING_KILL_SWITCH` env → `PermissionContext.kill_switch_active` | Engine rejects all orders when active |
| **P0.4** Reconciliation scheduler | Eng | `scripts/reconcile_positions.py` cron (5min) | Lifecycle state = broker state within 1 min |
| **P0.5** Structured logging + metrics | Eng | JSON logs + Prometheus `/metrics` endpoint | All engine events emitted as structured logs |
| **P0.6** CI/CD secrets + deploy | DevOps | `PRODUCTION_SSH_KEY`, `STAGING_SSH_KEY` in GitHub | `cd-staging.yml` passes |

**Exit Criteria**: 10 pairs on Binance testnet, kill-switch tested, reconciliation runs, metrics visible

---

### PHASE 1: MULTI-PAIR LIVE OPERATIONS (Week 3-4) — *Paper trading at scale*

| Task | Owner | Deliverable | Acceptance |
|------|-------|-------------|------------|
| **P1.1** 10-pair Binance Testnet runner | Eng | `scripts/live_10pair_binance.py` | 10 pairs running hourly, Telegram alerts |
| **P1.2** Portfolio risk integration | Eng | `PortfolioRiskManager` → `PermissionContext` | Max 20% single asset, 60% sector (crypto) |
| **P1.3** Correlation monitoring | Eng | Rolling 30d correlation matrix in metrics | Alert if >0.8 correlation exposure >40% |
| **P1.4** Position sizing per Kelly/fractional | Eng | `PositionSizer` using `risk_decision.interval_width` | Size scales with prediction confidence |
| **P1.5** Dust/fee handling | Eng | `LIVE_MAX_DUST_USD` respected, min notional | No failed orders due to dust |
| **P1.6** 72h soak test | QA | Continuous run log | 0 crashes, 0 unreconciled positions, <1% fill latency |

**Exit Criteria**: 10 pairs stable 72h, portfolio risk enforced, metrics dashboard live

---

### PHASE 2: RESEARCH → LIVE PIPELINE (Week 5-8) — *Automated model promotion*

| Task | Owner | Deliverable | Acceptance |
|------|-------|-------------|------------|
| **P2.1** Model registry | ML | MLflow/Weights&Biases integration | All models versioned, lineage tracked |
| **P2.2** Drift detection | ML | PSI + KS test on live features vs training | Alert + auto-block if PSI > 0.2 |
| **P2.3** A/B testing framework | Eng | Canary deployment (5% capital) | New model vs champion, statistical test |
| **P2.4** Automated promotion gate | Eng | `research_pipeline_promote.py` | Passes: holdout, WFO, cost stress, regime stability |
| **P2.5** Online learning (optional) | ML | Incremental update (River/sklearn-partial) | Model updates without full retrain |
| **P2.6** Feature store | Eng | Centralized feature definitions | Same features in research + live |

**Exit Criteria**: Model promoted from research → live without manual intervention

---

### PHASE 3: PRODUCTION HARDENING (Week 9-12) — *Mainnet readiness*

| Task | Owner | Deliverable | Acceptance |
|------|-------|-------------|------------|
| **P3.1** Staging environment | DevOps | Staging = mirror of production | Deploy to staging passes all smoke tests |
| **P3.2** Canary deployment | DevOps | 1% → 5% → 25% → 100% traffic split | Automated rollback on error rate >1% |
| **P3.3** Disaster recovery drill | Eng | `scripts/disaster_recovery.py` | RTO < 15min, RPO < 1min verified |
| **P3.4** Security audit | Sec | Penetration test, secret scan | 0 critical, 0 high findings |
| **P3.5** Operational runbooks | Eng | `RUNBOOK.md` for all failure modes | On-call can resolve without escalation |
| **P3.6** Mainnet go/no-go gate | All | Signed checklist | All P0-P3 criteria met, sign-off |

**Exit Criteria**: Mainnet deployment approved, runbooks tested, DR verified

---

### PHASE 4: SCALE & OPTIMIZE (Month 4+) — *Continuous improvement*

| Task | Owner | Deliverable |
|------|-------|-------------|
| **P4.1** Multi-venue execution | Eng | Binance + Bybit + OKX smart routing |
| **P4.2** Advanced order types | Eng | TWAP, VWAP, iceberg, post-only |
| **P4.3** Portfolio optimization | Quant | Mean-variance / HRP / Black-Litterman |
| **P4.4** Alternative data | ML | On-chain, funding rates, order flow |
| **P4.5** Reinforcement learning | ML | RL agent for execution optimization |

---

## 📋 DETAILED TASK BREAKDOWN

---

### P0.1 — Binance Spot Testnet Integration

```python
# Required changes:
# 1. scripts/live_binance_testnet.py (new)
#    - CCXT Binance testnet client
#    - 10 pair config from InstrumentRegistry
#    - PaperExchangeAdapter for Binance
# 2. src/trading_agent/execution/binance_adapter.py (new)
#    - BinanceSpotAdapter implementing BrokerAdapter protocol
#    - Rate limiting, precision handling
# 3. Config: live_config.py add BINANCE_TESTNET_* symbols
```

**Dependencies**: InstrumentRegistry (done), CCXT (available)

---

### P0.2 — Unified Engine Factory

```python
# src/trading_agent/execution/engine_factory.py (new)
class ExecutionEngineFactory:
    @staticmethod
    def create(symbol: str, config: EngineConfig) -> ExecutionEngine:
        rules = InstrumentRegistry.get(symbol)
        return ExecutionEngine(
            exchange_name=config.exchange,
            instrument_rules=rules,
            state_dir=config.state_dir / symbol,
            disable_paper_telemetry=config.disable_telemetry,
        )
```

**Usage**: Single entry point for all pairs, consistent config

---

### P0.3 — Kill-Switch Wiring

```python
# In ExecutionEngine.execute_signal():
kill_switch = os.getenv("TRADING_KILL_SWITCH", "false").lower() == "true"
permission_ctx = PermissionContext(
    ...,
    kill_switch_active=kill_switch,
)
```

**Test**: Set `TRADING_KILL_SWITCH=true` → all orders blocked

---

### P0.4 — Reconciliation Scheduler

```python
# scripts/reconcile_positions.py
async def reconcile_all():
    for symbol in ACTIVE_SYMBOLS:
        engine = ENGINES[symbol]
        broker_positions = await engine.exchange.fetch_positions()
        lifecycle_positions = engine.lifecycle.state.positions
        diff = compare(broker_positions, lifecycle_positions)
        if diff:
            engine.lifecycle.reconcile(diff)  # Emits RECONCILIATION_APPLIED
            alert_on_drift(diff)
```

**Schedule**: Cron every 5 minutes

---

### P0.5 — Structured Metrics

```python
# src/trading_agent/execution/metrics.py (new)
from prometheus_client import Counter, Histogram, Gauge

ORDERS_SUBMITTED = Counter("orders_submitted_total", "Total orders", ["symbol", "side"])
ORDER_LATENCY = Histogram("order_latency_seconds", "Submit latency", ["symbol"])
POSITION_EXPOSURE = Gauge("position_exposure", "Current exposure", ["symbol"])
EXECUTION_HEALTH = Gauge("execution_health", "Health state", ["symbol"])
```

**Emit in**: `engine.py`, `lifecycle.py`, `gateway.py`

---

### P1.1 — 10-Pair Runner

```python
# scripts/live_10pair_binance.py
async def run_cycle():
    engines = {s: EngineFactory.create(s) for s in TEN_PAIR_1H_SYMBOLS}
    for symbol, engine in engines.items():
        signal = await generate_signal(symbol)  # Enhanced MA or ML model
        obs = await fetch_observation(symbol)
        orders = engine.execute_signal(signal, obs)
        await notify_telegram(orders)
```

**Concurrency**: `asyncio.gather` with semaphore (max 3 concurrent)

---

### P1.2 — Portfolio Risk in Permission

```python
# src/trading_agent/execution/portfolio_risk.py
class PortfolioRiskManager:
    def check(self, symbol: str, side: str, qty: float) -> PermissionResult:
        current_exposure = self.get_exposure(symbol)
        portfolio_var = self.calculate_var()
        if side == "buy" and current_exposure + qty > MAX_SINGLE_ASSET:
            return PermissionResult(BLOCK, EXPOSURE_LIMIT, ...)
        if portfolio_var > MAX_PORTFOLIO_VAR:
            return PermissionResult(BLOCK, PORTFOLIO_VAR_LIMIT, ...)
        return PermissionResult(ALLOW)
```

**Integration**: `PermissionContext.portfolio_risk = manager.check(...)`

---

### P2.1 — Model Registry

```python
# MLflow integration
import mlflow


def log_model(artifact_path: str, model, metrics: dict):
    with mlflow.start_run():
        mlflow.log_params(model.get_params())
        mlflow.log_metrics(metrics)
        mlflow.sklearn.log_model(model, artifact_path)
        return mlflow.active_run().info.run_id


def promote_model(run_id: str, stage: str = "Staging"):
    client = mlflow.MlflowClient()
    client.transition_model_version_stage(
        name="enhanced_ma",
        version=run_id,
        stage=stage,
    )
```

---

## 📈 SUCCESS METRICS PER PHASE

| Phase | Metric | Target |
|-------|--------|--------|
| **P0** | Test pass rate | 100% (1022+) |
| | Binance testnet pairs | 10/10 |
| | Reconciliation drift | 0 positions |
| | Kill-switch response | < 100ms |
| **P1** | Uptime (72h) | 99.9% |
| | Fill latency (p99) | < 500ms |
| | Portfolio VaR | < 5% daily |
| | Max single asset | < 20% |
| **P2** | Model promotion time | < 24h |
| | Drift detection recall | > 95% |
| | A/B test statistical power | > 80% |
| **P3** | Deploy frequency | Daily |
| | Rollback time | < 5min |
| | DR RTO | < 15min |
| **P4** | Sharpe improvement | +0.5 YoY |
| | Capacity | $10M+ AUM |

---

## 🚨 RISK REGISTER

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Binance API changes | Medium | High | Adapter abstraction, integration tests |
| Model drift undetected | Medium | High | PSI monitoring, automated retrain trigger |
| Reconciliation lag | Low | Critical | 5min cron + alert on drift |
| Kill-switch failure | Low | Critical | Dual-path: env + DB flag |
| Capital loss bug | Low | Critical | Paper → Testnet → Canary → Mainnet gates |
| Data feed outage | Medium | High | Multi-source fallback, cached last price |

---

## 📅 TIMELINE SUMMARY

```
Week 1-2:  ████████████  P0 Foundation (Binance, Factory, Kill-switch, Reconcile, Metrics, CI/CD)
Week 3-4:  ████████████  P1 Multi-Pair Live (10 pairs, Portfolio Risk, Correlation, Sizing, Soak)
Week 5-8:  ████████████████████  P2 Research→Live (Registry, Drift, A/B, Promotion, Online, Features)
Week 9-12: ████████████  P3 Production (Staging, Canary, DR, Security, Runbooks, Go/No-Go)
Month 4+:  ████████████  P4 Scale (Multi-venue, Advanced orders, Portfolio opt, Alt data, RL)
```

---

## 🎯 IMMEDIATE NEXT ACTIONS (This Week)

1. **Create `scripts/live_binance_testnet.py`** — Start with 1 pair (BTC/USDT), verify end-to-end
2. **Add `ExecutionEngineFactory`** — Refactor engine creation
3. **Wire `TRADING_KILL_SWITCH`** — Test in paper
4. **Write `scripts/reconcile_positions.py`** — Cron job
5. **Add Prometheus metrics** — `/metrics` endpoint in webui
6. **Configure GitHub secrets** — Unblock CI/CD deploy

---

## 📁 DOCUMENTATION TO CREATE/UPDATE

| Document | Status | Owner |
|----------|--------|-------|
| `EXPERT_ROADMAP.md` | ✅ This file | — |
| `BINANCE_TESTNET_INTEGRATION.md` | 📝 TODO | Eng |
| `ENGINE_FACTORY_DESIGN.md` | 📝 TODO | Eng |
| `RECONCILIATION_DESIGN.md` | 📝 TODO | Eng |
| `METRICS_SPEC.md` | 📝 TODO | Eng |
| `PORTFOLIO_RISK_DESIGN.md` | 📝 TODO | Quant |
| `MODEL_REGISTRY_DESIGN.md` | 📝 TODO | ML |
| `CANARY_DEPLOYMENT.md` | 📝 TODO | DevOps |
| `RUNBOOK_RECONCILIATION.md` | 📝 TODO | Eng |
| `RUNBOOK_KILL_SWITCH.md` | 📝 TODO | Eng |

---

## 🏁 CONCLUSION

**Current system is production-grade for P0 execution safety** but **not mainnet-ready** due to:
1. Limited to 3 pairs (Alpaca constraint)
2. Missing operational infrastructure (reconciliation, kill-switch, metrics)
3. No automated research→live pipeline
4. CI/CD deploy blocked

**Recommended path**: Execute Phase 0 in parallel (2 engineers), then Phase 1. Phase 2-3 can overlap with Phase 1 soak testing. Target mainnet readiness in **12 weeks**.

---

*This roadmap is a living document. Update after each phase completion.*