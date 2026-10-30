# H-XSEC-01: ghi chú engine cho reviewer

Status: CURRENT — đã xây, kiểm trên dữ liệu tổng hợp, và chạy D1 một lần ngày 30/10/2026. `run_authority: NONE`, market PAUSED.
Owner: research operator
Verified: 2026-10-30
Evidence: `src/trading_agent/backtest/xsec_relative_strength.py`, `tests/test_xsec_relative_strength.py`, [đề xuất đã đóng băng](../proposals/H_XSEC_01_RELATIVE_STRENGTH.md)

Tài liệu này dành cho bước 3 của đề xuất: reviewer đọc source, assertion và raw receipt. Nó không phải review và không cấp quyền chạy.

## Đã xây

| Phần | Nội dung |
| --- | --- |
| Mô phỏng | `simulate`: một số dư tiền mặt, chỉ trade phần chênh, bán trước mua sau |
| Quyết định tuần | `plan_steps`, `top_coins`, `arm_picks`: 22 nhánh dùng chung một lịch |
| Đối soát | `reconcile`: dựng lại tiền và vị thế chỉ từ fills. `run_arms` đối soát mọi nhánh |
| Quy tắc | `decide`: đúng quy tắc D1 đã đóng băng |
| Đọc dữ liệu | `load_series`: cắt tại 06/02/2026 12:00 UTC, trước holdout |
| Packet | `freeze`, `verify`, `run`: ghi một lần, không resume, PENDING không chạy được |

Module chỉ chạy được D1. Không có tham số đổi mốc cắt, nên nó không đọc được holdout hay cohort forward. Quy tắc forward sẽ được cài khi tới lúc dùng, bằng source mới.

Module không đăng ký vào strategy registry, nên runner không thể tới nó. `portfolio_backtest` không được dùng lại vì nó đánh giá runtime đã promote theo từng symbol qua allocator, còn thiết kế này so một quy tắc cố định với các nhánh đối chứng cố định.

## Lựa chọn ngoài văn bản đóng băng

Đặc tả đóng băng không nói rõ các điểm dưới đây. Tác giả đã chọn trước khi đọc bất kỳ giá nào, và reviewer cần xét từng điểm:

| Điểm | Lựa chọn |
| --- | --- |
| Mark cuối khi thiếu nến mark | Close của nến gần nhất ở hoặc trước mốc mark |
| Mark cuối | Không tính phí thoát, ở mọi nhánh |
| Coin được coi là ngừng giao dịch lúc rebalance | Không có nến 1h nào từ giờ khớp đến nến mark |
| Coin được coi là ngừng giao dịch lúc mark | Nến cuối cũ hơn 24 giờ so với nến mark |
| Giỏ khi universe co lại | 25% chia đều cho số coin còn lại |
| Thứ tự lệnh | Bán trước, mua sau, theo tên symbol |
| Coin rời top 4 | Bán toàn bộ số lượng đang giữ |
| Lệnh mua | Số lượng = phần chênh / giá khớp, nên notional đúng bằng phần chênh |
| Căn chỉnh nến ngày | Thêm kiểm tra: close ngày của mỗi Chủ nhật phải bằng close nến 23:00 (sai số 1e-6), nếu không run là INVALID. Sai nhãn ngày sẽ làm tín hiệu nhìn trước tương lai |

Sai lệch nào reviewer không chấp nhận thì sửa trước khi chuẩn bị packet. Các điểm này nằm ngoài mục đã đóng băng, nhưng phải chốt trước khi đọc giá.

## Đính chính đề xuất

Mục 9 của đề xuất ghi "không tìm thấy" backtest nhiều tài sản trong `trading_agent.backtest`. Chưa chính xác: `portfolio_backtest` là engine nhiều tài sản, chỉ là không dùng được cho thiết kế này. Đề xuất không được sửa vì mã hash gắn với toàn bộ file, nên đính chính nằm ở đây.

## Đã kiểm

- 45 test trên dữ liệu tổng hợp. Không test nào đọc dữ liệu thị trường.
- Hai kịch bản cắm sẵn đáp án: momentum thật ra CONTINUE_TO_CONFIRMATION, đảo chiều thật ra REJECT.
- Dòng "độc" sau mốc cắt, giá 1e12, không lọt vào kết quả.
- Cắm 12 lỗi có chủ đích (nhìn trước tương lai, khớp sớm một bar, bỏ phí, bỏ trượt giá, bỏ mốc cắt, bỏ đối soát, nới 18/20…). Cả 12 đều bị test bắt. Lần đầu sót 3, và test đã được bổ sung.

Đây là tự kiểm tra của tác giả, không thay thế review.

## Chưa chứng minh

- Engine chưa so parity với engine native, vì native không có quy tắc này. Mốc 01:00 thứ Hai đã được đối chiếu với quy ước k+2 của native qua tài liệu và code, chưa qua phép đo.
- Hành vi trên dữ liệu thật: nhãn ngày, nến thiếu, kiểu timestamp. `require_daily_alignment` sẽ kiểm một phần lúc chạy.

## D1 đã chạy như thế nào

D1 chạy ngày 30/10/2026 trên packet local `data/xsec_01_d1_20261030_01/`, theo quyết định của operator rằng nghiên cứu không cần chặt tới mức đó. Ba bước đã bỏ qua: review độc lập trước khi chạy, packet approved, và merge trước khi chuẩn bị packet.

`--prove-inputs` trả về FAIL vì chính file tham chiếu BTC `30m` sai trước 10/08/2023, dù khớp manifest. File `4h` của BTC, cũng khớp manifest, đã dùng để phân xử: `1d` khớp 1132/1132 dòng, `1h` khớp 6795 giá đóng cửa và 6794/6795 giá mở cửa. `INPUT_PROOFS` vẫn ghi `30m`, nên kiểm tra này sẽ tiếp tục báo FAIL cho tới khi đổi tham chiếu.

## Việc còn lại trước lần đọc forward

1. Simulator chặn cứng ở mốc 06/02/2026, nên cần một loader đọc được cohort forward và phải test trên dữ liệu tổng hợp trước.
2. Đổi tham chiếu của BTC trong `INPUT_PROOFS`.
3. Operator duyệt đúng packet trước khi đọc, vì `run_authority` là NONE. Chỉ đọc một lần, sau 27/12/2027.
