# Phép đo chân short trên futures

Status: HISTORICAL — kết quả một lần đo ngày 31/10/2026. Market vẫn PAUSED cho mọi việc khác.
Owner: research operator
Verified: 2026-10-31. Một bản viết lại riêng từ protocol, do agent trong cùng phiên làm nên không phải review độc lập, khớp mọi lệnh, trừ một lệnh long ZEC của R2 (hòa SMA tuyệt đối), lệnh này lệch ở cả ba lần chạy R2 của Block A. Lệch lớn nhất trên chân short là 0.029 điểm/năm, trên mọi book là 0.446. Khớp chỉ cho thấy hai bản đọc protocol giống nhau.
Evidence: packet local `data/short_side_20261031_01/` (`PROTOCOL.md`, `result_A.json`, `result_B.json`, `verify/`), [tracker](../CAMPAIGN_STATUS.md)

**Thẩm quyền.** Operator yêu cầu ngày 31/10/2026: only long có vẻ không ổn, thử xem short trên futures có làm tăng lợi nhuận và có đáng xem xét không. Đây là ngoại lệ một lần, exploratory, trên dữ liệu đã đọc trước holdout. Không có promotion, không đổi cổng nào, mainnet vẫn NO-GO.

## Kết luận

**Trên ba quy tắc đã đo, thêm short không làm tăng lợi nhuận.** Ở thiết lập chính, short làm book giảm ở cả 12 ô của protocol (3 quy tắc, 2 block, chi phí 1x và 2x; Block B chưa tính funding) và không giảm được drawdown. Ngoài hai năm dương nhỏ ở 1x, nó chỉ kiếm tiền khi thị trường giảm. Hiện chưa đáng xem xét như một chân lợi nhuận. Không quy tắc nào đạt mức `worth a designed test`.

Kết quả này không chứng minh short không có lợi thế: trên Block A, tức block chính, R2 và R3 là `cannot tell` vì khoảng bất định quá rộng, chỉ R1 là `clearly worse`. Block B là `cannot tell` ở cả ba quy tắc, nên không xác nhận từ `clearly worse` của R1.

## Cách đo

- Ba book cùng tín hiệu và cùng ledger: S0 long spot, P1 long perp, P2 = P1 + short. Hiệu ứng short = P2 − P1.
- Ba quy tắc lấy từ tham số có sẵn, không tinh chỉnh: R1 `volatility_breakout` (tham số H-VOLBREAK-01), R2 giao cắt SMA 20/80, R3 momentum 21 ngày, cập nhật hằng tuần. Chân short là chính quy tắc đó chạy trên chuỗi giá gương.
- Block A: 13 coin, 2023-01-01 đến 2026-02-05, giá spot cộng funding thật. Block B: BTC, ETH, BNB, 2020–2022, chưa có funding.
- Vào lệnh ở open bar sau tín hiệu, stop 5%, đòn bẩy 1x, notional cố định, chi phí 20 bps khứ hồi trên perp và 30 bps trên spot.
- Ledger tái lập đúng run gốc của H-VOLBREAK-01 khi dùng cùng đường tín hiệu (BTC, long, k+2): 53/53 lệnh, cùng điểm vào, cùng điểm ra, gross 19.06% (tổng gross trên notional của từng lệnh, nên không so trực tiếp với return trên vốn).

## Hiệu ứng short

Đơn vị là điểm phần trăm vốn mỗi năm, cộng dồn, notional cố định. Cặp số a/b trong toàn trang là chi phí 1x/2x. Khoảng tin cậy 90% ở chi phí 1x. Block B chưa tính funding. Quy tắc gán từ kết luận được chốt trước khi đo.

| Block | Quy tắc | Chi phí 1x | Chi phí 2x | Khoảng 90% | Năm 2022, 1x | Kết luận |
| --- | --- | ---: | ---: | --- | ---: | --- |
| A | R1 breakout | −23.3 | −34.2 | −34.8 đến −11.6 | | clearly worse |
| A | R2 SMA 20/80 | −3.3 | −17.1 | −33.3 đến +28.0 | | cannot tell |
| A | R3 momentum | −6.9 | −9.2 | −27.7 đến +15.2 | | cannot tell |
| B | R1 breakout | −8.2 | −19.8 | −30.8 đến +13.4 | +14.0 | cannot tell |
| B | R2 SMA 20/80 | −21.7 | −35.0 | −57.6 đến +17.5 | +19.7 | cannot tell |
| B | R3 momentum | −6.7 | −9.0 | −35.6 đến +25.9 | +19.6 | cannot tell |

- **Gần như chỉ có lãi khi giảm giá.** Năm 2022 ở Block B dương cả ba quy tắc. Các năm khác của hai block chỉ dương ở R2 năm 2024 (+4.1) và R3 năm 2025 (+1.0), đều ở 1x và đều âm ở 2x. Ngoài ra là 36 ngày đầu 2026 của Block A: +2.2, +30.5, +1.2 ở 1x, tính trên vốn, chưa quy năm. Cửa sổ an toàn kết thúc đúng đáy (12/13 coin).
- **Không giảm drawdown.** Đo bằng điểm vốn ở 1x, kế toán cộng dồn. Từ P1 sang P2, Block A: −19.3 → −71.8, −42.9 → −69.2, −60.3 → −60.1. Block B: −20.9 → −39.9, −77.4 → −100.7, R3 không đổi ở −140.6. Trường hợp tốt nhất là R3 Block A, chỉ nhích 0.2 điểm.
- **Funding không đổi được dấu.** Ở Block A funding thật đã nằm trong bảng trên: tính trên notional đang short, funding nhận được là −0.5, +2.7, +2.9%/năm. Block B cần 125, 51, 22%/năm mới hòa ở 1x, còn dải giả định chỉ là ±8%. Với Block B đây là suy luận, vì chưa có funding thật.
- **Biến thể.** Trong các biến thể của protocol, dấu chỉ dương ở R3 không stop trong Block A (+2.9/+1.6, Block B −9.8/−11.0) và R1 k+2 trong Block B ở 1x (+4.1, 2x −7.4). Không biến thể nào đúng ở cả hai block.
- **R1 theo đường windowed.** Đây là độ nhạy do bước kiểm chứng chạy, số chính giữ nguyên. Block A −20.86/−31.48, từ kết luận không đổi. Block B −30.37/−42.05, cận trên của khoảng 90% là −10.84 so với +13.4 ở số chính. R1 short ở Block B đi từ +4.05 đến −30.37 tùy đường tín hiệu và timing, nên R1 ở Block B không vững.
- **Stop (Block A).** Khớp ở mức trigger đứng vững làm số chính: 1,144/1,145 stop đo trên nến 1 phút khớp đúng trigger. Biến thể khớp ở đáy hoặc đỉnh của bar quá nặng, không nên dùng làm kịch bản xấu; cận thấp hợp lý của R2 short, theo suy luận, là −6.5/−20.2. Bằng chứng này yếu vì nến phút không thấy trượt giá bên trong phút, và 41% số stop của Block A không có nến nhỏ. Stop của Block B chưa đo.

## Một đầu mối còn lại

Phần này nằm ngoài protocol, là kiểm tra thêm của bước kiểm chứng, không phải kết quả của phép đo. Nó chỉ có ý nghĩa nếu operator mở lại research. Ledger ở đây reset notional mỗi thứ Hai, nên số của danh sách chọn tay khác +2.9/+1.6 ở trên.

R3 không stop có short dương trên top 30 theo quy tắc (+13.0/+11.1), âm trên top 13 theo quy tắc (−5.4/−7.2) và quanh 0 trên danh sách chọn tay (+0.7/−0.8). Đây chưa phải bằng chứng:

- Gần như toàn bộ đến từ đợt giảm 2025–26. Nếu kết thúc ở 2025-10-05, top 30 là −9.2/−11.0.
- Short thụ động cùng danh sách đạt +18.2/+16.8, cao hơn quy tắc.
- So với cùng coin ở thời điểm dịch chuyển, p = 0.43 ở 2x: không thấy kỹ năng chọn thời điểm.
- Dữ liệu này không có high và low nên không đo được stop thật. Dấu ở 2x khi có stop vì vậy chưa rõ.

## Điều futures thực sự đổi được

Hiệu ứng venue (P1 − S0), tức chuyển long sang perp, ở Block A là +4.2/+8.9, +2.6/+9.5, −2.5/−1.4 điểm/năm cho R1, R2, R3. Quy tắc giao dịch nhiều được lợi vì phí giả định thấp hơn. Quy tắc giữ lâu thì thiệt vì long trả funding, 9.3 đến 9.8%/năm trên notional đang giữ. Con số này dựa trên biểu phí giả định và không cứu được quy tắc nào.

## Hai phát hiện phụ

**Chân long của R3 không phải lợi thế.** Con số +65 điểm/năm của S0 trong Block A là số học đúng theo protocol nhưng không ai kiếm được. Notional cố định để một lệnh lớn vô hạn: reset hằng tuần chỉ còn +36 điểm/năm, lãi kép +30 đến +35%/năm, thấp hơn mua và giữ cùng danh sách (lãi kép +38 đến +41%/năm). Cùng ledger, không stop, tính bằng điểm/năm: danh sách chọn tay +69, top 13 theo quy tắc +4.7, top 30 −5.0. 10 trong 437 lệnh chiếm 105% lợi nhuận. Kết luận của H-TSMOM-01 vẫn đứng.

**Research không đo đúng `volatility_breakout` như đã viết.** Mỗi bar của đường tournament chỉ thấy warmup+1 dòng, nên quy tắc quên vị thế đang mở: entry rời khỏi vùng hợp lệ sau 5 bar và lệnh bị đóng. 38/53 lệnh của H-VOLBREAK-01 thoát theo cách đó. Trên cùng dữ liệu BTC 2024, tín hiệu tính từ toàn bộ lịch sử cho 56 lệnh và gross 39.69% thay vì 19.06%. Bản này gần quy tắc như viết hơn nhưng chưa trùng hẳn: sau khi thoát vì max hold, tín hiệu trở lại 1 và lệnh vào lại không cần breakout mới.

- Rủi ro này đã được công bố trong prereg và có test ghim lại (`tests/test_volatility_breakout_admission.py`), nhưng chưa ai đo cỡ của nó.
- `regime_switching` và `range_mean_reversion` cũng bị ảnh hưởng, `stat_arbitrage_ls` chỉ lệch ở tín hiệu; 17/21 strategy (20 canonical cộng `di_adx_state_edge`) không lệch ở tham số mặc định trên bar tổng hợp.
- Riêng việc warmup không đổi theo tham số khiến 536 ô rsi period 21 và 42 ô trend_pullback slow 120 không thể giao dịch. Các ô đó là chưa được thử, không phải đã trượt.
- Không có look-ahead, không lỗi kế toán. Bản toàn bộ lịch sử cũng không chắc tốt hơn: dấu đổi theo coin và timing. Chưa rõ verdict nào đổi, vì chưa chạy lại native. Sửa warmup cần quyết định riêng.

## Giới hạn

- Giá dùng spot thay perp. Không có nến perp, mark price, basis theo coin, thanh lý hay ADL.
- Block B chưa có funding. Năm giảm 2022 chỉ đo được sau chi phí, trước funding.
- 13 coin là danh sách sống sót và thực chất chỉ như khoảng 2 phép thử độc lập.
- Mọi p-value của `random_book` và phần tách drift/timing trong file kết quả đều bị rút lại. Control đó dùng lại hold đã bị stop cắt và coi coin là độc lập. Các từ kết luận không đổi, vì net 2x đã âm.
- Phí và trượt giá trên perp là giả định, chưa so với fill thật.
- `result_B.json` không có từ kết luận; từ ở bảng trên được áp theo protocol sau khi đo.
- `measure.py` không khớp hash đóng băng. Bản đóng băng (`dce66c53`) crash vì sai tên cột funding trước khi in số lời lỗ nào, nên được sửa một dòng, hash hiện tại là `4f7020b3`. `FROZEN.sha256` có ghi chú việc này nhưng dòng hash vẫn là bản cũ, nên `sha256sum -c` báo FAILED.

## Nếu muốn đi tiếp

Mọi việc dưới đây là quyết định của operator. Muốn trả lời chắc hơn cần: nến perp và mark price, funding 2020–2022, danh sách coin theo quy tắc có high và low. Sau đó mới tính được có thiết kế nào đủ power hay không. Đưa short vào vận hành còn phải sửa simulator, planner, lifecycle, broker và runner, vì đường hiện tại long-only và adapter futures chưa dựng được. Chưa rõ tài khoản của operator có được dùng USD-M futures hay không.
