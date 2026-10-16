# Calibration v2 — contract và quy trình bàn giao

## Trạng thái

Current update2026-10-16: combined source04ba6a07… at dirty master9b69462 has
134regression PASS, AC01–AC15:15PASS, Ruff PASS, with source/config/tests and
348protected files stable. Authority: `data/readiness_closure_20261016_01/verified.json`.
The existing subset support binds expected_strategies into the plan and uses
the same strategy-name validator in producer/verifier. A proposed ma_vol_target
subset has2cells, not a completed five-family study.
Read-only preflight11checks pass at
`data/readiness_preflight_20261016_01/preflight.json`, but status is
BLOCKED_REVIEW_INTEGRATION. Independent review and clean integration are still
required BEFORE freeze (workflow section5). The agent must not synthesize
approved_by or treat generic continuation as approval for an unseen fingerprint.
No new packet frozen/measurement executed. Details in current tracker.
Selected implementation is now local commit2fc02ac on
codex/readiness-integration-20261016, same Python tree bytes. Separate automated
code review found no blockers; that is not human sign-off or permission to run.
An unrelated untracked campaign script keeps source capture dirty, so clean
source/freeze prerequisites remain pending. See post-commit integration evidence
in data/readiness_integration_20261016_01; do not use the old master SHA as current.

Historical integration snapshot2026-10-14 (superseded by update above):

Protocol producer/worker/verifier hiện thống nhất `protocol_version: "2.0"`.
Local snapshot source32bfdb61…: 80scoped tests PASS và AC01–AC15:15PASS được
đối chiếu raw SHA/identity tại `data/calibration_v2_recheck_20261014/review.json`.
Đang chuẩn bị tích hợp lên nhánh `codex/calibration-integration-20261014`;
cần final verification trên commit tích hợp, không lấy evidence pre-commit làm
approval. Không independent sign-off, market calibration, qualification hoặc live.
ADX25 đã chốt; các ghi chú drift và431tests dưới đây là historical snapshots.

Preset decision2026-10-14: **KEEP_ADX25**, chỉ cost/plumbing/preset validation,
không execution approval. Kiểm tra documentation phát hiện actual source0edcf435…
khác source04141… của snapshot431PASS. Vì vậy revision hiện tại cần review và
kiểm chứng lại; không gán kết quả test cũ cho source mới. Xem
`data/calibration_scope_decision_20261014/identity_drift.json` và tracker hiện hành.

**Historical LOCAL_VERIFIED_REVIEW_PENDING** (2026-10-14): worker-authority hardening đã
PASS431tests/22scoped modules, AC01–AC15:15PASS, Ruff/diff và identity/history guards
PASS. Focused56tests không cộng vào431. Không full repo suite, external independent
sign-off hoặc clean integration. 414tests/AC15 ngày13/10 là historical.
Không dùng trạng thái của packet v1 làm chứng cứ cho v2. Calibration không cấp
quyền pilot, qualification, mở holdout, publish policy hoặc live trading.

Evidence cho snapshot source04141… và verdict lúc đó được ghi tại
`data/calibration_review_20261014/final_verification/verified.json`.
Nếu file này chưa tồn tại hoặc không PASS, không tuyên bố local verification hoàn tất.
Tracker: [CAMPAIGN_STATUS](CAMPAIGN_STATUS.md).
Handoff: [CAMPAIGN_EVIDENCE_HANDOFF](operations/CAMPAIGN_EVIDENCE_HANDOFF.md).

## 1. Mục đích và những điều không chứng minh

Calibration đo chi phí và kiểm tra tính đúng đắn của các measurement validation.
Đây là preset evaluation, không phải tối ưu tham số, full nested-WFO hay chọn
strategy tốt nhất cho live. Không có train/outer-OOS/holdout trong runner này;
phần trước validation chỉ là warmup. Qualification tiếp tục dùng pipeline WFO
hiện hữu với đầy đủ gates, sensitivity và accounting.

Chi phí của runner này không bao gồm train-window simulation, fitting, outer
OOS, toàn bộ grid hay sensitivity của full WFO. Không ngoại suy p95/ETA/full-pilot
budget từ hai synthetic cells hoặc chỉ cộng thời gian của mười preset cells.
Lịch qualification cần budget riêng cho đúng fold geometry và runtime thực tế.

- Market scope cố định: BTC/USDT 4h; `rsi`, `bbands`, `enhanced_ma`,
  `ma_adx`, `ma_vol_target`; mỗi strategy một canonical default × hai windows.
- Early: [2024-01-01, 2024-07-01). Late: [2025-07-01, 2026-01-01).
  Bar indices lấy theo timestamps thực, không suy bằng số giờ từ epoch.
- Cost: 1x. Workers: 1. Retries: 0.
- Budget: 180s wall / 120s CPU mỗi cell; 1800s wall / 1200s CPU tổng;
  RSS group 2 GiB. Không tự tăng budget khi fail.
- Eligibility validation: ≥30 closed trades và Sharpe >0; tie-break canonical.
  Không eligible ⇒ ABSTAIN và không tạo selection freeze.
- Failed/missing/invalid measurement ⇒ INCOMPLETE/REJECTED, không giả thành ABSTAIN.

Canonical defaults lấy qua `validate_params(strategy, None)`, bao gồm các giá trị
được normalize; manifest chứa toàn bộ params thực thi. Không sao chép bảng params
cũ (đã sai tên `vol_target`, RSI và default của MA).

## 2. Data, source và trial-history contract

Input phải là một Parquet pre-holdout đã có sẵn. Không tải dữ liệu, không đọc
price holdout để tạo input mới. Preflight đọc cột timestamp trước, kiểm thứ tự,
uniqueness và last timestamp < frozen holdout start; sau đó mới đọc OHLCV của
input đã hợp lệ. Help/import/verifier không gọi default market-data loader.
Verifier có thể đọc metadata timestamp và byte hash của input đã bind.

Boundary JSON do operator/reviewer chuẩn bị, gồm:

```json
{
  "holdout_start_utc": "2026-02-06T16:00:00+00:00",
  "research_manifest_sha256": "<actual SHA256 of data/research_manifest.json>"
}
```

Runner đối chiếu boundary với `holdout_window` của manifest preregistered,
không sửa `data/research_manifest.json` hoặc trạng thái one-shot holdout.

Freeze bind actual source before, input byte SHA/data-content identity,
environment allowlist, config SHA, loaded simulator settings, canonical candidate,
windows, selection contract, budget và protocol fingerprint. Worker capture lại
source/input/config trước measurement; parent kiểm identity after. Drift ⇒ fail.
Một HEAD SHA không chứng minh dirty tree ổn định.

Market evaluations chỉ append vào
`data/wfo/experiments_parallel.sqlite3`; history và search-family
`s3_wfo_parallel_canonical` giữ nguyên. Search-space identity dùng canonical
research grid, dù calibration chỉ đo một preset. Canonical full normalized params
là phần của candidate identity: không giả reuse khi raw-param identity cũ khác.
Registry-derived burden không bị reset bằng DB mới, đổi tên family hoặc evaluator.

Lưu ý scope: defaults ADX25 của enhanced_ma/ma_adx khác parent-grid30. Calibration
không phải literal subset các điểm grid hoặc kết quả qualification/reuse. Operator
đã chốt giữ ADX25 cho cost/validation riêng ngày2026-10-14;
không sửa params sau freeze, không hạ eligibility và không suy best strategy từ
calibration. Proposal metadata-only hiện tại:
`data/calibration_review_20261014/calibration_scope_proposal.json` (chưa approved).

### Kiểm chứng đường nối sang qualification — 2026-10-14

Audit chỉ đọc: `data/calibration_identity_20261014/identity_audit.json`;
`audit_execution.json` rc0. So sánh toàn bộ Cartesian grid hiện hành với defaults
qua `validate_params`, rồi tính `param_hash(params + cost_scenario=1x)` đúng như
WFO. Không gọi simulator, không đọc OHLCV hoặc ghi registry.

| Strategy | Số điểm grid / cost | Default khớp effective params | Default khớp raw params hash |
| --- | ---: | ---: | ---: |
| rsi | 8 | Có, 1 điểm | Có, 1 điểm |
| bbands | 4 | Có, 1 điểm | Có, 1 điểm |
| enhanced_ma | 6 | Không: ADX25 khác30 | Không |
| ma_adx | 9 | Không: ADX25 khác30 | Không |
| ma_vol_target | 4 | Có, 1 điểm | Có, 1 điểm |

Không phát hiện lệch biểu diễn raw/normalized ở các điểm gần default được kiểm;
không kết luận có lỗi int/float trong năm preset này. Tổng31 điểm/cost là mô tả
grid, không phải số trial full WFO hoặc dự toán thời gian. Audit không chọn preset,
không thay policy, không cấp reuse và không đo hiệu quả thị trường.

Ngay cả ba preset khớp hash cũng **không được reuse tự động**: còn phải khớp
source/data/feature/search-space/cost/fold và toàn bộ execution context. Calibration
chỉ warmup trước validation, còn WFO mô phỏng cả train/validation; một hash tham số
khớp không biến hai evaluation thành một. Không alias/migrate/rewrite trial ID cũ
hoặc copy score để tiết kiệm chi phí. Registry burden giữ nguyên qua các giai đoạn.

Quyết định operator ngày2026-10-14: **giữ default ADX25** cho `enhanced_ma` và
`ma_adx`, chỉ đo chi phí/kiểm plumbing và preset validation; không đại diện điểm
grid30. Các default khác, grid nghiên cứu30, eligibility và budget giữ nguyên.
Record append-only: `data/calibration_scope_decision_20261014/operator_scope_decision.json`,
trích nguyên văn yêu cầu human "giữ ADX25". Đây là quyết định preset, **không phải**
execution approval, independent sign-off hoặc quyền qualification/live.
Proposal cũ vẫn DRAFT_NOT_FROZEN_NOT_APPROVED; không sửa/reseal packet cũ.
Nếu về sau operator muốn calibration
đại diện chính xác các preset grid để chuẩn bị qualification, phải duyệt thay đổi
preset policy trước, sửa producer/shared verifier/tests đồng bộ, refreeze/reverify
và xin approval mới. Không đổi default toàn hệ thống hoặc sửa grid nghiên cứu chỉ
để làm cho hai bên khớp. Không cần sửa source cho lựa chọn giữ default25 hiện tại.

Sau calibration được duyệt và review, qualification phải preregister grid/cost/
fold geometry/selection/sensitivity/gates và budget riêng, giữ registry hiện hữu;
chạy đầy đủ validation rồi outer-OOS theo gates hiện có. Không lấy winner từ
10 calibration cells làm best strategy hoặc dùng SELECT calibration để cấp live.

Mỗi cell có intent append-only trước execution, phase UNKNOWN, metric
`inner_validation_attempt`, status STARTED, bookkeeping-only; đây không phải
measurement hoặc score bằng 0. Outcome riêng có phase INNER_VALIDATION.
FAILED outcome giữ status FAILED và metric_available=false; số 0 chỉ là placeholder
accounting, không phải Sharpe đo được. Nếu chết đột ngột, STARTED và spec vẫn còn;
packet thiếu outcome không được nghiệm thu.

## 3. Resource enforcement và failure semantics

Plan mới bắt buộc `worker_authority_contract=owned_pipe_intent_v1`. Worker CLI
nội bộ không được gọi trực tiếp: parent phải truyền one-shot inherited pipe,
bind PID/out/key/protocol/registered intent. Worker kiểm lại plan, intent STARTED,
chưa có outcome, và approval có timezone, trước measurement. Authority scope
được lưu trong worker-result và đối chiếu receipt/verifier. Đây là chống chạy nhầm
hoặc replay qua entrypoint, không phải security boundary trước code tùy ý của
cùng OS user. Approval content hash không chứng minh tác giả là human.

Packet v2 cũ thiếu worker contract/receipts này không được nâng trạng thái hoặc
reseal. Evidence cũ chỉ chứng nhận revision cũ; phải refreeze/reverify khi tích hợp.

Mỗi cell dùng fresh Linux process/session, chạy cùng canonical simulator
`tournament.run_cell` inline trong worker. Timeout/retry bên trong inline runner
không được relabel là đã enforced; budget do parent watchdog sở hữu group áp dụng.

- Lấy mẫu group RSS/CPU khoảng 20ms; CPU gồm utime/stime và cutime/cstime của
  children đã wait/reap, không mất CPU khi child biến khỏi /proc và không cộng
  lặp living children. Vượt wall/CPU/RSS ⇒ TERM rồi KILL group sở hữu.
- Chờ leader được reap và không còn active descendants. Zombie PIDs được ghi
  riêng; zombie không còn thực thi, nhưng không được tuyên bố mọi PID biến mất.
- CPU receipt hợp lệ gồm user + system của SELF + CHILDREN trên fresh worker,
  đối chiếu sampled values. RSS dùng max(sampled group RSS, fresh worker ru_maxrss).
- PID và worker-result identity phải khớp receipt; không lấy getrusage tích lũy
  của parent rồi gán chung cho mười cells.
- RSS là sampled watchdog, không phải hard allocator/cgroup cap; có thể vượt
  ngắn giữa samples và có thể đếm shared pages nhiều lần.
- Aggregate wall đo execution loop; freeze/preflight/final verification là overhead
  riêng. Aggregate CPU là tổng worker CPU; failed-killed worker có sampled receipt
  và không thể được ACCEPTED.
- SIGTERM/KeyboardInterrupt cleanup session sở hữu và cố ghi INCOMPLETE.
  SIGKILL/power loss có thể chỉ để intent/partial output; không suy completion
  từ việc process đã biến mất. Không resume/reseal packet đó.

## 4. Một verifier và terminal seal

`trading_agent.calibration.packet.verify_calibration_packet` (protocol_version
"2.0") được dùng bởi cả producer và `scripts/verify_calibration.py`.
`verify_packet` là wrapper phát hiện version và delegate; không có hai bộ
C1–C10 độc lập.

| Check | Bằng chứng phải khớp |
| --- | --- |
| Protocol | Fixed matrix, defaults, canonical candidate ID, source/data/config/cost/budget |
| C1 | Đúng toàn bộ keys, không thiếu/trùng; outcome COMPLETED |
| C2 | Measurement INNER_VALIDATION; intent UNKNOWN bookkeeping, không OOS |
| C3 | Tất cả context markers recompute được; intent/outcome đúng cell/spec/metric |
| C4 | Recompute selection; SELECT có exact calibration-only freeze, ABSTAIN không có |
| C5 | Contained report path + byte SHA + typed artifact + raw reconstruction, params/cost/window/health |
| C6 | Fresh worker receipts, finite CPU và RSS, watchdog, per-cell/aggregate budgets, cleanup |
| C7 | Không holdout/OOS/promotion/policy artifacts trong toàn cây packet |
| C8 | Execution summary chứa đúng ordered keys |
| C9 | Logical registry snapshot khớp live committed rows; prior history không bị xóa/sửa |
| C10 | Actual identity before/worker-start/after và input bytes không drift |
| Seal | Terminal cuối cùng, exact framed inventory và scoped registry digest |

SQLite được đọc trong transaction `mode=ro`, bao gồm committed WAL, không dùng
`immutable=1` bỏ qua WAL. Seal không phụ thuộc binary DB/WAL checkpointing:
specs, aliases và đầy đủ evaluation metadata/metrics được snapshot logic.
Các append không liên quan về sau không làm hỏng packet; scoped unexpected rows
hoặc sửa một row bất kỳ trong history/scoped snapshot bị reject.

File inventory bao gồm backup/extra/nested files, chỉ loại đúng terminal và DB
sidecars; add/remove/byte edit đều fail. Mtime-only với bytes không đổi vẫn hợp lệ.
Terminal ghi exclusive/durable sau cùng. Không có API reseal. Hash seal không phải
chữ ký xác thực tác giả; independent reviewer vẫn phải kiểm revision tích hợp.

Selection freeze trong calibration là record v2 validation-only, không phải
`InnerSelectionFreeze` được dùng để cấp quyền chạy outer WFO. Không feed trực tiếp
calibration freeze/decision vào live routing.

## 5. Operator workflow (chưa được tự chạy market)

1. Independent reviewer kiểm diff và fresh receipts trên đúng revision; tích hợp
   sạch rồi chạy lại verification nếu source thay đổi.
2. Chuẩn bị boundary JSON và input pre-holdout hiện hữu; kiểm SHA và phạm vi.
3. Freeze vào thư mục mới (controlled execution, fresh result-file):

```bash
.venv/bin/python scripts/run_calibration.py --freeze \
  --out data/<new_packet> --input <existing_pre_holdout.parquet> \
  --boundary <reviewed_boundary.json>
```

4. Operator đọc manifest, matrix/params/identity/budget. Tự tạo approval file
   ngoài packet, tuyệt đối không để agent tự điền approved_by:

```json
{
  "approved_by": "<operator>",
  "approved_at": "<actual ISO UTC>",
  "protocol_fingerprint": "<exact frozen fingerprint>",
  "approval_sha256": "<SHA256 of canonical JSON of the preceding three fields>"
}
```

Canonical JSON: sort_keys=true, separators=(",", ":"), UTF-8, no NaN.
SHA này bind nội dung, không phải digital signature hoặc thay thế consent.

5. Chỉ sau explicit approval, execute một lần:

```bash
.venv/bin/python scripts/run_calibration.py --execute \
  --out data/<new_packet> --approval <operator_approval.json>
.venv/bin/python scripts/verify_calibration.py data/<new_packet>
```

Bọc các lệnh bằng `scripts/qwenpaw_control/controlled_exec.py` theo AGENTS.md,
heartbeat30 và result-file ngoài packet. Không ghi supervisor logs sau terminal
vào packet. Không chạy lại ở cùng destination.

6. Nếu REJECTED/INCOMPLETE: giữ nguyên packet, điều tra raw reports/receipts;
   sửa engineering rồi refreeze revision và xin approval mới khi cần.
   Nếu ACCEPTED_CALIBRATION: reviewer kiểm raw evidence, sau đó mới đề xuất budget
   và protocol qualification. Không chọn trước ma_vol_target hay Enhanced MA
   chỉ vì chạy nhanh hoặc vì tracker cũ ghi VERIFIED.

## 6. Synthetic verification và handoff bắt buộc

`--synthetic-demo --out <fresh_path>` tự sinh 800 bars seed7, RSI ×2windows,
registry TEST riêng; chạy simulator thật qua cùng worker/supervisor/verifier.
Chỉ SYNTHETIC_TEST_ONLY. Không thay thế 10 market cells hoặc alpha proof.

Regression phải gồm positive real-engine roundtrip, missing/invalid measurement,
nonfirst-row/metric/metadata tampering, raw SHA/path, fake freeze, duplicate matrix,
input/config/source drift, finite CPU/RSS, wall/CPU/RSS watchdog và descendants,
missing terminal, added/removed/edited evidence, mtime-only, read-only verification,
legacy rejection và resume/reseal rejection. Chạy AC01–AC15 theo AGENTS.md.

Handoff phải có source/config/test identity before-after, fresh logs/JUnit/AC raw
hashes, historical packet +operational registry guards, measured resources,
verdict và danh sách việc chưa được cấp quyền. Chỉ ghi
LOCAL_VERIFIED_REVIEW_PENDING khi fresh verification PASS; không ghi final
acceptance, strategy qualified hoặc live-ready.
