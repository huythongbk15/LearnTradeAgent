# Toàn bộ luồng chạy một lệnh trade (End-to-End)

## Tổng quan kiến trúc

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│                         EXECUTION ENGINE (src/trading_agent/execution/engine.py) │
│                                                                                  │
│  Phase 2 (Signals) ──────────▶ Canonical Pipeline ──────────▶ Phase 3 (Execution) │
│       AgentMessage          LegacyAdapter → Risk → Planner → Lifecycle → Gateway   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

---

## 1. ENTRY POINT: `ExecutionEngine.execute_signal()`

**File**: `src/trading_agent/execution/engine.py:301`

```python
def execute_signal(
    self, signal: AgentMessage, observation: EnrichedMarketObservation | None = None
) -> list[Order]:
```

### Input validation
| Kiểm tra | Fail action |
|----------|-------------|
| `instrument_rules` provided | `RuntimeError` |
| `signal.signal` = HOLD | Return `[]` (no action) |
| Symbol trong `signal.details` | Return `[]` (warning) |
| Price từ `_get_current_price()` | Return `[]` (warning) |
| `observation` is None | Return `[]` (warning) |
| `observation.is_closed` = False | Return `[]` (warning) |

### Protective order sync (trước khi plan)
```python
self._sync_protective_orders()  # Dọn dẹp protective orders của positions đã đóng
```

---

## 2. LEGACY ADAPTER: `AgentMessage → UnifiedRiskDecision + TargetExposure`

**File**: `src/trading_agent/execution/canonical/legacy_adapter.py`

```
AgentMessage (Trader signal)
        │
        ▼
┌───────────────────┐
│ LegacyDecisionAdapter.adapt() │
│  - Parse symbol, side, qty    │
│  - Validate BACKTEST_ALLOW_NEW_EXPOSURE env   │
│  - Build UnifiedRiskDecision  │
│  - Build TargetExposure       │
└───────────────────┘
        │
        ▼
risk_decision: UnifiedRiskDecision  {decision_id, allowed_target_exposure, ...}
target:        TargetExposure       {symbol, exposure, horizon, ...}
```

### UnifiedRiskDecision fields quan trọng:
- `decision_id`, `forecast_fingerprint`, `model_artifact_id`
- `requested_target_exposure`, `allowed_target_exposure`, `max_new_exposure`
- `reduce_only`, `risk_level` (LOW/MEDIUM/HIGH/EXTREME)
- `calibration_state`, `ood_state`, `regime_state` + evidence scores
- `interval_width` (prediction interval)

---

## 3. PORTFOLIO STATE & MARKET PRICE (Canonical Inputs)

```python
portfolio = CurrentPortfolioState(
    symbol=symbol,
    current_exposure=current_notional / equity,
    equity=equity,
    existing_quantity=current_qty,
    available_cash=usdt_balance,
)

price = MarketPrice(
    symbol=symbol,
    mid=current_price, bid=current_price, ask=current_price, last=current_price
)
```

**Data source**: `PaperExchange` (paper trading) — protected by `_state_lock`

---

## 4. ORDER PLANNING: `CanonicalExecutionService.plan()`

**File**: `src/trading_agent/execution/application.py:76` → `OrderPlanner.plan()`

**Input**: `target`, `risk_decision`, `observation`, `portfolio`, `price`, `existing_reservations`

**Output**: `OrderPlanningResult`
```python
OrderPlanningResult(
    status=OrderPlanningStatus.ORDER_REQUIRED | NO_ORDER | REDUCE_ONLY | BLOCKED,
    intent=OrderIntent | None,
    reason_codes=(),
    requested_delta=...,
    executable_delta=...,
)
```

### OrderPlanner logic (`src/trading_agent/execution/canonical/order_planner.py`):
1. **Sizing**: từ `target.exposure` × `portfolio.equity` → `target_notional`
2. **Delta**: `target_notional - current_notional` → `requested_delta`
3. **Risk clamp**: `max_new_exposure`, `reduce_only` từ `risk_decision`
4. **Instrument rules**: `min_order_qty`, `qty_step`, `min_notional`, `price_precision`
5. **Reservations**: trừ `existing_reservations` (sell protective orders pending)

**Fail cases**:
- `delta ≈ 0` → `NO_ORDER`
- `reduce_only=True` + `delta > 0` → `REDUCE_ONLY`
- `delta < min_notional` → `BLOCKED` (reason: `MIN_NOTIONAL`)
- Precision step violation → `BLOCKED`

---

## 5. PERMISSION CHECK: `CanonicalExecutionService.evaluate_permission()`

**File**: `src/trading_agent/execution/permission.py` → `evaluate_order_permission()`

**Input**: `PermissionContext` (đầy đủ context để quyết định)

### PermissionContext fields quan trọng:
| Field | Nguồn | Ý nghĩa |
|-------|-------|---------|
| `execution_health` | Lifecycle state | HEALTHY/DEGRADED/PROTECTION_GAP/RECONCILIATION_BLOCKED |
| `exposure_effect` | Intent side | INCREASE (buy) / REDUCE (sell) |
| `risk_decision` | Planned risk decision | `reduce_only`, `max_new_exposure` |
| `trusted_price` | Engine price cache | Must have `exchange_timestamp` |
| `max_price_age_seconds` | Config | 60s default |
| `reconciliation_state` | Lifecycle | IDLE/ACTIVE/BLOCKED |
| `protection_state` | Lifecycle | NONE/PROTECTION_REQUIRED/ACTIVE |
| `manual_blocked` | Lifecycle | Manual intervention flag |
| `kill_switch_active` | ENV `TRADING_KILL_SWITCH` | Emergency stop |
| `data_trust` | Market data | TRUSTED/DEGRADED/UNTRUSTED |
| `inventory_state` | Broker | KNOWN/UNKNOWN |
| `free_inventory` | Portfolio | Cash (buy) hoặc Qty (sell) |
| `authorized_sellable_inventory` | Portfolio | Qty thực tế có thể bán |
| `order_size`, `order_side` | Intent | Kích thước & hướng lệnh |
| `require_fresh_market_data` | Config | True = reject stale price |
| `enforce_inventory` | Config | True = check free inventory |

### PermissionResult:
```python
PermissionResult(
    decision=PermissionDecision.ALLOW | REDUCE_ONLY | BLOCK,
    reason=PermissionReason.EXPOSURE_LIMIT | STALE_PRICE | PROTECTION_GAP | ...,
    detail="...",
)
```

**Chặn lệnh nếu**:
- `kill_switch_active` → `BLOCK` (EMERGENCY_STOP)
- `execution_health != HEALTHY` → `BLOCK`
- `manual_blocked` → `BLOCK`
- `protection_state == PROTECTION_REQUIRED` + `exposure_effect == INCREASE` → `BLOCK`
- `reconciliation_state == BLOCKED` → `BLOCK`
- Stale price (>60s) → `BLOCK`
- `free_inventory` < `order_size` → `BLOCK` (INSUFFICIENT_INVENTORY)
- `reduce_only` + `exposure_effect == INCREASE` → `REDUCE_ONLY`
- `exposure_effect == INCREASE` + `current_exposure >= max_new_exposure` → `BLOCK`

---

## 6. SELL PRE-PROCESSING: Cancel Protective Orders

**File**: `src/trading_agent/execution/engine.py:434`

```python
if intent.side.lower() == "sell":
    cancel_ok, canceled_protection_intents = self._cancel_resting_protection(symbol)
    if not cancel_ok:
        return orders  # Block sell nếu không cancel được protective
```

**Quy trình cancel**:
1. `_sync_protective_orders(symbol)` — reconcile trạng thái protective orders
2. Với mỗi protective intent còn `remaining_reserved_quantity > 0`:
   - `gateway.cancel(broker_order_id, correlation_id)` → `CancelEvidence`
   - `lifecycle.cancel_protective_order(intent_id, evidence)`
3. Nếu cancel fail → `require_manual_intervention` → return `False`

---

## 7. ORDER SUBMISSION: `CanonicalExecutionService.submit_planned()`

**File**: `src/trading_agent/execution/application.py:123`

### 7.1 Pre-submit validation
- `planning.status == ORDER_REQUIRED`
- `intent` not None
- `permission_context.risk_decision is risk_decision` (same object)
- `permission_context.order_side == intent.side`
- `permission_context.order_size == intent.quantity`

### 7.2 Lifecycle state machine (durable events)
```python
# Tạo intent trong lifecycle
lifecycle.create_order_intent(intent_id, symbol, side, size, idempotency_key)

# Approve risk (ghi nhận risk decision)
lifecycle.approve_risk(intent_id, risk_decision)

# Authorize (tạo Authorization payload + payload_hash)
authorization = lifecycle.authorize_order(intent_id, idempotency_key, metadata)

# Request broker submission (claim submission ownership)
lifecycle.request_broker_submission(intent_id, claimed_by=intent_id)

# Submit qua gateway
result = gateway.submit(authorization_id, correlation_id)
```

### 7.3 BrokerGateway.submit() → PaperExecutionAdapter

**File**: `src/trading_agent/execution/canonical/broker_gateway.py:74`

```python
def submit(self, authorization_id: str, correlation_id: str) -> BrokerSubmitResult:
    # 1. Fetch authorization từ lifecycle
    auth = self.lifecycle.get_authorization(authorization_id)
    
    # 2. Verify claim ownership (prevent double-submit)
    if auth.claimed_by != correlation_id:
        raise ExecutionBlockedError("Submission claim mismatch")
    
    # 3. Build order from authorization payload
    order = Order(...)
    
    # 4. Submit to adapter (PaperExecutionAdapter)
    submit_fact = self.adapter.submit_order(order, correlation_id)
    
    # 5. Return BrokerSubmitResult preserving state (including UNKNOWN)
    return BrokerSubmitResult(
        success=submit_fact.state in ACCEPTED_STATES,
        broker_order_id=submit_fact.broker_order_id,
        state=submit_fact.state,  # ACCEPTED/OPEN/PARTIALLY_FILLED/FILLED/REJECTED/UNKNOWN
        error=submit_fact.error,
        raw_response=submit_fact.raw_response,
    )
```

### 7.4 Record broker fact (durable)
```python
# In CanonicalExecutionService._submit_authorization()
event = self.lifecycle.record_broker_submit_result(
    intent_id,
    self._as_broker_fact(intent_id, result),
)
```

---

## 8. POST-SUBMIT: Protective Order Placement (BUY only)

**File**: `src/trading_agent/execution/engine.py:457`

```python
if fill_received and intent.side.lower() == "buy":
    # Lấy quantity thực tế từ paper exchange (account fees/slippage)
    position = self.exchange.get_position(symbol)
    protected_quantity = position.quantity
    
    # Tạo protective plan (stop_loss 5%, take_profit 10%)
    plan = ProtectionPlan(
        symbol=symbol,
        stop_trigger=current_price * 0.95,
        take_profit=current_price * 1.10,
        protected_quantity=protected_quantity,
    )
    
    # Tạo protective intent trong lifecycle
    protective_event = lifecycle.create_protective_order(...)
    
    # Submit qua emergency_protection (bypass normal permission)
    protection_result = execution_service.emergency_protection(
        EmergencyReduceRequest(
            intent_id=protection_intent_id,
            symbol=symbol,
            side="sell",
            quantity=protected_quantity,
            reason="PROTECTIVE_STOP",
            parent_intent_id=intent.intent_id,
        )
    )
    
    # Acknowledge protective order
    if protection_result.success:
        lifecycle.acknowledge_protective_order(protective_event.aggregate_id, evidence)
    else:
        lifecycle.require_manual_intervention(...)
```

---

## 9. RESULT CONVERSION: `BrokerSubmitResult → Order`

**File**: `src/trading_agent/execution/engine.py:550` → `_result_to_order()`

```python
def _result_to_order(self, result, symbol, side, quantity) -> Order:
    return Order(
        id=result.broker_order_id or f"local-{intent_id}",
        symbol=symbol,
        side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
        type=OrderType.MARKET,
        quantity=quantity,
        status=OrderStatus.FILLED if result.state == FILLED else OrderStatus.OPEN,
        filled_quantity=...,
        avg_fill_price=...,
    )
```

---

## 10. FILL RECONCILIATION (Async/Background)

### PaperExchange auto-fill
- `PaperExchange.submit_order()` → fills immediately at `last_price_cache`
- Fills recorded in `paper_exchange._state` + `paper_exchange._orders`

### Reconciliation paths:
1. **Immediate fill** (paper): broker returns `FILLED` → lifecycle `receive_fill()`
2. **Async reconciliation**: `_record_protective_fill()` fetches broker status
3. **Periodic**: `ExecutionEngine.reconcile_all()` (not shown, external cron)

---

## 11. STATE MACHINE: ExecutionLifecycle (Durable Events)

**File**: `src/trading_agent/execution/lifecycle/lifecycle.py`

### Intent states (IntentStatus):
```
PENDING → APPROVED → AUTHORIZED → SUBMITTED → ACKNOWLEDGED
                              ↓
                        PARTIALLY_FILLED → FILLED
                              ↓
                        CANCEL_REQUESTED → CANCELED
                              ↓
                        REJECTED
```

### Key events persisted (append-only):
| Event Type | Khi nào |
|------------|---------|
| `INTENT_CREATED` | `create_order_intent()` |
| `RISK_APPROVED` | `approve_risk()` |
| `ORDER_AUTHORIZED` | `authorize_order()` |
| `SUBMISSION_CLAIMED` | `request_broker_submission()` |
| `BROKER_SUBMIT_REQUESTED` | `gateway.submit()` |
| `BROKER_SUBMIT_RESULT` | `record_broker_submit_result()` |
| `FILL_RECEIVED` | `receive_fill()` |
| `RECONCILIATION_APPLIED` | `record_reconciled_broker_submit_result()` |
| `PROTECTIVE_ORDER_CREATED` | `create_protective_order()` |
| `PROTECTIVE_ORDER_ACKNOWLEDGED` | `acknowledge_protective_order()` |
| `MANUAL_INTERVENTION_REQUIRED` | `require_manual_intervention()` |

### Snapshot/Replay:
- `lifecycle.snapshot()` → JSON state (orders, intents, positions, protective)
- `lifecycle.replay(events)` → rebuild state from event log
- Global sequence (`global_seq`) invariant: >0 for new events, =-1 for legacy

---

## 12. ERROR HANDLING & ROLLBACK

| Failure point | Rollback action |
|---------------|-----------------|
| Permission blocked | No lifecycle events created (pre-auth) |
| `ExecutionBlockedError` in submit | `_mark_protection_gap()` nếu protective đã cancel |
| Protective order submit fail | `require_manual_intervention(intent_id)` |
| Broker returns `UNKNOWN` | State = OPEN, no resubmit (P0-2) |
| Double-submit claim race | `claim_submission()` returns False → block |
| Reconciliation fail | `reconciliation_state = BLOCKED` |

---

## 13. LIVE PAPER TRADING RUNNER

**File**: `scripts/live_cron_runner.py`

```bash
python scripts/live_cron_runner.py --execute
```

### Flow:
```
cron (hourly)
    │
    ▼
live_cron_runner.py
    │
    ├─── 1. Alpaca API: fetch prices (BTC, SOL, AVAX)
    ├─── 2. Compute Enhanced MA signals (MA10 vs MA60, ADX filter)
    ├─── 3. Compare strategy state vs Alpaca positions
    ├─── 4. Build AgentMessage (BUY/SELL/HOLD)
    ├─── 5. Build EnrichedMarketObservation (closed bar)
    ├─── 6. ExecutionEngine.execute_signal()
    └─── 7. Telegram alert nếu có fill/fail
```

---

## 14. SUMMARY: Complete Flow Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                           COMPLETE TRADE FLOW                                     │
└──────────────────────────────────────────────────────────────────────────────────┘

  CRON / USER
      │
      ▼
┌─────────────────────┐
│  AgentMessage       │  ← Trader agent signal (BUY/SELL/HOLD)
│  EnrichedMarketObs  │  ← Closed bar from data layer
└─────────┬───────────┘
          │
          ▼
┌─────────────────────────────────────────────────────────────────────────────────┐
│ ExecutionEngine.execute_signal()                                                │
│   ├─ Validate inputs (instrument_rules, symbol, price, observation)             │
│   ├─ _sync_protective_orders()                                                  │
│   ├─ LegacyDecisionAdapter.adapt() → risk_decision + target                     │
│   ├─ Build CurrentPortfolioState + MarketPrice                                  │
│   ├─ CanonicalExecutionService.plan()                                           │
│   │     └─ OrderPlanner.plan() → OrderIntent (qty, side, price_ref, idempotency)│
│   ├─ PermissionContext → evaluate_order_permission()                            │
│   │     └─ ALLOW / REDUCE_ONLY / BLOCK (with reason)                            │
│   ├─ IF SELL: _cancel_resting_protection() → CancelEvidence                     │
│   ├─ CanonicalExecutionService.submit_planned()                                 │
│   │     ├─ lifecycle.create_order_intent()       ──▶ INTENT_CREATED             │
│   │     ├─ lifecycle.approve_risk()              ──▶ RISK_APPROVED              │
│   │     ├─ lifecycle.authorize_order()           ──▶ ORDER_AUTHORIZED           │
│   │     ├─ lifecycle.request_broker_submission() ──▶ SUBMISSION_CLAIMED         │
│   │     ├─ gateway.submit(authorization_id)                                 │
│   │     │     ├─ PaperExecutionAdapter.submit_order()                          │
│   │     │     └─ Returns BrokerSubmitResult (state: ACCEPTED/OPEN/FILLED/UNKNOWN)│
│   │     └─ lifecycle.record_broker_submit_result() ──▶ BROKER_SUBMIT_RESULT     │
│   ├─ IF BUY + fill_received:                                                    │
│   │     ├─ Create ProtectionPlan (stop_loss 5%, take_profit 10%)               │
│   │     ├─ lifecycle.create_protective_order() ──▶ PROTECTIVE_ORDER_CREATED     │
│   │     ├─ execution_service.emergency_protection()                            │
│   │     │     ├─ lifecycle.emergency_reduce()                                  │
│   │     │     └─ gateway.submit_protection()                                   │
│   │     └─ lifecycle.acknowledge_protective_order() ──▶ PROTECTIVE_ORDER_ACK    │
│   └─ Return Order[] (filled/OPEN)                                               │
└─────────┬───────────────────────────────────────────────────────────────────────┘
          │
          ▼
    PERSISTED STATE (SQLite: events.db)
    ┌─────────────────────────────────────┐
    │  ExecutionLifecycle.state           │
    │  - orders: Dict[intent_id, OrderState]    │
    │  - intents: Dict[intent_id, IntentState]  │
    │  - protective_orders: Dict[...]           │
    │  - execution_health, reconciliation, etc. │
    └─────────────────────────────────────┘
          │
          ▼
    REPLAY CAPABILITY
    lifecycle.replay(all_events) → rebuild exact state
```

---

## 15. KEY INVARIANTS (P0 Execution Safety)

| Invariant | Enforced where |
|-----------|----------------|
| Single submission per intent | `request_broker_submission()` + claim check |
| Broker UNKNOWN = OPEN (not REJECTED) | `gateway.submit()` preserves state |
| Idempotency key = intent_id | `OrderIntent.idempotency_key` |
| Authorization binds risk_decision + payload_hash | `authorize_order()` |
| No synthetic fills | `lifecycle.receive_fill()` only from broker fact |
| Global_seq > 0 for new events | `store._SCHEMA` CHECK constraint |
| Snapshot round-trip all fields | `lifecycle.snapshot()` includes risk, auth, exposure |
| Replay isolation (no snapshot writes) | `_in_replay` flag in lifecycle |

---

## 16. CONFIGURATION POINTS

| Config | File | Purpose |
|--------|------|---------|
| `instrument_rules` | `OrderPlanner` constructor | Min qty, step, precision, min notional |
| `max_price_age_seconds` | `PermissionContext` | Stale price threshold (default 60s) |
| `require_fresh_market_data` | `PermissionContext` | Reject if no fresh data |
| `enforce_inventory` | `PermissionContext` | Check free inventory |
| `BACKTEST_ALLOW_NEW_EXPOSURE` | ENV | Allow new exposure in backtest |
| `TRADING_KILL_SWITCH` | ENV | Emergency stop all trading |
| `LIVE_MAX_DUST_USD` | ENV | Dust threshold for risk-reducing sells |

---

## 17. TESTING ENTRY POINTS

| Test | File | Covers |
|------|------|--------|
| Full e2e paper flow | `tests/execution/test_e2e_paper_flow.py` | Signal → fill |
| P0 convergence | `tests/test_p0_convergence.py` | Canonical pipeline |
| Execution lifecycle | `tests/test_execution_lifecycle.py` | State machine, replay |
| Execution hardening | `tests/test_execution_hardening.py` | P0 invariants |
| Permission logic | `tests/test_order_permission.py` | All block/allow cases |

---

**Generated**: 2026-08-24 | **System**: Trading Agent System | **Status**: Paper trading operational