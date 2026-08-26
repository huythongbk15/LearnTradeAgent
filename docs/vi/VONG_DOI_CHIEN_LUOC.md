# Vòng đời chiến lược và ranh giới authority

> Trạng thái: **HIỆN HÀNH**, có đánh dấu rõ phần **MỤC TIÊU** · Kiểm tra: 2026-08-26
>
> Bản contract đối chiếu: [Strategy Lifecycle](../architecture/STRATEGY_LIFECYCLE.md)

## 1. Vì sao cần vòng đời tách biệt?

Một trading system an toàn không chọn strategy và gửi order trong cùng một bước.
Research được phép thử nhiều giả thuyết; runtime chỉ được dùng policy nhỏ, bất biến
và đã được promote. Tách hai cadence giúp:

- hạn chế overfitting theo dữ liệu mới nhìn thấy;
- tái hiện được decision lịch sử;
- rollback mà không sửa bằng chứng cũ;
- ngăn research code tự cấp quyền mở exposure;
- audit chính xác ai, artifact nào và policy nào đã cho phép order.

## 2. Chuỗi lifecycle

```text
Strategy implementation
  → StrategyDescriptor
  → canonical registry
  → point-in-time features
  → EvaluationArtifact
  → statistical selection
  → SelectionPolicyArtifact
  → PromotionRecord
  → StrategyRuntime / RoutingDecision
  → portfolio + risk authorization
  → order lifecycle + fill attribution
```

## 3. Trạng thái từng stage

| Stage | Đầu ra | Khi lỗi phải làm gì? | Trạng thái |
| --- | --- | --- | --- |
| Descriptor | `StrategyDescriptor` | Reject identity/parameter schema không hợp lệ | **HIỆN HÀNH** |
| Registry | Adapter + descriptor đã allowlist | Unknown strategy phải lỗi rõ ràng | **HIỆN HÀNH** |
| Feature window | OHLCV đã đóng trước decision time | Thiếu history → abstain | **HIỆN HÀNH** |
| Canonical bridge | Runtime tương thích authority | Không đủ warmup → giữ flat | **HIỆN HÀNH** |
| Tournament cell | `EvaluationArtifact` | Tạo artifact `FAILED`, không làm biến mất cell | **HIỆN HÀNH, đang harden S2** |
| Statistical selector | Ranking + uncertainty | Không đủ bằng chứng → `no winner` | **MỤC TIÊU** đến khi exit gate đóng |
| Selection policy | Policy bất biến theo pair/regime | Thiếu lineage → reject | **MỤC TIÊU** |
| Promotion | `PromotionRecord` | Không promotion → không runtime eligibility | **NỀN TẢNG HIỆN HÀNH** |
| Runtime resolver | `StrategyRuntime` | Identity/integrity mismatch → fail closed | **NỀN TẢNG HIỆN HÀNH** |
| Regime router | `RoutingDecision` | Giữ incumbent, giảm risk hoặc abstain | **MỤC TIÊU** |
| Shared capital | Target exposure đã constraint | Infeasible → giảm/reject allocation | **MỤC TIÊU** |

## 4. Chuỗi identity bắt buộc

Một decision có thể audit phải truy được ít nhất:

```text
strategy_id
descriptor_id
parameter_hash
data_manifest_hash
commit/code identity
cost/fault scenario
evaluation_artifact_id
selection_policy_id
promotion_record
routing_decision_id
```

Nếu runtime không chứng minh được chuỗi trên, nó không được thay bằng
`legacy_runner`, strategy mặc định hoặc params ngầm.

## 5. Các invariant quan trọng

1. Decision tại `t` chỉ dùng bar đã đóng trước `t`.
2. Params dùng tính signal phải đúng params đã ghi trong artifact.
3. Mỗi tournament cell có state/output cô lập.
4. Cell lỗi vẫn xuất hiện trong index và không được tham gia selection.
5. Runtime chỉ load promotion đủ điều kiện cho đúng environment.
6. Regime đổi không đồng nghĩa switch ngay lập tức.
7. Portfolio/risk có authority cao hơn preference của strategy.
8. Emergency reduction được phép giảm risk nhưng không được mở exposure mới.

## 6. Hai cadence

```text
Research chậm:
data lock → tournament → nested WFO → review → policy → promotion

Runtime nhanh:
observation → policy lookup → route/abstain → risk → execution
```

Runtime không được chạy optimizer, tự thay params theo P&L gần nhất hoặc tự promote
winner bên trong trading loop.

## 7. Thứ tự fallback an toàn

1. Giữ incumbent nếu promotion và evidence còn hợp lệ.
2. Giảm exposure khi uncertainty/execution health xấu đi.
3. Dùng `AbstainStrategy` khi không candidate nào đủ điều kiện.
4. Kích hoạt protective reduction theo emergency policy.
5. Không bao giờ mở exposure bằng candidate chưa được xác minh.

## 8. Câu hỏi review

- Strategy này được chọn từ tập candidate nào?
- Holdout có bị nhìn trước khi khóa policy không?
- Report và runtime có cùng strategy/params identity không?
- Nếu router không chắc chắn, hệ thống làm gì?
- Ai có authority promote và revoke?
- Rollback có cần sửa artifact lịch sử không? Nếu có, thiết kế chưa đạt.

