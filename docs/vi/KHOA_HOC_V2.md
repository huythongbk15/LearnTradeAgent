# Khóa học V2 — Xây dựng trading system dựa trên bằng chứng

> Trạng thái: **HIỆN HÀNH** · Đối tượng: developer, researcher và operator
>
> Syllabus đối chiếu: [Trading System Course V2](../tutorials/README.md)

Khóa học này thay Course V1 dựa trên việc đọc từng file. Người học tập trung vào
contract, invariant, failure path và artifact. Mục tiêu không phải “chạy ra lợi
nhuận”, mà là biết kết luận khi nào evidence đủ hoặc chưa đủ.

## Cách học mỗi module

1. Đọc khái niệm và contract.
2. Tìm implementation hiện tại qua `PROJECT_MAP.md`.
3. Chạy bài tập nhỏ nhất trong local/paper scope.
4. Mở artifact và kiểm tra identity.
5. Chủ động tạo một failure case.
6. Trả lời exit questions trước khi sang module tiếp theo.

## Lộ trình 12 module

| # | Module | Tài liệu | Sản phẩm học tập | Trạng thái |
| --- | --- | --- | --- | --- |
| 1 | System invariants và authority | [Kiến trúc](../ARCHITECTURE.md) | Vẽ năm plane và đánh dấu authority mở exposure | **HIỆN HÀNH** |
| 2 | Data trust và point-in-time | [Research Methodology](../RESEARCH_METHODOLOGY.md) | Phát hiện missing history, gap và future leakage | **HIỆN HÀNH** |
| 3 | Canonical strategy contract | [Vòng đời strategy](VONG_DOI_CHIEN_LUOC.md) | Giải thích descriptor, registry, bridge và abstain | **HIỆN HÀNH** |
| 4 | Baseline và golden replay | [Runbook luồng chính](KIEM_TRA_LUONG_CHINH.md) | Tạo output cô lập và kiểm tra determinism | **HIỆN HÀNH** |
| 5 | Execution-aware backtest | [Backtest Engine](../BACKTEST_ENGINE.md) | Tách alpha khỏi fee, fill và execution failure | **HIỆN HÀNH** |
| 6 | Strategy tournament | [Từ research đến vận hành](NGHIEN_CUU_DEN_VAN_HANH.md) | Reconcile matrix strategy × pair × scenario | **HIỆN HÀNH / HARDENING** |
| 7 | Nested WFO và statistical selection | [Adaptive Roadmap](../ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md) | Thiết kế selector có `no winner` và không leakage | **MỤC TIÊU** |
| 8 | Evidence và promotion | [Artifact](BANG_CHUNG_VA_ARTIFACT.md) | Truy evaluation → policy → promotion → runtime | **NỀN TẢNG HIỆN HÀNH** |
| 9 | Regime routing và safe switching | [Adaptive Roadmap](../ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md) | Giải thích posterior, entropy, hysteresis và incumbent | **MỤC TIÊU** |
| 10 | Shared-capital portfolio | [Production Policy](../PRODUCTION_POLICY.md) | Xử lý strategy preference dưới portfolio constraints | **MỤC TIÊU** |
| 11 | Execution lifecycle và protection | [Authority Chain](../AUTHORITY_CHAIN_OPS.md) | Truy intent → authorization → order → fill → ACK | **NỀN TẢNG HIỆN HÀNH** |
| 12 | Shadow, canary và production | [Live Runbook](../LIVE_TRADING_RUNBOOK.md) | Lập kế hoạch staged validation và rollback | **VẬN HÀNH CÓ GATE** |

## Bài lab cốt lõi — Audit một tournament cell

### Điều kiện

- `.venv` đã cài dependency;
- có local OHLCV cho `BTC/USDT`, timeframe `1h`;
- không cần live broker credentials.

### 1. Preview

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies rsi --symbols BTC/USDT --scenarios 1x \
  --tail-bars 2000 --out data/backtests/course_v2_vi --dry-run
```

Kỳ vọng: đúng một pending cell và chưa tạo report.

### 2. Execute

Chạy lại không có `--dry-run`. Output nằm dưới
`data/backtests/course_v2_vi/`.

### 3. Audit

Xác minh:

- `tournament_index.json` account đúng một cell;
- cell ID phản ánh strategy/pair/timeframe/params/scenario;
- `COMPLETED` có report path, `FAILED` có reason;
- metric thiếu không bị biểu diễn thành zero return;
- rerun không explicit không overwrite evidence hoàn tất;
- report strategy identity khớp canonical descriptor.

### 4. Failure exercise

Đưa unknown strategy vào `--strategies`. Kết quả an toàn là reject/failed rõ ràng,
không được chạy một strategy khác.

### 5. Viết kết luận

```text
Cell identity:
Data/code identity:
Status:
Execution health:
Cost assumptions:
Failure evidence:
Decision: PROMOTE | DO NOT PROMOTE | INSUFFICIENT EVIDENCE
Reason:
```

Một cell smoke không bao giờ đủ để `PROMOTE`; bài tập kiểm tra người học có nhận
ra giới hạn đó hay không.

## Exit questions toàn khóa

1. Tại sao tournament index đáng tin hơn bảng Sharpe copy vào Markdown?
2. Identity nào chứng minh params trong report là params tạo signal?
3. Vì sao tournament hoàn tất vẫn chưa được promote?
4. Router phải làm gì khi regime confidence thấp?
5. Portfolio/risk và strategy, bên nào có authority cao hơn?
6. Bằng chứng nào phân biệt paper-safe với production-validated?
7. Rollback đúng có sửa artifact lịch sử không?

## Điều kiện hoàn thành

Người học nộp:

- một smoke artifact hợp lệ;
- một failed artifact được giải thích;
- sơ đồ lineage và authority;
- đánh giá cost/execution health;
- một quyết định có hard gates và limitation rõ ràng.

`INSUFFICIENT EVIDENCE` là đáp án hợp lệ và thường là đáp án chuyên nghiệp nhất.

