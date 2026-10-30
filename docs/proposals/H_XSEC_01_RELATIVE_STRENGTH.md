# H-XSEC-01: xếp hạng sức mạnh tương đối giữa nhiều coin

Status: TARGET — DESIGN_FROZEN ngày 30/10/2026, `run_authority: NONE`. Market vẫn PAUSED.
Owner: research operator
Verified: 2026-10-30. Chưa đọc giá, chưa chạy phép đo nào. Dữ liệu chỉ kiểm qua footer parquet.
Evidence: [tổng hợp nghiên cứu](../operations/RESEARCH_SYNTHESIS_20261030.md), [điều kiện rời PAUSED](../RESEARCH_EVIDENCE.md#điều-kiện-rời-paused--một-quyết-định-không-một-batch-search)

Operator đã trả lời các câu hỏi (mục 7) và đóng băng thiết kế ngày 30/10/2026. Đóng băng chỉ khóa thiết kế, không phải quyền chạy: đề xuất vẫn **chưa đủ điều kiện chạy** vì bốn trong sáu mục chưa có câu trả lời quyết định được (mục 5). Mục 3 và quy tắc forward ở mục 6 không được sửa; muốn đổi thì tạo đề xuất mới. Tài liệu này không cấp quyền market, paper, testnet hay live cho bất kỳ ai.

## 1. Vì sao chọn hướng này

Các thất bại trước có hai nguyên nhân cấu trúc: lãi gộp xấp xỉ bằng phí, và quá ít lệnh trên một tài sản.

Xếp hạng chéo giữa nhiều coin nhắm vào cả hai. Mỗi tuần có một quyết định trên cả giỏ, nên số quan sát không phụ thuộc vào việc một tín hiệu có kích hoạt hay không. Rebalance theo tuần cũng làm giảm số lần trả phí so với khung 1h.

Đây là thông tin khác với những gì đã đo: quan hệ giữa các coin (nhóm G3 của [playbook](../operations/MULTI_STRATEGY_RESEARCH_PLAN.md)), không phải xu hướng của từng coin. Repo chưa từng đo nó. Strategy `cross_sectional_momentum_lo/ls` hiện có là bản xấp xỉ một tài sản, không được dùng lại tên hay kết quả của nó.

## 2. Một câu hỏi

Trên một giỏ coin cố định, giữ 4 coin mạnh nhất trong 21 ngày qua có thắng chính giỏ đó sau phí không?

Đây là câu hỏi về xếp hạng, không phải về timing thị trường. Vì vậy đối chứng chính là giỏ cùng mức exposure, không phải tiền mặt.

## 3. Candidate cố định

Một candidate duy nhất. Không có lưới tham số, và không đổi gì sau khi thấy kết quả.

| Thành phần | Giá trị | Nguồn gốc |
| --- | --- | --- |
| Universe | ADA, AVAX, BNB, BTC, DOGE, DOT, ETH, LINK, NEAR, SOL, TRX, XRP, ZEC (spot USDT) | Dữ liệu sẵn có. Đây là danh sách sống sót, xem mục 4 |
| Tín hiệu | Return 21 ngày từ nến ngày đã đóng | Hệ số momentum ba tuần của Liu, Tsyvinski & Wu |
| Rebalance | Mỗi tuần, thứ Hai 00:00 UTC | Thiết kế tuần của cùng bài báo |
| Nắm giữ | Top 4 của 13 (~30%), chia đều | "Top 30%" trong docstring strategy của repo |
| Exposure | Tổng 25% vốn, luôn đầu tư | Khớp mốc thụ động 25% của repo; giữ exposure cố định để câu hỏi chỉ còn là xếp hạng |
| Chiều | Long-only spot, không đòn bẩy | Profile hiện tại |
| Chi phí | 1x, 2x, slip stress như mọi packet | Không hạ phí. VIP 0 giao ngay là 0.1% cả maker lẫn taker (nguồn thứ cấp, xem mục 8) |
| Thời điểm khớp | Quy ước native k+2 | Preflight phải xác nhận cho rebalance nhiều tài sản |

Ba tuần là mức tốt nhất trong bốn mức của bài báo gốc, nên bản thân nó mang thiên lệch chọn lọc. Nó được chọn trên dữ liệu của bài báo, không phải dữ liệu của repo, nhưng không phải là tham số trung lập.

**Đối chứng:**

- Giỏ đều 13 coin, rebalance tuần, cùng exposure, trả cùng mức phí.
- 20 lựa chọn ngẫu nhiên 4 của 13 mỗi tuần, seed cố định trước khi chạy.

**Quy tắc quyết định** (đã đóng băng). Chỉ số chính là net return của candidate trừ net return của giỏ, trên cửa sổ D1:

| Kết quả | Điều kiện |
| --- | --- |
| REJECT, kết thúc, không retune | Excess ≤0 ở phí 2x, hoặc thấp hơn trung vị đối chứng ngẫu nhiên ở phí 1x |
| CONTINUE_TO_CONFIRMATION, chưa qualified | Excess >0 ở cả 1x, 2x, slip, và cao hơn ít nhất 18/20 đối chứng ở phí 2x |
| INCONCLUSIVE, đóng, không tìm tiếp | Mọi trường hợp còn lại |

**Đặc tả đóng băng.** Các chi tiết này được chốt trước khi đọc bất kỳ giá nào, để không còn lựa chọn nào sau khi thấy dữ liệu:

| Mục | Giá trị |
| --- | --- |
| Tín hiệu | `close(D) / close(D−21) − 1` từ `1d.parquet`; D là nến ngày Chủ nhật, đóng lúc 00:00 UTC thứ Hai |
| Hòa hạng | Theo tên symbol, A đến Z |
| Khớp lệnh | Giá open của nến 1h mở lúc 01:00 UTC thứ Hai, tức k+2. Thiếu nến đó thì dùng nến 1h kế tiếp có dữ liệu của chính coin đó |
| Chi phí mỗi chiều | 1x: phí 10 + trượt 5 bps. 2x: 20 + 10 bps. Slip: 10 + 25 bps |
| Vốn và trọng số | Vốn đầu 10 000 USDT. Candidate và đối chứng ngẫu nhiên 6.25% mỗi coin; giỏ 25/13 % mỗi coin, tính trên equity đã mark lúc khớp |
| Giao dịch | Chỉ trade phần chênh so với mục tiêu; phần chênh dưới 5 USDT bỏ qua |
| Đối chứng ngẫu nhiên | `random.Random(seed).sample(sorted(universe), 4)` mỗi tuần theo thứ tự thời gian, seed 1 đến 20, dùng chung cho ba mức phí |
| D1 | Rebalance đầu 23/01/2023, cuối 02/02/2026, 159 lần. Mark cuối ở close nến 1h kết thúc 06/02/2026 12:00 UTC. Cắt dữ liệu trước khi đọc, 0 dòng holdout |
| Forward | Rebalance đầu 02/11/2026, cuối 20/12/2027, 60 lần. Mark cuối ở open nến 1h mở lúc 01:00 UTC ngày 27/12/2027 |
| So sánh | Equity cuối kỳ ở cùng mức phí. Excess = (equity candidate − equity giỏ) / vốn đầu |
| Thiếu dữ liệu | Thiếu close ngày cần để xếp hạng thì run là INVALID, không nội suy |
| Coin ngừng giao dịch | Đóng ở giá 1h cuối cùng theo phí của kịch bản, rời universe từ rebalance kế tiếp ở mọi nhánh. Còn dưới 4 coin thì INVALID |
| Không nhìn giữa chừng | Cohort forward chỉ đọc một lần sau 27/12/2027, và chỉ khi D1 ra CONTINUE_TO_CONFIRMATION. Không dừng sớm, không kéo dài |

## 4. Rủi ro đã biết

**Thiên lệch sống sót.** 13 coin là những coin còn tồn tại khi dữ liệu được tải. Ammann, Burdorf, Liebi & Stöckl ước tính thiên lệch 0.93%/năm với danh mục theo vốn hóa và 62.19%/năm với danh mục chia đều. Chỉ số tương đối so với giỏ cùng universe giảm được một phần, không loại hết. Chưa biết có lấy được danh sách universe theo từng thời điểm không.

**Bằng chứng trái chiều.** Trong thiết kế của Fieberg và cộng sự, alpha momentum phần lớn đến từ chân short, mà ở đây không có chân short. Han, Kang & Ryu thấy nhiều danh mục momentum mất lợi nhuận khi tính phí và biến động trong ngày, và cross-sectional có cơ sở yếu hơn time-series. Ở chiều ủng hộ, nghiên cứu [Momentum and liquidity](https://arxiv.org/abs/1904.00890) thấy hiệu ứng trong nhóm coin thanh khoản nhất, và bản long-only thắng danh mục theo vốn hóa sau phí.

**Cổng drawdown.** Cổng outer đòi max drawdown ≤10%. Nếu các coin giảm 70% thì exposure 25% tương ứng khoảng 17% vốn, nên cổng này có thể trượt dù tín hiệu có giá trị. Không hạ cổng để mở đường.

**Sức mạnh thống kê.** Chưa đo được khi chưa đọc giá. Minh họa với độ lệch chuẩn excess tuần giả định 4% (không phải ước lượng):

| Số tuần | Hiệu ứng tối thiểu để t≈2 |
| --- | --- |
| 158 (D1) | Khoảng 0.64%/tuần, tức 33%/năm |
| 60 (forward) | Khoảng 1.03%/tuần, tức 54%/năm |

Nghĩa là cả D1 lẫn cohort 60 tuần chỉ phát hiện được hiệu ứng lớn. Kết quả có thể bác bỏ hoặc ủng hộ, không tự chứng minh được. Các tuần cũng không độc lập, nên con số thật còn tệ hơn.

**Chi phí.** Turnover chưa biết. Với τ là phần danh mục thay đổi mỗi tuần, chi phí trên phần đã đầu tư là τ × 30 bps mỗi tuần ở phí 1x. Ví dụ τ = 50% thì khoảng 7.8%/năm ở 1x và 15.6%/năm ở 2x. Slippage 5 bps là giả định của mô hình, chưa được đo cho các altcoin này.

**Phương án thay thế.** Bài của Han, Kang & Ryu gợi ý time-series momentum ở khung ngày đáng cân nhắc hơn. Đề xuất này không chọn nó vì trên một tài sản nó không giải quyết được vấn đề ít lệnh, và các họ xu hướng trên một tài sản đã có gánh thử nghiệm lớn. Reviewer có thể bác lựa chọn này.

**Chồng lấn với cảnh báo cũ.** Playbook ghi "không tự thêm coin khi thất bại". Đề xuất này chỉ có giá trị nếu là một cơ chế mới, không phải cách cứu kết quả ETH/BNB. Reviewer phải đánh giá chính điểm này.

## 5. Sáu điều kiện rời PAUSED

| Mục | Trả lời | Trạng thái |
| --- | --- | --- |
| 1. Nguồn payoff | Phản ứng chậm và dòng tiền đuổi theo coin đang thắng. Cơ chế cạnh tranh: chỉ là beta hoặc thiên lệch sống sót. Bị bác nếu không thắng giỏ cùng exposure. Bằng chứng học thuật trái chiều, đặc biệt ở chân long | CHƯA ĐỦ |
| 2. Khớp sản phẩm | Long-only spot khớp. Operator chấp nhận đổi profile nghiên cứu sang giỏ 13 coin rebalance tuần (lựa chọn C) | ĐÃ QUYẾT |
| 3. Thông tin đủ điều kiện | Không có feature mới, chỉ giá. Cohort forward 60 tuần đã đóng băng. Universe vẫn là danh sách sống sót, và 4 file đầu vào khác manifest (mục 7) | CHƯA ĐỦ |
| 4. Khả năng trả phí | Turnover và slippage altcoin còn UNKNOWN | CHƯA ĐỦ |
| 5. Thiết kế có thể bác bỏ | Một candidate, quy tắc dừng đặt trước, không mở rộng hậu nghiệm. Gánh thử nghiệm phải chốt với reviewer trước mọi claim | ĐÃ ĐÓNG BĂNG |
| 6. Quyền và review | Reviewer là operator, khác tác giả. Chưa có approval | CHƯA ĐỦ |

## 6. Cửa sổ dữ liệu

| Cửa sổ | Phạm vi | Trạng thái | Vai trò đề xuất |
| --- | --- | --- | --- |
| D1 | 01/2023 đến 06/02/2026, 13 coin, khoảng 158 tuần sau warmup | Giá đã bị đọc cho quy tắc khác; quy tắc này chưa đo | Sàng lọc để bác bỏ, không bao giờ là confirmation |
| Holdout | 06/02–05/08/2026, 26 tuần | Đóng băng, không chứng minh được là chưa chạm | Giữ niêm phong, không dùng (operator quyết định) |
| Forward | 60 tuần, 02/11/2026 đến 27/12/2027 | Chưa xảy ra | Cohort confirmation duy nhất thật sự chưa thấy; chỉ đọc một lần khi đủ 60 tuần |
| 2020–2022 | Chỉ BTC, ETH, BNB | Mức độ lộ chưa biết | Không dùng được, 3 coin không đủ để xếp hạng |

Các tuần từ 17/08/2026 đến ngày đóng băng không tính vào cohort, vì đề xuất được viết sau khi chúng đã xảy ra. Nến lịch sử không thay đổi, nên có thể tải cả cohort một lần khi đủ 60 tuần; không cần thêm scheduler.

**Quy tắc cho cohort forward** (đã đóng băng), chỉ áp dụng nếu D1 ra CONTINUE_TO_CONFIRMATION:

| Kết quả | Điều kiện |
| --- | --- |
| FORWARD_SUPPORTED, vẫn chưa qualified | Excess >0 ở cả 1x và 2x, và cao hơn ít nhất 18/20 đối chứng ở phí 2x |
| NOT_CONFIRMED, kết thúc, không kéo dài cohort | Mọi trường hợp còn lại |

FORWARD_SUPPORTED chỉ mở đường cho một đề xuất qualification riêng, không phải promotion.

## 7. Quyết định của operator, 30/10/2026

| Câu hỏi | Quyết định |
| --- | --- |
| Đổi profile sang giỏ 13 coin rebalance tuần | **Có.** Áp dụng cho nghiên cứu; runner, M1 và M3 không đổi, mainnet vẫn NO-GO |
| Holdout | **Giữ niêm phong** |
| Cohort forward | **60 tuần** (đổi từ 30 trước khi đóng băng; tác giả khuyến nghị tối thiểu 52; hệ quả ở mục 4) |
| Reviewer | **Operator.** Một người kiêm hai vai, khác tác giả. Ghi rõ đây không phải review của bên thứ ba |
| Đóng băng thiết kế | **Có**, ngày 30/10/2026, trước khi đọc bất kỳ giá nào. Bản ghi nằm ở packet local `data/xsec_01_freeze_20261030_01/` |

**Còn mở:**

1. **Giấy phép dữ liệu.** Chưa có câu trả lời, và đây là quyết định pháp lý của chủ dự án. Đề xuất hiện không dùng Binance Vision: các script tải trong repo đi qua `fetch_ohlcv` (REST), không thấy code tải archive `data.binance.vision`. Vì vậy điều khoản Vision không chặn bước tiếp theo. Điều khoản API chưa được kiểm, và câu hỏi phải được trả lời trước khi đi tới production.
2. **Gánh thử nghiệm.** Reviewer chốt trước mọi claim về DSR. Quy tắc D1 và forward không dùng DSR, nên không chặn hai bước đó. Tác giả đề xuất giữ con số lớn nhất đã ghi (khoảng 8000), không đặt lại về 1 cho họ mới.
3. **Bốn file đầu vào khác manifest.** `1h` của BTC và `1d` của BTC, ETH, BNB có hash khác `data/research_manifest.json`, vì được ghi lại sau ngày đóng băng manifest (14/09 và 22/09/2026). Preflight phải chứng minh các dòng trước mốc cắt không đổi, hoặc lấy lại nguồn đã đóng băng; không chứng minh được thì không chạy D1 trên các file đó.

## 8. Thứ tự làm nếu được duyệt

Mỗi bước có cổng riêng, không tự chuyển bước.

1. Hoàn thiện đề xuất theo [quy trình một lần chạy](../operations/RESEARCH_RUN_PROTOCOL.md): manifest, input, source, budget.
2. Xây phần ở mục 9 và kiểm trên dữ liệu tổng hợp, không dùng dữ liệu thị trường.
3. Reviewer duyệt adapter và parity: đọc source, assertion và raw receipt, kiểm lại arithmetic. Chạy lại script của tác giả không được tính là review.
4. Operator duyệt đúng packet.
5. Chạy một lần trên D1 theo quy tắc ở mục 3.

Phí VIP 0 ghi ở mục 3 lấy từ nguồn thứ cấp; trang phí chính thức của Binance chưa được xác minh, và mức phí thật của tài khoản phải được kiểm trước bước 1.

## 9. Việc kỹ thuật nếu đi tiếp

| Việc | Hiện trạng |
| --- | --- |
| Backtest rebalance nhiều tài sản | Không tìm thấy trong `trading_agent.backtest` |
| Strategy xếp hạng chéo thật | Chưa có; cần tên mới |
| Bằng chứng promotion | Chưa có code tạo `EvidenceArtifact` ngoài test |
| Artifact và resolver | `RuntimeStrategyResolver` đòi artifact 1h theo từng symbol; không hợp với danh mục tuần |
| Cổng outer | Cổng ≥30 lệnh tính theo cặp strategy; cần reviewer quyết định cách áp dụng cho danh mục, không tự nới |
| Runner | Chu kỳ 2 giờ theo tín hiệu từng symbol; chưa có rebalance danh mục |

## Nguồn

- Liu, Tsyvinski & Wu, [Common Risk Factors in Cryptocurrency](https://www.nber.org/papers/w25882), Journal of Finance 2022. Kết quả là gross, trên universe rộng, và bản thân ba tuần là mức được chọn.
- Fieberg, Liedtke & Zaremba, [Cryptocurrency Anomalies and Economic Constraints](https://doi.org/10.1016/j.irfa.2024.103218), 2024.
- Han, Kang & Ryu, [Time-Series and Cross-Sectional Momentum in the Cryptocurrency Market: A Comprehensive Analysis under Realistic Assumptions](https://acfr.aut.ac.nz/__data/assets/pdf_file/0009/918729/Time_Series_and_Cross_Sectional_Momentum_in_the_Cryptocurrency_Market_with_IA.pdf). Chỉ đọc được tóm tắt.
- [Momentum and liquidity in cryptocurrencies](https://arxiv.org/abs/1904.00890).
- Ammann, Burdorf, Liebi & Stöckl, [Survivorship and Delisting Bias in Cryptocurrency Markets](https://alexandria.unisg.ch/handle/20.500.14171/108037).
- Phí Binance, nguồn thứ cấp: [Binance.US support](https://support.binance.us/hc/en-us/articles/360047428113-Binance-US-Launches-Trading-Fee-Discount-and-VIP-Program-With-BNB), [CryptoPotato](https://cryptopotato.com/binance-fees/).

Các bài báo được đọc qua tóm tắt và kết quả tìm kiếm, chưa đọc toàn văn. Không dùng con số của bài báo làm prior cho quy tắc, phí hay mẫu của repo.
