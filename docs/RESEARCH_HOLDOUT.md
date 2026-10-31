# Final Holdout Policy (P2)

> Trạng thái: **FROZEN** — `data/research_manifest.json`. Manifest hiện tại không đổi từ 06/09/2026, nhưng cửa sổ đã bị chấm điểm; xem Sổ lộ holdout bên dưới.
> Metadata verified 2026-10-19; owner: research operator.
> Freeze metadata: `2026-09-06T11:33:21.967798+07:00`.

## Holdout window

| | |
| --- | --- |
| **Window** | 2026-02-06 16:00 UTC → 2026-08-05 16:00 UTC |
| **Timeframe** | 1h |
| **Datasets** | 80 dataset đã fingerprint SHA-256 (đính chính 31/10/2026; 71 là số của bản manifest 11/08) |
| **Manifest** | `data/research_manifest.json` (integrity SHA-256 tự tham chiếu) |
| **Generator** | `scripts/generate_holdout_manifest.py` |

Window được căn theo **end chung sớm nhất** trên toàn bộ symbol của timeframe,
đảm bảo không symbol nào rò rỉ dữ liệu tương lai vào holdout.

## Quy tắc bất biến

1. **Không dùng holdout cho bất kỳ quyết định nghiên cứu nào**: chọn tham số,
   feature engineering, training, validation, xếp hạng strategy, ensemble weight,
   hoặc thay đổi config đưa lên live.
2. **Chỉ score holdout đúng một lần** sau freeze date, để tạo bằng chứng release-gate.
3. **Không sửa** `data/research_manifest.json`. Muốn mở rộng holdout → tạo manifest
   mới với freeze date mới; manifest cũ vẫn là bản ghi cho window của nó.
4. Tool nào vi phạm sẽ fail-closed qua `guard_training_window()`:
   ```python
   from trading_agent.alpha_research.holdout import guard_training_window

   guard_training_window(start=..., end=...)  # raises HoldoutError nếu overlap
   ```

   Đính chính 31/10/2026: guard này không tự áp dụng cho mọi tool. Trong code nghiên cứu chỉ `nested_wfo` gọi nó: bỏ các fold chạm holdout rồi gọi guard cho từng fold còn lại. Chế độ validation-only chỉ lọc theo cửa sổ inner validation và không gọi guard, và khi không đọc được manifest thì holdout bị tắt. Ngoài ra chỉ có script evidence AC03 và test. Từ 14/10/2026 (`0951da33`), calibration từ chối input có timestamp cuối chạm holdout, chứ không tự cắt. Các đường tournament, shadow, paper và L2 không kiểm holdout, và `load_research_ohlcv` không tự cắt ở holdout, nên `--tail-bars` có thể đi vào vùng này, và đã từng đi vào.

## Xác minh / mở rộng

Manifest SHA-256:
`c1802ba532f7a7a17e7794fb2fb001f36c08f404b3a530be4026b870a65b2247`.
Inventory metadata — local evidence `data/bnb_qualification_readiness_20261019_01/inventory.json`
đã kiểm integrity mà không đọc giá holdout. Timestamp naive trong manifest được
helper chuẩn hóa UTC. Timeframe1h trong metadata không cấp quyền dùng holdout4h.

Không chạy generator với `--write` để ghi đè manifest đã freeze. Đề xuất mở rộng
phải được duyệt riêng, dùng artifact mới và giữ bản cũ. Dữ liệu nằm trước boundary
không tự động là unseen/OOS; cần exposure ledger. Metadata inventory không chứng
minh holdout chưa từng bị truy cập trong toàn bộ lịch sử dự án.

## Sổ lộ holdout

Ghi ngày 31/10/2026, gồm những gì đã tìm thấy tới ngày này. Chưa chứng minh là đầy đủ: registry SQLite và một số nhóm kết quả chưa được quét. Cửa sổ này không còn là dữ liệu chưa thấy và không dùng được làm confirmation độc lập. Nó vẫn giữ nguyên, không dùng cho quyết định mới.

Lịch sử manifest: freeze đầu ngày 11/08/2026, window 15:00 UTC, 71 dataset. Ngày 06/09/2026 file được sinh lại với `freeze_date` mới, window 16:00 UTC và 80 dataset, trái quy tắc 3. Commit `ea01e060` không ghi lý do. "Sau freeze" trong bảng tính từ 06/09.

| Loại lộ | Nội dung | Nguồn local |
| --- | --- | --- |
| Đã chấm điểm sau freeze | Ít nhất 3 lần trên BTC 1h, bar 27183–31503: `rsi` 06/09, `funding_carry` 15/09, `ma_vol_target` 25/09. `final_holdout.status` COMPLETED, `passes_hard_gates` false | `data/backtests/wfo_real_20260906`, `data/backtests/wfo_funding_carry_only`, `data/backtests/wfo/research/vol_target__BTCUSDT_1h` |
| Mở nhưng thất bại | 24/09: `ma_vol_target` BTC 1h và ETH 1h, status FAILED, không có metrics | `data/backtests/wfo/research/vol_target__BTCUSDT__1h`, `vol_target__ETHUSDT__1h` |
| Mở, chưa rõ có điểm không | 14/09: `trend_pullback` BTC 1h và `funding_carry` BTC 4h (bar 6796–7876). Không còn report; lần chạy lại dừng ở `Holdout already opened` | `data/backtests/wfo_full` |
| Report giao cửa sổ | 802 `report.json` có cửa sổ mô phỏng giao vùng này, đều có ngày file sau freeze đầu 11/08; 568 có ngày file từ 06/09/2026. Lớn nhất: `tournament` 201 (45 từ 06/09), `validation_l3` 180, `validation_l4_locked` 180. Ngày file chỉ là proxy của ngày chạy | `data/backtests/`, `data/enemy_*`, `data/calibration_20261012_*` |
| Calibration lệch cửa sổ | 12/10: cấu hình nhắm 2023–2024 nhưng 21 report mô phỏng qua ranh giới, tới 17/08/2026, phần lớn từ cell thất bại. Một report có measurement window [5984,7064] | `data/calibration_20261012_013330_utc`, `data/calibration_20261012_031500_utc` |
| Backtest ngoài report | Cross-asset 17/09: 31.783 bar 1h từ 2023-01-01 đến 2026-08-17, cắt qua cửa sổ | `data/backtests/cross_asset/` |
| Decode ngoài ý muốn | 19/10: dispatcher giải mã file BNB 4h 7946 dòng tới 17/08/2026. 30/10: loader của D1 ở commit `5b656422` giải mã cột timestamp của 26 input và close của mười file `1d` trước khi cắt | [tracker](CAMPAIGN_STATUS.md) |
| Sau freeze đầu, trước lần sinh lại | 11/08: bằng chứng enhanced_ma có 3 fold giao vùng này. 17/08: `final_holdout` của pipeline nghiên cứu trên 10 symbol, bản 1d chạy từ 2026-01-03, bản 4h chạy 2.000 bar từ 2025-03-30, tới khoảng 26/02/2026. 24/08 và 31/08: `portfolio_holdout`, 3 tháng cuối của từng file | `data/live_strategy_evidence.rejected.json`, `data/research_runs/latest/final_holdout/`, `data/wfo_results/` |
| Replay ngoài report | 18/09: tournament shadow và paper replay 720 bar 1h, 18/07 đến 17/08/2026, trên BTC, ETH, SOL. Kết quả ghi 15 dòng `TOURNAMENT_PROMOTION` và 5 dòng approval; runner không đọc các dòng này | `data/tournament_shadow/`, `data/tournament_paper/`, `data/promotion/` |
| Xếp hạng theo mật độ lệnh | 04/10: `scripts/select_l2_candidate.py` đếm lệnh trên BTC 1h 58.897 dòng tới 21/09/2026, không cắt ở holdout. Không đo lãi lỗ, nhưng đầu ra là thứ hạng strategy | `data/l2_selection.json` |
| Script ngoài report | 28/09: `scripts/_test_enhanced_ma_windows.py` chạy `enhanced_ma` BTC 1h trên 5 cửa sổ 8.000 bar. Cửa sổ nhãn `sideways_2024H2` bắt đầu ở bar 48.000, khoảng 23/06/2025 đến 22/05/2026, nên giao vùng này | `docs/archive/session-reports/ENHANCED_MA_MEASUREMENTS.md` mục 2 |

Ảnh hưởng tới quyết định: sweep ngưỡng RSI/ADX tháng 10 dùng t=30 và t=35 trên 2026-02-25 đến 2026-09-21, khoảng 78% cửa sổ nằm trong vùng này. Suy luận: đây là vi phạm quy tắc 1. Hai ảnh hưởng khác đã biết: điểm shadow trên cửa sổ này ghi thành 15 dòng `TOURNAMENT_PROMOTION` ngày 18/09, và `data/trend_pullback_admission_20261025_01/RESULTS.md` trích một report discovery trên 30/06–21/09/2026 làm gánh thử nghiệm, không làm căn cứ chọn. Chưa biết report nào khác có ảnh hưởng tới lựa chọn nào.

Hệ quả: các bước còn coi holdout là xác nhận cuối, như `SelectionPolicyBuilder` đòi holdout `COMPLETED` và hàng 4 của `operations/LIVE_READINESS_AGENT_PLAN.md`, hiện chưa có cửa sổ thay thế. Chọn dữ liệu xác nhận mới là quyết định của operator.

Giới hạn của guard: one-shot theo file manifest trong từng thư mục kết quả, nên không chặn strategy khác chấm cùng cửa sổ, và không chặn chính strategy đó chấm lại ở thư mục khác. `ma_vol_target` BTC 1h có cùng holdout id, mở ngày 24/09 ở `vol_target__BTCUSDT__1h` và mở lại ngày 25/09 ở `vol_target__BTCUSDT_1h`.

## Ghi chú triển khai P2 còn lại (chưa hoàn thành)

- Regime breakdowns: mới một phần. Per-fold trade minimum, block-bootstrap CI và PSR/DSR đã có trong `alpha_research/stats.py`, xem [Research Evidence](RESEARCH_EVIDENCE.md).
- Stress test fees/spread/slippage 1x/2x/3x + gaps, latency, missing data,
  partial fills, outages, correlated drawdowns: [Research Evidence](RESEARCH_EVIDENCE.md) và [Live Trading TODO](LIVE_TRADING_TODO.md) ghi đã làm từ 08/2026. Đính chính 31/10/2026 này chưa đối chiếu lại.
- Execution simulator (precision, fee assets, depth, cancellation, dust): [Live Trading TODO](LIVE_TRADING_TODO.md) ghi đã có Execution Simulator V2 trong `execution/simulator/`. Đính chính 31/10/2026 này chưa đối chiếu lại.
- Paper/testnet tracking error trước khi đánh giá maker/TWAP execution.
