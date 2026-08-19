# P0 COMPLETED & REMAINING

## Dựa trên: AUDIT_REPORT.md + code hiện tại (commit cd87e22)
- Test suite: 866 passed, 10 skipped
- Live paper: STABLE, 0 orders, DD 5.3%

---

## ✅ P0 COMPLETED (đã có trong code + tests pass)

### 1. OrderPlanner cash feasibility + post-feasibility revalidation
- File: `src/trading_agent/execution/canonical/order_planner.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - Cash feasibility: round DOWN to qty_step, never up
  - INSUFFICIENT_CASH_FOR_MIN_ORDER nếu cash < min_order_qty
  - Post-feasibility revalidation against allowed_target_exposure, max_new_exposure, target

### 2. Permission evidence fail-closed
- File: `src/trading_agent/execution/permission.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - INCREASE: require calibration_state, ood_state, regime_state == KNOWN
  - MISSING_CALIBRATION_EVIDENCE, MISSING_OOD_EVIDENCE, MISSING_REGIME_EVIDENCE → BLOCK

### 3. Durable risk authorization
- File: `src/trading_agent/execution/lifecycle/lifecycle.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - `approve_risk()` persists full `UnifiedRiskDecision` in RISK_APPROVED event
  - `_on_risk_approved()` reconstruct từ persisted payload

### 4. Global event order
- File: `src/trading_agent/execution/lifecycle/store.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - `global_seq` INTEGER PRIMARY KEY AUTOINCREMENT
  - Migration cho existing DBs (global_seq = -1 cho pre-migration)
  - Batch append pre-allocate global_seq

### 5. Global replay
- File: `src/trading_agent/execution/lifecycle/store.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - `read_events_global()` trả về events sorted by global_seq
  - Replay preserves causality across aggregates

### 6. ObservationId datetime migration
- File: `src/trading_agent/execution/canonical/events.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - `ObservationId.compute(..., bar_close_at: datetime, ...)`
  - Validates timezone-aware, dùng isoformat() stable

### 7. Durable idempotency registry
- File: `src/trading_agent/execution/lifecycle/store.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - Table `execution_order_intents` với UNIQUE(idempotency_key)
  - `upsert_order_intent()` returns existing intent_id nếu duplicate

### 8. BrokerGateway AuthorizedOrder contract
- File: `src/trading_agent/execution/canonical/broker_gateway.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - `AuthorizedOrder` wrapper
  - Accept both AuthorizedOrder và legacy dict for backward compat
  - Reject raw unauthorized OrderIntent

### 9. ExecutionEngine canonical migration
- File: `src/trading_agent/execution/engine.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - Không còn direct `exchange.place_order()`
  - Dùng `BrokerGateway.submit()`

### 10. Alpaca runner canonical migration
- File: `src/trading_agent/execution/canonical/cli_adapter.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - Wrapped with CanonicalBrokerAdapter
  - Dùng `gateway.submit()` thay vì direct `place_order()`

### 11. Binance runner canonical migration
- File: `src/trading_agent/execution/canonical/runner_adapter.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - Wrapped with CanonicalBrokerAdapter
  - Dùng `gateway.submit()` thay vì direct `place_order()`

### 12. Static CI guard
- File: `tests/test_direct_broker_write_guard.py`
- Trạng thái: IMPLEMENTED & VERIFIED
- Chi tiết:
  - AST-based scan cho forbidden methods
  - ALLOWED_FILES whitelist
  - Prevents future direct broker bypass

### 13. Additional fixes in this session
- Fix `_getattr__` typo in config loader
- Replace `id(order)` correlation with stable `symbol+side+size`
- `emergency_reduce()` emit `BROKER_SUBMISSION_REQUESTED`
- `emergency_reduce()` risk evidence: calibration_ece/ood/regime = 1.0
- `authorize_order()` rejects caller-supplied `authorization_id`
- WebUI close-all uses durable ExecutionEventStore
- Replace timestamp-based IDs with uuid4

---

## ⏳ P0 REMAINING / PARTIAL (cần verify thêm)

### 1. upstream risk policy has no fake zero-uncertainty defaults
- Trạng thái: PARTIAL
- Chi tiết: Cần verify rằng không có default calibration_ece=0.0, ood_score=0.0, regime_entropy=0.0 trong production code paths

### 2. interleaved replay == incremental execution state
- Trạng thái: PARTIAL
- Chi tiết: `test_replay_matches_incremental` pass, nhưng cần verify thêm với complex multi-intent scenarios

### 3. Lifecycle owns durable financial event transitions
- Trạng thái: PARTIAL
- Chi tiết: Cần verify rằng tất cả financial events (submit, fill, cancel) có durable evidence

### 4. reservations release only on terminal evidence
- Trạng thái: PARTIAL
- Chi tiết: `_release_sell_remainder()` được gọi trong `_on_order_rejected()`, cần verify đầy đủ

### 5. protective ACK requires external evidence
- Trạng thái: PARTIAL
- Chi tiết: Cần verify rằng PROTECTED state không thể reached without external ACK

### 6. PROTECTED implies real durable ACK
- Trạng thái: PARTIAL
- Chi tiết: Tương tự #5

### 7. protective quantity is real/non-zero
- Trạng thái: PARTIAL
- Chi tiết: Cần verify protective order có quantity > 0

### 8. execution observation requires provenance
- Trạng thái: PARTIAL
- Chi tiết: Cần verify market observation có provenance metadata

---

## ❌ P0 NOT YET IMPLEMENTED (NO từ AUDIT_REPORT)

### 1. runtime enforces artifact promotion eligibility
- Trạng thái: NO
- Chi tiết: Chưa có mechanism enforce artifact promotion eligibility

### 2. fast backtest and event-driven engine pass parity
- Trạng thái: NO
- Chi tiết: Chưa verify parity giữa fast backtest và event-driven engine

---

## ℹ️ UNKNOWN / P1 (không phải P0 blocker)

### 1. TrustedPrice validates exchange timestamp
- Trạng thái: UNKNOWN (P1)

### 2. EffectiveConfig validation == runtime values
- Trạng thái: UNKNOWN (P1)

### 3. custom config propagates end-to-end
- Trạng thái: UNKNOWN (P1)

### 4. cancel pending != canceled
- Trạng thái: UNKNOWN

### 5. cancel timeout != canceled
- Trạng thái: UNKNOWN

### 6. wall-clock alone cannot mark execution candle confirmed closed
- Trạng thái: UNKNOWN

### 7. incomplete 4h/1d data cannot leak into lower-TF decisions
- Trạng thái: UNKNOWN

---

## 📊 SUMMARY

- **P0 COMPLETED**: 12 core items + additional fixes = ✅
- **P0 PARTIAL**: 8 items cần verify thêm
- **P0 NOT IMPLEMENTED**: 2 items
- **UNKNOWN/P1**: 7 items

## 🚦 MAINNET GATE

- **Current**: NO-GO
- **Blocker**: P0 partial items + P0 not implemented + P1 convergence
- **Next**: Verify P0 partial items, implement P0 not implemented, complete P1 convergence
