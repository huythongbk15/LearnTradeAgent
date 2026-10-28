# Playbook nhiều agent: tinh gọn validation

> **Status:** CURRENT — quy trình đã áp dụng tới C05 cho bounded scope; không cấp quyền execution mới.
> **Owner:** một integration owner do operator chỉ định.
> **Verified:** 2026-10-23 — đối chiếu source surface; không phải nghiệm thu implementation.
> **Evidence:** [kế hoạch và invariant](VALIDATION_REFACTOR_PLAN.md), baseline verification — local evidence `data/bnb_validation_incomplete_fix_20261023_01/VERIFICATION.md`.

## 1. Hợp đồng chung

Phạm vi refactor hai boundary đã nghiệm thu; xem final handoff — local evidence `data/validation_refactor_final_20261023_01/RESULTS.md`. Không nhận lại các task C00–C05 như backlog mới. Playbook giữ để truy vết cách làm và chỉ tái sử dụng khi có task/budget mới được giao.

Đọc AGENTS.md, [Core System](../CORE_SYSTEM.md), phần hiện tại của [Campaign Status](../CAMPAIGN_STATUS.md), rồi kế hoạch liên kết trên. Đây là refactor validation, không phải phát triển strategy hoặc permission chạy market.

Không sửa packet/approval cũ, không mở holdout/OOS, không download/publish/push/merge/live. Không dừng process của user. Không sửa source dùng chung khi campaign đang chạy. Nếu source hoặc user edits chồng task, dừng phần chồng và báo integrator; không overwrite.

Chỉ operator/integrator được chốt phạm vi và budget; agent không tự nới gate, cap, số workers, retries hoặc biến review PASS thành run approval. Reviewer không review độc lập chính code mình viết.

## 2. Phân vai và ownership

| Vai / task | Được sửa sau khi task được giao | Không được sửa |
| --- | --- | --- |
| Integrator — C00/C04 | Launcher `scripts/run_wfo_parallel.py`, compatibility adapters, shared caller chỉnh tối thiểu | User changes ngoài scope; packet/approval cũ |
| Test owner — C01 | File characterization mới được chốt tại C00 | Production source; shared fixture khi chưa bàn giao lock |
| Reporting owner — C02 | `src/trading_agent/backtest/validation_reporting.py` mới; tests riêng được giao | Launcher, nested WFO, supervisor, gate policy |
| Supervisor owner — C03 | `src/trading_agent/backtest/validation_supervisor.py` mới; tests riêng | Launcher, reporting, strategy/data/selection |
| Reviewer — C05 | Read-only source/assertions/receipts; verdict riêng | Fix code rồi tự certify |

Tên module là TARGET, phải kiểm collision ở C00. Tối đa integrator + ba worker cùng lúc; reviewer vào sau khi có slot và source tích hợp ổn định. Không cần dùng hết agent: test owner nên khóa characterization trước khi hai module owner bắt đầu.

Các file `nested_wfo.py`, `campaign_execution.py`, `preregistration_binding.py`, `attempt_ledger.py`, `selection_eligibility.py` mặc định **không sửa** trong vòng đầu. Nếu phát hiện cần sửa caller, integrator ghi lý do/diff tối thiểu, đánh giá lại AC/budget trước. Không coi bảng ownership là quyền mở rộng scope.

## 3. Phiếu task tối thiểu

Integrator ghi phiếu vào handoff/task của công cụ đang dùng, không tạo tracker dài mới:

```text
Task ID / owner / trạng thái:
Base commit + source A + assertion hash set:
Dirty paths cần giữ nguyên:
Allowed write paths (exclusive):
Dependency / input API / output API:
Invariants áp dụng:
Exact tests + wall cap + result-file location:
Deliverable / reviewer / stop condition:
```

Trạng thái: PLANNED → READY → IN_PROGRESS → READY_FOR_REVIEW → ACCEPTED hoặc CHANGES_REQUIRED/BLOCKED. PASS tests không tự chuyển thành ACCEPTED. Mỗi file một writer; đổi owner phải bàn giao diff/hash trước.

## 4. Trình tự làm việc

### C00 — khóa baseline, không sửa kiến trúc

1. Đọc source/diff hiện tại và process thực tế; không tin registry stale.
2. Tính source identity mới, ghi per-file hash và consumer map. Không dùng HEAD diff thay cho snapshot diff nếu baseline có untracked files.
3. Liệt kê public symbols/imports và monkeypatch seams của runner. Xác nhận active launcher dùng QualificationCellRunner; không xóa ParallelCellRunner.
4. Chốt API hai module, ownership tests và exact verification lane/budget. Bảo đảm không có dependency package → launcher mới.
5. Nếu baseline khác tài liệu, mô tả delta; không reseal evidence cũ để làm khớp.

### C01 — characterization trước extraction

Matrix bắt buộc:

| Nhóm | Phải phân biệt được |
| --- | --- |
| Coverage | Đủ / thiếu / duplicate / unexpected; subject và windows đúng/sai |
| Integrity | COMPLETED thiếu report/hash sai; mixed FAILED + corrupt COMPLETED vẫn reject |
| Terminal | COMPLETE nhưng ABSTAIN; INCOMPLETE có reasons; không false qualification |
| Binding | Source/input/root/token/native-per-cost/config mismatch; non-scoped control không bị chặn |
| Ledger/timing | Fresh/cache hit, nhiều attempt, running vs interrupted, không double count, verifier không ghi timing |
| Process | Timeout và RSS, descendant ignore TERM, KILL/reap, không completion receipt giả |
| Compatibility | CLI pinned geometry, imports/globals/monkeypatch, spawn worker, cold reuse lineage |

Test phải có positive và negative controls, có action/trade thật khi assertion cần chúng; không dùng identity 0=0 làm bằng chứng. Source-string assertion không thay thế test hành vi. Nếu phải migrate test seam, giữ nguyên điều kiện cần chứng minh và reviewer xem cả assertion diff.

### C02/C03 — hai lát cắt nhỏ

Module owner soạn API/implementation/tests trong allowed paths, không đồng thời sửa launcher. Integrator gắn một lát cắt mỗi lần. Trước lát thứ hai, so output/negative behavior với characterization và quyết định tiếp tục hay dừng.

Đặc biệt: hàm chuyển module dùng globals của module mới. Re-export nó ở script không giữ tác dụng của monkeypatch vào script. Dùng adapter/collaborator tường minh hoặc migrate consumer có kiểm chứng; không thêm sys.modules alias/hack để làm test xanh.

### C04 — tích hợp một revision duy nhất

Đọc lại toàn diff và user edits, tính A và assertion hashes trước/sau. Chạy từng lane đã duyệt qua controlled execution, dừng ở lỗi đầu tiên; giữ failed receipts, không retry tự động. Dùng đúng interpreter và pytest options trong AGENTS.md. Không chạy launcher synthetic nhiều lần trong vòng debug unit.

Đối chiếu semantic outputs; source-bound IDs được xác minh theo lineage mới, không ép bằng source cũ. Không thay schema/CLI/gates để giản lược extraction. Nếu source đổi sau tests, đánh dấu evidence bị ảnh hưởng và verify lại trong ngân sách được duyệt.

### C05 — review độc lập và đóng scope

Reviewer nhận source diff, trước/sau identity, assertion diff, receipts gốc và danh sách UNKNOWN, không chỉ summary PASS. Đọc actual call chain CLI→runner→worker→terminal; kiểm các lỗi cũ không quay lại.

Verdict: ACCEPTED_FOR_REFACTOR_SCOPE / CHANGES_REQUIRED / BLOCKED. Đây **không** phải qualification, market-run authority hoặc live approval. Finding có ID, severity, primary evidence và cách falsify; tác giả sửa thì reviewer đánh giá lại phần đổi.

## 5. Prompt giao việc dùng chung

```text
Bạn nhận task <ID> trong VALIDATION_REFACTOR_PLAN và AGENT_PLAYBOOK.
Chỉ sửa allowed paths trong phiếu task; đọc AGENTS.md và baseline thực tế trước.
Mục tiêu là giữ hành vi, tách boundary được giao, không thêm authority/feature.
Không sửa strategy/gate/policy/geometry/cost/approval/packet cũ; không market/OOS/holdout/live.
Trước sửa: nêu dependency, invariant, API và test chứng minh không vacuous.
Mỗi bước: inspect → thay đổi tối thiểu → verify trong cap → đối chiếu invariant.
Failure/cap/source drift/overlap: dừng, giữ receipts, báo chính xác; không auto retry hoặc mở scope.
Không tự nhận independent review cho phần mình viết.
Bàn giao: diff paths + source/assertion identity + exact tests/receipts/time + UNKNOWN + đề nghị verdict.
```

## 6. Checklist nghiệm thu tích hợp

- [ ] Không quá hai module production mới; không runner/registry/gate mới.
- [ ] Launcher chỉ phối hợp hai boundary; không module mới import ngược launcher.
- [ ] Một owner timing; failed/missing/corrupt coverage không false PASS.
- [ ] CLI/import/monkeypatch/spawn/cache/reuse behavior có evidence.
- [ ] Native economics, policy, windows, trial accounting không đổi.
- [ ] Source mới và assertion hashes gắn đúng receipts; AC theo quy tắc, không mượn kết quả cũ.
- [ ] Tổng execution trong cap đã duyệt; mọi failed attempt giữ nguyên.
- [ ] User edits và market packet cũ nguyên vẹn; không approval/market run mới.
- [ ] Reviewer khác tác giả đã đưa verdict trên source tích hợp.
- [ ] Mainnet vẫn NO-GO; candidate không được nâng trạng thái bởi refactor.

Nếu không đạt, bàn giao BLOCKED/CHANGES_REQUIRED và câu hỏi nhỏ nhất cần operator quyết. Không mở thêm vòng nâng cấp để trì hoãn kết luận.
