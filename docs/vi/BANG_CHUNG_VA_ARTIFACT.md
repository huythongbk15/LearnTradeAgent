# Bằng chứng, artifact và chuỗi provenance

> Trạng thái: **HIỆN HÀNH**, phần chưa triển khai được ghi **MỤC TIÊU**
>
> Bản contract đối chiếu: [Evidence Artifact Catalog](../reference/EVIDENCE_ARTIFACTS.md)

Artifact là ranh giới giữa một tuyên bố và bằng chứng có thể kiểm tra. Tên file
giúp con người điều hướng; identity phải đến từ nội dung canonical hoặc contract
đã version hóa.

## Danh mục artifact

| Artifact | Producer | Consumer | Vai trò |
| --- | --- | --- | --- |
| Data manifest | Data/backtest loader | Runner, verifier | Khóa dataset, window và quality summary |
| `BacktestReportV2` | Full-system/portfolio backtest | Evaluator, reviewer | Report có schema, metrics, ledger và execution evidence |
| Golden replay manifest | `verify_golden_replay.py` | S0/release review | Chứng minh hai run cùng input có cùng output ổn định |
| `StrategyDescriptor` | Canonical registry | Tournament/runtime bridge | Identity, features, warmup và parameter schema |
| `EvaluationArtifact` | Tournament cell | Index/selector | Evidence của một cell và trạng thái `COMPLETED`/`FAILED` |
| Tournament index | Tournament CLI | Selector/reviewer | Kiểm kê không bỏ sót cell |
| Selection policy | Statistical selector | Router | **MỤC TIÊU:** eligible mapping, uncertainty và abstain rule |
| `PromotionRecord` | Promotion workflow | Runtime resolver | Cho phép artifact ở một environment/stage |
| `RoutingDecision` | Runtime router | Risk/audit | **MỤC TIÊU:** regime, incumbent, candidate và reason |
| Trade attribution | Fill/ledger analytics | Monitoring/research | **MỤC TIÊU:** alpha, portfolio và execution cost attribution |

## Identity tối thiểu

Mọi evaluation/policy artifact cần có:

```text
schema_version
artifact_id hoặc content_hash
created_at
commit/code identity
data_manifest_hash
strategy_id + descriptor_id
parameter_hash
symbol/timeframe/universe
cost_scenario + fault_profile
parent_artifact_ids
status + failure_reasons
```

`created_at` chỉ phục vụ audit ordering, không phải bằng chứng strategy mới hơn thì
tốt hơn.

## Đọc `BacktestReportV2`

Không đánh giá report chỉ bằng return hoặc Sharpe. Cần đọc theo nhóm:

| Nhóm | Câu hỏi |
| --- | --- |
| Data | Window nào, đủ bar không, có gap/future leak không? |
| Identity | Code, strategy và params nào tạo report? |
| Return/risk | Return đi cùng drawdown, volatility và benchmark ra sao? |
| Trades | Bao nhiêu trade, holding time, win/loss distribution thế nào? |
| Costs | Fee, spread, slippage, impact chiếm bao nhiêu alpha? |
| Execution | Reject, partial fill, latency/fault có xảy ra không? |
| Warnings | Metric nào unavailable hoặc có giới hạn? |

Metric thiếu phải là `unavailable` hoặc làm gate fail; không được ép thành `0` vì
`0` là một giá trị có ý nghĩa khác.

## Đọc `EvaluationArtifact`

Một cell đại diện cho:

```text
strategy × symbol × timeframe × params × cost scenario × fault profile
```

Quy tắc:

1. Một cell có một stable ID.
2. Cell crash phải tạo evidence `FAILED` nếu contract cho phép.
3. Metrics chỉ lấy từ report `COMPLETED` đã validate.
4. Report identity phải khớp descriptor/cell identity.
5. Selector loại cell failed/incomplete theo cấu trúc, không theo convention.
6. Rerun/overwrite phải là hành động explicit.

## Vì sao selection khác promotion?

```text
EvaluationArtifact[]
  → SelectionPolicyArtifact
  → reviewed PromotionRecord
  → StrategyRuntime
```

Selection là kết luận khoa học trong một phạm vi evidence. Promotion là quyết
định authority cho một environment. Tách hai bước cho phép review độc lập, expiry,
revoke và rollback.

## Lưu trữ và retention

- Lưu evidence bất biến dưới `artifacts/` hoặc run directory riêng.
- Không overwrite passing artifact bằng run mới cùng nhãn.
- Ledger lớn có thể lưu riêng theo content hash và được report tham chiếu.
- Giữ failed artifacts để phân tích lỗi và selection bias.
- `latest` chỉ là pointer tiện lợi, không phải nguồn quyết định.
- Redact secret, private account ID và broker payload.

## Checklist audit nhanh

- [ ] Có tái tạo được data và code không?
- [ ] Params trong artifact có thật sự tác động signal không?
- [ ] Có thể đổi report mà artifact ID không đổi không?
- [ ] Failed cells có hiện đầy đủ không?
- [ ] Runtime có chứng minh promotion nào cho phép decision không?
- [ ] Rollback có giữ nguyên historical evidence không?

