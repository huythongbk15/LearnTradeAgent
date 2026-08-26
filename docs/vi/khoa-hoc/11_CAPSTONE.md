# Bài 11 — Capstone: Từ strategy hypothesis đến evidence decision

> Mức độ: tổng hợp · Thời lượng: 8–12 giờ · Phạm vi: local research/paper

Capstone yêu cầu bạn tự thực hiện một mini research lifecycle. Mục tiêu là tạo hồ
sơ bằng chứng có thể review, không phải tối đa hóa return.

## Mục tiêu

- Tổng hợp data, strategy, backtest, tournament và authority contracts.
- Tạo một evidence dossier có thể tái hiện.
- Ra quyết định có uncertainty và limitations rõ ràng.
- Thiết kế đường đi tiếp theo mà không tự cấp quyền production.

## Chọn một trong hai hướng

### Hướng A — Audit strategy hiện có

Chọn `enhanced_ma`, `rsi`, `bbands`, `ma_adx` hoặc `ma_vol_target`. Phù hợp nếu bạn
muốn tập trung vào đọc code, artifact và thống kê.

### Hướng B — Thiết kế candidate mới

Dùng `breakout_n` từ bài 03 hoặc strategy đơn giản khác. Chỉ làm trên branch học
tập; không merge vào production. Phù hợp nếu bạn muốn luyện implementation/TDD.

## Phạm vi bắt buộc

- 1 strategy;
- ít nhất 2 pairs;
- timeframe 1h;
- baseline cost + một stress cost;
- ít nhất một failure/fault analysis;
- không live/testnet order;
- output directory riêng dưới `data/backtests/course_capstone/`.

## Phase 1 — Hypothesis và locked plan

Viết trước khi chạy:

```text
Strategy hypothesis:
Market behavior kỳ vọng:
Pairs/timeframe:
Data window/manifest:
Parameters/search space:
Baseline và stress costs:
Benchmark/incumbent:
Hard gates:
Statistical limitations:
Stop conditions:
```

Không sửa hypothesis/hard gates sau khi thấy kết quả mà không tạo version mới và
ghi rõ data đã nhìn.

## Phase 2 — Contract và data review

Deliverables:

1. Strategy review theo mẫu.
2. Point-in-time diagram.
3. Data quality/manifest audit.
4. Danh sách `NO_TRADE` conditions.
5. Test matrix happy/failure paths.

Hướng B cần thêm:

- descriptor draft/implementation;
- registry entry trên branch;
- parameter validation;
- deterministic, warmup, future-leak và params-binding tests.

## Phase 3 — Baseline

Chạy targeted tests và một single-pair smoke. Ghi:

- exact commit/environment;
- exact command;
- state/report path;
- report schema result;
- metrics + costs + warnings;
- điều baseline chưa chứng minh.

Nếu baseline không deterministic hoặc report invalid, dừng capstone và kết luận
`BLOCKED BY BASELINE`.

## Phase 4 — Mini tournament

Dry-run trước:

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies STRATEGY_ID \
  --symbols BTC/USDT,ETH/USDT \
  --scenarios 1x,slip_stress \
  --tail-bars 3000 \
  --out data/backtests/course_capstone \
  --dry-run
```

Kỳ vọng tối thiểu 4 cells. Chạy qua controller sau khi dry-run đúng.

Reconcile:

```text
expected = 4
completed + failed = 4
missing = 0
```

Nếu có missing, matrix fail. Không bỏ cell đó rồi tính trung bình ba cell còn lại.

## Phase 5 — Artifact audit

Với từng cell, lập bảng:

| Cell | Status | Return | DD | PF | Trades | Costs | Health | Warning |
| --- | --- | ---: | ---: | ---: | ---: | ---: | --- | --- |

Audit lineage:

```text
data manifest
→ strategy/descriptor + params
→ report
→ evaluation artifact
→ tournament index
```

Không ghi metric thiếu thành zero.

## Phase 6 — Selection exercise

Áp dụng hard gates đã khóa. Bắt buộc so ba quyết định:

1. `SELECT_CANDIDATE`;
2. `KEEP_INCUMBENT`;
3. `NO_SELECTION`.

Chọn một và giải thích:

- sample/fold limitations;
- cost stress;
- parameter stability;
- execution health;
- multiple-testing/trials;
- uncertainty;
- bằng chứng cần thêm.

Vì capstone chỉ có mini matrix, quyết định production đúng gần như luôn là
`INSUFFICIENT EVIDENCE`. Bạn vẫn có thể kết luận candidate đủ điều kiện cho bước
research tiếp theo.

## Phase 7 — Promotion/runtime threat model

Không promote thật. Thiết kế lineage và xử lý:

- missing artifact;
- environment mismatch;
- expired promotion;
- revoked policy;
- strategy/params mismatch;
- runtime cache stale.

Viết `PromotionRecord` draft và rollback target.

## Phase 8 — Operational plan

Tạo staged plan:

```text
Shadow → Internal paper → Broker paper/testnet → Canary
```

Mỗi stage có:

- entry gate;
- metrics/alerts;
- minimum duration/sample;
- stop trigger;
- reconciliation;
- exit/rollback decision.

Không thêm production/mainnet nếu capability matrix/live readiness chưa cho phép.

## Hồ sơ nộp

```text
01_locked_plan.md
02_strategy_data_contract.md
03_test_results.md
04_baseline_audit.md
05_tournament_inventory.md
06_selection_memo.md
07_promotion_threat_model.md
08_operational_plan.md
artifacts_or_links.md
final_decision.md
```

## Final decision

```text
Decision:
REJECT | CONTINUE_RESEARCH | PAPER_ELIGIBLE_CANDIDATE | INSUFFICIENT_EVIDENCE

Strongest evidence:
Strongest counter-evidence:
Known limitations:
Next experiment:
What would falsify the hypothesis:
```

## Demo review 15 phút

1. 2 phút: hypothesis và locked gates.
2. 3 phút: architecture/strategy/data contract.
3. 3 phút: artifacts và matrix accounting.
4. 3 phút: selection decision/uncertainty.
5. 2 phút: runtime/operations risks.
6. 2 phút: next experiment và falsification.

Dùng [Rubric](RUBRIC.md) để tự chấm trước khi kết thúc khóa học.

## Exit gate

- [ ] Nộp đủ mười thành phần hồ sơ.
- [ ] Matrix reconcile không có missing cell.
- [ ] Final decision tuân hard gates đã khóa.
- [ ] Threat model và staged operational plan đầy đủ.
- [ ] Tự chấm rubric và ghi rõ mọi mục chưa đạt.
