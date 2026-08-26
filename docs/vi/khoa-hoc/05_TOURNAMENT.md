# Bài 05 — Strategy tournament và ma trận bằng chứng

> Mức độ: trung cấp · Thời lượng: 5–6 giờ · Trạng thái: **HIỆN HÀNH / đang harden**

## Mục tiêu

- Hiểu `EvaluationCellSpec`, cell identity và `EvaluationArtifact`.
- Chạy dry-run và matrix nhỏ có kiểm soát.
- Reconcile đủ `COMPLETED + FAILED + MISSING`.
- Phân tích cost/fault scenarios.
- Phân biệt tournament ranking với statistical selection.

## File cần đọc

- `src/trading_agent/backtest/tournament.py`
- `scripts/run_strategy_tournament.py`
- `scripts/tournament_health.py`
- `tests/backtest/test_tournament.py`
- `tests/backtest/test_tournament_faults.py`
- [Bằng chứng và artifact](../BANG_CHUNG_VA_ARTIFACT.md)

## 1. Cell là đơn vị bằng chứng

```text
strategy_id
× symbol
× timeframe
× params
× cost scenario
× fault profile
= một EvaluationCellSpec / cell_id
```

Mỗi cell phải có isolated state và output. Một exception không được làm cell biến
mất khỏi tổng inventory.

## 2. Trạng thái cell

| Trạng thái | Ý nghĩa | Có được selection dùng? |
| --- | --- | --- |
| `COMPLETED` | Report validate và đủ contract | Chỉ khi qua hard gates tiếp theo |
| `FAILED` | Lỗi visible, có failure reason | Không |
| Missing | Không artifact dù cell được kỳ vọng | Toàn matrix fail |

`FAILED` là evidence hữu ích. Missing là mất bằng chứng.

## 3. Lab 1 — Dry-run matrix

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies enhanced_ma,rsi,bbands \
  --symbols BTC/USDT,ETH/USDT \
  --scenarios 1x,slip_stress \
  --tail-bars 2000 \
  --out data/backtests/course_b05 \
  --dry-run
```

Trước khi chạy, tự tính số cell. Kỳ vọng: `3 × 2 × 2 = 12`.

Ghi lại 12 cell IDs và giải thích phần params hash trong ID.

## 4. Lab 2 — Chạy matrix nhỏ

Để tiết kiệm thời gian, bắt đầu 2 cell:

```bash
python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 3600 --heartbeat 30 \
  --result-file data/backtests/course_b05.control.json \
  -- .venv/bin/python scripts/run_strategy_tournament.py \
  --strategies enhanced_ma,rsi \
  --symbols BTC/USDT \
  --scenarios 1x \
  --tail-bars 2000 \
  --out data/backtests/course_b05
```

Audit `tournament_index.json`:

- expected = 2;
- completed + failed = 2;
- missing = 0;
- report path tồn tại cho completed;
- failure reason tồn tại cho failed;
- strategy/params identity khớp report.

## 5. Lab 3 — Đọc fault evidence có sẵn

Không cần chạy fault suite dài. Đọc artifacts dưới:

```text
data/backtests/tournament_fault_tests/
```

Chọn hai profile, ví dụ partial fill và cancel race. Với mỗi profile:

1. Fault được inject ở boundary nào?
2. Expected safe behavior là gì?
3. Report/artifact ghi failure/health ở đâu?
4. Exposure có thể tăng trái phép không?
5. Test nào bảo vệ behavior?

## 6. Parameter sweep trap

Một cell ID đổi theo params chưa chứng minh signal đổi. Audit:

```text
CLI --params
→ EvaluationCellSpec.params
→ adapter/strategy construction
→ signal calculation
→ report metadata
→ artifact params_hash
```

Bài tập: tìm test chứng minh hai param sets tạo computation khác nhau. Nếu không có
test đủ mạnh, ghi gap thay vì tự suy đoán.

## 7. Health monitor

Đọc `scripts/tournament_health.py` và phân biệt:

- process còn chạy;
- artifact còn tiến triển;
- cell bị stall;
- matrix hoàn thành nhưng có failure;
- script exit 0 nhưng evidence thiếu.

Một PID sống không chứng minh tournament khỏe.

## 8. Bài tập tự làm — Matrix audit memo

Tạo bảng:

| Cell | Status | Return | Drawdown | Trades | Costs | Execution health | Eligible? |
| --- | --- | ---: | ---: | ---: | ---: | --- | --- |

Không xếp hạng chỉ bằng Sharpe. Viết `Eligible? = chưa xác định` nếu chưa có
statistical gates.

## Lỗi thường gặp

- Chạy full 5×10 trước khi smoke pass.
- Rerun overwrite evidence không explicit.
- Exception làm mất cell.
- Shared output/state gây race.
- Params chỉ đổi ID, không đổi signal.
- Dùng failed-cell rate thấp để bỏ qua missing cells.
- Tuyên bố winner từ in-sample tournament.

## Self-check

1. Vì sao cell `FAILED` tốt hơn missing?
2. Cell ID cần bind những gì?
3. Health monitor nên dựa artifact hay PID?
4. Vì sao cost scenario là chiều của matrix?
5. Tournament complete còn thiếu gì trước selection?

## Exit gate

- [ ] Dry-run đúng 12 cell.
- [ ] Matrix nhỏ được chạy bằng controller và reconcile đủ.
- [ ] Audit hai fault artifacts.
- [ ] Trace params binding.
- [ ] Viết matrix audit memo không tuyên bố winner quá sớm.

Tiếp theo: [Bài 06 — WFO và selection](06_SELECTION_WFO.md).

