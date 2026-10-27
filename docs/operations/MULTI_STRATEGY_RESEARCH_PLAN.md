# Playbook E2E — từ nghiên cứu đến vận hành có kiểm chứng

Cập nhật: **25/10/2026, Asia/Ho_Chi_Minh**. Chủ sở hữu: operator/maintainer. Đây là kế hoạch giao việc, **không cấp quyền chạy campaign, kết nối sàn hoặc live**.

## 1. Đích đến và phạm vi

Mục tiêu sản phẩm: một policy cho **một pair/timeframe**, đi hết data → research → qualification → policy → runtime → vận hành được giám sát. Không thêm sàn, portfolio/short/funding engine, ML/router hoặc framework để thay cho bằng chứng còn thiếu.

Phải phân biệt ba mức hoàn thành:

| Mức | Khi nào được tuyên bố | Hiện tại |
| --- | --- | --- |
| E2E-MEASUREMENT-CLOSED | Input/source/config được bind; native execution, coverage và accounting được kiểm; có verdict kể cả ABSTAIN/INCOMPLETE | Đã có với RSI/ADX, MA baseline, H-PULLBACK-01 và H-PULLBACK-TEMPORAL-01. Không có winner. |
| E2E-PAPER-ACCEPTED | Một policy đủ điều kiện nghiên cứu được bind đúng runtime; parity và vận hành paper/testnet/shadow có evidence theo contract | Chưa đạt cho candidate hiện tại |
| LIVE-CANARY-ELIGIBLE | Research, release, vận hành và risk/account gates đủ trên exact subject; reviewer và operator chấp thuận | NO-GO; vẫn chưa phải quyền đặt lệnh hoặc tăng vốn |

Không thể hứa rằng lặp hữu hạn sẽ tìm được edge. **NO_SUPPORTED_WINNER / NO_TRADE là kết quả hợp lệ.** Nếu mục tiêu kinh tế không đạt, đóng hypothesis thay vì thay đổi tiêu chuẩn để gọi hoàn thành.

Luồng chính:

~~~text
E0: source/data/authority
  → E1: admission + preregistration + review
  → E2: một phép đo exploratory có ngân sách
  → E3: qualification/OOS/sensitivity + review
  → E4: policy binding + runtime parity
  → E5: paper/testnet/shadow + release evidence
  → E6: xét live canary → báo cáo canary → xét scale riêng

Engineering lỗi → loop sửa có giới hạn, giữ failed receipts
Thiếu data/sample → HOLD → đề xuất observation/design mới, không nới gate
Economics không đạt → đóng hypothesis → chỉ mở hypothesis mới khi được duyệt
Safety/identity/approval lỗi → BLOCK, không tự chuyển stage
~~~

## 2. Nguồn sự thật và trạng thái bắt đầu

Chỉ dùng **[CAMPAIGN_STATUS](../CAMPAIGN_STATUS.md)** để cập nhật tiến độ. File này định nghĩa cách làm; packet lưu evidence. Không tạo tracker/TODO mới cho từng agent hoặc sao chép metrics vào nhiều tài liệu.

Thứ tự xử lý mâu thuẫn: AGENTS.md và giới hạn quyền → executable contracts/policy được freeze → raw evidence trên đúng revision → tracker → kế hoạch → snapshot lịch sử. Nếu contract/code/document không khớp, dừng phần bị ảnh hưởng và ghi finding; không chọn nguồn dễ PASS hơn.

| Thành phần | Trạng thái có bằng chứng | Việc không được suy ra |
| --- | --- | --- |
| [RSI/ADX BTC1h](../../data/rsi_fixed_validation_run_20261024_01/RESULTS.md) | 3/3 cells; 39 trades/cost; ABSTAIN | Không có winner, không qualification |
| [MA20/50 BTC1h](../../data/ma_simple_baseline_validation_run_20261024_01/RESULTS.md) | 3/3 cells; 64 trades/cost; ABSTAIN; closed và open PnL được tách | Không rank trực tiếp với RSI vì khác window |
| [MA registry sidecar](../../data/ma_simple_baseline_validation_run_20261024_01/postrun_registry_review_20261025_01/RESULTS.md) | Read-only reconcile 3/3; original receipts giữ nguyên | Không chứng nhận DSR, không nâng economic verdict |
| [BNB state-edge](../../data/bnb_stateedge_economic_review_20261023_01/RESULTS.md) | 13/15 cells; INCOMPLETE/HOLD | Hai timeout vẫn UNKNOWN; không tự rerun để điền bảng |
| [H-BRANCH-01](../../data/rsi_branch_attribution_20261024_01/RESULTS.md) | Đóng điều tra; không có căn cứ sửa owner-lock để cứu economics | Không chọn range-only từ subgroup hậu nghiệm |
| [Pullback admission](../../data/trend_pullback_admission_20261025_01/RESULTS.md) | 4/4 synthetic functions; có native trade thật; source không đổi | Không chứng minh samples/edge, không independent review |
| [Pullback preregistration](../../data/trend_pullback_prereg_20261025_01/RESULTS.md) | 1 fold/3 cells đã preflight; PREPARED_NOT_AUTHORIZED | Chưa có quyền market run từ tài liệu này |

Source tham chiếu hiện tại: 8df9a071be0df86feda26425ad66d269341f06a29803e8a22040931b57fa178d. Đây là snapshot, không hard-code làm source tương lai. Tests/config/payload có hash riêng; A phủ src/scripts không tự phủ tests. Worktree dirty khiến study provenance_eligible=false theo code hiện tại; hash ổn định không thay thế clean/reviewed release.

## 3. Vai trò, quyền và cách nhiều agent làm việc

| Vai trò | Trách nhiệm | Không tự làm |
| --- | --- | --- |
| Coordinator/integrator | Đọc tracker, chọn task đủ dependency, claim file/interface, gom revision, kiểm budget và bàn giao | Tự duyệt nghiên cứu/live hoặc gán self-review thành independent |
| Research owner | Hypothesis, params, exposure/trial history, data/fold/cost design và economic verdict | Chọn window/threshold theo outcome, xóa negative trials |
| Engineering/execution owner | Reproduce bug, patch nhỏ, native tests; chạy đúng một approved command | Đổi strategy/gates để sửa orchestration; tăng timeout/retry ngoài quyền |
| Evidence reviewer | Đọc raw material, source/assertions; kiểm độc lập arithmetic/negative cases/binding | Chỉ chạy lại script tác giả rồi gọi independent review |
| Ops/risk owner | Runtime mapping, account/environment boundaries, monitoring, recovery và release gates | Mở external session hoặc tăng risk/capital nếu chưa được duyệt |
| Human operator | Chọn scope, cấp ngân sách/quyền đọc/chạy; chấp thuận account/live/capital | Approval của agent không thay thế operator |

Một người có thể kiêm nhiều vai, nhưng author không tự đóng independent review. Reviewer có thể là agent khác tác giả với context/material phù hợp; phải công bố provenance và giới hạn, không giả danh bên thứ ba. **File này không cấp quyền tự spawn agent**: chỉ delegate khi user hoặc instruction áp dụng cho phép.

Quy tắc phối hợp:

1. Một writer cho mỗi file/interface. Claim owner + base revision + files ở task row hiện có; tránh sửa cùng source từ hai agent.
2. Không chỉnh src/scripts dùng chung khi campaign đang chạy; xác minh process thực tế, không tin registry stale. Không dừng process của user.
3. Local unit tests trên stores riêng có thể độc lập; AC01–AC15 phải serialize vì có shared producer paths. Market run hiện tại một worker, không chạy chồng campaign để tiết kiệm thời gian.
4. Sau integration: capture source/config/tests mới, chạy affected suites đúng revision; không cộng test counts giữa các SHAs thành PASS.
5. Chỉ review lại phần bị invalidate. Approval-only change không mặc nhiên cần full suite; phải chứng minh source/assertions không đổi và binding mới đúng. Code change không được dùng receipt cũ như receipt mới.

## 4. Work packages chi tiết

### E0 — Khóa điểm xuất phát, dữ liệu và authority

**Owner:** integrator + research owner. **Output:** một baseline record trong tracker, không một tracker mới.

- E0.1: đọc AGENTS.md, tracker Current, proposal và code consumer; kiểm tool/schema thật. Thiếu execute_shell_command: báo TOOL_UNAVAILABLE; không invent alias hoặc dùng shell. Fallback chỉ khi được cho phép cụ thể. Không suy quyền từ chat cũ về push/live.
- E0.2: kiểm actual PID/descendants, git status/diff và source/config/lockfile/assertion hashes. Phân loại user edits, task edits, unrelated edits; không reset, stash/clean hoặc commit/push tự ý. Không đưa secrets vào artifact.
- E0.3: giữ các failed/old packets. Nếu cần integration sạch cho qualification/release, đề xuất đúng diff/commit và xin quyền cần thiết; clean source mới không kế thừa qualification cũ chỉ vì tên strategy giống nhau.
- E0.4: lập data exposure record: physical file SHA riêng quality/metadata hash; timezone/open-close cutoff, rows, gaps, duplicate/NaN, prefix đọc/decoded/evaluated. UNKNOWN không được suy thành untouched. Timestamp preflight không là proof OHLCV quality hoặc historical non-exposure.
- E0.5: kiểm đủ eligible observations cho stage dự kiến. Current exposed 2023 input chỉ exploratory; confirmation cho mục tiêu 2026 cần design/observation scope riêng. Không tự download, mở reserved holdout hoặc sửa manifest để che mismatch.

**Exit:** source/config/input/authority nhất quán, mỗi UNKNOWN có owner và ảnh hưởng. Thay đổi sau freeze → dừng, ghi invalidation, tạo revision/packet mới nếu được duyệt. Không mua thêm audit khi không còn câu hỏi quyết định cụ thể.

### E1 — Admission và đóng băng một câu hỏi

**Owner:** research + execution; reviewer khác author cho official acceptance. **Output:** một proposal + frozen protocol + reviewer verdict.

- E1.1: đúng một strategy/pair/TF/parameter set, cơ chế có thể bác bỏ, lịch sử thử nghiệm và lý do chọn không phải max Sharpe. Phân biệt signal, filter, sizing và allocation.
- E1.2: đọc constructor/generate_signals, schema/default/registry, adapter và runner. Tên strategy không là proof semantics. Ví dụ pullback thực tế SMA20/80/ADX25/volume1.5, không EMA/Fibonacci/institutional flow; RSI period đang không tham gia signal nên không mở grid giả.
- E1.3: native synthetic positive và negative controls: có actual fills/trade, per-bar causality/timing, entry/exit, protection và inventory theo scope. Không gọi zero-trade identity là positive PASS. Ghi rõ injection so với actual indicators.
- E1.4: public preflight-only phải sinh đúng folds/cells/windows, không chỉ CLI chạy được. Pin params/cost bodies, history, source/config/environment, budget, trial burden, analytical comparisons và terminal outcomes.
- E1.5: reviewer kiểm source/assertions/raw receipts; ít nhất một positive/negative critical path khi scope/budget cho phép. Script của tác giả chỉ là self-verification. CHANGES_REQUIRED có severity, reproduction và file/interface ảnh hưởng; không lặp full review không thay đổi.
- E1.6: operator cấp authority riêng cho exact proposal/protocol/read scope/budget/output/environment. Fresh authorization record, không sửa proposal cũ để giả đã duyệt. Authority ≠ technical verdict; broad research CLI có thể chạy được không có nghĩa operator đã cho phép.

**Exit:** APPROVED_FOR_ONE_BOUNDED_RUN, hoặc BLOCKED_REVIEW / WAITING_OPERATOR. Đổi source, params, data/window, risk/cost, budget hay scope sau duyệt phải invalidate và review lại phần liên quan.

### E2 — Một phép đo exploratory, raw review và terminal verdict

**Owner:** execution → evidence reviewer → research owner. **Output:** packet mới đã seal + một quyết định.

1. Trước launch: kiểm actual competing processes; exact source/input/config/proposal hashes và output chưa dùng. Re-freeze qua launcher phải khớp prepared protocol; không copy protocol sang output khác mà bỏ kiểm binding.
2. Chạy existing launcher/supervisor/native engine bằng controlled_exec, .venv/bin/python, result file, heartbeat, timeout/RSS và one-worker/zero-retry đã duyệt. Không custom runner, monkeypatch hoặc bypass gate.
3. Cell đầu là official budget probe, tính một lần; stop-on-first technical failure. Không coi các cell NOT_ATTEMPTED là FAILED/completed hoặc tạo worker receipts giả.
4. Sau stop: kiểm process group/descendants thuộc run đã cleanup; giữ timeout/failed receipts. Không kill process không thuộc run.
5. Coverage: planned ↔ attempted ↔ completed/failed/unattempted; mỗi report SHA/source/params/cost/window khớp. Missing report không PASS. STARTED/no result/worker alive là RUNNING; worker chết là INTERRUPTED, không SELECT.
6. Accounting: fills/trade sum → closed gross-cost-net; closed net + open PnL → marked equity. Denominator là measurement opening equity. Không double-charge cost/inventory hoặc dùng gross_profit của winning trades như gross alpha.
7. Economics: samples, net expectancy/Sharpe/PF/DD, turnover/exposure, cost stress, concentration, reason/regime attribution với limits. Cash và passive benchmark phải cùng measurement window; khác native protection/authority phải công bố, không gọi risk-matched alpha.
8. Registry: nối evaluation key/attempt/report tới candidate ID, context, fold/phase/metric; query-only snapshot nếu cần. Ledger UNKNOWN lịch sử giữ nguyên, append sidecar thay vì sửa receipt đã seal.
9. Review mới phải phân biệt engineering/economic/qualification verdict; update tracker và đúng một next action. Không tự mở campaign khác.

| Trường hợp | Verdict | Được làm tiếp |
| --- | --- | --- |
| Drift/technical failure/timeout/coverage gap | INCOMPLETE_TECHNICAL | Điều tra read-only; fix loop khi còn đúng quyền/budget; không tự rerun market |
| Complete nhưng thiếu sample contract | INCOMPLETE_SAMPLE / ABSTAIN | Đề xuất observations/design mới; không hạ trade gate hoặc kéo dài window hậu nghiệm |
| Đủ mẫu, economics/selection không đạt | RESEARCH_ABSTAIN / NOT_QUALIFIED | Đóng hypothesis; hypothesis mới chỉ khi có lý do và authority mới |
| Selection đạt | DIAGNOSTIC_POSITIVE_NOT_QUALIFIED | Đề xuất E3; không publish/paper/live tự động |

### E3 — Qualification: chứng minh ngoài sample đã chọn

**Owner:** research + quant reviewer. **Dependencies:** candidate đáng nghiên cứu, clean/reviewed qualification revision và eligible data/methodology. **Output:** applicable gate report + independent verdict; không chỉ summary xanh.

- E3.1: trước khi đọc outcome, đóng folds/train/inner/outer/purge/embargo, overlap/dependence, selection/tie-break, costs, sensitivity, historical trials, gate applicability và toàn budget. Planner metadata chỉ chứng minh geometry, không power hoặc samples thực tế.
- E3.2: inner chọn params theo protocol; outer chỉ đánh giá lựa chọn trước đó, không dùng outer/holdout để chọn lại strategy/window. Exposed data không được relabel untouched.
- E3.3: historical selection burden gồm các khám phá ngoài registry; NO_MATCHING_HISTORY ở IDs mới không xóa thử nghiệm cũ. Khép câu hỏi DSR/PBO/dependence với reviewer trước claim, không chỉ điền số metric cho đủ schema.
- E3.4: sensitivity thực tế theo path qualification: cost2x, delay, neighbors và các arm applicable. Current neighbor implementation cần đủ giá trị axis và outer scope; không dùng synthetic hoặc validation-only thay real measured sensitivity. Fixed singleton parameter stability=1 không chứng minh robustness.
- E3.5: read/re-freeze current native gate contract; không đổi thresholds để candidate pass. Snapshot hiện tại có 20 gates, canonical_stats_verified và sensitivity_verified. Ví dụ: OOS Sharpe≥0.8, PF≥1.2, DD≤10%, Calmar≥0.5, DSR≥0.95, PBO≤0.2, ≥30 OOS trades; fold/cost/concentration/delay/neighbor gates cũng bắt buộc. Đây không phải toàn bộ gate set hoặc quyền áp chúng lên inner diagnostic.
- E3.6: holdout chỉ mở theo approval/protocol riêng sau design freeze; nếu đã expose hoặc thiếu eligibility thì không gọi là independent final test. Thiếu data/inference/sensitivity → BLOCKED_DATA_OR_METHOD, không waive/N/A để vượt gate.
- E3.7: reviewer kiểm raw continuous OOS equity, accounting, source/data/cost identity, full applicable gate results, trial burden và limits. Chỉ có QUALIFIED_RESEARCH khi toàn contract đạt trên exact subject; thiếu evidence là NOT_QUALIFIED.

Không có budget E3 mặc định từ 2100s exploratory. Tất cả folds/arms/probes/cleanup phải được đếm và duyệt trước; không bắt đầu khi budget hoặc một metric bắt buộc còn UNKNOWN.

### E4 — Đưa đúng qualified policy vào đúng runtime

**Owner:** execution + evidence/risk; đối chiếu L0/L1/L2 của [live readiness plan](LIVE_READINESS_AGENT_PLAN.md).

- E4.1: map actual entry point → resolver/artifact → strategy params/features/history → forecast → risk → target exposure → authority/planner → broker. Chứng minh đúng pair/TF/source/artifact/config/instrument rules, không silent fallback Enhanced MA.
- E4.2: hiện script live_enhanced_ma_binance.py còn bind enhanced_ma ở consumer được đọc. Pullback research PASS hypothetical không tự chạy được ở đó. Chỉ sửa wiring có scope sau qualification; không xây generic live platform trước khi có candidate.
- E4.3: replay cùng input/timestamps; freeze tolerances trước comparison. Kiểm vector tín hiệu và actual order/fill/mark/carry; khác sizing/stop/cooldown/cost/timing phải giải thích. Signal parity không bằng economic parity.
- E4.4: negative admission: missing/expired/wrong strategy/hash/environment/account/policy hoặc non-qualified evidence phải chặn BUY. Safe reduction giữ đúng policy; không khóa exits chỉ để no-order test PASS.
- E4.5: local fault drills: duplicate/partial/UNKNOWN fill, restart/cancel/stop races, missing protection, stale data, persistence/recovery. Isolated fixtures, không account calls hoặc fabricated live-eligible artifacts.

**Exit:** RUNTIME_PARITY_ACCEPTED trên integrated revision và qualified subject. Local plumbing có thể chuẩn bị sớm bằng non-promotable fixtures, nhưng không dùng để bỏ dependency research khi activate vận hành.

### E5 — Vận hành paper/testnet/shadow và release

**Owner:** ops/risk + execution + reviewer. **Dependencies:** exact admitted subject, environment/account approval, risk/loss budget/expiry, release và monitoring phù hợp. Xem [S7 runbook](../S7_OPERATIONAL_EVIDENCE_RUNBOOK.md), [production policy](../PRODUCTION_POLICY.md), live readiness L3.

- E5.1: tách environment/account stores, stable account identity, credentials scoped/withdrawal restrictions, symbol rules hiện hành, single leader/fencing và kill-switch owner. Không mở account/network session từ tài liệu này.
- E5.2: prove actual alerts đến observer, ledger/exchange reconciliation, exactly applicable protection, restart/network/UNKNOWN submit/cancel, backup/restore và rollback không replay submit. Dry-run không là proof drill thật.
- E5.3: policy-bound shadow và testnet evidence có đúng timestamps, unique lifecycle IDs, source/release/policy identity, healthy intervals, sample outcomes mature đúng lúc. Không gộp Alpaca paper/Binance testnet/Demo hoặc các subject khác nhau.
- E5.4: theo current contracts, testnet tối thiểu30 continuous days/100 unique complete lifecycles/0 unresolved orders/0 safety breaches; shadow tối thiểu30days/30observations/ECE≤0.10 và0critical alerts cùng các applicability rules. Recheck executable contract khi thực hiện; không lấy checkbox lịch sử làm acceptance hiện tại.
- E5.5: CI/release integrity: exact image/source/dependencies, signatures/SBOM/SLSA và verified artifact bindings theo release gate. Chưa truy cập/kiểm CI hoặc host thật thì NOT_VERIFIED; không push/deploy để tự chứng nhận.
- E5.6: integrate evidence review và permission expiry/rollback plan. Subject/release change phải đánh giá invalidation/reuse; không cộng ngày soak khác identity hoặc ép giao dịch chỉ để đủ count.

**Exit:** E2E-PAPER-ACCEPTED và release review đủ điều kiện, hoặc HOLD/NO_GO. Những elapsed-time gates cần wall-clock thật; không thể hoàn tất bằng một phiên test nhanh hoặc tạo timestamps giả.

### E6 — Live canary và xét scale riêng

**Owner:** human operator + risk/ops/reviewer. Đây là decision gate, không lệnh tự chạy.

E0–E5 đủ và approval ký đúng subject/account/release/pairs/risk-capital-loss budget/expiry/rollback owner → chỉ mới CANARY_ELIGIBLE. Actual canary cần permission riêng; deployment không auto-enable mainnet. Theo current S7, canary cần tối thiểu30days và0safety/loss-budget breaches trước scale review. Scale luôn là approval mới, không có cơ chế tự tăng vốn để bù lỗ.

Safety incident/unknown inventory/unresolved submit/failed protection/stale data/paging failure/drift/budget hoặc expiry → BLOCK new exposure, safe reduction theo quyền và escalate operator; không tự cancel protection hoặc đóng tài khoản ngoài runbook.

## 5. Vòng lặp có giới hạn — không chạy tới khi tình cờ có lãi

Các cap dưới là **đề xuất cho approval profile**, không tự cấp budget. Budget đã duyệt riêng có ưu tiên; không lấy ngân sách market bù engineering review.

| Loop | Cách làm | Giới hạn mặc định đề xuất | Điểm thoát |
| --- | --- | --- | --- |
| L-ENG | Evidence failure → reproduce → patch tối thiểu → affected tests → review → source/binding mới | Tối đa2 repair iterations; 180s/command và600s tổng local verification nếu được duyệt; test fail dừng để đọc receipt, không rerun command chưa sửa | Pass trên revision mới, hoặc UNVERIFIED/BLOCKED cần budget/scope mới; market run không auto retry |
| L-REVIEW | Findings có code/evidence → đóng finding → reviewer kiểm delta | Tối đa2 review rounds trong batch; không chạy cùng suite nguyên vẹn lặp lại chỉ để có thêm PASS | ACCEPTED trong scope hoặc NEEDS_DECISION với đúng blockers; không tự cấp independent label |
| L-MARKET | Một approved frozen candidate →3cells → raw review → terminal verdict | Current pullback proposal:600s/cell/2100s tổng,1worker,2048MiB sampledRSS,0retry; không automatic next hypothesis | Dừng sau một verdict, kể cả âm hoặc thiếu mẫu |
| L-HYPOTHESIS | Tổng hợp negative evidence → một thesis mới có cơ chế và quyết định cần trả lời → prereg | Không có quota tự động; nếu muốn autonomous batch, operator phải chốt trước số hypotheses/danh sách/rules/trial ledger và tổng budget; không dùng until-profitable | Có qualified research, hoặc hết approved search budget/eligible data → NO_SUPPORTED_WINNER |
| L-OPS | Observe authorized health → reconcile → mature outcomes → acceptance/incident review | Theo calendar, capital/loss/expiry của environment; không busy-wait hoặc tạo hoạt động giả | Accepted/HOLD/incident/expiry; không tự bật live hoặc tăng vốn |

Nếu chạm selection_policy.py, live_safety.py, nested_wfo.py hoặc evidence_ac*.py: **AC01–AC15 bắt buộc theo AGENTS.md**. Nếu cap600s không đủ affected tests+AC, dừng và xin điều chỉnh trước; không bỏ AC hoặc claim done. Tests cần .venv/bin/python và pytest -o addopts="". Không chạy full suite vô điều kiện sau thay đổi tài liệu/approval-only; chọn đúng surface, ghi skipped/NOT_RUN minh bạch.

Quy tắc chung mỗi iteration:

~~~text
READ tracker + exact task + authority + actual processes
IF dependency/identity/permission/budget thiếu: WAITING_INPUT, không thực thi
SELECT đúng một uncertainty và tiêu chí bác bỏ
DECLARE files/interfaces/output/cap/stop trước khi làm
EXECUTE trong scope bằng công cụ/schema thật, lưu receipt bền
VERIFY raw evidence + negative cases + identities; không chỉ đọc SUMMARY
IF technical failure: giữ receipts, phân loại trước mọi patch; chỉ loop nếu còn quyền/budget
IF economic/sample failure: đóng attempt; chỉ proposal mới, không tự rerun/tune
IF scope-pass: xin review/authority cho stage tiếp; không tự nhảy stage
UPDATE tracker: verdict + evidence + remaining budget + exactly one next action
~~~

Trước mỗi vòng, trả lời: kết quả mới có đổi quyết định nào không? Nếu không, không thêm lần seal/audit/test không cần thiết. Source change invalidate đúng dependencies; report/receipt/verdict content change là evidence change, không chỉ refresh bookkeeping. Proposal ≠ approval; self-verification ≠ independent review.

## 6. Các việc cụ thể ngay bây giờ

| ID | Đầu việc / owner | Trạng thái | Xong khi |
| --- | --- | --- | --- |
| NOW-01 | E2E playbook / integrator | Hoàn tất tài liệu ở lượt này | Một current plan, old history giữ nguyên, links hợp lệ; không campaign/production edit |
| NOW-02 | Pullback exact review / reviewer khác author | DONE26/10 | Separate-agent review accepted source d2864272… và protocol081fce81…; verdict trong execution packet, review cũ giữ nguyên |
| NOW-03 | Pullback one-run authority / operator | DONE26/10 | Authorization pin hashes cho đúng một packet3cells,read6160rows,measurement[912,5232),600/2100s/1worker/2048MiB/0retry/failfast |
| NOW-04 | Execute/review current exploratory / execution + evidence | DONE26/10 | 3/3COMPLETED,coveragePASS,25trades/cost<30,ABSTAIN; independent post-runVERIFIED_INCOMPLETE_SAMPLE_ABSTAIN; registrySQLreconciliation vẫnUNKNOWN;không qualification |
| NEXT-01 | Spot research / research | PAUSED_AFTER_CLOSED_MEASUREMENTS 27/10 | H-LIQ-REVERSAL-01 rejects the fixed 1.5%/6 rule with native policy on [6510,15150) only. Gross was negative. Do not open another strategy from this closeout. |
| NEXT-02 | Measured liquidity-reversal source / maintainer | INTEGRATED_WITH_STATUS | The strategy patch is committed after the dirty-tree measurement. Packet source stays a9133770 and does not become qualified. |
| NEXT-03 | Qualification → runtime → ops / các owners | BLOCKED_BY_DEPENDENCIES | Đi tuần tự E3–E6, không tự tạo live authority |

H-PULLBACK-01 đóng INCOMPLETE_SAMPLE_ABSTAIN. H-PULLBACK-TEMPORAL-01 đóng VERIFIED_RESEARCH_ABSTAIN_NOT_QUALIFIED. H-VOLBREAK-01 đã đo xong và được review: VERIFIED_DIAGNOSTIC_POSITIVE_NOT_QUALIFIED. Đề xuất E3 ghi BLOCKED_DATA_OR_METHOD tại [E3 proposal](../../data/volatility_breakout_e3_proposal_20261026_01/E3_PROPOSAL.md). Không chạy cell qualification. Mainnet NO-GO.

## 7. Handoff, evidence và định nghĩa DONE

Mỗi task row ở tracker tối thiểu có:

~~~text
Task/stage; owner; reviewer/provenance; base và tested source/config/assertion identity
Question; non-goals; dependency/approval references; allowed files/interfaces
Input physical SHA/quality/UTC windows; read/decode/evaluation/exposure scopes
Planned/attempted/completed/failed/unattempted; attempts và trial accounting
Command/schema; per-command và aggregate cap; rc; wall/RSS; cleanup evidence
Raw paths/hashes; arithmetic and negative checks; changed/unchanged artifacts
Engineering verdict; economic verdict; qualification/operational authority verdict
UNKNOWN/NOT_RUN/blocked; remaining budget; exactly one next action và owner
~~~

DONE phải nghĩa là deliverable được kiểm trên đúng revision và scope, không phải “đã code” hoặc một dòng VERIFIED. Chính author có thể LOCAL_VERIFIED; independent acceptance cần người review khác với evidence thật. Kết quả âm vẫn là DONE cho task đo; không tự đổi thành OPEN để tiếp tục thử cho tới khi xanh.

Giữ packet cũ, failed receipts và negative trials. Không ghi đè immutable files; sidecar phải nêu mình bổ sung gì và hash/link bản gốc. Hash identity không chứng minh nội dung assertion đúng. Snapshot registry logic không phải full DB audit/DSR validation. Không ship evidence ra ngoài máy nếu chưa có quyền.

## 8. Điều gì thật sự đưa dự án gần sử dụng hơn

Đừng đo tiến độ bằng số strategy, số file hoặc số vòng review. Đo bằng:

1. Một câu hỏi được trả lời bằng native evidence đáng tin, kể cả NO_TRADE.
2. Một candidate vượt qualification trên observations đủ điều kiện, sau costs và uncertainty hợp lệ.
3. Exact qualified policy chạy đúng runtime, không fallback hoặc giảm protections.
4. Đủ lifecycle/reconciliation/alerts/recovery và elapsed-time gates của đúng environment.
5. Operator đủ thông tin để duyệt hoặc từ chối canary bằng risk/loss budget rõ ràng.

Hiện chúng ta đã có(1), chưa có(2)–(5) cho candidate sẵn sàng live. Không có ETA đảm bảo tìm được edge; thời gian operational soak không thể rút bằng compute. Giữ mainnet NO-GO đến khi toàn bộ contract và approval đạt.

## 9. Tài liệu liên quan và lịch sử

- [Tracker duy nhất](../CAMPAIGN_STATUS.md): trạng thái/evidence hiện hành.
- [Research run protocol](RESEARCH_RUN_PROTOCOL.md): checklist cho một phép đo, không là approval.
- [Live readiness L0–L4](LIVE_READINESS_AGENT_PLAN.md): task contracts chi tiết; handoffs lịch sử không là quyền hiện tại.
- [S7 evidence](../S7_OPERATIONAL_EVIDENCE_RUNBOOK.md), [production policy](../PRODUCTION_POLICY.md), [capability matrix](../CAPABILITY_MATRIX.md): acceptance/maturity, không bị playbook này waive.
- [Pullback exact proposal](../../data/trend_pullback_prereg_20261025_01/RESULTS.md): source/protocol/input/budget cụ thể; không dùng số budget của BNB cũ hoặc candidate khác.
- [Bản kế hoạch trước E2E, archive nguyên bytes](archive/MULTI_STRATEGY_RESEARCH_PLAN_PRE_E2E_20261025.md): chỉ để truy vết; link tương đối bên trong snapshot có gốc cũ docs/operations, không là hướng dẫn execution hiện hành.

File này thay thế thứ tự công việc trong các snapshot cũ, không thay executable risk/research/release contracts, không đổi thresholds và không tự cấp bất kỳ external/live authority nào.
