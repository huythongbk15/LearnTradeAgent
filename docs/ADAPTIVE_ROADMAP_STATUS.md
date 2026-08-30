# Trạng thái Adaptive Strategy Selection & Routing

> Đọc [Core System](CORE_SYSTEM.md) và [Documentation Map](DOCUMENTATION_MAP.md) trước. File này là status/evidence record cho adaptive roadmap, không phải mô tả tổng quát của hệ thống.

**Ngày rà soát:** 2026-08-31
**Phạm vi:** S4 (selection/promotion), S5 (runtime routing), S6 (shared capital), S7 (shadow/testnet/canary)

## Kết luận ngắn

Phần code an toàn của S4–S6 đã được hoàn thiện và có kiểm thử. Hệ thống không còn bị buộc phải chạy `Enhanced MA` trong full-system backtest: CLI nhận strategy canonical và bộ tham số tương ứng; registry/resolver đã có thêm `ma_adx` và `ma_vol_target`.

S7 chưa thể đặt **complete** chỉ bằng code. Các mốc testnet/shadow/canary tối thiểu 30 ngày, số vòng lệnh thực tế, calibration và release attestation phải được tạo từ môi trường vận hành thật; không được thay bằng fixture hoặc số liệu tổng hợp.

## S4 — Selection policy và provenance

Đã hoàn thành trong `research/selection_policy.py`:

- Policy content-addressed: mọi trường persisted, bao gồm lifecycle/rollback lineage, tham gia `policy_id`.
- `ParamArtifact` lưu `strategy_id`, params, `param_hash` và `code_sha`.
- Policy VALIDATED/ACTIVE bắt buộc evidence IDs và provenance đầy đủ: commit, data manifest, feature manifest, release digest và strategy code SHA.
- Detached HMAC-SHA256 envelope, registry append-only, phát hiện collision/tamper.
- Activation compare-and-swap, actor/ticket bắt buộc, audit JSONL; rollback tạo artifact mới trỏ về known-good policy cũ.
- `SelectionPolicyBuilder.from_wfo_result()` chỉ nhận WFO có hard gates, frozen holdout COMPLETED, outer artifacts và study provenance thật.

`PolicyActivationService.advance_stage()` hiện là adapter bất biến giữa policy và canonical
`ResearchLifecycle`: chỉ cho phép tiến đúng một stage, có actor/ticket/evidence và ghi audit.
Canonical `ResearchLifecycle` vẫn là nơi đánh giá semantics của evidence; policy boundary
không thể tự skip stage. Production promotion vẫn phải đi qua canonical lifecycle.

## S5 — Regime router và safe switching

Đã hoàn thành trong `authority/adaptive_router.py` và `ml/regime_detection.py`:

- Posterior có probabilities, model ID, fitted window, generated-at, OOD score và fingerprint.
- Router tính điểm theo **toàn bộ posterior**, không dùng riêng argmax regime.
- Stale/unversioned/OOD/high-entropy/thiếu signed policy đều fail-closed về `NO_TRADE`.
- Persistence bars, score margin, minimum dwell và cooldown chống flip-flop.
- `position_owner_strategy_id` được giữ đến khi flat; switch pending không mở exposure mới.
- State machine có `STABLE`, `SWITCH_PENDING`, `WAIT_FLAT`, `ACTIVATE`.
- Routing decision immutable, content-addressed, có policy/posterior/strategy/reason/audit đầy đủ.
- State per pair/timeframe có checksum, restart replay và duplicate observation idempotency.
- `AdaptiveForecastRuntime` kiểm tra chữ ký, validity, params và code SHA trước khi resolve adapter canonical.
- `FullSystemSimulator` đã có cổng adaptive opt-in: caller phải cung cấp đủ router,
  posterior provider và runtime provider; thiếu dependency sẽ fail-closed, còn report
  ghi rõ `routing_mode` và toàn bộ `routing_decisions`.
- Full-system CLI nhận `--strategy-artifact` và từ chối manifest bị tamper hoặc lệch
  code/data/parameters; tournament `--tail-bars` đã nối vào execution window thật.
- Research pipeline đã đổi tên rõ thành `ResearchStrategyRuntime` (giữ alias tương thích),
  tránh nhầm với authority-bound `StrategyRuntime` dùng cho order execution.

## S6 — Shared-capital allocator

Đã bổ sung vào `authority/portfolio.py`:

- Aggregate cap theo strategy, symbol và correlation cluster trong cùng một batch.
- Nhiều strategy cùng symbol được cộng/net deterministic thay vì ghi đè target cuối;
  forecast đối nghịch được triệt tiêu trước khi phát target long-only.
- Regime risk multiplier, no-trade band, average daily notional và max participation.
- Pro-rata scaling giữ shared cash/gross exposure; duplicate request key bị từ chối fail-closed.
- Shared-capital event-driven backtest hiện có ledger fee/slippage/turnover/reconciliation và đã chạy 22 test pass (gồm allocator constraints).

Attribution STR-0608 đã được chuẩn hóa trong `backtest/portfolio_backtest.py`: fill mang
strategy/regime/factor metadata, tính gross/net PnL, fee, slippage, turnover và có
bảng tổng hợp theo strategy/pair/regime/factor. Factor không có trong observation
được ghi rõ là `unattributed`, không suy diễn ngược từ outcome.

## S7 — Shadow/testnet/canary

Đã có sẵn capability code:

- Shadow engine có env guard bắt buộc, tuyệt đối không submit live order.
- Simulated fills, protective shadow state, execution metrics và reality-gap report.
- Canonical promotion gate có các stage testnet/shadow/canary/production và không cho skip stage.
- Drift/calibration/reality-gap modules và promotion evidence validators đã tồn tại.
- Production gate đã bắt buộc release attestation theo exact commit/image digest,
  Cosign, SBOM, SLSA và provenance; testnet bắt buộc ≥100 order lifecycles,
  canary bắt buộc không vi phạm loss budget.

Chưa đủ bằng chứng để đóng S7:

Runbook thu thập payload và checklist nằm tại
[S7_OPERATIONAL_EVIDENCE_RUNBOOK.md](S7_OPERATIONAL_EVIDENCE_RUNBOOK.md).

- testnet/shadow/canary soak tối thiểu 30 ngày;
- 100 complete order lifecycles testnet, calibration tối thiểu 30 observations/ECE ≤ 0,10;
- reality-gap/tracking-error/drift report từ cùng release commit;
- named approvals và Cosign/SBOM/SLSA/provenance gate cho exact release.

## Kiểm thử đã chạy

Các nhóm kiểm thử trực tiếp sau thay đổi:

| Nhóm | Kết quả |
|---|---:|
| S4 lifecycle/signature/rollback | 20 passed |
| S5 adaptive router/runtime | 11 passed |
| Latest focused integration after final guard/attribution changes | 81 passed |
| Shadow/promotion/drift focused | 33 passed |
| Promotion bridge + governance regression | 37 passed |
| Shared-capital portfolio backtest + allocator constraints | 22 passed |
| S6-0608 attribution contract | 1 passed |
| Promotion + release-attestation gates | 21 passed |
| Supply-chain provenance verifier | 15 passed |
| Latest targeted regression (simulator/CLI/runtime contracts) | 94 passed |
| Full fast suite (last completed baseline before final CLI/runtime contract patch) | 1.243 passed, 9 skipped |
| Ruff trên các module thay đổi | clean |
| Mypy các module thay đổi | clean |

Kiểm tra `mypy src/trading_agent` toàn repository vẫn báo 698 lỗi legacy ở 102
file (chủ yếu versioning CLI, execution và deployment), không phải các module
adaptive đã thay đổi. Đây là backlog chất lượng riêng, chưa được đánh dấu là
đã sửa.

Các mục code còn mở có chủ đích:

- S2-0208: timeout/retry độc lập cho từng tournament cell chưa được đưa vào runner;
  hiện lỗi cell vẫn fail-closed và lớp điều phối ngoài chịu trách nhiệm timeout.
- S4-0403: `ArtifactLifecycle` cũ vẫn giữ để tương thích test/client; mọi promotion
  mới dùng `ResearchLifecycle` canonical và không được dùng state machine cũ cho release.

## Quyết định phát hành

- **Research / replay / paper:** có thể sử dụng adaptive policy/router sau khi policy được ký và evidence được kiểm tra.
- **Testnet / shadow:** cho phép chạy theo promotion gate, nhưng phải thu thập evidence thực tế ở trên.
- **Canary / production:** **NO-GO** cho đến khi S7 evidence gate đóng; không tự động tăng vốn hoặc bỏ qua approval.
