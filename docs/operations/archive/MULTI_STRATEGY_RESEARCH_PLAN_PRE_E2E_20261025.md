# Kế hoạch nghiên cứu nhiều strategy — từ diagnostic đến qualification

Khởi tạo: 2026-10-16. Cập nhật: **2026-10-25 — BASELINE CLOSED / NO_AUTOMATIC_NEW_RUN**.

## 0. Hợp đồng công việc hiện tại — đọc trước lịch sử bên dưới

Phần này thay thế thứ tự hành động ở các snapshot lịch sử, không thay đổi gate hay cấp quyền execution. Không tạo thêm framework, runner hoặc tracker. Mục tiêu gần nhất là **một hypothesis có phép đo đáng tin**, không phải bắt buộc tìm winner hoặc chạy hết registry.

### Kết quả mới nhất — đã đóng một vòng đánh giá

RSI/ADX BTC1h: 3/3 cells hoàn tất — local evidence `data/rsi_fixed_validation_run_20261024_01/RESULTS.md`, mỗi mức phí39 trades nhưng Sharpe -2.514/-3.643/-4.005; selection ABSTAIN, chưa qualified. Gross trước chi phí cũng âm. Không tự rerun/retune/promotion; các bước chờ duyệt trong lịch sử bên dưới không còn là trạng thái hiện tại của lần chạy này. Một hypothesis mới cần lý do kinh tế và scope/authorization riêng; không mở thêm framework.

### H-BRANCH-01 — đã đóng điều tra, không sửa strategy

Final attribution — local evidence `data/rsi_branch_attribution_20261024_01/RESULTS.md`:12.960/12.960records khớp;17trade khác nhánh có net+36,28 ở1x. Cơ chế có thật nhưng chưa chứng minh gây lỗ hoặc owner-lock có lợi. Không chọn range-only từ7trade hậu nghiệm, không bỏ MA/stop. KEEP_STRATEGY_UNCHANGED / NO_AUTOMATIC_FOLLOWUP_RUN. Không engine rerun, không mở holdout, không đổi gate. Scope nghiên cứu mới phải có luận điểm kinh tế độc lập và authorization riêng.

Supporting evidence: 16rawreports comparison — local evidence `data/economic_comparison_20261024_01/RESULTS.md`, 6native synthetic cases — local evidence `data/rsi_branch_synthetic_20261024_01/RESULTS.md`. Đây không phải bộ chứng minh live-ready; giữ RSI ABSTAIN, BNB HOLD.

### H-SIMPLE-BASELINE-01 — hoàn tất, ABSTAIN

Native baseline MA20/50 — local evidence `data/ma_simple_baseline_validation_run_20261024_01/RESULTS.md`:3/3cells hoàn tất,64closed trades/cost, Sharpe-0,162/-1,544/-1,995. Marked return-0,481/-4,002/-5,149%;1x gồm closed net-363,76 và open PnL+315,51. Đủ mẫu nhưng selectionABSTAIN, chưaqualified. Không retry, retune hoặc automatic follow-up.

Measurement822,5142) khácRSI[920,5240), không phải paired ablation hoặc bằng chứng regime có hại. Benchmarksidecar cùngwindow đã tính; passive25%marked+3,436%1x chỉ là context, khôngrisk-matched/alpha. Source không đổi; integrator review, chưa independent review mới. [Đề xuất cũ — local evidence `data/measurement_reference_20261024_01/PROPOSAL.md` giữ nguyên như lịch sử; quyền một run đã dùng hết, không còn pending run hay quyền tái chạy.

Giai đoạn bounded evaluation đã đóng với kết quả âm; live vẫnNO-GO. Hướng nghiên cứu mới cần luận điểm kinh tế và scope riêng, không mặc định quét MA, đổi timeframe hoặc thêm framework để cứu backtest.

### Baseline registry gap — đã đóng, không cần audit lặp

Read-only reconciliation3/3 — local evidence `data/ma_simple_baseline_validation_run_20261024_01/postrun_registry_review_20261025_01/RESULTS.md` nối ledger/worker/report với đúng experiment và execution context; metric khớp, không duplicate. Ledger cũ giữ UNKNOWN lịch sử; sidecar không đổi verdict. Manifest provenance_eligible=false vì worktree_dirty=true: trước qualification tương lai cần revision sạch được review, không relabel measurements cũ hoặc tự commit/reset user edits. Không đổi gate, không rerun để đổi nhãn. Gap thống kê DSR và edge vẫn không được chứng nhận bởi audit này.

### H-PULLBACK-01 — preparation hoàn tất, chờ duyệt đúng một run

Exact protocol/plan — local evidence `data/trend_pullback_prereg_20261025_01/RESULTS.md` đã dựng qua publicpreflight:defaultSMA20/80,ADX25,volume1.5,BTC1h;1fold×3costs,measurement912,5232),source8df9a071…stable. Proposedbudget600s/cell/2100stotal/1worker/2048MiBsampledRSS/0retry/failfast. PREPARED_NOT_AUTHORIZED;0market/engineevaluation ở preparation.

[Admission4/4 — local evidence `data/trend_pullback_admission_20261025_01/RESULTS.md` không thay economics; discovery10trades/Sharpe-3,018 lịch sử không bị xóa hoặc resettrialburden. Exposed2023input chỉ exploratory, không đủlive2026; dirtysource provenanceeligiblefalse giữ nguyên. Một run mới cần freshhumanapproval pin exactscope; khôngtuning/extension/autofollowup hoặc runner mới.

### Đã đóng, không tự mở lại

| Công việc | Kết quả và bằng chứng | Không suy ra |
| --- | --- | --- |
| Refactor validation C00–C05 | Nghiệm thu bounded scope — local evidence `data/validation_refactor_final_20261023_01/RESULTS.md` | Không phải speedup được đo hoặc quyền rerun |
| BNB state-edge economic review | 13 completed cells — local evidence `data/bnb_stateedge_economic_review_20261023_01/RESULTS.md`; HOLD / NOT_QUALIFIED / NO_RERUN | Không biến 2 timeout thành economic FAIL/PASS; không gọi post-hoc diagnostics là OOS |
| Inventory có giới hạn | 22 raw reports, 9/18 broad IDs — local evidence `data/strategy_evidence_inventory_20261023_01/RESULTS.md` | Không phải exhaustive audit hoặc 22 independent trials |
| RSI/ADX metadata lineage | Audit đã đóng — local evidence `data/rsi_lineage_closure_20261023_01/RESULTS.md`; LEGACY_DESCRIPTIVE_ONLY | Không phải original packet đã phục hồi; không dùng metadata hash như file SHA |

Không sửa các packet này để nâng verdict. Source reference của engineering handoff là `fe166b1923280620e942824e9fec5353844cb75dc9039bf030760fc1a1076b38`; phải tính lại khi bắt đầu công việc có execution, không gán receipt cũ cho source mới.

### Một tuyến tiếp theo, chia nhỏ và có điểm dừng

| Bước | Đầu việc và owner | Điều kiện hoàn tất / dừng |
| --- | --- | --- |
| N0 — quyết định hypothesis | Operator + research owner chọn đúng một candidate/pair/timeframe và lý do kinh tế, ghi ngay vào tracker hiện có | Chưa chọn thì **AWAITING_HYPOTHESIS_DECISION**, không tự lấy max Sharpe, chạy mọi ID hoặc mặc định RSI/ADX là winner |
| N1 — admission read-only | Research owner đọc implementation và canonical adapter của subject đã chọn; ghi exact params, entry/exit, history, timing, native sizing/stop/cost, required data | Bất tương thích semantics hoặc thiếu input ⇒ BLOCKED có lý do; không xây short/portfolio/funding engine để vượt blocker |
| N2 — preregistration | Research owner soạn một payload mới: source/input identities, timestamp windows, fixed params, costs, gates từng cấp, trial ledger, terminal outcomes và budget | Không đủ eligible data hoặc runtime basis ⇒ INCOMPLETE_PROPOSAL; không chọn window sau khi xem lợi nhuận, không gọi dữ liệu đã expose là untouched |
| N3 — review và authorization | Reviewer khác tác giả đọc source/assertions/payload; operator duyệt chính scope/read/decode và budget của một attempt | Review kỹ thuật không tự cấp quyền. Approval phải bind payload thực tế; sửa ngoài approval fields làm mất hiệu lực và cần đánh giá lại |
| N4 — một phép đo giới hạn | Execution owner dùng shared launcher + frozen binding thống nhất cho plan/worker/native; packet mới, một worker, 0 automatic retry | Probe là cell đầu chính thức, tính vào tổng; fail/timeout/cap ⇒ giữ receipt và dừng, không tự tăng timeout, đổi geometry hay rerun |
| N5 — verdict và bàn giao | Reviewer đối chiếu raw reports, coverage, accounting, costs, concentration và missing cells; cập nhật tracker | Validation-only không thể QUALIFIED. COMPLETE + ABSTAIN không phải winner; thiếu coverage là INCOMPLETE; chỉ đề xuất qualification khi có căn cứ và protocol riêng |

Các con số 15 cells / 300s / 5400s của BNB cũ **không phải budget mặc định cho N4**. Các đoạn đề xuất BTC4h/5 candidates, dự báo mật độ, phase R0–R3 và số17 bên dưới là lịch sử, không phải lệnh tự tiếp tục.

### Mẫu quyết định N0 — điền trong CAMPAIGN_STATUS, không tạo tracker mới

```text
Hypothesis: <cơ chế kỳ vọng sau chi phí, có thể bác bỏ>
Subject: <một strategy_id / pair / timeframe>; params: <exact values>
Lý do chọn: <semantics + evidence liên quan, không chỉ Sharpe tốt nhất>
Đã expose: <phạm vi biết được>; UNKNOWN: <ghi rõ, không suy untouched>
Không thay đổi: native authority/stop/sizing, thresholds, holdout restrictions
Tiêu chí dừng: <missing input/parity/sample/economics/budget; kết quả âm được giữ>
Quyền hiện có: read-only / proposal only; execution authorization: <PENDING hoặc reference>
```

**Khuyến nghị hiện tại:** chưa rerun BNB chỉ để lấp hai ô trống; chưa đầu tư tiếp vào phục hồi RSI packet cũ. Chọn hypothesis trước khi viết code hay mua thêm một vòng tính toán. Có thể chọn RSI/ADX như một nghiên cứu MỚI hoặc một candidate chưa được rà, nhưng không bên nào có ưu thế đã được chứng minh từ inventory hiện tại. Nếu không có lý do kinh tế cụ thể, dừng nghiên cứu tại HOLD là kết quả hợp lệ.

### Native timing follow-up — chưa admit market

Synthetic native contract — local evidence `data/rsi_native_contract_20261023_01/RESULTS.md`:11/11 PASS (2 mới+9 indicator cases), lint PASS; strategy/engine không đổi. Mọi BUY/SELL action fixture khớp full-frame signal tại bar−2; mọi closed trade thật có entry timestamp/open×1,0005 đúng bar thực thi. Đây không phải qualification hoặc parity cho mọi params.

Flat fixture xác nhận RSI0/ADX0 ⇒ BUY liên tục; signal không đồng nghĩa fill. Cần chốt contract no-change⇒neutral50 hay giữ legacy BUY có chủ ý trước N2. Đề xuất50/0/100 cho flat/only-loss/only-gain chưa được implement; không tự đổi behavior hoặc chạy market. Data/budget mới vẫn chưa chốt.

### Checklist chung cho agent nhận việc

**Latest data revision (2026-10-24):** verified pre-holdout reuse — local evidence `data/btc1h_preholdout_layout_20261024_01/RESULTS.md` avoids decoding current raw files whose single row group crosses holdout. Existing20261008 frozen input hash45ba1b… verified; new byte-identical copy25198rows has fresh quality PASS,0 reserved timestamps. It is previously exposed research, original raw-parent SHA remains unknown. New6160-row prefix+onefold/3cost proposal is materialized, not evaluated. Budget600s/cell/2100s total and requested first-failure/probe rule are **not approved or proven enforced**. Next is data/proposal/stop-semantics review then official protocol and explicit run authorization, not another strategy change. Old raw mismatch remains historical fact, but a new explicit candidate-input route now exists. No market/OOS/holdout/sensitivity/live run.

**Latest review/data preflight:** separate-author review — local evidence `data/fixed_params_review_20261023_01/REVIEW.md` accepts launcher synthetic scope on source4930c28c… (inherited context/no rerun). Timestamp-only preflight — local evidence `data/fixed_params_review_20261023_01/RESULTS.md` now proves **DATA_IDENTITY_BLOCKED**: current physical1h SHA392712… differs from manifest aedecd…; neither file can be silently relabeled. Timestamp decoding found27183 strictly pre-holdout1h rows with1 non-hourly step; extended/full53456 with15 steps. No OHLCV columns decoded, but whole-file bytes hashed and holdout timestamps read. Windows/price-quality/exposure eligibility not approved. Do not run preflight with the price-reading resolver or update manifest to conceal mismatch. Next decision is explicit data revision/price-decode scope, not more strategy code.

**Latest fixed-param implementation:** shared launcher slice — local evidence `data/fixed_params_validation_20261023_01/RESULTS.md` adds `--fixed-params` scalar JSON for validation/preflight only. Defaults resolve into a singleton grid and frozen worker/native parameters; no flag preserves old grid.102 distinct selected tests verified (101 fast+1 real synthetic launcher integration), lint PASS; source `4930c28c2fc4cb509888076c97da3aa74c4fd3edc78f27818dca8ea7b79714f3` stable. Integration is SYNTHETIC_TEST_ONLY/NOT_QUALIFIED, not economics. Parameter-binding blocker below is historical and resolved only in this tested scope. Independent review of this new launcher patch and data-eligibility/windows/cost/budget are still open. `--fixed-params '{}'` is an input example, not a market command or approval. Prior source review/AC is not inherited.

**Latest admission review:** separate-reviewer verdict — local evidence `data/rsi_admission_review_20261023_01/REVIEW.md` is ACCEPTED_FOR_SYNTHETIC_SCOPE on source5a2a71ec… (inherited context, no fresh test run). Economic admission remains **BLOCKED_PARAMETER_BINDING**: CLI RSI/ADX pins legacy ADX15/RSI35–65/MA20–50, not the proposed defaults ADX20/RSI30–70/MA20–80. Next-measurement preflight — local evidence `data/rsi_admission_review_20261023_01/NEXT_MEASUREMENT.md` defines the smallest proposed explicit fixed-param input slice and data-scope unknowns. No global grid edit, production change, test execution or market run in this review. Metadata-only manifest read is not fresh verification of file SHA/eligible timestamps. Do not issue the legacy CLI command as if it represented this hypothesis.

**Latest RSI edge-contract update:** operator đã duyệt flat⇒50/only-loss⇒0/only-gain⇒100, và patch synthetic — local evidence `data/rsi_neutral_contract_20261023_01/RESULTS.md` đã thực thi:48/48 integrated cases + lint PASS trên stable source `5a2a71ec678699795e2220024f8dece9db13737e10e2751f412365923c1bc9f4`. Cửa sổ mixed giữ formula cũ, warmup null không đổi. Flat decision phía trên là lịch sử đã đóng; không hỏi lại hoặc giữ nhãn flat-BUY hiện hành. Chỉ RSI trong candidate này đổi, standalone `rsi` không đổi. N2 vẫn pending review/data/windows/cost/budget mới; không có market authorization, không chuyển economics cũ sang source mới.

1. Đọc AGENTS.md, tracker Current và bốn handoff ở trên; xác nhận tool thực sự có trong session. Thiếu `execute_shell_command` ⇒ báo TOOL_UNAVAILABLE, không invent alias hay tự đổi công cụ execution.
2. Kiểm process thực tế trước sửa shared source; không coi registry stale là bằng chứng process đã dừng. Không chạm process của user.
3. Công bố scope/owner/allowed writes/budget trước thực thi; không gộp engineering verification với market budget.
4. Preserve dirty edits và packet cũ; mismatch identity ⇒ dừng, không refresh manifest để che drift.
5. Báo đúng test IDs, source identity và receipt; không cộng test overlap, không gọi self-verification là independent review. Chạm gate files ⇒ AC01–AC15 theo AGENTS.md.
6. Handoff phải nêu completed / UNKNOWN / blocked / next decision. Không tự tạo loop cho tới khi có lợi nhuận, không mở live/paper/account/broker hoặc holdout từ kế hoạch này.

### N0/N1 follow-up — đề xuất RSI/ADX, BLOCKED_SEMANTICS_REVIEW

Sau yêu cầu tiếp tục của operator, research owner đề xuất **một** subject: `rsi_adx_regime_filter / BTC/USDT / 1h`, spot long-only qua native policy. Đây là đề xuất admission mới, không phải winner được chọn từ Sharpe, không kế thừa packet cũ hoặc approval market. Lý do: một cơ chế kết hợp range/trend bằng OHLCV có thể kiểm tra mà không thêm funding/universe/short engine.

**Hypothesis kinh tế:** chuyển giữa RSI mean-reversion khi ADX thấp và MA event-crossover khi ADX cao có thể tạo đủ closed trades và lợi nhuận sau chi phí bền qua các windows đã đăng ký. Có thể bị bác bỏ bởi thiếu mẫu, kết quả âm/không đạt gates hoặc cost stress; không có cam kết phải pass. Phép đo fixed policy không được diễn giải thành bằng chứng router tốt hơn RSI/MA riêng: muốn claim tương đối cần comparator và protocol/budget riêng.

**Params đề xuất = defaults hiện tại, không lấy điểm tốt nhất sweep cũ:** `rsi_period=14`, `rsi_oversold=30`, `rsi_overbought=70`, `ma_fast=20`, `ma_slow=80`, `adx_period=14`, `adx_threshold=20`, `ma_type=sma`. ADX20 ở candidate này không đổi reference ADX25 của `ma_adx`/BNB. Chưa đóng băng experiment identity hoặc data windows.

Static review đọc đầy đủ implementation và các phần schema/default/registry/adapter liên quan trong `canonical/candidates.py` (không phải execution review toàn chuỗi). Hash file tại lượt đọc:

- `rsi_adx_regime_filter.py`: `c231526c267aa6e645aef4ea9f2c5dbd0729fae3f6ad5ded7c3d0d79b30f088d`
- `canonical/candidates.py`: `7a4a075ad5957a7b12502f8314861be9253afd8a7a9404f127d0843c611e530b`

**Finding chặn admission — directional movement có thể âm.** Code chọn `plus_dm=up_move` khi `up_move>down_move`, không kiểm `up_move>0`; nhánh minus tương tự. Ví dụ hai bar: previous high=12/low=3, current high=11/low=5 ⇒ up_move=−1, down_move=−2 ⇒ code chọn plus_dm=−1. Với current high=10/low=4 ⇒ up_move=−2, down_move=−1 ⇒ minus_dm=−1. Đây là counterexample suy trực tiếp từ biểu thức, **chưa chạy Polars/engine**. Không coi ADX của implementation hiện tại là Wilder ADX chuẩn; chưa biết mức ảnh hưởng kinh tế.

Các semantics khác phải giữ đúng nhãn:

- RSI là simple rolling averages của gains/losses, không phải Wilder recursive smoothing. ADX là rolling mean của DM/TR ratios rồi rolling DX, không phải ratio của Wilder-smoothed DM/TR.
- RANGE phát BUY/SELL theo điều kiện RSI liên tục; TREND chỉ phát tại thay đổi dấu MA. Chuyển regime không tự tạo MA edge nếu MA direction không đổi.
- Canonical warmup khai báo 100 bars; static constant không tự chứng minh full-prefix/bounded-frame parity cho mọi grid. Đề xuất chỉ fixed defaults.
- Docstring nói `rsi_maintain`/smoothed RSI nhưng constructor không đọc tham số đó và schema không có nó. Không đưa tham số này vào protocol hoặc claim chức năng đã có.

**Task kế tiếp tối thiểu, chưa thực thi:** synthetic regression cho inside-bar hai hướng ở trên (sau warmup), flat/zero-range hữu hạn, equality ADX boundary, regime transition không MA cross, prefix causality và canonical-vs-full-frame alignment. Assertion phải có expected values độc lập, không dùng implementation tự so với chính nó. Kiểm timing/fill native cần fixture engine riêng nếu đi tới N2. Không dùng market data để debug indicator.

Chưa sửa strategy: nếu xác nhận cần sửa DM để phù hợp directional-movement semantics thì sửa là source/candidate revision mới, cần regression và freeze mới; không backfill metrics hoặc kế thừa economics của packet legacy. Nếu cố ý giữ biến thể thì phải đặt contract rõ và review trước admission, không quảng bá như chỉ báo chuẩn.

**Execution blocker:** `execute_shell_command` không được publish trong session kiểm này (**TOOL_UNAVAILABLE**). Không dùng Node để spawn process hoặc tự thay công cụ; chưa chạy tests, không claim PASS. Agent có đúng tool phải dùng `.venv/bin/python` qua controlled execution/result-file theo AGENTS.md, chốt test IDs/cap trước chạy. Không sử dụng budget market cũ.

**Update sau authorization công cụ của operator:** synthetic correction — local evidence `data/rsi_dm_synthetic_fix_20261023_01/RESULTS.md` xác nhận3 regressions đỏ rồi sửa DM dương và zero-range;39/39 cases cuối và lint PASS. Fixture factory thiếu RSI/ADX đã bổ sung, không bỏ assertions. Indicator source đã đổi: hashes và trạng thái TOOL_UNAVAILABLE phía trên là snapshot trước sửa, không phải source hiện tại. Không sửa thresholds/policy hoặc chạy market. Adapter parity100 timestamps không thay engine/fill parity. Flat RSI=0/BUY và rolling indicator semantics vẫn cần review; economic impact UNKNOWN. Chưa có independent review patch.

**Trạng thái hiện tại: PROPOSED_SUBJECT / SYNTHETIC_INDICATOR_FIX_VERIFIED / ADMISSION_PENDING; N2–N5 chưa được admit.** Data path/hash, exposure eligibility, timestamp windows, runtime basis, costs/gate mapping và market budget vẫn chưa chốt; không soạn lệnh runnable với placeholder hoặc gọi đây là preregistration hoàn chỉnh. Mainnet vẫn NO-GO.

---

## Snapshot lịch sử — không phải quyền chạy hiện tại

Baseline engineering: `cdf39ce132a22bac32e5daff8c95f009bf9ff567`.
Integration follow-up: exact selected commit identity is recorded in
`data/retire_wfo_wrapper_20261016_01/compatibility_commit_receipt.json`;
fresh post-commit evidence in `data/compatibility_post_commit_20261016_01/`.
Earlier dirty-patch receipts below remain historical. Author self-review does
not replace independent sign-off or operator approval for a new campaign.
Không thay thế [quy trình calibration](../../CALIBRATION_PROTOCOL.md) hoặc native
gate contract. Không phải khuyến nghị đầu tư hay chứng nhận live.

## 1. Kết luận và luồng quyết định

**Update 2026-10-23:** inventory report-only — local evidence `data/strategy_evidence_inventory_20261023_01/RESULTS.md` đối chiếu 22 raw reports và source catalog hiện tại: 18 broad declarations + state-edge scoped; con số17 bên dưới là snapshot lịch sử, không phải giới hạn hiện tại. Không candidate nào launch-ready trong phạm vi được rà. Đây không phải full economic audit cho mọi ID hoặc quyền tiếp tục batch cũ. Ưu tiên chọn một hypothesis và binding đúng, không quét lại toàn registry để tìm winner.

Mục tiêu không phải tìm strategy có backtest đẹp nhất, mà tìm candidate có
đủ dữ liệu, thực thi đúng, vượt kiểm định ngoài mẫu sau chi phí và có đường
triển khai an toàn. ABSTAIN là kết quả hợp lệ; không bắt buộc phải có winner.

```text
17 candidate trong registry
  → kiểm semantics / dữ liệu / khả năng thực thi từng candidate
  → đo feasibility validation-only với default cố định
  → kiểm đồng thời số trades và số folds khả dụng
  → preregister protocol + grid nhỏ + costs + sensitivity + trial burden
  → nested WFO trên dữ liệu pre-holdout, lựa chọn chỉ bằng inner validation
  → review độc lập và qualification theo native gates
  → paper/shadow + kiểm operational readiness
  → xin phê duyệt riêng trước pilot live

Bất kỳ bước nào thiếu bằng chứng → BLOCKED / ABSTAIN, không tự mở rộng scope.
```

## 2. Bằng chứng đã có và giới hạn

Packet `data/calibration_telemetry_btc4h_20261016_01`:
`ACCEPTED_CALIBRATION`; producer và verifier CLI cùng PASS12checks. Đây là cùng
shared verifier chạy hai lần, **không phải hai thuật toán kiểm chứng độc lập**.
Supervisor58.43s; execution49.61s; CPU25.89s. Fingerprint:
`60b7ac42f968d34363ed900530c4dd1ee68b1262f1879a5732cc1bf17be67878`.

| Cửa sổ | Bars đo | Trades | Return | Sharpe | Max DD | PF | Verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| 2024H1 | 1092 | 9 | +1.91% | 0.555 | 6.48% | 1.253 | ABSTAIN: thiếu30trades |
| 2025H2 | 1104 | 8 | -3.73% | -1.376 | 6.36% | 0.327 | ABSTAIN: thiếu30trades, Sharpe dưới sàn |

Early:1074HOLD/9BUY/9SELL,12runtime submissions,6flat exits;9closes gồm6stop
và3signal. Late:1088HOLD/8BUY/8SELL,14submissions,2flat exits;8closes gồm2stop
và6signal. Không thiếu attribution. Late có carry-in và carry-out: không so
BUY count với closed trades theo quan hệ một-một hoặc bỏ unrealized inventory.
Returned orders không gồm mọi protective order; fill snapshot không phải fill
event count. Telemetry đo simulator-gate signal, không phải raw forecast posterior.

Kết luận giới hạn: tín hiệu thưa là nút thắt trong hai cửa sổ này; chưa có bằng
chứng rằng tất cả strategy đều thưa hoặc tất cả filters đều tốt. `ma_vol_target`
trong bridge hiện tại là crossover với target exposure cố định25%, chưa chứng
minh realized-volatility targeting. Không đổi tên bằng chứng thành OOS.

Quy đổi thô, không dự báo:9/1092 và8/1104 tương ứng khoảng3640 và4140bars để
đạt30closes nếu mật độ giữ nguyên (~20–23tháng ở4h). Tính mùa vụ, regime,
overlap và carry làm giả định này không ổn định. Không dùng làm ETA hay tự chọn
window; kiểm bằng phép đo đã preregister. Không cộng trades khác pair/fold để
vượt gate từng candidate/fold.

## 3. Inventory và phạm vi khả dụng

Nguồn: `src/trading_agent/strategies/canonical/candidates.py`,
`src/trading_agent/calibration/execution.py` và validator trong `packet.py`.
Registry có17candidate; điều này không chứng minh tất cả có feature/parity/live
support. Docstring còn nhắc "five" không phải giới hạn registry thực tế.

| Nhóm | Candidate IDs | Xử lý trước chạy |
|---|---|---|
| Calibration hiện hỗ trợ | enhanced_ma, ma_adx, ma_vol_target, rsi, bbands | Kiểm default, semantics, dữ liệu, raw attribution; không đổi gate |
| Pool mở rộng cần compatibility audit | volatility_breakout, trend_pullback, ma_crossover, range_mean_reversion, ensemble_ma_adx, ma_adx_regime, regime_switching | Xác minh code path/feature/parity/leakage và runner support; không tự truyền vào CLI calibration |
| Chiến lược cần kiểm hợp đồng dữ liệu/thị trường đặc thù | funding_carry, stat_arbitrage_lo, stat_arbitrage_ls, cross_sectional_momentum_lo, cross_sectional_momentum_ls | Đọc implementation, xác minh funding/reference/universe/short semantics thực tế; thiếu dữ liệu thì BLOCKED, không giả lập thành OHLC đơn pair |

Giữ ADX25 cho nghiên cứu ma_adx đã thống nhất. Ghi resolved params, không suy
từ biến môi trường chung; ma_vol_target không có tham số ADX. Chưa khẳng định
defaults của mọi candidate đều dùng ADX25. Strategy có tham số ADX khác phải
được chỉ rõ và review, không âm thầm sửa code/config hoặc frozen packet.

## 4. Các phase và exit criteria giao agent

### R0/R1 audit đã thực hiện — 2026-10-16

#### Engineering follow-up: fixes có regression proof

#### Pool audit tiếp nối — source không đổi, test coverage mở rộng

`pool_audit_receipt.json` trong `data/retire_wfo_wrapper_20261016_01/` ghi:
108targeted regression tests PASS, Ruff PASS;17/17default candidates giữ nguyên
prefix signals khi thêm future rows trên deterministic synthetic fixture.
Hai candidate `_ls` được chạy qua StrategyRuntime trong test: negative signal
trả SELL và reduce_only=True. Không đặt broker order. Code source giữ nguyên
treeb22da73a/dirty patch đã chạy AC01–AC15; chỉ mở rộng tests. Không cộng108với
205để báo tổng vì chúng overlap;205là integrated snapshot trước mở rộng test.
Tất cả protected files của snapshot trước vẫn khớp, trừ đúng file test được
mở rộng có SHA mới trong receipt. Không thay packet hoặc registry.

R1 disposition: inventory17/17 và scoped causality/runtime-mapping proofs xong;
**chưa full execution/economic parity cho17candidate**. Các blocker còn lại đã
được phân loại, không còn coi là lỗi history/funding backfill chưa sửa:

| Phạm vi | Disposition | Hành động hợp lý |
|---|---|---|
| 5calibration candidates | Có runner, vẫn research-only | Review/tích hợp source mới trước freeze R2; xin budget/fingerprint riêng |
| Regime history2candidates | FIXED có prefix regression | Chưa mở calibration allowlist; không dùng weights/MA dynamic claims quá implementation |
| Funding | Missing data fail-closed/PIT fill fixed | BLOCKED cho qualification tới có rate publication binding và funding accrual tests |
| Stat/CS đơn tài sản | Approximation được xác nhận qua code | Nghiên cứu dưới hypothesis/nhãn đúng hoặc thiết kế multi-asset riêng; không rename packet cũ |
| Hai `_ls` | CLOSE_LONG_ONLY_RUNTIME_CONFIRMED | BLOCKED cho long-short; không cần triển khai short để hoàn thành mục tiêu spot long-only |

Khuyến nghị phạm vi gần nhất: tiếp tục **spot long-only, BTC/USDT4h** với5candidate
runner đã hỗ trợ; không mở derivatives/short hoặc xây portfolio engine chỉ để
sử dụng hết tên strategy. Khi ít mẫu, R2 có thể tiếp tục trả ABSTAIN và R3 phải
đánh giá feasibility; không có cam kết phải tìm ra winner. Approval đã dùng cho
2cells cũ không áp dụng source mới hay10cells đề xuất.

Final local closure: **205regression tests PASS, AC01–AC15 PASS, Ruff PASS,
git diff --check clean**;383protected file hashes và source identity ổn định.
Tree `b22da73aac8f82ef9a1094836b56c844516f88ecee6518c898f113c2e5f00a90`,
dirty patch trêncdf39ce. Chưa commit hoặc independent integration review cho
patch này. Không cộng89targeted tests vào205để báo294tests; scope có overlap.

Source mới là patch trêncdf39ce, chưa commit; không dùng source identity cũ để
freeze/reuse. Trạng thái xác minh cuối nằm tại
`data/retire_wfo_wrapper_20261016_01/compatibility_verified.json`, không suy PASS
từ kết quả từng bước bên dưới.

- Regression ban đầu:7FAIL/2PASS tái hiện percentile null ở bounded history và
  funding backfill/fallback. Receipt `compatibility_red.json` trong cùng thư mục.
- Đã sửa default history của ensemble_ma_adx/ma_adx_regime từ96 thành268bars.
  Resolver tính regime_lookback+ATR+buffer và MA/ADX requirements theo params;
  campaign history lấy max toàn grid cho hai candidate này. Test252/400lookback
  so percentile và last signal của bounded window với full prefix cùng fixture.
  Đây là scoped finite-history proof, chưa all-regime/economic parity.
- Funding thiếu cột funding_rate giờ fail closed; bỏ momentum-derived fallback
  và backward_fill. Chỉ forward_fill, giữ leading unknown là null/HOLD. Synthetic
  future-append test chứng minh rate tương lai không backfill past. Input vẫn cần
  publication-time/lag/accrual binding trước nghiên cứu funding thực tế; có cột
  không tự chứng minh nguồn thật hoặc settlement economics.
- Fixture registry determinism giờ lấy max descriptor history và cung cấp funding
  giả lập **tường minh trong test**, không hạ history để giữ fixture cũ PASS.
- 89targeted regressions PASS; Ruff PASS. Không thay thresholds, defaultsADX25,
  short authority, calibration allowlist hoặc research_only.

Hai HISTORY_REVIEW blockers trong bảng lịch sử bên dưới đã được sửa về engineering,
nhưng vẫn BLOCKED_R2 vì calibration chưa hỗ trợ chúng và chưa có qualification.
Funding chuyển từ synthetic/PIT defect sang BLOCKED_DATA_ECONOMICS_REVIEW; không
coi missing-data fail-closed là đã có dữ liệu funding hợp lệ. Các label/short
blockers giữ nguyên có chủ đích, không "fix" bằng cách cấp quyền short.
`authority/resolver.py` ánh xạ last_val<0 thành SELL với reduce_only=True:
đây là close-long semantics, không engine mở short.

Thêm giới hạn semantics cần giữ trong inventory: `_get_regime_weights` của
ensemble_ma_adx hiện gán base_weight hằng, không chứng minh dynamic weighting;
ma_adx_regime dùng base MAs và dynamic ADX threshold, không thực sự thay MA
period theo regime. Không quảng bá tên strategy thành năng lực chưa triển khai.

Phần dưới giữ nguyên audit lịch sử trêncdf39ce; không đọc warmup96 trong bảng
như trạng thái source mới. Không rerun/reseal packet diagnostic cũ sau sửa.

Evidence: `data/retire_wfo_wrapper_20261016_01/r0_r1_audit.json`.
R0:28assertions PASS (14/cell): SHA raw, bar/terminal partitions, records trong
measurement window, unique close IDs, close attribution/reasons, metric identity,
return basis, accounting presence, quyết định ABSTAIN và hai phương trình PnL.
Packet inventory trước/sau không đổi; source giữ nguyêncdf39ce.

- Early: opening equity9896.638114 → final10085.639654; delta189.001540.
  Gross241.008658 − fees34.671372 − slippage17.335746 = net189.001540.
- Late: opening9851.170326 → final9483.533685; delta−367.636640.
  Closed gross−322.187156 − fees27.280067 − slippage13.640413 = net−363.107636;
  cộng open unrealized−4.529005 = total−367.636640.
- Return dựa opening equity tại measurement, **không phải** initial capital10000
  của full simulation. Vì vậy final equity và return headline không được đối
  chiếu với10000 để kết luận lỗi. Spread/impact bằng0 trong paper model không
  chứng minh chi phí live bằng0.

R0 status: **SCOPED_RECONCILIATION_PASS**, chưa independent full fill-ledger
reconstruction/sign-off. Không nâng thành final accounting acceptance.

R1 inventory:17/17default params validate và resolved warmup lấy trực tiếp từ
code; source SHA, symbols, features, params đầy đủ trong JSON. Tất cả descriptor
đang `research_only=True`; không candidate nào được inventory này chứng nhận live.
51tests PASS trong candidate_factory, canonical_contract, canonical_wave_c và
regime_lookahead; đây là generic/factory/PIT coverage, không17economic parity proofs.
Receipt: `data/retire_wfo_wrapper_20261016_01/compatibility_tests.json`.

| Candidate | Default warmup bars | Calibration CLI | Trạng thái/giới hạn sau audit |
|---|---:|---|---|
| enhanced_ma | 100 | Có | SUPPORTED_CALIBRATION_ONLY; defaults20/80,ADX25; chưa qualification |
| ma_adx | 100 | Có | SUPPORTED_CALIBRATION_ONLY; defaults20/80,ADX25 |
| ma_vol_target | 100 | Có | SUPPORTED_CALIBRATION_ONLY; đã đo2cells; fixed-exposure crossover |
| rsi | 16 | Có | SUPPORTED_CALIBRATION_ONLY;14/30/70; chưa market feasibility mới |
| bbands | 22 | Có | SUPPORTED_CALIBRATION_ONLY;20/2; chưa market feasibility mới |
| regime_switching | 200 | Không | BLOCKED_R2; cần rolling-refit/state/parity audit cho hybrid200 |
| funding_carry | 22 | Không | BLOCKED_SEMANTICS; thiếu funding có synthetic fallback; cần real funding binding, accrual và PIT tests |
| volatility_breakout | 36 | Không | BLOCKED_R2; constructor/schema có, chưa per-candidate execution parity |
| ensemble_ma_adx | 96 | Không | BLOCKED_HISTORY_REVIEW; regime_lookback252 nhưng canonical bridge chỉ cấp warmup+1=97bars |
| ma_adx_regime | 96 | Không | BLOCKED_HISTORY_REVIEW; cùng bất tương xứng252vs97; cần test full-prefix vs bounded window |
| trend_pullback | 96 | Không | BLOCKED_R2; defaultsADX25, cần feature/runtime parity |
| ma_crossover | 51 | Không | BLOCKED_R2;20/50, chưa mở calibration contract |
| range_mean_reversion | 22 | Không | BLOCKED_R2; cần xác minh VWAP/zscore/state semantics |
| stat_arbitrage_lo | 22 | Không | BLOCKED_LABEL; code hiện single-asset rolling zscore, không cointegration/hedge pair |
| stat_arbitrage_ls | 22 | Không | BLOCKED_LABEL_SHORT; single-asset zscore; bridge -1=SELL không chứng minh short |
| cross_sectional_momentum_lo | 61 | Không | BLOCKED_LABEL; hiện single-asset rolling momentum/MA, không universe ranking |
| cross_sectional_momentum_ls | 61 | Không | BLOCKED_LABEL_SHORT; cùng fallback; chưa long-short execution proof |

"BLOCKED_R2" chỉ nói runner calibration hiện không hỗ trợ và thiếu evidence,
không khẳng định mọi runner đều không chạy được. Các bản OHLC fallback có thể
nghiên cứu như hypothesis riêng được đặt nhãn đúng; không dùng kết quả của nó
để quảng bá pairs arbitrage hoặc cross-sectional portfolio.

Funding code có forward_fill rồi backward_fill khi funding_rate tồn tại:
cần test point-in-time availability/publication lag, đặc biệt prefix có leading
null; không mặc nhiên gọi whole-window indicator output là causal. Chưa kết luận
packet vừa đo bị ảnh hưởng: packet đó dùng ma_vol_target, không funding.

R1 status: **INVENTORY_COMPLETE / COMPATIBILITY_PARTIAL**. Chưa đóng toàn bộ
exit criteria R1: cần per-candidate feature/runtime parity và history probes,
không sửa `research_only` để ép sử dụng. Ưu tiên tiếp theo:

1. Chứng minh history252vs97 cho hai regime candidates bằng synthetic prefix tests;
   xác định degradation/fallback thực tế trước sửa warmup, không sửa theo tên biến.
2. Rà soát funding fallback/PIT, short mapping và nhãn các đơn-asset approximations;
   giữ BLOCKED cho đúng market semantics cho tới có bằng chứng.
3. Chốt5candidate calibration với ADX25 đã resolve; xin budget/fingerprint riêng
   nếu muốn R2. Không mở toàn bộ17candidate chỉ vì factory tests PASS.

| Phase | Công việc nhỏ | Artifact / điều kiện hoàn thành | Quyền chạy |
|---|---|---|---|
| R0 — evidence review | Đối chiếu2raw reports, quyết định, PnL basis, carry, costs và close IDs; kiểm bar partitions | Review receipt ngoài packet; nêu mismatch hoặc PASS theo từng assertion; không sửa/reseal | Chỉ đọc |
| R1 — compatibility | Lập1row/candidate: source SHA, research_only, params, warmup, feature inputs, symbol/TF, execution support, parity tests | Đủ17rows; trạng thái SUPPORTED/BLOCKED kèm code/test evidence; không coi descriptor là proof | Code audit + synthetic tests, chưa market |
| R2 — feasibility | Đề xuất calibration 5strategy×2windows với default cố định, không grid search; ma_vol_target đã đo dùng như historical observation, không relabel thành same-source run | Freeze matrix + source/input/config/cost/budget; cấp quyền riêng. Nếu muốn omit/reuse2cells cũ phải có explicit manifest/verifier support, không copy JSON thủ công | Chưa được duyệt |
| R3 — geometry/protocol | Đánh giá density/trade adequacy và folds cùng lúc; chọn1protocol trước outer; ghi mọi trial đã xem | Exact pre-holdout input SHA; windows, purge/embargo, grid, cost scenarios, sensitivity matrix, gate hashes, history/trial burden, limits và stop rules | Metadata-only trước; execution cần approval |
| R4 — qualification | Chạy nested WFO đúng protocol; inner chọn, outer chỉ đánh giá; mandatory sensitivity và native gates | Raw/seal/source/registry hợp lệ + independent review. Báo riêng engineering verdict và research verdict | Budget/fingerprint approval riêng |
| R5 — operational | Paper/shadow, parity và order lifecycle/recovery/risk-stop; kiểm nối selection policy vào runner thực tế | Operational evidence và owner sign-off; không coi research winner tự nối vào live runner | Không live nếu chưa cấp quyền riêng |

Ưu tiên R0/R1 trước R2. 5strategy là giới hạn runner calibration hiện có, không
phải tuyên bố pool17strategy đã được chọn lọc kinh tế. Nếu5strategy đều thiếu
mẫu: báo INFEASIBLE_WITH_CURRENT_PROTOCOL, rồi đề xuất thay đổi hypothesis
(TF/pair/family/history) như một nghiên cứu mới, tính trial burden và xin duyệt.
Không chạy tất cả17ngay để "tìm một cái PASS".

## 5. Thiết kế R2 đề xuất — chưa freeze/chưa duyệt

**Update: thiết kế này đã được duyệt và chạy**, không còn pending execution.
Packet `data/calibration_feasibility_pool5_btc4h_20261016_01`, fingerprint
`0b5ff99f4752c512718e6727686597a5194b9a023f3c04b063ff9f93e3725f87`,
sourceb897c32: ACCEPTED_CALIBRATION,10COMPLETED/10ABSTAIN; all thiếu30trades,
7cells còn fail Sharpe>0. Supervisor336.56s, execution327.52s, CPU194.60s.
Evidence ngoài packet: `data/pool5_freeze_20261016_01/results_summary.json`
và execution_handoff.json. Source/input/config/382protected files giữ nguyên;
registry append được kiểm qua canonical verifier. Đây là calibration feasibility,
chưa OOS hoặc strategy qualification. Bảng metrics trong CAMPAIGN_STATUS.md.

R2 closed trong phạm vi fixed defaults trên2windows, không đóng R1 broad parity
hay R4qualification. R3 chưa có thiết kế vừa đủ mẫu vừa đủ folds được chứng minh.
Không chọnma_adx chỉ vì early+5.28%/Sharpe1.27 trên7trades. RSI/BB tạo nhiều
actions/trades hơn nhưng cả2windows lỗ; không suy "tăng tần suất = tốt hơn".
Các bullet dưới là thiết kế đã chạy, không phải quyền chạy lại packet.

- BTC/USDT4h, input pre-holdout hiện hữu6795bars, không download.
- Hai cửa sổ cố định2024H1 và2025H2, không chọn theo lợi nhuận.
- 5strategy được calibration hỗ trợ,1resolved default/candidate,1xcost.
- Dự kiến10cells nếu dùng nguyên contract hiện có,1worker,0retries.
- Budget đề xuất theo contract:180s wall/120s CPU mỗi cell;1800s wall/1200s CPU
  aggregate, sampled RSS2GiB. Không suy ETA10cells từ2cells MA.
- Giữ minimum30validation trades và Sharpe floor hiện hành; output count không
  phải quyền promotion. Default không khớp ADX25 hoặc semantics → dừng trước freeze.
- Đây là feasibility diagnostic: đủ mẫu chỉ cho phép đề xuất qualification,
  không dùng return ranking của hai cửa sổ để tuyên bố best strategy.

## 6. R3: tiêu chí chọn thiết kế, không ép thiết kế để PASS

### R3 metadata đã hoàn tất — quyết định sau POOL5

Authority: `data/research_r3_geometry_20261016_01/analysis.json` và
close_boundary_execution.json. Source sạchb897c32. Không chạy simulator,
không freeze protocol, không thay registry. Packet POOL5 và các input được hash
trước/sau; chỉ đọc timestamp columns, không đánh giá holdout prices.

Đã inventory10input hiện hữu: BTC/ETH/BNB/SOL/XRP ×1h/4h. Mỗi input4h có6795bars,
1h có25198bars. Timestamps tăng nghiêm ngặt,0nonstandard intervals; cả last open
và last close không vượt holdout cutoff2026-02-06T16:00Z. Đây chưa là OHLCV
quality/parity/cost proof cho1h hoặc mọi pair.

BTC1h SHA: `45ba1b1bc6a91986eda445de8026cab9995fcabe011a735244a4c7cf0e0825ce`.
BTC4h giữ SHA99114c33… đã dùng POOL5. Không cần download hoặc resample để **đề
xuất** nghiên cứu1h. Hai input bắt đầu khác nhau, không coi là cùng history.

Tính lại bằng `_get_fold_indices` trên exact6795bars, train12/test3/step3months,
purge/embargo100bars theo current default-five contract:

| Validation (tháng30ngày) | Folds | Số cost-clearing tối thiểu để p≤.10 | Nhận xét |
|---|---:|---:|---|
| 3 | 6 | 6/6 | Validation không overlap, nhưng mẫu còn thưa |
| 6 | 5 | 5/5 | Validation overlap; POOL5 đã thiếu30trades ở ~6tháng |
| 9 | 4 | 4/4 | Overlap; density scaling RSI gần đủ, chưa được đo |
| 12 | 3 | Không thể | Kể cả3/3 thì p=.125, không đạt gate |

ExactBTC1h25198bars cũng cho6/5/4/3folds với cùng tháng và100bar gaps. Nhiều bars
hơn không đồng nghĩa nhiều tháng/folds độc lập hơn. Không dùng4h trade density
để dự báo1h, hoặc gọi các folds overlap là independent statistical trials.

| Candidate | Bars4h cần cho30closes theo mật độ2cửa sổ đã đo | Quyết định |
|---|---:|---|
| enhanced_ma | 3640–5520 | Không mở grid4h hiện tại; thiếu feasibility proof |
| ma_adx | 4680–6624 | Không chọn từ7trades đầu dương; giữADX25 |
| ma_vol_target | 3640–4140 | Không kéo window tới20–23tháng để bù trades rồi bỏ gate folds |
| rsi | 1490–1578 | 9tháng có scaling30.82–32.64trades, nhưng2windows lỗ; không đủ đề cử winner |
| bbands | 1560–1656 | 9tháng scaling29.35–31.15; không bảo đảm30 |

Các khoảng trên là phép chia density, **không forecast/power analysis hoặc
trade adequacy thực đo ở cửa sổ mới**. Protocol9tháng không bị chứng minh bất
khả thi toán học cho mọi strategy; kết luận đúng là chưa được chứng minh khả
thi và hiệu quả. Không cherry-pick9tháng RSI sau khi xem cả5candidate để tuyên
bố alpha. Các observations này phải tính vào trial history nghiên cứu tiếp.

### Phương án đề nghị và work packages tiếp theo

**Chọn tiếp engineering hỗ trợ explicit1h trước, chưa chọn strategy.** Lý do:
dữ liệu1h có sẵn, có thể đo hypothesis tần suất/chi phí mới mà không hạ gates.
Chưa có bằng chứng1h sẽ tốt hơn. Không mở5pairs ngay; giữBTC trước để không
nhân trial burden và nhu cầu broker/capital. Không chạy lại WFO4h lớn chỉ vì
muốn một packet có PASS.

| Lựa chọn | Kết luận hiện tại | Điều kiện thay đổi |
|---|---|---|
| Lặp grid4h hiện có | Không khuyến nghị ở bước kế | Hypothesis mới, sample/fold/cost proof và budget riêng |
| 4h validation9tháng RSI/BB | Chưa chọn làm qualification | Đăng ký như nghiên cứu mới; sample thực đo, overlap/power review; không rank từ2windows |
| BTC1h fixed-five feasibility | Hướng đo tiếp được đề xuất | Engineering contract1h và approval mới, không default4h trá hình |
| Mở nhiều pairs/17strategy/short | Hoãn | Compatibility/data/execution và multiple-testing plan riêng |

1. **E1 — explicit timeframe contract (chưa triển khai):** calibration market
   hiện hardcode4h trong execution.py; synthetic-demo1h không chứng minh market1h
   support. Bổ sung một contract có version cho timeframe, byte binding, timestamp
   spacing/bar-close boundary, warmup theo bars và window calendar. Producer và
   verifier phải cùng hiểu1h; giữ tương thích xác minh packetv2 cũ, không mutate
   hoặc reseal chúng. Không chỉ thêm CLI flag rồi đổi label báo cáo.
2. **E2 — tests trước market:** synthetic1h/4h roundtrip, wrong-TF input rejected,
   missing/gapped/duplicated timestamps, close-at-cutoff handling đúng policy,
   params/feature/cost/receipt drift, old packet read-only verification, AC01–AC15.
   Re-review/integrate trên source mới trước freeze; approvalb897c32 không reuse.
3. **E3 — budget pilot trước10cells:** đề xuất riêng phép đo1h nhỏ để biết wall/
   CPU/RSS trên ~4xmeasurement bars; vẫn chưa chạy. Xác định cells/limits/exit
   trước freeze. Timeout là INCOMPLETE, không tự tăng budget hay retry.
4. **E4 — fixed-five1h feasibility:** chỉ sau pilot và budget/fingerprint approval
   riêng, cùng2calendar windows đã preregister, cùng fixed defaults,1xcost và
   gates30trades/Sharpe>0. Period20/80 ở1h tương ứng horizon khác4h; ghi rõ đó là
   hypothesis khác, không "cùng strategy performance". Không tuning song song.
5. **E5 — chọn qualification protocol:** chỉ candidate có sample adequacy và
   economics đáng nghiên cứu mới đề xuất grid/folds/cost stress/sensitivity/trial
   burden. Vẫn chưa gọi qualified trước outer/native gates và review độc lập.

Exit/stop: drift hoặc unsupported contract → dừng engineering; pilot timeout →
giữ packet và xin quyết định; feasibility tất cảABSTAIN → báo NO_SUPPORTED_WINNER,
không tự mở pair/strategy/holdout. Nếu không có thiết kế đủ bằng chứng trong dữ
liệu cho phép, giữ paper-only/no-live là kết quả đúng.

R3 status: **METADATA_COMPLETE / NEXT_HYPOTHESIS_PROPOSED**, chưa full protocol
freeze hay đủ quyềnE3/E4market run. Các đoạn dưới là constraints/background.

Đã có planner metadata cho historical input7946bars,6796timestamp trước cutoff:
validation3/6/9/12months cho6/5/4/3folds và binomial p<=.10 cần6/5/4/impossible
folds cost-clearing. Đây là bằng chứng lịch sử, không phải kết quả trên input
6795bars của diagnostic. Phải recompute trên exact bound input mới.

Tradeoff bắt buộc ghi trong protocol:

1. Cửa sổ dài hơn có thể tăng trades nhưng giảm folds; folds overlap không tạo
   quan sát độc lập. Binomial geometry-pass không chứng minh statistical power.
2. 1h cần input/feature/cost và bar alignment phù hợp đã có; không tự resample4h
   thành1h, không dùng thêm holdout để bù lịch sử. Chi phí/turnover phải đo lại.
3. Nhiều pair không phải nhiều mẫu độc lập; universe và phép gộp phải preregister.
4. Chọn policy bằng inner validation; không lựa lại grid/window sau khi xem outer.
   Historical observations/trials phải giữ, không reset multiple-testing burden.
5. Không tự tách semantic identity khỏi execution/source binding để reuse. Chỉ
   reuse nếu contract có kiểm compatibility, source lineage và explicit support.
6. Dừng nếu không tồn tại thiết kế vừa đủ trades vừa đủ fold evidence trong dữ
   liệu được phép. Trình lựa chọn mới, không bỏ sensitivity, giảm gate hay mở holdout.

## 7. Handoff bắt buộc và definition of done

Mỗi phase ghi: source HEAD+tree+dirty scope; input/config/manifest SHA; danh sách
đã xem và chưa xem; test commands/results; tài nguyên đo; exact verdict; remaining
blockers và quyền cần thêm. Artifact mới nằm ngoài sealed packet cũ.

Nếu sửa promotion/gate/evidence paths thuộc AGENTS.md: chạy AC01–AC15. Các thay
đổi engineering khác chạy tests đúng phạm vi; không cộng số tests lịch sử vào
số tests revision hiện tại. Đổi source sau freeze → reverify/refreeze và xin
approval mới, không reseal. Không tự commit/push/merge, mở tài khoản hay gửi lệnh.

Agent tự kiểm cuối vòng:

- Tôi vừa chứng minh engineering, feasibility, OOS qualification hay live readiness?
- Mỗi kết luận trỏ được raw report/test trên đúng revision không?
- Có strategy bị bỏ qua chỉ vì runner chưa hỗ trợ không? Có gắn BLOCKED rõ không?
- Có đổi cửa sổ/params/threshold sau khi xem kết quả hoặc bỏ lịch sử trials không?
- Bước tiếp theo có thật sự giảm một uncertainty cụ thể, trong budget được duyệt?

Nếu không trả lời được: giữ trạng thái pending và nêu missing evidence. Không
tự lặp campaign. Kế hoạch này hoàn tất khi R0/R1 có bằng chứng, R2/R3 được duyệt
riêng nếu cần chạy, và không còn nhầm calibration PASS thành strategy qualified.









