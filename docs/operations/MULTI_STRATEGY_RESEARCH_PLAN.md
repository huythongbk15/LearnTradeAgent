# Kế hoạch nghiên cứu nhiều strategy — từ diagnostic đến qualification

Ngày: 2026-10-16. Trạng thái: **DRAFT — chưa cấp quyền chạy campaign mới**.
Baseline engineering: `cdf39ce132a22bac32e5daff8c95f009bf9ff567`.
Integration follow-up: exact selected commit identity is recorded in
`data/retire_wfo_wrapper_20261016_01/compatibility_commit_receipt.json`;
fresh post-commit evidence in `data/compatibility_post_commit_20261016_01/`.
Earlier dirty-patch receipts below remain historical. Author self-review does
not replace independent sign-off or operator approval for a new campaign.
Không thay thế [quy trình calibration](../CALIBRATION_PROTOCOL.md) hoặc native
gate contract. Không phải khuyến nghị đầu tư hay chứng nhận live.

## 1. Kết luận và luồng quyết định

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
