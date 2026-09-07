# Kế hoạch củng cố hệ thống và bàn giao cho agent

> Status: **TARGET** · Owner: maintainer / agent điều phối · Verified: **2026-09-07**
> Phạm vi: tính đúng của evidence → policy → adaptive execution → shared capital → operational gates.
> Mainnet: **NO-GO**. Đây là kế hoạch thực hiện, không phải chứng nhận các hạng mục đã hoàn thành.

## 1. Bắt đầu ở đâu

Agent nhận việc phải đọc `AGENTS.md` áp dụng trong workspace, tài liệu này, [tiến độ](TIEN_DO_VA_LO_TRINH_PHASE.md), [luồng cốt lõi](../CORE_SYSTEM.md), và contract liên quan đến ticket. Đọc code/test hiện tại trước khi sửa; không suy luận hoàn thành từ tên commit hoặc báo cáo cũ.

Mục tiêu là **một đường tạo và xác minh evidence đáng tin cậy**, không thêm framework hoặc thêm strategy để làm đẹp kết quả. Tái sử dụng canonical engine, registry, lifecycle và gate hiện có. Nếu chưa có strategy đủ điều kiện, đầu ra đúng là `NO_TRADE`, không hạ ngưỡng để có winner.

Tài liệu này quản lý ticket, dependency và bàn giao của đợt củng cố. Bảng tiến độ chỉ tổng hợp S/P-phase; contract trong code và bằng chứng bất biến quyết định hành vi. Không tạo thêm báo cáo tiến độ song song.

## 2. Mốc xuất phát và những điều chưa được phép kết luận

Audit ngày 07/09/2026 dựa trên HEAD `f8dda19c8324ae07d0b89e4c5edd094fd72eba78` cùng thay đổi local trong `src/trading_agent/backtest/nested_wfo.py` và file chưa tracked `scripts/run_s6_campaign.py`. Không reset, ghi đè hoặc tự commit các thay đổi này. Agent mới phải kiểm tra lại vì workspace có thể đã thay đổi.

| Phát hiện đã xác minh | Nguồn / tác động |
| --- | --- |
| Grid Enhanced MA dùng `fast/slow/signal_ma`, constructor đọc `fast_period/slow_period` | [runner](../../scripts/run_wfo_parallel.py), [strategy](../../src/trading_agent/strategies/enhanced_ma.py): hai cấu hình khác nhau đều khởi tạo MA 20/80; phải sửa trước khi tối ưu lại |
| Bộ tổng hợp dùng PBO/DSR/CI proxy và ngưỡng khác canonical | [aggregator](../../scripts/aggregate_wfo_cells.py), [nested WFO](../../src/trading_agent/backtest/nested_wfo.py): không dùng `FINAL_PASS` của aggregator làm qualification chuẩn |
| Provenance được suy từ checkout lúc tổng hợp | Commit sạch sau khi chạy không chứng minh nguồn gốc từng cell |
| S5 `real` vẫn tạo policy/hash/score mẫu và trạng thái tài khoản đơn giản hóa | [campaign S5](../../scripts/run_s5_adaptive_campaign.py): chưa phải bằng chứng adaptive trading đầu-cuối |
| Approval không chữ ký vẫn được `ApprovalChain.evaluate(PRODUCTION)` chấp nhận khi đủ vai trò | [approval](../../src/trading_agent/authority/approval.py): kiểm tra bằng DB tạm; chưa thấy class nối vào promotion chính, không suy ra live bypass |
| S6 hợp nhất tên stress giữa các pair, lấy số liệu pair đầu làm mẫu | [nested WFO](../../src/trading_agent/backtest/nested_wfo.py), [campaign S6](../../scripts/run_s6_campaign.py): có thể che thiếu coverage từng pair |

Bằng chứng kiểm tra tại mốc audit: 24 test approval/permission/tournament-control đạt; mypy đạt 28 file được cấu hình; Ruff còn 12 lỗi trong script S6 local. Đây không phải full regression. Các số này là lịch sử, agent không được sao chép thành kết quả mới.

Artifact [Enhanced MA parallel](../../data/backtests/wfo_parallel_summary_enhanced_ma/wfo_decision.json) cần tái thẩm định, không xóa hoặc sửa thành tích lịch sử. Artifact [RSI BTC canonical](../../data/backtests/wfo_real_20260906/wfo_summary.json) có `NO_TRADE`; không suy rộng thành mọi strategy đều thất bại. [S5 synthetic](../../data/backtests/s5_adaptive_campaign/s5_campaign_summary.json) chỉ là diagnostic. Đường dẫn ở đây là locator; ticket evidence phải ghi thêm content hash vì đường dẫn có thể bị ghi đè.

## 3. Thứ tự và nguyên tắc phối hợp

```text
R00 khóa baseline và phân công
 ├─ R01 schema tham số ─ R02 một luồng kiểm định ─ R03 evidence binding ─ R04 chạy lại S3
 └─ R08 chữ ký/approval: phát triển và test riêng có thể song song
R04 ─ R05 policy thật và replay thời gian ─ R06 adaptive execution ─ R07 shared capital
R03 + R05 + R08 ─ tích hợp chốt promotion
R06 + R07 + chốt promotion ─ R09 nghiệm thu toàn luồng và cập nhật trạng thái
```

Không triển khai R06/R07 từ các policy giả rồi gọi đó là bằng chứng thị trường. Có thể chuẩn bị interface và test synthetic trước khi R04 xong, nhưng chưa được đóng evidence gate. R08 chỉ được thực hiện song song khi không chạm file người khác đang giữ. Sửa lõi `nested_wfo.py` cho R02/R03/R07 phải tuần tự hoặc được một integrator duy nhất tích hợp.

### Quy tắc nhận việc

1. Điều phối viên giao ticket, scope file và tiêu chí nghiệm thu. Không tự nhận “sửa hết hệ thống”.
2. Ghi owner cụ thể vào bảng ticket trước khi sửa. Mỗi file chỉ có một writer; owner ở bảng là vai trò gợi ý, chưa phải agent đã được khởi chạy.
3. Nếu dùng workspace chung, không dùng git thao tác làm đổi checkout/reset ảnh hưởng người khác. Nếu dùng worktree riêng, khóa base commit và tích hợp tuần tự; không tự tạo task/agent khi chưa được giao quyền.
4. Không thay public schema âm thầm. Đề xuất version, producer, consumer, migration và negative tests trước khi sửa nhiều module.
5. Không chạy hai campaign dùng cùng output directory, trial DB, holdout registry hoặc mutable cache. Dùng run ID riêng; holdout chỉ có một owner.
6. Không tự gửi lệnh giao dịch, deploy, publish, gọi dịch vụ ngoài máy, hoặc lấy credentials. Test dùng local data/simulator; testnet/live cần phê duyệt riêng.
7. Hạng mục vượt scope: báo dependency và đề xuất ticket, không sửa tiện tay. Không xóa artifacts/tài liệu cũ trong đợt này.

## 4. Bảng công việc điều phối

Trạng thái ticket: `TODO → IN_PROGRESS → REVIEW → DONE`; `BLOCKED` phải ghi lý do và điều kiện mở lại. `DONE` đòi hỏi reviewer và evidence, không chỉ commit. Trạng thái nghiên cứu/vận hành vẫn dùng vocabulary trong [Documentation Standard](../DOCUMENTATION_STANDARD.md).

| ID | Gói việc / owner đề xuất | Phụ thuộc | Trạng thái | Owner thực tế / evidence |
| --- | --- | --- | --- | --- |
| R00 | Baseline, file ownership / Integrator | Không | TODO | Chưa giao |
| R01 | Tham số và trial identity / Strategy | R00 | **IN_PROGRESS** | **Signal/equity parity VERIFIED** (see R01_PARITY_REPORT.md); remaining: param schema validation, trial identity deduplication, grid enumeration tests |
| R02 | Canonical WFO và thống kê / Research | R01 | TODO | Chưa giao |
| R03 | Provenance và completeness / Evidence | R02 | TODO | Chưa giao |
| R04 | Campaign S3 bất biến / Research runner | R03 | TODO | Chưa giao |
| R05 | Policy và replay clock / Authority | R04 | TODO | Chưa giao |
| R06 | Adaptive execution đầu-cuối / Execution | R05 | TODO | Chưa giao |
| R07 | Shared-capital và stress coverage / Portfolio | R06 | TODO | Chưa giao |
| R08 | Approval có chữ ký / Security | R00; tích hợp cần R03 + R05 | TODO | Chưa giao |
| R09 | Regression, evidence và bàn giao / Integrator + reviewer | R07 + R08 | TODO | Chưa giao |

### R00 — Khóa baseline, không khóa nhầm kết luận

- Ghi HEAD, dirty diff, môi trường, test baseline, phạm vi dữ liệu local và owner các file dùng chung. Xin chủ sở hữu xác nhận trước khi đưa thay đổi S6 local vào baseline.
- Đánh dấu các báo cáo parallel/S5 là chưa đủ điều kiện promotion tại chỉ mục trạng thái; giữ nguyên artifact gốc.
- Chốt scope campaign dự kiến: pair, 1h, strategy allowlist, grids, cost, folds, holdout, ngân sách thời gian và tài nguyên. Không mặc định dữ liệu đủ 10 pair.
- **Nghiệm thu:** baseline có identity; backlog đã có người nhận; các kết luận cũ được phân biệt với bằng chứng có thể promote.

### R01 — Schema tham số và danh tính trial

**TRẠNG THÁI: IN_PROGRESS — Signal/equity parity VERIFIED ✅**

**Đã hoàn thành (Signal/Equity Parity):**
- `scripts/r01_signal_parity.py`: 1000/1000 bars identical signals giữa canonical registry adapter và `build_legacy_candidate()`
- `scripts/r01_equity_parity.py`: FullSystemSimulator với canonical signals injected vs legacy strategy → **0.00% diff** trên mọi metric
- Xác nhận `LegacyDataFrameAdapter` là zero-overhead wrapper không thay đổi hành vi

**Còn lại (Param Schema & Trial Identity):**
- **File chính:** `scripts/run_wfo_parallel.py`, `src/trading_agent/strategies/canonical/candidates.py`, strategy constructors; rà các runner/grid khác dùng cùng chiến lược.
- Validate khóa, kiểu, miền giá trị, quan hệ fast/slow. Unknown key phải báo lỗi; alias nếu giữ phải có migration rõ ràng, không âm thầm fallback.
- Artifact ghi requested params, normalized/effective params, schema version và hash. Effective identity lấy từ cấu hình thực thi, không chỉ chuỗi JSON người dùng gửi.
- Deduplicate cấu hình thực tế trong cùng evaluation context; phân biệt tham số lặp với lần đánh giá hợp lệ khác fold/cost. Không tự đặt effective trial count bằng số cell.
- **Test bắt buộc:** typo bị từ chối; default có chủ đích; alias round-trip; hai cấu hình MA hợp lệ tạo trạng thái constructor khác nhau; cùng effective params có cùng identity; grid enumerate đúng. Không đòi mọi cấu hình phải tạo PnL khác nhau.
- **Nghiệm thu:** chạy smoke local thấy grid thật sự điều khiển strategy; không còn test hoặc báo cáo coi các alias trùng là trial độc lập.

**Evidence:** `R01_PARITY_REPORT.md`, `data/backtests/r01_parity/r01_signal_parity_report.json`, `data/backtests/r01_parity/r01_equity_parity_report.json`

### R02 — Chỉ một thẩm quyền kiểm định S3 — **✅ COMPLETE**

**Đã hoàn thành (Core Architecture + Tests):**
- ✅ `run_nested_wfo()` accepts `cell_runner` callback for parallel execution
- ✅ `_run_parameter_trial()` uses `cell_runner` for inner validation
- ✅ Outer OOS test uses `cell_runner`
- ✅ `run_nested_wfo_portfolio()` passes `cell_runner` through
- ✅ `scripts/run_wfo_parallel.py` rewritten: schedules canonical WFO cells via `ParallelCellRunner`
- ✅ `scripts/aggregate_wfo_cells.py` → `scripts/diagnose_wfo_cells.py` (diagnostic-only, no verdict/promotion)
- ✅ `tests/test_r02_canonical_authority.py` — 13 equivalence tests, 13/13 PASS in 113s

**Test coverage (R02 acceptance criteria):**
- ✅ Serial/parallel cùng input → cùng identity và verdict
- ✅ Đổi outer return không đổi inner-selected params (freeze invariance)
- ✅ Thiếu thống kê không trở thành PASS (fail-closed)
- ✅ Thử nhiều params không dùng lại final holdout
- ✅ Metrics khớp ledger theo cost (4 inner trials cho 2 cost × 2 folds)
- ✅ Negative control thất bại đúng gate (SYNTHETIC not promotable)

**File chính:** `src/trading_agent/backtest/nested_wfo.py`, `scripts/run_wfo_parallel.py`, `scripts/diagnose_wfo_cells.py`, `tests/test_r02_canonical_authority.py`.

**Nghiệm thu:** không còn đường thứ hai tạo qualification yếu hơn; negative control thất bại đúng gate. 13/13 R02 tests pass.

**Evidence:** `tests/test_r02_canonical_authority.py` (13 tests, 113s, mock-based for speed; real WFO pipeline covered by `tests/test_nested_wfo.py` 51/51 and `tests/test_nested_wfo_evidence.py`)

### R03 — Provenance, completeness và resume

- **File chính:** tournament artifact/runner, trial registry và manifest validation hiện có; phối hợp owner R02 trước khi sửa `nested_wfo.py`.
- Producer ghi identity ngay lúc chạy. Consumer kiểm code/data/features/effective params/cost/window/fold/seed và policy version. Phân biệt code tạo evidence với code chỉ đọc báo cáo.
- So manifest dự kiến với cell thực tế: thiếu, failed, duplicated, stale, mixed-study hoặc bị sửa phải hiện rõ và fail-closed ở qualification.
- Resume chỉ dùng cache khi toàn bộ evaluation identity tương thích; không dựa riêng cell ID hoặc tên folder. Tính đủ trial từ registry thật.
- **Test bắt buộc:** tamper hash; trộn hai commit; dirty run rồi tổng hợp từ clean commit; mất cell; retry trùng; thay window/cost/data khi resume. Tất cả trường hợp không hợp lệ không được nâng thành PASS.
- **Nghiệm thu:** evidence có thể truy ngược và replay; re-aggregation không nâng cấp nguồn gốc của dữ liệu cũ.

### R04 — Chạy lại S3 với scope đã khóa

- **Đầu vào:** R01–R03 đã review, commit triển khai được chủ sở hữu chấp nhận, manifest dữ liệu đủ chất lượng, trial DB/output riêng.
- Chạy smoke một pair trước, sau đó scope nhỏ nhiều strategy, cuối cùng matrix đã phê duyệt. Benchmark trước khi ước lượng full run; không chạy full nhiều lần để tìm kết quả đẹp.
- Kiểm tra lịch sử truy cập holdout. Holdout đã dùng để quyết định thiết kế/strategy không còn là untouched; không reset registry để dùng lại. Nếu hết dữ liệu chưa thấy, báo thiếu dữ liệu/wait forward evidence.
- Xuất metrics theo pair/strategy/fold/cost và chuỗi portfolio thích hợp: net return, Sharpe, PF, MDD, turnover, exposure, trade count, CI, DSR/PBO, stress, cell health và lý do abstain.
- **Nghiệm thu:** study bất biến, completeness đạt, report truy được ledger. PASS và NO_TRADE đều là đầu ra nghiên cứu hợp lệ; không có winner thì R05–R07 chỉ được kiểm định abstain hoặc synthetic integration, chưa có bằng chứng trading thực.

### R05 — Policy thật và thời gian replay

- **File chính:** `scripts/run_s5_adaptive_campaign.py`, authority promotion/policy/resolver hiện có. Không dựng hệ policy thứ hai.
- Chế độ real nhận verified bundle từ R04/S4, không tự tạo score, evidence ID, hash hoặc key fixture. Synthetic và real phải được phân loại, tách storage và bị kiểm tra ở consumer.
- Replay dùng event time thống nhất; policy và model chỉ có hiệu lực sau khi dữ liệu dùng để tạo chúng đã khả dụng. Không backdate policy để làm lịch sử hợp lệ; lưu lineage cả lúc fit và lúc được phép dùng.
- **Test bắt buộc:** policy thiếu/expired/not-yet-valid/tampered; synthetic lẫn real; model fit sau thời điểm quyết định; không có policy đạt thì abstain.
- **Nghiệm thu:** mọi routing decision truy được policy và training cutoff hợp lệ tại đúng thời điểm.

### R06 — Adaptive execution đầu-cuối

- **File chính:** campaign S5 và adapter vào canonical execution engine, authority router/runtime. Core engine sửa riêng qua owner.
- Nối router → risk/permission → order → simulator → fills → ledger → positions/cash/equity → reconciliation. Bỏ giả định luôn flat, exposure 0 và equity cố định trong đường real.
- So adaptive với incumbent cố định trên cùng dữ liệu OOS, vốn, cost và execution semantics; tính cả switching cost. Tiêu chí so sánh khóa trước chạy; không bắt hệ thống phải luôn thắng.
- **Test bắt buộc:** switch khi còn vị thế; position ownership; cooldown/restart; stale data; reject/partial fill; spread/slippage/fee; không phát lệnh trùng khi replay event.
- **Nghiệm thu:** equity/exposure suy từ ledger; fixture có fill kiểm tra được PnL và cash. Abstain không lệnh cũng được chấp nhận nếu có lý do; không coi equity phẳng là bằng chứng giao dịch thành công.

### R07 — Shared capital và coverage từng pair

- **File chính:** `scripts/run_s6_campaign.py`, authority portfolio/allocator; phần sensitivity trong `nested_wfo.py` phải xin chuyển ownership.
- Multi-pair dùng một account ledger, không cộng các tài khoản vốn độc lập rồi gọi shared-capital. Kiểm aggregate cap, correlation cluster, cash reservation và attribution.
- Stress coverage là ma trận pair × scenario × status × evidence ID. Không lấy hợp tên stress giữa pair để kết luận đủ; số liệu phải có aggregation rule hoặc giữ riêng từng pair. Holdout status kiểm tất cả thành viên.
- **Test bắt buộc:** hai pair cùng tranh vốn; correlation shock; thiếu stress chỉ một pair; holdout một pair fail; partial fill và restart; attribution khớp tổng tài khoản trong tolerance đã quy định.
- **Nghiệm thu:** không vượt vốn/cap theo contract; đủ coverage hoặc chặn rõ; summary không bỏ trống identity bắt buộc; xử lý Ruff của script local cùng chủ sở hữu.

### R08 — Approval xác thực và tích hợp promotion

- **File chính:** `src/trading_agent/authority/approval.py`, promotion hook/store; đọc [approval schema](APPROVAL_CHAIN_SCHEMA.md) và [production policy](../PRODUCTION_POLICY.md).
- Dùng thư viện chữ ký chuẩn, không tự viết mật mã. Khóa tin cậy phải gắn danh tính và role được cấp, có rotation/revocation; không hardcode private key. Test key chỉ được dùng trong test.
- Payload ký bao gồm artifact identity, stage, decision, actor/role, timestamp/expiry và replay identity. Canonical serialization/version phải thống nhất producer–verifier.
- Từ chối unsigned trong đường thật; không cho cờ test mở đường production. Kiểm future timestamp, expiry, role separation, duplicate/replay và thay đổi artifact.
- **Test bắt buộc:** unsigned, sai key, role không được cấp, sửa payload, stale/future, replay, revoked key; approval hợp lệ cũng phải qua promotion integration chứ không chỉ unit test store.
- **Nghiệm thu:** thiếu/sai approval chặn tại consumer thực; có approval không tự bỏ qua research, risk, release hoặc operational gates.

### R09 — Nghiệm thu và bàn giao

- Chạy regression theo lớp ở mục 6 trên revision cuối; reviewer kiểm negative paths và diff của file dùng chung.
- Evidence index ghi code/data/config/hash, commands, exit code, test counts, skipped tests có lý do, output locator, holdout usage và known limitations.
- Cập nhật bảng ticket và [tiến độ S/P](TIEN_DO_VA_LO_TRINH_PHASE.md). Chỉ nâng mức maturity có bằng chứng; không biến `TESTED` thành `PRODUCTION_VALIDATED`.
- **Nghiệm thu engineering:** R01–R08 có review và evidence theo scope. **Nghiệm thu operational:** riêng biệt, theo runbook shadow/testnet/canary; thời gian soak, approval và release attestation không được thay bằng simulator.

## 5. Contract bàn giao giữa các gói

Các trường dưới đây là yêu cầu ngữ nghĩa mục tiêu, không khẳng định schema hiện tại đã có đủ. Ưu tiên mở rộng schema versioned đang tồn tại; người producer và consumer phải thống nhất trước khi code.

| Ranh giới | Nội dung tối thiểu | Consumer phải từ chối khi |
| --- | --- | --- |
| R01 → R02 | Strategy/schema version; requested/effective params; effective hash; code identity | Unknown params hoặc cấu hình thực không khớp |
| R02 → R03 | Study/fold/window/cost/trial identity; selected params; OOS ledger; gate policy version | Selection dùng tương lai; không đủ ledger/thống kê |
| R03 → R04/R05 | Verified manifest; đủ cell; source hashes; evidence class; verdict + reasons | Tampered/mixed/incomplete; diagnostic bị dùng để promote |
| R05 → R06 | Verified policy; effective interval; training cutoff; candidate identity; approvals cần thiết | Policy/model chưa được phép tồn tại ở replay time |
| R06 → R07 | Event/order/fill identity; owner; account ledger; positions; cash reservation | Double-count; thiếu đối soát; vốn bị dùng trùng |
| R07/R08 → R09 | Coverage từng pair; ledger totals; signature verification; integration gate evidence | Một thành viên thiếu kiểm định hoặc chữ ký không hợp lệ |

## 6. Chiến lược test và chạy campaign

1. **Mỗi patch:** unit/negative tests đúng module, Ruff phần thay đổi; không chạy full suite cho mọi chỉnh sửa nhỏ.
2. **Mỗi ranh giới:** integration producer–consumer, deterministic synthetic fixtures. Synthetic không được promote.
3. **Trước campaign:** static checks, fast regression phù hợp, smoke local, kiểm side effect của test/script. Test không được xóa artifact baseline hoặc dùng chung holdout DB.
4. **Trước bàn giao:** full regression theo test profile của repository và ngân sách đã thống nhất; ghi rõ coverage, skip và phần chưa chạy. Không gọi focused tests là full suite.
5. **Campaign real:** chỉ đọc market data local đã khóa; output mới, timeout và heartbeat. Không chạy thử command có thể consume holdout trước khi R04 đã chốt scope.

Ví dụ kiểm tra local đã dùng ở audit (chạy từ root repo trong WSL). Không gọi broker/dịch vụ ngoài; pytest có thể ghi cache/log local và fixtures tạm. Result file dưới `/tmp` phải thay tên riêng cho mỗi lần chạy; đây là diagnostic, không phải evidence đủ để promote. Kỳ vọng exit code 0 và summary số test. Không cần rollback source; giữ log, không tự xóa evidence.

```bash
python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 300 --heartbeat 30 --result-file /tmp/R00-focused-UNIQUE.json \
  -- .venv/bin/python -m pytest -q \
  tests/authority/test_approval_chain.py \
  tests/authority/test_execution_permission.py \
  tests/backtest/test_tournament_cell_control.py
```

Mọi lệnh quan trọng/lâu phải dùng toolkit theo `AGENTS.md`, luôn có result file. Agent phải kiểm tra CLI `--help` và tác động ghi trước khi bổ sung command campaign cụ thể vào bàn giao; tài liệu này không đoán cờ CLI chưa xác minh.

## 7. Mẫu giao việc và báo cáo trả về

Điều phối viên sao chép phần sau, điền đủ trường rồi mới giao agent:

```text
Ticket: Rxx — <tên>
Đọc trước: AGENTS.md + tài liệu kế hoạch + contract liên quan.
Base revision và dirty files phải bảo toàn: <...>
Owner / reviewer / file được sửa: <...>
Mục tiêu và không làm: <...>
Dependency đã đạt, evidence đầu vào và hash: <...>
Public interface được phép thay đổi / consumer phối hợp: <...>
Acceptance tests và negative cases: <...>
Ngân sách chạy / output directory / DB riêng: <...>
Điểm cần dừng hỏi: vượt scope, xung đột file, dùng holdout, external action.
Đầu ra: diff + test log + evidence index + known limitations.
Không tự commit/deploy/promote hoặc đánh dấu phase complete ngoài phạm vi giao.
```

Báo cáo trả về phải có: ticket/status; revision và file đã sửa; hành vi trước/sau; command thực chạy, exit code và số test; đường dẫn/hash evidence; compatibility/migration; tồn đọng/blocker; việc kế tiếp. Nếu chưa chạy test hoặc chỉ có synthetic, nói rõ. Reviewer tái kiểm negative case trước khi chuyển `REVIEW → DONE`.

## 8. Điều kiện dừng hoặc đổi hướng

- Không có dữ liệu untouched: không reset holdout; báo thiếu evidence và đề xuất forward window.
- Không strategy nào đạt: giữ `NO_TRADE`, điều tra data/cost/hypothesis; nghiên cứu mới phải là study mới có trial budget.
- Metrics không reconcile hoặc provenance sai: dừng qualification downstream, giữ artifact lỗi để truy vết.
- Core interface cần refactor rộng: tách proposal có chi phí/migration; không mở rộng trong ticket nhỏ.
- Thiếu người ký, môi trường testnet hoặc thời gian soak: giữ operational phase pending; không giả lập để đóng.

**Bước triển khai đầu tiên: R00 → R01. Chưa chạy full campaign lại trước khi R01–R03 được nghiệm thu.**
