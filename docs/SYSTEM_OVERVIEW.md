# Tổng quan hệ thống: cái gì đang thật sự chạy

Status: CURRENT
Owner: operator và project maintainers
Verified: 2026-10-31, tại commit `54c3ee08`. Đối chiếu với code, audit log và decision file local.
Evidence: [tracker](CAMPAIGN_STATUS.md), [tổng hợp nghiên cứu](operations/RESEARCH_SYNTHESIS_20261030.md), [sổ lộ holdout](RESEARCH_HOLDOUT.md), [kế hoạch vận hành](operations/OPERABLE_SYSTEM_PLAN.md)

Trang này trả lời ba câu: hệ thống đang làm gì, strategy nào đã qua, và mục tiêu nào đang được theo. Thiết kế nằm ở [CORE_SYSTEM](CORE_SYSTEM.md). Tiến độ và quyết định mới nhất nằm ở [tracker](CAMPAIGN_STATUS.md).

## Hiện trạng

- **Chưa có strategy nào để chọn.** Không strategy nào đạt `RESEARCH_VALIDATED`. Đây là kết quả đo, không phải việc còn dở.
- **Runner chạy nhưng không mua.** `scripts/live_enhanced_ma_binance.py` chạy mỗi 2 giờ trên Binance Spot testnet, tính Enhanced MA vì lịch sử, không phải vì được chọn. BUY bị khóa với lý do `entries_locked_no_promoted_runtime`. Nó chỉ quản lý và thoát vị thế, trừ khi operator ký track vận hành (M3). M3 là bộ kiểm thử đường lệnh theo đồng hồ, không phải strategy.
- **Research và runner là hai hệ thống riêng.** Ngoài test, không code nào tạo `EvidenceArtifact` hay gọi promote, nên một kết quả research tốt cũng chưa có đường tới runner.
- **Market đang dừng.** Từ 2026-10-30 operator không mở market hypothesis mới. Đọc forward của H-XSEC-01 và testnet vẫn tiếp tục. Ngoại lệ ghi ở tracker. Mainnet **NO-GO**.

## Ba phần đã xây, chưa nối

| Phần | Đã có | Chỗ đứt |
| --- | --- | --- |
| Research | Data, simulator, nested WFO, 20 gate, packet có binding | Kết thúc ở thẻ viết tay, không sinh bằng chứng cho ladder |
| Promotion ladder | Stage, requirement, store | Không có producer; `data/state/promotion.db` và `artifacts.db` có 0 dòng; bậc testnet đòi bằng chứng testnet hoặc shadow mà chưa code nào tạo ra |
| Runner | Lifecycle, stop, reconciliation, safety, audit | `RuntimeStrategyResolver` chỉ biết 8 tên, trong đó `online_learning` không resolve được. `volatility_breakout`, `trend_pullback`, `liquidity_reversal` không có trong danh sách. Mainnet chỉ nhận enhanced_ma |

Với cùng một strategy, research và runner cũng giao dịch khác nhau: thời điểm vào lệnh, nhịp chạy, cách thoát, cỡ lệnh và giới hạn rủi ro. Độ lệch này chưa từng được đo.

## Cơ chế chọn strategy

Có ba thiết kế chồng lên nhau, không phải một cơ chế chạy thông suốt:

- [Adaptive roadmap](ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md) S0–S7: thiết kế đích.
- Tournament tháng 9/2026: shadow, paper, duyệt 5 vai. **Không phải chạy thử thật.** Nó replay nến quá khứ, điểm Sharpe dùng chính nến đang quyết định, và 5 dòng duyệt do một script ghi với chữ ký `unsigned`. Đường này đã tắt trên master và chưa từng nối vào runner.
- [Playbook](operations/MULTI_STRATEGY_RESEARCH_PLAN.md) E0–E6 cùng L2 và ladder: quy trình hiện hành.

| Bước | Thực tế |
| --- | --- |
| E0–E2: dữ liệu, đăng ký, một phép đo | Đã chạy hơn 24 vòng, không vòng nào đạt `RESEARCH_VALIDATED`. D1 của H-XSEC-01 ra `CONTINUE_TO_CONFIRMATION`, chưa phải đạt |
| E3: nested WFO, gate, holdout | Có chạy theo các contract cũ, kết quả FAIL, ABSTAIN hoặc bị chặn |
| Ladder tới `TESTNET_ELIGIBLE` | Chưa từng chạy với bằng chứng thật |
| E4–E6: gắn runner, soak, canary | Chưa chạy với strategy nào |

## Vì sao chưa thể có một câu "đạt" sạch

- **Dữ liệu.** Holdout 2026-02-06 đến 2026-08-05 đã bị chấm điểm, xem [sổ lộ holdout](RESEARCH_HOLDOUT.md). Cohort chưa đọc đã thỏa thuận trước duy nhất là đọc forward của H-XSEC-01 sau 2027-12-27.
- **Phạm vi.** Gần như mọi phép đo là long-only trên họ MA và trend.
- **Cổng.** Ba nơi quyết định với ngưỡng khác nhau: nested WFO cho research, file ký từ `BacktestEngine` cho mainnet, ladder cho testnet. Số trial trong DSR khác nhau theo đường. Không cổng nào so với nắm giữ thụ động. Mọi quyết định theo 20 gate của nested WFO đều trượt nhiều cổng khác, nên sửa một cổng không đổi kết quả nào. Đổi cổng là quyền của operator.
- **Chi phí.** Phí và trượt giá giả định chưa từng được đối chiếu với fill thật.
- **Đường tín hiệu.** Đường tính tín hiệu của research, gồm tournament và nested WFO, chỉ cho mỗi bar thấy warmup+1 dòng, nên một số quy tắc quên vị thế đang mở. Kết quả đã ghi của H-VOLBREAK-01 vì vậy là của đường đã đăng ký, không phải của quy tắc đúng như đã viết, xem [kết quả short](operations/SHORT_SIDE_RESULTS_20261031.md).

## Short và futures

Runner hiện chỉ chạy spot long. Ngày 2026-10-31 operator yêu cầu một phép đo có giới hạn. Kết quả: trên ba quy tắc, thêm short không làm tăng lợi nhuận, gần như chỉ kiếm tiền khi thị trường giảm, và không giảm drawdown. Trên khối chính 2023 đến 2026-02-05, hai quy tắc là `cannot tell` và một là `clearly worse`; trên khối 2020–2022 cả ba là `cannot tell`. Vì vậy kết quả không chứng minh short không có lợi thế. Phép đo dùng giá spot thay perp. Chi tiết ở [kết quả short](operations/SHORT_SIDE_RESULTS_20261031.md).

## Ba mục tiêu đang được ghi

Các tài liệu nêu ba mục tiêu khác nhau, và hiện chưa có quyết định gộp chúng:

- [CORE_SYSTEM](CORE_SYSTEM.md): một vòng lặp tái lập được, giải thích được, có giới hạn rủi ro.
- [Playbook](operations/MULTI_STRATEGY_RESEARCH_PLAN.md): một policy đủ điều kiện cho một cặp và khung thời gian, tới vận hành có giám sát.
- [Kế hoạch vận hành](operations/OPERABLE_SYSTEM_PLAN.md): một lần chạy end-to-end sạch trên testnet, lặp lại được.

## Dễ gây nhầm

Các chữ `Winner`, `APPROVED`, `FINAL_PASS` trong tài liệu cũ không phải bằng chứng. Hãy đối chiếu với output của nested WFO trước khi tin. Tài liệu đã được đính chính có banner ngày 2026-10-31.
