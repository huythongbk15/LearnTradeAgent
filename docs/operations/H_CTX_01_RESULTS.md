# H-CTX-01: kết quả bước 1

Status: HISTORICAL — đo ngày 30/10/2026, đo khám phá, không phải xác nhận.
Owner: research operator
Verified: 2026-10-30
Evidence: [đề xuất đã khóa](../proposals/H_CTX_01_MARKET_STATE.md). Bảng số, script và audit nằm trong packet local `data/xsec_04_context_20261030_01/`, không có trên master.

**Bước 1 không đạt: 0 STRONG, 1 HINT.** Bước 2 chỉ mở khi có ít nhất một STRONG, hoặc hai HINT trên cùng một mục tiêu, nên H-CTX-01 dừng theo mục 5 của đề xuất đã khóa. Đây là đo khám phá trên cửa sổ đã đọc nhiều lần. Kết luận này chỉ áp dụng cho H-CTX-01; lần đọc forward của H-XSEC-01 vẫn giữ nguyên.

Tương quan hạng (IC) giữa tham số và lợi nhuận tuần kế tiếp, trên 158 tuần (157 tuần với P3 và P4):

| Tham số | Chiều ghi trước | T1 BTC | T2 giỏ top 13 | T3 giỏ trừ BTC |
| --- | --- | ---: | ---: | ---: |
| P1 return BTC 24h | + | −0.113 | −0.120 | −0.131 |
| P2 vol BTC 24h | − | +0.095 | +0.103 | +0.093 |
| P3 `corr_168` top 13 | − | +0.077 | +0.067 | +0.024 |
| P4 `residual_sum_24` top 13 | + | −0.046 | +0.001 | +0.014 |
| P5 funding BTC 7 ngày | − | −0.081 | −0.089 | −0.073 |
| P6 funding top 30, 7 ngày | − | −0.033 | −0.084 | **−0.125 (HINT)** |
| P7 premium BTC 7 ngày | − | +0.028 | −0.039 | −0.091 |
| P8 taker buy BTC 7 ngày | + | +0.054 | −0.015 | −0.095 |
| P9 breadth 21 ngày | + | −0.054 | −0.072 | −0.085 |

HINT duy nhất là P6 trên T3: p 0.0251, nửa đầu −0.137, nửa sau −0.180. Toàn bộ p và hai nửa của 27 ô nằm trong `step1.json` của packet local.

## Đọc kết quả

**Kết quả chỉ đủ sức thấy hiệu ứng rất lớn.** Để có 80% power cần |IC| khoảng 0.20 cho HINT và 0.30 cho STRONG. Mức đủ trả phí 2x thấp hơn nhiều: khoảng 0.01 đến 0.06 trên T1 và T2, tùy tín hiệu đổi trạng thái nhanh hay chậm, và khoảng 0.04 đến 0.12 trên T3 vì mỗi lần đổi phải giao dịch cả hai chân. Dưới khoảng 0.30, kết quả là chưa kết luận được, không phải bằng chứng rằng các tham số không có thông tin.

**HINT không tách được khỏi ngẫu nhiên.** Khi không có tín hiệu nào, bảng 27 ô vẫn cho trung bình 1.2 đến 1.3 HINT. Sau hiệu chỉnh cho cả họ phép thử, p của HINT này khoảng 0.31 đến 0.39 (0.31 ở p ước lượng lại 0.0219, 0.34 đến 0.39 ở p khóa 0.0251). Funding của chính top 13, tức các coin T3 nắm giữ, chỉ cho −0.089, p 0.074.

Các kiểm tra sau đều là đọc post hoc, không phải kết luận:

- IC riêng phần khi kiểm soát T3 các tuần trước còn −0.079 đến −0.096. Dữ liệu không tách được T3 tự đảo chiều với funding gây ra đảo chiều.
- Bỏ ZEC khỏi giỏ thì còn −0.099, p 0.062, nhưng đây là mức yếu nhất trong 82 lần bỏ một coin nên đã được chọn lọc.
- Một phép thử đi ngược lại: IC trong từng năm là −0.182, nhưng nó dùng trung bình năm chỉ biết sau đó, nên chỉ ghi lại, không phải bằng chứng.

**Các hàng ngược chiều không phải phát hiện.** Bốn hàng ngược chiều hoàn toàn là P1, P2, P3 và P9, và không ô nào trong đó có p hai phía dưới 0.05 (thấp nhất 0.095). Khi không có tín hiệu, có từ bốn hàng như vậy trở lên xảy ra 28% đến 30% số lần. P1 trên T3 nếu tính theo chiều ngược sẽ thành HINT thứ hai, nhưng kế hoạch ghi một chiều cho mỗi tham số nên ô này không được tính.

**Giờ khớp lệnh.** Cổng đóng ở giờ khớp đã khóa 01:00. Xem lại sau khi đo, cổng mở ở 7 trong 24 giờ, điều mà ngẫu nhiên tạo ra khoảng 10% đến 25% số lần tùy điều kiện xét. Vì vậy điều này không nói gì về tín hiệu và không phải lý do để đo lại ở giờ khác.

**Cổng bước 2 lỏng hơn vẻ ngoài.** Khi không có tín hiệu, cổng vẫn mở 19% đến 24% số lần, chủ yếu qua đường hai HINT. P5, P6, P7 gần như một tín hiệu (tương quan hạng 0.75 đến 0.85). Cổng sau này nên đếm theo khái niệm, không theo ô.

## Sai lệch so với kế hoạch

- 158 tuần thay vì 159: tuần 02/02/2026 không đủ 7 ngày nên bị bỏ. Thêm nó như một dòng thiếu thì kết quả không đổi (P6 trên T3 −0.127).
- P3 và P4 có 157 tuần vì BTC thiếu nến 2023-03-24 13:00. Lần chạy đầu của script crash ở dòng này trước khi in thống kê nào, và bản sửa để ô đó là thiếu.
- Premium và taker chỉ tải cho BTC, vì P7 và P8 chỉ cần BTC. Tổng dữ liệu 5.2 MB so với ước tính 15 MB.
- Con số 0.23 ở mục 4 là ngưỡng tới hạn, không phải cỡ hiệu ứng phát hiện được. Tại đó power chỉ khoảng 47% đến 49%.
- Block bootstrap chỉ lấy mẫu lại hạng của mục tiêu, giữ nguyên tham số, và khối nối vòng; kế hoạch chỉ ghi khối 8 tuần. Cách này không đổi kết luận, nhưng là lý do P6 lệch 6.8% đến 7.7% ở mục Kiểm chứng.
- Một số định nghĩa chỉ nằm trong `xsec04_measure_final.py`, không có trong kế hoạch: BTC không nằm trong trung vị top 13, funding cần ít nhất 15 lần thanh toán, cửa sổ funding không gồm kỳ 00:00 thứ Hai, P7 là trung bình 7 giá trị cuối ngày, P8 là tỷ lệ của hai tổng 7 ngày.
  - BTC trong trung vị đã đổi thử trên P3 và P4, không ô nào đổi mức (IC lệch tối đa 0.017).
  - Ngưỡng 15 lần và cửa sổ funding chỉ đổi thử trên P6: P6 trên T3 vẫn là HINT, IC −0.121 đến −0.126. Ngưỡng không ảnh hưởng P5 vì BTC luôn có 21 lần thanh toán, nhưng P5 chưa được đổi thử cửa sổ, nên ba ô P5 chưa kiểm tra.
  - P7 chỉ thử được bằng proxy, p 0.08 đến 0.31, không thành HINT thứ hai.
  - P8 chưa được đổi thử. Biến thể trung bình 7 tỷ lệ ngày tính được từ chính dữ liệu đã tải, nhưng chưa chạy.

## Lỗi đã biết, ghi lại và không chạy lại

- **P6 không đủ 30 coin:** chỉ 24 đến 29 coin mỗi tuần. 68% số coin-tuần thiếu là PEPE, SHIB, BONK, FLOKI, LUNC, do script tải ghép đúng tên, không phải đổi tên hay hủy niêm yết như mục 7 của đề xuất viết. Nguyên nhân có thể là hợp đồng tiền tố 1000, nhưng chưa xác nhận.
- **Danh sách funding lấy từ hợp đồng ngày 30/10/2026:** giá trị P6 không nhìn trước, nhưng tập coin có file phụ thuộc vào sự kiện sau đó. 2000 lần điền ngẫu nhiên phần thiếu cho IC −0.145 đến −0.104, vẫn không tới STRONG.
- **Regex token đòn bẩy loại nhầm JUP và SYRUP** khỏi universe của xsec_02 và xsec_04. Chưa đo trực tiếp được. Thay bằng coin hạng 14 đến 30 làm proxy, cổng mở 0 trong 3000 lần khi đổi 10 hoặc 20 tuần, 0.8% khi đổi 40 tuần và 3.6% khi đổi cả 96 tuần đủ điều kiện. Đây là đổi ngẫu nhiên. Nếu chọn tuần xấu nhất sau khi biết kết quả thì 6 tuần đã đủ mở cổng, hoặc 20 tuần nếu chặn lợi nhuận ở tứ phân vị.
- **PAXG và USTC lọt vào universe.** Bỏ hai coin này, cả 27 ô giữ nguyên mức.
- **Giá cuối tuần không có chặn trên trong `xsec04_measure_final.py`.** Lỗi này xảy ra 0 lần trong 2054 coin-tuần, nhưng không nên dùng lại đoạn này cho universe khác khi chưa thêm chặn.
- **Con số +48.2% của 25% BTC trong RESULTS của xsec_02 khác cơ sở so sánh:** đó là mua giữ không rebalance. Nếu rebalance hằng tuần như các ô khác thì là +38.9% trên cùng cửa sổ 159 lần rebalance, hoặc +44.0% nếu bỏ tuần lẻ cuối, chưa tính phí. Kết luận BTC cao hơn mọi ô theo quy tắc vẫn đúng.

## Kiểm chứng

Bốn agent trong cùng phiên kiểm tra lại: ba agent dựng lại từ dữ liệu thô, agent thống kê dùng nguyên `weekly_table.parquet`. Đây không phải review độc lập, và H-CTX-01 chưa có reviewer.

- Cả bốn tái tạo được 27 IC; ba agent dựng từ dữ liệu thô cũng khớp bảng tuần tới sai số làm tròn.
- Không tìm thấy nhìn trước trong giá trị tham số: 4266 phép thử đổi giá trị vô lý và 1422 phép thử cắt dữ liệu không làm tham số nào đổi. Các phép thử này chạy trên bản dựng lại, không phải trên script đo; bảng đo thừa hưởng kết quả vì khớp bản đó tới sai số làm tròn. Bản cố tình rò rỉ bị phát hiện ở cả 9 tham số.
- Không file nào có dòng từ mốc cắt 06/02/2026 12:00 UTC trở đi.
- Phép thử gần đúng cỡ: tỷ lệ dương tính giả gộp 4.8% đến 5.3% ở mức 5% trên các null trí nhớ ngắn. Có hai trường hợp lệch nhẹ về phía thuận lợi: nếu phổ mẫu của mục tiêu là thật thì gộp 5.9%, từng ô tới 9.4% ở T1 và T2, nhưng không ô nào gần ngưỡng; với P6 là 6.8% đến 7.7%, nên chỉ làm HINT yếu đi.
- Benjamini-Hochberg ở 10% (chỉ để khám phá, trên p hai phía) không bác bỏ ô nào: 0 trong 27 ô có ý nghĩa, q thấp nhất 0.55.

Số lần nhìn: 648 ô quét giờ khớp sau khi đo (24 giờ × 27 ô, đã gồm 27 ô khóa ở 01:00), cộng các biến thể độ nhạy trong `audit/` của packet local chưa đếm chính xác. Các lần nhìn thêm chỉ là kiểm tra, không có ô nào được dùng để đổi kết luận.

Phơi nhiễm dữ liệu: BTC 2020–2022 không còn là "chưa biết" như mục 7 của `docs/operations/RESEARCH_SYNTHESIS_20261030.md`. Kết quả T8A đã đưa 940 nến ngày BTC của giai đoạn này qua một pool 16 strategy, và các dòng lệnh đã lưu có những lần chạy BTC tháng 3–4/2020 và tháng 10–11/2022. Điều đó cho thấy đường giá đã được dùng, chưa cho thấy ai đã nhìn P1 hay P2 so với lợi nhuận tuần kế tiếp.

Bằng chứng nằm trong `audit/` của packet local: `workflow_result.json` chứa toàn bộ báo cáo, phát hiện và tổng quan tài liệu; các thư mục con chứa script và output dạng văn bản; `completeness/c03_sweep.out` là bảng 24 giờ. Mảng nhị phân 231 MB không được chép vào đây.
