# Tổng hợp nghiên cứu: vì sao chưa strategy nào qua

Status: HISTORICAL — ảnh chụp ngày 30/10/2026, không phải quyết định mới. Market vẫn PAUSED.
Owner: research operator
Verified: 2026-10-30. Đối chiếu trực tiếp với nguồn: ngưỡng outer, H-VOLBREAK-01, H-PULLBACK-01, H-PULLBACK-TEMPORAL-01, H-LIQ-REVERSAL-01, RSI/ADX, BASE/PLUS, thinning, holdout, bằng chứng enhanced_ma. Các dòng khác trong bảng mục 3 lấy từ tracker, chưa đối chiếu từng packet.
Evidence: [tracker](../CAMPAIGN_STATUS.md), [research evidence](../RESEARCH_EVIDENCE.md), [holdout](../RESEARCH_HOLDOUT.md), packet local trong `data/`

Không có phép đo mới nào được chạy để viết tài liệu này. Dữ liệu thị trường chỉ được kiểm qua metadata (footer parquet), không đọc giá.

> **Đính chính 31/10/2026.** Phần dưới giữ nguyên như đã viết ngày 30/10. Các điểm sau thay thế hoặc bổ sung câu tương ứng. Đây là đối chiếu với code và artifact đã lưu, không chạy backtest mới, và không verdict nào đổi.
>
> - **Holdout (dòng Verified, mục 5, mục 7).** Câu "chưa từng được chấm điểm" sai. Cửa sổ đóng băng đã bị chấm điểm trên BTC 1h ít nhất ba lần: `rsi` 06/09, `funding_carry` 15/09, `ma_vol_target` 25/09, đều có `final_holdout.status` COMPLETED trên bar 27183–31503 (`data/backtests/wfo_real_20260906`, `data/backtests/wfo_funding_carry_only`, `data/backtests/wfo/research/vol_target__BTCUSDT_1h`). Cổng `final_holdout_completed` chỉ kiểm lần chạy đã hoàn tất, không kiểm lãi lỗ. Manifest đóng băng lần đầu ngày 11/08 và được sinh lại ngày 06/09. Chi tiết ở [sổ lộ holdout](../RESEARCH_HOLDOUT.md).
> - **Dữ liệu chưa xem (mục 6).** "Không còn dữ liệu chưa xem" chỉ có căn cứ cho BTC, ETH, BNB 1h. Mức lộ của các symbol và khung khác chưa được kiểm kê, nên không được suy ra là chưa xem. Cohort forward từ 02/11/2026 chưa xảy ra và dành riêng cho H-XSEC-01.
> - **Mục 8 không phải danh sách "chưa từng đo".**
>   - Khung ngày đã đo, dù mỏng: BTC 1d có 298 `report.json` và 3 WFO với 13, 1 và 12 lệnh OOS; walk-forward `ma_crossover` 1d trên 10 symbol ngày 24/08, không có gate; T8A-B BTC, ETH, BNB 2020–2022. Khung tuần mới chỉ có ở dạng rebalance.
>   - Funding đã đo: `funding_carry` có 150 report BTC 1h và một run 7 fold ngoài. Chưa xác minh PnL có gồm dòng tiền funding.
>   - Phép đo short có funding đầu tiên chạy ngày 31/10/2026: [kết quả](SHORT_SIDE_RESULTS_20261031.md). Short đã đo tới 30/10/2026, nhưng không trên đường nào qua cổng và không trên perp USDT. `scripts/run_cross_asset_backtest.py` dùng trọng số âm thật cho nhánh long-short của `stat_arbitrage_ls` và cross-sectional momentum, trên BTC/USDT và ba cặp chéo BTC, 31.783 bar 1h từ 2023-01-01 đến 2026-08-17 nên cắt qua holdout. Trường `trades` trong kết quả của script là số bar đổi trọng số, không phải số lệnh. Script chỉ tính commission 10 bps, không có slippage, borrow hay funding, và chưa thấy ghi nhận review. `scripts/run_tournament_shadow.py` chấm SELL là −1, nhưng điểm đó có look-ahead. Hai id `*_ls` trong registry là bản xấp xỉ một tài sản; `FullSystemSimulator`, đường đo của `nested_wfo`, coi SELL là thoát về flat, nên các cổng chưa chấm chân short nào. `BacktestEngine` có `long_only=False` và có test, nhưng không tính funding, và không script nào bật chế độ đó. Runtime là Spot long-only: `OrderPlanner` từ chối target âm. Các phép đo này không trả lời việc thêm short có tăng lợi nhuận hay không.
>   - Xếp hạng chéo: script trên đã xếp hạng 4 instrument đó ngày 17/09. D1 và H-XSEC-02 đo sau khi tài liệu này được viết.
> - **"Lỗ trước phí" (mục 1, 2).** Gross là số của simulator, nơi mọi stop khớp ở bar low. RSI/ADX cố định âm dù stop khớp ở đâu. MA 20/50 không có stop; closed gross âm nhẹ (−15 đến −20 USDT) nhưng còn một vị thế mở đang lãi, nên RESULTS của nó không kết luận lỗ trước phí. Với H-LIQ-REVERSAL-01, BASE/PLUS và thinning, dấu của gross chưa rõ. Nếu stop khớp ở trigger, số ô chuyển sang dương là 3/3, 9/12, 18/36; nếu khớp dưới trigger 100 bps là 0/3, 4/12, 9/36. H-LIQ-REVERSAL-01 1x đi từ −268 đến khoảng +37 USDT. Net vẫn âm ở mọi ô trong các trường hợp này.
> - **Đơn vị (mục 2).** Gross, chi phí và net trong bảng "Lãi gộp xấp xỉ bằng phí" tính bằng USDT, không phải %. Return % theo RESULTS: H-VOLBREAK-01 +0.575% ở 1x và −2.385% ở 2x, so với passive +34.4%; H-PULLBACK-01 +0.721%, so với passive +4.9%; H-PULLBACK-TEMPORAL-01 −0.479%, so với passive +31.7%. Các dòng trong bảng khác cửa sổ nên không được xếp hạng với nhau.
> - **Mục 2, "Quá ít lệnh".** Số 30 ở đây là hai ngưỡng khác nhau. Selection contract đòi candidate có ít nhất 30 lệnh trong từng cửa sổ validation. Cổng outer `min_trades_per_pair_strategy_ge_30` cộng lệnh qua mọi fold, và ngưỡng lấy từ spec nên có thể khác 30.
> - **Mục 2, chi phí.** Mô phỏng tính 30 bps khứ hồi, spread 0. Các cổng cost-clearing dùng sàn 0.32% × số lệnh, so với return trên vốn đã trừ phí và không scale theo exposure. Sàn này gồm 2 bps spread mà mô phỏng không tính.
> - **Phạm vi bảng mục 3.** 24 dòng không phải toàn bộ lịch sử. Bảng nhảy từ 11/08 sang 06/10, bỏ sót walk-forward 4h và 1d trên 10 symbol (24/08), WFO SOL 1h (05/09), `wfo_real_20260906`, `wfo_full` 529 ô (14/09), `wfo_funding_carry_only` (15/09), cross-asset (17/09), T8A (23/09) và vol_target BTC 1h (25/09). Bảng cũng thiếu ba mốc dễ bị đọc nhầm là đã có strategy qua: đợt 05/09 ghi `enhanced_ma` Winner/FINAL_PASS ([trang đó](../vi/MULTI_STRATEGY_WFO_2026_09_05.md)), danh sách 18/09 ghi APPROVED cho `enhanced_ma` và `stat_arbitrage_ls` ([trang đó](../STRATEGY_SIGNOFF_P2PHASE4.md)), và tournament 18/09 (replay 720 nến, Sharpe có look-ahead, tự promote khi chạy chế độ live). Cả ba đều không phải bằng chứng qua cổng, nên kết luận mục 1 không đổi.
> - **Mục 4, cổng outer.** Danh sách thiếu `cost_clearing_folds_above_chance_p_le_010` (binomial p ≤0.10), `cost_clearing_single_window_le_60pct`, `median_return_clears_cost_floor`, `zero_trade_fold_pct_le_50` và `parameter_neighbors_net_pnl_positive`. Hợp đồng native có 20 cổng, thêm 2 cổng holdout khi chạy holdout.
> - **Mục 4, cách tính.** Net return, Sharpe, PF, max drawdown và Calmar của cổng outer là trung vị qua các fold, không phải số toàn chuỗi. Fold dương là fold có Sharpe >0.
> - **Mục 5, "DSR tính khoảng 8000 lần thử".** 8000 là giá trị mặc định nhập tay của `--trials` trong `scripts/generate_live_strategy_evidence.py`. Cổng `dsr_ge_095` lấy số thử từ registry, chỉ trong phạm vi candidate của chính lần chạy. Các packet validation 24–27/10 ghi `raw_trial_count` 3, riêng một packet ghi 9.

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

> **Đính chính 31/10/2026.** Dòng TESTNET_ELIGIBLE thiếu một điều kiện: `REALITY_GAP` phải có nguồn testnet hoặc shadow, nên mô phỏng không qua được bậc này. Bậc này vì thế lặp vòng: cần bằng chứng testnet trước khi được lên testnet.

Cổng outer trong `nested_wfo` chặt hơn thang trên: Sharpe ≥0.80, PF ≥1.20, max drawdown ≤10%, Calmar ≥0.5, ít nhất 60% fold dương, cùng các cổng chịu phí 2x, trễ một bar và bỏ lệnh tốt nhất.

Hai sự thật tách biệt:

- Chưa strategy nào đạt bậc đầu tiên. `data/promotion_store/` rỗng.
- Ngoài test, không có code nào tạo `EvidenceArtifact` cho thang này hoặc gọi `promote` cho một strategy thật. Kể cả có strategy tốt, hiện chưa có đường đưa nó lên testnet.

Các bản ghi `paper_eligible` trong packet backtest và 5 dòng "approved" trong `data/promotion/approvals.sqlite3` không phải bằng chứng hiệu suất: chúng được ghi trực tiếp bởi script, và runner không đọc chúng.

Track vận hành (M3) mở khóa mua cho bộ kiểm thử vòng đời lệnh, không cho strategy, và không tạo bằng chứng promotion.

## 5. Gánh thử nghiệm và dữ liệu

**Đã thử bao nhiêu.** Registry ghi 218 spec và 421 đánh giá trong `experiments.sqlite3`, 208 spec và 613 đánh giá trong `experiments_parallel.sqlite3`. Ghi chú DSR tính khoảng 8000 lần thử. Con số thật chưa được đối soát: SQL không phủ mọi lần thử.

**Dữ liệu đã lộ.** File BTC 1h từ 24/03/2023 đến 06/02/2026 đã bị đọc và đo. Không còn dataset confirmation chưa đọc cho BTC 1h. ETH và BNB 1h cũng đã bị đọc.

**Holdout.** Cửa sổ 06/02–05/08/2026 đóng băng từ 06/09/2026 và chưa từng được chấm điểm. **(Đính chính 31/10/2026: câu này sai, cửa sổ đã bị chấm điểm ít nhất ba lần trên BTC 1h. Xem đầu trang.)** Không chứng minh được là chưa bị chạm: file BNB 4h và lịch báo cáo RSI từng đi vào vùng này, và chính [holdout policy](../RESEARCH_HOLDOUT.md) ghi metadata không chứng minh điều đó.

**Dữ liệu trong máy** (footer parquet, tháng 10/2026):

| Symbol | Khoảng thời gian |
| --- | --- |
| BTC, ETH, BNB | 1d và 1h_extended từ 01/01/2020 đến 21/09/2026 |
| ADA, DOGE, NEAR, SOL, TRX, XRP, ZEC | Từ 01/01/2023 đến khoảng 16–17/08/2026 |
| AVAX, DOT, LINK | Từ 01/01/2023 đến khoảng 05/08/2026 (1h của AVAX đến 17/08) |

Backfill Binance Vision chưa được tải: điều khoản phân biệt nghiên cứu cá nhân với sử dụng production, và chủ dự án chưa chọn phạm vi.

## 6. Vì sao không tinh chỉnh tham số

- Gánh thử nghiệm đã rất lớn. Thêm một vòng tìm tham số chỉ làm DSR và PBO khó qua hơn, và thứ qua được nhiều khả năng là khớp nhiễu.
- Không còn dữ liệu chưa xem để kiểm tra kết quả tinh chỉnh. (Đính chính 31/10/2026: chỉ có căn cứ cho BTC, ETH, BNB 1h, xem đầu trang.)
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

> Đính chính 31/10/2026: khung ngày, funding, short và xếp hạng chéo đã có phép đo, tuy mỏng hoặc chưa sạch. Xem đầu trang.

- **Xếp hạng chéo thật sự giữa nhiều coin.** Strategy `cross_sectional_momentum_lo/ls` trong repo là bản xấp xỉ một tài sản, theo docstring của nó. 12 lần chạy ngày 11/09 đều không xong (5 lần chết vì `database is locked`, 7 lần dừng sau bước bỏ fold) và không có spec nào trong registry.
- **Khung ngày và tuần** trong repo; tài liệu học thuật có kết quả ở horizon này.
- **Thông tin ngoài giá** (nhóm G5–G11 của [playbook](MULTI_STRATEGY_RESEARCH_PLAN.md)), funding, perpetual, short và ML; các hướng này đang ngoài phạm vi.

Đề xuất tiếp theo, đã đóng băng thiết kế nhưng chưa được duyệt để chạy: [H-XSEC-01](../proposals/H_XSEC_01_RELATIVE_STRENGTH.md).
