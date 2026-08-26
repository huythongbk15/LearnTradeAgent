# Bài 04 — Backtest thực tế và cách đọc report

> Mức độ: trung cấp · Thời lượng: 5–6 giờ · Trạng thái: **HIỆN HÀNH**

## Mục tiêu

- Phân biệt signal quality và execution quality.
- Hiểu lookahead, position timing, fees, slippage và impact.
- Chạy single-pair backtest bằng state cô lập.
- Audit `BacktestReportV2` thay vì nhìn một metric.
- Hiểu golden replay và giới hạn của determinism.

## File cần đọc

- [Backtest Engine](../../BACKTEST_ENGINE.md)
- `src/trading_agent/backtest/engine.py`
- `src/trading_agent/backtest/portfolio_backtest.py`
- `src/trading_agent/backtest/report_v2.py`
- `src/trading_agent/backtest/reporting.py`
- `src/trading_agent/execution/simulator/`
- `tests/test_backtest_accounting.py`
- `tests/test_backtest_report_v2.py`
- `tests/test_portfolio_backtest.py`

## 1. Mental model

```text
closed-bar observation
  → strategy decision
  → target exposure/order plan
  → simulated broker/fills
  → fee + spread + slippage + impact
  → ledger
  → equity + risk + execution metrics
  → schema-validated report
```

Nếu backtest chỉ nhân `position × close_return`, nó không chứng minh behavior của
order lifecycle, cash guard, partial fills hoặc protection.

## 2. Metrics phải đọc theo nhóm

| Câu hỏi | Metrics/evidence cần đọc cùng nhau |
| --- | --- |
| Có lợi nhuận không? | total/annualized return + benchmark |
| Rủi ro thế nào? | max drawdown + volatility + tail loss |
| Risk-adjusted ra sao? | Sharpe/Sortino + sample length |
| Edge có thật không? | profit factor + win rate + trade count |
| Chi phí ăn bao nhiêu? | fee + spread + slippage + impact attribution |
| Execution có ổn không? | rejects + partial fills + latency/fault health |
| Có tái hiện không? | data/code/config identity + golden replay |

Không đọc win rate một mình. Strategy win rate thấp vẫn có thể có payoff tốt; PF
cao với vài trade vẫn có thể không đáng tin.

## 3. Lab có hướng dẫn — Contract tests

```bash
.venv/bin/python -m pytest -q \
  tests/test_backtest_accounting.py \
  tests/test_backtest_report_v2.py \
  tests/test_portfolio_backtest.py
```

Chọn ba test và ghi invariant:

- cash/equity accounting;
- schema/required fields;
- rejection hoặc fill safety.

## 4. Lab thực hành — Single-pair smoke

```bash
.venv/bin/python scripts/full_system_backtest.py \
  --fresh --symbol BTC/USDT --timeframe 1h --tail-bars 2000 \
  --allow-new-exposure \
  --state-dir data/backtests/course_b04/state \
  --report-path data/backtests/course_b04/report.json \
  --run-id course_b04
```

Lệnh đọc local data, ghi local paper state/report và không cho phép mainnet.

### Audit report

Dùng mẫu `Audit report/artifact`, trả lời:

1. Data window và quality evidence ở đâu?
2. Strategy/code/params identity ở đâu?
3. Equity bắt đầu/kết thúc có reconcile với return không?
4. Trade/fill count có hợp lý không?
5. Costs nào được model, costs nào chưa?
6. Warning nào làm yếu kết luận?

## 5. Lab chi phí

Không cần sửa production code. Dùng tournament cost scenarios ở bài sau hoặc đọc
`CostScenario` trong `backtest/tournament.py` để lập bảng:

| Scenario | Commission | Slippage | Kỳ vọng |
| --- | ---: | ---: | --- |
| 1x | baseline | baseline | reference |
| 2x | tăng | tăng | return/PF giảm |
| slip stress | baseline/tăng | stress | strategy turnover cao chịu ảnh hưởng mạnh |

Viết trước giả thuyết: metric nào giảm mạnh nhất và vì sao. Không điều chỉnh giả
thuyết sau khi thấy kết quả mà không ghi lại.

## 6. Golden replay

Golden replay kiểm tra cùng input/config tạo cùng decision, ledger và metrics trong
tolerance. Nó chứng minh determinism, không chứng minh profitability hoặc
production safety.

Đọc:

- `scripts/verify_golden_replay.py`
- `artifacts/golden/golden_replay_s0.json`
- `tests/test_multi_pair_backtest_contract.py`

> `multi_pair_1h_backtest.py` là batch thật, không dùng `--help`. Chỉ chạy qua
> controller khi bạn có đủ thời gian/tài nguyên.

## 7. Bài tập tự làm

### Bài 04-A — Metric conflict

Chọn một report có:

- return dương nhưng drawdown lớn; hoặc
- PF > 1 nhưng return âm; hoặc
- Sharpe dương nhưng ít trade.

Viết memo 200–300 từ giải thích vì sao chưa thể promote.

### Bài 04-B — Thiết kế test accounting

Thiết kế fixture 3 bar, một buy, một partial fill, một fee. Tính bằng tay:

- cash sau fill;
- position quantity;
- fee;
- mark-to-market equity;
- realized/unrealized PnL.

Sau đó viết test đối chiếu simulator/ledger hiện có.

## Lỗi thường gặp

- Quên shift decision/position.
- So strategy với cash thay vì benchmark phù hợp.
- Không cô lập state giữa run.
- Ép metric thiếu thành zero.
- Nhìn return gross thay vì net costs.
- Dùng deterministic replay như bằng chứng out-of-sample.

## Exit gate

- [ ] Accounting/report/portfolio tests đạt.
- [ ] Chạy và audit một report local.
- [ ] Hoàn thành metric-conflict memo.
- [ ] Tính tay và thiết kế test accounting.
- [ ] Giải thích được determinism chứng minh và không chứng minh điều gì.

Tiếp theo: [Bài 05 — Tournament](05_TOURNAMENT.md).

