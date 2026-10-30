# Tổng hợp nghiên cứu: vì sao chưa strategy nào qua

Status: HISTORICAL — ảnh chụp ngày 30/10/2026, không phải quyết định mới. Market vẫn PAUSED.
Owner: research operator
Verified: 2026-10-30. Đối chiếu trực tiếp với nguồn: ngưỡng outer, H-VOLBREAK-01, H-PULLBACK-01, H-PULLBACK-TEMPORAL-01, H-LIQ-REVERSAL-01, RSI/ADX, BASE/PLUS, thinning, holdout, bằng chứng enhanced_ma. Các dòng khác trong bảng mục 3 lấy từ tracker, chưa đối chiếu từng packet.
Evidence: [tracker](../CAMPAIGN_STATUS.md), [research evidence](../RESEARCH_EVIDENCE.md), [holdout](../RESEARCH_HOLDOUT.md), packet local trong `data/`

Không có phép đo mới nào được chạy để viết tài liệu này. Dữ liệu thị trường chỉ được kiểm qua metadata (footer parquet), không đọc giá.

## 1. Kết luận

- Khoảng 24 đợt nghiên cứu trên ít nhất 15 họ quy tắc, từ tháng 8 đến 30/10/2026. Không strategy nào đạt RESEARCH_VALIDATED.
- Thất bại rơi vào ba kiểu: tín hiệu lỗ ngay trước phí, lãi gộp xấp xỉ bằng phí, hoặc quá ít lệnh để kết luận.
- Thông báo `entries_locked_no_promoted_runtime` không phải một lỗi cấu hình. Chưa strategy nào đủ điều kiện vào promotion, và phần code tạo bằng chứng promotion cũng chưa có.
- Tinh chỉnh tham số trên dữ liệu hiện có không phải hướng đi hợp lý, vì lý do ở mục 6.

## 2. Ba kiểu thất bại

### Tín hiệu lỗ trước phí

| Quy tắc | Số liệu | Nguồn |
| --- | --- | --- |
| H-LIQ-REVERSAL-01 | Gross −268.36 USDT qua 95 lệnh | `data/liq_reversal_validation_run_20261027_01/RESULTS.md` |
| RSI/ADX cố định | Gross −244.24 USDT qua 39 lệnh | `data/rsi_fixed_validation_run_20261024_01/RESULTS.md` |
| BASE/PLUS | Gross alpha âm ở 12/12 nhánh | `data/plus_context_matrix_20261029_01/MATRIX_RESULTS.json` |
| Thinning | Net trên notional tệ hơn đúng chi phí thực (30.0/59.9/69.8 bps), nên gross âm ở mọi nhóm đã báo cáo | `data/btc_thinning_execution_approved_20261030_02/` |

**enhanced_ma**, strategy mà runner testnet dùng, với đúng tham số live (10/60, ADX 40): 6 fold từ 17/02/2025 đến 11/08/2026. Sharpe trung vị BTC −2.50, SOL −0.56, AVAX −0.11, trong khi sàn mainnet là 0.50. BTC lỗ ở 5/6 fold. DSR 0.0 với 8000 lần thử. Nguồn: `data/live_strategy_evidence.rejected.json`.

### Lãi gộp xấp xỉ bằng phí

| Quy tắc | Lệnh | Gross 1x | Chi phí 1x | Net 1x | Khi phí 2x |
| --- | ---: | ---: | ---: | ---: | --- |
| H-VOLBREAK-01 | 53 | 351.84 | 295.23 | +56.61 | Net −228.68 |
| H-PULLBACK-01 | 25 | 214.01 | 141.99 | +72.03 | Thiếu mẫu (<30), Sharpe −0.633 |
| H-PULLBACK-TEMPORAL-01 | 40 | 178.68 | 226.41 | −47.73 | Sharpe −1.17 |

Suy luận: với chi phí mô hình khoảng 30 bps khứ hồi, các quy tắc này kiếm khoảng 25–45 bps gross mỗi lệnh. Biên an toàn gần như bằng không.

### Quá ít lệnh

Trên BTC 4h, các cửa sổ cho 0–22 lệnh, trong khi cổng đòi ít nhất 30. Tracker ước tính cần khoảng 18–30 tháng dữ liệu validation để đủ 30 lệnh ở mật độ đó. enhanced_ma chỉ có 3–8 lệnh mỗi fold 90 ngày.

### Mốc so sánh

Trên cùng các cửa sổ đo, phân bổ thụ động 25% lãi +31.7% (temporal), +34.4% (volbreak) và khoảng +32.9% (liquidity reversal). Các strategy ở quanh 0 hoặc âm. Đây là bối cảnh, không phải alpha đã khớp rủi ro; so sánh theo rủi ro chưa được tính.

## 3. Các đợt đã đo

Nguồn mặc định là [tracker](../CAMPAIGN_STATUS.md) và [research evidence](../RESEARCH_EVIDENCE.md).

| Ngày | Đã thử | Kết quả ghi nhận | Tiêu chí trượt |
| --- | --- | --- | --- |
| 06/08 | enhanced_ma, in-sample 2023→08/2026 | CANDIDATE, bị thay thế | Chỉ in-sample; BTC 4h 32 lệnh |
| 05–06/08 | MA+RSI walk-forward | FAIL | Sharpe OOS ≈0 ở nhiều fold |
| 11/08 | Pipeline live, BTC+SOL 1h, 6 fold 180 ngày | FAIL | Sharpe trung vị −0.44/−1.18/−2.06 |
| 06/10 | ma_crossover pilot, BTC 4h | FAIL 18 cổng | OOS −0.12%, 13 lệnh |
| 06/10 | ma_crossover lưới 3×3, BTC 4h | FAIL 16 cổng | 5 lệnh, PBO 0.486 (giới hạn 0.20) |
| 06–07/10 | ma_crossover 15/20, một fold | FAIL 14 cổng | 5 lệnh; trượt cổng phí 2x và bỏ lệnh tốt nhất |
| 07/10 | Lưới MA, BTC 4h, 6 fold | fold_000 ABSTAIN | 3–5 lệnh validation |
| 08/10 | Enhanced MA và MA đối chứng, BTC 1h | NOT_QUALIFIED, chưa xong | Fold đầu Sharpe −3.16…−1.02 |
| 08/10 | vol_target, BTC 4h nested WFO | FAIL | 26 lệnh, Sharpe trung vị −1.06, PF 0.50 |
| 15/10 | ma_adx và vol_target, BTC 4h | ABSTAIN | 0–6 lệnh validation |
| 16/10 | POOL5, năm strategy, BTC 4h | ABSTAIN 10/10 | 5–22 lệnh; 7/10 Sharpe ≤0 |
| 17/10 | Sàng lọc ETH/BNB 4h, 16/40 ô | PARTIAL | Không nhóm nào đủ 30 lệnh với Sharpe dương ở cả hai cửa sổ |
| 17–19/10 | BNB 4h MA20/80+ADX25 | INSUFFICIENT_EVIDENCE | 7 lệnh; lệnh tốt nhất lớn hơn tổng net |
| 19/10 | BNB 4h ADX25 native | FAILED/INCOMPLETE | 4–7 lệnh mỗi ô |
| 23/10 | BNB 4h DI/ADX state-edge | INCOMPLETE/HOLD | Fold F2, F3 gross không đủ trả phí hoặc âm |
| 24/10 | MA 20/50, BTC 1h | RESEARCH_ABSTAIN | 64 lệnh, Sharpe −0.16/−1.54/−2.00 |
| 24/10 | RSI/ADX cố định, BTC 1h | RESEARCH_ABSTAIN | 39 lệnh, Sharpe −2.51/−3.64/−4.01 |
| 26/10 | H-PULLBACK-01, BTC 1h | INCOMPLETE_SAMPLE | 25 lệnh; Sharpe 0.695/−0.633/−1.06 |
| 26/10 | H-PULLBACK-TEMPORAL-01 | RESEARCH_ABSTAIN | 40 lệnh; Sharpe −0.20/−1.17/−1.48 |
| 26/10 | H-VOLBREAK-01 | DIAGNOSTIC_POSITIVE_NOT_QUALIFIED | 53 lệnh; 2x và slip dưới sàn Sharpe |
| 27/10 | H-LIQ-REVERSAL-01 | RESEARCH_ABSTAIN | 95 lệnh; Sharpe −1.93/−3.16/−3.59 |
| 27/10 | H-OWN24-CONTEXT-01, ETH/BNB 1h | Không IMPROVES | Mọi nhánh âm |
| 29/10 | BASE–PLUS, ETH/BNB 1h | CLOSED_NO_SUPPORTED_INCREMENT | 0/6 IMPROVES; return −32.7% đến −70.2% |
| 30/10 | Thinning, PLUS với 5 đối chứng ngẫu nhiên | DESCRIPTIVE_ONLY | ETH thắng 2, 1, 0 của 5 đối chứng; mọi nhánh lỗ |

## 4. Vì sao lệnh mua vẫn khóa

Để runner testnet nhận một lệnh BUY, strategy phải đi qua thang promotion trong `trading_agent.research.promotion` và bản ghi trong `PromotionStateStore`:

| Bậc | Điều kiện | Loại |
| --- | --- | --- |
| RESEARCH_VALIDATED | OOS net >0, ít nhất 30 lệnh, DSR ≥0.95, PBO ≤0.20, chịu được stress phí, ổn định tham số ≥0.70 | Hiệu suất |
| PAPER_ELIGIBLE | Artifact đã xác minh, 0 lỗi toàn vẹn | Toàn vẹn |
| TESTNET_ELIGIBLE | Mô phỏng thực thi ≥100 kịch bản và reality gap ≤0.50, cả hai 0 vi phạm | Thực nghiệm |
| Runtime | Một artifact 1h cho mỗi symbol của runner, khớp tham số và `code_sha` | Ràng buộc |

Cổng outer trong `nested_wfo` chặt hơn thang trên: Sharpe ≥0.80, PF ≥1.20, max drawdown ≤10%, Calmar ≥0.5, ít nhất 60% fold dương, cùng các cổng chịu phí 2x, trễ một bar và bỏ lệnh tốt nhất.

Hai sự thật tách biệt:

- Chưa strategy nào đạt bậc đầu tiên. `data/promotion_store/` rỗng.
- Ngoài test, không có code nào tạo `EvidenceArtifact` cho thang này hoặc gọi `promote` cho một strategy thật. Kể cả có strategy tốt, hiện chưa có đường đưa nó lên testnet.

Các bản ghi `paper_eligible` trong packet backtest và 5 dòng "approved" trong `data/promotion/approvals.sqlite3` không phải bằng chứng hiệu suất: chúng được ghi trực tiếp bởi script, và runner không đọc chúng.

Track vận hành (M3) mở khóa mua cho bộ kiểm thử vòng đời lệnh, không cho strategy, và không tạo bằng chứng promotion.

## 5. Gánh thử nghiệm và dữ liệu

**Đã thử bao nhiêu.** Registry ghi 218 spec và 421 đánh giá trong `experiments.sqlite3`, 208 spec và 613 đánh giá trong `experiments_parallel.sqlite3`. Ghi chú DSR tính khoảng 8000 lần thử. Con số thật chưa được đối soát: SQL không phủ mọi lần thử.

**Dữ liệu đã lộ.** File BTC 1h từ 24/03/2023 đến 06/02/2026 đã bị đọc và đo. Không còn dataset confirmation chưa đọc cho BTC 1h. ETH và BNB 1h cũng đã bị đọc.

**Holdout.** Cửa sổ 06/02–05/08/2026 đóng băng từ 06/09/2026 và chưa từng được chấm điểm. Không chứng minh được là chưa bị chạm: file BNB 4h và lịch báo cáo RSI từng đi vào vùng này, và chính [holdout policy](../RESEARCH_HOLDOUT.md) ghi metadata không chứng minh điều đó.

**Dữ liệu trong máy** (footer parquet, tháng 10/2026):

| Symbol | Khoảng thời gian |
| --- | --- |
| BTC, ETH, BNB | 1d và 1h_extended từ 01/01/2020 đến 21/09/2026 |
| ADA, DOGE, NEAR, SOL, TRX, XRP, ZEC | Từ 01/01/2023 đến khoảng 16–17/08/2026 |
| AVAX, DOT, LINK | Từ 01/01/2023 đến khoảng 05/08/2026 (1h của AVAX đến 17/08) |

Backfill Binance Vision chưa được tải: điều khoản phân biệt nghiên cứu cá nhân với sử dụng production, và chủ dự án chưa chọn phạm vi.

## 6. Vì sao không tinh chỉnh tham số

- Gánh thử nghiệm đã rất lớn. Thêm một vòng tìm tham số chỉ làm DSR và PBO khó qua hơn, và thứ qua được nhiều khả năng là khớp nhiễu.
- Không còn dữ liệu chưa xem để kiểm tra kết quả tinh chỉnh.
- Nguyên nhân mang tính cấu trúc. Tham số không đổi được quan hệ giữa lãi gộp và phí, cũng không tạo thêm lệnh trên cùng khung thời gian.
- [RESEARCH_EVIDENCE](../RESEARCH_EVIDENCE.md) đã ghi quyết định không retune các họ đã đóng để giữ search budget.

## 7. Chưa biết

- Một quy tắc gross dương có giữ được trên dữ liệu chưa xem không.
- Chi phí thực của tài khoản so với mô hình 30 bps. Testnet thu phí 0 nên không đo được phần phí ở đó.
- Hiệu suất so với phân bổ thụ động sau khi khớp rủi ro.
- Số lần thử hiệu dụng cho DSR.
- Holdout có thật sự chưa bị thấy không.
- Mức độ lộ của dữ liệu 2020–2022 của BTC, ETH, BNB.

## 8. Các hướng chưa từng được đo

- **Xếp hạng chéo thật sự giữa nhiều coin.** Strategy `cross_sectional_momentum_lo/ls` trong repo là bản xấp xỉ một tài sản, theo docstring của nó. 12 lần chạy ngày 11/09 đều không xong (5 lần chết vì `database is locked`, 7 lần dừng sau bước bỏ fold) và không có spec nào trong registry.
- **Khung ngày và tuần** trong repo; tài liệu học thuật có kết quả ở horizon này.
- **Thông tin ngoài giá** (nhóm G5–G11 của [playbook](MULTI_STRATEGY_RESEARCH_PLAN.md)), funding, perpetual, short và ML; các hướng này đang ngoài phạm vi.

Đề xuất tiếp theo, chưa được duyệt và chưa chạy: [H-XSEC-01](../proposals/H_XSEC_01_RELATIVE_STRENGTH.md).
