# Từ nghiên cứu đến vận hành strategy

> Trạng thái: **HIỆN HÀNH**, các gate chưa đóng được đánh dấu **MỤC TIÊU**
>
> Bản đối chiếu: [Research-to-Production Guide](../guides/RESEARCH_TO_PRODUCTION.md)

Mục tiêu của quy trình không phải tìm strategy có Sharpe cao nhất. Mục tiêu là
tạo ra một quyết định `PROMOTE`, `DO NOT PROMOTE` hoặc `INSUFFICIENT EVIDENCE`
có thể tái hiện và kiểm toán.

## Bước 1 — Khóa baseline

Trước khi so sánh strategy, chứng minh engine và dữ liệu có tính tái hiện.

Smoke một pair bằng local data và state cô lập:

```bash
.venv/bin/python scripts/full_system_backtest.py \
  --fresh --symbol BTC/USDT --timeframe 1h --tail-bars 2000 \
  --allow-new-exposure \
  --state-dir data/backtests/baseline_a/state \
  --report-path data/backtests/baseline_a/report.json \
  --run-id baseline_a
```

Lệnh này ghi state và report local, không cấp quyền mainnet. Nó chỉ chứng minh
luồng single-pair được kết nối.

Golden replay multi-pair là tác vụ dài; phải chạy qua controller:

```bash
python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 14400 --heartbeat 30 \
  --result-file data/backtests/multi_pair_replay_a.control.json \
  -- .venv/bin/python scripts/multi_pair_1h_backtest.py
```

> Cảnh báo: `multi_pair_1h_backtest.py` không có chế độ help-only. Gọi script,
> kể cả với `--help`, sẽ bắt đầu batch.

Chạy hai lần để có hai run directory khác nhau, sau đó:

```bash
.venv/bin/python scripts/verify_golden_replay.py \
  --run-a data/backtests/multi_pair_1h/<RUN_A> \
  --run-b data/backtests/multi_pair_1h/<RUN_B>
```

Chỉ đạt khi decision, ledger và metrics ổn định trong tolerance đã khóa.

## Bước 2 — Đăng ký canonical strategy

Mỗi candidate cần:

- `StrategyDescriptor` hợp lệ;
- `strategy_id` duy nhất;
- parameter schema và default rõ ràng;
- feature requirements và warmup;
- adapter/implementation đã allowlist;
- behavior `NO_TRADE` khi không đủ điều kiện.

Không đủ history, feature lỗi hoặc unknown strategy phải fail closed. Không được
thay thế bằng một strategy khác để “giữ hệ thống chạy”.

## Bước 3 — Chạy tournament smoke

Xem trước matrix, chưa ghi kết quả:

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies enhanced_ma,rsi \
  --symbols BTC/USDT,ETH/USDT \
  --scenarios 1x \
  --tail-bars 2000 \
  --out data/backtests/tournament_smoke \
  --dry-run
```

Kỳ vọng: `2 strategy × 2 symbol × 1 scenario = 4 cell`.

Bỏ `--dry-run` để chạy. Kiểm tra:

- tổng `COMPLETED + FAILED` bằng đúng số cell dự kiến;
- không có cell biến mất vì exception;
- report identity khớp descriptor và cell;
- params đã ghi thực sự điều khiển signal;
- mỗi cell có state directory riêng;
- execution health không bị thay bằng số 0 giả.

## Bước 4 — Khóa full matrix

Trước khi chạy full, ghi bất biến:

| Thành phần | Phải khóa |
| --- | --- |
| Universe | Pair, timeframe, thời gian bắt đầu/kết thúc |
| Data | Manifest/hash, quality rule, gap policy |
| Candidate | Registry version, strategy IDs, incumbent |
| Params | Search space, constraint, seed |
| Costs | Fee, spread, slippage, impact và stress scenarios |
| Faults | Stale/gap, reject, partial fill, cancel race, protection outage |
| Statistics | Fold policy, metric, minimum sample, multiple-testing rule |
| Output | Run ID, output root, schema version |

Không được thay scoring rule hoặc candidate list sau khi đã nhìn holdout.

## Bước 5 — Statistical selection (**MỤC TIÊU**)

Tournament hoàn tất chưa đủ để chọn winner. Selection cần:

- nested WFO hoặc tách train/validation/test tương đương;
- minimum trades và effective sample size;
- uncertainty interval;
- multiple-testing correction;
- parameter neighborhood stability;
- robustness qua cost/fault scenarios;
- so sánh incumbent và `no winner`.

Nếu không candidate nào vượt hard gate, output đúng là `NO_SELECTION`, không phải
candidate ít tệ nhất.

## Bước 6 — Policy và promotion (**MỤC TIÊU**)

`SelectionPolicyArtifact` trả lời strategy nào được hỗ trợ bởi evidence.
`PromotionRecord` trả lời environment nào được phép chạy artifact đó. Hai quyết
định phải độc lập.

Policy/promotion cần bind:

- data, code, config và evaluation identity;
- creator/reviewer;
- environment và promotion stage;
- expiry, revoke và rollback;
- incumbent và abstain behavior;
- risk budget tối đa.

## Bước 7 — Shadow → paper → testnet → canary

```text
Research evidence
  → Shadow: chỉ tạo decision
  → Internal paper: mô phỏng order/fill
  → Broker paper/testnet: kiểm tra protocol và reconciliation
  → Canary: phạm vi/vốn giới hạn
  → Production: chỉ sau approval hiện hành
```

Mỗi stage phải đo decision parity, reject, partial fill, fee/slippage thực tế,
protective coverage, reconciliation và reality gap.

## Bước 8 — Quyết định cuối

Một review hợp lệ phải trả lời:

```text
Decision: PROMOTE | DO NOT PROMOTE | INSUFFICIENT EVIDENCE
Environment:
Artifact/policy identity:
Evidence window:
Hard gates:
Stress/fault outcome:
Known limitations:
Rollback target:
Reviewer:
```

Hoàn thành development phase không được dùng thay cho soak và operator approval.

