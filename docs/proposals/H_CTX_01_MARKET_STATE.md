# H-CTX-01: tham số trạng thái thị trường

Status: HISTORICAL — đã khóa và đo ngày 30/10/2026. Bước 1 không đạt (0 STRONG, 1 HINT), H-CTX-01 dừng, bước 2 không mở. Phần dưới giữ nguyên bản đã khóa, kể cả những điểm sau này thấy sai: đo 158 tuần (P3 và P4 là 157), P6 chỉ phủ 24 đến 29 coin và phần thiếu chủ yếu do ghép tên, 0.23 là ngưỡng tới hạn, premium và taker chỉ tải cho BTC.
Owner: research operator
Verified: 2026-10-30. Kết quả: [H_CTX_01_RESULTS.md](../operations/H_CTX_01_RESULTS.md).
Evidence: [tổng hợp nghiên cứu](../operations/RESEARCH_SYNTHESIS_20261030.md), [playbook §10](../operations/MULTI_STRATEGY_RESEARCH_PLAN.md#10-kế-hoạch-btc-context-và-policy-riêng-theo-pair), packet local `data/xsec_02_explore_20261030_01/`, `data/xsec_03_explore_20261030_01/`

## 1. Vì sao đổi cách tiếp cận

Nghiên cứu BTC context đã chỉ ra hai điều:

- **H1:** lợi ích của PLUS chủ yếu đến từ ít lệnh hơn, không phải từ thông tin BTC.
- **H3:** policy gốc có kỳ vọng âm, nên bộ lọc nào đặt lên cũng không cứu được.

Vòng xếp hạng và bộ lọc xu hướng cho kết quả cùng chiều: giảm rủi ro được, nhưng chỉ vì đầu tư ít hơn. Vậy thay vì thêm một bộ lọc cứng, ta dùng BTC context và dữ liệu phái sinh làm **tham số suy luận về trạng thái thị trường**, và đo trực tiếp xem chúng có báo trước lợi nhuận tuần tới không.

Đo thông tin trước khi giao dịch có ba lợi ích: không bị trộn với tác dụng của việc ít lệnh, không phụ thuộc vào một quy tắc gốc yếu, và rẻ hơn nhiều so với chạy mô phỏng.

## 2. Tham số suy luận

Đo tại close Chủ nhật, chỉ dùng dữ liệu đã có tại thời điểm đó. Chiều dự đoán được ghi trước.

| ID | Tham số | Nhóm | Chiều dự đoán |
| --- | --- | --- | --- |
| P1 | `btc_simple_return_24` | C1 BTC context, có sẵn | + |
| P2 | `btc_realized_vol_24` | C1, có sẵn | − |
| P3 | Trung vị `corr_168` của top 13 với BTC | C2, có sẵn | − |
| P4 | Trung vị `residual_sum_24` của top 13 | C2, có sẵn | + |
| P5 | Tổng funding 7 ngày của BTC | G6, mới | − |
| P6 | Trung vị funding 7 ngày của top 30 | G6, mới | − |
| P7 | Premium index trung bình 7 ngày của BTC | G6, mới | − |
| P8 | Tỷ lệ taker buy trên volume spot BTC, 7 ngày | G5, mới | + |
| P9 | Tỷ lệ top 30 có return 21 ngày dương | G4, tính từ dữ liệu đã có | + |

Lý do cho chiều G6: funding hoặc basis cao nghĩa là phe long đang đông và trả tiền để giữ vị thế. Đây là giả thuyết, chưa phải bằng chứng.

Funding và premium chỉ dùng làm thông tin. Sản phẩm vẫn là spot long-only, không mở perp hay short.

## 3. Mục tiêu dự đoán

Lợi nhuận từ open 01:00 UTC thứ Hai tới open 01:00 UTC thứ Hai kế tiếp, cùng quy ước khớp lệnh:

| ID | Mục tiêu | Câu hỏi |
| --- | --- | --- |
| T1 | BTC | Có nên đứng ngoài BTC tuần này? |
| T2 | Giỏ đều top 13 theo quy tắc | Có nên đứng ngoài thị trường? |
| T3 | T2 trừ T1 | Altcoin hay BTC tốt hơn tuần này? |

## 4. Bước 1: đo thông tin

159 tuần, cùng cửa sổ, 9 tham số × 3 mục tiêu = 27 phép thử. Thống kê là tương quan hạng Spearman, p một phía theo chiều đã ghi, tính bằng block bootstrap khối 8 tuần, 10 000 lần.

| Mức | Điều kiện |
| --- | --- |
| STRONG | Đúng chiều, p < 0.05/27, cùng dấu ở cả hai nửa thời gian |
| HINT | Đúng chiều, p < 0.05, cùng dấu ở cả hai nửa thời gian |

Với 27 phép thử, chỉ do ngẫu nhiên đã có thể ra khoảng 1 đến 2 HINT, nên một HINT đơn lẻ không có ý nghĩa. Sai số của tương quan khoảng 0.08, nên STRONG cần tương quan cỡ 0.23 trở lên.

## 5. Bước 2: chỉ làm nếu bước 1 đạt

Điều kiện: có ít nhất một STRONG, hoặc ít nhất hai HINT trên cùng một mục tiêu.

Quy tắc cố định: lấy trung bình phân vị (tính bằng cửa sổ mở rộng, không nhìn trước) của các tham số đạt, đã chỉnh theo chiều. Trên 0.5 thì giữ mục tiêu với exposure 25%, ngược lại để tiền mặt. Với T3 thì giữ giỏ top 13 khi trên 0.5, ngược lại giữ BTC.

Đối chứng: đường có exposure không đổi bằng mức trung bình của quy tắc, và 1000 đường xáo trộn các tuần.

SUPPORTED chỉ khi ở phí 2x: lãi ròng >0, cao hơn đối chứng cùng mức đầu tư, từ phân vị 95 trở lên ở phí 0, và không âm ở ít nhất 3 trong 4 năm. Nếu bước 1 không đạt thì dừng, không sang bước 2.

## 6. Dữ liệu cần tải

Chỉ dùng API công khai, không cần API key, không dùng Binance Vision, chỉ tới mốc cắt 06/02/2026 12:00 UTC.

| Nguồn | Endpoint | Phạm vi | Ước tính |
| --- | --- | --- | --- |
| Funding | `fapi/v1/fundingRate` | 165 coin, từ 11/2022 | ~8 MB |
| Premium index | `fapi/v1/premiumIndexKlines`, 1d | 165 coin | ~3 MB |
| Taker buy | `api/v3/klines`, 1d | 180 coin | ~3 MB |

Khoảng 15 MB, 1000 lệnh gọi, vài phút. Lưu tại `data/raw/binance_xsec03/`.

**Không gồm:** open interest và tỷ lệ long/short. REST chỉ giữ 30 ngày; lịch sử nằm ở Binance Vision, cần quyết định về giấy phép dữ liệu.

## 7. Giới hạn

- Giá BTC trong cửa sổ này đã bị đọc nhiều lần, nên kết quả chỉ là khám phá, không phải xác nhận.
- Giai đoạn 2023–2026 chỉ chứa một chu kỳ thị trường lớn.
- 15 trong 180 coin không có hợp đồng trùng tên, chủ yếu do đổi tên hoặc hủy niêm yết, nên P6 phủ không đủ.
- Nếu đạt, vẫn cần một cohort forward riêng trước khi tin.
