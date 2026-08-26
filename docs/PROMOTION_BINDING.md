# Authoritative Promotion Binding

## Overview

Promotion binding connects the **Research** pipeline to **Runtime** execution through
content-addressed strategy artifacts. This document describes the binding mechanism
that ensures only validated, promoted strategies reach the execution engine.

## Architecture

```
┌─────────────────────┐
│   Research Pipeline │
│  (Training + Eval)  │
└──────────┬──────────┘
           │
           │ 1. Strategy trained & evaluated
           │ 2. Metrics computed (Sharpe, DSR, PBO, etc.)
           │ 3. Promotion criteria validated
           ▼
┌─────────────────────┐
│  Promotion Engine   │
│  └─► StrategyArtifact
└──────────┬──────────┘
           │
           │ 4. Artifact stored (code_sha + param_hash)
           │ 5. Manifest generated for operator visibility
           ▼
┌─────────────────────┐
│  Promotion Binding  │ ◄── THIS LAYER
│  RuntimeStrategyResol│
└──────────┬──────────┘
           │
           │ 6. Resolve → Strategy instance
           │ 7. Verify integrity & drift
           │ 8. Apply environment constraints
           ▼
┌─────────────────────┐
│  Execution Engine   │
│  + Authority Chain  │
└─────────────────────┘
```

## Key Components

### StrategyArtifact
Content-addressed artifact containing:
- `code_sha`: Hash of strategy code
- `parameter_hash`: Hash of canonical parameters
- `metadata`: Parameters + execution_model_version
- `artifact_id`: Deterministic ID = hash(code_sha, parameter_hash)

### PromotedStrategyManifest
Operator-visible record of promotion:
- Human-readable strategy name
- Promotion stage (testnet/shadow/canary/production)
- Parameters (for audit)
- Metadata (actor, timestamp, metrics)

### RuntimeStrategyResolver
Bridge component that:
1. Maps strategy names to concrete classes
2. Instantiates Strategy with artifact parameters
3. Verifies parameter hash integrity (drift detection)
4. Applies environment constraints (exposure caps, allowed symbols)

## Environment Mapping

| Environment | Symbol Restriction | Timeframe Restriction | Drift Handling | Reload Behavior |
|-------------|-------------------|----------------------|----------------|-----------------|
| RESEARCH | Unlimited | Unlimited | Warning | Hot Reload |
| PAPER | Top 50 symbols | 1d, 4h, 1h | Warning | Hot Reload |
| TESTNET | Top 20 symbols | 1d, 4h | Block | Hot Reload |
| SHADOW | All disabled | All disabled | N/A | Cold Start |
| CANARY | Top 10 symbols | 1d only | Block + Alert | Hot Reload |
| PRODUCTION | Top 5 symbols | 1d, 4h | Block + Kill Switch | Manual Reload |

## Fail-Closed Behavior

If any check fails, the binding returns `None`:
- **Artifact not found**: `ResolutionOutcome.ARTIFACT_NOT_FOUND`
- **Parameter drift**: `ResolutionOutcome.PARAMETER_DRIFT`
- **Symbol not allowed**: `ResolutionOutcome.SYMBOL_NOT_ALLOWED`
- **Timeframe not supported**: `ResolutionOutcome.TIMEFRAME_NOT_SUPPORTED`
- **Instantiation failed**: `ResolutionOutcome.INSTANTIATION_FAILED`

## API Usage

```python
from trading_agent.authority import RuntimeStrategyResolver, Environment
from trading_agent.authority.loader import PromotedStrategy

# Create resolver with config
resolver = RuntimeStrategyResolver(config)

# Resolve a promoted strategy
strategy = resolver.resolve(promoted_strategy)

if strategy is None:
    # Check why it failed
    outcome = resolver.last_outcome  # See ResolutionOutcome enum
    logger.error(f"Resolution failed: {outcome}")
    return

# Set up authority chain
decision_input = DecisionInput(
    strategy=strategy,
    signal=signal,
    ...
)
```

## Tests

See `tests/authority/test_resolver.py` and `tests/authority/test_promotion_binding.py`.

---

# Research → Runtime Bridge (Milestone D, commit `7ec1574`)

> Trước Milestone D: `ResearchLifecycle.promote()` chỉ phát event trong memory — KHÔNG persist vào `PromotionStateStore`; resolver không bao giờ thấy promotion mới mà không restart. Bridge này vá đúng lỗ hổng đó.

## Kiến trúc bridge

```
ResearchLifecycle.promote(candidate)
        │
        │ 1. validate gates (evidence, stage policy)
        │ 2. update lifecycle stage IN-MEMORY
        │
        ├──► on_event(PromotionEvent)  ← hook wire point
        │         │
        │         ▼
        │    PromotionHook.handle(event)          [authority/promotion_hook.py]
        │         │
        │         │ a. upsert PromotionStateStore (authoritative persistence)
        │         │ b. verify store stage == event stage (fail-closed)
        │         │ c. optional: load vào RuntimeLoader + start manifest watcher
        │         │
        │         └─ FAIL ⇒ raise BridgeError
        │                │
        │                ▼
        │    promote() BẮT lỗi ⇒ PromotionError ⇒ stage KHÔNG đổi (atomic)
        │
        ▼
RuntimeStrategyResolver.resolve_for(env, symbol, timeframe)
        → đọc PromotionStateStore MỖI LẦN resolve (hot-reload friendly by design)
        → trả StrategyRuntime mới ngay không cần restart
```

## Tính chất bảo đảm

| Tính chất | Cơ chế |
|---|---|
| **Atomic** | Hook fail → PromotionError → lifecycle stage giữ nguyên. Không có trạng thái nửa vời giữa memory và store. |
| **Fail-closed** | Artifact thiếu trong artifact store khi handle() ⇒ BridgeError ⇒ promote thất bại. Không bao giờ "promote trên giấy" mà runtime không resolve được. |
| **Idempotent** | Gọi handle() 2 lần cùng event ⇒ OK, không double side-effect (store upsert là idempotent theo record id). |
| **Hot-reload** | Resolver tra store mỗi lần resolve; artifact cùng (symbol, timeframe) phiên bản mới hơn ⇒ lần resolve sau trả runtime mới. CLI `execution run-promoted` khởi động `RuntimeLoader` watcher tự động pick-up manifest thay đổi. |

## Wire point

```python
from trading_agent.authority.promotion_hook import PromotionHook

hook = PromotionHook(
    promotion_store=promotion_store,
    artifact_store=artifact_store,
    runtime_loader=loader,          # optional — None nếu không cần watcher
)
lifecycle = ResearchLifecycle(..., on_event=hook.handle)
```

CLI production path (`commands/execution.py run-promoted`) wire sẵn: promote qua lifecycle bất kỳ đâu trong process ⇒ resolver thấy ngay.

## Golden flow (kiểm chứng bởi `tests/test_promotion_bridge.py`)

1. Promote candidate PAPER_ELIGIBLE qua lifecycle với hook ⇒ `resolver.resolve_for(PAPER)` trả StrategyRuntime **ngay lập tức**.
2. Handle cùng event lần 2 ⇒ success, không double-effect.
3. Artifact thiếu ⇒ promote() raise, `lifecycle.stage` giữ nguyên (atomic fail-closed).
4. Promote artifact v2 mới hơn cùng (symbol, tf) ⇒ resolve sau đó trả v2, không restart.
5. Manifest file được loader ghi ra disk cho operator audit.

## Phân biệt với Authority Lifecycle stages

`PromotionStateStore.is_stage_compatible()` là nguồn truth duy nhất cho stage ordering (RESEARCH → PAPER_ELIGIBLE → TESTNET → ...). Đừng dùng whitelist cứng `_ALLOWED_STAGES` kiểu cũ — đã bị loại bỏ vì trôi lệch với policy. Xem [ENVIRONMENT_MAPPING.md](ENVIRONMENT_MAPPING.md).