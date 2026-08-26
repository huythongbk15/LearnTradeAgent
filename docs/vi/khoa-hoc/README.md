# Khóa học thực hành Trading Agent System

> Trạng thái: **HIỆN HÀNH** · Phiên bản: 1.0 · Thời lượng đề xuất: 45–60 giờ

Đây là khóa học đầy đủ để bạn hiểu dự án từ dữ liệu đến execution và có thể tự
thực hành trên codebase. Khóa học không yêu cầu tin vào con số backtest có sẵn:
mỗi kết luận phải đi qua code, test và artifact.

## Sau khóa học bạn làm được gì?

Bạn có thể:

1. Vẽ và giải thích năm plane của hệ thống.
2. Truy một decision từ market data đến order/fill/audit.
3. Phát hiện future leakage, sai data identity và metric gây hiểu nhầm.
4. Đọc, đăng ký và kiểm thử canonical strategy.
5. Chạy backtest/tournament cô lập và audit artifact.
6. Thiết kế selection policy không overfit và chấp nhận `NO_SELECTION`.
7. Giải thích promotion, runtime resolver và fail-closed authority.
8. Phân tích lifecycle, risk, protective order và reconciliation.
9. Lập kế hoạch shadow → paper → testnet → canary → production.
10. Hoàn thành capstone có bằng chứng và rubric rõ ràng.

## Đối tượng và kiến thức đầu vào

- Biết Python cơ bản: function, class, dataclass, exception, pytest.
- Biết Git cơ bản và đọc JSON/Markdown.
- Hiểu OHLCV, order, position, PnL ở mức nhập môn.
- Không cần biết trước kiến trúc dự án.

Nếu còn thiếu, học [Bài 00 — Chuẩn bị](00_CHUAN_BI.md) và hoàn thành toàn bộ
preflight trước khi sang bài 01.

## Cấu trúc khóa học

| Bài | Chủ đề | Thời lượng | Lab chính | Trạng thái hệ thống |
| --- | --- | ---: | --- | --- |
| [00](00_CHUAN_BI.md) | Môi trường và phương pháp học | 2–3h | Preflight + test contract | **HIỆN HÀNH** |
| [01](01_KIEN_TRUC.md) | Kiến trúc và authority | 3–4h | Trace một decision | **HIỆN HÀNH** |
| [02](02_DU_LIEU.md) | Dữ liệu và point-in-time | 4–5h | Phá và phát hiện future leak | **HIỆN HÀNH** |
| [03](03_CANONICAL_STRATEGY.md) | Canonical strategy | 5–6h | Audit và mở rộng candidate | **HIỆN HÀNH** |
| [04](04_BACKTEST_REPORT.md) | Backtest và report | 5–6h | Single-pair + audit report | **HIỆN HÀNH** |
| [05](05_TOURNAMENT.md) | Strategy tournament | 5–6h | Matrix smoke + failed cell | **HIỆN HÀNH / hardening** |
| [06](06_SELECTION_WFO.md) | WFO và statistical selection | 5–6h | Tự chấm candidate | **MỤC TIÊU** |
| [07](07_PROMOTION_RUNTIME.md) | Promotion và runtime | 4–5h | Trace artifact → runtime | **NỀN TẢNG HIỆN HÀNH** |
| [08](08_EXECUTION_RISK.md) | Execution và risk | 5–6h | Trace order lifecycle | **HIỆN HÀNH** |
| [09](09_PORTFOLIO_ROUTER.md) | Portfolio và regime router | 4–5h | Bài toán shared capital | **HIỆN HÀNH + MỤC TIÊU** |
| [10](10_VAN_HANH.md) | Vận hành và release | 4–5h | Release drill + rollback | **HIỆN HÀNH, mainnet gated** |
| [11](11_CAPSTONE.md) | Capstone end-to-end | 8–12h | Evidence review hoàn chỉnh | Tổng hợp |

Tài liệu hỗ trợ:

- [Mẫu nhật ký và bài làm](MAU_BAI_LAM.md)
- [Rubric chấm điểm](RUBRIC.md)
- [Đáp án và gợi ý](DAP_AN_GOI_Y.md) — chỉ mở sau khi tự làm
- [Thuật ngữ song ngữ](../THUAT_NGU.md)

## Lịch học đề xuất trong 6 tuần

| Tuần | Nội dung | Sản phẩm |
| --- | --- | --- |
| 1 | Bài 00–01 | Environment record + system map |
| 2 | Bài 02–03 | Data leakage report + strategy contract review |
| 3 | Bài 04–05 | Backtest report audit + tournament index |
| 4 | Bài 06–07 | Selection memo + promotion lineage |
| 5 | Bài 08–10 | Execution trace + operational release checklist |
| 6 | Bài 11 | Capstone và tự chấm theo rubric |

Mỗi buổi 90–150 phút:

```text
20% đọc mental model
25% đọc code/test
40% chạy lab hoặc tự code
15% viết kết luận và self-check
```

## Quy tắc học thực hành

1. Luôn làm trên branch/worktree học tập; không sửa trực tiếp production branch.
2. Mọi lệnh dài/quan trọng chạy qua `controlled_exec.py`.
3. Mỗi lab có output directory riêng; không tái sử dụng live state.
4. Ghi exact command, commit và data identity vào nhật ký.
5. Không nhìn đáp án trước khi nộp deliverable của mình.
6. Nếu kết quả khác tài liệu, điều tra artifact trước khi sửa code.
7. Không dùng API key thật hoặc gửi order thật trong khóa học.

## Cách đánh giá

| Thành phần | Trọng số |
| --- | ---: |
| Self-check cuối mỗi bài | 15% |
| Lab và artifact | 35% |
| Code/contract review | 20% |
| Capstone | 30% |

- `>= 85`: có thể tự nghiên cứu và review thay đổi quan trọng.
- `70–84`: hiểu luồng chính, cần hỗ trợ ở statistical/operations edge cases.
- `< 70`: học lại các bài có hard gate chưa đạt.

Không có điểm cộng cho return cao. Có điểm cho kết luận đúng rằng evidence chưa đủ.

## Bắt đầu

1. Đọc [Bài 00](00_CHUAN_BI.md).
2. Tạo bản sao [Mẫu bài làm](MAU_BAI_LAM.md).
3. Hoàn thành preflight và lưu kết quả.
4. Chỉ sang bài 01 khi toàn bộ exit gate bài 00 đạt.

