# CANONICAL EXECUTION CONVERGENCE — AUDIT REPORT

## HEAD BEFORE
9a3857b2d9fd454439d91a240cf8df5c464fc885

## HEAD AFTER (local)
80378f5528001f6ca582842ab4b35d75955e66ec

## BRANCH
codex/trading-methodology-hardening (ahead of origin by 1 commit)

---

## P0 VERIFIED (đã có trong code hiện tại)

| # | Requirement | Status | Notes |
|---|-------------|--------|-------|
| 1 | OrderPlanner exists và có cash feasibility logic | PARTIAL | Có nhưng có bug cash feasibility |
| 2 | RiskDecision có EvidenceState enum | YES | `EvidenceState.KNOWN/UNKNOWN/MISSING/STALE` |
| 3 | Lifecycle lưu `risk_decision` trong memory | YES | `OrderState.risk_decision` đã thêm |
| 4 | PermissionContext có `risk_decision` field | YES | Đã thêm và pass qua lifecycle |
| 5 | Test suite pass | YES | 861 tests passed |
| 6 | ruff/mypy clean | YES | Passed |

## P0 FIXED (đã được commit)

| # | Requirement | Commit | Notes |
|---|-------------|--------|-------|
| 1 | Commit 3: refactor/permission-unified-risk | `ff4f8ed` | PermissionContext.risk_decision, missing risk → BLOCK for INCREASE |
| 2 | OrderState lưu risk_decision | `ff4f8ed` | Cho approve_risk/submit |
| 3 | validate_order_risk nhận risk_decision | `ff4f8ed` | Live paths pass qua permission |

## P0 NOT FIXED (cần làm tiếp)

| # | Requirement | Severity | File(s) | Issue |
|---|-------------|----------|---------|-------|
| 1 | OrderPlanner cash feasibility bug | P0 | `order_planner.py:436-440` | Khi `available_cash/price < min_order_qty`, code ép `quantity = max(min_qty, cash_qty)` → BUY vượt cash |
| 2 | Post-feasibility risk revalidation | P0 | `order_planner.py` | Sau khi điều chỉnh feasibility, không revalidate `final_exposure <= allowed_target_exposure` và `final_delta <= max_new_exposure` |
| 3 | Risk evidence fail-closed enforcement | P0 | `permission.py` | Chưa kiểm tra `calibration_state==KNOWN`, `ood_state==KNOWN`, `regime_state==KNOWN` trước khi cho INCREASE |
| 4 | Durable risk authorization | P0 | `lifecycle.py`, `store.py` | `approve_risk()` chỉ persist `rationale`, không persist full `UnifiedRiskDecision` evidence |
| 5 | Global event order | P0 | `store.py` | Thiếu `global_seq INTEGER PRIMARY KEY AUTOINCREMENT` |
| 6 | Global replay | P0 | `store.py` | `read_events()` dùng `ORDER BY aggregate_id, seq` |
| 7 | ObservationId dùng bar_close datetime | P0 | `events.py:77` | Hiện tại dùng `bar_close: float` (giá), phải đổi sang `bar_close_at: datetime` |
| 8 | Durable idempotency | P0 | Không có | Không có persistent intent registry / idempotency table |
| 9 | BrokerGateway contract | P0 | `broker_gateway.py` | Cần kiểm tra xem có chấp nhận raw `OrderIntent` không |
| 10 | ExecutionEngine bypass | P0 | `engine.py:238,307` | Vẫn gọi `exchange.place_order()` trực tiếp |
| 11 | Alpaca runner bypass | P0 | `scripts/live_enhanced_ma.py:474` | Vẫn gọi `broker.place_order()` trực tiếp |
| 12 | Binance runner bypass | P0 | `scripts/live_enhanced_ma_binance.py:636,639,768,...` | Vẫn gọi `broker.replace_order()`, `broker.place_order()`, `broker.cancel_order()` |
| 13 | TrustedPrice freshness | P1 | `data_trust.py` / lifecycle | Cần kiểm tra `is_fresh()` có validate exchange_timestamp không |
| 14 | Effective config | P1 | config module | Cần kiểm tra `_validate(raw)` có merge ENV trước khi validate không |
| 15 | Market data provenance | P1 | `market_observation.py` | Cần kiểm tra execution constructor có strict không |

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
| cash=0 can never produce min-size BUY | NO |
| lot/min-notional adjustments cannot increase exposure beyond risk | NO |
| final executable exposure is revalidated after feasibility | NO |
| UNKNOWN/MISSING/STALE risk evidence blocks new exposure | NO |
| upstream risk policy has no fake zero-uncertainty defaults | PARTIAL |
| RISK_APPROVED persists actual UnifiedRiskDecision evidence | NO |
| restart reconstructs risk authorization | NO |
| approve_risk(None) cannot authorize exposure increase | YES |
| SQLite has durable global_seq | NO |
| global replay uses global_seq | NO |
| interleaved replay == incremental execution state | NO |
| ObservationId uses bar_close_at timestamp | NO |
| order idempotency is durable across restart | NO |
| BrokerGateway cannot accept raw unauthorized OrderIntent | UNKNOWN |
| BrokerGateway performs broker I/O only | UNKNOWN |
| Lifecycle owns durable financial event transitions | PARTIAL |
| no hardcoded event seq in BrokerGateway | UNKNOWN |
| cancel pending != canceled | UNKNOWN |
| cancel timeout != canceled | UNKNOWN |
| reservations release only on terminal evidence | PARTIAL |
| protective ACK requires external evidence | PARTIAL |
| PROTECTED implies real durable ACK | PARTIAL |
| protective quantity is real/non-zero | PARTIAL |
| one ProtectionState enum exists | UNKNOWN |
| ExecutionEngine does not directly place orders | NO |
| Alpaca runner does not directly place orders | NO |
| Binance runner does not bypass canonical | NO |
| static CI guard prevents future direct broker bypass | NO |
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
- Test suite 861 passed

**Chưa làm được (P0 blockers):**
1. OrderPlanner cash feasibility bug + post-feasibility revalidation
2. Permission chưa enforce evidence state (KNOWN/UNKNOWN/MISSING/STALE)
3. RISK_APPROVED event chưa persist full risk decision evidence
4. SQLite thiếu global_seq và global replay order
5. ObservationId vẫn dùng bar_close price thay vì datetime
6. 3 runner/engine vẫn bypass canonical flow (direct broker calls)
7. Thiếu durable idempotency registry
8. Thiếu static CI guard cho direct broker calls

**Kế hoạch đề xuất:**
1. Fix OrderPlanner cash feasibility + post-feasibility revalidation
2. Thêm evidence enforcement vào permission.py
3. Persist full UnifiedRiskDecision trong RISK_APPROVED event
4. Thêm global_seq vào store schema + migration
5. Đổi ObservationId sang bar_close_at
6. Tạo durable idempotency table
7. Migrate ExecutionEngine, Alpaca, Binance sang canonical flow
8. Thêm AST static guard cho direct broker calls

---

MAINNET: NO-GO
