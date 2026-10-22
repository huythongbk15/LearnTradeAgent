# BNB qualification readiness — vòng lặp có giới hạn

Status: CURRENT readiness decision; qualification execution NOT APPROVED.
Owner: research operator. Verified: 2026-10-19.
Evidence: [inventory](../../data/bnb_qualification_readiness_20261019_01/inventory.json),
[inspection source](../../data/bnb_qualification_readiness_20261019_01/inventory.py).

## Kết luận hiện tại

**NO_GO cho campaign qualification mới; chưa có strategy đủ bằng chứng live.**
Operator đã chọn **native execution làm authority**: giữ BNB/USDT 4h,
MA20/80 + ADX14/25 + DI; không retune indicator để làm đẹp kết quả.
Giữ native authority và protective stop.25% là requested allocation/trần lúc vào,
không phải allocation được đảm bảo;18.75% là quan sát dưới moderate profile,
không hard-code thành tỷ lệ cố định. Exposure mark-to-market có thể tăng theo giá.
Native cooldown24h thuộc circuit breaker, KHÔNG phải cooldown sau mọi SELL.
Đây là policy identity khác research V2; kết quả cũ chỉ dùng screening.
Không cần ép native khớp economics của V2 để cứu kết quả nghiên cứu cũ.

## Các vòng đã hoàn tất

| Câu hỏi | Kiểm tra nhỏ nhất | Kết quả và quyết định |
| --- | --- | --- |
| Ranh giới holdout nào đúng? | Kiểm integrity và metadata manifest, không đọc giá holdout | UTC `[2026-02-06T16:00, 2026-08-05T16:00]`; sửa tài liệu cũ, không sửa manifest |
| Dữ liệu nào đã được quan sát? | Hợp các khoảng run đã biết, registry chỉ đọc | **ĐÃ SỬA 2026-10-20 (rev6).** Loader research đóng băng đọc TOÀN BỘ bytes file (bắt buộc verify sha256) rồi `read_parquet` **decode mọi dòng mọi cột** trước khi slice — nên **6795/6795 bar đã bị read+decode**, kể cả [5600,6795); đo/evaluate thì mới chỉ trong [0,5600). File là **pre-holdout**: bar cuối 2026-02-06 08:00, holdout mở 16:00 → **0 bar holdout** (probe timestamp-only). Vì vậy **không còn gọi [5600,6795) là "chưa expose"**; nó là *read+decoded, not evaluated*. Đây là **thay đổi scope cần duyệt**, không chỉ sửa wording |
| Runner multiwindow có cùng policy không? | Đọc source và tái hiện bằng3 nến synthetic | Không: cooldown1bar, khác sizing/metrics; low hiện tại kích hoạt bán tại open cùng nến. Không dùng kết quả này để qualify candidate24h; vẫn tính là exposure nghiên cứu |
| Có thể đưa candidate vào qualification ngay không? | Trace CLI → tournament → simulator | Chưa chứng minh replay V2 và FullSystemSimulator khớp stop/cooldown/sizing/cost. Dừng trước chạy thị trường |

Khoảng đo đã biết (index half-open): `[1800,4080)` và `[4520,5600)`.
Đây chỉ là lower bound; training, prefix, nghiên cứu thủ công có thể phủ thêm.
Registry151 specs/537 evaluations không phải số effective independent trials.
Kết quả hai window đã xem chỉ là descriptive validation, không phải OOS.

## Một vòng làm việc chuẩn cho agent

1. Ghi một câu hỏi có thể bác bỏ, snapshot source/data/policy và bằng chứng đầu vào.
2. Chọn kiểm tra nhỏ nhất: đọc metadata → fixture synthetic → test hẹp → mới đề xuất run.
3. Đối chiếu kỳ vọng, ledger, timing, identity và giới hạn quyền thực thi.
4. Nếu có lỗi engineering: sửa tối thiểu trên revision mới và thêm regression test;
   không sửa packet cũ, không đồng thời retune strategy.
5. Tự phản biện: có leakage, thay policy, nhiều lần thử, cost double count,
   PnL open bị gọi là realized, hoặc label OOS sai không?
6. Ghi PASS/FAIL/UNKNOWN và bước tiếp theo. Tối đa6 vòng cho một scope;
   cùng blocker không có bằng chứng mới2 vòng thì bàn giao, không chạy lặp vô hạn.

Kết quả hợp lệ có thể là NOT_QUALIFIED/ABSTAIN. Không chạy đến khi có lợi nhuận.
Agent được tự kiểm tra nội bộ trong scope; không được tự tạo approval.

## Kịch bản và điều kiện dừng

| Tình huống | Hành động |
| --- | --- |
| Source/data identity đổi | Dừng, tạo revision và inventory mới; không suy diễn reuse hợp lệ |
| Ledger/timing sai | Fixture tái hiện → fix riêng → regression; chưa diễn giải alpha |
| Research/runtime khác policy | NO_GO; lập bảng parity và synthetic trace trước qualification |
| Không chứng minh được test data chưa dùng | Không gọi OOS; bàn giao protocol/data decision |
| Qualification thật thất bại gate đã freeze | NOT_QUALIFIED; không hạ threshold hoặc mở holdout để cứu |
| Mọi kiểm tra trong scope đạt | Independent review; không tự chuyển paper/live |
| Timeout/budget hết | Giữ artifacts, ghi terminal status; không tự retry campaign |

## Bước kế tiếp hiện tại — native policy

**2026-10-20 (rev6: review độc lập của operator → CHANGES_REQUIRED, đã xử lý; cần
review lại trên A/B/C mới).**
[Contract + synthetic](../../data/bnb_di_adx_stateedge_20261020_01/RESULTS.md) ·
[Preregistration duy nhất](../../data/bnb_di_adx_stateedge_prereg_20261020_01/PREREGISTRATION.md).
Bốn finding của reviewer và cách xử lý:
1. **Lệnh đăng ký chạy sai geometry.** CLI default `--val-months 3` ⇒ không tái tạo 5
   cửa sổ 6 tháng đã đóng băng. Lệnh nay ghim đủ geometry
   (`--train-months 12 --val-months 6 --test-months 3 --step-months 3`); test integration
   qua launcher thật assert CLI cho đúng 5 cửa sổ + 15 cell, và assert CLI **không** có
   pin thì cho cửa sổ khác ⇒ pin có ý nghĩa. purge/embargo không phải tuỳ chọn CLI: suy từ
   warmup descriptor `max(warmup, horizon)` = 100/100, khớp control.
2. **Ledger chưa được chứng minh.** Rule cũ ghép file tầng *runner* với file tầng
   *worker* (hai thư mục khác nhau, không có liên kết). Nay tách rõ và liên kết bằng
   **cache key** (thư mục `cell_execution/<uuid>/` của worker nằm trong attempt dir của
   runner); rule đúng: worker ghi `result.json` **kể cả khi cell fail** (mang artifact),
   nên **thiếu** `result.json` ⇒ worker bị **kill** = INTERRUPTED; bản ghi bền của lỗi là
   `cell_failures/<key>__<uuid>.json` ở tầng runner. Test mới chạy runner/worker **thật**.
3. **Claim sai về đọc dữ liệu.** Payload nói token sai bị chặn *trước khi đọc data* — chỉ đúng
   ở thời điểm **execution**. Ở thời điểm **plan**, `build_canonical_cells` gọi loader
   trước rồi mới resolve registry ⇒ data **đã** được đọc. Claim nay tách rõ; **không**
   đổi loader (phạm vi đọc là phạm vi đã được duyệt).
4. **Integration chưa phủ launcher đầy đủ.** Test integration synthetic mới đi qua
   validation launcher end-to-end và assert terminal coverage: `execution_finished.json`
   COMPLETED · `parallel_canonical_summary.json` verdict VALIDATION_COMPLETE ·
   `exact_coverage.json` PASS · số cell thực thi = số cell kế hoạch.
**Integration đó lộ và sửa bảy hop hỏng mà integration cũ (mức unit) không thấy:** CLI
không có param grid cho candidate scoped (0 cell); protocol producer và protocol verifier
**vẫn** dùng literal budget cũ (180/1800/128/4096) nên **từ chối** budget đã đăng ký;
`nested_wfo.run_nested_wfo` dựng registry không token; `_compute_strategy_code_sha` rơi về
`"unknown"` ⇒ **mất attribution source** trong trial identity; campaign artifact rebuild và
`selection_eligibility.verify_validation_reports` dựng registry không token. Envelope nay
do **cùng bộ hằng số có tên** ở **ba nơi** (CLI guard, producer, verifier) — không bỏ
guard, không đi vòng entrypoint khác.
**Còn là gap cho các stage sau** (ngoài validation-only, nên ghi nhận chứ không sửa ở đây, và
đã liệt kê trong payload): sensitivity arm (`wfo_sensitivity.py`), promotion gate
(`research/selection_policy.py`), calibration path và live admission vẫn resolve registry
không token.
**Identity sau sửa — review phải phát hành lại trên đây.** A source
`ca12f95124d37e76a2b25f482ade3c4a4324652d49074c657b75d360a95f2708` (dirty
`d3fb0e20…`); B prereg `d52297088464cf2c2f90a220bfd61021f1c6fd4e4c5440483b9bad555b5a73f8`;
C manifest rev12. AC01-AC15 rc0 ALL PASS, identity trước/sau giống nhau.
**Trạng thái: independent review PENDING (cần phát hành lại trên revision mới) · operator
scope approval PENDING · chưa chạy market · chưa qualified.**

Sample feasibility đã kiểm bằng reports+số học,không market run:
[kết quả](../../data/native_sample_feasibility_20261020_01/RESULTS.md).
2x/stress có17unique closed events,chứ không phải28giao dịch độc lập từ tổngfold.
Giữ5folds trên6795bars chỉ cho validation tối đa8tháng với train/test/gaps hiện tại.
Không đổi gate30. Cần operator chọn giữ event-crossover và bổ sung observation
đủ điều kiện,hay cho phép candidate state-entry/re-entry RIÊNG trước khi codeđổi
policy hoặc duyệt budget mới. Chưa lựa chọn thay operator,không tự mởpaper/live.

2026-10-20:đã đối soát14report về signal→authority→fill và inventory:
[kết quả](../../data/native_signal_fill_review_20261020_01/RESULTS.md).
BUY xuống runtime đều có filled snapshots; các skip là already_long. Strategy
hiện tại là event-crossover với ADX/DI ngay lúc giao cắt,không tự late-entry/re-entry
khi ADX mạnh lên sau đó. Không tăng sizing/tắt authority để tạo thêm giao dịch.
Cần chốt ý định event-vs-state và thiết kế sample/data trước campaign mới; thay
semantics là candidate mới,chưa được tự động duyệt. Không market run/gate change.

**Actual validation attempt FAILED/INCOMPLETE; không tiếp tục dùng freeze cũ.**
[Kết quả thật,incident và fix](../../data/native_validation_activation_20261019_01/RESULTS.md).
14/15cells hoàn tất,một timeout; dispatcher rawloader7folds lệch5folds frozen bị
guard chặn. Raw file chứa holdout đã bị decode ngoài ý định; không gọi untouched.
14cell metrics đúng pre-cut input nhưng chỉ4–7trades,đều dưới gate30. Không có
official selection/complete coverage hay live approval. Đã sửa frozenloader ở
dispatcher/provenance/slices và truyền folds,regression+ACPASS trên source mới.
Không rerun tự động; trước campaign mới cần đọc lại evidence hiện có về tần suất/
concentration và preregister scope/budget/sourcefreeze mới. P2sensitivity vẫn mở.
Các thông tin preflight accepted dưới đây là lịch sử,trước lượt chạy thất bại.

Preflight singleton ADX25 hiện đã tạo và được review độc lập ACCEPTED:
[packet và bằng chứng](../../data/native_singleton_preflight_20261019_01/RESULTS.md).
Đúng5folds/15inner cells/3contracts,quality6795barsPASS,70targeted tests và
AC01–15PASS. Chưa chạy cells; chưa approval execution. Không còn thiếu adapter
preflight; bước tiếp là phê duyệt scope/budget và đường kích hoạt supervised đúng
frozen protocol. Các nhận định adapter còn thiếu bên dưới là lịch sử.

P1 đã được sửa và có156targeted tests/AC01–15PASS trên source mới:
[bằng chứng fix](../../data/native_binding_fix_20261019_01/RESULTS.md).
Đã kiểm frozen-vs-raw-vs-artifact ở cache publication/read và coverage;
không đổi planned cell_id. P2 sensitivity vẫn chưa hoàn tất cho FULL qualification.
Review ban đầu và kế hoạch bị chặn bên dưới được giữ làm lịch sử; trước chạy
validation-only vẫn phải có singleton ADX25 adapter và freeze/budget đúng scope.

Review độc lập mới trả REQUEST_CHANGES:
[review và packet dự thảo có budget](../../data/native_evaluation_review_20261019_01/PLAN.md).
Phải sửa expected-vs-raw/cache/final contract binding trước chạy; sensitivity
preregistration còn thiếu cho FULL qualification. Dữ liệu hiện có chỉ đủ nhãn
revalidation đã quan sát, không chứng minh untouched OOS. Packet tiếp theo đề xuất
validation-only15cells,1800s/2048MiB/1worker/0retry; chưa approve/freeze thực thi.

Xem [native adoption packet](../../data/native_policy_adoption_20261019_01/RESULTS.md).
Pure preflight declaration và frozen contract theo cell đã nối vào canonical
qualification worker; mismatch phải bị chặn trước bar đầu tiên. Legacy protocol
không có map contract bị từ chối, không âm thầm nâng cấp packet cũ.
Source/data/cost vẫn được bind riêng trong protocol; identity match không chứng
minh lợi nhuận hay execution thực tế hoàn hảo. Direct diagnostic simulator vẫn
cho phép expected ID tùy chọn; không tuyên bố mọi CLI đều bị khóa.

Tiếp theo: review độc lập revision và bằng chứng; xác minh data eligibility/access
history; preregister packet native mới (dataset/window/folds, cost/sensitivity,
gate nguyên trạng, timeout/RSS/tổng budget, zero automatic market retries).
Chỉ chạy sau scope/budget được chốt; chưa có campaign mới hoặc live approval.
Sensitivity canonical tự khai báo contract từ config hiện hành; chưa tuyên bố
mọi neighbor/sensitivity cell có expected ID riêng freeze trong map qualification.

## Lịch sử trước quyết định native — không phải target parity hiện tại

Latest [evaluation closure](../../data/evaluation_closure_20261019_02/RESULTS.md)
đã sửa thêm outer parallel wrapper làm rơi expected contract.12screening cells và
3validation Sharpe của RSI/ADX đã được corroborate; packet WFO gốc bị xóa nên
không coi là nghiệm thu.128tests/AC01–15PASS không thay thế market evidence.
Local audit loop đóng với RESEARCH_HOLD; native/research economic parity, mandatory
all-CLI enforcement và independent review vẫn chưa hoàn tất.

Native reporting + optional expected cell-contract check đã được tích hợp:
[evaluation closure](../../data/evaluation_closure_20261019_01/RESULTS.md).
Expected ID mismatch chặn trước price update đầu tiên; không đổi trading policy.
Chưa bắt buộc ở mọi CLI, chưa economic parity. Audit RSI/ADX cũng phát hiện
report calendars giao khoảng holdout; cần xác minh exact data/access history
trước tuyên bố untouched hoặc một qualification scope mới. Old prototype notes
bên dưới là lịch sử, không phải trạng thái tích hợp hiện tại.

[Execution-contract prototype](../../data/bnb_execution_contract_20261019_01/RESULTS.md)
đã có19tests PASS, tách semantic identity khỏi evidence binding và ghi rõ reference
research không đổi. Prototype chưa nối vào preflight/reporting: WFO mới đang chạy.
Không được gọi identity match là behavioral parity, hoặc coi prototype là gate.

Operator đã cho phép tự hoàn tất vòng engineering, không cần duyệt từng thao tác
nội bộ. [Real-engine synthetic handoff](../../data/bnb_native_parity_accounting_probe_20261019_01/RESULTS.md):
signal parity840/840; economic parity không đạt, sizing native18.75% khác25%.
Caller accounting thiếu marked-equity basis đã được tích hợp sau khi các process
research dừng: [integration handoff](../../data/bnb_accounting_integration_20261019_01/RESULTS.md).
57tests PASS; caller regression đã xanh,2 native synthetic cases không wrapper
đối soát vị thế mở thành công. Economic parity vẫn không đạt; không mở market run.

Synthetic counterexample pass đã chạy:
[parity findings](../../data/bnb_policy_parity_20261019_01/RESULTS.md).
29 regression tests PASS không có nghĩa parity PASS. Direct4h native loop trace
xác nhận khác stop sampling; RiskController API không arm cooldown sau ordinary
record_trade. Full sell/reentry, fill-ledger, cost và risk-reset parity chưa đạt.
Giữ candidate, không thay live protection/gates để ép kết quả khớp.

Lập contract parity research V2 ↔ native qualification/runtime bằng synthetic
fixtures: signal t-1, stop dựa nến đã đóng, thời điểm fill, cooldown từ fill,
entry25% và exposure drift, fee/spread/impact, open-position accounting.
Chọn policy thực sự muốn qualify rồi mới sửa adapter tối thiểu nếu cần.
Không coi cùng MA/ADX parameters là cùng execution policy.

Native qualification entry point là `scripts/run_wfo_parallel.py`; không thay bằng
runner multiwindow hoặc campaign reuse. `--preflight-only` cũng có thể tạo thư mục:
không coi mọi CLI preflight là read-only. Không chạy CLI này trong vòng hiện tại.

Trước market run phải có: source/data hashes, exposure/trial ledger, folds và test
scope hợp lệ, cost/sensitivity/gates đã freeze, terminal criteria, independent
pre-review, operator approval cho scope/budget cụ thể và packet mới.
**Folds/test scope và market budget hiện chưa approved**, không phải runnable plan.
Cost floor/selection-score hurdle cần quyết định riêng: không tự sửa promotion gate.
Fixed-fill cost diagnostic không phải stress replay.

## Quyền hạn và ngân sách vòng hiện tại

Chỉ metadata/code inspection, synthetic regression và tài liệu; mỗi lệnh qua
controlled_exec có timeout/result receipt. Không campaign thị trường mới, không
holdout prices, download, publish, remote push, broker/account, paper/live, gate
changes hoặc overwrite packet. Approval các run trước không chuyển sang run mới.
Scope heartbeat `ho-n-t-t-synthetic-parity-bnb` đã hoàn tất tích hợp/verify và
bàn giao, không phải vòng lặp vô hạn hay quyền chạy market. Dừng monitor sau
handoff này; công việc execution-contract tiếp theo không được tự gọi là qualified.
