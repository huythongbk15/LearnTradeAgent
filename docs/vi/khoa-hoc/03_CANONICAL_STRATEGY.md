# Bài 03 — Canonical strategy: descriptor, registry và abstention

> Mức độ: trung cấp · Thời lượng: 5–6 giờ · Trạng thái: **HIỆN HÀNH**

## Mục tiêu

- Hiểu canonical strategy contract.
- Phân biệt descriptor, adapter, bridge và runtime.
- Audit parameter binding và point-in-time signal.
- Thiết kế strategy mới có `NO_TRADE` và test đầy đủ.

## File cần đọc

```text
src/trading_agent/strategies/canonical/descriptor.py
src/trading_agent/strategies/canonical/registry.py
src/trading_agent/strategies/canonical/features.py
src/trading_agent/strategies/canonical/adapter.py
src/trading_agent/strategies/canonical/bridge.py
src/trading_agent/strategies/canonical/abstain.py
src/trading_agent/strategies/canonical/candidates.py
tests/strategies/test_canonical_contract.py
tests/strategies/test_canonical_wave_c.py
tests/strategies/test_s1_exit_gate.py
```

## 1. Bốn lớp trách nhiệm

| Thành phần | Trách nhiệm |
| --- | --- |
| `StrategyDescriptor` | Identity, features, warmup, params và capability metadata |
| Registry | Allowlist và integrity giữa ID, descriptor, adapter |
| Adapter | Chuyển observation/window sang forecast canonical |
| Runtime bridge | Kết nối canonical forecast với authority/runtime contract |

Không nhét promotion hoặc portfolio allocation vào strategy implementation.

## 2. Signal contract

Strategy nên trả action/exposure có ba trạng thái logic:

```text
BUY / positive target
SELL / negative hoặc reduction target
NO_TRADE / abstain
```

`NO_TRADE` không phải lỗi. Nó là decision hợp lệ khi:

- thiếu warmup/features;
- data untrusted;
- uncertainty quá cao;
- market/risk condition không phù hợp;
- strategy không có edge theo policy.

## 3. Lab có hướng dẫn — Chạy S1 exit gate

```bash
.venv/bin/python -m pytest -q \
  tests/strategies/test_canonical_contract.py \
  tests/strategies/test_canonical_wave_c.py \
  tests/strategies/test_s1_exit_gate.py
```

Chọn `rsi` hoặc `bbands` và trace:

```text
registry ID
→ descriptor
→ adapter factory
→ required feature
→ warmup
→ canonical action
→ bridge signal
```

## 4. Audit parameter binding

Một params hash đúng chưa chứng minh params được dùng. Bạn phải tìm:

1. CLI/spec nhận params ở đâu?
2. Adapter/strategy instance nhận params ở đâu?
3. Indicator/signal đọc field nào?
4. Artifact ghi canonical params/hash thế nào?
5. Test nào chứng minh đổi params làm đổi computation?

Viết trace cho một strategy. Nếu chỉ tìm được identity change nhưng không có
behavior test, ghi `INSUFFICIENT EVIDENCE`.

## 5. Bài tập thiết kế strategy mới

Thiết kế, chưa cần merge, một `breakout_n` strategy:

- input: closed OHLCV window;
- params: `lookback`, `buffer_pct`;
- BUY nếu close vượt previous high + buffer;
- SELL/reduce nếu close dưới previous low - buffer;
- `NO_TRADE` khi thiếu warmup hoặc range bằng zero;
- deterministic và không giữ global mutable state.

Deliverable:

- descriptor draft;
- parameter validation;
- pseudocode adapter;
- 6 test cases;
- two failure cases;
- reason codes.

### Sáu test bắt buộc

1. Descriptor/content identity ổn định.
2. Duplicate registry ID bị reject.
3. Thiếu warmup → `NO_TRADE`.
4. Không dùng decision bar tương lai.
5. Đổi `lookback` tác động output ở fixture phù hợp.
6. Cùng input/params cho cùng output.

## 6. Bài tập state

Đọc `state.py` và trả lời:

- state key bind strategy/pair/timeframe thế nào;
- replay có thể deterministic không;
- thay strategy version có được dùng state cũ không;
- event ledger hỗ trợ audit gì.

## 7. Lỗi thường gặp

- Descriptor ghi params nhưng adapter dùng hard-coded defaults.
- Dùng current incomplete bar.
- Không có abstain path.
- Registry tự import/instantiate strategy không allowlist.
- State chia sẻ giữa pair hoặc run.
- Parity test chỉ so tổng return, không so từng decision.

## Self-check

1. Descriptor khác implementation thế nào?
2. Vì sao registry là security boundary?
3. `NO_TRADE` khác signal `0` mơ hồ thế nào?
4. Test nào chứng minh params binding tốt nhất?
5. Vì sao parity nên so từng bar?

## Exit gate

- [ ] Canonical/S1 tests đạt.
- [ ] Trace hoàn chỉnh một strategy hiện có.
- [ ] Audit params binding có bằng chứng.
- [ ] Hoàn thành thiết kế `breakout_n` và sáu test.

Tiếp theo: [Bài 04 — Backtest và report](04_BACKTEST_REPORT.md).

