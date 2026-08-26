# Runbook kiểm tra luồng chính

> Trạng thái: **HIỆN HÀNH** · Phạm vi: local research/paper validation
>
> Bản đối chiếu: [Main-flow Validation Runbook](../operations/MAIN_FLOW_VALIDATION.md)

Runbook này kiểm tra luồng research và execution quan trọng mà không biến smoke
test thành hành động production.

## Phạm vi an toàn

- Các lệnh dùng local data và output cô lập, trừ khi ghi rõ khác.
- Không lệnh nào cấp quyền mainnet.
- Chạy từ repo root bằng `.venv/bin/python`.
- Tác vụ quan trọng/dài phải qua `controlled_exec.py`.
- Không dùng live state directory làm output backtest.
- Dừng khi thiếu data, sai identity, schema lỗi hoặc thiếu cell không giải thích được.

## Thang kiểm tra

| Mức | Phạm vi | Ý nghĩa |
| --- | --- | --- |
| L0 | Import, CLI, schema và contract tests | Tooling gọi được và contract cơ bản đạt |
| L1 | Một strategy/một pair | Luồng local chính được nối |
| L2 | Matrix nhỏ nhiều strategy/pair/scenario | Isolation và artifact accounting hoạt động |
| L3 | Full matrix đã khóa | Candidate research evidence |
| L4 | Replay lặp lại + fault/stress | Determinism và failure behavior được chứng minh |
| L5 | Shadow/paper/testnet/canary soak | Operational evidence; cần approval riêng |

Đạt L2 không có nghĩa đạt L5.

## L0 — Kiểm tra môi trường và contract

```bash
.venv/bin/python scripts/run_strategy_tournament.py --help
.venv/bin/python scripts/full_system_backtest.py --help
.venv/bin/python scripts/verify_golden_replay.py --help
```

Không gọi `multi_pair_1h_backtest.py --help` vì script đó bắt đầu chạy batch.

Targeted tests:

```bash
.venv/bin/python -m pytest \
  tests/test_backtest_report_v2.py \
  tests/strategies/test_s1_exit_gate.py \
  tests/backtest/test_tournament.py
```

Đạt khi zero failure và không import nhầm Python environment.

## L1 — Single-cell smoke

Preview, không ghi report:

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies rsi --symbols BTC/USDT --scenarios 1x \
  --tail-bars 2000 --out data/backtests/validation_l1 --dry-run
```

Bỏ `--dry-run` để chạy. Kiểm tra:

- đúng một cell được account;
- `COMPLETED` có report/metrics hoặc `FAILED` có reason;
- strategy, symbol, timeframe, params và scenario khớp;
- report pass schema validation;
- không có external broker credential được sử dụng.

## L2 — Matrix nhỏ

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies enhanced_ma,rsi,bbands \
  --symbols BTC/USDT,ETH/USDT \
  --scenarios 1x,slip_stress \
  --tail-bars 3000 \
  --out data/backtests/validation_l2
```

Vì đây có thể là tác vụ dài, khi chạy thực tế hãy bọc lệnh trên bằng:

```bash
python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 3600 --heartbeat 30 \
  --result-file data/backtests/validation_l2.control.json \
  -- .venv/bin/python scripts/run_strategy_tournament.py \
  --strategies enhanced_ma,rsi,bbands \
  --symbols BTC/USDT,ETH/USDT \
  --scenarios 1x,slip_stress \
  --tail-bars 3000 \
  --out data/backtests/validation_l2
```

Kỳ vọng `3 × 2 × 2 = 12` cell. Trong `tournament_index.json`:

```text
COMPLETED + FAILED = 12
MISSING = 0
```

## L3 — Full locked matrix

Trước khi chạy, ghi lại exact command và khóa:

- commit/release;
- data manifest và window;
- strategies, symbols, timeframe;
- params/search space;
- cost/fault scenarios;
- output root;
- statistical policy.

Pass condition không phải “có strategy Sharpe tốt”. Pass condition là đủ cell,
identity ổn định, report hợp lệ, sample đủ, stress visible và không leakage.

## L4 — Replay và failure behavior

Chạy cùng locked matrix hai lần vào hai output root. So sánh:

- decisions;
- order/fill ledger;
- headline metrics;
- artifact/manifest identity;
- completed/failed inventory.

Fault suite cần bao phủ khi implementation hỗ trợ:

- stale/gapped market data;
- partial fill;
- rejection burst;
- cancel/fill race;
- protective-order outage;
- abnormal cost/impact.

Đạt khi lỗi được chứa, hiển thị và không tạo unauthorized exposure.

## L5 — Operational evidence

Theo [Live Trading Runbook](../LIVE_TRADING_RUNBOOK.md) và
[Operational Drills](../OPERATIONAL_DRILLS.md). Bắt buộc có environment approval,
soak duration/sample, reconciliation evidence và rollback readiness.

Backtest local không đóng L5.

## Mẫu biên bản kết quả

```text
Release/commit:
Environment:
Command/config identity:
Data manifest:
Output root:
Expected cells:
Completed / Failed / Missing:
Schema validation:
Determinism result:
Warnings/limitations:
Reviewer:
Decision: PASS | FAIL | CONDITIONAL
```

## Chính sách khi lỗi

| Lỗi | Quyết định |
| --- | --- |
| Missing cell | `FAIL` |
| Unknown strategy hoặc artifact mismatch | `FAIL` |
| Missing/invalid data manifest | `FAIL` |
| Metric unavailable | Không ép về 0; fail gate hoặc ghi unavailable |
| Một winner đẹp nhưng matrix thiếu | Không promote |
| Test chỉ pass riêng lẻ | Điều tra shared state; chưa được waive là flaky |
