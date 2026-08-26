# Bài 08 — Execution lifecycle, risk và protective controls

> Mức độ: nâng cao · Thời lượng: 5–6 giờ · Trạng thái: **HIỆN HÀNH**

## Mục tiêu

- Truy `OrderIntent` đến broker/fill/reconciliation.
- Hiểu canonical broker gateway và lifecycle state.
- Phân biệt submit, ACK, fill, cancel request và terminal cancel.
- Giải thích reservation, cash/exposure guard và protective order.
- Phân tích race/fault mà không tạo unauthorized exposure.

## File cần đọc

- [Authority Chain](../../AUTHORITY_CHAIN_OPS.md)
- `src/trading_agent/execution/application.py`
- `src/trading_agent/execution/canonical/order_planner.py`
- `src/trading_agent/execution/canonical/broker_gateway.py`
- `src/trading_agent/execution/canonical/protection.py`
- `src/trading_agent/execution/lifecycle/lifecycle.py`
- `src/trading_agent/execution/lifecycle/store.py`
- `src/trading_agent/execution/risk_controller.py`
- `tests/execution/test_canonical_pipeline.py`
- `tests/test_execution_lifecycle.py`
- `tests/execution/test_cancel_stop_regressions.py`

## 1. State machine tối thiểu

```text
PLANNED
  → SUBMIT_REQUESTED
  → ACKNOWLEDGED
  → PARTIALLY_FILLED
  → FILLED

ACKNOWLEDGED/PARTIALLY_FILLED
  → CANCEL_REQUESTED
  → CANCELED | FILLED | REJECTED
```

`CANCEL_REQUESTED` không phải `CANCELED`. Trong thời gian race, fill vẫn có thể đến.

## 2. Reduction trước increase

Khi rebalance nhiều order, giảm exposure trước rồi mới tăng exposure giúp:

- giải phóng cash/reservation;
- giảm gross exposure tạm thời;
- tránh âm cash do thứ tự fill;
- làm safety guard deterministic hơn.

Nhưng thứ tự plan không thay thế aggregate actual-fill checks.

## 3. Lab contract tests

```bash
.venv/bin/python -m pytest -q \
  tests/execution/test_canonical_pipeline.py \
  tests/test_execution_lifecycle.py \
  tests/execution/test_cancel_stop_regressions.py -vv
```

Chọn test cancel/fill race và vẽ timeline event.

## 4. Bài tập tính tay — Reservation

Tài khoản:

```text
cash = 10,000
BTC position = 0.10
mark = 50,000
```

Batch muốn:

- sell 0.05 BTC;
- buy ETH trị giá 4,000;
- fee estimate 0.1%;
- sell fill chỉ 50%;

Tính:

1. cash reservation trước submit;
2. cash thực sau partial sell;
3. buy quantity tối đa an toàn;
4. fee buffer;
5. điều gì xảy ra nếu buy được fill trước sell.

So kết quả với invariants trong backtest/execution tests.

## 5. Protective order

`PROTECTED` chỉ hợp lệ khi có external evidence/ACK đủ contract. Không được đánh
dấu protected chỉ vì đã gọi API.

Audit:

- quantity > 0 và đúng position;
- protection side/price hợp lệ;
- ACK durable;
- replacement không tạo gap không kiểm soát;
- outage dẫn đến giảm risk/alert;
- restart/reconcile khôi phục đúng state.

## 6. Bài tập fault table

| Fault | Nguy cơ | Safe response | Evidence cần có |
| --- | --- | --- | --- |
| Submit timeout | Duplicate order | Reconcile/idempotency, không assume failed | Request/correlation + broker query |
| Partial fill | Exposure/cash lệch | Update actual fill, resize remaining | Fill ledger |
| Cancel/fill race | Double action | Process event ordering/idempotently | Lifecycle events |
| Protection outage | Naked exposure | Reduce/stop new exposure/alert | Outage + protective state |
| Stale market data | Sai sizing/price | Reject/abstain | Trusted timestamp reason |

Bổ sung hai fault của riêng bạn.

## 7. Reconciliation

Reconciliation trả lời:

- broker có order nào hệ thống không biết?
- internal open order có còn tồn tại bên broker?
- position/cash/fills có khớp không?
- event nào bị mất hoặc xử lý hai lần?

Không resume trading sau restart chỉ dựa vào local state nếu broker state chưa
được đối chiếu.

## 8. Lỗi thường gặp

- Dùng timestamp làm idempotency ID yếu.
- Release reservation trước terminal evidence.
- Coi API response 200 là protective ACK đầy đủ.
- Direct broker write bypass gateway/lifecycle.
- Retry submit mà không correlation/idempotency.
- Dùng planned quantity thay actual fill để tính position.

## Exit gate

- [ ] Execution/lifecycle/regression tests đạt.
- [ ] Vẽ đúng cancel/fill race timeline.
- [ ] Tính tay reservation và partial-fill scenario.
- [ ] Audit protective ACK contract.
- [ ] Hoàn thành fault table và reconciliation checklist.

Tiếp theo: [Bài 09 — Portfolio và router](09_PORTFOLIO_ROUTER.md).

