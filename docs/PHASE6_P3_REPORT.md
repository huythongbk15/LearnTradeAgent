# Phase 6 P3 — Advanced Research & Production Hardening

**Status:** ✅ Completed  
**Date:** 2026-07-31

## 1. Overview

P3 wraps up Phase 6 by making the advanced components **testable**, **observable**
and **operable** outside a live Kubernetes cluster:

- Full **integration test suite** (52 tests) covering every P2 component
- **Production hardening** of event sourcing, online learning, messaging,
  multi-region and chaos components (incl. real bug fixes)
- **Dry-run modes** for multi-region sync controller and chaos experiments
  (no cluster required — CI/local friendly)
- **Performance benchmarks** and **load tests**
- **Makefile targets** + **CI/CD** glue

## 2. Integration Test Suite

File: `tests/test_phase6_integration.py` (52 tests)

| Test Class | Coverage |
|---|---|
| `TestEventSourcing` | File-backed EventStore append/read/batch roundtrip; Trade/Position/Risk/Signal projections |
| `TestOnlineLearning` | AdaptiveEMA/RSI/Bollinger/MACD; AdaptiveStrategy signal generation, reset |
| `TestMetaLearning` | MAML, Reptile, MetaSGD, ANIL meta-training + adaptation; MetaStrategyAdapter |
| `TestPortfolioOptimizer` | mean-variance, HRP, Black-Litterman (views), max-sharpe |
| `TestAttribution` | Brinson-style return attribution over 100-day window |
| `TestAutoRebalancer` | calendar, threshold, CPPI, force-rebalance, disabled state |
| `TestStrategyVersioning` | registry register/activate/deprecate, loader, git store save/load/tag |
| `TestSandbox` | validate (ok / forbidden import / syntax error), execute on_bar & get_params |
| `TestMessaging` | Message envelope roundtrip; in-memory bus publish/subscribe |
| `TestMultiRegion` | status, failover promotion, sync policy, **dry-run** modes |
| `TestChaos` | experiment suite, runner selection, report generation, **dry-run** modes |
| `TestEndToEndFlow` | adaptive strategy → events → projections → rebalance pipeline |

Run: `python -m pytest tests/test_phase6_integration.py -v`

## 3. Production Hardening & Bug Fixes

Real bugs found and fixed while writing the integration suite:

1. **`trading/ml/online/indicators.py`** — `OnlineSMA` had no `value` property,
   crashing `OnlineBollingerBands` (AttributeError on `.value`).
2. **`trading/events/models.py`** — `Event.from_dict()` returned a **plain dict**
   instead of a typed `Event` instance; Decimal fields with integer values
   (`"50"`) lost their type on read-back. Now dispatches by `event_type` via
   `_EVENT_CLASS_REGISTRY` and uses a strict numeric regex for Decimal
   deserialization.
3. **`trading/events/projections.py`** — `EventType.RISK_LIMIT_BREACHED` typo
   (enum member is `RISK_LIMIT_BREACH`) → AttributeError on limit-breach events.
4. **`trading/strategies/sandbox.py`** — wrong import (`Strategy` →
   `BaseStrategy`); generated script used lowercase `true` (NameError);
   class discovery iterated `dir()` and indexed `globals()`, raising KeyError.
5. **`trading/strategies/versioning/registry.py`** — `StrategyMetadata` was
   missing `is_active`; `StrategyLoader.load` exec'd source in an empty
   namespace → `NameError: BaseStrategy`.
6. **`trading/messaging/*`** — `nats` / `redis` imports were hard module
   dependencies; now optional with lazy `connect()` and a clear error message.
   `MessagePriority` is exported from the package root.

## 4. Dry-Run Modes (no cluster required)

### Multi-Region Sync Controller

`RegionSyncController(regions, dry_run=True)`:

- `start()` skips kube config loading and client initialization
- `_sync_region()` simulates a successful sync (zero lag, healthy status)
- `_check_region_health()` simulates healthy regions (lag-based degradation)
- `_update_region_config()` logs the intended ConfigMap change instead of calling k8s
- Failover logic still runs fully — role promotion/demotion is testable

Run: `make region-dryrun` or
`python trading/infrastructure/multi_region/sync_controller.py dryrun`

### Chaos Engineering

`ChaosExperimentSuite(namespace, dry_run=True)`:

- `run_all()` simulates each experiment (RUNNING → COMPLETED lifecycle,
  fake metrics) and produces a full report
- Useful for validating report generation and experiment definitions
  before touching a real cluster

Run: `make chaos-dryrun` or `python scripts/chaos_dryrun.py`

## 5. Performance Benchmarks

Script: `scripts/benchmark_phase6.py` (run: `make benchmark`)

| Component | Result |
|---|---|
| Event store append (10k) | ~216 ms |
| Event store read (10k) | ~149 ms |
| Adaptive strategy update | ~110 µs / bar |
| Meta-learning (Reptile, 5 tasks × 3 steps) | ~0.1 ms |
| Optimizer mean-variance (10 assets, 500 days) | ~1.5 ms |
| Optimizer HRP (10 assets, 500 days) | ~14 ms |
| Attribution (500 days) | ~1 ms |
| Sandbox subprocess execution | ~28 ms / exec |
| Auto-rebalance (5 assets) | ~0.12 ms |

## 6. Load Tests

Script: `scripts/load_test_phase6.py` (run: `make loadtest`; `--quick` for CI)

| Scenario | Throughput |
|---|---|
| Event store concurrent writes (4 writers) | ~28k ev/s |
| Event store reads | ~66k ev/s |
| Online learning sustained stream | ~9.2k bars/s |
| Optimizer mean-variance (20 assets) | ~10 ms |
| Optimizer HRP (20 assets) | ~16 ms |

## 7. Makefile Targets

```make
benchmark      # Phase 6 performance benchmarks
loadtest       # Phase 6 load tests (QUICK=1 for CI)
chaos-dryrun   # chaos experiments without cluster
region-dryrun  # multi-region sync controller without cluster
integration    # Phase 6 integration tests
```

## 8. Full Test Status

Sau refactor `trading/` → `src/trading_agent/`, test di dời về `tests/` (conftest chèn src/ vào sys.path).

```
python -m pytest tests/ -q
107 passed      # +7 MetaLearning (MAML/Reptile/MetaSGD/ANIL), +Phase6 integration
```

Verification khác:
- `ruff check .` → All checks passed (format chưa đạt, cosmetic — giữ nguyên)
- import-all smoke: 98/100 module OK (chỉ web3/jaeger optional deps)
- `scripts/benchmark_phase6.py` → online learning 9.2k bars/s, portfolio optimizer 1.3–14ms
- `scripts/load_test_phase6.py --quick` → event store concurrency + throughput OK
