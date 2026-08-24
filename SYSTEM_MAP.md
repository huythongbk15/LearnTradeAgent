# SYSTEM_MAP.md — Current Architecture Snapshot (HEAD: b027a675)

> Auto-generated reference for the authority-chain refactor.  
> **Do not commit this file** — it's a living working document.

---

## 1. Repo Layout (src/trading_agent)

```
src/trading_agent/
├── agents/                    # Phase 2 — Multi-agent signal generation
│   ├── base.py               # AgentMessage, AnalysisContext, BaseAgent
│   ├── technical.py          # TechnicalAnalyst (rule-based + LLM)
│   ├── sentiment.py          # SentimentAnalyst (rule-based + LLM)
│   ├── risk.py               # RiskManager → RiskDecision
│   ├── risk_decision.py      # RiskDecision, RiskLevel (enum)
│   ├── trader.py             # Trader (weighted vote → final AgentMessage)
│   ├── llm.py                # LLM orchestration, backtest mode
│   ├── orchestrator.py       # AgentOrchestrator (4-agent pipeline)
│   ├── calibration.py        # Empirical calibration
│   ├── swarm/                # Specialized agent swarm
│   └── ...
├── alpha_research/           # Phase 1 — Alpha discovery
│   ├── feature_store.py      # FeatureArtifact, FeatureStore (parquet)
│   ├── methodology.py        # AlphaEvaluator, AutoMLPipeline, WFO
│   └── pipeline.py           # 40+ alpha factors + scan
├── authority/                # NEW: Authority Chain (Milestone A complete)
│   ├── __init__.py
│   ├── config.py             # AuthorityConfig (Pydantic, single schema)
│   ├── causation.py          # CausationID, CausationChain (content-addressed)
│   ├── decision.py           # DecisionAuthority (Signal/Artifact → URD + TargetExposure)
│   ├── exposure.py           # ExposureAuthority (exposure cap validation)
│   ├── execution.py          # ExecutionAuthority (Intent → Lifecycle → Gateway)
│   ├── portfolio.py          # PortfolioAllocator, PositionSizer (multi-pair)
│   ├── loader.py             # PromotedStrategy, RuntimeLoader (hot-reload)
│   └── audit.py              # CausationLogger, DecisionAuditCLI
├── execution/                # Phase 3 — Canonical execution stack
│   ├── engine.py             # ExecutionEngine (paper + canonical)
│   ├── canonical/
│   │   ├── instrument_registry.py  # TEN_PAIR_1H_SYMBOLS + rules
│   │   ├── order_planner.py        # TargetExposure → Intent
│   │   ├── broker_gateway.py       # BrokerGateway, adapters
│   │   ├── protection.py           # SL/TP planning
│   │   ├── risk_decision.py        # UnifiedRiskDecision
│   │   ├── causation.py            # CausationID, chain (LEGACY - use authority.causation)
│   │   ├── adapters.py             # PaperExecutionAdapter
│   │   ├── legacy_adapter.py       # LegacyDecisionAdapter
│   │   ├── market_observation.py   # EnrichedMarketObservation
│   │   └── events.py               # ExecutionEventType
│   ├── lifecycle/
│   │   ├── store.py                # ExecutionEventStore (SQLite)
│   │   ├── lifecycle.py            # ExecutionLifecycle, ExposureEffect
│   │   └── events.py               # ExecutionEventType enum
│   ├── permission.py               # PermissionContext, evaluate_permission
│   ├── paper_exchange.py           # PaperExchange (simulated broker)
│   ├── simulator/                  # High-fidelity simulator
│   ├── types.py                    # Order, OrderSide, OrderStatus
│   └── application.py              # CanonicalExecutionService
├── portfolio/             # Portfolio optimization, risk budgeting
├── research/              # Research governance (artifact, promotion)
│   ├── artifact.py           # StrategyArtifact, PersistentArtifactStore
│   ├── promotion.py          # ResearchStage, EvidenceArtifact, Gate
│   ├── lifecycle.py          # ResearchLifecycle
│   ├── calibration.py        # Empirical calibration
│   ├── drift.py              # Drift detection
│   ├── forecast.py           # Forecast validation
│   ├── trials.py             # Trial management
│   └── uncertainty.py        # Uncertainty quantification
├── risk/                    # Portfolio risk, position sizing
│   ├── portfolio_risk.py
│   └── position_sizer.py
├── cli/                     # Commands: backtest, research, agents, live
├── config/                  # Config loader
├── data/                    # Data ingestion, market data
├── exchanges/               # CCXT adapters, DEX, futures
├── llm/                     # LLM utilities
└── regime.py                # Regime indicators
```

---

## 2. Current Data/Authority Flow (text diagram)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    CURRENT ARCHITECTURE (HEAD b027a675)                 │
└─────────────────────────────────────────────────────────────────────────┘

PHASE 1: ALPHA RESEARCH (alpha_research/)
┌─────────────────┐    ┌──────────────────┐    ┌───────────────────────┐
│  FeatureStore   │───▶│ AlphaEvaluator   │───▶│ AutoMLPipeline / WFO  │
│  (parquet,      │    │ (IC, IR,         │    │ (nested CV,          │
│   content-hash) │    │  PBO, deflated   │    │  combinatorial)      │
└─────────────────┘    │  Sharpe, decay)  │    └──────────┬────────────┘
                       └──────────────────┘               │
                                                          ▼
┌─────────────────┐    ┌──────────────────┐    ┌───────────────────────┐
│  StrategyArtifact  │◀───│ PromotionGate   │◀───│ EvidenceArtifact      │
│  (immutable,     │    │ (stages:        │    │ (content-addressed,   │
│   hash-chained)  │    │  EXPLORATORY →  │    │  validator-signed)    │
└─────────────────┘    │  PRODUCTION)     │    └───────────────────────┘
                       └──────────────────┘
                            │
                            ▼ (manual or scripted promotion)
                       ┌──────────────────┐
                       │ PROMOTED ARTIFACT│
                       │ (code_sha,       │
                       │  param_hash,     │
                       │  data_sha)       │
                       └──────────────────┘

PHASE 2: MULTI-AGENT SIGNALS (agents/)
┌─────────────────┐    ┌──────────────────┐    ┌───────────────────────┐
│ TechnicalAnalyst│───▶│                  │    │                       │
│ SentimentAnalyst│───▶│  AgentOrchestrator│───▶│  Trader (weighted     │
│ RiskManager     │───▶│  (4-agent pipe)  │    │  vote + risk override)│
└─────────────────┘    └──────────────────┘    └──────────┬────────────┘
                                                         │
                                              AgentMessage {signal, confidence,
                                                            risk_level, details}
                                                         ▼
                                               ┌───────────────────────┐
                                               │ BacktestEngine        │
                                               │ (agent_ensemble       │
                                               │  strategy)            │
                                               └───────────────────────┘
                                                         │
                                                         ▼ (manual parameter copy)
                                               ┌───────────────────────┐
                                               │ LIVE EXECUTION        │
                                               │ (ExecutionEngine)     │
                                               └───────────────────────┘

PHASE 3: CANONICAL EXECUTION (execution/)
┌─────────────────────────────────────────────────────────────────────────┐
│  ExecutionEngine.execute_signal(AgentMessage)                           │
│                                                                         │
│  1. LegacyDecisionAdapter.adapt(signal, obs)                            │
│     → UnifiedRiskDecision + TargetExposure                              │
│                                                                         │
│  2. OrderPlanner.plan(target, risk, obs, portfolio, price)              │
│     → Intent {symbol, side, qty, limit, protection}                     │
│                                                                         │
│  3. PermissionContext + evaluate_permission()                           │
│     → ALLOW/DENY (exposure caps, freshness, inventory, health)          │
│                                                                         │
│  4. ExecutionLifecycle.claim_intent() → BROKER_GATEWAY.submit()         │
│     → PaperExecutionAdapter → PaperExchange                             │
│                                                                         │
│  5. Lifecycle events: CLAIMED → SUBMITTED → FILLED/REJECTED             │
└─────────────────────────────────────────────────────────────────────────┘

---

## 3. Critical Gaps (Why This Refactor)

| Gap | Current State | Required State |
|-----|---------------|----------------|
| **Research → Live param copy** | Manual: copy params from report to strategy config | Auto: `PromotedStrategy` loads artifact directly |
| **Agent ensemble → Execution** | `AgentMessage` → `LegacyDecisionAdapter` (lossy) | `UnifiedRiskDecision` as single authority object |
| **Strategy versioning** | Strategy class + params in config (drift-prone) | Content-addressed `StrategyArtifact` at runtime |
| **Single pair vs multi-pair** | Engine runs one symbol; multi-symbol = multiple engines | `PortfolioAllocator` + `PositionSizer` above engines |
| **Permission = exposure gate** | `PermissionContext` checks risk + health + freshness | Explicit `ExposureAuthority` chain |
| **Canonical = paper only** | `PaperExecutionAdapter` hardcoded | Adapter interface for live brokers (CCXT/Alpaca) |
| **No replay/verify** | Lifecycle events stored, no replay harness | Deterministic replay from event store |
| **Config drift** | YAML configs scattered, no schema enforcement | Single `AuthorityConfig` schema + validation |

---

## 4. Key Types Currently in Play

### Research Governance (`research/`)
- `StrategyArtifact` — immutable, content-addressed (code_sha, data_sha, param_hash)
- `EvidenceArtifact` — content-addressed evidence with validator signature
- `ResearchStage` — EXPLORATORY → RESEARCH_VALIDATED → PAPER_ELIGIBLE → TESTNET_ELIGIBLE → SHADOW_ELIGIBLE → CANARY_ELIGIBLE → CANARY → PRODUCTION
- `PersistentArtifactStore` — SQLite + integrity chain (sha256 chain)

### Multi-Agent (`agents/`)
- `AgentMessage` — role, signal, confidence, reasoning, details (dict)
- `RiskDecision` — risk_level, target_exposure_pct, max_new_exposure_pct, reduce_only
- `Trader` — weighted vote (tech 40%, sentiment 20%, risk 40%)

### Canonical Execution (`execution/canonical/`)
- `UnifiedRiskDecision` — calibrated_score, target_exposure, max_new_exposure, uncertainty, etc.
- `TargetExposure` — target_exposure_pct, max_new_exposure_pct, reduce_only, confidence, authority_chain
- `Intent` — symbol, side, qty, limit_price, protection, strategy_version, causation_id
- `InstrumentRules` — min/max qty, step, precision, notional caps, spot_long_only
- `PermissionContext` — all gates (health, freshness, inventory, exposure, reconciliation)
- `OrderPlanner` — converts TargetExposure → Intent using InstrumentRules

### Lifecycle (`execution/lifecycle/`)
- `ExecutionLifecycle` — intent state machine (PENDING→CLAIMED→SUBMITTED→FILLED/REJECTED)
- `ExecutionEventStore` — SQLite event log with global_seq, integrity
- `ExposureEffect` — INCREASE / REDUCE / NEUTRAL
- `TrustedPrice` — price + exchange_timestamp + received_at
- `PortfolioRiskSnapshot` — position, equity, cash, observed_at, source

### Instrument Registry
- `TEN_PAIR_1H_SYMBOLS` — BTC, ETH, SOL, XRP, BNB, ZEC, DOGE, TRX, ADA, NEAR (USDT spot)
- `INSTRUMENT_RULES_1H` — MappingProxyType, fail-closed on unknown symbol

---

## 5. Test Coverage (Current)

```bash
# Full suite
pytest -x -q  # 914 passed, 12 skipped, 2 warnings (222s)

# Key test modules
tests/test_execution_lifecycle.py      # 41 tests — lifecycle, snapshots, replay
tests/test_execution_hardening.py      # 16 tests — P0 safety hardening
tests/test_e2e_paper_flow.py           # 24 tests — end-to-end paper flow
tests/test_research_promotion.py       # Promotion gate tests
tests/test_canonical_execution.py      # OrderPlanner, Permission, Gateway
tests/test_promotion_guard.py          # PromotionGate edge cases
tests/test_legacy_cutover_migration.py # Legacy migration tests
```

---

## 6. Live Paper Trading Status (as of HEAD b027a675)

- **Equity**: $95,209.34 | **Cash**: $95,209.34 | **Positions**: 0 open
- **Drawdown**: 5.3% from $100,512.53 peak
- **Risk scale**: 75% | **Trading**: ALLOWED
- **Symbols**: BTC/USDT, SOL/USDT, AVAX/USDT — all FLAT
- **Events**: No fill/fail events; Binance `exchangeInfo` errors transient (resolved)

---

## 7. Milestone Status

### ✅ MILESTONE A — Authority Foundation (COMPLETED at HEAD b027a675)
- `AuthorityConfig` — Pydantic schema with env overrides, env-specific presets
- `CausationID` / `CausationChain` — content-addressed, cryptographically chained
- `DecisionAuthority` — fail-closed Signal/Artifact → UnifiedRiskDecision + TargetExposure
- `ExposureAuthority` — single source of truth for exposure caps (portfolio/strategy/symbol/correlation/cash/notional)
- `ExecutionAuthority` — Intent → Lifecycle claim → BrokerGateway (fail-closed)
- `PortfolioAllocator` / `PositionSizer` — multi-pair risk budget allocation (ready for Milestone C)
- `PromotedStrategy` / `RuntimeLoader` — artifact → runtime with hot-reload, param drift detection
- `CausationLogger` / `DecisionAuditCLI` — JSONL audit log, deterministic replay

### ⏳ MILESTONE B — Single-Pair Canonical Loop (PENDING)
- Wire `DecisionAuthority` → `ExposureAuthority` → `ExecutionAuthority` in `ExecutionEngine`
- Replace `LegacyDecisionAdapter` with authority chain
- Add `authority_chain` field to `UnifiedRiskDecision`
- Add `--authority-config` flag to `cli/commands/live.py`

### ⏳ MILESTONE C — Multi-Pair Portfolio Layer (PENDING)
- `PortfolioPermission`: cross-symbol exposure caps, correlation limits
- Integration with `PortfolioAllocator` in live loop

### ⏳ MILESTONE D — Research → Runtime Bridge (PENDING)
- `PromotionHook` in `research/promotion.py` on PRODUCTION promotion
- Hot-reload manifest in live trading

### ⏳ MILESTONE E — Observability & Replay (PENDING)
- Deterministic replay from event store
- DecisionAuditCLI integration

---

## 8. Files Created (Milestone A)

### New Authority Module
- `src/trading_agent/authority/config.py` — AuthorityConfig (Pydantic)
- `src/trading_agent/authority/causation.py` — CausationID, CausationChain
- `src/trading_agent/authority/decision.py` — DecisionAuthority
- `src/trading_agent/authority/exposure.py` — ExposureAuthority
- `src/trading_agent/authority/execution.py` — ExecutionAuthority
- `src/trading_agent/authority/portfolio.py` — PortfolioAllocator, PositionSizer
- `src/trading_agent/authority/loader.py` — PromotedStrategy, RuntimeLoader
- `src/trading_agent/authority/audit.py` — CausationLogger, DecisionAuditCLI
- `src/trading_agent/authority/__init__.py` — Exports

### Documentation
- `SYSTEM_MAP.md` — This file (updated)
- `EXPERT_ROADMAP.md` — Original roadmap (unchanged)