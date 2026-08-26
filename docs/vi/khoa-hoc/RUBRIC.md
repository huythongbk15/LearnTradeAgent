# Rubric chấm khóa học và capstone

Tổng điểm: 100. Một hạng mục có thể được 0 dù return cao nếu thiếu identity hoặc
vi phạm safety boundary.

## 1. Kiến trúc và authority — 12 điểm

| Mức | Tiêu chí |
| --- | --- |
| 10–12 | Trace đầy đủ data → decision → authorization → execution → audit; fail-closed đúng |
| 6–9 | Hiểu luồng chính nhưng thiếu store/causation hoặc một authority boundary |
| 1–5 | Chỉ mô tả modules, chưa phân biệt proposal và authorization |
| 0 | Cho phép direct broker bypass hoặc fallback không xác minh |

## 2. Data và point-in-time — 12 điểm

| Mức | Tiêu chí |
| --- | --- |
| 10–12 | Data identity, closed bars, gap/holdout/warmup đều rõ và có tests |
| 6–9 | Không future leak nhưng manifest/quality analysis chưa đầy đủ |
| 1–5 | Chỉ kiểm schema/row count |
| 0 | Dùng future/incomplete data mà không phát hiện |

## 3. Strategy contract — 12 điểm

| Mức | Tiêu chí |
| --- | --- |
| 10–12 | Descriptor/registry/params/abstain/state/determinism có evidence |
| 6–9 | Happy path tốt, failure/params binding còn yếu |
| 1–5 | Strategy chạy được nhưng identity/contract mơ hồ |
| 0 | Unknown/missing strategy bị thay thế âm thầm |

## 4. Backtest và accounting — 12 điểm

| Mức | Tiêu chí |
| --- | --- |
| 10–12 | Audit report toàn diện, net costs, ledger/accounting/replay rõ |
| 6–9 | Đọc đúng return/risk nhưng execution evidence còn thiếu |
| 1–5 | Chỉ nhìn headline metrics |
| 0 | Metric thiếu bị ép 0 hoặc accounting sai không nhận ra |

## 5. Tournament và artifacts — 12 điểm

| Mức | Tiêu chí |
| --- | --- |
| 10–12 | Reconcile đủ cells, failed visible, identity/lineage/fault audit đầy đủ |
| 6–9 | Matrix đủ nhưng cost/fault/params audit chưa sâu |
| 1–5 | Chỉ bảng ranking |
| 0 | Bỏ missing cells hoặc overwrite evidence |

## 6. Statistical selection — 12 điểm

| Mức | Tiêu chí |
| --- | --- |
| 10–12 | Nested WFO, trials, uncertainty, hard gates, incumbent/no-selection rõ |
| 6–9 | Có OOS/gates nhưng multiple testing hoặc stability yếu |
| 1–5 | Chọn theo average/best Sharpe |
| 0 | Tune và đánh giá trên cùng data rồi promote |

## 7. Promotion/runtime — 10 điểm

| Mức | Tiêu chí |
| --- | --- |
| 8–10 | Lineage, environment, expiry/revoke/cache/rollback threat model đầy đủ |
| 4–7 | Happy path rõ, revocation/rollback chưa đủ |
| 1–3 | Promotion chỉ là flag/filename |
| 0 | Runtime chạy artifact không đủ eligibility |

## 8. Execution/risk — 10 điểm

| Mức | Tiêu chí |
| --- | --- |
| 8–10 | Lifecycle/race/reservation/protection/reconciliation phân tích đúng |
| 4–7 | Luồng bình thường đúng, fault handling còn thiếu |
| 1–3 | Chỉ mô tả submit/fill |
| 0 | Timeout/cancel/ACK được hiểu sai gây exposure risk |

## 9. Operations và communication — 8 điểm

| Mức | Tiêu chí |
| --- | --- |
| 7–8 | Reproducible record, staged gates, incidents, rollback, limitations rõ |
| 4–6 | Có runbook nhưng gate/trigger định lượng chưa đủ |
| 1–3 | Chỉ có commands, thiếu decision/evidence |
| 0 | Gợi ý mainnet không approval hoặc để lộ secret |

## Hard-fail conditions

Bất kỳ điều nào sau đây giới hạn tổng điểm tối đa 50:

- future leakage không được phát hiện;
- missing cells bị bỏ qua;
- direct live/broker order trong khóa học;
- secret/private account data trong deliverable;
- report/artifact bị sửa để tạo kết quả đẹp;
- tuyên bố production-ready không có readiness/soak approval;
- không thể xác định commit/data/params của kết quả.

## Chứng nhận tự đánh giá

- `85–100`: độc lập tốt trong research/review; vẫn cần operator authority cho live.
- `70–84`: đạt luồng chính; cần review ở statistics hoặc execution edge cases.
- `55–69`: hiểu khái niệm nhưng evidence practice chưa ổn.
- `<55`: học lại theo các exit gate chưa đạt.

