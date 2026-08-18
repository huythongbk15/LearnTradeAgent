# P0 Execution Map — canonical execution pipeline inventory

## Canonical path (required)
MarketObservation → Forecast / LegacyForecastAdapter → UnifiedRiskDecision → TargetExposure → OrderPlanner → OrderPermission → ExecutionLifecycle → BrokerGateway → Exchange/Broker

## Current paths inventory

### 1. ExecutionEngine.execute_signal()
- Source decision: AgentMessage (Phase 2 signal)
- Risk decision: NONE (bypasses UnifiedRiskDecision)
- Planner: NONE (direct quantity calc in engine)
- Permission: NONE
- Lifecycle: NONE
- Gateway: YES (BrokerGateway.submit())
- Direct broker call: NO (but bypasses canonical risk/permission/lifecycle)
- Durable event: NO
- Reservation release: NO
- Protection: Direct mutation of pos.stop_loss/pos.take_profit after fill
- **Status: BYPASS — self-authorizing**

### 2. scripts/live_enhanced_ma.py
- Source decision: MA crossover signal
- Risk decision: Runner internal checks
- Planner: Runner internal sizing
- Permission: Runner internal checks
- Lifecycle: NONE
- Gateway: Via CanonicalBrokerAdapter (wrapper)
- Direct broker call: YES (broker.place_order(), broker.cancel_order(), broker.replace_order())
- Durable event: Partial (via adapter but adapter fabricates AuthorizedOrder)
- **Status: BYPASS — runner self-authorizes via _to_authorized()**

### 3. scripts/live_enhanced_ma_binance.py
- Source decision: MA crossover signal
- Risk decision: Runner internal checks
- Planner: Runner internal sizing
- Permission: Runner internal checks
- Lifecycle: NONE
- Gateway: Via CanonicalBrokerAdapter (wrapper)
- Direct broker call: YES (broker.place_order(), broker.cancel_order(), broker.replace_order())
- Durable event: Partial
- **Status: BYPASS — runner self-authorizes via _to_authorized()**

### 4. CanonicalBrokerAdapter
- Accepts: legacy Order
- Converts: _to_authorized() fabricates AuthorizedOrder
- Gateway: YES
- **Status: BYPASS — fabricates authorization**

### 5. BrokerGateway
- Accepts: AuthorizedOrder | OrderIntent (backward compat)
- Event emission: YES (ORDER_SUBMITTED, ORDER_REJECTED, CANCEL_REQUESTED, CANCEL_CONFIRMED, PROTECTIVE_ORDER_CREATED, PROTECTIVE_ORDER_ACKNOWLEDGED)
- Event seq: Hardcoded seq=1, seq=2
- Broker I/O: YES
- Lifecycle ownership: NO (emits its own events)
- **Status: BYPASS — emits financial lifecycle events, allocates seq**

### 6. ExecutionLifecycle
- load(): uses read_events() → sorts by aggregate_id, seq (NOT global_seq)
- replay(): sorts by (aggregate_id, seq) (NOT global_seq)
- Financial transitions: YES (owns state machine)
- Event writing: BrokerGateway writes events, not lifecycle
- **Status: PARTIAL — replay not global, event ownership split**

### 7. ExecutionEventStore
- global_seq: INTEGER NOT NULL DEFAULT 0 (not UNIQUE, not >0 enforced)
- Migration: backfills using ORDER BY aggregate_id, seq (fabricates history)
- append_batch(): omits global_seq → DEFAULT 0
- read_events_global(): exists but not used by lifecycle.load()
- **Status: BROKEN — global_seq not transactionally unique, migration fabricates order**

### 8. OrderPermission
- Permission evaluation: YES (centralized)
- Used by: NONE in current execution paths (Engine and runners bypass)
- **Status: BYPASS — not integrated into actual execution flow**

### 9. OrderPlanner
- TargetExposure → OrderIntent: YES
- Used by: NONE in current execution paths
- **Status: BYPASS — not integrated into actual execution flow**

### 10. ProtectionPlan
- Quantity semantics: qty=0 magic semantics in BrokerGateway
- Broker evidence: PROTECTIVE_ORDER_ACKNOWLEDGED emitted by gateway without real broker evidence validation
- **Status: BYPASS — magic qty=0, weak evidence**

### 11. Durable idempotency
- Table exists: execution_order_intents with UNIQUE(idempotency_key)
- upsert_order_intent(): exists
- Used in submission: NO (BrokerGateway does not check before submit)
- **Status: BYPASS — exists but not used**

## Required fixes (P0)

### Global event sequence
- [ ] Schema: global_seq INTEGER PRIMARY KEY AUTOINCREMENT (or UNIQUE + transactionally allocated)
- [ ] Migration: do NOT fabricate historical order; declare boundary or use rowid evidence
- [ ] append(): assign global_seq transactionally
- [ ] append_batch(): assign global_seq for each event in batch
- [ ] No global_seq=0 after migration/insert

### Global replay
- [ ] Lifecycle.load() → store.read_events_global()
- [ ] Lifecycle.replay_global() → preserve global_seq order, no aggregate sort
- [ ] Lifecycle.replay() → per-aggregate replay (separate API)
- [ ] Tests: interleaved aggregates → incremental == restart

### Lifecycle ownership
- [ ] BrokerGateway emits NO financial lifecycle events
- [ ] BrokerGateway allocates NO event seq
- [ ] Lifecycle is sole writer of financial state transitions
- [ ] Lifecycle interprets broker facts → persists events

### BrokerGateway contract
- [ ] Accept ONLY AuthorizedOrder (reject OrderIntent)
- [ ] Broker I/O only (submit, cancel, fetch, protection submit)
- [ ] Return typed facts (BrokerSubmitResult, CancelResult, ProtectiveSubmitResult)
- [ ] No event emission
- [ ] No seq allocation

### Authorization integrity
- [ ] AuthorizedOrder unforgeable (private constructor / factory in lifecycle)
- [ ] Required fields: intent_id, idempotency_key, risk_decision_id, forecast_fingerprint, model_artifact_id, permission_result, authorization_id, lifecycle_event_id, correlation_id, symbol, side, quantity, exposure_effect, current_exposure, resulting_exposure, authorized_at, authorization_hash
- [ ] Gateway verifies structure/integrity before broker submit

### ExecutionEngine
- [ ] No self-authorization (no direct AuthorizedOrder construction)
- [ ] No independent final sizing (TargetExposure → exact quantity)
- [ ] No direct position protection mutation (use ProtectionPlan)
- [ ] Canonical path: AgentMessage → LegacyAgentForecastAdapter → Forecast → UnifiedRiskDecision → TargetExposure → OrderPlanner → Permission → Lifecycle → BrokerGateway

### Runners
- [ ] No _to_authorized() fabrication
- [ ] No direct legacy fallback (replace_order)
- [ ] Canonical path: runner operational checks → canonical risk → planner → permission → lifecycle → gateway

### Cancel terminal evidence
- [ ] Typed CancelState (REQUEST_ACCEPTED, PENDING, CANCELED, FILLED, REJECTED, EXPIRED, UNKNOWN, FAILED)
- [ ] No exception == canceled
- [ ] Lifecycle decides terminal transition
- [ ] confirm_cancel() requires typed CancelEvidence
- [ ] No fail-open confirm_cancel()

### Reservation release
- [ ] Release ONLY on terminal evidence (CANCELED, FILLED, REJECTED, EXPIRED)
- [ ] PENDING/UNKNOWN/REQUEST_ACCEPTED → keep locked
- [ ] Terminal evidence typed and durable

### Protective evidence
- [ ] PROTECTIVE_ORDER_ACKNOWLEDGED requires real broker evidence
- [ ] Typed ProtectiveAckEvidence (broker_order_id, venue, status, acknowledged_at, etc.)
- [ ] PROTECTED implies durable broker/reconciliation evidence
- [ ] No qty=0 magic semantics (explicit CLOSE_POSITION or EXPLICIT_QUANTITY)

### Durable idempotency
- [ ] Lifecycle atomically reserves idempotency key before broker submission
- [ ] Duplicate key → return existing intent/result
- [ ] No timestamp-based logical identity
- [ ] Cross-process test: same intent, 1 broker submit

### ObservationId
- [ ] bar_close_at normalized to UTC before hashing
- [ ] Naive datetime rejected
- [ ] Equivalent instants → same observation ID

### Static guard
- [ ] Scan production scripts (live_enhanced_ma*.py, CLI paths)
- [ ] No unsafe allowlist hiding runtime bypass
