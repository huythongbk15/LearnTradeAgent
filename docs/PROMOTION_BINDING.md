# Authoritative Promotion Binding

## Overview

Promotion binding connects the **Research** pipeline to **Runtime** execution through
content-addressed strategy artifacts. This document describes the binding mechanism
that ensures only validated, promoted strategies reach the execution engine.

> **Correction 2026-10-31.** This page describes a design and the code that exists for it, exercised in tests. The path has never run for a real strategy. No code outside tests builds `EvidenceArtifact` or calls `ResearchLifecycle.promote`, `PromotionHook` has no caller, `data/promotion_store/` is empty and `data/research/` does not exist, so no strategy is promoted. `scripts/full_system_backtest.py` writes `PAPER_ELIGIBLE` records to its own run store; those are not promotions. The `TESTNET_ELIGIBLE` rung needs a `REALITY_GAP` item sourced from testnet or shadow, which no code produces. `PromotionStateStore.upsert_from_event` writes any stage with no evidence check, so "only validated strategies" holds by convention, not by code. The CLI and the runner also read different stores: `run-promoted` uses `data/state/promotion.db` and `data/state/artifacts.db`; the Binance runner uses `LIVE_PROMOTION_DB` and `LIVE_ARTIFACT_DB`, by default `data/promotion_store/promotions.db` and `data/research/artifacts.db`.

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
4. Applies environment constraints (allowed symbols and timeframes); exposure caps are applied later by the authority chain

## Environment Mapping

| Environment | Minimum stage (`ENV_MIN_STAGE`) |
|---|---|
| RESEARCH | EXPLORATORY |
| PAPER | PAPER_ELIGIBLE |
| TESTNET | TESTNET_ELIGIBLE |
| SHADOW | SHADOW_ELIGIBLE |
| CANARY | CANARY_ELIGIBLE |
| PRODUCTION | No mapping: nothing resolves |

Apart from a research exemption on symbols, this stage is the only per-environment rule in the resolver. The symbol check reads `config.symbols` (10 symbols by default, without AVAX) and applies only when `symbol` is in the parameters and the environment is not research. The timeframe check allows 1d, 4h, 1h, 15m, 5m and 1m everywhere, and only when `timeframe` is in the parameters. Drift blocks resolution in every environment.

## Fail-Closed Behavior

If a check fails, the resolver returns `None` and logs why, except that a `symbol` or `timeframe` parameter outside the allowed set raises `ValueError`. See [Runtime Resolver](RUNTIME_RESOLVER.md).

## API Usage

```python
from trading_agent.authority import RuntimeStrategyResolver, Environment
from trading_agent.authority.decision import DecisionInput

resolver = RuntimeStrategyResolver(config, promotion_store=store, artifact_store=artifacts)
runtime = resolver.resolve_for("BTC/USDT", "1h", Environment.TESTNET)
if runtime is None:
    return  # fail closed; the reason is in the log

output = runtime.execute(market_data, portfolio_state)
decision_input = DecisionInput(strategy_output=output, symbol="BTC/USDT", ...)
```

## Tests

See `tests/authority/test_resolver.py` and `tests/test_promotion_bridge.py`.

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
RuntimeStrategyResolver.resolve_for(symbol, timeframe, environment)
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
    runtime_loader=loader,  # optional — None nếu không cần watcher
)
lifecycle = ResearchLifecycle(subject_artifact_id)
lifecycle.promote(to_stage, evidence=evidence, actor=actor, on_event=hook.as_callback())
```

**Hiệu đính 2026-10-31.** Chưa có đường production nào nối hook này. `trading-agent execution run-promoted` (`cli/commands/live.py`) chỉ đọc store và khởi động `RuntimeLoader`; nó không tạo `PromotionHook` hay `ResearchLifecycle`. Ngoài test, không code nào gọi `promote`.

## Golden flow (kiểm chứng bởi `tests/test_promotion_bridge.py`)

1. Promote candidate PAPER_ELIGIBLE qua lifecycle với hook ⇒ `resolver.resolve_for(PAPER)` trả StrategyRuntime **ngay lập tức**.
2. Handle cùng event lần 2 ⇒ success, không double-effect.
3. Artifact thiếu ⇒ promote() raise, `lifecycle.stage` giữ nguyên (atomic fail-closed).
4. Promote artifact v2 mới hơn cùng (symbol, tf) ⇒ resolve sau đó trả v2, không restart.
5. Manifest file được loader ghi ra disk cho operator audit.

## Phân biệt với Authority Lifecycle stages

`PromotionStateStore.is_stage_compatible()` là nguồn truth duy nhất cho stage ordering (RESEARCH → PAPER_ELIGIBLE → TESTNET → ...). Đừng dùng whitelist cứng `_ALLOWED_STAGES` kiểu cũ — đã bị loại bỏ vì trôi lệch với policy. Xem `ENV_MIN_STAGE` trong `src/trading_agent/authority/promotion_store.py`; [ENVIRONMENT_MAPPING.md](ENVIRONMENT_MAPPING.md) là thiết kế chưa được thực thi.