# Documentation Map — Bản đồ tài liệu tinh gọn

> **Status:** CURRENT
> **Owner:** Trading systems
> **Verified:** 2026-08-31
> **Rule:** đọc [Core System](CORE_SYSTEM.md) trước; tài liệu khác không được định nghĩa một luồng execution khác.

## 1. Bốn lớp tài liệu

| Lớp | Câu hỏi trả lời | Tài liệu chính | Trạng thái |
| --- | --- | --- | --- |
| **CORE** | Hệ thống là gì và đâu là đường lệnh duy nhất? | [CORE_SYSTEM](CORE_SYSTEM.md), [README](README.md), [ARCHITECTURE](ARCHITECTURE.md) | CURRENT |
| **CONTRACTS** | Dữ liệu, strategy, evidence, authority và lifecycle có hợp đồng gì? | [Strategy Lifecycle](architecture/STRATEGY_LIFECYCLE.md), [Backtest Engine](BACKTEST_ENGINE.md), [Promotion Binding](PROMOTION_BINDING.md), [Runtime Resolver](RUNTIME_RESOLVER.md), [Evidence Artifacts](reference/EVIDENCE_ARTIFACTS.md) | CURRENT |
| **OPERATIONS** | Kiểm tra, vận hành, rollback và release ra sao? | [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md), [Live Runbook](LIVE_TRADING_RUNBOOK.md), [S7 Evidence](S7_OPERATIONAL_EVIDENCE_RUNBOOK.md), [Security](SECURITY.md), [Deployment](DEPLOYMENT.md) | CURRENT / evidence pending |
| **RESEARCH & LEARNING** | Làm nghiên cứu và học hệ thống như thế nào? | [Research-to-Production](guides/RESEARCH_TO_PRODUCTION.md), [Research Methodology](RESEARCH_METHODOLOGY.md), [Course V2](tutorials/README.md), [Khóa học tiếng Việt](vi/khoa-hoc/README.md) | CURRENT + TARGET boundaries |

Các file khác là reference chuyên biệt hoặc historical; không cần đọc tuần tự để hiểu core.

## 2. Lộ trình đọc theo vai trò

### Người mới / developer

1. [CORE_SYSTEM.md](CORE_SYSTEM.md)
2. [Getting Started](getting-started.md)
3. [ARCHITECTURE.md](ARCHITECTURE.md)
4. [DEVELOPMENT.md](DEVELOPMENT.md)

### Researcher / strategy engineer

1. [CORE_SYSTEM.md](CORE_SYSTEM.md)
2. [Research-to-Production](guides/RESEARCH_TO_PRODUCTION.md)
3. [Research Methodology](RESEARCH_METHODOLOGY.md)
4. [Evidence Artifacts](reference/EVIDENCE_ARTIFACTS.md)
5. [Research Holdout](RESEARCH_HOLDOUT.md)

### Runtime / execution engineer

1. [CORE_SYSTEM.md](CORE_SYSTEM.md)
2. [Strategy Lifecycle](architecture/STRATEGY_LIFECYCLE.md)
3. [Backtest Engine](BACKTEST_ENGINE.md)
4. [Authority Chain Ops](AUTHORITY_CHAIN_OPS.md)
5. [Live Trading Runbook](LIVE_TRADING_RUNBOOK.md)

### Operator / release reviewer

1. [CORE_SYSTEM.md](CORE_SYSTEM.md)
2. [Capability Matrix](CAPABILITY_MATRIX.md)
3. [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md)
4. [Live Readiness](LIVE_TRADING_TODO.md)
5. [S7 Operational Evidence Runbook](S7_OPERATIONAL_EVIDENCE_RUNBOOK.md)

### Người học bằng tiếng Việt

Đọc [khóa học thực hành tiếng Việt](vi/khoa-hoc/README.md) theo 12 bài, nhưng dùng `CORE_SYSTEM.md`, Capability Matrix và runbook làm nguồn sự thật khi nội dung bài học khác với code hiện tại. Course V1 đã được loại bỏ khỏi repository.

## 3. Phân loại các nhóm đang gây phình

| Nhóm | Ví dụ | Cách dùng |
| --- | --- | --- |
| Roadmap / phase | `ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md`, `P0_EXECUTION_MAP.md`, `PHASE*_*.md` | Chỉ dùng để theo dõi kế hoạch và lịch sử; không dùng làm runtime manual. Xem [Adaptive Roadmap Status](ADAPTIVE_ROADMAP_STATUS.md). |
| Snapshot / summary cũ | Các phase report còn lại | Chỉ đọc để truy vết; số liệu hoặc lệnh có thể cũ. Ưu tiên CORE và Capability Matrix. |
| Generated map | `PROJECT_MAP.md`, các báo cáo sinh tự động | Chỉ tham khảo cấu trúc vật lý; không sửa tay, không dùng để suy ra capability. |
| Operations chuyên biệt | `deployment/`, `security/`, `operations/`, các runbook | Chỉ đọc khi thực hiện đúng tác vụ; phải ghi environment, evidence và rollback. |
| Optional surfaces | agents/LLM, options, DEX/futures, portfolio, web, infra/K8s | Không thuộc core; không thêm vào đường order nếu chưa có contract/evidence. |
| External knowledge | `wsl-guide/` | Hướng dẫn WSL độc lập, không phải tài liệu trading-system. |
| Historical material | Git history và phase record đã đóng | Chỉ truy vết khi cần; không cập nhật như current docs. |

## 4. Quy tắc hợp nhất và lưu trữ

- Mỗi trang chỉ có **một primary type**: concept, tutorial, how-to, reference, runbook hoặc historical.
- Nếu hai trang trả lời cùng một câu hỏi, giữ một trang làm authority và thêm liên kết forward ở trang còn lại.
- Không copy bảng metrics vào tài liệu evergreen; link tới artifact bất biến có identity đầy đủ.
- Roadmap chỉ mô tả `TARGET`; code/tests/evidence mới quyết định `CURRENT`.
- Khi một trang không còn được cập nhật, thêm banner `HISTORICAL` và liên kết tới trang thay thế; chỉ di chuyển vào `archive/` khi đã kiểm tra toàn bộ inbound links.
- Mỗi thay đổi core phải cập nhật `CORE_SYSTEM.md`, `CAPABILITY_MATRIX.md` hoặc status/runbook liên quan; không rải cùng một sự thật qua nhiều summary.

## 5. Nguồn sự thật theo câu hỏi

| Câu hỏi | Nguồn duy nhất cần ưu tiên |
| --- | --- |
| Luồng hệ thống là gì? | [CORE_SYSTEM.md](CORE_SYSTEM.md) |
| Code đang đạt đến đâu? | [Capability Matrix](CAPABILITY_MATRIX.md) + tests/CI evidence |
| Mainnet có được phép không? | [Live Readiness](LIVE_TRADING_TODO.md) |
| Một strategy đi từ research tới runtime thế nào? | [Strategy Lifecycle](architecture/STRATEGY_LIFECYCLE.md) + [Promotion Binding](PROMOTION_BINDING.md) |
| Một lệnh đi qua những gate nào? | [ARCHITECTURE.md](ARCHITECTURE.md) + [Runtime Resolver](RUNTIME_RESOLVER.md) |
| Chạy validation ra sao? | [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md) |
| Thiếu evidence S7 cần bổ sung gì? | [S7 Operational Evidence Runbook](S7_OPERATIONAL_EVIDENCE_RUNBOOK.md) |

## 6. Definition of done cho tài liệu

Một tài liệu được xem là sẵn sàng khi có status/owner/verified date, link nội bộ chạy được, ví dụ ghi rõ environment và side effect, phân biệt current/target, không chứa secret, và không lặp lại authority của trang khác. Checklist đầy đủ nằm ở [Documentation Standard](DOCUMENTATION_STANDARD.md).
