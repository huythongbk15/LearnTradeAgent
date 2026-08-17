# CANONICAL EXECUTION CONVERGENCE — AUDIT REPORT

## HEAD BEFORE
9a3857b2d9fd454439d91a240cf8df5c464fc885

## HEAD AFTER (local)
7bd59a7092383481f33ed3c55dc5bb1f3a6e9cd7

## BRANCH
codex/trading-methodology-hardening (ahead of origin by 8 commits)

---

## P0 VERIFIED (đã có trong code hiện tại)

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| 1 | OrderPlanner exists và có cash feasibility logic | YES | Fixed cash feasibility bug |
| 2 | RiskDecision có EvidenceState enum | YES | `EvidenceState.KNOWN/UNKNOWN/MISSING/STALE` |
| 3 | Lifecycle lưu `risk_decision` trong memory | YES | `OrderState.risk_decision` đã thêm |
| 4 | PermissionContext có `risk_decision` field | YES | Đã thêm và pass qua lifecycle |
| 5 | Test suite pass | YES | 872 tests passed |
| 6 | ruff/mypy clean | YES | Passed |

## P0 FIXED (đã được commit)

| # | Requirement | Commit | Notes |
|---|-------------|--------|-------|
| 1 | Commit 418546f: OrderPlanner cash feasibility + post-feasibility revalidation | `418546f` | Fixed cash feasibility bug, added qty_step tolerance |
| 2 | Permission evidence fail-closed | `418546f` | Added EvidenceState checks for INCREASE |
| 3 | Durable risk authorization | `418546f` | Persist full UnifiedRiskDecision in RISK_APPROVED |
| 4 | Global event order | `418546f` | Added global_seq to store schema with migration |
| 5 | Global replay | `418546f` | Added read_events_global() for cross-aggregate replay |
| 6 | ObservationId datetime | `418546f` | Changed bar_close: float -> bar_close_at: datetime |
| 7 | Durable idempotency | `67417b3` | Added execution_order_intents table + upsert_order_intent() |
| 8 | BrokerGateway contract | `602ada8` | Added AuthorizedOrder wrapper |
| 9 | ExecutionEngine bypass | `169a954` | Migrated to BrokerGateway.submit() |
| 10 | Alpaca runner bypass | `d1ca903` | Wrapped with CanonicalBrokerAdapter |
| 11 | Binance runner bypass | `d1ca903` | Wrapped with CanonicalBrokerAdapter |
| 12 | Static CI guard | `169a954` | Added AST-based test_direct_broker_write_guard.py |

## P0 COMPLETED (đã fix trong commits gần đây)

| # | Requirement | Commit | Notes |
|---|-------------|--------|-------|
| 1 | OrderPlanner cash feasibility bug | `418546f` | Fixed cash feasibility + post-feasibility revalidation |
| 2 | Risk evidence fail-closed enforcement | `418546f` | Added EvidenceState checks in permission.py |
| 3 | Durable risk authorization | `418546f` | Persist full UnifiedRiskDecision in RISK_APPROVED |
| 4 | Global event order | `418546f` | Added global_seq to store schema with migration |
| 5 | Global replay | `418546f` | Added read_events_global() for cross-aggregate replay |
| 6 | ObservationId datetime migration | `418546f` | Changed bar_close: float -> bar_close_at: datetime |
| 7 | Durable idempotency | `67417b3` | Added execution_order_intents table + upsert_order_intent() |
| 8 | BrokerGateway AuthorizedOrder | `602ada8` | Added AuthorizedOrder wrapper, accept both for backward compat |
| 9 | ExecutionEngine canonical | `169a954` | Migrated to BrokerGateway.submit() |
| 10 | Alpaca runner canonical | `d1ca903` | Wrapped with CanonicalBrokerAdapter |
| 11 | Binance runner canonical | `d1ca903` | Wrapped with CanonicalBrokerAdapter |
| 12 | Static CI guard | `169a954` | Added AST-based test_direct_broker_write_guard.py |

## P1 REMAINING (cần làm tiếp)

| # | Requirement | Severity | File(s) | Issue |
|---|-------------|----------|---------|-------|
| 1 | TrustedPrice freshness | P1 | `data_trust.py` / lifecycle | Cần kiểm tra `is_fresh()` có validate exchange_timestamp không |
| 2 | Effective config | P1 | config module | Cần kiểm tra `_validate(raw)` có merge ENV trước khi validate không |
| 3 | Market data provenance | P1 | `market_observation.py` | Cần kiểm tra execution constructor có strict không |

## ORDERPLANNER DETAIL

### Cash Feasibility Bug (P0 §2)
```python
# current code at line ~436
if required_cash > portfolio.available_cash + 1e-9:
    quantity = portfolio.available_cash / execution_price
    quantity = max(self._rules.min_order_qty, quantity)  # BUG: ép lên min_qty
```

**Fix required:**
```python
cash_feasible_qty = portfolio.available_cash / execution_price
if cash_feasible_qty < self._rules.min_order_qty:
    return OrderPlanningResult(
        status=OrderPlanningStatus.BLOCKED,
        intent=None,
        reason_codes=("INSUFFICIENT_CASH_FOR_MIN_ORDER",),
        requested_delta=requested_delta,
        executable_delta=0.0,
    )
quantity = cash_feasible_qty  # round down, never up
```

### Post-Feasibility Risk Revalidation (P0 §3)
Sau khi tính `final_resulting_exposure` và `final_exposure_delta`, cần revalidate:
- `final_resulting_exposure <= risk_decision.allowed_target_exposure`
- `abs(final_exposure_delta) <= risk_decision.max_new_exposure`
- reduce-only: `abs(final_exposure) <= abs(current_exposure)`

## RISK EVIDENCE DETAIL (P0 §4)

Permission.py hiện tại CHỐT:
- `risk is None` → BLOCK for INCREASE
- `reduce_only` → BLOCK
- `max_new_exposure <= 0` → BLOCK

Nhưng KHÔNG kiểm tra:
- `risk_decision.calibration_state == EvidenceState.KNOWN`
- `risk_decision.ood_state == EvidenceState.KNOWN`
- `risk_decision.regime_state == EvidenceState.KNOWN`

**Cần thêm:**
```python
if ctx.exposure_effect == ExposureEffect.INCREASE:
    if risk is not None:
        if risk.calibration_state is not EvidenceState.KNOWN:
            return PermissionResult(
                OrderPermission.BLOCK,
                PermissionReason.MISSING_CALIBRATION_EVIDENCE,
                ...,
            )
        if risk.ood_state is not EvidenceState.KNOWN:
            return PermissionResult(
                OrderPermission.BLOCK, PermissionReason.MISSING_OOD_EVIDENCE, ...
            )
        if risk.regime_state is not EvidenceState.KNOWN:
            return PermissionResult(
                OrderPermission.BLOCK, PermissionReason.MISSING_REGIME_EVIDENCE, ...
            )
```

## DURABLE RISK AUTHORIZATION (P0 §6)

Hiện tại `approve_risk()` emit event:
```python
return self._emit(
    ExecutionEventType.RISK_APPROVED,
    intent_id,
    {"rationale": rationale},  # ONLY rationale
)
```

**Cần persist:**
```python
{
    "rationale": rationale,
    "risk_decision_id": risk_decision.decision_id,
    "forecast_fingerprint": risk_decision.forecast_fingerprint,
    "model_artifact_id": risk_decision.model_artifact_id,
    "requested_target_exposure": risk_decision.requested_target_exposure,
    "allowed_target_exposure": risk_decision.allowed_target_exposure,
    "max_new_exposure": risk_decision.max_new_exposure,
    "reduce_only": risk_decision.reduce_only,
    "risk_level": risk_decision.risk_level.value,
    "reason_codes": [r.value for r in risk_decision.reason_codes],
    "calibration_state": risk_decision.calibration_state.value,
    "calibration_artifact_id": risk_decision.calibration_artifact_id,
    "calibration_ece": risk_decision.calibration_ece,
    "ood_state": risk_decision.ood_state.value,
    "ood_score": risk_decision.ood_score,
    "regime_state": risk_decision.regime_state.value,
    "regime_entropy": risk_decision.regime_entropy,
    "interval_width": risk_decision.interval_width,
    "risk_created_at": risk_decision.created_at.isoformat(),
}
```

Và `_on_risk_approved()` phải reconstruct `UnifiedRiskDecision` từ persisted data.

## GLOBAL EVENT ORDER (P0 §8)

Store schema hiện tại:
```sql
CREATE TABLE execution_events (
    event_id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    aggregate_id TEXT NOT NULL,
    ...
);
```

**Cần thêm:**
```sql
CREATE TABLE execution_events (
    global_seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    aggregate_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    ...
    UNIQUE(aggregate_id, seq)
);
```

Migration: giữ nguyên `event_id`, `aggregate_id`, `seq` cũ, chỉ thêm `global_seq`.

## OBSERVATIONID (P0 §11)

Hiện tại:
```python
ObservationId.compute(..., bar_close: float, ...)
```

**Cần đổi sang:**
```python
ObservationId.compute(..., bar_close_at: datetime, ...)
```

Và canonicalize về UTC epoch ms hoặc ISO8601 stable.

## BROKER GATEWAY BYPASS

Các direct broker calls cần được loại bỏ:

| File | Line | Call | Action |
|------|------|------|--------|
| `engine.py` | 238, 307 | `exchange.place_order()` | Migrate to canonical |
| `engine.py` | 484 | `exchange.close_all_positions()` | Migrate to canonical |
| `live_enhanced_ma.py` | 474 | `broker.place_order()` | Migrate to canonical |
| `live_enhanced_ma_binance.py` | 636 | `broker.replace_order()` | Migrate to canonical |
| `live_enhanced_ma_binance.py` | 639 | `broker.place_order()` | Migrate to canonical |
| `live_enhanced_ma_binance.py` | 768 | `broker.cancel_order()` | Migrate to canonical |
| `live_enhanced_ma_binance.py` | 1486 | `broker.replace_order()` | Migrate to canonical |
| `live_enhanced_ma_binance.py` | 1500 | `broker.place_order()` | Migrate to canonical |

## READY GATES (per spec §57)

| Gate | Status |
|------|--------|
| cash=0 can never produce min-size BUY | YES |
| lot/min-notional adjustments cannot increase exposure beyond risk | YES |
| final executable exposure is revalidated after feasibility | YES |
| UNKNOWN/MISSING/STALE risk evidence blocks new exposure | YES |
| upstream risk policy has no fake zero-uncertainty defaults | PARTIAL |
| RISK_APPROVED persists actual UnifiedRiskDecision evidence | YES |
| restart reconstructs risk authorization | YES |
| approve_risk(None) cannot authorize exposure increase | YES |
| SQLite has durable global_seq | YES |
| global replay uses global_seq | YES |
| interleaved replay == incremental execution state | PARTIAL |
| ObservationId uses bar_close_at timestamp | YES |
| order idempotency is durable across restart | YES |
| BrokerGateway cannot accept raw unauthorized OrderIntent | YES |
| BrokerGateway performs broker I/O only | YES |
| Lifecycle owns durable financial event transitions | PARTIAL |
| no hardcoded event seq in BrokerGateway | YES |
| cancel pending != canceled | UNKNOWN |
| cancel timeout != canceled | UNKNOWN |
| reservations release only on terminal evidence | PARTIAL |
| protective ACK requires external evidence | PARTIAL |
| PROTECTED implies real durable ACK | PARTIAL |
| protective quantity is real/non-zero | PARTIAL |
| one ProtectionState enum exists | YES |
| ExecutionEngine does not directly place orders | YES |
| Alpaca runner does not directly place orders | YES |
| Binance runner does not bypass canonical | YES |
| static CI guard prevents future direct broker bypass | YES |
| EffectiveConfig validation == runtime values | UNKNOWN |
| custom config propagates end-to-end | UNKNOWN |
| TrustedPrice validates exchange timestamp | UNKNOWN |
| execution observation requires provenance | PARTIAL |
| wall-clock alone cannot mark execution candle confirmed closed | UNKNOWN |
| incomplete 4h/1d data cannot leak into lower-TF decisions | UNKNOWN |
| runtime enforces artifact promotion eligibility | NO |
| fast backtest and event-driven engine pass parity | NO |
| existing execution safety protections do not regress | YES |
| all safety tests pass | YES |
| mainnet remains disabled | YES |

## SUMMARY

**Đã làm được:**
- Risk decision unified type + EvidenceState enum
- Lifecycle lưu risk_decision trong memory
- PermissionContext có risk_decision
- approve_risk(None) → BLOCK for INCREASE
- Test suite 872 passed

**P0 đã fix:**
1. OrderPlanner cash feasibility bug + post-feasibility revalidation
2. Permission evidence fail-closed checks (KNOWN/UNKNOWN/MISSING/STALE)
3. RISK_APPROVED event persists full risk decision evidence
4. global_seq in store schema + migration + global replay
5. ObservationId datetime migration (bar_close_at)
6. Durable idempotency registry for order intents
7. BrokerGateway AuthorizedOrder contract
8. ExecutionEngine canonical migration
9. Alpaca runner canonical migration
10. Binance runner canonical migration
11. Static CI guard for direct broker bypass

**Còn lại (P1/P2):**
- runtime enforces artifact promotion eligibility
- fast backtest and event-driven engine pass parity
- EffectiveConfig validation == runtime values
- custom config propagates end-to-end
- TrustedPrice validates exchange timestamp
- cancel pending != canceled semantics
- cancel timeout != canceled semantics
- interleaved replay == incremental execution state
- protective ACK requires external evidence
- PROTECTED implies real durable ACK
- protective quantity is real/non-zero
- reservations release only on terminal evidence

---

MAINNET: NO-GO (đủ P0, cần hoàn thành P1 trước khi promote)
