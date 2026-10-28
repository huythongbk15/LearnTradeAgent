# Kế hoạch tinh gọn đường validation

> **Status:** CURRENT — phạm vi extraction hai boundary đã nghiệm thu; phần deferred không thuộc nghiệm thu và tài liệu không cấp quyền chạy.
> **Owner:** integration owner; mỗi task có một agent chịu trách nhiệm.
> **Verified:** 2026-10-23 — source/tests/receipts và review độc lập cho bounded scope; không phải chứng nhận vận hành.
> **Evidence:** engineering baseline — local evidence `data/bnb_validation_incomplete_fix_20261023_01/RESULTS.md`, verification — local evidence `data/bnb_validation_incomplete_fix_20261023_01/VERIFICATION.md`, [campaign status](../CAMPAIGN_STATUS.md).

## 1. Quyết định đề xuất

**Trạng thái mới nhất:** C00–C05 đã đóng cho phạm vi coverage/publication và Linux supervisor. Final handoff — local evidence `data/validation_refactor_final_20261023_01/RESULTS.md` chứa exact revision, test scope, budget và verdict độc lập. Các đề xuất và checkpoint bên dưới được giữ như lịch sử quyết định, không phải backlog để agent tự chạy lại.

**Tái cấu trúc có giới hạn, không viết lại hệ thống.** Vòng đầu chỉ tách reporting/terminal của validation và supervisor khỏi launcher. Tối đa hai module production mới; không thêm runner, scheduler, registry, gate hay tracker chiến dịch.

Mục tiêu sản phẩm vẫn là một vòng đời nhỏ nhưng trọn vẹn: dữ liệu có identity → candidate cố định → đánh giá native → verdict trung thực → chỉ đi tiếp nếu đủ bằng chứng. Refactor giúp đường này dễ kiểm chứng; nó không tạo trading edge và không phải lý do chạy lại candidate đang yếu.

| Lựa chọn | Lợi ích | Rủi ro / chi phí | Quyết định |
| --- | --- | --- | --- |
| Chỉ sửa lỗi, giữ cấu trúc | Ít thay đổi nhất | Launcher tiếp tục ôm nhiều trách nhiệm | Giữ làm phương án dừng nếu extraction không có lợi |
| Tách hai boundary validation | Giảm phạm vi cần hiểu khi sửa terminal/process | Cần khóa compatibility và ownership | **Chọn cho vòng đầu** |
| Viết lại engine / gộp mọi runner / dọn cả repo | Có thể đẹp hơn về kiến trúc | Đổi quá nhiều identity và hành vi, trì hoãn câu hỏi edge | Không chọn |

## 2. Baseline thực tế và giới hạn suy luận

Snapshot đọc ngày 23/10/2026:

| Hạng mục | Quan sát | Ý nghĩa |
| --- | --- | --- |
| `scripts/run_wfo_parallel.py` | 2.042 dòng; 22 hàm và 2 class top-level | CLI, plan, audit, worker, supervision, coverage và terminal đang chung file |
| `nested_wfo.py` | 4.963 dòng; `run_nested_wfo` khoảng 1.784 dòng | Có nợ cấu trúc, nhưng chưa tách trong vòng này |
| Consumer footprint | 14 file test có nhắc module runner | Di chuyển hàm có thể phá monkeypatch và import compatibility |
| Regression baseline | 95 PASS; receipt elapsed 124,03s | Mốc chi phí của lane đã chạy, không phải dự báo mọi máy |
| AC01–AC15 baseline | ALL PASS; receipt elapsed 42,11s | Chỉ thuộc source baseline, không tự áp cho source mới |

Baseline source A đã ghi: `a50046b389bffd47fb5f19295ee34ccb139f7ba2c55175ae0b9e4eef28778b96`. Agent bắt đầu phải tính lại identity và diff; không mặc định workspace còn đúng snapshot này. Source đổi do di chuyển file là bình thường; điều cần giữ là semantics và lineage, không phải cố giữ hash cũ.

Candidate BNB state-edge vẫn **RESEARCH_HOLD / NOT_QUALIFIED / NO_RERUN**. Packet thị trường cũ vẫn INCOMPLETE (13/15 cell), không sửa receipts hoặc nâng nó thành kết quả của code mới. Economics và lịch sử exposure xem tracker; tài liệu này không thay thế tracker.

## 3. CURRENT và TARGET

CURRENT, đường đang được launcher chọn:

```text
CLI / protocol / binding
  → QualificationCellRunner (CampaignCellRunner)
  → _run_measured → _run_cell_worker → tournament.run_cell
  → native simulator / authority / paper exchange
  → validation coverage + summary + supervisor terminal
```

`ParallelCellRunner` là đường khác còn consumer; không được gọi là dead code rồi xóa. `CampaignCellRunner` vẫn sở hữu execution timing, attempt và cache. Không tạo executor thay thế.

TARGET của vòng đầu:

```text
scripts/run_wfo_parallel.py — CLI + phối hợp + compatibility adapters
  ├─ validation_reporting.py — coverage/terminal validation-only
  ├─ validation_supervisor.py — deadline/RSS/process-group cleanup
  └─ các owner hiện có — binding, ledger, execution, selection, native engine
```

Hai module mới không import ngược launcher. Dependency đi một chiều; collaborator được truyền tường minh khi cần. Không dùng dependency injection tổng quát hoặc cờ mode để gộp mọi verifier.

### Ranh giới trách nhiệm

| Owner | Giữ / nhận trách nhiệm | Không được nhận |
| --- | --- | --- |
| Launcher | CLI, chọn đường, kết nối dependency, compatibility public API | Thuật toán strategy hoặc gate mới |
| `validation_reporting.py` mới | Validation-only coverage và terminal interpretation/publication hiện có | Qualification verifier hợp nhất, ghi timing, sửa economics |
| `validation_supervisor.py` mới | RSS/group PID, deadline, TERM→KILL, reap và terminal orchestration hiện có | Đọc market data, resolve strategy, quyết định qualification |
| `CampaignCellRunner` | Timing và attempt/cache execution hiện có | Runner thứ hai cạnh tranh ownership |
| Binding / ledger / selection hiện có | Mỗi module giữ authority hiện tại | Bản sao logic dưới tên mới |

Các dependency vòng lười giữa campaign execution và full-system simulator, và giữa nested WFO với sensitivity, đã được nhận diện. Chúng **chưa được giải quyết** bởi vòng này. Không tuyên bố toàn repo đã sạch dependency.

## 4. Invariant không được đổi

1. Strategy, params, native stop/authority/sizing/cooldown, timing tín hiệu/fill, costs, windows, purge/embargo và thresholds giữ nguyên.
2. FAILED cell → INCOMPLETE có nguyên nhân; không bỏ cell để cấp PASS. COMPLETED nhưng thiếu report/hash sai vẫn là lỗi integrity, kể cả packet có cell FAILED khác.
3. Thiếu/duplicate/unexpected cell hoặc sai subject/window đều bị phát hiện. Coverage complete không đồng nghĩa SELECT hoặc qualified.
4. ABSTAIN, thiếu mẫu, execution failure và economics failure không bị nhập nhằng. Cost scenarios không pool; fold overlap không trở thành quan sát độc lập.
5. Một owner ghi execution timing; verifier không ghi lại timing. Ledger phản ánh attempt thật, cache hit và attempt outcome, không tự thành nguồn cấp quyền.
6. Binding source/prereg/input/native contract/token/root tiếp tục fail-closed; không fallback strategy, không source attribution `unknown`.
7. Không thêm đọc dữ liệu, tái tạo windows sau freeze, mở OOS/holdout/sensitivity hoặc đổi trial accounting/DSR trong refactor.
8. Timeout/RSS/retry/worker semantics giữ nguyên; cleanup cả group, không giết process của user.
9. Public imports, CLI flags, serialized artifacts, spawn/pickling và cold reuse được kiểm bằng hành vi. Re-export tên hàm **không đủ** khi tests/callers monkeypatch globals của script.
10. Packet cũ bất biến; approval/evidence cũ không mang sang source mới. Không refresh manifest để che drift.

## 5. Phases và điểm dừng

| Phase / task | Đầu ra | Exit gate |
| --- | --- | --- |
| C00 — baseline | Source/file-hash map, dirty diff, consumer/public symbol map, frozen synthetic fixtures, ownership | Không có sửa chồng; baseline evidence được phân biệt với tests mới |
| C01 — characterization | Matrix test hành vi và golden outputs synthetic | Test phân biệt complete/incomplete/invalid/ABSTAIN, mixed corruption, timing và cleanup |
| C02 — reporting slice | Một module mới, adapter tối thiểu do integrator gắn | Output parity và failure parity; không ghi timing |
| C03 — supervisor slice | Một module mới, adapter tối thiểu | Timeout/RSS/descendant behavior không đổi; receipts không false-complete |
| C04 — integration | Imports/callers/test seams tương thích, evidence trên source tích hợp | Các lane trong §6 đạt; không gán tests cũ cho source mới |
| C05 — review độc lập | Verdict trên exact source + assertions + receipts | Reviewer khác tác giả; findings đóng hoặc dừng CHANGES_REQUIRED |

Dependency: **C00 → C01 → C02/C03 → C04 → C05**. C02/C03 có thể soạn module riêng song song sau khi boundary đã khóa; tích hợp lần lượt, không hai agent cùng sửa launcher. Sau C02 phải đánh giá lợi ích trước khi tiếp tục C03.

Không cam kết số ngày hoặc tốc độ tăng theo số agent. C00 phải ước lượng lại effort từ consumer/test seam thực tế; nếu extraction cần đổi simulator/thuật toán hoặc vượt hai module, dừng để thu hẹp thay vì tự mở scope.

## 6. Chứng minh hiệu quả và ngân sách đề xuất

Không lấy giảm số dòng làm bằng chứng duy nhất. Đạt khi: launcher không còn chứa implementation của hai boundary; không tăng số authority; artifacts/decisions giữ semantics; một lỗi terminal không đòi hiểu simulator; regression không mất assertion.

So old/new bằng fixture synthetic, cùng seed/config/worker, hai thư mục mới tách biệt. Chỉ normalize timestamp, PID, run ID, absolute root và elapsed khi không phải đối tượng test. Không xóa/normalize hash tùy tiện: source-bound identity phải được kiểm theo công thức và lineage của source mới. Chạy shadow ngoài đường production, không giữ hai engine authority.

**Đề xuất cap cho một vòng verify tích hợp; chưa phải cấp phép execution:**

| Lane | Cap wall time |
| --- | ---: |
| Unit mới cho boundary thuần | 60s |
| Regression lane 95 tests hiện có | 180s |
| Compatibility / cold reuse targeted | 180s |
| Launcher synthetic end-to-end, một lần ở cuối | 220s |
| AC01–AC15 | 180s |
| Lint / import / diff checks | 30s |
| **Tổng trần** | **850s (14 phút 10 giây)** |

Phải chốt exact test IDs trước chạy, tránh chạy trùng lane; số 95 chỉ là baseline, không được xóa test để giữ số. Dừng ở failure đầu tiên hoặc cap; không automatic retry. Fix rồi chạy lại là attempt mới cần budget còn lại hoặc quyết định bổ sung. Ghi cả failed receipts. Launcher lịch sử khoảng 99–126s không bảo đảm lần sau dưới 220s.

AC bắt buộc nếu đụng các file gate theo AGENTS.md; vòng tích hợp cuối cũng đề xuất chạy vì boundary ảnh hưởng evidence. Dùng `.venv/bin/python`, pytest `-o addopts=""`, controlled execution có timeout/result-file. Tool tên chính xác theo session; thiếu tool bắt buộc phải báo, không tự đặt alias. Không chạy suite nặng chỉ để làm xanh dashboard.

## 7. Ngoài scope và hướng sau

- Không move simulator khỏi script trong vòng đầu: có consumer phụ thuộc module identity/import side effects.
- Không tách toàn bộ `run_nested_wfo`, gộp ABC/plugin/canonical registry, đổi calibration/promotion/live admission.
- Không xóa DEX/options/RL/K8s hoặc dọn dữ liệu. Chỉ giữ chúng ngoài critical path; deletion cần inventory và authorization riêng.
- Không profiling market, tăng timeout, rerun candidate, tuning hoặc tuyên bố có edge. Runtime chậm cần profile synthetic riêng; tổ chức file không tự làm simulator nhanh hơn.

Sau C05, operator chọn **một** việc: đóng refactor ở đây; xử lý một bottleneck đã đo; hoặc quay về quyết định research theo [Research Run Protocol](RESEARCH_RUN_PROTOCOL.md). Chưa có candidate đủ bằng chứng thì mainnet vẫn NO-GO. Không tự mở phase nâng cấp tiếp theo.

## 8. Điều kiện bàn giao

Kết quả phải ghi rõ exact source/assertion hashes, diff scope, test IDs/receipts/caps, hành vi còn UNKNOWN, verdict độc lập và việc deferred. Rollback bằng revert riêng các thay đổi refactor đã tích hợp sau review, không reset worktree hoặc xóa packet. Cách giao việc và ownership: [Agent Playbook](VALIDATION_REFACTOR_AGENT_PLAYBOOK.md).

## 9. Checkpoint triển khai — 2026-10-23

- C00: preflight Git/process và source baseline khớp A đã ghi. Dirty user changes được giữ; không campaign WFO/full-system đang chạy tại preflight.
- C01: chạy lại 95 characterization/regression hiện có, PASS; chưa tuyên bố đã mở rộng toàn bộ matrix tương lai.
- C02 **partial**: tách riêng coverage verifier sang `validation_reporting.py`; launcher còn adapter lấy ba collaborator tại call time. Thân hàm giữ nguyên ngoài signature và tên ba callback. Terminal publication vẫn ở launcher.
- Reviewer khác tác giả: ACCEPTED_FOR_REFACTOR_SCOPE cho coverage extraction; không phải nghiệm thu toàn phase hoặc market qualification.
- C03 chưa bắt đầu; C04/C05 toàn scope chưa hoàn tất. Không chuyển simulator hoặc sửa nested WFO/gates trong checkpoint này.

Receipts, source map, test scope và kết quả tích hợp: checkpoint evidence — local evidence `data/validation_refactor_c02_20261023_01/RESULTS.md`. Trạng thái TARGET của toàn kế hoạch vẫn giữ nguyên. Bước tiếp theo là terminal publication slice trong C02, rồi mới đánh giá tách supervisor; không rerun market để kiểm refactor.

### Tiếp nối: terminal publication

Đã đưa publication sau native execution sang `validation_reporting.publish_validation_result`. Launcher truyền coverage verifier và durable writer tại call time; thứ tự receipts → coverage → terminal/summary, schema và verdict giữ nguyên. Reviewer khác tác giả chấp nhận extraction theo source/assertion review, không tự chứng nhận receipts thực thi.

Coverage và publication đã tách; phần orchestration/đọc kiểm receipt của supervisor vẫn ở launcher, chưa tuyên bố toàn C02–C05 hoàn tất. C03 chưa bắt đầu. Checkpoint mới: publication evidence — local evidence `data/validation_refactor_c02_publication_20261023_01/RESULTS.md`. Việc tiếp theo là khóa boundary supervisor và characterization của nó; chỉ tách nếu giữ được globals/test seams và cleanup semantics, không gộp simulator hoặc đổi runner.

### Tiếp nối: C03 supervisor

Đã tách bốn hàm RSS/PID/reap/supervision sang `validation_supervisor.py`, với adapter tại launcher truyền cwd và collaborator tại call time. Linux refusal, deadline/RSS sampling, TERM→KILL và cleanup-error propagation giữ nguyên. Đúng hai module production mới trong toàn scope; không tạo runner hoặc authority mới.

Reviewer khác tác giả chấp nhận source/assertion extraction. Test process thật và regression/AC được báo riêng trong C03 evidence — local evidence `data/validation_refactor_c03_20261023_01/RESULTS.md`. Orchestration và kiểm coherence các terminal receipt vẫn ở launcher như caller; không đổi thuật toán.

C04 cuối scope vẫn còn cold-reuse/compatibility và launcher end-to-end trên exact source C03; không kế thừa launcher PASS của source C02. C05 nghiệm thu toàn scope cũng còn mở. Cần một budget tích hợp riêng nếu caps không vừa phần còn lại của trần 850s; không tự mở thêm module, phase tối ưu hoặc campaign.

Đề xuất vòng C04/C05 cuối, **chưa được cấp phép**: cold-reuse/compatibility tối đa 180s; một launcher synthetic end-to-end tối đa 220s; identity/lint/diff tối đa 15s — tổng cap bổ sung **415s**. Cold-reuse scope tối thiểu là `test_canonical_cold_replay_preserves_selection_and_trial_burden` (workers 1/2), `test_cold_recovery_still_runs_sensitivity_and_exact_coverage` trong `test_qualification_cold_resume.py`, và `test_real_entrypoint_cold_import_across_timestamp_changes` trong `test_execution_reuse_integration.py`. Launcher ID giữ như checkpoint C02. Dừng ở failure/cap đầu tiên, không retry tự động. Reviewer C05 đọc source/assertions/receipts tích hợp; không tự certify bởi tác giả. AC hiện tại chỉ được dùng nếu source và assertion hashes liên quan vẫn giữ nguyên; nếu phải sửa code/test thì dừng để đánh giá lại phần evidence và budget cần rerun.

### Chốt C04/C05

Operator đã duyệt vòng 415s; thực dùng 110,661s, không retry. Cold-reuse 4/4 và một launcher synthetic 1/1 PASS trên A `fe166b1923280620e942824e9fec5353844cb75dc9039bf030760fc1a1076b38`, source/assertions/config không đổi. Reviewer context riêng đã đọc source, assertions, hashes và receipts rồi chốt **ACCEPTED_FOR_REFACTOR_SCOPE**. AC C03 giữ nguyên identity, không gán bằng chứng từ source khác.

Phạm vi này đã hoàn tất; không mở module/phase refactor tiếp theo. Evidence và giới hạn nghiệm thu — local evidence `data/validation_refactor_final_20261023_01/RESULTS.md`. Quay lại câu hỏi research cốt lõi theo §7 khi operator chọn hướng; không tự rerun candidate hoặc mở live.
