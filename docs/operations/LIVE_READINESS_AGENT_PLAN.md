# Quy trình agent hoàn thiện live readiness — L0–L4

> **Status:** TARGET — kế hoạch thực hiện, không phải chứng nhận live.
> **Owner:** maintainer/operator; execution, research, evidence và ops owners.
> **Verified:** 2026-10-04 — kiểm tra đường dẫn và baseline tại `6f1d69d4f1c4f8170835a9ce95b50e3a73b46eb7`.
> **Evidence:** AC baseline local-only: `data/acceptance_runs/ac01_15_20261004_084229_utc/SUMMARY.txt`.
> **Mainnet:** NO-GO. Tài liệu này không cấp quyền kết nối tài khoản, deploy hay gửi lệnh.
> **Primary type:** how-to — quy trình giao việc, thực hiện và nghiệm thu cho agent.

## 1. Mục tiêu, phạm vi và nguồn sự thật

### Current action order — 2026-10-16

Preflight completed without freeze/measurement:11technical checks pass, including
same source04ba6a07, unchanged protected files and existing6795-bar input SHA.
Authority: `data/readiness_preflight_20261016_01/preflight.json`.
Status BLOCKED_REVIEW_INTEGRATION. Do not repeat the unchanged suite or create
an approval to bypass the independent-review/clean-integration prerequisites.
After authorized review and integration, refreeze/reverify the integrated source
and request the operator's exact fingerprint approval. No broker/live authority.

Engineering snapshot verified locally:134 regression tests,15AC checks and
Ruff PASS on source04ba6a07…; source/config/tests/348protected files unchanged.
Authority: `data/readiness_closure_20261016_01/verified.json`. This is not
independent acceptance or proof of alpha/execution readiness.

Read [closure](VALIDATION_CLOSURE_20261016.md) and
[current tracker](../CAMPAIGN_STATUS.md) before older handoffs below.
The selected implementation is committed as2fc02ac on
codex/readiness-integration-20261016 (not pushed), based on master9b69462.
Unrelated work is preserved and the untracked old campaign script keeps source
capture dirty; do not freeze/execute until this prerequisite is resolved.
New telemetry is implemented; two historical market
validation packets both ABSTAIN. No qualified winner is established.

| Order | Work | Exit criterion / authority |
|---|---|---|
| 1 | Review and integrate input/telemetry/planner fixes | Same-source regression and AC evidence, reviewer findings closed; no automatic Git publication |
| 2 | Bounded telemetry diagnostic | Operator approves exact fresh calibration fingerprint and budget;2 fixed cells, no OOS/holdout/promotion |
| 3 | Decide research protocol | Explain signal vs filter attrition and sample feasibility; freeze folds/grid/cost/sensitivity/trial budget; no threshold relaxation |
| 4 | Qualify one candidate | Actual validation/OOS/statistical/sensitivity evidence, followed by separately controlled holdout/review; NO_TRADE if none passes |
| 5 | Bind candidate to runtime | Exact strategy/params/artifact/source/timeframe/release identity, no silent Enhanced MA fallback; prove in replay/paper first |
| 6 | Operational acceptance | Operator-authorized paper/testnet, reconciliation/protection/restart/kill-switch/rollback drills, then separate canary decision |

Diagnostic proposal: `data/readiness_closure_20261016_01/diagnostic_proposal.json`.
It is DRAFT, not a frozen plan or operator approval. The existing calibration
producer/worker/verifier is the execution path; do not create a parallel runner.
Canonical defaults20/80 are a representative plumbing subject, not a selected
winner. Keep fixed sizing semantics explicit; no new vol-sizing strategy here.
ADX25 calibration / research grid30 remain unchanged.

`build_campaign_policy.py` is retired and exits before both execution and its
old dry-run data scan. Use `plan_promotable_campaign.py --protocol <packet JSON>`
for metadata-only geometry; it no longer ranks combinations or invents an ETA.
This is an intentional CLI compatibility break to remove the stale route,
not a change to native promotion criteria. Existing canonical promotion services
are not disabled. Do not re-enable the legacy fresh-registry/sensitivity-off path.

Current Binance live script still binds Enhanced MA explicitly. Do not point
ma_vol_target evidence at that runner or relabel it Enhanced MA. Wiring another
strategy requires reviewed runtime/evidence identity work after qualification;
the existence of RealPolicyResolver/adaptive code is not proof this link is live.

Score semantics to preserve during review: SelectionPolicyBuilder subtracts
0.32*median_fold_trades from median_test_return_pct to obtain selection_score /
net_fold_edge_pct. Treat that as a score with an additional cost-floor margin,
not a second measured portfolio return. Verify the underlying return/cost units
before comparing it with raw net returns; do not silently retune the floor.

Input metadata warning: historical parallel protocols bind7946 bars extending
past the registered holdout boundary. Their measured windows are earlier; this
alone does not establish look-ahead contamination. New proposals must bind an
exact pre-holdout dataset (the diagnostic uses existing6795-bar bytes). Planner
timestamp counting finds6796 pre-boundary opens in the broader file; that is
not permission to add a bar to the immutable6795-bar input or claim equivalent
input identity. No holdout price column was read by the revised planner.

### Integration update — 2026-10-14

Đọc [integration handoff](CAMPAIGN_EVIDENCE_HANDOFF.md#integration-handoff--2026-10-14-validation_pending)
và [tracker](../CAMPAIGN_STATUS.md) trước các baseline bên dưới. Nhánh
`codex/calibration-integration-20261014` đang gom code/tests/docs; verification
trên commit mới còn pending. Snapshot pre-commit32bfdb61… có80scoped tests và
AC15PASS, không phải full repo suite/independent sign-off/market approval.
Giữ ADX25 calibration, grid30 research; không chạy campaign/holdout/publish/live.
Các đoạn "Latest integration" ngày10/10 phía dưới là historical handoffs.

### Kế hoạch điều phối hiện hành — 2026-10-10

**Latest integration (đọc trước các handoff lịch sử bên dưới):**
[Canonical validation-only repair](CAMPAIGN_EVIDENCE_HANDOFF.md#canonical-validation-only-repair--2026-10-10-local_verified_review_pending).
Source `139392686d1868b8cecc55efa50b8965020664a404d980f4603cf609881f90f3`,
dirty `dbb9713`; packet `data/validation_only_fix_20261010/`.375scoped tests và
AC01–AC15 PASS trên cùng source/config/test identity; registry425rows và các
packets cũ giữ nguyên. Real supervised synthetic demo PASS/ABSTAIN, không alpha.
Validation-only đi qua canonical fold/window selection và trial accounting,
exact raw coverage, bounded worker session và cleanup; không qualification/OOS/
holdout/sensitivity/live. Các packets validation cũ vẫn INCOMPLETE, không relabel.

Thứ tự bắt buộc: independent raw/negative review → clean integration → freeze
và reverify source/protocol mới → operator duyệt representative calibration
scope/budget → quyết định market pilot. Hiện tool chạy **full inner-validation**,
không thay thế calibration10cells early/late được đề xuất; không giảm grid/folds
để vừa timeout rồi tuyên bố full coverage. Lịch sử diagnostic attempts chưa nối
registry phải được reconciliation riêng nếu dùng cho research burden, không
backfill metric/score hoặc reset DB/search family. Chưa chạy market evaluation,
không tự mở holdout/publish/live; xem authority final verification và post-doc
guard trong handoff trước khi giao việc tiếp.

**Previous integration:**
[Bounded profiling handoff](CAMPAIGN_EVIDENCE_HANDOFF.md#bounded-profiling--2026-10-09-local_verified_review_pending).
Packet `data/synthetic_profile_completion_20261009/`,source
`8e539afd9cd5983129339ea2ced310eb2d516c96338424362ba1bbf4b6d31eb4`:
351scoped tests +AC01–AC15PASS,identity/registry/old packet guardsPASS.
5strategy synthetic-only simulator profiles có bounded supervisor và full
CPU/RSS/wall/trace receipts. **Không representative calibration/ETA/alpha proof**:
3presets0closed trades,1h300bars không đại diện pilot4h,không chọn strategy từ
timings. Reviewer/integration còn pending; market calibration/pilot cần duyệt
budget và canonical accounting, không tự tăng timeout/reset registry.
Refreeze protocol/budget/evidence sau integration; handoff/source phía dưới
là historical. Không publish/live hoặc tắt SQLite durability từ profile này.

**Previous integration:**
[Reuse integration closure](CAMPAIGN_EVIDENCE_HANDOFF.md#reuse-integration-closure--2026-10-09-local_verified_review_pending).
Packet `data/reuse_integration_completion_20261009/`, source
`96a103082d157bc744fc92153402f6dafe24bd9cde2060b0f0906247630f2997`.
Dispatch subject reuse đã nối và được kiểm strict; semantic timestamp bug và
unhashed protocol field đã xử lý; generic --no-publish không activate policy.
Final19scoped modules +AC01–AC15 PASS, cùng source/config/test identity;
không full repository suite/reviewer acceptance/live-ready.
Next owner order: independent raw review → clean integration → bounded
validation-only/synthetic-mechanics profiling proposal → operator-approved
canonical market pilot. Pilot BTC4h5OHLC có1,269inner +outer≤32 +sensitivity≤219,
**không** là ETA hay quyền tự chạy. Lưu output mới/identity/terminal status,
không hạ threshold hoặc đổi trial accounting để tạo PASS.

Phần này điều khiển thứ tự công việc tiếp theo; các baseline/đề xuất bên dưới
là lịch sử hoặc tham khảo, không phải chứng nhận current revision. Mục tiêu:
hệ thống cá nhân nhỏ, tái lập được, paper hoạt động thật và chỉ live khi cả
research lẫn execution đủ bằng chứng. Không mở rộng tính năng để thay bằng chứng.

Trạng thái xác nhận: remote/master đã nhận `dbb9713` (9file vận hành/data
binding/test); còn nhiều thay đổi local chưa tích hợp. Vol target BTC4h chạy
xong nhưng không đạt gates. Runner live vẫn ràng buộc Enhanced MA. Không có
quyền execute/mainnet từ tài liệu này. Funding/universe còn thiếu binding.

#### Handoff P2 trial-history — cập nhật2026-10-09, AWAITING_REVIEW

**Superseding current v2 implementation:**
[Evaluation identity v2](CAMPAIGN_EVIDENCE_HANDOFF.md#p2-evaluation-identity-v2--2026-10-09-awaiting_review).
Source `96392d372fd5b520388ff84bfe9bf7970c702e3f29ccca36528766dce43368c6`.
Logical candidate không đổi, evaluation theo protocol/context append mới và
idempotent replay; legacy giữ không reuse. Raw/effective burden mở rộng theo
candidate×context +legacy, DSR direct/summary lấy cùng registry accounting.
Hai BTC4h jobs hết collision blocker cho fresh v2 execution, **không** trở thành
qualified/reusable. Audit50job/310folds/12,195planned inner cells,0evaluations mới;
registry425rows không đổi. Final251scoped testsPASS/14modules và AC01–AC15:15PASS;
Ruff/diff checkPASS, source/environment không đổi; chưa independent acceptance.
Next:independent review/clean integration, per-subject exact reuse dispatch và
representative profiling trước market pilot có budget. Chưa full trace/multi-
campaign concurrent verdict-census/power-loss proof, không mainnet.
Phần guard/blocker/thiết kế chưa implement bên dưới là lịch sử trước contract v2.

**Đọc cập nhật mới nhất trước các baseline bên dưới:**
[P2 trial-history guard/catalog audit](CAMPAIGN_EVIDENCE_HANDOFF.md#p2-trial-history-preflight--2026-10-09-awaiting_review).
Source hiện tại `7b26764fb2eee94fc088a08e8b8b6e083f99b2b73e38369159b73e49029dcfad`.
50job OHLC đã preflight;48 không có history trùng, Trend Pullback/Vol Target
BTC4h bị chặn trước evaluation do152rows khác commit/thiếu context.425rows
operational registry giữ nguyên; không campaign thị trường mới. Guard chỉ kiểm
context, không chứng nhận metrics/OOS/alpha; full atomic append vẫn authoritative.

**Thứ tự giao việc tiếp theo:** registry owner/reviewer chốt versioned execution
identity tách khỏi logical candidate, giữ legacy/failed history và trial burden;
exact replay idempotent, metric conflict cùng execution vẫn fail-closed. Không
reset DB, đổi evaluator/search-family tùy tiện hay gắn context giả cho legacy.
Sau accounting/AC proof mới nối per-subject reuse vào orchestrator, tích hợp
clean revision và profiling đại diện. Chưa implement contract version mới,
chưa đóng P2/research qualification/mainnet. Evidence/tests/source binding xem
handoff mới nhất; các con số và source hashes bên dưới là lịch sử.
Verification cùng source:230scoped testsPASS/13modules và AC01–AC15:15PASS;
Ruff/diff checkPASS, source/environment không đổi. Chưa reviewer độc lập.

#### Handoff triển khai P1 — cập nhật2026-10-09, AWAITING_REVIEW

Đã sửa trong entry point catalog hiện hữu `scripts/strategy_research/run_campaign.py`
và wrapper `scripts/run_wfo_parallel.py`; không tạo pipeline mới. Wrapper luôn
truyền selection contract validation>=30trades và Sharpe>0 vào canonical WFO;
native OOS thresholds không đổi. Yêu cầu exact input binding và output mới;
không cho phép wrapper này mở holdout. Planner dùng parameter-resolved history,
purge/embargo và holdout metadata cùng cấu trúc canonical.

Protocol trước chạy ghi spec/grid/cost/history/folds/input bytes/source dirty
fingerprint và expected inner-cell inventory. Trước xuất summary phải đối chiếu
candidate reports, exact validation tuples và selected outer folds. Orchestrator
kiểm strategy/pair/timeframe đủ và không trùng, đối chiếu summary với WFO decision
được hash, kiểm report inventory; không nhận summary legacy như protocol mới.
Thiếu input hoặc funding/universe chưa bound thì preflight báo BLOCKED_DATA;
execution từ chối trước batch. Strategy FAIL khác execution/coverage INCOMPLETE.

Lượt2026-10-09 bổ sung cadence đúng1h/4h cho quality/simulator/bridge và không
che source out-of-order bằng sort trước audit. Freeze sensitivity rule và
requests trước rerun; raw stress/neighbor receipts ở thư mục riêng, source-bound.
Coverage bao gồm planned inner, selected outer và toàn bộ supplemental reruns;
thiếu/failed/tampered cell không được aggregate như mẫu đầy đủ. Stress bắt đầu
flat tại outer boundary như primary OOS; axes2values có adjacent-neighbor tests.
Không hạ native thresholds; historical packets không tương đương protocol mới.

Verification mới:226focused regressionsPASS (12modules,191.49s); integrated
AC01–AC15PASS;10frozen OHLC subjects qualityPASS,source_unchanged=true. Source
fingerprint `f0718a321afcbc3e40204047d61a8d611559bba8624beb88b36eadf9dbf2d096`.
Mẫu BTC4h chỉ preflight:6folds×4params×3cost=72inner cells,
0new evaluations.12validation candidates của fold đầu packet cũ đều không đạt
contract hiện hành→ABSTAIN. Xem [handoff và lệnh tái lập mới nhất](CAMPAIGN_EVIDENCE_HANDOFF.md#p1-cadencesensitivity--2026-10-09-awaiting_review).

Không đánh dấu toàn P0/P1 ACCEPTED: chưa reviewer độc lập, chưa clean integrated
commit và còn dependencies local/untracked. Supplemental inventory mới được
synthetic-test và code-verify, chưa actual market qualification theo protocol mới;
funding/cross-asset và environment/effective runtime config binding chưa đóng.
Các legacy entry points khác chưa được chứng nhận tương đương protocol này.
Next: integration owner đóng dependency/commit boundary; reviewer kiểm source,
raw evidence và complete scope trước P2 profiling/batch mới. Lượt test chỉ ra
3measurement-window tests chiếm~94% runtime; đo campaign riêng, không suy diễn
đây là bottleneck thật hay bỏ slow checks khỏi acceptance.

#### Handoff P2 execution/profile — 2026-10-09, AWAITING_REVIEW

Đã sửa wrapper timeout/retry isolation và giao diện callable outer; lưu receipts
inner/selected outer trước return, giữ fault và deterministic result ordering.
Final scoped86testsPASS; integrated AC01–AC15 chạy lại; packet mới
`data/research_runtime_p2_20261009/`. Source final fingerprint
`813226afad26c334d7110fef8bbf7cfb929c0f4fc337597a606ef16bd870a3f6`.
Broad authority regression có lượt TIMEOUT120s, không full-suite PASS.
Xem [evidence/diagnostic và lệnh tái lập](CAMPAIGN_EVIDENCE_HANDOFF.md#p2-executionprofile--2026-10-09-awaiting_review).

Profile300bars synthetic (source trước final callable binding): SQLite commit
~66.8%, fsync~14.1%, schema~10.4% exclusive; signal0.563s cumulative. Đây là
single-cell diagnostic có test overlap, không production ETA/speedup. Chưa đổi
SQLite durability hay tăng concurrency. Next P2-A đo isolated representative
windows/I/O/memory trước tối ưu; P2-B exact-key resume vẫn chưa enable qualification.
Cần kill/restart, trial-count/frozen-selection idempotence, source/data/config/
cost/fault/window mismatch tests và raw-ledger parity. Full P2 INCOMPLETE;
integration/reviewer/P1 environment binding vẫn là dependencies, không bị bỏ qua.

#### Handoff P2 cache v2 — 2026-10-09, AWAITING_REVIEW

CampaignCellRunner đã có exact-identity warm resume và cold import vào packet
disjoint mới: source/data/plan/environment/stage/window/cost/fault binding,
raw report SHA/config/typed reconstruction; matching cache invalid fail-closed.
Attempt UUID riêng và atomic no-replace publication giữ evidence cũ; genuine
child-kill trước/sau publication tests PASS. Final115testsPASS/9modules/26.27s,
integrated AC01–AC15:15PASS; demo1real simulator cell trên300synthetic bars có
warm/cold hit, raw-byte/metric/health parity, source packet unchanged.
Packet `data/research_resume_p2_20261009/`; source fingerprint
`5a86038372d9bf6d9804c816a155c84bd5a771844dfc8ba56ab41a55d86d7050`.
BTC4h preflight vẫn6folds/72inner cells/0evaluations/historical ABSTAIN.
Xem [contract/evidence/limits](CAMPAIGN_EVIDENCE_HANDOFF.md#p2-cache-v2--2026-10-09-awaiting_review).

Đây là foundation, không đóng P2-B: catalog qualification wrapper chưa bật
reuse; cần adapter stages validation/OOS rõ ràng, effective full config/history
và protocol binding, idempotent trial-count/frozen selection, gates/coverage sau
reuse. Reviewer kiểm cùng revision và negative cases; runtime owner bổ sung
concurrent writers/partial cold-import/parent-kill recovery. Performance owner
đo isolated representative I/O và chi phí source/data hashing; chưa thay live
durability. Broad authority TIMEOUT120s vẫn OPEN; chưa full suite/trace parity,
power-loss proof hoặc market qualification. Full P2 INCOMPLETE/live NO-GO.

#### Handoff P2 qualification cold recovery — 2026-10-09, AWAITING_REVIEW

Catalog worker `run_wfo_parallel.py` đã nối cache cho validation/selected OOS,
thêm `--reuse-from` với destination mới và exact frozen protocol. Source outer
cache cần decision/freeze nguồn hợp lệ; sensitivity vẫn rerun và được kiểm raw
coverage. Registry replay kiểm full metadata/metric atomically, qualification
trial bind protocol/source/runtime; conflict/legacy history từ chối, không reset.
Per-cell OS lock và UUID import recovery xử lý multi-process và interrupted-copy.
Environment allowlist/config-file SHA/actual loaded protection-sizing-risk và
loader input binding được kiểm; measurement capital được rebased đúng warm-up.
Final main143testsPASS và AC01–AC15:15PASS; authority13testsPASS sau sửa fixture
registry/callback isolation. Actual measured synthetic demo/raw parityPASS;
final source fingerprint `7b489f460c24da1012bfee5a1d6711c3ad49cb9d0d6c6b40f447ff062705713e`.
Readonly registry425rows chưa có protocol-context; không tự reset để chạy qua
conflicts. Combined WFO regression TIMEOUT120s được giữ; measurement fixtures
đã bind synthetic input cho child; WFO unit rerun64PASS/146.98s. Total220distinct
scoped casesPASS, không full-repository suite; final source/config QA khớp protocol.
Xem [contract/evidence/limits](CAMPAIGN_EVIDENCE_HANDOFF.md#p2-qualification-cold-recovery--2026-10-09-awaiting_review).

Next reviewer/integration: kiểm clean dependency boundary cùng final source,
registry history compatibility và CLI-to-WFO coverage. Catalog orchestrator chưa
có subject-map reuse dispatch; dùng worker CLI trực tiếp, không suy orchestrator
resume đã xong. P2-A isolated representative I/O/worker scaling/full trace parity
vẫn OPEN; chưa đổi SQLite durability, chưa power-loss/Windows QA. P3 chỉ bắt đầu
sau protocol/source/history review, không hạ thresholds để tìm winner. Full P2
chưa independent ACCEPTED; nghiên cứu hiện vẫn NOT_QUALIFIED/live NO-GO.

#### Phân công và dependency (work packages)

Mỗi hàng là work package; chia nhỏ nữa nếu vượt một revision/test boundary.
Agent nhận việc phải đọc AGENTS.md và contract liên quan, claim file trước sửa.
Không tự spawn hay nhắn task khác chỉ vì tài liệu liệt kê vai trò.

| ID / owner đề xuất | Việc cốt lõi / phạm vi | Đầu ra và điều kiện nghiệm thu | Phụ thuộc |
|---|---|---|---|
| P0-A / integration | Phân loại dirty tree: code hoàn thành, thử nghiệm, docs, data/state; map dependency | Danh sách commit candidates và exclusions; không gom data/state; tree/revision đã test khớp bản bàn giao | Không |
| P0-B / reviewer | Nghiệm thu integrated revision và ledger mẫu | Reproduce positive + negative checks; AC01–AC15 nếu chạm gate files; source fingerprint, dependencies và logs đầy đủ; test pass không thay alpha | P0-A |
| P1-A / research | Chốt entry point có thẩm quyền; catalog→canonical IDs; grid/history/cost/timing và protocol | Một manifest versioned cho từng subject; mismatch fail-closed; legacy/new selection thresholds không trộn ngầm | P0-B |
| P1-B / evidence | Coverage chính xác strategy/pair/timeframe/params/cost/fold/window | Expected-vs-observed inventory; thiếu/trùng/sai window/subject đều FAIL; bỏ kiểu chứng minh chỉ bằng tên strategy/count | P1-A |
| P2-A / performance | Profiling signal/simulator/I/O/worker; runtime budget trước chạy | Timing breakdown, chi phí dự kiến và giới hạn; tối ưu chỉ sau đo; parity trên signal/fill/ledger và negative tests | P1-A |
| P2-B / runtime | Durable per-cell results, resume, termination/restart | Kill/timeout/restart có kết quả và trace; chỉ reuse exact identity; không chạy lại cell hoàn thành hoặc bỏ cell lỗi khỏi mẫu | P1-B, P2-A |
| P3-A / researcher | Screening các họ Spot phù hợp rồi shortlist theo protocol preregistered | Baseline provenance, tất cả trial burden, shortlist có lý do và sample feasibility; không chọn theo đã thấy OOS | P1, P2 |
| P3-B / reviewer | Nested OOS qualification, stress và stability | Raw ledger + metric derivation + native verdict; PASS/FAIL/INVALID riêng; không đủ thì NO_TRADE, không ép tìm winner | P3-A |
| P4-A / execution | Nối promoted policy vào paper/runtime; tránh Enhanced MA fallback | Same strategy/params/history/timing/sizing/exits; missing/expired/mismatched policy→NO_TRADE; replay parity | P1-A; qualification cho policy thực |
| P4-B / operations | Paper với data mới; restart/stale/dup/partial/cancel/stop/NAV reconciliation | Soak manifest có coverage và events thật; faults có evidence; testnet chỉ chứng minh kỹ thuật, không lợi nhuận | P4-A |
| P5 / operator | Canary Spot một pair, vốn/loss/exposure và stop rules | Operator chốt budgets, credential scope, venue minima; research + operational acceptance + execute approval riêng | P3-B, P4-B |

P4 có thể phát triển/test bằng fixture hợp lệ song song P2/P3, nhưng fixture
không cấp quyền live và không giả làm alpha evidence. Funding/cross-asset không
được chạy bằng OHLC Spot đơn pair hoặc placeholder funding/universe. Không tự
giảm thresholds để cứu kết quả. Không đổi runtime account/config của operator.

#### Quy trình làm việc chung

1. Đọc tracker và logs mới nhất; xác nhận không có campaign dùng source đang sửa.
2. Claim ID/files/interfaces; nêu mục tiêu, reproduction và điều không làm.
3. Chụp revision + dirty fingerprint + data/config/protocol hashes trước chạy.
4. Làm một thay đổi có tiêu chí kiểm; ghi controlled results theo AGENTS.md.
5. Kiểm positive và negative độc lập; packet cũ bất biến, không backfill như thể
   metadata đã tồn tại từ trước. Trạng thái RUNNING phải kiểm process lẫn logs.
6. Reviewer đọc raw artifacts, không chỉ dòng VERIFIED hoặc summary PASS.
7. Bàn giao AWAITING_REVIEW; chỉ ACCEPTED khi đúng scope và có reviewer evidence.
   FAIL strategy không đồng nghĩa FAIL arithmetic; process rc0 không phải alpha PASS.
8. Sau campaign dừng và verification đạt mới commit/merge theo quyền user;
   push/deploy/execute là hành động riêng. Không tự dọn dirty tree của người khác.

Mẫu claim/handoff bắt buộc:

```text
Task ID / owner / reviewer / trạng thái:
Files claimed / interfaces affected / dependencies:
Revision + source fingerprint; dirty changes và exclusions:
Input byte hash / quality hash / cutoff / protocol/config hashes:
Failure reproduction trước sửa; hypothesis và bằng chứng:
Commands + controlled result paths; tests PASS/FAIL/SKIP và lý do:
Metric definitions / nguyên liệu raw / expected-vs-observed coverage:
Negative controls; limits của bằng chứng; invalidation/rollback:
Research verdict / operational verdict / live authority (tách riêng):
Next action / owner / approval cần thiết:
```

#### Mẫu đã thực hiện: P0-B arithmetic audit (scope hẹp)

Xem [handoff mẫu](CAMPAIGN_EVIDENCE_HANDOFF.md#mẫu-agent--audit-arithmetic-vol-target-2026-10-08).
Packet `data/live_readiness_example_20261008/` có auditor độc lập dùng stdlib,
controlled result và JSON kết quả. Không import metric implementation của dự án.
Kiểm 6outer reports bằng path từ artifact, không glob chọn report có số đẹp.

Kết quả: input byte hash khớp binding; 6fold IDs/window không trùng/không overlap;
return và drawdown tái tính từ equity; trade P&L từ quantity/fill prices/fees khớp
ledger; aggregate arithmetic khớp. Negative control tăng median return1 điểm
phần trăm bị reject. Selected validation trades `[5,5,5,5,5,4]` cho thấy wrapper
legacy không chứng minh contract inner-validation>=30 của luồng mới.

Verdict mẫu: **arithmetic PASS / research NOT_QUALIFIED / live NO-GO**.
Chưa chứng nhận source dirty lịch sử, Sharpe/DSR/PBO, complete grid/stress coverage,
parquet quality hay current integrated revision. Không được biến mẫu này thành
full P0-B ACCEPTED; owner tiếp theo phải đóng các phạm vi còn thiếu.

#### Phạm vi không làm trong lượt tiếp theo

- Không thêm framework research mới, strategy mới hoặc tăng số pair cho đủ sample.
- Không tiếp tục batch lớn trước P1/P2 preflight và budget.
- Không adaptive switching live, futures/leverage, LLM order authority.
- Không tuyên bố alpha/current revision được chứng nhận từ các packet cũ.
- Ba lựa chọn còn cần operator: venue/scope Spot-futures, vốn và loss budget tuyệt
  đối, thời gian giám sát. Chưa trả lời thì chỉ research/paper không account IO.

### Bối cảnh lịch sử theo operator — 2026-10-07

Operator yêu cầu tập trung strategy/config cho đơn máy và dữ liệu craw hiện
có, gác workstream license/provider. Không đặt LICENSE_APPROVED như một gate
code mới; việc gác kiểm tra không phải xác nhận quyền dữ liệu. Không mua/tải
nguồn mới trong workstream này. Giữ data technical quality/cutoff/causality và
live safety gates; đơn máy không được miễn reconciliation, risk hay evidence.

Các điểm đã kiểm bằng code:

- Research hiện tại: ma_crossover BTC4h;9 candidates của fold đầu đạt3–5
  validation trades, dưới30, nên ABSTAIN; không có OOS qualification.
- Runner `live_enhanced_ma_binance.py` vẫn định danh enhanced_ma cố định,
  không tiêu thụ tùy ý ma_crossover winner; phải khớp promoted artifact.
- `config/wfo_config.json` là legacy profile5pairs/1h+4h, mintrades10,
  commission0.0005/slippage0.0002; không tương đương campaign hiện tại
  minOOS30/commission0.001/slippage0.0005. Không gọi đây là active approved
  live profile; không âm thầm sửa cấu hình đang dùng của operator.
- Runner mặc định3symbols/weights4,3,3; không nên dựa defaults để triển khai
  canary1pair. Example YAML bật futures dù canary scope Spot; phải kiểm
  effective runtime configuration, không giả định example được dùng.

Workstream tiếp theo, theo dependency:

1. Audit **effective params/history** enhanced_ma ở grid và runtime (bài học
   lỗi MA slow60/80/120 thiếu history). Không chuyển sang thử enhanced_ma
   trước khi chứng minh indicators/signal không bị thiếu lookback.
2. Chốt candidate/profile nhỏ: đề xuất BTC/USDT Spot1h, enhanced_ma làm
   incumbent vì tương thích runner, không mặc định winner; ma_crossover làm
   comparator chỉ sau có đường admission/runtime parity phù hợp. Strategy
   không đủ evidence phải FLAT/ABSTAIN, không fallback về incumbent không đạt.
3. Dùng cùng effective params/history/cost assumptions/candle timing giữa
   research, paper và live. Thu nhỏ grid được preregister, ưu tiên audit
   signal/trade coverage trước backtest dài; không tăng pairs để cộng sample.
   Không tự giảm validation30/minOOS30 theo kết quả3–5trades vừa thấy.
4. Qualification trên local pre-holdout được phép; mọi kết quả đủ3 trạng thái
   PASS/FAIL/INVALID. Net costs, sensitivity, stability, selection bias và
   exact plan/source/ledger proofs giữ nguyên. Research PASS không mở execute.
5. Paper đơn máy: closed-bar timing, stale-data, restart recovery, duplicate
   order prevention, partial/cancel/stop, reservations và position/NAV reconcile;
   replay cùng signals phải khớp forecast/order decisions của backtest.
6. Canary profile đề xuất1pair, long-only Spot, không futures/leverage/LLM
   trong đường quyết định lệnh. Vốn/exposure/order/daily-loss/drawdown budgets
   phải được operator duyệt theo account thực và exchange minimums; không
   tự đặt con số hoặc sửa mainnet limits. Tách state/audit/credentials khỏi paper.
7. Chỉ xin execute thật sau evidence+operational acceptance và quyền riêng;
   cần kill switch/cancel+protect/reconciliation và stop criteria rõ. Không
   tự dùng `--execute` hay `--confirm-live` khi chỉ được yêu cầu sửa code.

Exit tối thiểu của bước1–3: profile manifest duy nhất chỉ rõ strategy/pair/
timeframe/params/history/costs và evidence, parity test PASS, không còn drift
với live runner. Tiếp theo agent phải sửa/kiểm strategy history và config
mapping, không quay lại provider-license research khi operator chưa yêu cầu.

Mục tiêu gần nhất: một luồng Binance Spot long-only 1h, 1–2 pairs được operator
chọn, dùng policy đã được approve cho từng pair và đóng băng trong kỳ đánh giá.
Không mặc định Enhanced MA là winner. Không mở futures, margin, funding carry,
exchange mới hoặc adaptive 10 pairs trong workstream này.

Đây là **phạm vi đề xuất**; cần operator chốt ở mục 3 trước campaign/soak ngoài máy.
Không được dùng canary phạm vi nhỏ để tuyên bố toàn bộ adaptive project đã nghiệm thu.
Policy cố định vẫn phải đáp ứng risk/authority/calibration/OOD/regime contracts hiện
có; không được tạo profile đơn giản bằng cách bỏ các gate đó.

Đọc theo thứ tự:

1. `AGENTS.md` tại root và instructions tại thư mục làm việc nếu có.
2. [Core System](../CORE_SYSTEM.md), [Architecture](../ARCHITECTURE.md).
3. [Production Policy](../PRODUCTION_POLICY.md), [S7 operational evidence](../S7_OPERATIONAL_EVIDENCE_RUNBOOK.md).
4. Tài liệu này để chọn task, dependency, evidence và trình tự review.
5. [Main-flow validation](MAIN_FLOW_VALIDATION.md) và contract liên quan tới task.

Tài liệu này là tracker duy nhất cho **workstream L0–L4**. Các TODO/phase report cũ
chỉ để truy vết; không suy ra release readiness từ checkbox cũ. Code/tests/raw
artifacts quyết định behavior, production/S7 contracts quyết định admission.
Nếu các nguồn mâu thuẫn: giữ NO-GO, ghi decision record và yêu cầu owner giải quyết;
không chọn tài liệu có ngưỡng dễ hơn. Không tự sửa ngưỡng trong plan để hợp thức hóa code.

## 2. Baseline và vấn đề phải đóng

Baseline là snapshot, không phải kết quả tự động áp dụng cho revision tiếp theo.
AC01–AC15 đã PASS tại SHA trên; hai nhóm regression kiểm tra ngày 04/10 có
249 PASS, 9 SKIP. Đây không phải full suite hoặc exchange acceptance mới.
Artifact baseline có thể chỉ có trong workspace gốc, không đi cùng Git clone.
Nếu không truy cập được, ghi NOT_AVAILABLE và chạy verification phù hợp để tạo
bundle mới; không coi lời ghi trong tài liệu là evidence đã kiểm tra độc lập.
Runner/evidence/ops vẫn cần các đầu việc sau:

| ID | Vấn đề baseline | Đường kiểm tra | Work package |
| --- | --- | --- | --- |
| B01 | BUY producer chưa nối đầy đủ risk decision, planning, permission context | `build_decisions`, `prepare_orders`, `execute_orders`, `_submit_live_order` trong [Binance runner](../../scripts/live_enhanced_ma_binance.py) | L0.1–L0.3 |
| B02 | Sáu legacy runner tests bị skip; testnet stop acceptance còn call signature cũ | [Runner tests](../../tests/test_binance_live_runner.py), [testnet acceptance](../../tests/test_binance_testnet_acceptance.py) | L0.3 |
| B03 | Soak tổng hợp theo symbol; failed runs, duplicate/unrelated events có thể tạo false PASS | [Soak tracker](../../scripts/testnet_soak_tracker.py) | L1.2 |
| B04 | Cost floor/selection score cần thống nhất net/gross, turnover/notional và margin | [Live safety](../../src/trading_agent/execution/live_safety.py), [policy builder](../../src/trading_agent/research/selection_policy.py), [generator](../../scripts/generate_live_strategy_evidence.py) | L1.1 |
| B05 | Chưa có published `data/live_strategy_evidence.json`; promotion store rỗng tại baseline | Local-only: `data/live_strategy_evidence.rejected.json`, `data/campaign_evidence_9month/campaign_summary.json`, `data/campaign_evidence_sa/campaign_summary.json` | L2 |
| B06 | Payoff script thiếu per-trade records; break-even constant sai; snapshot handover khác artifact | [Payoff analysis](../../scripts/analyse_payoff_distribution.py), root `HANDOVER_2026_10_03.md` | L1.1, L1.4 |
| B07 | Dữ liệu hourly có nhiều phiên bản, cutoff/gaps khác nhau; chưa đủ release-bound ops evidence trong workspace đã kiểm tra | `data/raw/binance/`, [live TODO](../LIVE_TRADING_TODO.md) | L1.4, L3 |

Agent phải xác nhận lại B01–B07 trên revision nhận việc; ghi RESOLVED nếu có
evidence mới, không triển khai lại một sửa chữa đã tồn tại. Không khẳng định máy
VPS/remote CI thiếu evidence chỉ vì workspace local không có.

## 3. Quyền hạn và decision record bắt buộc

| Hành động | Quyền mặc định |
| --- | --- |
| Đọc code, sửa trong task được giao, local tests với fake broker/local data | Theo yêu cầu giao việc và `AGENTS.md`; bảo toàn thay đổi người khác |
| Fetch market data, kết nối account/API, paging ra ngoài, testnet/demo orders, deploy, push/PR | Cần người dùng/operator cho phép rõ scope trước khi làm; task code không tự cấp quyền này |
| Mainnet order, thay loss budget, tăng vốn, đổi API permission, đổi release policy | Operator approval riêng; không suy ra từ “hoàn thiện hết”, AC PASS hoặc deploy PASS |
| Xóa/quarantine/overwrite dữ liệu, holdout, policy, state/ledger | Hỏi trước; ưu tiên recoverable, xác định target và backup; không reset history |
| Spawn/delegate agent | Chỉ khi instructions/quyền hiện hành cho phép; việc đọc plan không tự cấp quyền delegation |

Trước L2/L3, owner ghi decision record D01 với:

- Exchange/market type, pairs, timeframe, deployment profile, account alias đã redacted.
- Vốn thử nghiệm, absolute loss budget, per-order/per-symbol/gross limits và dust policy.
- Data sources, cutoff, evaluation horizon, holdout chưa bị dùng và research trials budget.
- Search space/strategy eligibility, gate version, cost schedule và benchmark.
- Policy/parameter freeze, lịch refresh evidence, điều kiện expiry/drift/retraining.
- Quyền kết nối ngoài máy, môi trường được phép, người duyệt và thời hạn authorization.

Thiếu lựa chọn material: dừng task phụ thuộc, báo rõ quyết định cần chốt; vẫn có thể
làm diagnostic/local tasks không phụ thuộc. Không tự đặt mức vốn hay giả lập approval.

## 4. Invariants không được phá

1. Một đường order canonical; không thêm direct broker submit cho BUY/stop/cancel.
2. BUY cần risk decision + planner output + permission context hợp lệ, bound cùng
   policy/config/instrument/intent. Không tự tạo evidence/calibration/regime placeholder.
3. Thiếu/expired/untrusted evidence phải chặn tăng exposure. Risk-reducing exit/protection
   chỉ tiếp tục khi inventory, permission và safety authorization cho phép; không
   bypass hard kill switch. Entry lock, reduce-only và hard kill là các trạng thái khác nhau.
4. UNKNOWN/timeout không đồng nghĩa failed. Query/reconcile trước retry; ambiguous
   inventory/order state chặn tăng exposure và chuyển incident/manual intervention.
5. Exchange fills/fees và ledger xác định cash, inventory, PnL; không dùng intended qty
   hoặc equity phẳng thay bằng chứng fill. Duplicate/reordered events không tạo PnL mới.
6. Single leader trên **account + environment**, không chỉ một `state_path.lock`.
   Hai state paths cho cùng account không được tạo hai writers; backup restore không
   reset loss high-water mark hoặc idempotency history.
7. Chỉ closed bars; kiểm timestamp UTC, freshness, gaps, duplicates, quote/depth và
   instrument rules tại submission. Không nội suy tùy ý để biến dữ liệu thiếu thành đủ.
8. DATA/STRATEGY/POLICY/EXECUTION/CONFIG identities phải khớp. Fixture được phép để
   test behavior nhưng không đủ để promote REAL_MARKET hoặc đóng ops soak.
9. Không hạ gate, giảm costs, đổi denominator/fold layout, loại loser hoặc reuse holdout
   sau khi xem outcome để candidate PASS. Thay contract cần decision + review + rerun.
10. Không in secrets, private account IDs hoặc raw authenticated payload vào log/docs.
    HMAC/signature không thay thế source provenance và phép đo thực.
11. Chưa có candidate đạt gate là kết quả research hợp lệ; không ép tạo ACTIVE policy.
12. Mainnet default NO-GO; deployment không được tự enable trading hoặc scale vốn.

## 5. Dependency và cách nhận việc

```text
L0: runnable vertical slice ─┐
                           ├─ L2: qualifying policy ─┐
L1: trustworthy evidence ──┘                         ├─ integrated review ─ L4 canary
                         └─ L3: operational proof ──┘
```

L1 có thể chuẩn bị cùng L0; chỉ mở operational soak sau khi L0 và phần L1 liên quan
đã review. L2 và L3 có thể song song, nhưng policy-specific shadow evidence phải
gắn policy đã freeze. Ops campaign phục vụ execution không được biến thành alpha
evidence hoặc coi policy khác nhau là cùng một subject.

Trước mỗi task: đọc tracker, `git status`, diff liên quan; claim owner/files; xác nhận
dependency và approval. Một writer cho một file có rủi ro overlap. Trên working tree
chung, serialize edits/runs có shared output; không chạy nhiều AC producers ghi cùng
`/tmp/acNN_evidence.json`. Có conflict thì phối hợp, không ghi đè.

Vòng làm việc: reproduce failure → test đúng failure → sửa tối thiểu → positive và
negative tests → integrated check → evidence bundle → reviewer → cập nhật tracker.
Reviewer khác tác giả; nếu không có reviewer, để `AWAITING_REVIEW`, không tự nghiệm thu.

## 6. Work packages và tiêu chí hoàn thành

### L0 — Hoàn thiện đường thực thi

| Task / owner role | Deliverable | Điều kiện PASS |
| --- | --- | --- |
| L0.1 / execution | Map producer→consumer cho risk/target/authority/planner/permission, gồm identity và failure reason | Map chỉ rõ dependency thật trong code; không thiết kế luồng parallel bypass canonical |
| L0.2 / execution | Nối runner với producers hợp lệ; entry point dùng dependency injection cho fake broker | Valid admission tạo BUY submit/fill; missing/stale/mismatched admission tạo 0 broker submits; planner qty đúng normalized venue qty |
| L0.3 / execution + QA | Update sáu legacy tests và testnet signature; E2E entry-point tests | BUY→fill→stop, partial/unknown, cancel-fill race, protective exit, restart/replay và sell-locked inventory được kiểm; critical local cases không SKIP/XFAIL |
| L0.4 / runtime + ops | Chứng minh leader/account isolation và persistent state semantics | Same account với khác state paths vẫn không double writer; restore/crash không duplicate submit hoặc reset risk baseline |

L0 evidence: entry-point trace, canonical event ledger, fake broker call log, expected
cash/inventory/fees/PnL bằng independent accounting oracle. Mock chỉ exchange boundary;
không mock risk/planner/permission thành ALLOW. NO_TRADE chỉ chứng minh abstention,
không thay case positive BUY. Testnet opt-in chưa chạy phải ghi NOT_RUN, không PASS.

#### L0 implementation handoff — 2026-10-04 (local, chưa nghiệm thu)

Consumer đã nhận `LiveBuyAdmission(risk_decision, planning, permission_context)` qua
`run` → `run_locked` → `build_decisions` → `prepare_orders` → `execute_orders` →
`CanonicalExecutionService.submit_planned`. CLI hiện có producer mặc định từ các
promotion/artifact stores đã tồn tại; BUY thiếu authority hợp lệ vẫn bị chặn.

Producer đã nối vào Binance runner:

- [RuntimeStrategyResolver.resolve_for](../../src/trading_agent/authority/resolver.py)
  lấy đúng promoted runtime theo symbol/timeframe/environment; không có thì abstain.
- [PromotedLiveAdmissionProducer](../../src/trading_agent/execution/live_admission.py)
  gọi runtime → DecisionAuthority → shared PortfolioAllocator → ExposureAuthority
  → OrderPlanner → permission. Không khởi tạo paper engine và không gửi lệnh.
  Input là OHLCV content-addressed, closed candle, quote receipt thật và portfolio
  reconciled từ broker. Thiếu promotion/calibration/forecast fingerprint thì fail closed.
- Artifact phải khớp code SHA của strategy registry hiện tại. Mainnet còn yêu cầu
  `strategy_artifact_ids` theo đúng toàn bộ universe trong cùng payload evidence đã
  xác minh HMAC/build/metrics. Certificate Enhanced MA không cho phép strategy hoặc
  tham số khác. Đây là runtime binding, không chứng minh policy có alpha.
- [OrderPlanner](../../src/trading_agent/execution/canonical/order_planner.py) tạo
  intent; [permission evaluator](../../src/trading_agent/execution/permission.py) và
  [CanonicalExecutionService](../../src/trading_agent/execution/application.py)
  kiểm lại admission/live truth trước lifecycle authorization và gateway IO.

| Identity | Nguồn / consumer | Quy tắc |
| --- | --- | --- |
| `decision_id`, model ID, forecast fingerprint | Risk decision → planner intent → permission | Phải cùng subject; context giữ đúng risk object; quantity phải khớp sau venue normalization |
| `intent.intent_id` | Planner → canonical lifecycle/replay | Dùng cho submit và reconcile lifecycle, không dùng candle key thay thế |
| `intent.idempotency_key` | Planner → broker client ID/risk ledger | Broker lookup dùng key này; giữ canonical ID trong record để recovery |
| `signal_key` | Pair + side + closed candle → persistent risk ledger | Chặn decision mới tạo thêm order cho cùng candle, kể cả sau restart |

Risk decision phải timezone-aware, không ở tương lai và không quá 3.600 giây trong
runner 1h này. Admission thiếu/sai identity/quantity/evidence bị chặn trước reserve.
Factory `build_canonical_execution_stack` dùng nguồn quote/portfolio giống runner;
không tự tạo receipt timestamp, không coi total inventory là free inventory.

Partial SELL phải xác nhận terminal cancel/filled lookup trước khi đặt stop cho phần
còn lại. Unknown cancel giữ reservation và chặn overlapping stop. Reconcile known
order áp dụng **delta** của cumulative fill, không ghi lại toàn bộ fill khi lookup lặp.
Stop cleanup của acceptance chỉ xử lý order thuộc chính run, qua canonical gateway.

Full `run` E2E local không inject admission: BUY → fill → stop → restart/no duplicate
→ risk exit/SELL; Decimal oracle kiểm cash, quantity, fees và realized PnL. Các case
missing promotion/calibration/fingerprint tạo 0 exchange orders. Full entrypoint còn
kiểm crash sau venue accept/trước durable ACK (reconcile không submit lại), lookup
UNKNOWN (giữ reservation/chặn batch), partial SELL (terminal cancel residual rồi
stop cho inventory còn lại; batch dừng an toàn). Cancel-fill races được kiểm ở
runner/lifecycle layer; không gọi tất cả là full entrypoint crash matrix.

`LiveAccountSession` dùng account-scoped OS lock cộng state-path lock, binding HMAC
và account-scoped event store. Cùng account không được đổi state path để reset
baseline; mất/corrupt state hoặc binding phải recovery, không bootstrap lại. Receipt
monotonic của CCXT được kiểm freshness/latency trước khi chuyển sang UTC.
`LIVE_ACCOUNT_ID` phải là định danh account ổn định do operator xác minh; đổi label,
xóa namespace hoặc chạy nhiều host không được coi là isolation đã chứng minh.
Old state context khác fingerprint bị chặn: cần migration được review, không xóa
ledger để chạy lại. Local tests không chứng minh venue UID hoặc distributed leader.

| Runtime input | Ràng buộc |
| --- | --- |
| `LIVE_ACCOUNT_ID` | Stable venue account UID đã operator xác minh; không dùng API key hoặc run ID |
| `LIVE_SAFETY_HMAC_KEY` | Key hợp lệ cho signed state/binding/evidence; không ghi secret vào docs/log |
| `LIVE_PROMOTION_DB` | Existing promotion database; default `data/promotion_store/promotions.db`; thiếu thì chặn |
| `LIVE_ARTIFACT_DB` | Existing artifact database; default `data/research/artifacts.db`; thiếu thì chặn |
| `strategy_artifact_ids` | Mapping symbol → artifact trong signed mainnet evidence; không thêm ID fixture vào evidence thật |

Không có qualified policy trong registry thì NO_TRADE/fail closed là đúng; không
seed các local fixtures để làm CLI chạy được. Resolver lấy runtime eligible theo
pair/timeframe/environment; wiring này không chứng minh adaptive regime routing
hay chọn strategy tốt nhất về hiệu quả đầu tư.

Còn thiếu để nghiệm thu L0: reviewer độc lập, full-entrypoint crash matrix mở rộng
và xác minh account identity/recovery vận hành. External testnet chưa được chạy.
Risk/evidence IDs trong tests là local fixtures, không được dùng để publish/promote.

Rollback: rollback code/config qua cơ chế được duyệt, giữ ledger/state; nếu inventory
UNKNOWN thì khóa entries và reconcile, không khôi phục snapshot cũ để “xóa lỗi”.

### L1 — Hợp đồng đo lường và evidence đáng tin cậy

| Task / owner role | Deliverable | Điều kiện PASS |
| --- | --- | --- |
| L1.1 / quant + risk | Metric schema/unit: gross/net, realized/MTM, account/position returns, fills/round trips; cost schedule và safety margin riêng | Ledger→PnL→return bridge khớp oracle; đổi position fraction/cost scenario cho kết quả đúng; không double-charge hoặc dùng trade count như 100% equity turnover |
| L1.2 / evidence | Soak evaluator theo unique lifecycle, inventory protection, healthy coverage intervals và identity | Duplicate/unrelated stops, failed-only/dry-run logs, wrong environment/account/SHA, gaps, unresolved orders và unprotected inventory đều không đóng gate |
| L1.3 / safety + runtime | Một admission decision bound release/policy/evidence/approval; kiểm ở runtime, không chỉ generator | Diagnostic flags như `--no-stats-gate` không tạo live-eligible evidence; thiếu/expired/mismatched evidence không vào BUY; safe reduction xử lý theo invariant 3 |
| L1.4 / data + research | Data manifest và truth reconciliation cho các report | Phân loại gaps/exchange halt/missing bars; chọn input version rõ; metrics từ source artifacts, per-trade payoff không lấy fold ratio thay; break-even dùng `(1-p)/p` trên đúng sample |

Cost oracle tối thiểu: zero-cost, known-cost single round trip, multi-leg/partial fills,
3–4% vs 100% deployment, variable notional, portfolio allocations, 1x/2x scenarios,
open inventory cuối kỳ. Tách net expectancy và required safety margin có tên/unit riêng.
Không chọn một con số hardcoded để thay cost schedule thực.

Reproduce B03 bằng failed-only timestamps trong 30 ngày, duplicate fills/stops cùng
symbol nhưng unrelated IDs: baseline có thể PASS; sau sửa phải FAIL có reason cụ thể.
Log synthetic này chỉ là negative test, không nhập vào operational evidence store.

Gate contract phải khai báo threshold, unit, statistical hypothesis, denominator,
empty-fold policy và dependency assumptions. Overlapping folds không được đếm thành
independent trials. Threshold đang tồn tại không tự động trở thành hợp lý, nhưng chỉ
được đổi sau review methodology **trước** campaign, không theo outcome muốn đạt.

Rollback: giữ schema/version cũ và migration có kiểm chứng; không rewrite immutable
evidence cũ. Đánh dấu invalid/superseded, giữ lý do và identity cho audit.

### L2 — Chứng minh một policy đủ điều kiện

Đầu vào L2 phải có campaign-evidence contract đã thống nhất ở L1: immutable run ID;
HEAD + dirty patch/source hashes; config/seed/trials ledger; data manifest và cutoff;
fold IDs/windows/purge/embargo/overlap; cost schedule và net/gross semantics; trade
ledger + continuous OOS equity + metric denominators; gate results/failure reasons;
artifact hashes và replay command. `campaign_summary.json` chỉ có status không đủ.
L1.4 kiểm/reconcile bundle này; L2.2 là producer phải xuất đầy đủ bundle, không chỉ
policy-admission evidence.

D01 phải freeze eligibility, ranking và tie-break trước campaign. Chỉ rank candidate
đạt hard gates và sample/dependence contract; không chọn mặc định Enhanced MA hoặc
chọn winner bằng cách nhìn lại holdout. Fold-clearing rate chỉ là ranking input nếu
đã định nghĩa denominator/dependence và duyệt trước; mọi candidate fail thì abstain.
Các giả định p-value/sample size/fold independence chưa thống nhất phải giải quyết
ở L1, không đổi ngưỡng trong L2 để cứu kết quả.

| Task / owner role | Deliverable | Điều kiện PASS |
| --- | --- | --- |
| L2.1 / research | Protocol freeze theo D01: search space, trials ledger, folds/purge/embargo, benchmarks, costs và holdout | Preflight đủ data/trade density; thiết kế inference xử lý dependence; protocol timestamp trước khi xem evaluation outcome |
| L2.2 / research | Reproducible campaign trên đúng pair/timeframe/config dự kiến live | Source code hashes attributable; có trade ledger và continuous OOS equity với attribution; gate report không có metric thiếu/default giả |
| L2.3 / quant + execution | Reality-gap report giữa research simulator và canonical runtime replay | Giải thích được sizing/rebalance/order slicing/fees/stops/delay/carry-boundary; tolerance được freeze trước comparison; discrepancy ngoài tolerance chặn promote |
| L2.4 / governance | Signed policy/binding và activation package | Candidate đạt toàn bộ applicable gates; holdout evaluation đúng protocol; expired/wrong-code/wrong-data/wrong-stage policy bị runtime từ chối |

Headline bắt buộc: net expectancy, profit factor, equity return/CAGR khi horizon phù hợp,
Sharpe và uncertainty, drawdown/tail loss, turnover/exposure, sample size theo trade và
fold, regime breakdown, costs, benchmark và abstention. Không cộng return của cửa sổ
OOS overlap thành portfolio return; MTM và realized PnL phải được phân biệt.

Stress suite: increased costs, one-bar delay, bỏ best trade, parameter neighbors,
regime changes và liquidity/order filters. PSR/DSR/PBO dùng theo contract đã review,
không “đủ số chỉ số” nếu sample/dependence assumptions không đúng.

Policy admission phải kiểm từng pair và portfolio chung. Không bắt buộc Enhanced MA
thắng; không loại pair thất bại rồi tiếp tục gọi cùng một evaluation là untouched
holdout. Nếu tất cả fail: lưu NOT_QUALIFIED và đề xuất experiment mới; không mở L4.

Rollback: giữ incumbent đã qualified hoặc abstain khi không có; không dùng policy
quarantined/historical hoặc reset activation stage để có lệnh.

### L3 — Chứng minh vận hành thật

| Task / owner role | Deliverable | Điều kiện PASS |
| --- | --- | --- |
| L3.1 / ops + execution | Authorized TESTNET campaign, real source logs và ledger | S7 yêu cầu tối thiểu 30 continuous days, 100 unique complete lifecycles, 0 unresolved orders, 0 safety breaches; inventory protection theo thời gian đạt contract |
| L3.2 / risk + research | Policy-bound SHADOW outcomes/calibration/health | S7: ít nhất 30 days, 30 observations, ECE ≤0.10, empirical/healthy, 0 critical alerts; dùng delayed mature outcomes, không backfill tương lai |
| L3.3 / ops + security | Deployment/incident drill evidence, provenance và paging delivery | Single leader; restart/network/unknown-fill/backup-restore/credential scenarios kiểm đúng môi trường; actual alert receipt; verified image digest/Cosign/SBOM/SLSA đúng release |
| L3.4 / reviewer | Integrated release evidence review | Source logs/hashes/identities/thời gian/gates khớp; mọi applicable gate đủ evidence; external CI/host checks chưa truy cập phải ghi NOT_VERIFIED |

Không đóng elapsed-time gate bằng unit tests, generated timestamps hoặc activity giả.
30 ngày là wall-clock của đúng subject/release, không phải thời gian compute. Có thể
chuẩn bị local telemetry, incident scripts và deployment checklist cùng L1/L2;
không tự mở external soak. Chỉ bắt đầu soak được tính sau authorization và dependency
gates liên quan; release/subject thay đổi phải review lại quyền reuse theo contract.
Testnet và Demo khác môi trường; không gộp ngày/lifecycles và không tự thay S7 TESTNET
bằng Demo. Testnet/Demo PnL không chứng minh mainnet alpha. Chỉ thêm môi trường sau
operator authorization và compatibility review.

Drill dry-run chỉ chứng minh dry-run, không chứng minh deploy/credential revocation thật.
Mỗi drill ghi trigger, expected action, actual effect, detection/recovery times, state
trước/sau và evidence observer. Thay execution/risk/schema/policy/config phải đánh giá
invalidate evidence nào; mặc định không cộng soak khác subject/release. Reuse chỉ với
compatibility decision, dependency hashes và reviewer approval rõ ràng, không relabel.

Rollback: khóa entries, duy trì/kiểm exchange-native protection khi được phép,
reconcile exchange với ledger, khôi phục qua approved procedure; không replay submits
từ backup. Không thực hiện credential/incident action ngoài scope đã được duyệt.

### L4 — Canary và xét tăng vốn

| Task / owner role | Deliverable | Điều kiện PASS |
| --- | --- | --- |
| L4.1 / reviewer + operator | Canary admission package và explicit approval | L0–L3 đóng trên integrated subject; pair/config/policy/image digest/loss budget/approval expiry khớp; budget tương thích actual exchange filters |
| L4.2 / operator + ops | Authorized canary, monitor độc lập và reconciliation | Không tự enable từ deploy; snapshot balances/fees/orders khớp ledger; incident/expiry/drift có safe action; budget không vượt |
| L4.3 / reviewer + operator | Canary report và quyết định scale/hold/reject | S7 canary tối thiểu 30 days, 0 safety/loss-budget breaches; đánh giá execution quality và realized performance; scale cần approval mới |

Kiểm feasibility theo `LiveRiskLimits.for_profile`: budget BUY phụ thuộc min(absolute
cap, equity-percent cap), deviation buffer và rounding. Với cap 0.25% equity,
`equity >= minNotional / 0.0025` chỉ là lower-bound minh họa, không phải khuyến nghị
nạp tiền hoặc lý do tự tăng limit. Test min/max notional, lot/step, deadband, partial
exit và dust với rules hiện hành từ exchange; không hardcode minimum theo ví dụ docs.

Operator approval cần approver, ticket, subject digest, account alias/environment,
capital/loss budget, pairs, limits, expiry và rollback owner. Approval record chưa
được xác thực là thiếu approval; agent không được tự điền tên operator để đóng gate.

Stop triggers: unknown inventory, unresolved submit/cancel, failed protection,
duplicate economic effect, stale/corrupt data, failed paging, identity mismatch,
budget breach hoặc approval expiry. Action tùy permission: BLOCK new exposure,
REDUCE_ONLY khi an toàn, escalate operator; hard kill có semantics riêng.

## 7. Chuẩn evidence bundle và revision tích hợp

Mỗi task có bundle riêng; final review dùng một integrated release subject. Thiết kế
bundle dưới đây là **TARGET specification**, chưa khẳng định có validator tự động.
Không tạo JSON mang nhãn VERIFIED/REAL_MARKET chỉ để khớp schema.

```text
<bundle-id>/
  manifest.json          # identity + checksums + environment + verdict
  protocol.json          # frozen gates/thresholds/trials/holdout, nếu research
  commands/              # argv, rc, UTC start/end, controlled-exec results
  artifacts/             # source logs, ledger/metrics/gate reports, redacted
  review.md              # reviewer, independent reproduction, gaps, verdict
  handoff.md             # changes, dependencies, risks, rollback, next owner
```

Manifest fields bắt buộc:

- `schema_version`, `bundle_id`, `task_ids`, producer và reviewer identities.
- Exact code commit + code/tree fingerprint; relevant working-tree diff/hash;
  final release không chấp nhận unrecorded relevant dirty changes.
- Environment/account alias, image digest nếu deployed, dependency/lockfile hash.
- Strategy/policy/binding/config/risk/cost/instrument-rules hashes.
- Data manifest/window/hash, fold/holdout IDs, evidence source và subject artifact ID.
- UTC generation/observation window, expiry, dependency/invalidation scope.
- Command + rc + output paths/checksums; raw-source derivation của từng metric.
- Applicable gate IDs, measured outcomes/units, PASS/FAIL/NOT_RUN/NOT_APPLICABLE reasons.
- Approval references nếu cần; explicit overall verdict NO_GO/CANARY_ELIGIBLE/SCALE_REVIEW.

Không coi SHA trên header log là đủ nếu tested tree khác SHA. Tests từ nhiều SHAs
không cộng thành final PASS. Rerun relevant suites trên revision tích hợp và reviewer
kiểm source evidence, không chỉ đọc SUMMARY/VERIFIED. Semantic changes vào engine,
gate hoặc fixtures có thể invalidate research/ops dù các tests cũ đã xanh.

Durable evidence không để chỉ ở `/tmp`; sau review chuyển vào approved evidence store
bằng quy trình không overwrite, giữ checksums và redaction. Không ship off-host nếu
chưa có authorization. Docs/tracker chỉ link artifact, không copy bảng metrics dài hạn.

## 8. Lệnh local verification chuẩn

Chạy trong WSL, repository root, với `.venv/bin/python`. Không dùng QwenPaw venv
cho project tests. Tạo `TASK_OUT` riêng từng run; không overwrite run người khác.

```bash
TASK_RUN="$(date -u +%Y%m%dT%H%M%SZ)-local-review"
TASK_OUT="/tmp/trading-live-${TASK_RUN}"
mkdir -p "$TASK_OUT"

python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 60 --heartbeat 30 --result-file "$TASK_OUT/preflight.json" \
  -- git status --short

python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 60 --heartbeat 30 --result-file "$TASK_OUT/runner-help.json" \
  -- .venv/bin/python scripts/live_enhanced_ma_binance.py --help
```

Local-only preflight/help; writes command results dưới `TASK_OUT`, không gửi lệnh.
Help rc=0 chỉ xác nhận CLI load được. Giữ output làm evidence; không cleanup tự động.

```bash
python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 600 --heartbeat 30 --result-file "$TASK_OUT/execution-tests.json" \
  -- .venv/bin/python -m pytest -q -o addopts="" -rs \
  tests/test_binance_live_runner.py tests/test_live_safety.py \
  tests/test_policy_evidence_gate.py tests/test_selection_policy_lifecycle.py \
  tests/execution/test_cancel_stop_regressions.py tests/test_e2e_authority_chain.py

python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 600 --heartbeat 30 --result-file "$TASK_OUT/evidence-tests.json" \
  -- .venv/bin/python -m pytest -q -o addopts="" -rs \
  tests/test_execution_simulator.py tests/test_r05_real_policy_replay.py \
  tests/test_r06_adaptive_execution.py tests/test_r07_portfolio_gates.py \
  tests/test_s5_adaptive_campaign.py tests/test_generator_evidence_integrity.py \
  tests/test_live_evidence_generator.py tests/test_testnet_soak_tracker.py \
  tests/test_live_audit_watchdog.py tests/test_synthetic_alerts.py

python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 600 --heartbeat 30 --result-file "$TASK_OUT/ac01-15.json" \
  -- env "PYTEST_ADDOPTS=-o addopts=" \
  .venv/bin/python scripts/run_acceptance_ac01_15.py
```

Local tests mặc định phải dùng isolated fixtures/fake brokers; không set opt-in live
environment variables. Pytest writes local temporary outputs; AC runner tạo run mới
trong `data/acceptance_runs/`, preserve previous evidence nhưng dùng shared `/tmp/acNN`
producer paths: **serialize AC runs**. Expected rc=0; log riêng mọi skip/xfail. Sáu
critical runner cases cần được unskip bằng tests mới, không xóa tests để có 0 SKIP.

Theo `AGENTS.md`, sửa `selection_policy.py`, `live_safety.py`, `nested_wfo.py` hoặc
`scripts/evidence_ac*.py` bắt buộc rerun AC01–AC15. Final integration phải chạy cả
affected suites và AC; local group PASS không thay remote CI/provenance verification.
L3 opt-in exchange commands và L4 mainnet commands **không được cung cấp như lệnh
copy/paste ở plan này**; operator dùng approved runbook sau khi grant authorization.

Tests/validators mới cho tasks ở mục 6 phải bổ sung vào appropriate suite và CI;
commands trên là baseline groups, không phải danh sách hoàn chỉnh bất biến. Không
khẳng định schema/validator TARGET đã implement chỉ vì plan có mô tả nó.

## 9. Tracker và mẫu bàn giao agent

Baseline AC PASS không đóng package live. Tiến độ dưới đây tách consumer đã sửa
khỏi producer và vận hành chưa được chứng minh.
Không đổi status chỉ bằng lời kể; mỗi row cần owner, tested revision và evidence link.

| Package | Status | Owner | Tested revision / evidence / reviewer |
| --- | --- | --- | --- |
| L0.1 | AWAITING_REVIEW | Agent hiện tại | Map consumer/identity ở mục L0; producer dependencies còn được ghi rõ; chưa có reviewer độc lập |
| L0.2 | AWAITING_REVIEW | Agent hiện tại | Default promoted producer + real authorities/planner; full entrypoint positive/negative local proof; signed runtime bindings; cần reviewer |
| L0.3 | AWAITING_REVIEW | Agent hiện tại | Full entrypoint BUY/stop/restart/SELL, crash-before-ACK/UNKNOWN/partial exit + Decimal oracle; races ở runner layer; external testnet NOT_RUN; cần reviewer |
| L0.4 | IN_PROGRESS | Agent hiện tại | Local account lock/isolation/binding + baseline tests; cần operator xác minh stable venue UID, migration/recovery và multi-host leader nếu triển khai nhiều host |
| L1.1–L1.4 | OPEN | Chưa claim | Chưa nghiệm thu |
| L2.1–L2.4 | OPEN | Chưa claim | Phụ thuộc D01, L0/L1 |
| L3.1–L3.4 | OPEN | Chưa claim | Phụ thuộc authorization, L0/L1 và subject freeze |
| L4.1–L4.3 | OPEN | Operator/reviewer chưa claim | NO-GO tới khi L0–L3 đủ evidence |

Bằng chứng local cho lượt sửa này:
manifest và source hashes mới: `data/acceptance_runs/l0_execution_20261004_final_local/manifest.json`,
AC01–AC15 15/15 PASS: `data/acceptance_runs/l0_execution_20261004_final_local/AC01_15_SUMMARY.txt`
(local-only, không nằm trong source checkout).
Regression chọn lọc mới: **163 PASS, 3 SKIP**; ba skips là external testnet opt-in,
không còn sáu legacy runner skips. Đây không phải full repository suite. Ruff và
`git diff --check` PASS. Revision tích hợp `252bb20fda5d3de69b0e1e873ca68dd511e8e1c5`
+ dirty source hashes trong manifest; patch chưa commit. Local fixture AC15 PASS
không đóng 30 ngày operational soak. Chưa có reviewer độc lập nghiệm thu patch.
Bundle 09:20/09:21 và kết quả 168 PASS trước đó là lịch sử cho patch cũ, không
chứng minh producer/account-session mới. Bộ chọn lọc hiện tại có thêm resolver,
authority chain và các tests mới, nhưng không cùng danh sách với bộ trước đó.

Tái lập bộ local mới (không gửi lệnh): dùng controlled_exec với timeout 600, heartbeat
30 và result file, chạy `.venv/bin/python -m pytest -q -o addopts= --tb=short` trên:
`tests/test_binance_live_runner.py`, `tests/test_live_safety.py`,
`tests/test_live_account_session.py`, `tests/test_promoted_live_admission.py`,
`tests/test_binance_testnet_acceptance.py`,
`tests/execution/test_cancel_stop_regressions.py`,
`tests/execution/test_instrument_registry.py`, `tests/test_cli_execution_safety.py`,
`tests/authority/test_resolver.py`, `tests/test_e2e_authority_chain.py`.
Giữ testnet opt-in tắt. Sau đó chạy integrated AC theo AGENTS.md.

Khi claim, tách row theo task ID để mỗi task có trạng thái riêng. Task workflow:
`OPEN → IN_PROGRESS → AWAITING_REVIEW → ACCEPTED`; dùng `WAITING_INPUT`/`FAILED`
kèm reason. Capability maturity dùng vocabulary của [Documentation Standard](../DOCUMENTATION_STANDARD.md),
không đổi TESTED thành PRODUCTION_VALIDATED chỉ vì task ACCEPTED.

Mẫu brief/handoff:

```text
Task ID / owner / reviewer:
Objective và explicit non-goals:
Base revision + existing overlapping changes:
Dependencies + authorization/decision record IDs:
Files claimed / expected interfaces / consumers:
Failure reproduction trước sửa:
Implementation hoặc measurement thực hiện:
Positive tests / negative tests / skipped và lý do:
Controlled results + code/data/config hashes + bundle path:
Metrics và derivation source (nếu có):
Rollback/invalidation scope:
Open risks / missing approvals / next owner:
Verdict: AWAITING_REVIEW | ACCEPTED | NOT_QUALIFIED | NO_GO
```

Reviewer checklist:

- [ ] Tested tree đúng integrated identity; dependency hashes khớp.
- [ ] Reproduce ít nhất một positive và một negative critical case từ entry point.
- [ ] Raw ledger/source logs chứng minh metrics, không chỉ summary/checkbox.
- [ ] Không có gate bypass, fake provenance, undisclosed SKIP hoặc changed-after-outcome protocol.
- [ ] Soak có môi trường/subject/time coverage thật; counterexamples không PASS.
- [ ] Secrets redacted; approved authority đúng scope và expiry.
- [ ] Rollback và incident handling không làm mất state hoặc tăng exposure ngoài quyền.
- [ ] Tracker, contract docs và capability claim thống nhất, không tự cấp quyền mainnet.

Brief khởi đầu có thể giao cho một agent (không tự cấp quyền spawn):

```text
Thực hiện L0.1 theo docs/operations/LIVE_READINESS_AGENT_PLAN.md.
Đọc AGENTS.md và contracts trước; xác nhận B01/B02 trên working tree hiện tại.
Chỉ dùng local code/data và fake broker; không kết nối account hoặc gửi lệnh.
Claim task và files, bảo toàn thay đổi người khác. Map identity/dependency từ
runner entry point tới risk, authority, planner, permission và lifecycle.
Reproduce missing-admission failure và đề xuất wiring cùng negative tests.
Không bỏ gate, không tạo fake promoted evidence, không thay research thresholds.
Bàn giao bundle và danh sách dependency cần owner chốt; để AWAITING_REVIEW.
Nếu cần mở scope sang implementation L0.2, báo rõ trước khi làm vượt brief này.
```

## 10. Luồng research và chọn strategy — contract triển khai

Luồng bắt buộc: dữ liệu pre-holdout đóng băng → profile nghiên cứu chung →
inner validation chọn tham số → outer OOS đo hiệu quả → native qualification →
policy theo pair/timeframe → paper parity → live admission. Không bước nào được
thay bằng việc chọn Sharpe cao nhất trên toàn bộ lịch sử.

| Bước | Đầu ra phải chứng minh | Khi không đạt |
|---|---|---|
| Input | Byte hash, quality hash riêng biệt, cutoff, closed candles; không đọc reserved holdout | Dừng nghiên cứu |
| Profile | Spot long-only BTC/USDT 1h; cùng data, phí, slippage, vốn và fold geometry cho mọi ứng viên | Không so sánh các campaign khác cấu hình |
| Candidate | Enhanced MA là ứng viên, không mặc định thắng; MA crossover là comparator; lịch sử theo tham số thực tế | Loại candidate sai contract |
| Inner selection | Chọn chỉ bằng validation trước OOS; tối thiểu 30 trades và Sharpe > 0 theo qualification hiện hành | ABSTAIN; không mở OOS cho fold đó |
| Outer measurement | Ledger OOS, số trades, return, Sharpe, drawdown, costs và native gate verdict; giữ toàn bộ trial burden | NOT_QUALIFIED; không publish |
| Policy | Registered code identity, parameters, pair/timeframe, evidence, validity, risk cap, fallback NO_TRADE | Resolver từ chối policy thiếu/sai/expired |
| Paper | Cùng canonical strategy/parameters/history/timing với policy; restart, duplicates, partials, cancel/stop, reconciliation | Không đủ điều kiện live |
| Live | Admission khớp policy, account/risk approval riêng, instrument rules và snapshot hợp lệ | NO_GO; không tự fallback Enhanced MA |

Chọn “hợp lý nhất” là chọn trong tập **đủ điều kiện**, theo selection protocol
đóng băng trước khi đo; không có ứng viên đạt thì NO_TRADE là kết quả đúng.
Đổi strategy theo thời điểm chỉ qua policy mới được đo và kích hoạt hợp lệ;
không đổi chỉ vì một vài lệnh thua hay hiệu quả in-sample đẹp hơn.

Thứ tự bàn giao còn lại: (1) đóng băng profile 1h và kiểm coverage pre-holdout;
(2) chạy candidate research bounded, không publish; (3) reviewer kiểm raw OOS
và native verdict; (4) chỉ nếu đạt mới nối policy vào runner và chứng minh
signal/exit parity; (5) paper soak; (6) operator xét live canary.
Runner `live_enhanced_ma_binance.py` hiện vẫn ràng buộc Enhanced MA: chưa được
tuyên bố generic strategy routing đã hoàn tất chỉ vì resolver hỗ trợ nhiều strategy.

## 11. Tham chiếu ngoài repository

- [Binance REST reliability](https://developers.binance.info/en/docs/products/spot/rest-api): timeout/5xx có thể UNKNOWN; cần reconcile.
- [Binance filters](https://github.com/binance/binance-spot-api-docs/blob/master/filters.md): notional/lot/step theo instrument hiện hành.
- [Binance Demo vs Testnet](https://developers.binance.info/en/docs/products/spot/demo-mode/general-info): môi trường không đồng nhất và không chứng minh live alpha.
- [Deflated Sharpe Ratio](https://doi.org/10.3905/jpm.2014.40.5.094): selection bias và multiple testing.

Recheck tài liệu exchange ở thời điểm thực hiện L3/L4. Tham chiếu ngoài không thay
runtime contracts, operator approval hoặc evidence riêng của project.
