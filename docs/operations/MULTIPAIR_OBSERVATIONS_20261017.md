# Quan sát đa pair — 2026-10-17

## Review/tích hợp engineering — 18/10/2026

**Lock recovery đã được operator duyệt18/10:** lỗi git add128 trước đó do lock
rỗng từ17/10được giữ ở integration_blocker.json/commit_execution.json. Đã kiểm
lại process Git, file users, inode/mtime/size; rename đúng unused lock sang
index.lock.backup_20261018 trong review folder, không xoá. lock_backup.json ghi
nhận recovery. Local commit identity/verification được ghi receipt riêng để
không sửa precommit evidence. Không push/merge remote hoặc mở paper/live.

Đã rà diff ba file production execution (application, permission, lifecycle)
và runner/tests/docs của case, không gộp thay đổi khác của operator. Thêm
type-check rõ ở runtime permission và submit: chỉ nhận PermissionContext;
HistoricalPermissionPreview/PermissionResult/dict/None không là quyền submit.
Không dựa vào AttributeError tình cờ để fail-closed. Test previewALLOW với đầy
đủ unit fixtures vẫn bị từ chối trước ghi intent/event/order; real stale context
vẫn bị từ chối. Không đổi policy25%entry, source strategy hoặc economic gates.

Precommit verification trên source/test identity ổn định:

- 126regression tests PASS (network connect blocked, global monitoring writes
  disabled trong test driver),42lifecycle/accounting tests PASS riêng; không
  cộng các counts overlapping lịch sử vào chúng.
- Lint PASS, AC01–AC15 PASS:
  `data/acceptance_runs/ac01_15_20261018_004800_utc/verification.json`.
- 53file raw evidence/experiment/decision/history giữ nguyên hash; source/tests
  không đổi trong verification. Source khác lần17/10do hardening chủ đích,
  không relabel evidence17/10thành proof source mới.
- Review script/driver hashes, before.json/after.json, review.json và controlled
  receipts ở `data/bnb_integration_review_20261018_01/`.

Whitelist tích hợp9file được ghi trong review.json; không bao gồm wfo_wrapper.sh,
MULTI_STRATEGY_RESEARCH_PLAN, tool guard changes hoặc dữ liệu/state của user.
Index/hook được kiểm trước local integration; không push/merge remote. Identity
sau local integration phải gắn receipt riêng, không ghi đè precommit review hoặc
AC snapshot. Self-review không phải independent reviewer/human signoff.

**Paper admission vẫn NO_GO.** ResearchPromotionGate requirements diagnostic,
khi chưa cung cấp formal evidence bundle, thiếu outer_oos, minimum_trades,
deflated_sharpe, pbo, cost_stress, parameter_stability. Đây không phải tuyên bố
đã scan hết mọi registry lịch sử; chỉ case đang đóng không được chứng minh bởi
bundle đã kiểm.7trades/validation đã xem và simulation có profit không tự đạt
30trades/OOS/DSR/PBO. UNKNOWN runtime calibration/OOD/regime chưa được giải quyết;
operator approval25%entry không thay thế chúng. Không tạo formal evidence hoặc
promotion giả từ metrics/decision.

Phần engineering đã tiến thêm nhưng không thể tuyên bố đủ paper bằng cách chỉ
code tiếp. Cần protocol/evidence qualification hợp lệ và applicability contract
cho deterministic MA trước khi mở đường runtime. Không tự mở holdout, chạy
campaign mới, hạ gates hoặc bật account/testnet/live trong bước review này.

## Quyết định operator — giữ25% lúc vào lệnh

Operator đã chọn rõ **giữ nguyên25%entry allocation**, cho phép exposure tăng
theo giá như peak40.13%đã quan sát. Phạm vi là case BNB/USDT4h MA20/80 ADX14/25;
không đổi chính sách toàn bộ pair/strategy hoặc cấp quyền paper/live.

Contract giữ nguyên theo implementation hiện tại:

- Khi flat→long, tính quantity từ25%equity và giá tham chiếu open lúc sizing,
  floor theo qty step. Fees/spread/impact tính riêng: đây không phải hard cap
  của filled notional/all-in cash cost sau khớp.
- Không tự bán/rebalance chỉ vì mark-to-market exposure vượt25%; không scale-in
  khi đã long. Drift40.13%không phải vi phạm một hard MTM cap chưa được chọn.
- Signal SELL, stop, drawdown/daily-loss halt và cooldown của thí nghiệm vẫn
  giữ nguyên. Cho phép drift không đồng nghĩa tắt risk controls hoặc mọi mức
  rủi ro đều được chấp thuận.
- Không đổi stop timing cho runtime thật, không hạ gates hoặc chuyển evidence
  UNKNOWN→KNOWN; không phê duyệt chạy market scenario mới bằng quyết định này.

Lưu decision riêng tại `data/bnb_entry_allocation_decision_20261017_01/decision.json`
(explicit chat instruction, không phải chữ ký số). **9contract tests PASS**:
entry sizing theo3mức giá; exposure40%không rebalance/scale-in; SELL/stop/halt vẫn
hoạt động; scope không cấp live/new-run authority. Không sửa run.py/test_run.py,
manifest, receipts hoặc kết quả của experiment đã đóng. Không thay trading logic
nên không chạy lại market simulation hoặc relabel AC evidence cũ.

Mục chọn entry-vs-hard-MTM cap đã đóng. Bước gần nhất tiếp theo là review/tích hợp
source đã kiểm và xử lý eligibility/evidence contract cho runtime; không mở rộng
pool hoặc chọn live chỉ vì thí nghiệm có profit dương.

## Kết quả mới nhất — execution research có fills, chưa đủ live

Đã tái sử dụng **MarketReplayEngine V2** và ExecutionLedger hiện có, không thêm
runtime stage/framework, không gọi FullSystemSimulator để tự tạo promotion.
Experiment cố định tại `data/bnb_execution_replay_20261017_01/`: manifest trước
chạy, run.py/test_run.py, raw_result.json, orders.json, decisions.json, signals.json,
fills.json, result.json và auditor arithmetic riêng audit.py/audit.json.

**Verdict: PASS_EXECUTION_RESEARCH_ONLY / INSUFFICIENT_EVIDENCE_OF_ALPHA.**
Đã có vòng hẹp signal→simulated intent→fill→cash/position→persisted fills→ledger
reconstruction. Không phải canonical broker lifecycle admission, realtime paper,
engine process pause/resume hoặc live readiness. Không tạo qualified policy,
PAPER_ELIGIBLE, KNOWN evidence hay broker/account IO.

### Sửa sai signal timing trước khi đo

Đối chiếu từng bar cho thấy admission/clock sandbox trước **32action mismatches**
so report research gốc, dù tổngBUY7/SELL9/HOLD1064 giống nhau. Chúng dùng legacy
MA signal ở timing khác. Vì vậy evidence cũ vẫn chứng minh permission/clock
checks của input đã chạy, nhưng **không chứng minh signal parity với case gốc**.
Giữ nguyên các receipts cũ; không sửa lại để tạo lịch sử toàn đúng.

Đã sửa run_bnb_operational_sandbox.py dùng chính canonical_signal_series và
build_parameterized_adapter của research. Contract hiện hữu: signal[j] dùng
window head(j), execution ở j+1; feature window cuối ở execution_index−2.
Không "sửa" research timing contract trong experiment này. Guard so từng action
và execution_bar trên1080bars với raw report đã pinnedSHA; mismatch bất kỳ sẽ
dừng trước fills. Kết quả mới **1080/1080PASS**, không chỉ bằng tổng số signal.
Thêm tests positive/negative parity và future/decision-bar perturbation.

### Contract đã freeze trước chạy

BNB/USDT4h, MA20/80 ADX14/25; indices[2360,3440), thêm context bar2359 chỉ để
cung cấp previous volume, không mở vị thế ở context. Initial cash10000, spot
long/flat, entry sizing25%equity tại open, không rebalance liên tục. Cooldown24h
theo last sell fill; maxDD15% và daily loss8% được kiểm từ prior closed mark.
Stop5% tính từ average fill cost, trigger bằng prior completed low, exit market
ở open kế tiếp; TP/trailing off, không force-liquidate cuối. Stop priority trước
signal exit. Book tick0.01/qty_step0.001/min_notional10 là local simulation rules,
chưa exchange-verified. Seed42, taker/maker fee0.1%, spread10bps, depth/impact và
latency theo SimulationConfig đã lưu. Impact/book giả lập từ OHLCV chưa calibrated.
Unfilled market remainder không tự retry. Hai runs cùng seed chỉ kiểm engineering
determinism, không phải hai economic trials độc lập hoặc selection sweep.

**Không so return trực tiếp với baseline11.87%**: opening capital/sizing scale,
close-bar stop thay intrabar open→low→close protection, mark-sampled risk và cost
model khác. Không gọi đây là reproduction parity của toàn bộ baseline; chỉ
signal timing là exact parity. Không chọn/tune tham số sau khi thấy lợi nhuận.

| Chỉ số | Kết quả audit từ persisted data |
|---|---:|
| Evaluation bars | 1080 |
| Orders / filled orders / fill events | 14 / 14 / 14 |
| Closed round trips | 7 |
| Signal exits / closed-bar stops | 5 / 2 |
| Ending cash/equity, vị thế cuối | 11684.6222USDT, flat |
| Gross closed PnL / fees / net PnL | 1725.3972 / 40.7750 / 1684.6222USDT |
| Net return | +16.8462% |
| Max drawdown magnitude | 8.3089% |
| Sharpe annualized4h,365.25×6bars/year | 2.2135 |
| Win rate / profit factor,7round trips | 28.57% / 5.0668 |
| Mean / peak close-mark exposure | 13.23% / 40.13% |
| Time in market | 46.85% |

Raw engine DD dùng số âm−8.3089%; bảng trình bày magnitude, không sửa raw.
Besttrade net1863.3255USDT lớn hơn totalnet1684.6222; sáu trades còn lại cộng
−178.7033. Profit dương vẫn tập trung một trade, không đủ chứng minh edge.
**Entry allocation25% không phải hard mark-to-market exposure cap25%**;
không gọi peak40.13% là đã kiểm soát đúng một hard cap chưa được định nghĩa.

Auditor không dùng ledger helper để cộng cash/inventory/fees, kiểm từng fill
quantity/fee,7round trips và1080close marks; tính lại Sharpe/DD; kiểm520protected
files gồm source, original packet và receipts trước. PASS. Đây là một chương
trình arithmetic audit riêng, **không phải independent human review**.
Persisted fill journal tái dựng ledger khớp cash/position/realized PnL/fees;
duplicate IDs không nhân fill trong **audit reconstruction wrapper**, conflicting
duplicate bị reject trong tests. Đây không chứng minh native engine dedup/resume
toàn bộ state: book/pending orders/RNG/impact chưa được process restart ở đây.
Raw theoretical_alpha_pnl mặc định0chưa được tính, không dùng làm alpha evidence.

Verification bản cuối: **96targeted tests PASS**, gồm adapter, canonical timing,
V2 property invariants, clock/permission và authority/resolver; lint PASS.
Fresh AC01–AC15 PASS, identity_stable=true:
`data/acceptance_runs/ac01_15_20261017_142332_utc/verification.json`.
Source vẫn local dirty trên HEADb897c32, tree
`9540f44ad83eed543e0244538a6a247faa7f85c7ea47684cb920959f8ba82ce7`.
Không phải clean integrated revision/release. Receipts execution_replay_*.json
trong `data/bnb_adx_operational_20261017_01/`. Market simulation runtime8.35s,
không đổi source/packet trong run, không mở holdout hoặc tải dữ liệu mới.

### Chốt hướng tiếp theo, không mở rộng strategy pool

1. **Đã chốt entry allocation25%, không hard MTM cap** theo quyết định phía trên.
   Stop timing cho runtime thật vẫn cần contract phù hợp; không coi25%entry là
   cap rủi ro liên tục. Nếu đổi contract về sau phải đăng ký trước, không tune
   sizing/exit path để cứu16.85%return.
2. Giữ verdict kinh tế chưa đủ mẫu. Không chọn live chỉ từ7trades hoặc besttrade;
   không mở holdout, hạ gates, hay fabricate calibration/OOD/regime.
3. Đóng gói/review/tích hợp đúng source đã kiểm trước khi paper thật. Nếu cần
   resume, dùng canonical lifecycle đã có và test đúng quyền admission, không
   đánh đồng ledger reconstruction với engine resume. Không xây runner thứ ba.

## Case trọng tâm: BNB/MA ADX25 — bước1 hoàn tất

Đóng phạm vi về1pair/1strategy/1config: BNB/USDT4h, MA20/80, ADX14/25,
spot long-only. Không tuning, không thêm pair/selector/timeframe. Mục tiêu là
một vòng đời nhỏ có verdict thật, không bắt buộc phải có lợi nhuận đủ live.

**Verdict bước1: ACCOUNTING_RECONCILED / INSUFFICIENT_EVIDENCE_OF_ALPHA.**
Không phải strategy đã bị chứng minh vô dụng; cũng không đủ điều kiện
qualification/live. Hai windows đã xem và case được chọn sau screening, không
được relabel thành OOS. Gates không đổi. Bước1 chỉ phân tích dữ liệu có sẵn.

Authority: `data/bnb_adx_case_20261017_01/step1_results.json`;
`verified_execution.json` PASS. Raw report hashes, từng closed trade net PnL,
gross/fees/slippage, marked equity, position quantity theo từng bar và3metrics
return/Sharpe/DD được đối chiếu.34protected files/input không đổi; source không
đổi trong analysis. Không chạy simulator, account/broker hay market campaign.

### Benchmark và phương pháp

Primary baseline chọn trước khi tính:25%opening equity mua BNB tại open đầu
measurement,75%cash không lãi; fractional quantity, mua một lần không rebalance,
commission0.1% và adverse slippage0.05% mỗi phía theo recorded cost model.
Mark tại close từng bar giống strategy; không bắt buộc liquidate cuối window.
Không spread/impact/lot rounding/stoploss; đây là phép tính benchmark analytical,
không phải execution-equivalent paper run. Giá trị entry và equity return được
kiểm thêm bằng công thức endpoint độc lập; cash baseline0%.

| Chỉ số | Fold0:2024-01-29 08Z→2024-07-27 08Z exclusive | Fold1:2025-01-23 08Z→2025-07-22 08Z exclusive |
|---|---:|---:|
| Closed trades strategy | 7 | 4 |
| Strategy net marked return | +11.87% | +2.44% |
| Buy/hold initial25% net marked return | +22.89% | +2.53% |
| Strategy − primary benchmark | −11.02điểm% | −0.09điểm% |
| Strategy max DD | 6.97% | 2.15% |
| Benchmark max DD | 14.86% | 6.72% |
| Strategy Sharpe | 2.047 | 1.085 |
| Benchmark Sharpe | 1.953 | 0.510 |
| Strategy time in market | 46.67% | 48.15% |
| Strategy average close-mark exposure | 10.02% | 9.29% |
| Buy/hold average exposure | 36.34% | 23.49% |

Vì25%là **initial allocation**, không phải constant exposure, benchmark tự drift
theo giá. Strategy target25% cũng không phải hard mark-to-market cap mọi thời
điểm; observed peak close exposure31.69%/21.67%. Không gọi bảng này là risk-matched
alpha test hoặc kết luận strategy kém hơn chỉ dựa return tuyệt đối.

Sensitivity mô tả bổ sung: giải weight buy/hold để average exposure trong window
bằng strategy, cho return5.53%/1.01%, so strategy11.87%/2.44%. **Weight này dùng
dữ liệu evaluation để tính (ex post)**, không phải policy có thể giao dịch từ
trước và không là bằng chứng OOS/alpha. Average exposure matching vẫn không bằng
volatility/DD matching. Không chọn benchmark nào có lợi cho verdict sau tính.

### PnL đã thực hiện, vị thế mở và độ tập trung

| Thành phần (USD) | Fold0 | Fold1 |
|---|---:|---:|
| Opening marked equity | 10246.6960 | 11518.9234 |
| Gross closed PnL | +1261.5120 | −62.4224 |
| Closed fees | 30.4243 | 17.1264 |
| Closed slippage | 15.2125 | 8.5632 |
| Closed net PnL | +1215.8752 | −88.1119 |
| Open unrealized, gồm entry fee | 0 | +369.2631 |
| Total marked equity delta | +1215.8752 | +281.1512 |

Gross−fees−slippage=closednet; closednet+openunrealized=equitydelta. Không trừ
thêm0.32×trades vào net return đã tính chi phí. Không sửascore/gates của code.
Nếu hypothetically liquidate cuối window với cùng fee/slippage: strategy return
11.8660%/2.4080%; benchmark22.8145%/2.4901%. Đây chỉ là exit-cost sensitivity,
không phải actual fills. Không biến unrealized thành realized trong raw report.

Fold0besttrade net+1435.89USD, lớn hơn tổngnet+1215.88USD. Phần đóng góp còn lại
là−220.01USD. Đây là concentration diagnostic, không phải backtest loại trade
(sizing/cash path sẽ thay đổi nếu thực sự loại). Không đủ7/4trades để kết luận
edge ổn định hoặc chọn tham số khác sau khi xem kết quả.

### Điều kiện chuyển bước, không mở rộng dự án

- Bước1 đã đóng với kết quả trên. Không chạy lại grid để ép verdict tốt hơn.
- Bước2 vẫn chưa đủ feasibility về mẫu/folds theo contract hiện hành; chưa mở
  outer/holdout. Không hạ gates để cứu case. Nếu cần protocol mới phải đăng ký
  trước và xin quyền, giữ history đã xem.
- Có thể đề xuất paper/shadow **operational-only** cho đúngconfig này để đi hết
  vòng đời ứng dụng: chứng minh signal→intent→order→fill→position→reconcile và
  stop/restart. Phải cấp quyền riêng, không publish qualified policy hoặc
  bypass promotion gate. Chưa thực hiện bước này trong analysis.
- Khi paper kết thúc, kết luận vận hành và kết luận lợi thế kinh tế phải tách
  riêng. Một case vận hành đúng nhưng chưa có lợi thế được chứng minh vẫn là
  kết quả trọn vẹn; không gọi live-ready.

## Case operational follow-up — offline replay hoàn tất

Ngày17/10, không sửa production code hoặc xây runner mới. Đã tận dụng chính
events.db/paper_binance.json của BNB/MA ADX25; SQLite backup từ read-only source
và JSON copy vào `data/bnb_adx_operational_20261017_01/window_0`, window_1.
Không connect event store writable trực tiếp vào packet gốc. Không tạo/promote
policy, không giả scores và không submit thêm case orders.

| Assertion | Cửa sổ0 | Cửa sổ1 |
|---|---:|---:|
| Events replay, gồm cả simulation prefix | 393 | 627 |
| Lifecycle orders, gồm prefix/protection | 47 | 75 |
| Measured strategy orders linked | 12 | 8 |
| Startup load == full global replay | PASS | PASS |
| Repeated event IDs không tạo fill mới | PASS | PASS |
| Intent/risk/authorization/payload/fill links | PASS | PASS |
| Filled buys−sells == persisted position | PASS | PASS |
| Position quantity sau restart | 0 | 3.318BNB |
| Equity sau restart | 11462.5712 | 11800.0745 |
| Replay execution health | normal | normal |

Measured order IDs đối chiếu với cả paper orders và durable intent/risk decision/
authorization; filled_size không vượt authorized_quantity. Closed measured trade
IDs có trong persisted paper ledger. Paper state được mở lại2lần, balances/
orders/positions khớp.33originalpacket files giữ nguyên. Event count1020và122orders
không phải1020measurement events hoặc122measurement orders; measurement chỉ20
strategy-returned orders, không bao gồm tất cả protective orders.

Evidence: `offline_replay_receipt.json`, `chain_receipt.json` và controlled
execution logs trong `data/bnb_adx_operational_20261017_01/`.

Generic lifecycle/paper/accounting tests: **42/42PASS**, network connect bị chặn,
global monitoring write callbacks tắt cho phiên test; local paper JSON/events
persistence vẫn chạy thật trong test fixtures. Telemetry unit tests tự inject
recording sinks. Đây không kiểm acceptance của monitoring database production.
Lần wrapper đầu chạy từartifactCWD:40PASS/2FAIL vì subprocess không tìm script
migration relativepath. Đã chạy lại từrepoCWD, không sửa source/tests,42PASS;
giữ cả2logs trong `safety_tests.json` và `safety_tests_corrected_cwd.json`.
Không cộng42vào các counts integration lịch sử có scope khác.

**Verdict tách biệt:**

- Research: INSUFFICIENT_EVIDENCE_OF_ALPHA, không qualification/live.
- Recorded execution/restart: OFFLINE_CASE_REPLAY_PASS.
- Generic safety regression:42PASS, không proof ứng viên đã trải qua mọi failure
  mode trong một paper session thực tế.
- Continuous/realtime paper: **NOT_STARTED**, chưa có policy phù hợp đã được
  phép kích hoạt. Hai script tournament_paper_trader/run_tournament_shadow dùng
  defaults/timeframe khác và DRAFT policies không được activate khi thiếu
  evidence; không dùng chúng để ép case chạy hoặc sửa gate.

Đã có vòng offline nhỏ có thể tái lập: recorded signal→intent→risk/authorization
→order→fill→position→persist→restart→reconcile, và một kết luận kinh tế chưa đủ
bằng chứng. Không gọi đây là live readiness hoặc operational soak. Chuyển sang
continuous paper đòi hỏi chốt đúng eligibility/authority path, không tự đặt
RESEARCH_VALIDATED/PAPER_ELIGIBLE cho một unqualified policy. Chưa cấp quyền
account/testnet/mainnet, không mởholdout hoặc chạy thêm research campaign.

## Clock diagnostic và contract evidence — follow-up hoàn tất

Thay đổi nhỏ, không thêm môi trường/stage/framework: `TrustedPrice.is_fresh_at`
là phép kiểm theo một timestamp explicit; `is_fresh` runtime vẫn lấy giờ thật.
Timestamp aware bắt buộc; future/stale và age limit không hữu hạn/không dương
đều fail-closed. `preview_historical_order_permission` tái dùng các checks, trả
`HistoricalPermissionPreview` riêng, không có `allowed()`, không phải
PermissionResult/PermissionContext/durable authorization. `submit_planned` vẫn
re-evaluate context bằng giờ thật, không nhận preview làm quyền submit.

Sandbox bổ sung preview tại đúng bar timestamp, **không retimestamp market data**,
không tăng stale limit, không sửa gate evidence. Receipt mới:
`data/bnb_adx_clock_sandbox_20261017_01/{manifest,trace,result}.json`.

| Đường kiểm tra cho7BUY proposals | Kết quả |
|---|---|
| Runtime real clock, kill-switch bật | 7BLOCK stale_market_data |
| Historical preview, cùng kill-switch bật | 7BLOCK kill_switch_increase |
| Conditional preview giả định riêng kill-switch tắt | 7BLOCK missing_calibration_evidence |

Conditional probe **không tắt kill-switch của lifecycle**, không submit order
và không tạo phiên paper khác. OOD/regime vẫn UNKNOWN; session chưa đi tới hai
checks đó, unit fixtures riêng xác nhận chúng vẫn block. Tổng1080bars, BUY7/
SELL9/HOLD1064, orders/fills0, cash/equity10000, flat restart PASS. Không phải
trading acceptance hoặc economic test mới. Runtime98.47s;508source/packet files
giữ nguyên hash trong run. So với bản sandbox cũ, source có thay đổi clock chủ
đích; không tuyên bố source hiện tại bằng source lịch sử của sandbox_04.

Verification cho source hiện tại:

- 82 targeted tests PASS: clock boundaries, timezone/invalid limits, evidence,
  kill-switch, sandbox safety, resolver/admission. Có test previewALLOW với
  đầy đủ **unit fixture** evidence nhưng actual canonical submit vẫn bị chặn
  stale, event store0orders0. Không trộn fixture vào case evidence.
- 42 lifecycle/paper/accounting tests PASS, network denied/global monitoring writes
  disabled theo wrapper đã ghi rõ trước đó. Không cộng overlapping counts cũ.
- AC01–AC15 **PASS**, identity_stable=true, output mới
  `data/acceptance_runs/ac01_15_20261017_140720_utc/verification.json`.
- Lint PASS. Controlled receipts `clock_tests.json`, `clock_safety_tests.json`,
  `clock_acceptance_execution.json`, `clock_lint.json` trong operational folder.

Đây là **local dirty snapshot** trên HEADb897c32, tree fingerprint
`605839ae1d03134aeb628bd3df92dcc8cb1a7de482116270545bb0b46db4d634`;
không phải clean integration commit, release signoff hay live qualification.
Không sửa/merge các thay đổi khác của operator, gồm untracked wfo_wrapper.sh.

### Root cause và hướng tiếp — tách đúng ba loại bằng chứng

1. **Engineering/calibration-protocol acceptance**: packet/identity/budget/
   receipts đúng. Không tự chứng minh alpha hoặc forecast calibration.
2. **Economic qualification**: validation/OOS/trades/cost/stability. Case này
   vẫn thiếu mẫu, đã được chọn sau screening; không relabel validation thành OOS.
3. **Runtime risk evidence**: predictive calibration/OOD/regime phải có đúng
   producer, applicability và provenance, không thể điền theo operator approval.

`StrategyRuntime.execute` trả confidence0.5 cho series signal và mặc định các
stateUNKNOWN nếu artifact không cung cấp. MaAdxCrossover sinh crossover có ADX
filter, không tự sinh calibrated probability, OOD score hoặc fitted regime
posterior. Vì vậy không lấy literal0.5 để tính/khẳng định ECE đạt chuẩn; trước
tiên phải định nghĩa event/horizon/forecast nếu thực sự muốn mô hình xác suất.
Không cần gắn ML chỉ để ép một rule-based MA qua gate.

**Đầu việc kế tiếp nhỏ nhất:** quyết định applicability contract cho deterministic
strategy, không sửa threshold chung. Phân biệt simulation-only sizing/risk
contract với qualified runtime risk contract. Với execution research có fills,
ưu tiên xem xét `MarketReplayEngine`/ledger hiện có: engine có clock thị trường
và long-only accounting, không cần bịa PAPER_ELIGIBLE. Kết quả đó phải mang nhãn
research simulation, không phải canonical-runtime admission/paper promotion;
phải freeze cost model, stops/exit semantics và chỉ rõ parity trước chạy.
Chưa chạy engine này hoặc thay cost/exit rules trong follow-up hiện tại.

Nếu chọn đi tiếp canonical runtime thật, cần source-bound evidence producer
và profile được review trước, tests chứng minh không dùng exemption/simulation
receipt để authorize PAPER/TESTNET/PRODUCTION. Không tự thêm N/A→KNOWN hoặc
"operator approved→calibrated". Không dùng legacy `_from_agent_message` (còn
literalKNOWN); engine `execute_signal` đã disabled, không mở lại để né contract.
Không dùng FullSystemSimulator auto-generated local promotion/KNOWN metadata
làm bằng chứng đủ điều kiện runtime; đó là convention của historical simulator.

## Local sandbox đã duyệt — kết quả thực thi trước clock diagnostic

Operator đã duyệt sandbox local sau admission review bên dưới. Đã thêm một CLI
phạm vi cố định `scripts/run_bnb_operational_sandbox.py`, không framework/lane
promotion mới. Đây là **non-submitting historical admission sandbox**, chưa
phải full operational sandbox có trading fills hoặc continuous paper.

Evidence chính: `data/bnb_adx_admission_sandbox_20261017_04/manifest.json`,
`trace.json`, `result.json`; controlled execution:
`data/bnb_adx_operational_20261017_01/sandbox_run_v4_execution.json`.
Manifest đóng source hashes/data SHA/config/window1080bars trước xử lý. Code
không gọi submit; không cho đổi pair/params/window qua CLI; output phải là
directory mới dưới workspace/data và từ chối symlink escape/ghi đè. Socket
connect/connect_ex/create_connection bị chặn trong process; không phải chứng
nhận OS-level network isolation. PaperExchange telemetry=None, không tạo
promotion store hoặc active policy. Instrument rules là local fixture, chưa
phải exchange-verified rules. Không cấp broker/testnet/live authority.

| Kết quả | Giá trị |
|---|---:|
| Bars đã xử lý, indices[2360,3440) | 1080 |
| BUY / SELL / HOLD | 7 / 9 / 1064 |
| BUY proposals qua canonical planner | 7 |
| Permission BLOCK: stale_market_data | 7 |
| Orders submitted / fills | 0 / 0 |
| Ending cash/equity | 10000 / 10000 |
| Flat-state restart | PASS |
| Source/original packet hashes không đổi | 508files |
| Runtime controlled | 137.72s, limit180s |

Signals dùng268nến trước bar execution, không bao gồm current/future bar;
proposal dùng next bar open. SELL trong tài khoản flat không mở short hoặc
tạo inventory giả; HOLD không bị ép thành order. Giữ timestamp lịch sử thật,
không đổi thành now để qua freshness. Kill-switch giữ bật; proposed sizing
budget25% **không phải** measured risk/economic approval. Calibration/OOD/regime
để UNKNOWN, không điền KNOWN giả. Permission dừng ở stale trước các checks sau;
**không kết luận session đã exercise evidence hoặc kill-switch rejection**.
Unit control với quote fixture fresh riêng xác nhận unknown evidence và
kill-switch vẫn chặn; stale fixture xác nhận stale block. Không có fixture fill
nào được trộn vào case metrics.

Regression bản cuối: **47PASS** (9sandbox tests +17boundary +21resolver), lint
PASS. Receipts `sandbox_tests_final.json`, `sandbox_lint_final.json` trong
operational directory cũ. Không cộng counts chồng lặp vào các lần trước. Không
sửa các gate files thuộc mandatory AC triggers; AC01–AC15 chưa chạy lại ở bước
này, không relabel evidence AC cũ thành evidence cho runner mới.

Giữ các lần thất bại: sandbox_01 sai giả định epoch thay vì parquet datetime;
sandbox_02 mismatch enum type planner/lifecycle; sandbox_03 dùng enum value
uppercase/lowercase chưa tương thích. Đã sửa runner bằng UTC datetime contract
và mapping enum name, thêm tests; sandbox_04 là kết quả hoàn tất. Không sửa hoặc
xoá packet/lần thất bại để tạo một lịch sử toàn PASS.

**Verdict: PASS_ADMISSION_BLOCKED_NOT_TRADING_ACCEPTANCE.** Economic verdict
không đổi: INSUFFICIENT_EVIDENCE_OF_ALPHA. Lợi nhuận0ở sandbox này chỉ do cash
và không có fills; không phải backtest mới chứng minh strategy có/không có edge.
Flat restart không thay thế filled restart; proof filled replay trước đây vẫn
chỉ thuộc recorded offline case, không phải session này.

### Việc cốt lõi tiếp theo — không chạy vòng research mới để che blocker

Chưa hoàn thành mục tiêu sandbox full trade. Muốn dùng historical inputs qua
trọn lifecycle mà vẫn giữ safety thật, cần thiết kế/review **simulation clock
contract** tại adapter boundary (không retimestamp data, không tăng stale limit)
và contract evidence/risk phù hợp deterministic MA. Phải làm rõ calibration/OOD/
regime áp dụng thế nào cho strategy này; không đánh dấu KNOWN chỉ vì operator
cho phép simulation hoặc vì profit dương. Giữ risk/gates hiện tại fail-closed
cho tới khi có quyết định contract có tests và provenance thật. Chưa triển khai
clock/evidence exception, chưa thêm promotion stage/lane.

Nếu chỉ cần unit tests có fills, bộ canonical execution tests hiện có đã làm
việc đó bằng fixtures; không cần tạo một "paper PASS" thứ hai bằng fake scores.
Không tự launch realtime runner hoặc tải dữ liệu để né clock issue. Sandbox
hiện đóng bằng kết luận bị chặn đúng; tách phần còn thiếu khỏi nghiệm thu.

## Admission review lịch sử — trước khi xây hoặc chạy sandbox

Rà soát tiếp ngày17/10: **BLOCKED_BY_ADMISSION_CONTRACT**, không phải lỗi
strategy hoặc lý do để hạ qualification gates. `PromotionStateStore.ENV_MIN_STAGE`
cho phép EXPLORATORY ở RESEARCH, nhưng PAPER yêu cầu PAPER_ELIGIBLE; resolver
kiểm authoritative store trước cache/binding. Quyền research không phải quyền
paper/testnet/live. Chưa thấy lane operational-only trong đường paper đã kiểm.

Không sử dụng `scripts/tournament_paper_trader.py` cho case này:

- Script đọc OHLCV lịch sử và gọi tournament router, không có vòng submit/fill
  execution như tên/header mô tả. Không phải continuous realtime paper runner.
- Defaults1h/MA10/30 khác config đã chốt; router dùng environment production.
- Incumbents DRAFT, challengers còn literal scores/placeholder identity; không
  được sử dụng làm evidence hoặc activate. Chưa chạy/import script này.
- `main()` dùng `shutil.rmtree` xoá thư mục kết quả nếu tồn tại, kể cả dry-run;
  không được launch để ghi đè evidence. Source chưa sửa trong review này.
- Khi có chosen strategy, script cộng underlying bar return, không chứng minh
  portfolio return từ positions/fills/costs. Không dùng summary làm paper PnL.

Verification: `tests/authority/test_operational_paper_boundary.py` thêm17ca,
cùng resolver regression thành **38PASS**. Kiểm thiếu promotion, EXPLORATORY/
RESEARCH_VALIDATED bị từ chối PAPER/TESTNET/SHADOW/CANARY/PRODUCTION kể cả cache
có runtime; không bind strategy và không tự nâng stage. Stage mapping có
positive controls RESEARCH/EXPLORATORY và PAPER/PAPER_ELIGIBLE; không chứng minh
full admission thành công chỉ từ mapping. Test records chỉ trong tmp fixtures.
Network connect bị chặn.507file source/original case packet giữ hash trước/sau.
Evidence: `data/bnb_adx_operational_20261017_01/admission_boundary_receipt.json`
và `admission_boundary_execution.json`. Không cộng38vào counts trước khác scope.

### Đề xuất nhỏ nhất cần quyết định trước khi implementation

Giữ nguyên qualification gate. Nếu operator chọn thử vận hành ứng viên chưa đủ
economic evidence, phê duyệt **local operational sandbox**, không gọi đó là
PAPER_ELIGIBLE hoặc paper qualification. Contract riêng phải giới hạn:

1. BNB/USDT4h, MA20/80 ADX14/25, spot long-only, config/source/data hashes đóng
   băng trước chạy; không tuning/chuyển strategy. Không thêm router/framework.
2. Chỉ PaperExchange local với state/event-store mới, network denied, không
   account credentials hoặc live/testnet adapter. Không ghi promotion/active
   selection policy hay monitoring production; từ chối output dir đã tồn tại.
3. Dùng nến đã có theo thứ tự thời gian: phải gọi **historical operational
   simulation**, không gọi realtime/soak/OOS. Muốn nến mới phải cấp quyền nguồn
   market-data và chốt cutoff/budget riêng, không tự mở holdout.
4. Tận dụng planner/lifecycle/gateway/paper adapter hiện có, có manifest test-only
   thể hiện quyền simulation tách qualification; không tái sử dụng quyền đó
   trong PAPER/TESTNET/PRODUCTION hoặc giả RESEARCH_VALIDATED/PAPER_ELIGIBLE.
5. Trước chạy chốt finite bar/time budget và stop rules; nghiệm thu order/fill
   idempotency, durable authorization, restart, accounting/reconciliation,
   stale/duplicate/out-of-order input, kill-switch, open-position-at-stop.
   Không yêu cầu phải sinh lệnh nếu signal thật là HOLD; không ép trade cho đẹp.

Tại thời điểm review này chưa phê duyệt contract riêng hoặc tạo lane mới; kết
quả sandbox sau phê duyệt được ghi ở mục phía trên. Có thể đóng
case hiện tại bằng offline replay PASS + economic insufficient; muốn đi xa hơn
phải chọn rõ sandbox nói trên hoặc giữ strict paper admission và chỉ chạy khi
đã có qualification thật. Không tự nới gate để tránh quyết định này.

## Screening lịch sử trước khi chốt case

Trạng thái: **PARTIAL SCREENING, không qualification/live**. Đã lấy số liệu bằng
runner WFO validation-only có sẵn, không sửa production source/gates để chạy.
Source sạch `b897c32126014056e49493e6f9e6cefebec26e6c`.

## Phạm vi và kết quả thực thi

Manifest: `data/multipair_screen_20261017_01/batch_manifest.json`.
Đăng ký trước20packet/40cells: ETH,BNB,SOL,XRP ×5defaults ×2validation folds.
4h,1xcost,ADX25,1worker,0retries,180s/cell,360s/packet,2048MiB sampled session
RSS,2700s aggregate execution ceiling. Không mở outer/sensitivity/holdout.
WFOSpec:12m expanding train,6m validation,3m reserved test,12m step; months dùng
30days theo engine. Không phải hai cửa sổ calibrationBTC trước, không reuse BTC.

Batch dừng sau~29.1phút execution (~29.7phút gồm preflight):

- **16cells/8packets qua coverage và được tổng hợp.** ETH đủ5strategy,BNB đủ3MA.
- **1packet BNB/RSI INCOMPLETE:** cell thứ hai timeout180s, một attempt, không
  retry. Verifier sau đó báo `validation report is unavailable` do failed
  artifact tham chiếu placeholder `nonexistent/report.json`. Không coi lỗi này
  là dữ liệu lợi nhuận của strategy; không tạo report giả để vượt verifier.
- **11packets chưa chạy:** BNB/BBands, toàn bộSOL vàXRP. Chỉ freeze manifest,
  không tính chúng như trials có metrics hoặc gán kết quảABSTAIN/FAIL kinh tế.
- Packet RSI/BNB có bằng chứng partial/failed riêng, không ghép first-fold của
  packet này vào16cells đã qua exact coverage.
- Historical packet hashes không drift, source ổn định. Shared registry có
  trial appends của lần chạy, không xóa failed attempt hoặc trial history.

Evidence: `batch_terminal.json`, `observations.json`, `reviewed_summary.json`,
`summary_execution.json` trong `data/multipair_screen_20261017_01/`.
16raw report SHA và16marked-equity PnL equations PASS. Đây là author audit trên
runner coverage, chưa independent sign-off hoặc full fill-ledger replay.

## Các số liệu quan sát

Trong mỗi ô: fold sớm / fold muộn, sắp theo **measurement_start số**, không theo
tên thư mục hash. Exact calendar/start/end và raw SHA có trong reviewed_summary.

| Pair | Strategy | Trades | Return % | Sharpe | Max DD % |
|---|---|---:|---:|---:|---:|
| ETH | Enhanced MA | 4 / 6 | -3.21 / -2.74 | -1.69 / -1.09 | 4.12 / 5.48 |
| ETH | MA ADX25 | 4 / 5 | -3.21 / +5.17 | -1.69 / +1.16 | 4.12 / 6.29 |
| ETH | MA Vol Target | 8 / 9 | +8.71 / +0.23 | +1.73 / +0.10 | 7.39 / 6.26 |
| ETH | RSI | 21 / 32 | -3.75 / -2.00 | -0.98 / -0.24 | 7.23 / 10.10 |
| ETH | BBands | 18 / 29 | -5.01 / -4.19 | -1.39 / -0.63 | 7.61 / 9.09 |
| BNB | Enhanced MA | 7 / 5 | +11.87 / +1.71 | +2.05 / +0.79 | 6.97 / 2.84 |
| BNB | MA ADX25 | 7 / 4 | +11.87 / +2.44 | +2.05 / +1.09 | 6.97 / 2.15 |
| BNB | MA Vol Target | 12 / 6 | +8.78 / +1.81 | +1.50 / +0.69 | 9.55 / 3.57 |

## Diễn giải đúng, không chọn winner từ screening

1. BNB/nhómMA và ETH/MA Vol Target có return/Sharpe dương ở2folds đã đo;
   đáng giữ trong research observations, **chưa đủ mẫu** để đề cử qualification.
   MAvariants chia sẻ logic nên không phải ba xác nhận độc lập về alpha.
2. RSI/ETH có1fold32trades đạt sample gate, nhưng Sharpe âm; tăng số trades
   không tự tạo lợi thế. Không cộng21+32để pass gate từng fold.
3. Không có group nào vừa đủ30trades ởcả2folds vừa Sharpe>0 trongcả2folds.
   Không policy nào được publish/promote từ kết quả này.
4. Return dùng opening marked equity của measurement. Profit factor dựa closed
   trades; open/carry PnL có thể khiến return dương trong khi PF<1.16equations
   net_closed +open_unrealized = marked_equity_delta đã đối chiếu. Không gán
   mâu thuẫn giả bằng cách so final equity với vốn10000của full simulation.
5. Canonical purge/embargo phụ thuộc history từng strategy. Cùng strategy giữa
   ETH/BNB có cửa sổ đồng nhất, nhưng **cross-strategy windows không hoàn toàn
   cùng calendar**. Không dùng bảng này để xếp hạngbeststrategy một cách công
   bằng, và không trộn với packetBTCcalendar khác.
6. Có nhiều hypotheses/trials đã xem. Giữ chúng trong multiple-testing burden;
   không reset history khi đổi pair hoặc chỉ báo candidate dương.

## Việc tiếp theo có giá trị

- Trước hết giữ và bàn giao số liệu này, không thay bằng thêm một vòng code
  kiến trúc. Đã có evidence rằng pair matters và sample/economics khác nhau.
- Lần chạy sau ưu tiên coverage còn thiếu theo kiểu round-robin pairs để một
  packet chậm không khiến SOL/XRP hoàn toàn chưa có quan sát. Không tự reorder
  hoặc mutate batch đã đăng ký/đang chạy; đăng ký execution schedule mới rõ ràng.
- Không retryRSI/BNB bằng cách tăng cap hoặc bỏ raw verifier. Nếu cần hoàn tất
  nó, đo/profile bottleneck trước, đề xuất budget mới hoặc sửa engineering có
  parity proof; giữ nguyên prefix semantics/carry nếu muốn so sánh cùng protocol.
- Các packet chưa execute có identity/budget/source binding riêng. Chỉ tiếp tục
  khi được duyệt budget còn lại và xác minh registry/source/input compatibility;
  không resume packetINCOMPLETE, không copymetrics vào packet mới.
- Trước một nghiên cứu **ranking** nhiều strategy: chọn chung calendar trong
  protocol, warmup/history riêng phía trước nhưng cùngmeasurement, và kiểm đúng
  contract hiện hỗ trợ. Đây là điều kiện để so sánh, không phải sửa cửa sổ sau
  khi thấy returns. Chưa được cấp quyền market run mới bởi tài liệu này.

Không thực hiện thêm campaign, không hạ thresholds, không tải dữ liệu, không
push/merge hay giao dịch live sau khi batch dừng.
