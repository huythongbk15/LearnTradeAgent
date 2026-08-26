# Bài 07 — Selection artifact, promotion và runtime resolver

> Mức độ: nâng cao · Thời lượng: 4–5 giờ · Trạng thái: **NỀN TẢNG HIỆN HÀNH, policy MỤC TIÊU**

## Mục tiêu

- Phân biệt selection và promotion.
- Hiểu promotion record, environment eligibility và revocation.
- Trace runtime resolver fail-closed.
- Thiết kế lineage từ evaluation đến runtime decision.

## File cần đọc

- [Promotion Binding](../../PROMOTION_BINDING.md)
- [Runtime Resolver](../../RUNTIME_RESOLVER.md)
- `src/trading_agent/authority/promotion_store.py`
- `src/trading_agent/authority/promotion_hook.py`
- `src/trading_agent/authority/resolver.py`
- `src/trading_agent/authority/loader.py`
- `tests/test_promotion_bridge.py`
- `tests/authority/test_resolver.py`
- `tests/test_golden_execute_promoted.py`

## 1. Hai quyết định khác nhau

```text
Selection: evidence hỗ trợ candidate nào?
Promotion: environment nào được phép chạy artifact nào ngay lúc này?
```

Một artifact tốt về research vẫn có thể chưa được paper/testnet promote. Một
promotion cũ có thể bị revoke dù historical evaluation không thay đổi.

## 2. Lineage

```text
DataManifest
  → EvaluationArtifact[]
  → SelectionPolicyArtifact
  → PromotionRecord(environment, stage, expiry)
  → StrategyRuntime
  → StrategyOutput / decision audit
```

Runtime phải chứng minh lineage; không tự tái chạy selection.

## 3. Lab có hướng dẫn — Promotion/resolver tests

```bash
.venv/bin/python -m pytest -q \
  tests/test_promotion_bridge.py \
  tests/authority/test_resolver.py \
  tests/test_golden_execute_promoted.py -vv
```

Trace một happy path và một fail path:

- artifact được register ở đâu;
- promotion được lưu ở đâu;
- resolver kiểm tra environment/strategy/symbol/timeframe thế nào;
- integrity mismatch ném lỗi gì;
- output mang artifact metadata nào.

## 4. Bài tập threat model

Phân tích năm tình huống:

1. Promotion trỏ tới artifact không tồn tại.
2. Artifact content thay đổi nhưng ID giữ nguyên.
3. Paper promotion bị dùng ở live environment.
4. Symbol/timeframe request khác artifact.
5. Promotion hết hạn nhưng cache runtime vẫn còn.

Với mỗi tình huống ghi:

- detection point;
- safe behavior;
- audit evidence;
- test cần có.

## 5. Revocation và rollback

Rollback đúng:

- chọn lại promotion/policy đã biết tốt;
- không sửa historical artifact;
- không làm mất event history;
- có atomic/fenced transition;
- exposure hiện tại được xử lý theo policy.

Rollback không nhất thiết đóng vị thế ngay; cần tách strategy policy rollback và
emergency risk reduction.

## 6. Bài tập thiết kế `SelectionPolicyArtifact`

Viết JSON contract tối thiểu trên giấy, gồm:

```text
schema_version
policy_id/content_hash
created_at
candidate_set_hash
evaluation_artifact_ids
data/commit/config identities
pair/regime mapping
hard-gate results
uncertainty
incumbent
abstain/fallback rules
risk limits
expiry
reviewer/signature metadata
```

Giải thích field nào tham gia content hash và field nào chỉ là audit metadata.

## 7. Bài tập runtime trace

Dùng mẫu code-reading trace cho:

```text
promotion state store
→ runtime resolver
→ StrategyRuntime
→ forecast/StrategyOutput
→ decision audit
```

Bạn phải tìm được fail-closed path khi promotion thiếu hoặc strategy unknown.

## Lỗi thường gặp

- Selection tự động đồng nghĩa promotion.
- Runtime dùng latest file thay vì immutable ID.
- Cache bỏ qua revocation/expiry.
- Rollback sửa artifact lịch sử.
- Environment eligibility là string metadata nhưng không enforce.
- Dùng “default strategy” khi resolver fail.

## Exit gate

- [ ] Promotion/resolver tests đạt.
- [ ] Trace happy/fail paths.
- [ ] Hoàn thành threat model năm tình huống.
- [ ] Thiết kế policy artifact có lineage/expiry/abstain.
- [ ] Giải thích rollback không làm mất historical evidence.

Tiếp theo: [Bài 08 — Execution và risk](08_EXECUTION_RISK.md).

