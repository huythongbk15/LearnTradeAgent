# S7 Operational Evidence Runbook

Tài liệu này mô tả cách đóng các gate S7 bằng dữ liệu vận hành thật. Không dùng
fixture, số liệu tổng hợp hoặc chỉnh tay payload để thay thế log gốc.

## 1. Nguyên tắc bất biến

- Mỗi evidence gắn với đúng `subject_artifact_id`, release commit và image digest.
- Log nguồn phải append-only, có timestamp UTC, run ID và validator identity.
- Chỉ tạo `EvidenceArtifact` sau khi campaign kết thúc; không cập nhật ngược outcome
  chưa đến hạn vào allocator.
- Nếu thiếu một trường hoặc một gate không đạt, promotion phải giữ nguyên stage.

## 2. Testnet operational (S7-0706)

Thu thập từ testnet ledger:

```json
{
  "days": 30,
  "complete_order_lifecycles": 100,
  "unresolved_orders": 0,
  "safety_breaches": 0,
  "release_commit_sha": "<40-hex-sha>"
}
```

`EvidenceSource` phải là `TESTNET`. Gate hiện tại từ chối payload thiếu
`complete_order_lifecycles` hoặc có giá trị nhỏ hơn 100.

## 3. Shadow operational và calibration (S7-0707)

Shadow phải ghi counterfactual forecast, delayed outcome, exposure decision,
abstention, drift và execution-quality metrics. Evidence tối thiểu:

```json
{
  "days": 30,
  "critical_alerts": 0,
  "sample_count": 30,
  "ece": 0.10,
  "status": "empirical",
  "health_state": "healthy"
}
```

Calibration dùng `EvidenceSource.SHADOW`; ECE phải không vượt 0,10 và không được
dùng outcome chưa đến hạn.

## 4. Canary và approval (S7-0708/0709)

Canary phải nằm trong loss budget đã được phê duyệt:

```json
{
  "days": 30,
  "safety_breaches": 0,
  "loss_budget_breaches": 0,
  "approved_loss_budget_id": "<approval-id>"
}
```

Approval phải là evidence riêng, nguồn `OPERATOR`, có `approver` và `ticket`.
Việc tăng vốn là quyết định khác, không được suy ra tự động từ canary pass.

## 5. Adaptive routing trong full-system backtest

`FullSystemSimulator` hỗ trợ adaptive theo cơ chế opt-in. Caller phải truyền đồng
thời ba dependency: `adaptive_router`, `adaptive_posterior_provider` và
`adaptive_runtime_provider`. Thiếu bất kỳ dependency nào sẽ bị từ chối ngay;
không có fallback ngầm về strategy cố định.

- `adaptive_posterior_provider(symbol, timeframe, decision_row, observed_at)` phải trả
  `RegimePosterior` fresh, có model/fitted window/OOD metadata.
- Router chọn policy đã ký, áp dụng hysteresis/handover và ghi `routing_decisions`.
- `adaptive_runtime_provider(decision)` phải trả runtime canonical đúng strategy/
  params của decision; runtime không có thì backtest fail-closed.
- Khi đang có vị thế, owner strategy được phép quản lý lệnh giảm vị thế;
  strategy challenger không được mở exposure cho đến khi flat.

Report phải có `active_config.strategy.routing_mode = "adaptive"` và danh sách
`routing_decisions`; nếu hai trường này không có thì chỉ được coi là fixed baseline.

## 6. Exact release attestation (S7-0710)

Trước production promotion, CI phải tạo evidence nguồn `SYSTEM` với payload:

```json
{
  "commit_sha": "<40-hex-sha>",
  "image_digest": "sha256:<64-hex>",
  "cosign_verified": true,
  "sbom_verified": true,
  "slsa_verified": true,
  "provenance_verified": true,
  "verification_run_id": "<github-run-id>"
}
```

Các cờ phải là boolean `true` thực sự. `ResearchPromotionGate` sẽ từ chối sai
source, sai định dạng commit/digest, thiếu run ID hoặc bất kỳ attestation nào.

## 7. Lệnh kiểm tra trước khi promote

1. Đối chiếu commit trong evidence với commit của policy và image label.
2. Đối chiếu image digest với digest đã được verifier kiểm tra.
3. Kiểm tra toàn bộ order lifecycle, reconciliation, protection và alert log.
4. Gọi `ResearchPromotionGate.assess()`; chỉ tạo event khi `passed == true`.
5. Lưu evidence IDs và approval ticket vào audit append-only.

Nếu chưa có đủ evidence ở trên, trạng thái đúng là **NO-GO**, không phải pass
tạm thời.
