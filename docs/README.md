# Trading Agent — Documentation Hub

> **Status:** CURRENT
> **Verified:** 2026-08-31
> **Mainnet:** `NO-GO` cho tới khi các release gate có evidence thực tế.

Nếu chỉ đọc một trang, hãy đọc [CORE_SYSTEM.md](CORE_SYSTEM.md). Trang đó mô tả sản phẩm cốt lõi, đường lệnh duy nhất, invariant và ranh giới với các tính năng mở rộng.

Nhãn `CURRENT` mô tả behavior hoặc quy trình đang tồn tại; `TARGET` là mục tiêu chưa đủ evidence; `HISTORICAL` chỉ giữ để truy vết. Xem [Documentation Standard](DOCUMENTATION_STANDARD.md) để biết quy ước đầy đủ.

## Bắt đầu nhanh

| Bạn muốn | Đọc trước | Sau đó |
| --- | --- | --- |
| Hiểu toàn hệ thống | [Core System](CORE_SYSTEM.md) | [Architecture](ARCHITECTURE.md) |
| Chạy/kiểm tra local | [Getting Started](getting-started.md) | [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md) |
| Nghiên cứu/chọn strategy | [Research-to-Production](guides/RESEARCH_TO_PRODUCTION.md) | [Research Methodology](RESEARCH_METHODOLOGY.md), [Evidence Artifacts](reference/EVIDENCE_ARTIFACTS.md) |
| Vận hành paper/testnet | [Live Trading Runbook](LIVE_TRADING_RUNBOOK.md) | [S7 Evidence Runbook](S7_OPERATIONAL_EVIDENCE_RUNBOOK.md) |
| Đánh giá mức hoàn thiện | [Capability Matrix](CAPABILITY_MATRIX.md) | [Adaptive Roadmap Status](ADAPTIVE_ROADMAP_STATUS.md), [Live Readiness](LIVE_TRADING_TODO.md) |
| Học bằng tiếng Việt | [Khóa học thực hành](vi/khoa-hoc/README.md) | 12 bài, lab, rubric và capstone |

## Luồng chuẩn

```text
data quality + point-in-time features
  → canonical strategy / frozen forecast
  → risk decision / target exposure
  → authority + shared capital
  → order permission / execution plan
  → lifecycle / broker / fills / reconciliation
  → attribution / monitoring / promotion evidence
```

Không có bước nào được tự thay thế strategy, dữ liệu hoặc evidence bị thiếu. Kết quả an toàn là abstain, `NO_TRADE`, `BLOCK` hoặc `REDUCE_ONLY`.

## Bản đồ tài liệu

Xem [DOCUMENTATION_MAP.md](DOCUMENTATION_MAP.md) để biết taxonomy, thứ tự đọc theo vai trò, nguồn sự thật và quy tắc gộp/lưu trữ. [DOCUMENTATION_STANDARD.md](DOCUMENTATION_STANDARD.md) quy định cách viết command, status và evidence.

## Các hợp đồng quan trọng

- [Strategy Lifecycle](architecture/STRATEGY_LIFECYCLE.md) — research → selection → promotion → runtime.
- [Backtest Engine](BACKTEST_ENGINE.md) — cell accounting, execution simulation và attribution.
- [Promotion Binding](PROMOTION_BINDING.md) — content identity và provenance.
- [Runtime Resolver](RUNTIME_RESOLVER.md) — authority fail-closed.
- [Production Policy](PRODUCTION_POLICY.md) — điều kiện đủ để được xét production.
- [Capability Matrix](CAPABILITY_MATRIX.md) — `Implemented` khác `Tested`, `Paper`, `Testnet`, `Production`.

## Roadmap và tài liệu lịch sử

[ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md](ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md) là roadmap pha trộn ghi chú đã làm và mục tiêu tương lai; không dùng làm runtime manual. Đọc [ADAPTIVE_ROADMAP_STATUS.md](ADAPTIVE_ROADMAP_STATUS.md) để xem phần code-complete và evidence còn thiếu.

Các phase report còn lại trong `docs/` là reference/historical. `docs/wsl-guide/` và `COURSE/` đã được loại khỏi cây tài liệu chính vì không thuộc hoặc đã được thay thế trong hệ thống hiện tại.

## Quy tắc sự thật

1. Code và tests xác lập behavior; docs chỉ giải thích và trỏ tới evidence.
2. Backtest phải có data/code/config/cost identity; số không có identity chỉ là minh họa.
3. `Implemented`, `Tested`, `Research/Paper/Testnet Validated` và `Production Validated` là các mức khác nhau.
4. Không đưa secrets, private identifiers hoặc payload broker chưa redacted vào tài liệu.
5. Thay đổi core phải cập nhật trang core hoặc contract tương ứng; không tạo thêm summary trùng lặp.
