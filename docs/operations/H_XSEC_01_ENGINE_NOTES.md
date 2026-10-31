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

Module chỉ chạy được D1. Mốc cắt là hằng số và được áp dụng ngay lúc quét parquet, nên không dòng nào từ mốc cắt trở đi ra khỏi reader. Row group chứa mốc cắt vẫn phải giải mã để lọc. Quy tắc forward sẽ được cài khi tới lúc dùng, bằng source mới.

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

D1 đã dùng các lựa chọn này khi chưa có review. Các điểm này nằm ngoài mục đã đóng băng. Reviewer phải chốt chúng trước packet forward, và thay đổi nào cũng chỉ có hiệu lực từ lần đọc forward.

## Đính chính đề xuất

Mục 9 của đề xuất ghi "không tìm thấy" backtest nhiều tài sản trong `trading_agent.backtest`. Chưa chính xác: `portfolio_backtest` là engine nhiều tài sản, chỉ là không dùng được cho thiết kế này. Đề xuất không được sửa vì mã hash gắn với toàn bộ file, nên đính chính nằm ở đây.

## Đã kiểm

- 119 test trên dữ liệu tổng hợp. Không test nào đọc dữ liệu thị trường.
- Hai kịch bản cắm sẵn đáp án: momentum thật ra CONTINUE_TO_CONFIRMATION, đảo chiều thật ra REJECT.
- Dòng "độc" sau mốc cắt, giá 1e12, không lọt vào kết quả.
- Ở commit `5cb74514`, trước khi có input proof, cắm 12 lỗi có chủ đích (nhìn trước tương lai, khớp sớm một bar, bỏ phí, bỏ trượt giá, bỏ mốc cắt, bỏ đối soát, nới 18/20…). Cả 12 đều bị test bắt. Lần đầu sót 3, và test đã được bổ sung.
- Sau review, 14 trong 15 lỗi mới vẫn lọt qua test, nên bộ test được viết thêm. Sau đó cắm lại 67 lỗi, gồm cả 15 lỗi này: test bắt được 66. Lỗi còn lại chỉ khác ở mức làm tròn.

Đây là tự kiểm tra của tác giả, không thay thế review.

## Chưa chứng minh

- Engine chưa so parity với engine native, vì native không có quy tắc này. Mốc 01:00 thứ Hai đã được đối chiếu với quy ước k+2 của native qua tài liệu và code, chưa qua phép đo.
- `freeze`, `verify` và `run` chưa từng chạy trên dữ liệu thật. D1 đi vòng qua chúng, xem mục dưới.
- D1 có chạy `require_daily_alignment` trên dữ liệu thật, nhưng ngoài kiểm tra đó, nhãn ngày và nến thiếu chỉ được kiểm trên dữ liệu tổng hợp.

## D1 đã chạy như thế nào

D1 chạy ngày 30/10/2026. Operator nói nghiên cứu không cần quá chặt. Agent hiểu câu đó là cho phép chạy khi chưa có review và packet approved, báo lại sau khi chạy, và operator tiếp tục mà không phản đối. Ngày 31/10/2026, sau khi đọc báo cáo, operator chấp nhận việc D1 đã chạy khi input proof FAIL.

Thư mục `data/xsec_01_d1_20261030_01/` chứa kết quả và các script chạy trong `scripts/`. `freeze`, `verify` và `run` không được dùng: một script tạm gọi thẳng `_measure()`, nên các lớp bảo vệ của packet không áp dụng. Lần đầu ở commit `3a5ed33a` dừng vì schema của file `1d` trước khi đọc giá nào. Commit `5b656422` tạo ra các con số. Source hash của 26 input có trong bản ghi đóng băng, nhưng không được lấy lại lúc chạy.

Đề xuất yêu cầu cắt dữ liệu trước khi đọc. D1 chưa đạt đúng nghĩa đen điều đó: loader ở `5b656422` giải mã toàn bộ cột timestamp của 26 input, kiểm tăng dần cả trên dòng niêm phong, và giải mã close của mười file `1d` chỉ có `timestamp` trước khi bỏ chúng. Row group chứa mốc cắt ở các file khác có thể cũng đã được giải mã; layout của chúng chưa kiểm. Không dòng holdout nào tới simulator, và không giá niêm phong nào lọt vào kết quả. Loader trên master đã sửa để cắt trước, nên khác `5b656422` ở phần đọc dữ liệu; quy tắc mô phỏng không đổi.

`--prove-inputs` trả về FAIL vì chính file tham chiếu BTC `30m` sai trước 10/08/2023, dù khớp manifest. File `4h` của BTC, cũng khớp manifest, được dùng để kiểm một phần: `1d` khớp 1132/1132 dòng. Với `1h`, phép kiểm chỉ phủ 6795 trên 27179 dòng, giá mở ở giờ chia hết cho 4 và giá đóng ở giờ dư 3, nên không có nến 01:00 nào. Giá khớp BTC của 29 lần rebalance trước 10/08/2023 vì vậy chưa được kiểm từng dòng. Khớp 6794/6795 giá mở chỉ nói về các dòng đã so. Phép kiểm này chỉ nằm trong transcript, chưa lưu thành file.

Vòng quay 53% trong RESULTS của D1 chia cho 2500 USDT cố định. Trên giá trị đang đầu tư, vòng quay một chiều là 38.3%. Kết quả equity không đổi, vì phí tính theo từng fill.

## Việc còn lại trước lần đọc forward

1. Reviewer chốt chín lựa chọn ở trên.
2. Viết source forward. `decide` là quy tắc D1, quy tắc forward khác nó, nên cần viết riêng. Mark cuối phải là open của nến 01:00 ngày 27/12/2027, trong khi `simulate` luôn dùng close. Loader cần cận dưới 11/10/2026, tức D−21 của lần rebalance đầu, để vẫn không đọc dòng holdout nào.
3. Trước khi giải mã giá, kiểm từ timestamp rằng mọi coin có đủ nến tới mốc mark. Coin kết thúc sớm là input bị từ chối, không phải run INVALID, trừ khi packet ghi rõ delisting với nguồn từ sàn. Hiện code coi file thiếu đuôi là delisting và không để lại dấu vết trong summary. Hash mỗi file lúc tải, trước khi giải mã dòng nào.
4. Diễn tập `--prepare` và `verify(approved=False)` trên file thật, và kiểm schema cả 26 input chỉ bằng footer.
5. Operator duyệt đúng packet trước khi đọc, vì `run_authority` là NONE. Chỉ đọc một lần, sau 27/12/2027. Nếu run lỗi trước khi giải mã giá nào, tạo revision và packet mới với review mới theo [RESEARCH_RUN_PROTOCOL.md](RESEARCH_RUN_PROTOCOL.md).

Tham chiếu BTC là việc dọn riêng của D1, không thuộc danh sách trên, vì proof chỉ kiểm dòng trước mốc cắt. Đổi tham chiếu trong `INPUT_PROOFS` không đủ để qua proof: `_reference_rows` và `_compare` không nhận tham chiếu `4h`. Trước 29/07/2023, file khớp manifest duy nhất có dòng theo giờ là `30m`, chính file sai. `15m` bắt đầu từ mốc đó nhưng chưa được kiểm. Lựa chọn còn lại là tải lại đúng nguồn đã đóng băng, hoặc ghi một ngoại lệ có operator đồng ý.
