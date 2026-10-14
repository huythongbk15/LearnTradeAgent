# Campaign evidence: bàn giao kiểm chứng cục bộ

## Integration handoff — 2026-10-14, VALIDATION_PENDING

Operator đã duyệt phân loại/commit/push và tạo PR code/tests/docs. Nhánh
`codex/calibration-integration-20261014`, base `dbb9713`; không force-push,
không đưa database/state/research outputs hoặc approval files lên remote.
Integration commit0951da3:44modules/774cases,770PASS/1FAIL/3SKIP; source stable,
hai registries và1500protected files không đổi. Lỗi test ABSTAIN dùng runnerNone
không tạo validation source; fixture spawn còn có thể đọc default local data.
Sửa fixture inline và kiểm tiếp đã lộ runtime thiếu guard khi retry cùng thư mục
nhưng bỏ contract. Follow-up chặn downgrade trước registry/data, không đổi gates.
Không relabel lượt fail/timeout thành PASS. Regression và AC15 cần chạy trên
follow-up commit; xem receipt local `data/git_sync_audit_20261014/` và PR/CI.
Snapshot pre-commit source32bfdb61…: 80/80scoped tests PASS; AC15PASS raw hashes
và actual source/config/test identity khớp. Local authority:
`data/calibration_v2_recheck_20261014/review.json`.

Protocol2.0 đã thống nhất; bốn lỗi freeze/fingerprint/resource/inventory đã sửa.
ADX25 giữ nguyên. Final verification phải gắn đúng commit tích hợp mới; trạng
thái hiện tại chưa phải independent sign-off/clean-release/live-ready. Gate
changes trong batch yêu cầu AC01–AC15 theo AGENTS.md. Không dùng packet v1 cũ
để tuyên bố v2 đã chạy thị trường. Evidence raw/local paths trong tài liệu là
references cho operator tại máy, không phải dữ liệu được đưa vào source PR.

## Operator preset decision — 2026-10-14, KEEP_ADX25

Human chốt "giữ ADX25": enhanced_ma/ma_adx giữ canonical defaults25 cho
calibration cost/plumbing và preset validation. Grid nghiên cứu30 và các default/
budget/eligibility khác không đổi. Record mới, không sửa historical packet:
`data/calibration_scope_decision_20261014/operator_scope_decision.json`.
Lựa chọn preset đã CLOSED; các ghi chú "chờ chọn25/30" phía dưới là historical.

Guard quyết định phát hiện source hiện tại0edcf435… khác snapshot04141… của
431PASS/AC15; environment/runtime/test-source identity không khác trong comparison.
Evidence mới: `data/calibration_scope_decision_20261014/identity_drift.json`.
Không thay source trong lượt này, không gán kết quả test cũ cho revision mới.
Current state **REVISION_REVIEW_REQUIRED**: review diff và reverify trước chạy.

Không cần sửa source cho lựa chọn này. Chưa có execution approval/frozen
fingerprint, external independent sign-off hoặc clean integration. Không tự tạo
approved_by từ quyết định preset, không chạy campaign, reuse scores, qualification,
holdout hay live. Tiếp theo: independent review → clean integration/refreeze/
reverify → operator duyệt exact packet/budget → calibration thị trường.

## Calibration → qualification identity audit — 2026-10-14

Authority bổ sung: `data/calibration_identity_20261014/identity_audit.json`,
controlled `audit_execution.json` rc0. Chỉ metadata/code/SQLite read-only;
không simulator/OHLCV/registry write hoặc market evaluation. Actual identity
khớp nguyên revision431PASS/AC15 phía dưới; hai registry và1437file bằng chứng
cũ giữ nguyên. Không chạy suite lại hoặc cộng audit vào số tests.

- RSI8, BBands4, Enhanced MA6, MA ADX9, MA Vol Target4 điểm/cost: tổng31.
- Defaults RSI/BBands/MA Vol Target mỗi strategy khớp1raw params hash grid.
- Enhanced MA và MA ADX lệch ADX25/30: không có điểm default khớp.
- Không tái hiện lỗi int/float ở các điểm gần default đã kiểm. Không sửa hash,
  normalize lịch sử hoặc tạo alias trial để ép reuse. Hash khớp chỉ là điều kiện
  cần; full evaluation context/fold/source/data/cost vẫn phải khớp. Calibration
  warmup-only khác WFO train/validation, không copy measurement qua hai luồng.

Đề xuất giữ defaults cho cost/plumbing-only để không mở rộng scope; đây là
khuyến nghị, chưa phải quyết định operator. Muốn điểm grid30 phải thay preset
contract có chủ đích trước freeze, sửa producer/verifier/tests và kiểm chứng mới.
Agent tiếp theo không tự chọn nhánh hoặc ký approval/reviewer. Sau review và
calibration được duyệt mới lập qualification scope/budget đủ grid/folds/gates;
không gọi calibration winner là best strategy/live-ready. Xem
[contract](../CALIBRATION_PROTOCOL.md#kiểm-chứng-đường-nối-sang-qualification--2026-10-14).

Trạng thái vẫn **LOCAL_VERIFIED_REVIEW_PENDING**, không independent sign-off,
clean integration hoặc market approval. Không cập nhật ngược hash/doc receipt
trong các packet cũ: chúng chứng nhận snapshot bàn giao tại thời điểm tạo.

## Worker-authority review hardening — 2026-10-14

Current source `04141b7e8170ee2a5e6a8da4ba7c5068d994d94d34befe8cd00ca63e330a56c4`,
dirty HEAD `dbb9713`. **LOCAL_VERIFIED_REVIEW_PENDING**: authority
`data/calibration_review_20261014/final_verification/verified.json` PASS,
**431tests/22scoped modules**, 0FAIL/0ERROR/0SKIP, 426.05s; **AC01–AC15:15PASS**,
fresh raw SHA và identity stable, Ruff/diff PASS. QA driver521.76s, không market ETA.
Post-doc guard: `data/calibration_review_20261014/final_handoff.json`.
Không full repo suite; focused56tests không cộng thêm vào final431PASS.
Review này do cùng agent thực hiện, **không phải independent sign-off**.
Các authority ngày13/10 phía dưới giữ nguyên như historical snapshots.

### Finding, bằng chứng và sửa chữa

- `review_before.json`: gọi worker trực tiếp đã chạm mock engine mà không cần
  supervisor/intent; không thực sự chạy engine hoặc sửa packet được kiểm.
  Worker mới cần one-shot inherited pipe từ owning parent, bind PID/out/key/
  protocol/intent; kiểm plan, intent STARTED, chưa có outcome và approval trước
  measurement. Receipt/worker-result đối chiếu cùng authority scope.
- CPU probe5short children chỉ thấy0.13s rồi wall-timeout: sampler cũ quên CPU
  sau khi parent wait/reap child. Sampler mới cộng utime/stime +cutime/cstime;
  test buộc dừng theo CPU và test không double-count living children đều PASS.
- Producer/worker/verifier dùng chung approval scope/time check: nonempty owner,
  timezone-aware approval không sau intent/execution. Hash không chứng minh human
  authorship. Terminal không được tự claim qualified/live hoặc che error.
- Scope filter không crash vì nullable metadata không liên quan. Corrupt rows
  thuộc scope vẫn phải fail context/accounting/seal; không blanket reject history.
- Plan thêm `worker_authority_contract=owned_pipe_intent_v1`. Không retrofit
  receipt/contract vào packet cũ hoặc thay evaluator/search-family để reset burden.
  Contract này chống gọi nhầm/replay nội bộ, không chống arbitrary code cùng OS user.

### Scope calibration đã chuẩn bị — chưa freeze/approve/chạy

`data/calibration_review_20261014/calibration_scope_proposal.json`:
BTC/USDT4h, existing pre-holdout6795bars, 5canonical families ×early/late =10cells;
cost1x, workers1/retry0, wall180s/cell và1800s tổng, CPU120s/cell và1200s tổng,
RSS2GiB. Chỉ đọc timestamps/byte SHA để kiểm input, không đọc OHLCV để research.
File này là `DRAFT_NOT_FROZEN_NOT_APPROVED`, **không** phải packet để `--execute`.

Default ADX của enhanced_ma và ma_adx là25, còn parent research grid dùng30.
Không sửa ngầm25→30 hoặc gọi calibration là subset điểm của grid. Nếu giữ25,
chỉ diễn giải như preset cost/validation riêng; không reuse/rank/qualify như kết
quả full grid. Nếu muốn đo điểm30, cần đổi scope/preset policy rõ ràng, sửa contract
và refreeze/reverify, không sửa manifest đã đóng băng sau khi xem số liệu.

### Reviewer/maintainer phải làm tiếp

1. Kiểm source và evidence độc lập; không chỉ đọc tracker hoặc số tests PASS.
2. Chốt ý nghĩa default25/parent-grid30 và giới hạn đại diện cost full WFO.
3. Tích hợp sạch có chọn lọc (workspace còn user changes), rồi refreeze/reverify.
4. Operator duyệt input/params/windows/budget trên fingerprint sau tích hợp.
   Không agent nào tự điền approved_by hoặc suy approval từ lời "tiếp tục".
5. Sau market calibration được duyệt, review raw reports/accounting mới quyết định
   qualification/pilot. Chưa có best strategy theo pair; không fallback Enhanced MA
   hoặc hạ thresholds để ép SELECT. Mainnet vẫn NO-GO.

Evidence bất biến: protected market packets +toàn bộ QA packet13/10, registry425rows;
suite/AC và post-doc guard phải xác nhận không đổi. QP registry có stale entries;
đã kiểm `/proc` thực tế trước sửa: không campaign đang chạy, không stop/delete PID.
[Current contract](../CALIBRATION_PROTOCOL.md).

## Calibration v2 + qualification restoration — 2026-10-13

**LOCAL_VERIFIED_REVIEW_PENDING**. Authority
`data/calibration_engineering_20261013/final_verification_02/verified.json`: PASS,
**414tests/22scoped modules**, 0FAIL/0ERROR/0SKIP, 416.33s; integrated
**AC01–AC15:15PASS**, fresh raw SHA và identity stable; Ruff/diff PASS.
Không full repository suite hoặc independent acceptance. Tổng QA driver534.70s,
gồm synthetic demo, regression, AC và guards; không dùng số này làm market ETA.
Source được đóng băng cho lượt này:
`452170b22fdd9ad8a1cef165b40fb066b0ec970d4519e6c11e9d139dd151ac49`, dirty HEAD
`dbb9713f95ffb6bf24ce65a1ee469afbbf335d9d`. Các handoff phía dưới là historical,
không thay thế kiểm chứng trên source này.

### Những phần engineering đã sửa

- Calibration v2: một shared verifier cho producer/CLI; exact coverage,
  canonical eligibility, raw typed artifact/report reconstruction, effective
  strategy/cost/risk config và path confinement. Import/help không load prices.
- Bind actual source/input/environment/config trước, worker-start và sau chạy;
  fixed defaults, real timestamp indices, holdout metadata exclusion trước OHLCV.
  Không dùng source SHA hardcoded hoặc approval field-presence để tuyên bố PASS.
- Fresh supervised Linux worker/group cho từng cell; wall/CPU/RSS watchdog,
  fresh user+system CPU receipts, PID binding, aggregate ceilings và cleanup.
  Sampled RSS không phải hard allocator cap; zombie PIDs được phân biệt active.
- Terminal-last exclusive/durable; inventory hash có framing/POSIX paths, thêm/
  xóa/sửa bytes đều fail; mtime-only không fail. Logical SQLite snapshot gồm specs,
  aliases, toàn bộ metadata/metrics và committed WAL. Không bỏ SQLite khỏi proof.
- Market path append vào registry hiện hữu `experiments_parallel.sqlite3`, cùng
  search family/evaluator; intent UNKNOWN bookkeeping-only trước measurement và
  outcome INNER_VALIDATION riêng. Synthetic dùng registry riêng, không ghi history
  thị trường. Không reseal, backfill, reset, resume hay nâng packet v1 thành v2.
- Regression rộng hơn phát hiện full qualification đã mất runner/context và
  sensitivity API. Đã nối lại source-bound selection trước outer; dispatch outer
  qua runner được truyền; versioned/idempotent trial context; native frozen gate
  validation; DSR thực tế và summary dùng registry-derived raw context burden.
- Sensitivity schedule đóng băng trước execution trên mọi selected outer fold,
  fresh startup tại outer boundary, stress +adjacent neighbors cả grid hai điểm;
  raw receipts/reconstruction và exact coverage. Failed/invalid/exception attempts
  được giữ; incomplete coverage không tạo partial aggregates để vượt gates.

### Kiểm chứng và cách reviewer tiếp tục

Authority: `final_verification_02/verified.json`, fresh JUnit/logs và integrated
AC packet mà receipt này chỉ tới. Post-doc authority:
`data/calibration_engineering_20261013/final_handoff.json`. Không dùng logs PASS
của lượt trước nếu source không khớp. Vòng QA đầu đã đỏ (40FAIL/75PASS trước khi
dừng có kiểm soát); logs/result được giữ, không đổi nhãn thành hoàn tất. Sau sửa,
209 scoped tests PASS là diagnostic, không cộng lại vào số final tests.

Synthetic demo dùng simulator thật, RSI/800bars seed7 ×2windows. Early:
3trades, Sharpe−15.3465, wall14.52s, CPU5.23s, RSS336.58MiB. Late:7trades,
Sharpe−17.7338, wall18.48s, CPU6.34s, RSS337.41MiB. Cả hai COMPLETED/ABSTAIN,
0selection freezes; không alpha/market proof và không đại diện cost full WFO.

Reviewer phải kiểm source/config/test identity before-after, fresh AC raw SHA,
registry425rows/digest không đổi, toàn bộ historical packet inventory không đổi,
source-bound decisions và tests negative controls; không chỉ đọc tracker VERIFIED.
Kiểm thêm phạm vi calibration khác full-WFO (warmup, không train simulation),
canonical default/parent search-grid identity và accounting của STARTED/FAILED.

Next: independent review → clean integration → refreeze/reverify → explicit
operator approval cho input/10market cells/budget → evidence review mới đề xuất
qualification/pilot. Không tự chọn trước ma_vol_target/Enhanced MA, không hạ
thresholds hoặc tự chạy campaign nặng. Không qualification, holdout, publish,
broker/account/lệnh live mới; mainnet vẫn NO-GO.
[Quy trình và contract](../CALIBRATION_PROTOCOL.md).

## Canonical validation-only repair — 2026-10-10, LOCAL_VERIFIED_REVIEW_PENDING

Packet mới `data/validation_only_fix_20261010/`; dirty HEAD `dbb9713`, source
`139392686d1868b8cecc55efa50b8965020664a404d980f4603cf609881f90f3`.
Đọc section này trước các handoff lịch sử. Không ghép snapshots thành một
revision được nghiệm thu; integration/independent review còn riêng.

### Thay đổi và phạm vi

- Xóa thuật toán validation riêng dedup params/cost (làm81requests thành9cells),
  copy selection cho mọi fold, đọc sai metrics/default0 và coverage nonempty.
  Wrapper gọi **canonical `run_nested_wfo(validation_only=True)`**: full grid ×
  cost × từng fold/window, purge/embargo/warmup/holdout exclusion không đổi.
- Dùng selection contract ≥30validation trades và Sharpe>0, deterministic tie
  break. ABSTAIN vẫn giữ fold/attempt trong denominator; không freeze selected
  params nếu không eligible. Các freezes SELECT dùng canonical content identity.
  Mode này không gọi OOS/statistical gates/sensitivity/holdout, không tạo
  `wfo_decision.json` hoặc qualification artifact. `passes_hard_gates=false`
  luôn đúng, kể cả khi tất cả inner measurements hợp lệ.
- Trials đi qua registry canonical hiện hữu với full protocol execution context,
  candidate identity/search family/evaluator giữ nguyên. Failed/no-trade attempts
  vẫn được accounting; không đưa failed/missing reports vào coveragePASS.
  Không reset/backfill operational registry hoặc sửa các packets cũ.
- Protocol bind scope validation-only +budget, workers1/retries0/cell timeout≤180s.
  CLI không cho cold reuse validation-only; output phải là packet mới. Các packets
  full qualification vẫn giữ contract/defaults cũ, không bỏ sensitivity/gates.
- Supervisor sở hữu Linux session riêng, wall budget1..1800s (default1800) và
  RSS sum128..4096MiB (default2048) lấy mẫu50ms, gồm worker/cell children.
  RSS sum bảo thủ, có thể đếm shared pages nhiều lần; không hard allocator cap.
  Cleanup TERM→KILL có grace tối đa2s và chờ process group không còn active;
  preflight/postcheck nằm ngoài worker wall budget.
  SIGTERM của supervisor dọn session sở hữu; SIGKILL/power loss có thể chỉ để
  STARTED. Không suy cause/completion từ process mất hoặc từ partial report.
- Coverage kiểm đúng multiset cells/windows, raw report SHA/artifact identity,
  eligibility decision, canonical freezes, controls thực tế và context-scoped
  registry count/conflicts. Summary chỉ xuất **sau coveragePASS**, publication
  exclusive/durable. Parent kiểm source/input/config và completion artifacts,
  ghi terminal receipt; worker timeout/memory/error ⇒ không nghiệm thu.

### Evidence đã kiểm trên snapshot cuối

- `final_verification/verified.json`: PASS, **375 tests /21scoped modules**, JUnit
  0FAIL/0ERROR/0SKIP, 406.76s; không phải full repository suite. Ruff và
  `git diff --check` PASS. Source/config/runtime/all test hashes ổn định trước/sau.
- Integrated **AC01–AC15:15PASS** ở
  `data/acceptance_runs/ac01_15_20261010_095655_utc/`, cùng identity snapshot cuối.
  `final_handoff.json` là bước kiểm lại sau cập nhật docs: đọc raw AC hashes,
  JUnit/log hashes, source/config/tests, registry và old-packet byte inventory;
  nếu receipt thiếu hoặc `verification_status` không PASS thì chưa đủ bàn giao cuối.
- Demo **real supervised worker +simulator, synthetic-only**, 1fold/1cell RSI
  BTC1h, canonical trial context/isolated test registry: COMPLETED, exact coverage
  PASS; wall37.58s, sampled group RSS698.00MiB. 13validation trades, Sharpe−15.96
  ⇒ ABSTAIN theo cả trade floor và Sharpe floor; không selection freeze. Inner
  evaluation1, outer/sensitivity0, `passes_hard_gates=false`, `live_authorized=false`.
  Raw/log/terminal nằm ở `synthetic_demo/packet/`, authority `synthetic_demo/verified.json`.
- Operational registry **425rows**, logical SHA
  `d641649ae2f2a2b833ac7e5024407679ad81900c21699b94b9eade03e6a05dc4`
  không đổi. Bảy packets smoke/cold/orchestrator/validation cũ giữ nguyên bytes.

Giữ cả diagnostics thất bại, không thay bằng PASS: `tests_initial.json` ghi
20PASS/1FAIL vì fixture gán vào frozen dataclass (đã dùng `replace`); verifier
đầu ghi374PASS/1FAIL vì SIGKILL gửi xong nhưng descendant chưa dừng. Đã sửa
supervisor chờ xác nhận group không còn active, không nới test. Hai cleanup
tests PASS rồi chạy lại toàn bộ375tests/demo/AC trên source mới. Evidence
source6e865… trước sửa cleanup là lịch sử, không ghép vào verdict139392….

### Giới hạn và đầu việc tiếp theo

1. Không market campaign/calibration/pilot được chạy trong sửa chữa này.
   Real worker demo dùng synthetic input/isolated **test** registry; không dùng
   nó để né accounting của market trials. Không dùng timings synthetic làm ETA.
2. Nhánh hiện tại là **full inner-validation**, không phải10cells early/late
   calibration được đề xuất. Không cắt folds/grid để fit budget rồi gắn nhãn
   full coverage. Nếu thiếu budget thì INCOMPLETE, operator quyết định kế tiếp.
3. `wfo_validation_test*` cũ thiếu final evidence, không relabel/backfill/resume
   chúng. Những market diagnostic attempts trước đây chưa nối accounting cần
   một reconciliation riêng nếu dùng cho lịch sử nghiên cứu; chưa tự backfill
   metadata/score hoặc tuyên bố trial burden lịch sử hoàn chỉnh.
4. Reviewer đọc raw/new negatives trên đúng source; integration commit làm đổi
   binding ⇒ freeze/reverify mới. Sau đó duyệt calibration protocol/budget cho
   5OHLC BTC/4h, rồi mới quyết định pilot. Không đổi thresholds, mở holdout,
   download, publish, account/broker/live hoặc tăng timeout tự động.

## Bounded profiling — 2026-10-09, LOCAL_VERIFIED_REVIEW_PENDING

Đọc section này trước closure lịch sử bên dưới. Source dirty `dbb9713`, tree
`8e539afd9cd5983129339ea2ced310eb2d516c96338424362ba1bbf4b6d31eb4`.
Packet mới `data/synthetic_profile_completion_20261009/`; không đổi/relabel
packet reuse hoặc smoke cũ. Công cụ `scripts/strategy_research/profile_synthetic.py`
chỉ có chế độ synthetic, không nhận market input/holdout và không WFO/publish.

- Mỗi strategy dùng **tuple đầu tiên trong catalog**, seed7, BTC/1h300bars,
  cost1x, một process/cell, không retries. Params không chọn từ kết quả.
  Simulator thật chạy inline trong disposable Linux process; supervisor riêng
  enforce wall deadline60s/cell, total330s và RSS ceiling1024MiB lấy mẫu50ms.
  RSS ceiling là sampled watchdog, **không** hard allocator cap; có kill grace
  tối đa2s. Inline tournament timeout/retry không được tuyên bố là enforced.
- Full stdout/stderr, cProfile, artifact/input SHA, source/config/loaded runtime,
  CPU, RSS sample và process-lifetime peak, STARTED/terminal receipts lưu riêng.
  Output phải là directory chưa tồn tại; thiếu evidence hoặc drift không PASS.
  SIGTERM/monitor exception dọn worker sở hữu; SIGKILL/power loss có thể chỉ để
  STARTED, không suy đó thành hoàn tất. Không kill các helpers có sẵn.

| Preset | Wall s | CPU s | Sampled RSS MiB | Closed trades (synthetic) |
|---|---:|---:|---:|---:|
| Trend pullback | 7.390 | 6.638 | 344.07 | 0 |
| Range mean reversion | 19.753 | 6.477 | 345.39 | 8 |
| Volatility breakout | 6.940 | 6.661 | 344.08 | 0 |
| Vol targeting | 9.401 | 5.004 | 343.93 | 1 |
| Regime ensemble | 8.746 | 7.505 | 348.23 | 0 |

5/5 cells COMPLETED chỉ chứng minh công cụ đi qua simulator. Ba presets không
có closed trade; không đủ coverage để calibrate order-intensive cost cho chúng.
Không xếp hạng strategy bằng các timings/returns này; một sample/strategy,
cProfile overhead và cửa sổ1h ngắn không đại diện cho pilot4h/full folds.
Không p50/p95/ETA, không alpha/qualification proof. `profile/finished.json`
ghi `representative_calibration=false`, `campaign_eta_seconds=null`.
Range: commit10.63s +fsync2.67s +schema1.67s ≈14.97/16.68s profiledexclusive;
đây là diagnostic I/O trong mẫu này, không phải lý do tắt durability/fsync.

Tool safety tests14PASS; final20scoped modules **351PASS/0FAIL/0ERROR/0SKIP**
(JUnit +full `tests.log`), integrated **AC01–AC15:15PASS** ở
`data/acceptance_runs/ac01_15_20261009_154215_utc/`, fresh evidence/identity stable.
`verified.json`:PASS, source/config/test identity giữ nguyên trước/sau,
registry425rows/digest unchanged, old smoke/cold byte inventories unchanged,
Ruff/diff checkPASS. Full verification464.50s, regression361.97s; không full
repository suite. Đây là self-verification, independent review PENDING.

### Handoff tiếp theo (không tự chạy campaign lớn)

1. Reviewer kiểm raw, negative tests, input identity và registry/history guard
   trên **đúng dirty tree**, hoặc freeze/reverify sau integration commit.
   Cùng Git HEAD không đủ: source mới làm packet/protocol cũ lịch sử; không bỏ
   fingerprint/checks để reuse, không reset trial DB/search family.
2. Đề xuất calibration đại diện validation-only BTC/4h: early/late windows từ
   manifest pre-holdout, presets/cost/scenario cố định trước, đủ warmup và
   order activity; accounting vào registry hiện hữu theo canonical contract.
   Đo separate startup/simulator/serialization/hash-import, CPU/RSS/wall,
   repeated samples và coverage. Không tạo evaluator/DB phụ để tránh burden,
   không xem OOS để chọn presets. Nếu chưa đủ mẫu thì không công bố p95.
3. Operator duyệt window/job count/global worker cap/max wall/RAM trước chạy.
   Timeout không tự tăng; nếu budget không đủ thì INCOMPLETE, không cắt folds,
   grid hay sensitivity rồi gọi đó là full pilot. Dự toán lịch sử là1,269inner
   +outer≤32 +sensitivity≤219; sau calibration phải preflight/freeze lại budget
   trên revision tích hợp, không dùng packet budget cũ làm authority mới/ETA.
4. Không sửa SQLite batching/durability chỉ từ micro-profile này. Nếu profiling
   đại diện xác nhận bottleneck, thay đổi phải giữ full signal/fill/ledger parity
   và crash recovery trước chấp nhận. Paper-soak/live admission vẫn riêng.

## Reuse integration closure — 2026-10-09, LOCAL_VERIFIED_REVIEW_PENDING

Section này supersede trạng thái "dispatch chưa nối" của các handoff bên dưới.
Không supersede các giới hạn qualification/live hoặc biến packet cũ thành PASS.
Packet mới: `data/reuse_integration_completion_20261009/`.
Source dirty `dbb9713`, tree fingerprint
`96a103082d157bc744fc92153402f6dafe24bd9cde2060b0f0906247630f2997`.

### Đã tích hợp và kiểm chứng cục bộ

- Giữ phần nối `--reuse-from` của maintainer; dispatch theo đúng catalog ID ×
  pair × timeframe. Kiểm tất cả mapping trước khi submit bất kỳ job nào.
  Missing packet/cache, wrong subject/grid/input/source/environment/runtime/
  worker count hoặc hash/contract sai đều reject; không fallback fresh âm thầm.
  Worker canonical tiếp tục so sánh **toàn bộ protocol**, folds và trial history
  trước evaluation. Admission của orchestrator không phải qualification.
- Generic real-WFO plan có semantic identity bỏ đúng metadata `frozen_at`,
  `frozen_by` và hai derived hashes; giữ source **kể cả dirty fingerprint**,
  data/grid/folds/cost/native/selection/history và environment/runtime/controls.
  Hash semantic không tự hash chính nó; raw plan vẫn hash toàn bộ payload.
  CLI cold import kiểm source plan rồi mới execution. Legacy packet thiếu
  semantic contract giữ nguyên, không backfill/reuse tự động.
- Research protocol vốn deterministic: bỏ field semantic phụ chưa được hash
  nhất quán; authority vẫn full protocol fingerprint v2, không bỏ source/config.
- Cache timing có `cache_reason`; verified import giữ raw bytes và lineage.
  Generic attempt có STARTED và INCOMPLETE/MEASURED_PENDING_EVIDENCE receipts;
  orchestrator job có terminal status, exit code, elapsed, reuse source.
  Hard kill không thể tự ghi final receipt: chỉ có STARTED thì vẫn INCOMPLETE,
  cần đối chiếu process/controlled result, không suy diễn nguyên nhân.
- Global measured-cell cap mặc định4: cell-workers2 ⇒ tối đa2 subject jobs;
  có `--cell-workers`/`--max-cell-workers`. Không tự tăng job timeout.
- `--no-publish` generic CLI dừng trước policy build/registry/activation.
  Resume/cold-reuse generic yêu cầu flag này; qualification dùng canonical
  `run_wfo_parallel.py`, không dùng generic cache như chứng nhận promotion.
- AC suite giữ raw output mới, kiểm freshness/hash, serialize shared `/tmp`
  producers bằng OS lock. Lưu identity trước/sau gồm source/env/loaded runtime
  và SHA của test sources; identity drift khiến suite FAIL dù15checks rc0.

### Evidence và phạm vi chứng minh

- `new_tests_retry.json`:82 scoped PASS trên snapshot trung gian, không thay
  verification cuối. `tests_acceptance.json` giữ diagnostic319PASS/18FAIL:
  publication fixtures thêm native/selection contract nhưng chưa recompute
  semantic hash; guard từ chối đúng. Fixture được sửa cả hai hashes, không nới
  validator. `tests_acceptance_retry.json` + `final_verification/qa_final.json`
  là authority sau regression mở rộng và AC; không cộng các lần chạy trùng.
  Final19scoped modules PASS (exit0), integrated AC01–AC15:15PASS tại
  `data/acceptance_runs/ac01_15_20261009_151502_utc/`. Suite fresh raw hashes và
  identity trước/sau PASS; source/config/test sources cùng snapshot, Ruff/diff
  checkPASS,425rows/digest và hai old packet byte inventories unchanged.
  Không full repository suite; independent reviewer/integration acceptance PENDING.
- Test `test_real_entrypoint_cold_import_across_timestamp_changes` đi qua
  `main()`/argparse/actual plan freeze/cache importer; nested evaluator được
  thay bằng synthetic artifact và cố ý stop trước bundle/publication. Chứng
  minh wiring/timestamp independence/raw parity/lineage, **không** là market
  qualification hay chứng minh CLI subprocess/full simulator trace parity.
  Trial burden/idempotence được kiểm riêng bằng canonical qualification tests.
- `historical_packet_audit.json`: old smoke/cold packet có0cache hit, sensitivity
 2/3 và1/3, thiếu final evidence; root cause dừng **UNDETERMINED** vì không có
 terminal receipt. Chỉ inventory diagnostic; không sửa/relabel packet nguồn.
  Registry lookup không cung cấp tracked campaign result để xác định nguyên nhân;
  không gán timeout/kill/strategy failure khi không có bằng chứng.
- `catalog/catalog_history_audit.json`:50OHLC jobs,310folds,12,195inner cells
  chỉ preflight;48NO_MATCHING_HISTORY,2HISTORY_RETAINED_FRESH_EVALUATION_REQUIRED,
 10Funding BLOCKED_DATA,0market evaluations.425rows/digest unchanged.
- `pilot_budget.json`: BTC/4h5OHLC strategies,1,269inner, outer≤32,
  sensitivity≤219 (phụ thuộc validation-selected params), attempt/timeout upper
 bounds có tính retries. Các bounds **không phải ETA**; chưa có p50/p95 CPU/RAM
 đại diện, không dùng hai MA smoke timings suy ra tốc độ cả catalog.

### Việc agent tiếp theo và điều kiện dừng

1. Reviewer độc lập đọc raw/negative tests và tái lập trên đúng source/config;
   integration/commit là việc maintainer chốt riêng, không suy từ cùng Git HEAD.
2. Đề xuất profiling bounded trước: strategy shortlist, validation-only windows
   early/late, params/cost preset, CPU/RSS/wall/attempts/hash-import overhead,
   max wall time/global workers/memory và output mới. Không xem OOS scores để
   chỉnh shortlist; outer/sensitivity trace cơ học có thể dùng synthetic trước.
   Nếu dùng market measurements phải ghi accounting/context theo contract,
   không tạo DB/search-family/evaluator mới chỉ để tránh trial burden.
3. Operator duyệt calibration/budget rồi mới market pilot canonical một pair/4h;
   giữ full grid/folds/cost/selection/native gates/sensitivity, chấp nhận ABSTAIN.
   Không chạy toàn50jobs trước khi biết chi phí; timeout không tự tăng.
4. Funding/universe vẫn BLOCKED_DATA; portfolio time alignment, full trace,
   concurrent verdict-census, power-loss proof, paper-soak/live admission còn
   riêng. Không tuyên bố live-ready/release ACCEPTED từ engineering tests.

Không rerun evidence drivers vào cùng packet: publication exclusive. Reviewer
dùng output mới; regression command list ở `verify_revision.py`, AC được chạy
tuần tự sau regression. Không hạ thresholds/mở holdout/download/publish/live.
Commit/integration làm đổi `code_commit` trong source binding: phải freeze và
tái lập evidence ở revision mới, không chỉ sửa dòng VERIFIED/hashes trong packet.

## P2 evaluation identity v2 — 2026-10-09, AWAITING_REVIEW

Đã triển khai contract v2 trong registry/WFO/worker và consumer orchestrator,
không đổi candidate ExperimentSpec, database path, search-family, evaluator hay
native thresholds. Không schema migration, backfill hoặc sửa legacy rows.
Các sections dưới là historical evidence; trạng thái BLOCKED của audit trước
không còn là verdict current sau khi có contract **fresh evaluation** riêng.

### Contract và accounting

- Logical candidate giữ cùng ExperimentSpec (params×cost/code/data/search).
- Metadata mới có `evaluation_execution_identity`: version2, protocol SHA và
  SHA của toàn bộ frozen context (version/source/environment/runtime config).
  Idempotency key là candidate +fold +phase +context; append dùng BEGIN IMMEDIATE.
- Context/marker phải hợp lệ và khớp declaration; metadata snapshot được copy.
  Cùng protocol khai context khác, marker mất/sai, duplicate trong một scope hoặc
  đổi metric/metadata/environment của exact replay đều fail-closed.
- Protocol khác append **evaluation mới**, không sửa record cũ. Legacy được giữ
  trong bucket unscoped, **không** nhận identity mới, không dùng để replay.
  Qualification protocol v1 không được nâng cấp/reuse như v2; consumer kiểm v2.
- `unique_experiments` là logical candidate count. `raw_trial_count` đếm
  candidate×context và legacy bucket; mỗi candidate chưa execute vẫn đếm≥1.
  Fold/phase/cùng-context replay không tăng selection opportunities. Không có
  empirical correlation thì effective=raw; matrix phải có đúng kích thước mới.
- DSR trực tiếp và summarize_sharpe cùng lấy effective burden từ registry;
  native result xuất raw/effective và unique_candidate_count riêng. Failed trials,
  legacy bucket, các context cũ và phase execution counts không bị loại bỏ.

| Một candidate | Evaluation rows | Raw/effective burden mặc định |
|---|---:|---:|
| Legacy +protocol A +protocol B | 3 | 3 |
| Replay đúng protocol B | vẫn3 | vẫn3 |
| Protocol A có2folds×2phases, không legacy | 4 | 1 |

Legacy bucket giữ baseline accounting cũ, **không** suy đoán/reconstruct bao
nhiêu protocol lịch sử chưa được ghi identity. Accounting hiện scoped theo
candidate IDs của WFO; không tuyên bố đã chứng minh tất cả research choices qua
mọi dataset/search-space. Không dùng matrix nhỏ/caller trials=1 để reset burden.

### Catalog audit và verification

Packet mới `data/research_evaluation_identity_20261009/`; authoritative catalog
ở `final_catalog/catalog_history_audit.json`.
Source dirty `dbb9713`, fingerprint
`96392d372fd5b520388ff84bfe9bf7970c702e3f29ccca36528766dce43368c6`,
dirty diff `ed5368d629a907bb2d4c55554b67d0cf591de63ef3ec57807d0f035118f1f991`.

50job OHLC,310folds,**12,195planned inner cells**;0market evaluations thực thi.
48job NO_MATCHING_HISTORY; Trend Pullback BTC4h74rows và Vol Target BTC4h78rows
được phân loại HISTORY_RETAINED_FRESH_EVALUATION_REQUIRED,0conflicts hiện tại.
Đây là hết identity collision blocker cho **fresh execution**, không phải reuse
hay qualification PASS. Funding10subjects vẫn BLOCKED_DATA; cross-asset thiếu
universe/scenario.425operational rows và logical digest trước/sau audit không đổi.

Final **251scoped testsPASS/14modules/347.53s**, integrated
AC01–AC15:15PASS (local-only: `data/acceptance_runs/ac01_15_20261009_080845_utc/SUMMARY.txt`).
`tests_acceptance_retry.json` rc0, tổng387.78s; `qa_final.json` source/environment
unchanged, Ruff/diff checkPASS, fixture byte-SHAs. Không full repository suite
hay independent acceptance.91targeted testsPASS trước lint-only rename giữ lại
là diagnostic, không cộng vào251cases để đếm trùng.
Diagnostic `tests_scopes.json`:87PASS/1FAIL do test đọc sai WFOResult field,
đã sửa assertion sang statistical_hardening; không bỏ accounting assertion.
`tests_acceptance_final.json` dừng ở Ruff F402 (loop shadow field import),
chưa chạy tests/AC; rename variable và tạo final catalog packet mới, giữ logs cũ.

Tests v2 kiểm legacy/new-context/replay accounting, real registry DSR source,
failure→success conflict cùng context, protocol redeclaration khác fold,
independent append callers, mutation sau append, invalid contexts/markers,
no-upgrade legacy protocol và fresh/replay canonical WFO. Synthetic fixtures
không là alpha evidence. Final verification còn bao gồm cache kill/recovery,
canonical authority, real simulator measurement trên synthetic input và AC.

`readonly_trial_burden.json` gọi actual trial_counts qua read-only connections:
toàn registry84logical candidates/425rows (396inner +29outer), raw/effective84;
scoped BTC4h Trend Pullback36candidates/74rows và Vol Target12candidates/78rows.
Chưa evaluation v2 nào ghi vào operational registry: đây là baseline cũ được giữ,
không phải prospective burden đã execute. Khi mỗi candidate có thêm context mới,
unit/integration tests chứng minh burden tăng và exact replay không tăng.

Reviewer tái lập trong output mới, không rerun drivers vào packet đã tồn tại:

```bash
review_dir=$(mktemp -d /tmp/evaluation-identity-review.XXXXXX)
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 1200 --heartbeat 30 \
  --result-file "$review_dir/tests.json" -- .venv/bin/python -m pytest -o addopts= -q \
  tests/test_evaluation_execution_identity.py tests/test_qualification_cold_resume.py \
  tests/test_campaign_resume_cache.py tests/test_parallel_research_protocol.py \
  tests/test_parallel_cell_execution.py tests/test_campaign_execution.py \
  tests/test_campaign_runtime_input_binding.py tests/test_campaign_entrypoint_safety.py \
  tests/test_experiment_registry.py tests/test_research_cadence_sensitivity.py \
  tests/test_existing_research_manifest.py tests/backtest/test_tournament_cell_control.py \
  tests/test_r02_canonical_authority.py tests/test_nested_wfo.py
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 180 --heartbeat 30 \
  --result-file "$review_dir/ac.json" -- .venv/bin/python scripts/run_acceptance_ac01_15.py
```

So source/runtime/config với final_catalog protocol trước/sau; không dùng HEAD
chung để relabel dirty evidence. Nếu source thay đổi, tạo evidence mới.

### Bàn giao và bước tiếp

1. Reviewer độc lập kiểm source/context, raw/metadata và accounting trên cùng
   revision; integration owner gom đủ dependencies local/untracked. Author PASS
   không thay clean integrated revision hay independent acceptance.
2. Orchestrator chưa có per-subject reuse-map dispatch. Chỉ nối exact v2 protocol
   vào destination mới; không dùng packet preflight-only làm completed cache.
3. Profile isolated representative windows trước batch: 12,195inner cells là
   inventory, không ETA và không chi phí đã đo. Full trace/financial arithmetic,
   power-loss/Windows và multi-campaign concurrent verdict-census proof còn OPEN
   (append concurrency đã test, không tương đương end-to-end qualification concurrency).
4. Chỉ chạy market pilot theo ngân sách rõ sau review/profile; không tự chạy
   toàn catalog, đổi grid/threshold/holdout nhằm tìm PASS. Research selection,
   paper-soak/generic live routing vẫn là các bước sau; mainnet NO-GO.

Không broker/account/lệnh, download, holdout, publish, commit/push; packet cũ giữ nguyên.

## P2 trial-history preflight — 2026-10-09, AWAITING_REVIEW

Đã bổ sung read-only conflict guard vào worker hiện hữu, không tạo pipeline
research mới. `audit_parallel_trial_history` xây candidate ID bằng cùng hashes
và ExperimentSpec của canonical WFO; chỉ đối chiếu các candidate/fold/phase
trong kế hoạch. Registry mở SQLite `mode=ro`, snapshot transaction; không init,
migrate, xóa hoặc sửa rows. Identity gồm subject/code/data/features/search/cost/
policy/commit/seed, execution context và fold environment. Duplicate tuple hoặc
context không khớp bị chặn **trước evaluation**, lưu `trial_history_preflight.json`;
runner kiểm lại lúc khởi tạo, lưu `trial_history_startup.json`. Atomic append
hiện hữu vẫn kiểm full metadata/metrics và concurrent changes sau startup.

Đây là early conflict check, **không** chứng nhận raw reports, metrics hay khả
năng replay. `CONTEXT_COMPATIBLE_PENDING_EVALUATION` chưa là PASS qualification.
`NO_MATCHING_HISTORY` chỉ nói không có tuple lịch sử trùng trong scope hiện tại.
OUTER_OOS kiểm mọi candidate có thể được chọn: fail-closed bảo thủ trước khi biết
selection, không nói tất cả candidate ấy chắc chắn sẽ được thực thi.

**Catalog audit thực tế:** 5 strategy OHLC × 5 pairs × 1h/4h = 50 job preflight.
Không chỉ nghiên cứu 2 strategy; không backtest job nào trong lượt này.

| Scope | Kết quả |
|---|---|
| 48 job OHLC | NO_MATCHING_HISTORY; chưa kiểm hiệu quả hoặc đủ điều kiện selection |
| Trend Pullback, BTC/USDT 4h | BLOCKED_HISTORY_CONFLICT:72 INNER_VALIDATION +2 OUTER_OOS =74rows |
| Vol Target, BTC/USDT 4h | BLOCKED_HISTORY_CONFLICT:72 INNER_VALIDATION +6 OUTER_OOS =78rows |
| Funding Carry,10subjects | BLOCKED_DATA:chưa bound funding series |
| Cross-asset | BLOCKED_DATA:chưa bound universe/scenario protocol riêng |
| Operational registry | 425rows trước/sau, logical SHA `d641649ae2f2a2b833ac7e5024407679ad81900c21699b94b9eade03e6a05dc4` không đổi |

Cả 152matched rows có `commit_sha` khác và thiếu execution context mới.
Không diễn giải 425rows là tất cả incompatible;273rows khác không thuộc
matched scope của hai job này. Không coi audit là OOS mới, không sửa identity
của packet/trial cũ để nhận kết quả legacy như protocol mới.

**Current source:** dirty `dbb9713`, fingerprint
`7b26764fb2eee94fc088a08e8b8b6e083f99b2b73e38369159b73e49029dcfad`,
dirty diff `68decbd6a06edfdaf8d236645f6cda6fedd183e5dd733884daf5775c1a514dfa`.
Packet catalog audit (local-only): `data/research_history_preflight_20261009/catalog_history_audit.json`.
Các sections sau là historical evidence trên source khác, không relabel current.

Verification cuối:230scoped testsPASS/13modules/290.42s; integrated
AC01–AC15:15PASS (local-only: `data/acceptance_runs/ac01_15_20261009_062704_utc/SUMMARY.txt`).
`tests_acceptance_final.json` rc0, tổng329.13s; `qa_final.json` xác nhận source/
environment không đổi, Ruff/diff checkPASS. Không full repository suite hay
independent acceptance. Tests mới kiểm actual
canonical candidate-ID parity, missing-context/subject/phase/environment,
duplicate/malformed metadata, startup race, unrelated-history và read-only
no-database initialization. Fixture planner nay dùng synthetic parquet thật
thay bytes giả; registry riêng từng test. Diagnostic `tests_history.json`
22FAIL/42PASS do fixture parquet giả, retry `tests_history_retry.json`64PASS;
`catalog_audit_execution.json` fail do driver chưa normalize `byte_sha256`,
retry `catalog_audit_retry.json` rc0. Giữ nguyên các logs, không overwrite.

**Đầu việc kế tiếp (không bypass conflict để chạy batch):**

1. Registry owner chốt contract tách logical candidate identity khỏi versioned
   evaluation/protocol identity. Giữ nguyên legacy rows, failures và trial burden;
   đổi protocol phải tạo immutable evaluation mới, không đổi display name,
   evaluator/search-family tùy tiện hoặc dùng database mới để reset history.
   Exact same-protocol replay phải idempotent; khác metrics trong cùng execution
   identity vẫn reject. Đây là thiết kế cần review, **chưa implement** trong lượt này.
2. Chứng minh accounting:legacy/new-context được phân biệt; rerun cùng context
   không tăng burden; execution mới giữ historical burden; failed/duplicate/
   concurrent attempts không biến mất. Rerun AC01–AC15 sau mọi gate/WFO change.
3. Chỉ sau đó đóng orchestrator per-subject reuse dispatch, clean integration/
   reviewer và representative profiling/full signal-fill trace parity. Không tự
   chọn48job không trùng lịch sử để né hai job conflict hoặc chạy batch nặng.

Research vẫn NOT_QUALIFIED, live NO-GO. Không thresholds thấp hơn, holdout,
download, account/broker, publish, commit/push hoặc market campaign mới.

## P2 qualification cold recovery — 2026-10-09, AWAITING_REVIEW

**Scope:** `scripts/run_wfo_parallel.py` nối CampaignCellRunner vào canonical
WFO; helper cache/OS locks trong `campaign_execution.py`; atomic idempotent
trial writes trong `research/trials.py` và `nested_wfo.py`. Không đổi native
thresholds, live durability hoặc source packet. Tests mới
`tests/test_qualification_cold_resume.py`; mở rộng cache regression hiện hữu,
cô lập fixtures `test_r02_canonical_authority.py` và `test_nested_wfo.py`.
Các sections dưới giữ historical source/evidence, không relabel thành current.

**Contract mới:** `--reuse-from SOURCE --out NEW_PACKET`; protocol phải khớp
toàn bộ source/data/history/grid/folds/cost/selection/native gates, workers,
timeouts, environment versions, allowlisted simulator settings, config-file SHA
và actual loaded risk/sizing/protection. Không same-packet qualification replay,
không migrate legacy packet/cache. Input binding của loader phải khớp frozen
binding trước/sau cell; không chỉ kiểm bytes của một path không được loader dùng.

Validation và selected outer có stages riêng. Outer phải khớp quyết định SELECT
đã freeze; nếu có source outer cache thì source decision/freeze không được thiếu
hay thay đổi. Cold import copy report bytes vào destination, không copy execution
state; mọi coverage/selection/native gates vẫn chạy. Sensitivity **luôn rerun**,
không reuse và không bị bỏ khỏi complete-scope coverage. CLI không authorize
holdout/publish/live. Thread dispatch1..4 giữ plan order, mỗi attempt vẫn isolated.

Registry `BEGIN IMMEDIATE` kiểm identity/metric/metadata/environment và trả lại
exact record khi replay; không ghi trùng giữa independent callers. Qualification
trial metadata bind protocol/source/environment/runtime config. Conflicting,
duplicate hoặc legacy history bị từ chối, không xóa/reset registry để chạy qua.
Lỗi này cần reviewer phân loại và protocol/version governance trước batch mới;
không coi kết quả đo mới là bằng chứng tương thích với trial cũ.

OS advisory per-cell lock ngăn hai process cùng execute exact cell; parent/child
exit tự nhả lock. Interrupted cold-copy giữ orphan nhưng import mới dùng UUID
khác; chỉ cache publish hoàn chỉnh mới được reuse. Output namespace escape qua
symlink bị từ chối trước write. Chưa power-loss proof hay Windows-specific QA.

Report validation có thể rebased vốn tại measurement open sau warm-up/train:
`initial_capital` phải khớp `measurement_evidence.opening_equity`, không ép bằng
simulator starting capital. Actual300bar synthetic measurement[150,300] kiểm
10,000 starting capital versus9,890.95884026065 measured opening equity; raw
bytes/metrics/health parity khi cold reuse. Không alpha/financial arithmetic proof.

**Final source:** dirty `dbb9713f95ffb6bf24ce65a1ee469afbbf335d9d`, fingerprint
`7b489f460c24da1012bfee5a1d6711c3ad49cb9d0d6c6b40f447ff062705713e`;
dirty diff `fa78e191c263ff79324e89dab67052fa7b554b0c6ee26a6297d1806ebe36a01f`.
Packet: `data/research_qualification_resume_20261009/`.

| Check | Kết quả / file trong packet |
|---|---|
| Final main regression | `tests_acceptance_final.json`:143PASS/11modules/69.69s; source unchanged trước/sau tests+AC |
| Mandatory AC01–AC15 | cùng result rc0; `data/acceptance_runs/ac01_15_20261009_054110_utc/SUMMARY.txt`:15PASS |
| Cold recovery end-to-end | test fixture:3inner/outer hits,4sensitivity reruns,7raw reports coveragePASS; trial count/freeze unchanged; workers1/2 parity kiểm riêng |
| Authority regression | `authority_isolated_final.json`:13PASS/36.03s; mock callbacks, isolated registry, verdict/gates/freeze/metrics equivalence |
| Nested WFO unit regression | `nested_wfo_bound_final.json`:64PASS/146.98s; measurement-window simulator nhận synthetic input binding thật trong child |
| Actual measured simulator demo | `demo_final_retry/demo_measured_result.json`:PASS trên final source;1synthetic cell/1cold hit/raw parity/source packet unchanged |
| Actual BTC4h preflight | `btc4h_preflight_final/research_protocol.json`:6folds/72inner cells/0evaluations; historical validation vẫn ABSTAIN |
| Final protocol | `902dd4cb99d45c211fe9022b7e6b1e520d9c5b72c605b356d6560c1f7de5f18c` |
| QA | `qa_final.json`:source/config-environment unchanged, Ruff/diff checkPASS; final fixture SHAs recorded |
| Registry audit readonly | `registry_history_readonly.json`:425rows/0protocol-context rows/0duplicate phase groups; không chứng nhận compatibility từng candidate |

Diagnostic logs giữ nguyên: `tests_expanded.json` thiếu tên module (không chạy
tests); `final_demo_preflight_execution.json` demo driver thiếu main guard gây
spawn exit (sửa driver, output mới); `authority_final.json`:9FAIL/4PASS, fixture
đụng shared legacy registry và pre-bound real callback đọc local market dataset.
Kết quả mô phỏng ngoài ý định đó **không** là research/qualification/baseline.
`authority_repaired.json`:12PASS/1FAIL do assertion field API, đã sửa; combined
authority+WFO `authority_repaired_final.json` TIMEOUT120s, không full-suite PASS.
Sau timeout không còn process mới; các helpers cũ dưới WSL /init không bị dừng.
`lint_authority.json` E731 đã sửa bằng direct lambda callback, không suppress rule.
Authority timeout cũ đã có scoped rerun PASS13, WFO unit rerun PASS64; combined
TIMEOUT log vẫn giữ, không relabel. Main143 + authority13 + WFO64 =220distinct
scoped cases PASS trong các lượt riêng; **không full repository suite**.

Measurement fixtures cũ patch loader chỉ trong parent nên spawn child vẫn có
thể đọc market data. Fixture mới bind synthetic parquet thật, giữ2000bars và
assertions; measured run dừng tại measurement_end như canonical WFO. Không dùng
đổi fixture để bỏ gate hay thay real simulator thành mock cho measurement tests.
Actual simulator demo không kiểm full signal/fill trace hoặc independent financial
arithmetic. Không benchmark campaign speedup từ demo/test timings.

Reviewer có thể tái lập trong result directory mới (AC chạy tuần tự):

```bash
review_dir=$(mktemp -d /tmp/qualification-recovery-review.XXXXXX)
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 600 --heartbeat 30 \
  --result-file "$review_dir/tests.json" -- .venv/bin/python -m pytest -o addopts= -q \
  tests/test_qualification_cold_resume.py tests/test_campaign_resume_cache.py \
  tests/test_parallel_research_protocol.py tests/test_parallel_cell_execution.py \
  tests/test_campaign_execution.py tests/test_campaign_runtime_input_binding.py \
  tests/test_campaign_entrypoint_safety.py tests/test_experiment_registry.py \
  tests/test_research_cadence_sensitivity.py tests/test_existing_research_manifest.py \
  tests/backtest/test_tournament_cell_control.py tests/test_r02_canonical_authority.py \
  tests/test_nested_wfo.py
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 180 --heartbeat 30 \
  --result-file "$review_dir/ac.json" -- .venv/bin/python scripts/run_acceptance_ac01_15.py
```

So source và config/environment trước/sau với final protocol; source thay đổi thì
không reuse packet này làm current evidence. Không replay demo output cũ; driver
được copy vào fresh output/layout hoặc chọn destination mới trước tái lập.

**Next reviewer/integration:** reproduce cùng source và fixture hashes; kiểm
registry-context conflicts trước batch, không reset/history migration ngầm.
Catalog orchestrator chưa dispatch subject-map reuse; worker CLI trực tiếp đã có
cold-recovery contract. P2-A representative I/O/worker scaling/full trace parity,
clean integration và independent sign-off vẫn OPEN. Full P2 chưa ACCEPTED,
research NOT_QUALIFIED/live NO-GO. Không publish/commit/push/broker/download hay
preregistered market campaign nặng trong lượt này.

## P2 cache v2 — 2026-10-09, AWAITING_REVIEW

**Scope/owner:** author engineering slice cho `CampaignCellRunner` trong
`src/trading_agent/backtest/campaign_execution.py` và regression mới
`tests/test_campaign_resume_cache.py`. Không sửa catalog qualification wrapper,
native thresholds, live durability hoặc packet lịch sử. Reviewer độc lập chưa có.

**Source đã đo:** dirty `dbb9713f95ffb6bf24ce65a1ee469afbbf335d9d`, fingerprint
`5a86038372d9bf6d9804c816a155c84bd5a771844dfc8ba56ab41a55d86d7050`;
dirty diff `61153fb8ce8fbdc6a3f1c82e87699487154ae300e3c483cfd441f628cb36bbf5`.
Packet mới: `data/research_resume_p2_20261009/`. Sections bên dưới là historical
verification trên source trước đó, không tự động chứng nhận source hiện tại.

### Contract và recovery đã kiểm

- Key v2 bind source/data/plan, Python/numpy/scipy/polars versions, cell/params,
  full cost/fault, stage, absolute windows, delay/gap policy/exceptions hash và
  execution controls. Kiểm source/data/environment trước và sau reuse/execution.
- Cache hợp lệ: kiểm typed artifact/hash, raw report SHA256, confined paths trước
  đọc, config fingerprint/requested params/cost/timing/gap, window và reconstructed
  metrics/health/descriptor. Cache matching bị partial/legacy/tampered/mismatch
  bị từ chối; không silently rerun hoặc ghi đè evidence đó.
- Cache thiếu: chạy trong `cell_attempts/<uuid>` mới; giữ attempt/report cũ.
  Completed cache được publish nguyên tử, no-replace, file/parent fsync trên POSIX.
  Failure records riêng từng attempt; không chỉ giữ failure cuối.
- Cold import chỉ giữa hai packet disjoint: copy raw report bytes vào destination,
  cập nhật artifact path và ghi lineage; không copy executable state hay sửa packet
  nguồn. Key khác nghĩa là cell mới, không reuse gần đúng.
- Genuine child kill trước publication: restart chạy lại cell chưa có cache,
  report cũ giữ nguyên. Kill sau publication: restart hit cache, không chạy simulator.
  Chưa chứng nhận power-loss, multi-writer locking hay partial cold-copy recovery.

### Evidence trên source này

| Check | Kết quả / path trong packet |
|---|---|
| Final scoped regression | `tests_final.json`:115PASS/9modules/26.27s; không full repository suite |
| Integrated AC01–AC15 | `acceptance_execution.json`:rc0; `data/acceptance_runs/ac01_15_20261009_014321_utc/SUMMARY.txt`:15PASS; author run, chưa independent review |
| Frozen BTC4h preflight | `btc4h_preflight/research_protocol.json`:6folds/72inner cells/0evaluations; historical12validation candidates vẫn ABSTAIN |
| Protocol fingerprint | `e7736b196da12199adb32e6c7db1e85d8db8bea375e29b0e37458b8cb278dee3` |
| Real simulator, synthetic input only | `demo_result.json`:PASS;1new cell/300bars,1warm hit,1cold hit; report bytes/metrics/health parity và source packet unchanged |
| Final QA | `handoff_qa.json`:rc0; Ruff và git diff --check PASS; source sau tests/AC/demo/docs vẫn khớp frozen preflight |

Demo baseline cell13.87s, warm hit0.10s, cold hit0.26s chỉ là single synthetic
sample, không campaign speedup/ETA hay alpha. Full signal/fill trace parity
**NOT_CHECKED**. Demo script từ chối output cũ; tái lập bằng copy driver vào fresh
`data/<packet>/` cùng layout, không ghi đè sample. Initial lint failure trong
`lint_tests.json` (unused import) giữ nguyên; import đã sửa trước final tests.
Broad authority TIMEOUT120s của packet runtime trước vẫn OPEN, chưa rerun.

### Next owner / điều kiện trước enable qualification resume

1. Integration + independent reviewer kiểm dependencies/dirty boundary và
   reproduce positive/negative/cache kill tests cùng AC trên cùng integrated revision.
2. Nối adapter vào canonical qualification wrapper: stage validation/OOS rõ ràng,
   effective full config/history/protocol binding, trial-count và frozen-selection
   idempotence; verify coverage/raw ledger sau reuse, không bỏ gates.
3. Test concurrent writers, partial import/parent kill và mismatch negatives;
   đo overhead SHA/source reads và isolated representative I/O trước tăng workers.
4. Rerun broad authority test với controlled budget thích hợp trước acceptance.

Caller plan hash không tự chứng minh preregistration. Raw SHA/config/reconstruction
và ledger presence không phải independent financial arithmetic, complete trace
proof hay chữ ký provenance. Catalog qualification wrapper **chưa enable resume**;
legacy real-campaign helper đã dùng class này nhưng chưa chứng nhận tương đương.
Cache v1 không tự migrate; không chạy lại vào packet cũ. Full P2 INCOMPLETE,
research NOT_QUALIFIED/live NO-GO. Không commit/push, market campaign nặng,
holdout, download hay broker/account/live action trong lượt này.

## P2 execution/profile — 2026-10-09, AWAITING_REVIEW

**Task/owner:** author P2-A/P2-B engineering slice; reviewer độc lập chưa có.
**Files claimed:** `scripts/run_wfo_parallel.py`,
`tests/test_parallel_cell_execution.py`; bounded profile driver trong packet mới
`data/research_runtime_p2_20261009/`. Không sửa nested WFO/native thresholds,
execution lifecycle durability, operator runtime config hoặc packet lịch sử.

**Final source:** dirty `dbb9713`, fingerprint
`813226afad26c334d7110fef8bbf7cfb929c0f4fc337597a606ef16bd870a3f6`;
dirty diff SHA256 `2ef8c0a6cbb5c03496c58cd86071a8e93ed9cf0101c64b7190c8beffe8867ce5`.
P0-A dependency/clean integration và independent review vẫn chưa đóng.

### Reproduction và sửa lỗi engineering

Source inspection trước sửa: `_run_cell_worker` gọi `_use_multiprocessing=False`
trong khi `run_cell` ghi rõ mode này không enforce timeout/retry. Timeout tại
Future không terminate running task; shutdown(wait=True) có thể tiếp tục chờ.
Ngoài ra runner không callable, nên canonical outer path fallback sang run_cell
thay vì dùng wrapper timeout/receipts đã khai. Worker còn bỏ fault khỏi primitives.

| Thay đổi | Kiểm chứng / giới hạn |
|---|---|
| Isolated attempts trong cả workers1 và spawn pool | run_cell enforce deadline/retries, child timeout được terminate trước return/retry; không timeout một Future đang queued rồi coi đã dừng |
| Runner callable cho selected outer OOS | Inner và outer dùng cùng declared timeout/retry/context; không đổi selection/OOS gates |
| Fault identity được forward | Params/cost/fault/window giữ nguyên; không silently chuyển fault profile thành none |
| Exclusive started/result receipts trước return | cell_execution/<attempt_uuid>/ giữ request/context/PID/time và typed artifact; file flush/fsync; FAILED/exception không mất khỏi batch |
| Completion-order consumption, plan-order return | Tie-break/selection ordering giữ nguyên; pending map được giải phóng; transport exceptions có receipt FAILED riêng |

Receipts chỉ là execution evidence, **không phải cache hay qualification**.
Started thiếu result hoặc result partial là INCOMPLETE, không success. Chưa chứng
nhận power-loss/directory-fsync/parent-kill recovery. Tổng wall time gồm nhiều
attempts, backoff, spawn, queue và I/O; per-attempt timeout không phải ETA subject.
Overall campaign vẫn phải chạy qua controlled toolkit. Maxworkers1..4 được kiểm.
Supplemental sensitivity còn canonical serial path, không nhận receipts wrapper
này; raw sensitivity inventory đã có ở P1, hai scope không trộn ngầm.

### Evidence cuối và diagnostic không được che

| Check | Kết quả / đường dẫn trong packet |
|---|---|
| Final scoped regressions | tests_outer_final.json:86PASS/6modules/10.80s; genuine hung child, genuine spawn pool, callable outer, fault, exceptions, ordering và retry controls |
| Integrated AC01–AC15 | acceptance_outer_final.json rc0; data/acceptance_runs/ac01_15_20261009_012606_utc/SUMMARY.txt:15PASS; chạy serial, không independent sign-off |
| Final actual preflight | btc4h_preflight_outer_final/research_protocol.json:6folds/72inner cells/0evaluations; execution rule frozen; historical12validation candidates ABSTAIN |
| Final protocol fingerprint | f472fdfc68abf2c45bfebd8f87df3d3bdb21e8156ef7b5a9881875882636faa8 |
| QA/source | handoff_qa.json và source_outer_final.json là checks cuối sau tests/AC; source phải khớp final preflight, không chỉ HEAD |

Không gọi full repository suite PASS. `tests_final.json` (broad command thêm
test_r02_canonical_authority.py) **TIMEOUT120s**; output chưa có pytest final
summary. Test cuối của module này dùng real run_cell dù fixture có tên fast;
không suy timeout là lỗi strategy hoặc bỏ nó khỏi acceptance lâu dài. Scoped
suite final là phạm vi riêng, không thay thế broad result. Process check sau
timeout không thấy test process mới còn chạy; các spawn processes hơn21h dưới
WSL /init không bị dừng/claim ownership. `tests_repaired.json` giữ fixture mismatch
fault; INITIAL_DIAGNOSTIC.md ghi lượt lỗi đầu và controller không tạo được log
vì parent directory chưa tồn tại. Không fabricate original result bị thiếu.

### Profiling: số đo nhỏ nhưng có hướng ưu tiên

`profile_result.json`, `profile_breakdown.json`, `cell_profile.pstats` đo một RSI
cell300bars synthetic, seed7,1h; không market qualification/campaign ETA. Baseline
11.588s, profiled13.746s; metrics/health/manifest khớp. cProfile gây overhead;
không so hai timings như speedup. Full signal/fill trace parity chưa kiểm và
không có algorithm optimization trong lượt này.

| Thành phần | Thời gian trong profiled cell | Cách đọc |
|---|---|---|
| SQLite commit | 9.172s,127calls | Exclusive ~66.8% tổng13.738s |
| fsync | 1.938s,28calls | Exclusive ~14.1% |
| executescript/schema | 1.427s,7calls | Exclusive ~10.4% |
| canonical_signal_series | 0.563s | Cumulative; không cộng với exclusive hoặc simulator |
| Simulator.run | 11.365s | Cumulative, đã bao gồm phần persistence bên dưới |
| Loader / quality | 0.0022s /0.0059s | Cumulative, riêng mẫu300bars |

Profile chụp source fingerprint `a134dfd5c769fefe5eee806a5d9dcca7df864eded96c27bb155a5fe9387ded8c`
**trước** final callable binding. Profile target tournament/simulator không sửa
giữa hai source, nhưng đây không phải exact-final-revision benchmark. Có overlap
với scoped tests; chưa kiểm representative windows, repeated isolation, memory
hoặc worker scaling. Kết luận chỉ cho mẫu này: ưu tiên đo persistence/I/O tiếp,
không tăng workers hay tắt SQLite sync/fsync để cứu runtime.

### Reviewer và next owner

```bash
review_dir=$(mktemp -d /tmp/research-runtime-review.XXXXXX)
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 120 --heartbeat 30 \
  --result-file "$review_dir/tests.json" -- .venv/bin/python -m pytest -o addopts= -q \
  tests/test_parallel_cell_execution.py tests/test_parallel_research_protocol.py \
  tests/test_campaign_execution.py tests/test_research_cadence_sensitivity.py \
  tests/test_existing_research_manifest.py tests/backtest/test_tournament_cell_control.py
```

AC suite chạy serial theo AGENTS.md; reviewer so source trước/sau với final
protocol. Không sửa packet để thay SHA. Profile driver từ chối output cũ; nếu
tái lập, reviewer dùng bản copy driver trong fresh data/<packet> (giữ đúng ROOT
layout), hoặc thay output riêng trước chạy, không ghi đè baseline/profile files.

1. Integration/reviewer: clean dependency boundary; review wrapper callable,
   isolation and shutdown; verify raw receipts/negative cases và tests/AC.
2. P2-A: profiling isolated repeat trên representative bounded pre-holdout
   windows sau review; đo storage latency/schema init/commit count, memory và
   workers1/2. Không suy số đo synthetic thành ETA hoặc thay durability live.
3. P2-B: chưa bật qualification resume. Preregister exact reuse key chứa
   source/data/effective config/history/cost/fault/delay/windows/stage/protocol;
   validate raw report+ledger, preserve failed attempts, idempotent trial count.
   Reuse vào packet mới với confined attributed reports; không mutate old packet
   hay mất frozen selection. Bắt buộc kill/restart và mismatch negatives trước enable.
4. Rerun broad authority tests với budget phù hợp, không coi scoped86PASS là đủ
   full integration. Sau P1/P2 acceptance mới sample feasibility và bounded P3.

Verdict slice **AWAITING_REVIEW**; full P2 **INCOMPLETE**; research
**NOT_QUALIFIED**; live **NO-GO**. Không commit/push/broker/holdout hoặc heavy
market campaign. Driver chỉ tạo synthetic input và chạy2small diagnostic cells.

## P1 cadence/sensitivity — 2026-10-09, AWAITING_REVIEW

Historical P1 verification source; xem P2 execution/profile trên cho source mới
và scope verification mới. Không promote số226PASS cũ thành exact-source test mới.

**Task:** phần tiếp theo P1-A/P1-B; author verification, chưa independent acceptance.
**Revision:** `dbb9713` + dirty source; fingerprint
`f0718a321afcbc3e40204047d61a8d611559bba8624beb88b36eadf9dbf2d096`.
Dirty diff SHA256: `649e468a084ce0c44cade83efe8212f7614a4acdc0b3e149c46afb0ad3258e51`.
Packet mới: `data/research_quality_sensitivity_20261009/`. Không sửa packet lịch sử.

### Lỗi có bằng chứng và phạm vi sửa

| Vấn đề | Thay đổi / ràng buộc |
|---|---|
| Tournament quality và bridge hardcode1h cho cả4h | Shared timeframe_delta cho simulator/quality/bridge; kiểm thứ tự source trước sort, không che out-of-order |
| Preflight chưa kiểm full-frame quality theo cadence | Ghi quality report đúng timeframe; frozen qualification input phải gap_policy=reject, không nới policy của các caller khác |
| Stress có thể aggregate chỉ các rerun thành công | Freeze deterministic requests trước rerun; giữ receipt FAILED/MISSING/INVALID_SOURCE; thiếu bất kỳ request→INCOMPLETE, aggregate không được chứng nhận |
| Stress dùng prewindow trading khác primary OOS flat boundary | Rerun bắt đầu flat tại outer_test_start; indicator vẫn đọc history; primary và stress dùng cùng startup convention |
| Neighbor axis2values bị bỏ qua | Adjacent-neighbor rule áp dụng axes>=2values; rule ghi vào protocol trước chạy, không dùng OOS để chọn thêm thử nghiệm |
| Coverage chưa bao gồm supplemental reports | Exact scope planned_inner_selected_outer_and_sensitivity; source confinement, report hashes/reconstruction, frozen plan và source commit đều kiểm trước summary |

Files claimed: `src/trading_agent/backtest/{reporting,tournament,nested_wfo}.py`,
`scripts/{full_system_backtest,run_wfo_parallel}.py`,
`scripts/strategy_research/run_campaign.py`; tests cadence/sensitivity, nested WFO
và parallel protocol. Dependency boundary của handoff trước vẫn áp dụng; dirty
changes bên ngoài không mặc nhiên đã được author này review hoặc sẵn sàng commit.

Sensitivity rule: mỗi selected outer fold có cost2x, slippage stress, delay1bar
và mỗi immediate adjacent parameter neighbor. Requests phụ thuộc selected
validation params/fold geometry, không phụ thuộc observed OOS scores. Raw reruns
ở thư mục `sensitivity/` riêng; plan được ghi trước rerun. Drop-best-trade là
derived diagnostic từ baseline ledger, không phải một cell rerun mới. Wrapper
qualification không cho tắt real sensitivity để nhận verified summary.

### Bằng chứng và giới hạn

| Check | Kết quả / artifact |
|---|---|
| Exact frozen input quality | quality_audit_result.json:10subjects (5pairs×1h/4h), byte hashes khớp, gap reject PASS;0new evaluations |
| Cadence negative control | BTC4h gắn sai1h sinh6794false gaps/20382false missing bars; đúng4h có0gaps |
| Actual local preflight | btc4h_preflight/:6folds/72inner cells;0evaluations; protocol fingerprint a96d66a4e961791abd1d086347ba31fee2cd534994a2952371cdb70c0e4f706a |
| Synthetic full/negative coverage | 2folds×(3stress+1neighbor)=8requests; thiếu fold, failed cell, changed delay/raw report, missing/altered plan bị từ chối; không phải alpha evidence |
| Final focused regressions | tests_final.json:226PASS,12modules,191.49s; không phải full repository suite |
| Mandatory integrated acceptance | acceptance_execution.json rc0; data/acceptance_runs/ac01_15_20261009_010826_utc/SUMMARY.txt:15ACsPASS |
| Stable source / lint | source_verification.json:source_unchanged=true; lint_final.json:Ruff/git diff --checkPASS |

Giữ tests_initial.json/new_tests_quick.json và lint_initial.json làm diagnostic:
lỗi callback name collision, strict-quality exception handling, unused import
đã sửa; fixtures untyped không được coi là attributable sensitivity evidence.
Lượt final riêng biệt là authority của author verification.

Historical first-fold12validation candidates vẫn ABSTAIN theo>=30trades/Sharpe>0.
Không có strategy qualification mới. Startup/cadence/neighbor rule thay đổi làm
packet cũ không tương đương current protocol; không relabel old stress metrics.
Raw reconstruction/hash linkage không phải independent arithmetic audit hoặc
chữ ký chống giả. AC/tests PASS không chứng minh alpha, full live flow hay clean
integrated revision. Native gate thresholds không thay đổi.

### Reviewer tái lập và bước tiếp theo

Tại repository root WSL, dùng thư mục mới và controlled execution:

```bash
review_dir=$(mktemp -d /tmp/research-cadence-review.XXXXXX)
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 60 --heartbeat 30 \
  --result-file "$review_dir/quality_execution.json" -- .venv/bin/python \
  data/research_quality_sensitivity_20261009/audit_quality.py \
  --out "$review_dir/quality_result.json"
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 600 --heartbeat 30 \
  --result-file "$review_dir/tests.json" -- .venv/bin/python -m pytest -o addopts= -q \
  --durations=5 tests/test_research_cadence_sensitivity.py \
  tests/test_parallel_research_protocol.py tests/test_nested_wfo.py \
  tests/test_backtest_reporting.py tests/test_native_gate_contract.py \
  tests/test_existing_research_manifest.py tests/test_selection_eligibility.py \
  tests/test_frozen_research_input.py tests/test_research_data_resolution.py \
  tests/test_campaign_integrity.py tests/test_parameterized_ma_history.py \
  tests/test_campaign_execution.py
```

Chạy integrated AC serial theo AGENTS.md, đối chiếu fingerprint trước/sau; HEAD
một mình không nhận diện dirty source. Local inputs phải tồn tại, không tự tải
thay thế. Reproduce ở source khác là verification mới, không exact reproduction.

Verdict: engineering slice **AWAITING_REVIEW**, full P0/P1 **INCOMPLETE**,
research **NOT_QUALIFIED**, live **NO-GO**. Chưa commit/push/publish, không broker,
holdout hoặc heavy campaign mới.

1. P0-A/P0-B: integration owner đóng dependency boundary trên clean revision;
   reviewer tự kiểm raw evidence/negative controls và chạy tests/AC lại.
2. P1 remaining: environment/effective config binding, legacy entry-point scope,
   scenario-specific funding/universe inputs; không gọi10OHLC subjects là universe coverage.
3. P2-A: profiler phải đo signal/simulator/I/O/worker thực trước campaign mới.
   Test durations mới chỉ cho thấy3measurement-window tests chiếm180.81s (~94%)
   của lượt191.49s; ưu tiên quick-vs-slow checks có parity, không bỏ slow tests
   khỏi integrated acceptance hoặc suy ra đây là bottleneck campaign.
4. P2-B rồi P3: durable exact-cell reuse/timeout/restart; feasibility và bounded
   screening preregistered. Không chạy Vol Target dài lần nữa chỉ kỳ vọng đổi
   kết quả, không hạ30trades/chọn window theo OOS đã thấy.

## P1 contract/coverage — 2026-10-08, AWAITING_REVIEW

Historical handoff; cadence và supplemental inventory đã có author verification
ở section2026-10-09 trên, chưa independent acceptance. Không coi các mục còn
thiếu ở section này là trạng thái mới nhất.

**Task:** P1-A/P1-B engineering slice, không phải full phase acceptance.
**Owner:** author của lượt này; **reviewer:** chưa có độc lập.
**Revision:** `dbb9713` + local dirty source; fingerprint
`f9867a438123207cdd5fa20d6c70a58cadba5f20dff8fc5abb111512f67b3fe1`.
Đây không phải commit tích hợp mới hoặc chứng nhận remote/master.

### Phạm vi và reproduction trước sửa

Wrapper song song không truyền selection_contract dù canonical WFO đã hỗ trợ;
vì vậy historical run chọn candidate4–5validation trades mà không chứng minh
contract>=30 hiện hành. Coverage cũ gom theo tên strategy nên thiếu một pair/
timeframe vẫn có thể bị che bởi report của subject khác. Planner còn dùng static
history và unpack holdout metadata hai giá trị trong khi canonical trả ba.
Không sửa packet cũ hoặc hạ gate để cứu kết quả đã biết.

| File claimed | Thay đổi trong lượt này |
|---|---|
| scripts/run_wfo_parallel.py | Grid alias Vol Target dùng catalog authority; freeze input/source/spec/history/cost/folds/validation contract; exact planned-vs-observed validation/OOS attribution trước summary; preflight-only; từ chối output cũ/holdout |
| scripts/strategy_research/run_campaign.py | Unique byte-verified input bindings; fail-fast missing/scenario input; worker protocol/decision/report checks; subject coverage strategy/pair/timeframe; từ chối rc0 thiếu verified summary |
| src/trading_agent/backtest/campaign_integrity.py | Exact subject coverage helper, không đổi legacy verifier hay gate thresholds |
| tests/test_parallel_research_protocol.py | Synthetic positive/negative cases cho protocol, report reconstruction, missing/duplicate/window/subject/cost/tamper/giả PASS |
| tests/test_existing_research_manifest.py | TIMEOUT giữ nguyên và làm coverage INCOMPLETE; canonical mapping không sửa verdict |

P0-A dependency boundary: nested_wfo.py/tournament.py/candidates.py đang có thay
đổi trước lượt này; selection_eligibility.py/native_gate_contract.py/
campaign_execution.py còn untracked. Không được commit riêng wrapper rồi giả
định clone remote có đủ dependencies. `integration_scope.json` ghi trạng thái
Git các file liên quan; không thay thế review mọi thay đổi của dirty tree.

### Bằng chứng đã chạy

Packet mới: `data/research_protocol_verification_20261008/`. Các artifact không
sửa packet cũ; lượt final giữ riêng so với các lượt verification trước.

| Check | Bằng chứng / kết quả |
|---|---|
| Focused regressions | tests_final.json:111PASS; tám test modules, không phải full test suite |
| Integrated AC01–AC15 | acceptance_execution_final.json, rc0; data/acceptance_runs/ac01_15_20261008_145106_utc/SUMMARY.txt:15ACsPASS |
| Actual frozen-input preflight | preflight_execution_final.json; btc4h_preflight_final/research_protocol.json;6folds,4params,3cost→72inner cells;0evaluations |
| Validation-only historical diagnostic | btc4h_preflight_final/preflight_example_result.json:12candidates fold đầu→ABSTAIN theo contract>=30/Sharpe>0 |
| Negative controls | Missing/duplicate folds/candidates/subjects, wrong window/pair/timeframe/cost, changed raw report, altered protocol, lowered selection và forged summary PASS đều bị từ chối |
| Source/QA cuối | source_verification.json: source_unchanged=true sau tests/AC; lint_final.json: Ruff và git diff --checkPASS |

Protocol final fingerprint:
`bcb27ead1c7853ade4d674b48e25351d5407a7017eb881bfe5c608c3efab97d6`.
Author tests không phải independent reviewer sign-off. Diagnostic đọc historical
validation metrics không nâng provenance/source của packet lịch sử và không
chứng nhận alpha mới. Hash linkage/reconstruction không phải chữ ký chống giả.

### Reviewer tái lập

Đứng tại repository root trong WSL; dùng output mới, không overwrite packet:

```bash
example_dir=$(mktemp -d /tmp/research-protocol-review.XXXXXX)
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 60 --heartbeat 30 \
  --result-file "$example_dir/preflight_execution.json" -- .venv/bin/python \
  data/research_protocol_verification_20261008/preflight_example.py \
  --out "$example_dir/btc4h"
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 240 --heartbeat 30 \
  --result-file "$example_dir/tests.json" -- .venv/bin/python -m pytest -o addopts= -q \
  tests/test_parallel_research_protocol.py tests/test_existing_research_manifest.py \
  tests/test_selection_eligibility.py tests/test_campaign_integrity.py \
  tests/test_native_gate_contract.py tests/test_frozen_research_input.py \
  tests/test_research_data_resolution.py tests/test_campaign_execution.py
```

AC suite phải chạy serial, không cùng một AC run khác vì share /tmp producers.
Sử dụng integrated command trong AGENTS.md; so source fingerprint với protocol,
không chỉ SHA HEAD. Nếu fingerprint khác, kết quả là verification mới, không
reproduce exact source của handoff này. Local data có thể không theo Git clone.

### Verdict và việc tiếp theo

Engineering slice: **AWAITING_REVIEW**; full P0/P1: **INCOMPLETE**;
research/alpha: **NOT_QUALIFIED**; live: **NO-GO**. Không publish/push/holdout/
broker IO, không campaign nặng mới, không đổi budgets hoặc operator config.

1. Integration owner review dependencies trên clean integrated revision và
   chạy lại tests/AC, không gom data/state hay edits ngoài phạm vi.
2. Research/evidence owner audit cadence/quality parity1h/4h: tournament đang
   khai expected_interval1h; cần chứng minh behavior và quality identity đúng
   cho4h, chưa suy ra preflight PASS là quality PASS.
3. Đóng inventory sensitivity/stress bổ sung, source/environment/config binding
   và scope các legacy entry points. Coverage ở đây chỉ planned inner grid và
   selected outer reports, không phải mọi supplemental cell hoặc universe.
4. Sau đó profiling/budget P2 và screening preregistered P3. Không chạy lại Vol
   Target dài chỉ để kỳ vọng kết quả khác, không giảm30trades hoặc chọn window
   theo OOS đã thấy. Paper/live qualification và operator approval vẫn riêng.

## Mẫu agent — audit arithmetic Vol target 2026-10-08

Điểm bắt đầu hiện hành: [kế hoạch điều phối](LIVE_READINESS_AGENT_PLAN.md#kế-hoạch-điều-phối-hiện-hành--2026-10-09).
Nội dung license bên dưới là lịch sử/deferred, không phải ưu tiên của lượt này.

**Task:** P0-B/example; chưa full ACCEPTED. **Scope:** một subject lịch sử,
ma_vol_target (catalog vol_target), BTC/USDT4h,6outer OOS folds. Không re-run
backtest, không mở holdout, không đổi gates, không cấp quyền live.

**Input packet:** `data/existing_research_vol_target_rerun2_20261008/`.
**Auditor:** `data/live_readiness_example_20261008/audit_example.py`.
**Evidence:** `audit_result.json`, `controlled_result.json` cùng thư mục auditor.
Kết quả đều mới; raw packet không bị sửa. Auditor từ chối ghi đè kết quả có sẵn.

### Cách tái lập không ghi đè

Đứng tại root repository trong WSL; dùng đúng .venv và controlled toolkit:

```bash
python3 scripts/qwenpaw_control/controlled_exec.py --timeout 60 --heartbeat 30 \
  --result-file /tmp/vol_audit_readonly_repeat.json -- .venv/bin/python -c \
  'import runpy,json; m=runpy.run_path("data/live_readiness_example_20261008/audit_example.py",run_name="audit_readonly"); r=m["audit"](); print(json.dumps(r,indent=2)); raise SystemExit(0 if r["arithmetic_status"]=="PASS" else 1)'
```

`run_name` không phải `__main__`, nên chỉ audit/print, không tạo lại audit_result.
Lượt đầu đã thực thi dưới1giây, controlled rc0. Một reviewer khác phải tự chạy
lại; không gọi kết quả của author là independent reviewer sign-off.

### Kiểm và kết quả

| Check | Cách chứng minh | Quan sát |
|---|---|---|
| Data identity | SHA256 của exact input đối chiếu execution_started binding | Khớp; không thay thế quality audit |
| Linkage/window | Outer artifact report path confined trong subject; subject và window khớp | 6folds khớp, distinct/nonoverlapping |
| Return/DD | Tái tính return từ đầu/cuối equity và drawdown từ running peak | Khớp từng fold và median aggregate |
| Trades/P&L | Long fill-price difference × quantity − entry/exit fees; đối chiếu ledger và measured accounting | 26trades; checks khớp |
| Negative control | Sửa median return +1 điểm phần trăm trong memory | aggregate_matches từ chối; không sửa packet |
| Protocol reconciliation | Đọc selected inner validation counts | `[5,5,5,5,5,4]`; không chứng minh ngưỡng>=30 luồng mới |
| Source identity | Báo cáo khai commit30675aa; không tái dựng dirty tree lịch sử | Chưa được chứng nhận exact source |

Median return OOS -1.4055524%, median DD3.0178488%; arithmetic PASS không chứng
minh expected profitability. Tổng ledger P&L của các fold độc lập không được
diễn giải như compounded return của một tài khoản liên tục.

**Verdicts:** arithmetic PASS; research NOT_QUALIFIED; full evidence audit
INCOMPLETE; live NO-GO. Sharpe/DSR/PBO chưa được auditor này tái tính. Các report
hashes được chụp lúc audit, không giả nhận là inventory đóng băng trước campaign.

**Next owner:** P1-A đối chiếu legacy/new selection protocol và source binding;
P1-B expected-vs-observed coverage từng tuple; reviewer mở rộng audit trên
integrated revision. Không sửa packet lịch sử để làm như nó đã tuân protocol mới.

## Production data-license review — 2026-10-07

Kết luận: chưa có nguồn đạt LICENSE_APPROVED cho project. Đây là kiểm tra
điều khoản công khai, không phải ý kiến pháp lý hoặc license đã mua. Không tạo
account, nhận trial/key, gửi thông tin dự án, liên hệ sales hay tải dữ liệu.

| Nguồn | Bằng chứng quyền sử dụng | Coverage/rủi ro | Trạng thái |
|---|---|---|---|
| Binance Vision | Điều khoản dataset hạn chế production/live; commercial cần enterprise agreement | BTC4h có archive nhưng license code không thay dataset license | Không dùng production theo terms mặc định; hỏi license riêng |
| CoinAPI | Customer Agreement1.2: internal business; usage guide nêu internal trading | Agreement2.3 đặt trách nhiệm third-party rights lên customer; coverage2018BTCUSDT4h và quyền retention/model sau termination chưa xác minh | Ưu tiên yêu cầu xác nhận bằng văn bản, chưa approve |
| Tardis | Terms9.5 cho task-specific quantitative models dùng internal trading execution;9.4 retention có điều kiện | Licensor restrictions/10.5 vẫn áp dụng; không general-purpose AI hoặc model cho bên thứ ba. Binance high-cap history từ2019-03-30, không đủ2018 | Phương án thay thế nếu duyệt đổi start date và pipeline |
| Binance REST/WebSocket | Developer page dẫn sang Product Terms | Endpoint public/API key cóTRADE không chứng minh quyền archival/derived-data production | Chưa xác minh, không coi là cách né Vision terms |

Nguồn chính thức được kiểm tra:

- [CoinAPI Customer Agreement](https://www.coinapi.io/legal), effective2025-08-18,
  sections1.2,2.3,7.3; [usage guide](https://www.coinapi.io/usage-policy).
  Guide là giải thích, hợp đồng/service-specific terms và upstream rights mới
  quyết định phạm vi. Không dùng bài blog để thay xác nhận trong order.
- [Tardis Terms](https://docs.tardis.dev/legal/terms-of-service), modified2026-09-13,
 9.4–9.5,10.5; [Binance coverage](https://docs.tardis.dev/historical-data-details/binance).
 Raw/trades-to4h khác native exchange klines; phải kiểm aggregation UTC,
 cadence/gaps/no-trade bars và volume semantics, không ghép im lặng.
- [Binance Spot Terms entrypoint](https://developers.binance.com/en/docs/products/spot/PROD-TERMS-OF-USE)
  và [Vision Dataset Terms](https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md).

### Nội dung cần provider xác nhận trước purchase/download

Yêu cầu BTCUSDT Binance global SPOT (không BinanceUS/futures),2018-01-01 đến
2022-12-31 UTC, native4h hoặc documented trade-derived4h; model phục vụ
automated trading tài khoản riêng, internal non-display, không bán feed/model.
Không gửi request này khi chưa được chủ dự án cho phép liên hệ.

1. Có quyền cấp dữ liệu Binance cho purpose trên không; cần thêm exchange
   license không? Xác nhận phạm vi ghi trong order/addendum và licensor exceptions.
2. Có đủ ngày/cặp/timeframe, dữ liệu nguồn/version/gap list, candle definition,
   source hashes và correction policy không? Không thayvenue để lấp history.
3. Có quyền cache/archive/backup/recompute4h/backtest và dùng derived models
   cho live/internal production không? Còn quyền sau hết subscription không?
4. Quyền lưu dataset, metrics, model parameters và audit evidence sau termination;
   điều kiện phải xóa/ngừng dùng, ảnh hưởng tới model đã huấn luyện.
5. Điều kiện cho local/cloud processors và coding agents; không đưa raw licensed
   data vào chat/LLM hay dịch vụ ngoài khi chưa được phép. Tách data-access role.
6. Gói/fees/quotas/renewal/overage, giới hạn users/environments, quốc gia/pháp nhân
   áp dụng; chủ dự án duyệt budget/account/legal acceptance riêng.

Exit criterion: có contract/confirmation rõ rights+coverage+retention+agent
processing, owner approval và evidence lưu nội bộ. Marketing nói commercial
allowed hoặc provider bán dữ liệu không tự đạt criterion. Chưa mua provider
thì chưa chuyển sang ingestion/qualification. Không suy license mới hợp thức
hóa các dataset/model cũ; giữ provenance và rights registry tách biệt.

## BTC4h historical backfill proposal — chờ phê duyệt tải

**2026-10-07: chủ dự án đã duyệt tải, nhưng dừng trước download do license scope
mới được phát hiện.** [Binance Vision Dataset Terms](https://github.com/binance/binance-public-data/blob/master/TERMS_AND_CONDITIONS.md),
v1.0 updated2026-08-26, quy định dataset CC BY-NC-SA4.0, personal non-production
research/backtest ở3–4, hạn chế live proprietary execution/automated commercial
orders ở4.2, commercial use cần enterprise data license riêng ở3.4. Terms còn
áp dụng cho derived data/indicators và coi access/use là acceptance. Không suy
license MIT của code thành quyền dataset production. Đây là scope warning,
không phải kết luận pháp lý về mọi trường hợp sử dụng của chủ dự án.

Không downloadZIP/checksum, không tự chấp nhận terms/license. Cần chủ dự án
chọn research-only isolated dataset không promotion sang live, hoặc xác minh
nguồn/license cho mục tiêu production trước khi tiếp tục. Không tự đổi sang
Binance REST API/nguồn khác để né hạn chế; nguồn thay thế cần kiểm terms riêng.
Quyền download trước đó không tự giải quyết lựa chọn license mới này.

Chủ dự án đã chọn giữ BTC4h và xác định nguồn/phạm vi. Chưa phê duyệt tải
dữ liệu hay chạy qualification mới. Không sửa live/native/eligibility gates.

Nguồn chính: [Binance public-data repository](https://github.com/binance/binance-public-data/blob/master/README.md),
Spot monthly Klines tại `data.binance.vision`, symbol BTCUSDT, interval4h.
Kho chính thức hỗ trợ4h và checksum SHA256 cho ZIP; archive có thể được sửa
về sau nên phải lưu snapshot/hash/checksum nguyên bản, không chỉ URL.
SPOT từ2025-01-01 dùng microseconds; dữ liệu trước đó cần parser units đúng,
không suy diễn mọi timestamp đều milliseconds. Terms cần được kiểm tra trước
download/use. Chưa kiểm tra từng tháng/URL hoặc bảo đảm toàn bộ archive tồn tại.

Phạm vi đề xuất:

- **Backfill development: 2018-01-01T00:00:00Z đến trước2023-01-01T00:00:00Z**,
  60 monthly ZIPs BTCUSDT4h của2018–2022 cùng60 checksum files, nếu tồn tại.
- Ghép với packet pre-holdout hiện có, kết thúc2026-02-06T12:00:00Z (inclusive
  open timestamps theo manifest cũ). Không lấy phần raw local sau cutoff.
- Raw local4h footer:7946 rows,2023-01-01 đến2026-08-17; kiểm bằng
  `local_4h_metadata_result.json`, chỉ footer, không nạp price rows/holdout.
  Chưa thấy lịch sử trước2023 trong file4h chính; inventory này không chứng
  minh không còn bản sao khác trong toàn workspace.
- Mọi vùng reserved holdout khác cũng phải nằm trong denylist trước parser;
  không gọi lịch sử bổ sung là unseen confirmation. Đây là development data.

URL mẫu (template, chưa download):
`https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/4h/BTCUSDT-4h-YYYY-MM.zip`
và cùng URL thêm `.CHECKSUM`.

Data acceptance checklist trước campaign:

1. Duyệt nguồn/phạm vi/terms, kiểm allowlist cố định60tháng và denylist holdout;
   download vào thư mục raw mới, không overwrite file hiện có, không credentials.
2. Kiểm SHA256 mỗi ZIP theo checksum; archive thiếu/mismatch là BLOCKED,
   không đổi nguồn hoặc bỏ tháng im lặng. Chặn ZIP traversal, giới hạn file/size,
   retry/timeout hữu hạn; ghi URL, fetch time, size, hashes, provenance.
3. Parser OHLCV4h UTC chính xác, explicit timestamp units, ordering/duplicate
   rejection; OHLC finite/positive, high>=open/close/low, low<=open/close/high,
   volume>=0, không có nến chưa đóng. Không forward-fill, upsample hay fabricate.
4. Kiểm cadence4h/gaps toàn bộ lịch sử; gaps phải ghi rõ và theo policy hiện
   tại, không chỉ nhìn input start/end. Kiểm continuity ở boundary2022/2023.
5. Giữ preserved pre-holdout bytes/hash nguyên vẹn; raw mới + normalized mới
   tách riêng. Manifest combined ghi source shards/checksums, resolved windows,
   excluded ranges, schema/conversion version; hashnormalized bytes/window.
6. Recompute fold geometry bằng chính `_get_fold_indices`; chỉ báo FEASIBLE
   khi đủ required folds/gaps. Không cam kết30 trades từ sốtháng hay sốbars.
7. Coverage study preregistered trước khi đọc metrics: chốt validation horizon
   và rules, counts/signals/health trên development, không xếp hạng theo return,
   không OOS/holdout. Ghi toàn bộ attempts/trial burden và thời gian đã xem.
8. Sau quality+coverage acceptance mới freeze experiment/source/cost/history/
   native contract, review độc lập và xin duyệt execution; không tự chạy ngay
   sau download, không hạ>=30 nếu coverage vẫn thiếu.

Geometry dài18–30m validation có thể có dư địa với lịch sử mở rộng này, nhưng
chưa chốt horizon hoặc bảo đảm trade sufficiency. DSR/PBO/stability/cost stress
vẫn là điều kiện độc lập; không bỏ chúng khi kéo dài thời gian.

## Coverage redesign giữ ngưỡng30 — 2026-10-07

Chủ dự án đồng ý lập phương án giữ ngưỡng30 sau corrected-history ABSTAIN.
Đây là quyền lập kế hoạch, không phải phê duyệt tải dữ liệu, đổi timeframe,
mở holdout, chạy experiment mới hay thay đổi statistical contract.

Kiểm tra trực tiếp `_get_fold_indices` trên6796 pre-holdout bars, train12m,
test3m/step3m, purge121/embargo121:

| Validation | Folds khả dụng | Giữ6 folds? |
|---|---:|---|
| 3 tháng | 6 | Có, nhưng lần đã chạy chỉ3–5 trades/candidate |
| 6 tháng | 5 | Không |
| 12 tháng | 3 | Không |
| 18 tháng | 1 | Không |
| 30 tháng | 0 | Không |

Reproduce bằng `coverage_feasibility.py`; output
`coverage_feasibility_result.json` trong packet corrected-history. Đây chỉ
là geometry feasibility, không chứng minh số trades, alpha hay gate PASS.
Không coi validation overlapping giữa folds là trades độc lập; không cộng
3–5 trades của9 candidates thành sample cho một candidate.

### Phương án và thứ tự ưu tiên

1. **Giữ subject BTC4h, bổ sung lịch sử development hợp lệ** — phương án
   ít đổi hypothesis nhất. Khoảng18–30m validation chỉ là cảnh báo coverage
   tuyến tính từ một fold; tốc độ trades có thể thay đổi. Muốn giữ train12m,
   6 folds/test3m phải đủ tổng span thực tế (khoảng48–60m+gaps cho val18–30m).
   Cần phê duyệt nguồn/phạm vi dữ liệu; không dùng reserved holdout để vá thiếu
   history. Phải kiểm gaps, exchange/market/symbol, UTC, fees, duplicate bars,
   provenance và regime coverage; hash/freeze input mới. Chưa tải dữ liệu.
2. **BTC1h với data chất lượng tương đương** — hypothesis mới, không phải
   phép tăng sốbars để bảo đảm30 trades. MA periods theo bars đổi horizon
   kinh tế và chi phí/turnover; phải chốt quy tắc periods theo bars hay thời
   gian trước xem kết quả, tuyệt đối không tự upsample4h thành1h. Cần inventory
   local data được phép trước, rồi chủ dự án duyệt experiment riêng.
3. **Strategy khác hoặc low-frequency contract riêng** — chưa ưu tiên khi
   mục tiêu đang giữ30 và giữ hypothesis. Thêm strategy là tăng trial burden;
   contract low-frequency không được tự thay30 bằng counts vừa quan sát.

### Các bước triển khai và điều kiện dừng

- C0 (đã làm): đóng kết quả ABSTAIN, bảo toàn registry/bằng chứng; geometry
  matrix PASS. Không chạy lại identical campaign.
- C1: duyệt phương án dữ liệu/hypothesis cụ thể. Ghi source, start/end,
  reserved windows, quyền truy cập và budget; chưa có quyền thì dừng ở plan.
- C2: data manifest + quality checks; quan sát coverage signals/trades trên
  development được khai báo, không OOS/holdout và không xếp hạng theo return.
  Ghi mọi cấu hình được thử. Coverage check dùng để chứng minh workload/sample
  khả thi, không biến vùng đã xem thành unseen confirmation.
- C3: preregister grid, time horizons, train/val/test, gaps, >=30 validation
  trades, minOOS30, native gates, trial burden, worker/memory/time budget,
  sensitivity; chốt exact costs/history/source. Nếu không đủ geometry hoặc
  sample thì NOT_FEASIBLE, không hạ threshold hay lấy thêm candidates để cộng.
- C4: một campaign theo plan mới. ABSTAIN giữ fail-closed, không bỏ fold;
  PASS/FAIL/INVALID đều ghi rõ. Không dùng metric development để tuyên bố live.
- C5: reviewer độc lập kiểm evidence revision tích hợp; chỉ sau đó xin duyệt
  confirmation chưa xem/paper operational validation. Live có approval riêng.

Thành công coverage là chứng minh sample đủ trước experiment và giữ boundary
development/confirmation rõ ràng; không phải làm mọi strategy đạt PASS.

## Kết quả campaign sửa history — 2026-10-07

`campaign_qualification_ma_history_20261007` dừng sau423,16s, fold_000
ABSTAIN, campaign INCOMPLETE, không OOS. Đủ9 inner candidates có3–5 validation
trades; Sharpe1,2813–2,7967; return+2,8677%–+7,5978%; DD4,3192%–6,5975%.
Đây là validation, không được gọi OOS/best-strategy qualification.
Tất cả bị loại vì insufficient trades theo ngưỡng30 đã duyệt, không còn0
tín hiệu do lookback. Audit9 report từ disk PASS: source/input/grid/history/
cost/warmup/reconstruction/saved decision. Bằng chứng:
`first_fold_verification_result.json`, `verify_first_fold.py`, `run_result.json`.

Agent tiếp theo không chạy lại cùng cấu hình hay giảm30 theo counts đã thấy.
Giữ NO-GO. Cần quyết định mới giữa:

- Giữ30, thiết kế horizon/data hoặc subject/timeframe phù hợp hơn; preregister
  và đánh giá khả thi trước execution, không tự tải dữ liệu mới/mở holdout.
- Xây contract thống kê riêng cho low-frequency strategies; phải được duyệt
  và review phương pháp, không dùng Sharpe dương với3–5 trades để thay thế proof.

Ước lượng coverage tuyến tính từ một fold chỉ mang tính cảnh báo:30 trades
ở tốc độ3–5/3tháng cần18–30tháng validation; không bảo đảm tốc độ ổn định.
Với train12/val18/test3 và6 folds step3, cần khoảng48tháng+gaps, vượt input
hiện có37,8tháng. Vì vậy không thể sửa bằng kéo val18 rồi giữ mọi thứ khác.
Các trạng thái RUNNING bên dưới là lịch sử, đã bị kết quả này thay thế.

## Sửa MA history — 2026-10-07

**Cập nhật:** WFO regression **196 PASS** (616,65s), log đã lưu. Các test
span bổ sung cuối được chứng minh bởi focused118; CLI correction bởi CLI19
và review độc lập19. Không cộng các nhóm hoặc coi là full-repo suite.
Campaign mới `data/campaign_qualification_ma_history_20261007/` đang chạy,
giữ nguyên ngưỡng/grid đã duyệt; warmup/purge/embargo121, hai workers,
no-publish. Chờ `run_result.json`; không sửa source Python trong lúc chạy.
Các dòng “regression đang chạy” bên dưới là lịch sử, không phải trạng thái mới.

Đã sửa warmup theo effective params của ma_crossover, giữ resolver chung cho
descriptor/adapter/signal và max-grid history cho WFO/CLI purge/embargo.
Plan freeze history_contract; consumer đối chiếu lại và bắt exact simulation.
118 focused tests PASS; CLI/history19 PASS; independent review19 PASS;
AC01–AC15 15/15 PASS (52,26s), Ruff/diff-check PASS. Không cộng các nhóm.
Test gồm slow20/50/60/80/120, prefix causality và fold spans default/override.
Full WFO regression đang chạy; chưa được gọi sửa hoàn tất hoặc chạy campaign
mới trước khi xem kết quả. Logs: `data/acceptance_runs/ma_history_20261007/`.
Không suy rộng rằng lookback của mọi strategy đã được audit. Packet cũ giữ
nguyên, plans cũ thiếu history_contract bị từ chối; cần experiment ID mới.

## Qualification kế tiếp — đề xuất chờ duyệt, 2026-10-07

**Kết quả hiện tại: dừng sau 122,71s, fold_000 ABSTAIN, campaign incomplete.**
9 inner cells hoàn tất nhưng đều 0 trades; chưa chạy OOS. Đã reproduce lỗi:
registered warmup51 tạo cửa sổ52 bars, nhỏ hơn slow MA60/80/120 của grid.
Thử đọc-only 138 validation bars cho fast10/slow60,80,120: cửa sổ cũ 0 tín
hiệu; đủ history cho mỗi cấu hình có 2 tín hiệu. Không xem đây là thiếu alpha.

Phải sửa lookback theo effective params, dùng resolver nhất quán cho
descriptor/adapter/canonical signals/WFO simulation/consumer verification;
freeze resolved history contract. Test slow > default, exact simulation,
prefix causality, AC01–AC15 và independent review trước campaign mới.
Không sửa artifact cũ, không resume với source mới, không hạ eligibility;
giữ registry trial burden. Evidence: `warmup_diagnosis_result.json` và
`run_result.json` trong packet qualification. Nội dung RUNNING sau đây là
lịch sử launch, không phải trạng thái hiện tại.

**Cập nhật:** chủ dự án đã duyệt bằng "tiếp tục" sau câu hỏi xác nhận ngưỡng.
Campaign đã khởi chạy trong `data/campaign_qualification_20261007/`, plan thật
được ghi trước cells; hai workers, timeout7200s, heartbeat30s. Kết quả còn chờ
`run_result.json`; không suy ra PASS từ trạng thái RUNNING. Không sửa Python
source khi chạy. Nội dung DRAFT bên dưới được giữ như lịch sử đề xuất.

Trạng thái: DRAFT, chưa phê duyệt eligibility production, chưa được phép
chạy experiment này chỉ vì agent đọc được lệnh bên dưới. Diagnostic một fold
đã hoàn thành; không tiếp tục lặp smoke cùng dữ liệu để tìm PASS.

| Thành phần | Phương án đề xuất | Ràng buộc |
|---|---|---|
| Subject | BTC/USDT spot, 4h, ma_crossover | Một subject trước, không tự mở rộng 10 pairs |
| Input | Parquet pre-holdout đã lưu | Development/requalification, không phải mẫu unseen |
| Geometry | 6 outer folds, train/val/test 12/3/3 tháng, step 3 tháng | Purge/embargo theo registered descriptor; exact indices trong plan |
| Search | Grid mặc định hiện có, phải kiểm tra đúng 9 tổ hợp trước freeze | 54 inner cells; sensitivity là workload bổ sung |
| Eligibility | Đề xuất >=30 validation trades và Sharpe >0 | Chỉ lọc tính khả dụng, không chứng minh alpha; cần chủ dự án duyệt |
| OOS trades | `--min-trades 30` | CLI mặc định 10 không phù hợp mục tiêu production đề xuất |
| Execution | 2 workers, timeout có heartbeat/result file | Không dùng cache khác source/data/plan; không hứa thời gian trước benchmark |
| Authority | `--no-publish`, holdout chưa mở, live NO-GO | PASS development không tự mở live |

Sáu folds tăng cơ hội đánh giá stability, concentration và cost-clearing;
không bảo đảm các gate đạt. PBO và neighbor phải có bằng chứng thật do producer
tạo; không coi có nhiều folds/grid là mặc nhiên tính được metric. Trade count
vẫn do dữ liệu quyết định, không hạ ngưỡng nếu sample thiếu.

Quy trình bắt buộc:

1. Chủ dự án duyệt hoặc sửa bộ eligibility đề xuất trước execution.
2. Dry-run kiểm tra grid, fold coverage, workload và cost contract. Ghi rõ
   khoảng thời gian từng fold, input hash, source fingerprint và native gates.
3. Đóng băng plan trong packet mới; không sửa Python source khi campaign chạy.
4. Chạy một lần theo plan đã duyệt. ABSTAIN dừng fail-closed; INVALID là thiếu
   bằng chứng, không đổi thành PASS. Không thay geometry/grid sau xem OOS.
5. Kiểm chứng ledger/NAV, selection/report hashes, geometry/cost/warmup và
   sensitivity từ disk; reviewer độc lập kiểm đúng revision tích hợp.
6. Nếu FAIL: phân loại thiếu coverage và robustness thật sự thất bại, giữ
   nguyên kết quả. Research thay đổi hypothesis cần experiment ID mới và
   ghi số lần thử để không che selection bias.
7. Chỉ sau development qualification được duyệt mới lập kế hoạch confirmation
   chưa xem/holdout riêng. Việc mở holdout, publish và live cần phê duyệt riêng.

Lệnh preflight (không execution, không publication):

```bash
.venv/bin/python scripts/run_real_wfo_campaign.py \
  --strategy ma_crossover --symbol BTC/USDT --timeframe 4h \
  --data-file data/campaign_pilot_carry_recheck_20261006/verification/pilot_pre_holdout.parquet \
  --out-root data/campaign_qualification_draft_20261007 \
  --train-months 12 --val-months 3 --test-months 3 --n-outer 6 \
  --min-trades 30 --workers 2 --max-inner-cells 54 \
  --min-validation-trades 30 --min-validation-sharpe 0 --no-publish --dry-run
```

Chạy qua controlled_exec theo AGENTS.md; luôn giữ result file. Lệnh production
chỉ được bỏ `--dry-run` sau phê duyệt, không bỏ `--no-publish`.

## Mới nhất: sửa theo review độc lập — 2026-10-07

Đã chạy diagnostic trên source hiện tại, **95,01s**, packet
`data/campaign_frozen_selection_diagnostic_20261007/`. Verifier đọc từ disk
PASS: source khớp plan, schema-v2 hợp lệ, costs/geometry/warmup/selection đúng.
Validation 8 trades, Sharpe 1,579139; OOS 5 trades, +0,288308%, Sharpe 0,931486.
Qualification FAIL với 14 gate failed/invalid; exit 1 là NOT_QUALIFIED có chủ ý,
không phải crash. Không publish, không mở holdout, không đặt lệnh sàn.

Đây là mẫu BTC/USDT 4h đã dùng trước đó, không phải xác nhận thống kê mới.
Bước tiếp theo phải phê duyệt production eligibility contract và preregister
experiment đủ coverage trước khi dùng dữ liệu mới/holdout. Không tự nới gate
hay thử thêm grid chỉ để có PASS. Giữ live NO-GO: trade/fold coverage thiếu,
PBO/stability/neighbor chưa đo đủ, doubled-cost và drop-best-trade không đạt.
Kiểm chứng bằng `run_result.json`, `verification_result.json`, `verify_run.py`
trong packet; `SCOPE.md` đã được ghi trước execution.

Ba phát hiện ban đầu đã được sửa: đối chiếu cửa sổ train/validation/OOS với
timestamp input đóng băng; khóa tên và mức phí của toàn bộ cost scenario;
chặn đường dẫn plan/decision thoát packet, kể cả symlink. Reviewer xác nhận
ba điểm này được xử lý, chạy độc lập 108 test PASS, nhưng bổ sung yêu cầu P2:
simulation start phải khớp warmup từ train đóng băng, không chỉ nằm trước
validation. Đã siết equality theo registered warmup + buffer 100 bars của WFO;
regression cuối **136 PASS** (18,21s), reviewer độc lập **109 PASS** (4,62s),
xác nhận cả bốn phát hiện đã đóng trong phạm vi review. Ruff/diff-check PASS.
Không cộng số test giữa các nhóm; không nâng thành nghiệm thu production/live.
Source capture hiện tại phải do caller đáng tin cung cấp như CLI; consumer
không tự xác thực metadata source tùy ý. Logs lưu trong packet identity.

AC01–AC15 **15/15 PASS trên code cuối** (37,61s), log
`frozen_selection_ac_final.json` trong packet identity. Không chạy campaign mới,
không mở holdout hay đặt lệnh. Qualification vẫn FAIL; publish/live bị khóa.
Agent tiếp theo phải giữ frozen cost binding, inclusive timestamp spans đổi
sang exclusive end indices, và simulation geometry chính xác khi sửa producer.

## Mới nhất: pin revision và data-quality identity — 2026-10-06

Consumer yêu cầu revision validation khớp source commit của campaign, fingerprint
chất lượng dữ liệu khớp outer artifact và phải tồn tại hợp lệ. Test sửa/re-hash
commit/fingerprint vẫn bị từ chối. Không nhầm fingerprint chất lượng này với
SHA256 bytes của parquet; metadata consistency không tự chứng minh toàn bộ input.
**117 tests PASS**, AC01–AC15 **15/15 PASS** (38,23s), Ruff PASS.

Không chạy campaign mới trong lượt pinning này. Script
`verify_provenance_compatibility.py` trong packet identity chỉ tái kiểm report
lịch sử; output ghi `historical_packet=true`, `current_source_matches_run=false`,
`fresh_campaign=false`. Không dùng script ấy để tuyên bố qualification mới.
Review độc lập và production-contract approval còn thiếu; giữ khóa publish/live.

## Trước đó: siết validation identity — 2026-10-06

Consumer đối chiếu thêm cell ID, cost scenario/commission/slippage, fault profile,
zero signal delay và simulation window không vượt cửa sổ validation. Test sửa
các field rồi tính lại hash vẫn phải bị từ chối. Report thiếu, JSON lỗi hoặc
không có metrics mapping trả ValueError, không được xem là metric mặc định.
**114 tests PASS**, AC01–AC15 **15/15 PASS** (45,96s), Ruff PASS. Không cộng với
102/127 ở các nhóm cũ; suite 127 chưa chạy lại sau hardening này.

Packet mới: `data/campaign_eligibility_identity_diagnostic_20261006/`.
Reviewer dùng source snapshot tương ứng và chạy:

```bash
PYTHONPATH=src .venv/bin/python data/campaign_eligibility_identity_diagnostic_20261006/verify_selection.py
```

Campaign diagnostic đã hoàn tất **102,48s**; verifier source hiện tại PASS
về binding và tái dựng SELECT. Qualification **FAIL, 14 gates**; không publish.
Log test/AC/run và verifier đã lưu trong packet. Ngưỡng/candidate/data vẫn là fixture kỹ thuật như SCOPE.md,
không phải production contract được duyệt. Các packet phía dưới là lịch sử
trước hardening; source check của chúng sẽ từ chối code hiện tại đúng thiết kế.
Không tự đánh dấu ACCEPTED, không tháo publish/live lock.

## Cập nhật eligibility/ABSTAIN — 2026-10-06 (lịch sử trước identity hardening)

Contract tùy chọn đã nối CLI → frozen plan → WFO → consumer. Không có candidate
đủ điều kiện thì ghi quyết định ABSTAIN rồi dừng toàn campaign, không bỏ fold
để tạo aggregate PASS. Consumer kiểm tra toàn bộ grid/fold, hash/window report,
subject/params của artifact, tái dựng artifact từ report và tính lại SELECT.
CLI vẫn bắt buộc `--no-publish` khi bật eligibility; không mở live.

Kiểm tra trên source cuối sau staging: **102 tests PASS**, AC01–AC15 **15/15 PASS** (37,31s), lint
và diff check PASS. Các nhóm regression lịch sử bên dưới không được cộng vào
con số này để gọi là một lần chạy full suite. Suite hồi quy WFO 5 file đã hoàn
tất: **127 tests PASS**, pytest 500,64s / controlled wall 501,68s. Không cộng
127 với 102 vì hai nhóm có test trùng; suite 127 chạy trước sửa staging,
code WFO không đổi sau đó. Chưa chạy toàn bộ repository suite.
Kết quả này thay thế lần timeout 180s lịch sử, không thay thế review độc lập.

Campaign kỹ thuật SELECT trên source cuối đã chạy **94,69s** và revalidate từ đĩa bằng rebuilder
thật: `data/campaign_eligibility_staging_diagnostic_20261006/`. MA 15/20,
validation 8 trades/Sharpe 1,579139; OOS một fold +0,288308%, Sharpe 0,931486,
5 trades. **Qualification FAIL, 14 gates; không publish.** Dữ liệu/candidate
đã được xem trước, ngưỡng 1 validation trade/Sharpe > 0 chỉ là fixture kỹ thuật,
không phải phê duyệt policy production hay confirmation sample mới.

Reviewer chạy từ workspace:

```bash
PYTHONPATH=src .venv/bin/python data/campaign_eligibility_staging_diagnostic_20261006/verify_selection.py
```

Script trả `source_plan_schema_bound=true`, fold đã tái dựng, qualification FAIL
và `live_authorized=false`. Nó không ký nghiệm thu và không cấp quyền giao dịch.
Log AC/regression/run và kết quả verifier đã lưu trong cùng thư mục evidence.
Nguồn hiện tại phải khớp snapshot trước chạy; không suy diễn các grid cũ đã
kiểm chứng code eligibility mới. Còn cần review tích hợp độc lập và contract
production được chốt trước một campaign qualification mới; giữ nguyên holdout
và các native gates. Publish staging đã nhận source packet root tường minh,
bị giới hạn trong publication root; 5 fixture synthetic chạy real consumer/
rebuilder/writer chứng minh valid + idempotent và từ chối tampering/coverage/root.
Source packet phải còn tồn tại: chưa phải archive tự chứa hay signature service.
Khóa diagnostic-only chỉ được cân nhắc tháo sau review/approval, không vì tests PASS.

## Rà soát sau pilot của operator — 2026-10-06

Lượt tiếp theo đã bổ sung proof cho empty ledger và open inventory sinh trước
OOS, gồm partial exit + remaining inventory. Grid 9 candidate / 2 fold chạy
444,56s, RAM sampled process-tree RSS khoảng 1,02 GiB; bundle revalidate PASS
về cấu trúc nhưng qualification FAIL (16 gate). Median OOS +0,144154%, Sharpe
0,465743, 5 trades, PBO 0,485714. Không coi return dương là đủ điều kiện live.
Chi tiết và các giới hạn cập nhật tại tracker; evidence trong
`data/campaign_grid9_20261006/verification/`. Các con số 140 test và pilot đơn
ở đoạn dưới là lịch sử; regression hiện tại là 145 test, AC01–AC15 PASS.

Tiến độ hiện tại được ghi ở `docs/CAMPAIGN_STATUS.md`, mục “Current local
verification”. Commit `30675aa` giữ được open-leg evidence nhưng chưa đủ:
exporter xử lý NAV như cash; boundary trade thiếu proof; producer schema-v2
loại các field carry nên consumer không thể đối chiếu open leg.

Đã sửa ba bridge này, không nới native gate. Regression 140 PASS, AC01–AC15
PASS. Pilot mới chạy 221,79s, tạo được bundle hợp lệ rồi bị từ chối qualification
(median return −0,120844%, Sharpe −1,249501, 13 trades, 18 failed gates).
Không publish. Evidence tại `data/campaign_pilot_carry_recheck_20261006/`.
PASS atomic publication với carry chỉ được chứng minh bằng synthetic fixture,
không đổi nhãn thành REAL_MARKET acceptance. Các đoạn dưới là lịch sử kiểm tra.

## Native contract và tối ưu campaign — 2026-10-06

Trước cell đầu tiên, `run_nested_wfo` ghi `native_gate_contract.json`:
20 gate native (22 nếu mở final holdout), policy version, minimum OOS trades,
threshold/comparison/unit và fingerprint. Cost floor giữ công thức đăng ký
`0.32 * median(positive_fold_trade_counts)`; không hạ ngưỡng theo kết quả.
Contract cũng nằm trong frozen campaign plan. Native gate evidence và aggregate
campaign gates là hai lớp riêng; consumer so nguyên bộ native gate với WFO
object và contract, không dùng aggregate gates thay thế native qualification.
Canonical stats và sensitivity verified vẫn là yêu cầu native, không bị bỏ qua.

Entrypoint hỗ trợ `--workers 1|2|4`, `--max-inner-cells` và `--resume`.
Inner cells chạy độc lập, kết quả trả theo thứ tự đầu vào; outer selection vẫn
tuần tự. Timing inner/outer được ghi `cell_timings.jsonl`. Resume chỉ dùng cell
COMPLETED khi source/data/plan/window/cost/fault khớp, report SHA khớp và metrics/
health được tái dựng từ report. Không tái dùng cache để thay thế evidence gates.
Sensitivity vẫn chạy đầy đủ, hiện chưa qua batch runner/cache; budget inner
không phải tổng số backtest hoặc SLA. Không coi 4 workers mặc định là nhanh nhất.
Resume chưa hỗ trợ thay thế bundle đã tồn tại bằng một bundle khác; nếu output
cuối đã tồn tại, dùng directory mới hoặc giữ nguyên evidence để xử lý có kiểm soát.

Kiểm chứng local: AC01–AC15 PASS (`/tmp/native_contract_ac_final.json`).
Smoke dữ liệu BTC/USDT 1h, 31.783 bars, 1 candidate/1 fold, 1m/1m/1m chạy
149,77 giây, không publish; bundle có 20 native gate và verdict FAIL (21 gate
native/aggregate không đạt). Outer OOS return −1,44649%, Sharpe −4,28694,
5 trades: đây là kiểm tra đường chạy/contract, không phải qualified strategy.
Evidence tại `data/campaign_smoke_20261006_native_frozen/`, log đầy đủ tại
`/tmp/native_frozen_actual_smoke.json`. Không dùng headline sensitivity +3,20%
làm kết quả outer OOS. Mainnet vẫn NO-GO.

Regression cuối: **86 passed**, lint và diff whitespace PASS; log đã sao lưu
vào `data/campaign_smoke_20261006_native_frozen/verification/`.
Benchmark 4 candidate cells, simulation [0,800), measurement [600,800):

| Workers | Cold batch | Verified resume | Cache hits |
| --- | ---: | ---: | ---: |
| 1 | 97,25s | 0,009s | 4/4 |
| 2 | 64,93s | 0,010s | 4/4 |
| 4 | 38,66s | 0,019s | 4/4 |

Metrics và execution health giống nhau giữa cả ba cấu hình và resume.
Evidence/script tại `data/campaign_worker_benchmark_20261006/`.
Đây là một lượt benchmark ngắn, không phải benchmark công bằng để công bố
speedup: lượt workers=1 có phần thời gian trùng smoke; cold filesystem/runtime
và tải máy chưa được kiểm soát. Chưa đo peak RSS hoặc full-window sensitivity.
Giữ default workers=1; dùng 2 để pilot cửa sổ thật, chỉ dùng 4 sau khi kiểm
memory/load. Không nhân số liệu ngắn này để hứa ETA campaign dài.

Việc tiếp theo: pilot ít candidate trên các fold đã đóng băng; profiling
sensitivity (hiện chạy serial), đo peak RSS, benchmark lặp không có workload
khác, rồi mới mở full grid. Chưa tuyên bố chiến lược đủ điều kiện live.

## Kiểm đường publication PASS — 2026-10-06

Entrypoint dùng `selected_wfo_subject`: selected parameters theo từng fold
phải thuộc frozen search grid; duplicate/missing folds và unfrozen keys bị
từ chối. Hash subject bind selected mapping, không nhầm với search-space hash.
Plan/search grid vẫn giữ nguyên; không sửa plan sau khi thấy winner.

Publication API tách `publication_root` khỏi `data_root`. Data root chỉ dùng
validate input; output phải nằm trong publication root đã khai báo, được resolve
trước kiểm tra. Nếu không truyền publication root, giữ ràng buộc cũ data root.
Entrypoint truyền đúng campaign output root, không nới root thành `/`.

`test_campaign_publication_end_to_end.py` dùng synthetic fixtures và source
identity giả được ghi rõ, nhưng chạy schema validator, ledger reconstruction,
consumer gate và atomic writer thật, không stub. Kiểm positive publication,
idempotent retry, mismatch params/gates/report/cost/source/output và selected
parameters ngoài grid. Đây là contract verification, KHÔNG phải strategy
qualification hoặc release evidence thực tế.

Nhóm cuối 90 regression PASS, lint/diff PASS
(`/tmp/publication_contract_finaltests.json`); AC01–AC15 chạy lại 15/15 PASS
(`/tmp/publication_contract_ac.json`). Kết quả được giữ trong
`data/acceptance_runs/publication_contract_20261006/` với nhãn contract tests,
không phải campaign REAL_MARKET hay policy được phát hành.

**Blocker lịch sử, đã xử lý ở native contract phía trên:** frozen plan trước đây chứa aggregate
gates + minimum-fold gate, còn WFO gate_results chứa native safety/provenance/
cost/sensitivity gates. Consumer yêu cầu exact gate-ID set/threshold/observation
match; campaign thực không thể vượt đường PASS này với plan hiện tại.
Không bỏ hoặc lọc native gates để làm PASS. Không lấy thresholds từ kết quả
hậu nghiệm rồi gọi là frozen.

Work package tiếp theo:

1. Centralize native gate specifications theo WFOSpec/descriptor/config trước
   chạy, gồm version, applicability, IDs, thresholds, comparator, units,
   metric/source references (bao gồm sensitivity và holdout khi applicable).
2. Freeze gate contract vào study/campaign plan; phân biệt research aggregate
   rules và native execution/safety gates bằng contract rõ ràng, không coi
   hai tập khác nhau là một tập rồi silently filter.
3. Producer chỉ đọc measured observations từ artifacts; consumer tái kiểm
   full applicable set và threshold identity against frozen contract.
4. Thêm real-shaped PASS/FAIL fixtures và tampered native gate negatives;
   bắt buộc AC01–AC15 nếu chạm WFO/promotion/evidence theo AGENTS.
5. Chỉ sau đó chạy smoke/3-fold campaign trong output mới, no-publish trước.

## Đồng bộ producer–consumer metadata — 2026-10-06

Frozen plan mới chứa `gate_version=campaign-aggregate-v1`; bundle sao chép đúng
version đã freeze. Overlap policy dùng enum `independent|overlapping`, từ chối
khai báo mâu thuẫn; validator vẫn kiểm các test spans thực tế. Bundle chỉ chứa
fold summaries khai báo `trades_absent_reason`: per-trade records nằm trong
source reports được bind bằng `results[].report_sha256`, không dựng trade giả.

Minimum-fold rule (2 folds) hiện là gate `campaign_minimum_folds` trong frozen
gate set; aggregate.fold_count được tái tính từ results. Không còn đánh dấu
mọi gate FAIL khi chỉ có một fold dù metric đã vượt ngưỡng. Không đổi ngưỡng
Sharpe/return/trades. Validator từ chối count sai ngay cả sau khi rehash bundle.

83 regression PASS (`/tmp/schema_alignment_finaltests.json`), lint PASS.
Producer tests gồm cả PASS/FAIL qua consumer structural validation; không phải
WFO qualification hoặc promotion. 15 producer tests PASS sau bổ sung tamper
count (`/tmp/schema_tamper_tests.json`). Plan cũ không được sửa hậu nghiệm;
phải freeze mới. Smoke mới: `data/campaign_smoke_20261006_schema_v4`, result
`/tmp/schema_alignment_smoke.json`; chỉ một candidate/fold và no-publish.

Smoke real-data v4 kết thúc sau 133,64s, rc=1, consumer đã qua structural
validation và từ chối `campaign is measured but NOT_QUALIFIED` (không còn ba
lỗi metadata/schema trước đó). Đây là qualification rejection dự kiến: một
fold và OOS âm, không phải strategy PASS. Chưa chứng minh đường publication
end-to-end với campaign thật đủ chuẩn. AC01–AC15 15/15 PASS
(`/tmp/schema_alignment_ac.json`). Logs/check results lưu bền vững ở
`data/campaign_smoke_20261006_schema_v4/verification/`.

Tiếp theo: review đường PASS end-to-end (selected params, native hard gates,
source/data bindings và publication directory constraints) bằng fixture hợp
lệ/negative tests trước campaign 3 folds. Không dùng structural PASS hoặc
single-fold smoke để tuyên bố strategy qualified/mainnet GO.

## Sửa binding sau khi dừng campaign — 2026-10-06

Campaign cũ PID 27705 và các worker đã được dừng theo xác nhận operator;
log/artifacts giữ nguyên, không sử dụng làm qualification.

Entrypoint hiện giới hạn frozen plan theo `--n-outer` và truyền
`max_outer_folds` vào WFO; WFO từ chối nếu thiếu folds đủ điều kiện sau holdout.
Purge/embargo của plan dùng cùng defaults của descriptor và cùng override với
runtime, không còn freeze giá trị 0 trong khi runtime tự dùng lookback gap.

`frozen_research_data` bind exact input path + SHA256 + subject cho loader
research; workers spawn kế thừa binding. Mỗi lần load kiểm hash và đọc chính
bytes đã hash, không fallback sang file extended. Context chỉ dành cho serial
campaign orchestration trong một process, không dùng cho threaded concurrent
campaigns; ingestion/live loader giữ nguyên. Binding được gỡ khi context thoát.

18 regression PASS (`/tmp/frozen_binding_finaltests.json`), thêm assertion dry-run
đúng 2 folds/strategy PASS (`/tmp/fold_limit_script_test.json`). AC01–AC15 sau
thay đổi WFO: 15/15 PASS (`/tmp/frozen_binding_ac.json`). Test loader chứng minh
child process kế thừa binding, subject sai/file thay đổi bị từ chối.
Dry-run BTC 1h real file 31.783 bars tạo đúng 1 fold (`/tmp/smoke_dryrun.json`).
Smoke thực thi dùng một candidate, một fold, 1m/1m/1m và no-publish trong
`data/campaign_smoke_20261006_binding`, controlled timeout 600s, result file
`/tmp/binding_actual_smoke.json`. Không suy ra strategy đủ chuẩn từ smoke nhỏ;
phải kiểm kết quả kết thúc và bundle trước khi claim PASS.

Smoke đầu kết thúc sau 142,46s, rc=1: collector dùng `return_pct` nhưng native
WFO xuất `total_return_pct`. Đã sửa mapping ở collector/consumer; legacy alias
chỉ được dùng khi canonical field vắng, hai giá trị mâu thuẫn bị từ chối.
Đồng thời plan lấy cost schedule từ paper runtime constants (10/5/0 bps),
thay vì hard-code 5/2/1 không phản ánh mô hình thực thi.
88 regression PASS, lint PASS (`/tmp/binding_final_regression.json`);
AC01–AC15 chạy lại trên các sửa mới 15/15 PASS (`/tmp/binding_final_ac.json`).
Smoke lần hai dùng output root mới `data/campaign_smoke_20261006_binding_v2`,
result file `/tmp/binding_actual_smoke_v2.json`, không ghi đè lượt đầu.

Lượt hai rc=1 sau 129,57s: consumer đọc bundle trên đĩa trước publication.
Đã sửa ordering: publication API tự staging/validate; no-publish ghi diagnostic
bằng exclusive-create rồi gọi consumer trên đúng thư mục strategy. Diagnostic
không được ghi đè và không có nghĩa đã admissible. 32 regression PASS
(`/tmp/binding_order_tests.json`), 2 test runtime binding cấp entrypoint PASS
(`/tmp/campaign_runtime_binding_tests.json`).

Lượt ba kết thúc rc=1 sau 157,70s. Data/fold binding đúng, có một outer artifact
và `enhanced_ma/campaign_evidence.json` diagnostic, nhưng consumer từ chối:
thiếu `gate_version`, overlap policy `non_overlapping` không thuộc enum
`independent|overlapping`, thiếu trades hoặc `trades_absent_reason`.
Đây là lỗi hợp đồng producer/consumer còn phải sửa, không gọi smoke PASS.
OOS row: return -1,44649%, Sharpe -4,28694, net -144,64875, 5 trades;
bundle verdict FAIL, không qualification và không publication. Không nhầm
headline sensitivity/warm-up +3,20% với outer OOS row này.

Verification JSON và log smoke được lưu trong
`data/campaign_smoke_20261006_binding_v3/verification/`.
Việc tiếp theo: đồng bộ metadata schema của frozen plan/native builder và
consumer, kiểm FAIL artifacts vẫn tự-consistent; sau đó mới chạy lại smoke,
review và campaign 3 folds. Không sửa gate ngưỡng để biến FAIL thành PASS.

## Kiểm tra process 2026-10-06 và việc chuẩn bị tiếp theo

Campaign PID 27705 còn chạy tại thời điểm kiểm tra, elapsed khoảng 19 giờ;
worker PID 104867 vẫn dùng CPU và `/tmp/campaign_real.log` tiếp tục cập nhật.
Không kết luận treo chỉ từ CPU thấp của process cha. Lệnh dùng `--no-publish`.
Chưa có bundle cuối trong `data/wfo_real_campaign/enhanced_ma/`; có frozen plan.
132 report files trong output root là số tổng, không phải số folds hoàn tất
của lượt này. Registry có nhiều bản ghi cũ; phải đối chiếu OS process thực.

Các commit mới đã có frozen plan/native builder và isolation/smoke-grid;
nhận xét cũ “chưa có builder” không còn mô tả đầy đủ HEAD hiện tại.

Checklist sau khi process kết thúc (không sửa source đang chạy):

1. Thu exit status nếu launcher có lưu; nếu không, ghi UNKNOWN thay vì suy ra
   PASS từ việc PID biến mất. Lưu log, plan, reports và bundle vào evidence run.
2. Kiểm phạm vi: CLI `--n-outer 3` nhưng plan hiện có 11 folds. Trong script
   hiện tại n_outer dùng cho mô tả/span, chưa giới hạn `plan_folds_from_spec`.
   Làm rõ semantics và thêm test CLI trước lượt campaign kế tiếp.
3. Kiểm binding dữ liệu: plan dùng explicit `--data-file`, nhưng
   `run_nested_wfo(spec, out_root=evidence)` không nhận frame/path trực tiếp.
   Chứng minh digest/timestamps/bar count/fold indices thực thi khớp plan;
   nếu không khớp, kết quả chỉ diagnostic, không dùng promotion.
4. Đối chiếu source trước/sau, chi phí mô hình, report digests, accounting,
   gate observations và verdict; không nới gate để đạt PASS.
5. Kiểm integrated AC summary và source identity. Log `/tmp/ac0115.log`
   có các AC PASS nhưng không thay thế kiểm manifest/SUMMARY đầy đủ cùng revision.
6. Chỉ khi evidence hợp lệ mới review độc lập và cân nhắc publication;
   `--no-publish` không cấp quyền activation hoặc live orders. MAINNET NO-GO.

Không restart/kill process hoặc chạy campaign thứ hai vào cùng output root.
Chưa có ETA đáng tin cậy; heartbeat/progress theo fold nên được bổ sung sau
lượt này để các lần chạy dài có exit status, timeout và tiến độ đo được.

## Cập nhật 2026-10-05: native rows và fail-closed entrypoint

`collect_wfo_campaign_rows` dựng lại accounting từ trades/equity trong report
nguồn, đối chiếu declaration và metric WFO, xuất digest đúng bytes report.
Hai entrypoint `run_real_wfo_campaign.py` và `build_campaign_policy.py` đã gọi
bước này trước consumer gate. Report thiếu/sai, fold trùng, inventory mở hoặc
metric không khớp bị từ chối; không điền dữ liệu giả để vượt gate.

Regression mới: **74 PASS**, 13,78s (`/tmp/entrypoint_safety_tests.json`). Bao gồm
collector/writer/consumer/schema/integrity và ba test cấp script chứng minh
source thay đổi, accounting sai, bundle thiếu đều dừng trước policy builder,
không tạo promotion store. Test script dùng WFO mock, không phải campaign thật;
chưa bao phủ entrypoint nhiều strategy `build_campaign_policy.py` ở cấp script.

Đây mới là producer của accounting rows, **chưa hoàn tất native campaign bundle**.
Thứ tự tiếp theo bắt buộc:

1. Đóng băng trước chạy: digest dữ liệu, spans fold với quy ước biên rõ ràng,
   purge/embargo, toàn bộ gate IDs/ngưỡng/đơn vị và source context.
2. Dựng payload schema-v2 từ kế hoạch frozen và kết quả đo; kiểm digest dữ liệu
   sau chạy, không lấy gate hậu nghiệm rồi gọi là frozen.
3. Gọi publication API sau validation, dùng run directory riêng; không ghi đè.
4. Chạy campaign thật cùng regression trên một source snapshot, lưu bằng chứng
   bền vững và reviewer độc lập kiểm lại trước promotion.

Carry/open inventory và impact/cap credit chưa biểu diễn đầy đủ vẫn fail-closed.
Soak venue chưa hoàn thành; **MAINNET NO-GO**. Các log `/tmp/` chỉ là kết quả
kiểm tra cục bộ, không thay thế release evidence bundle.

## Kiểm tra mới nhất: accounting strictness

`campaign_writer.publish_campaign_bundle` đã có publication API: snapshot JSON
finite vào staging, gọi consumer/schema gate trên chính bytes đó, fsync rồi
atomic create không ghi đè. Bundle khác ở cùng run directory bị từ chối;
idempotent retry vẫn kiểm report nguồn; concurrent same-payload publishers
không thay thế nhau. Payload thiếu bị validator thật chặn trước publication.
**64 test PASS** (`/tmp/campaign_writer_finaltests.json`), lint/diff PASS.
Test publication mechanics stub consumer để cô lập filesystem, không chứng
nhận một WFO campaign thành công. Đây chưa phải native payload builder và
chưa được gọi tự động bởi campaign scripts; không đánh dấu bước 3 hoàn thành.

Consumer campaign hiện yêu cầu `results[].report_sha256`, đọc đúng bytes
report của EvaluationArtifact, rồi tái tính paper accounting từ trades/equity.
Declared accounting phải khớp ledger tái tính; gross/fees/slippage/spread/net/
trade count của campaign phải khớp source report. Report thiếu, bị sửa (kể cả
tái hash nhưng ledger sai), inventory mở hoặc row cost sai đều bị từ chối.
Impact/cap credit nonzero vẫn bị chặn vì campaign schema hiện chưa biểu diễn
đầy đủ; không bỏ thành phần để làm gate xanh. **115 regression PASS**
(`/tmp/source_binding_regression.json`). Fixture consumer cô lập validator,
không phải chứng nhận campaign thực. Native bundle writer còn phải xuất
report digest và trường accounting từ chính lượt đo.
AC01–AC15 sau consumer binding **15/15 PASS** (`/tmp/source_binding_ac.json`),
lint/diff PASS. Không thay thế bằng chứng campaign/soak thực tế.

Paper runtime chính hiện ghi `paper_cost_basis_v1` trong state và
`metadata.measured_accounting` trong trade tại fill-time; market/stop/forced
close có reference thực trong mô hình. Partial exit phân bổ basis và restart
giữ basis. Legacy state, limit fill chưa có decomposition, mirror venue thiếu
reference không được chứng nhận. Mô hình paper chỉ có slippage/commission:
zero spread/impact là giới hạn mô hình được ghi rõ, không phải đo trên venue.
Full-system report đã xuất measured accounting khi closed ledger khớp equity;
tournament tái tính trên measurement window, không giữ evidence full-run
sau khi clip. Boundary carry/open inventory vẫn fail closed.
Native partial/restart/report contract **46 PASS**
(`/tmp/native_partial_restart_tests.json`); AC trước cập nhật clip **15/15 PASS**
(`/tmp/native_measured_ac.json`). Campaign bundle writer/promotion binding và
operational soak chưa đóng; không tuyên bố đã hoàn thành tất cả bước.

Nhóm native paper/tournament sau nối report **60 PASS**
(`/tmp/native_window_regression.json`); AC01–AC15 chạy lại **15/15 PASS**
(`/tmp/native_final_ac.json`), lint/diff PASS. API paper accounting đã được
kiểm bằng roundtrip và partial → restart → close với expected gross/fees/
slippage/net tính độc lập. Report có `measured_accounting` hoặc lý do từ chối.

Ledger của execution backtest simulator đã sửa partial exit tính theo quantity
thực khớp, phân bổ entry/exit fee và components theo tỷ lệ, pooled cost basis,
short/reversal và idempotent fill replay. Closed-trade exporter đã nối vào
`SimulatorBacktestResult.accounting_evidence` khi flat; inventory mở giữ
NOT_QUALIFIED. Equity cuối kỳ được cập nhật sau liquidation. Tổng cost summary
dùng component thực đo và giữ riêng price-cap credit, không trộn drag vào
slippage. Exporter yêu cầu explicit price_cap_credit cho mỗi leg.
Regression gộp mới **215 PASS** (`/tmp/ledger_export_regression.json`).
AC01–AC15 chạy lại **15/15 PASS** (`/tmp/ledger_export_ac.json`), lint/diff PASS.
Đây là đường backtest simulator, không đồng nghĩa WFO/paper runtime đã dùng
ledger này. Native campaign exporter, boundary carry, runner soak và release
bundle tích hợp bền vững vẫn còn mở; MAINNET NO-GO.

Rà soát commit `bb8c795`: per-fill component đã có ở backtest simulator,
chưa nối vào WFO/paper runtime hoặc exporter native. Đã sửa caller market
SELL truyền rate sai dấu; helper từ chối NaN/Infinity/bool và chuỗi rate
không khớp fill. Limit cap ghi riêng `price_cap_credit`; invariant gồm
spread + slippage + impact + cap credit = signed fill drag. Exporter giữ
maker spread/impact credit âm thay vì từ chối mọi giá trị âm.
**46 test PASS** (`/tmp/latest_fill_finaltests.json`). Còn phải truyền
component/credit từ fill sang partial/carry ledger và campaign export;
không được bỏ credit hoặc coi commit per-fill là hoàn thành cả workflow.

Exporter `backtest/accounting_evidence.py` đã có API closed-trade accounting,
đối soát component entry/exit với fill drag, net từng trade với fills/fees và
tổng net với equity delta độc lập. Không mặc định component thiếu bằng 0;
inventory mở, boundary carry, duplicate ID, số không hữu hạn đều bị từ chối.
Output không chứng nhận strategy và chưa nối vào native WFO/campaign producer.
Producer cần ghi `metadata.simulation.execution_components.entry/exit`, mỗi
leg có `slippage`, `spread`, `market_impact` bằng quote currency; thiếu nguồn
đo thì giữ gate từ chối. Không lấy rate cấu hình thay chi phí thực đo.
Test exporter/reporting/campaign binding: **82 PASS**
(`/tmp/accounting_export_finaltests.json`); lint PASS. Đây là unit contract
với fixture độc lập, không phải qualification campaign thực đo.

Regression gộp **165/165 PASS** (`/tmp/cost_strict_regression.json`),
AC01–AC15 **15/15 PASS** (`/tmp/cost_strict_ac.json`), lint/diff check PASS.
Có 6 phản ví dụ cho fee thiếu/âm, boolean quantity, quantity 0, NaN PnL
và Infinity price. Exporter cost-components và operational soak vẫn chưa
hoàn thành. Log tạm không thay thế release evidence bundle bền vững.

## Phạm vi và trạng thái

Đây là bản bàn giao triển khai validator và consumer gate, không phải chứng nhận
strategy đủ điều kiện live. MAINNET vẫn NO-GO. Chưa chạy campaign lớn hay gửi
lệnh ra sàn trong lần sửa này.

## Đã triển khai

- Campaign schema v2 kiểm file dữ liệu tồn tại trong data root, SHA256 thật,
  số dòng và hash nội dung OHLCV của cửa sổ khai báo.
- Từ chối metric thiếu, NaN/Infinity, kết quả fold trùng, aggregate sai và
  accounting không khớp `gross - fees - slippage - spread = net`.
- Kiểm OHLCV thực: timestamp không rỗng/trùng/đảo thứ tự, giá hữu hạn và
  dương, volume không âm, high/low bao được open/close. Hash đúng không thể
  thay thế kiểm tra này.
- Tái tính verdict từ gate set; hash của gate set và campaign không thay thế
  kiểm tra ngữ nghĩa. FAIL hợp lệ vẫn không được promotion.
- Hai script `run_real_wfo_campaign.py` và `build_campaign_policy.py` kiểm
  evidence trước khi tạo/ghi policy, đối chiếu subject, fold, params, metric,
  artifact, cost và gate với kết quả WFO thực tế. Source được chụp trước/sau
  campaign để phát hiện thay đổi trong lúc chạy.
- L2 chuẩn hóa symbol slash/underscore, timeframe alias và tie-break bằng
  strategy ID sau các tiêu chí định lượng; không còn phụ thuộc thứ tự input.
- Counterexample được sửa sau khi tạo fixture và tái hash identity để chứng minh
  validator không chỉ từ chối vì campaign ID cũ.

## Những việc agent tiếp theo phải làm

1. Bổ sung exporter từ accounting thực đo của WFO, không mặc định chi phí thiếu
   bằng 0. WFO hiện chưa xuất đầy đủ schema v2; consumer gate sẽ từ chối.
2. Chứng minh canonical dataset dùng trong selector và WFO là cùng dữ liệu,
   không chỉ cùng symbol/timeframe. Gắn input digest vào artifact đo thực.
   WFO/tournament hiện đã dùng `load_research_ohlcv`, chung resolver ưu tiên
   lịch sử mở rộng và storage root cấu hình với selector. `load_ohlcv` cho
   ingestion/live vẫn giữ nguyên exact-path semantics. Test cục bộ chứng minh
   resolution tương đương; vẫn cần digest frozen trong artifact campaign.
   Tournament đã truyền chính `source_df` vào simulator thay vì để simulator
   tự đọc file lần nữa. Injected frame yêu cầu manifest rõ ràng. Lỗi cũ làm
   test synthetic thực thi với 31.783 bar thật trên máy; không chỉ là mock
   loader cũ. Đã cập nhật các fixture loader, chưa được dùng kết quả test lỗi
   cũ làm evidence qualification.
3. Kiểm thêm phân loại gap và tính đúng purge/embargo theo bar thực tế.
   Validator hiện chưa chứng minh đầy đủ hai điều này.
4. Kiểm accounting per-trade với oracle độc lập, bao gồm cash/fees/realized PnL
   qua restart; kiểm tổng fold không thay thế kiểm ledger.
   `calculate_cost_attribution()` đã sửa gross từ reference prices/quantity/side
   độc lập với net, có test long/short và net cố ý sai. Không dùng trực tiếp
   làm qualification exporter: spread/impact vẫn chưa đo riêng và slippage
   chứa execution drag gộp (`cost_components_separated=False`).
   Accounting hiện từ chối thiếu fee/PnL/quantity/prices, bool thay số,
   NaN/Infinity, giá hoặc quantity không dương, fee âm; không điền 0.
   `complete` chỉ nói đủ dữ liệu; `reconciled` kiểm residual riêng.
   `qualification_eligible=False` cho attribution chưa tách chi phí.
   Exporter phải đo gross từ reference prices/quantity/side, tách chi phí
   entry/exit, xử lý boundary carry và open inventory rồi so với net ledger.
5. Bổ sung test ở cấp script để khẳng định evidence thiếu/sai không gọi
   registry/activation. Unit test consumer đã kiểm payload so với WFO object,
   nhưng stub validator nhằm cô lập binding: không phải campaign thực đo hay
   chứng nhận strategy.
6. Instrument runner cho schema soak mới: subject cố định, health, correlation
   và liên kết fill với stop. Log cũ chỉ dùng chẩn đoán, không đủ qualification.

L2 signal density chỉ là ưu tiên nghiên cứu; không phải số lệnh đã khớp,
round trip có lãi, unseen holdout hay bằng chứng alpha. Source fingerprint
hiện bao phủ Python trong `src/` và `scripts/`, chưa đóng băng toàn bộ dependency
và cấu hình môi trường.

## Kiểm tra đã chạy

Regression campaign/canonical/L2/soak/research-loader mới: **136 passed**;
lint PASS. Kết quả: `/tmp/research_tests.json`.
Bao gồm 12 case consumer binding và 5 case OHLCV được tái hash sau khi làm sai.
Consumer cũng từ chối NaN cost và duplicate fold/gate trong WFO object thực tế.
AC01–AC15 chạy lại sau thay đổi WFO loader: **15/15 PASS**, exit code 0.
Kết quả máy đọc: `/tmp/consumer_quality_regression.json` và
`/tmp/research_frozen_ac.json` (tạm thời, không phải release bundle bền vững).
Không suy diễn từ các test cục bộ này rằng đã nghiệm thu vận hành live,
đạt hiệu quả strategy hoặc hoàn thành soak trên sàn.

Nhóm WFO/tournament mở rộng **chưa xác nhận PASS toàn bộ**. Lượt trước phát
hiện lỗi synthetic đọc nhầm dữ liệu thật; sau sửa source-frame injection,
lượt chạy lại ghi nhận một test hoàn tất (`.`), rồi timeout 180 giây khi
nhóm còn chạy. Không coi timeout là PASS và không cộng nhóm này vào 136 test.
Lần chạy sau cần tách từng test/cell, giảm grid/fold của fixture và lưu timing;
không chỉ tăng timeout hoặc bỏ assertion sensitivity.

Ngày 2026-10-05: tách nhóm WFO evidence và tournament, giữ test sensitivity
thực đầy đủ; hai test registry chạy `real_sensitivity=False` vì chỉ kiểm
trial counts/idempotency. Reporting/accounting regression: **23 passed**,
kết quả `/tmp/oct5_cost_tests.json`. Tournament **43/43 PASS** trong 155,38s;
case fault tốn khoảng 23–27s/case (`/tmp/oct5_tournament.json`). AC05 đã sửa
fixture net gồm phí và oracle gross độc lập, thêm wrong-net counterexample;
AC01–AC15 chạy lại **15/15 PASS** (`/tmp/oct5_ac_corrected.json`). Nhóm WFO
evidence **4/4 PASS** trong 321,16s (`/tmp/oct5_wfo_evidence.json`): sensitivity
166,46s, registry idempotency 82,14s, registry count 49,92s, holdout 8,33s.
Timeout nhóm 180s trước đó không phù hợp tổng workload, không phải chứng minh
deadlock. WFO/tournament được khởi chạy trước sửa reporting trong cùng lượt
làm việc; kết quả này chứng minh loader/fixture, không phải full regression
của accounting mới. Accounting mới được kiểm bằng 23 test và AC tích hợp.
