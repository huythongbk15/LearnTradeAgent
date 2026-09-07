# Tiến độ và lộ trình các phase

> Snapshot rà soát: **2026-09-07** · P0: **R0–R4 (R00,R01,R02,R03,R04) ✅ COMPLETE** · Production mainnet: **NO-GO**

## Cập nhật điều phối 07/09/2026 — đọc trước các bảng lịch sử

[Kế hoạch củng cố và bàn giao agent](KE_HOACH_CUNG_CO_VA_BAN_GIAO_AGENT.md) là backlog thực hiện hiện hành cho đợt này: R00–R09, dependency, ownership, acceptance tests và mẫu giao việc.

**R0–R3 đã nghiệm thu** (commit `3cb4d81`→`b95fac1`):
- **R00**: Baseline locked (commit `3cb4d81`, 167 tests baseline)
- **R01**: Adapter LegacyDataFrameAdapter + CanonicalRegistry parity VERIFIED (signal 1000/1000 bars identical, equity 0.00% diff)
- **R02**: Single S3 Validation Authority (canonical WFO + `cell_runner` callback, 13/13 equivalence tests PASS)
- **R03**: Provenance, completeness, resume (28/28 tests PASS, every trial record carries `evaluation_identity`)

| Phase | Trạng thái sau R0–R3 | Điều kiện tiếp theo |
| --- | --- | --- |
| S0–S1 | Nền tảng + R01 parity đã đóng | R01 schema/effective identity (signal/equity parity VERIFIED) ✅ |
| S2 | Engineering có; evidence binding đã đóng (R03) | R01–R03 ✅; matrix có completeness/provenance |
| S3 | Canonical WFO là thẩm quyền duy nhất (R02); 13 + 28 + 33 tests PASS | R02 ✅ + R03 ✅ + R04 ✅ → R05 (real policy) |
| S4 | Có lifecycle/policy; identity binding qua R03 | R03 ✅ + R05; approval tích hợp R08 |
| S5 | Có routing; campaign hiện tại chưa chứng minh adaptive execution | R05–R06 |
| S6 | Có allocator; campaign và tổng hợp stress còn thiếu | R07 với ledger chung, coverage từng pair |
| S7 | NO-GO | R08–R09 và operational evidence theo policy |

24 test trọng điểm và mypy 28 file đạt ở lần audit; Ruff có 12 lỗi trong script S6 local. Đây không phải full regression. Trạng thái P0 GREEN ở snapshot cũ không chứng nhận checkout hiện tại. Chưa có kết luận live bypass; các lỗi evidence và approval mới phải được xử lý trước khi dùng để promotion.

Các mục 1–3 bên dưới giữ bối cảnh/backlog cũ để truy vết; khi khác với cập nhật này, dùng bảng trên và kế hoạch R00–R09 cho điều phối, không dùng nhãn COMPLETE cũ để đóng việc. Artifact lịch sử không bị sửa hoặc xóa.

Tài liệu này là bảng điều phối ngắn gọn cho phần đã làm, phần còn thiếu và điều
kiện đóng từng phase. Đây là tài liệu tiến độ, không thay thế contract trong code,
policy đã ký hoặc evidence artifact bất biến.

## 1. Cách đọc trạng thái

| Nhãn | Ý nghĩa |
| --- | --- |
| **GREEN / COMPLETE** | Engineering và các gate nội bộ trong phạm vi phase đã đạt. Không đồng nghĩa đã được xác nhận bằng vốn thật. |
| **AMBER / EVIDENCE PENDING** | Capability đã có trong code/test nhưng còn thiếu campaign dữ liệu thật, OOS hoặc approval cần để promote. |
| **PARTIAL** | Một phần capability đã có; vẫn còn hạng mục tích hợp hoặc vận hành bắt buộc. |
| **RED / NO-GO** | Chưa được phép promote/canary/mainnet; hệ thống phải abstain, `NO_TRADE`, `REDUCE_ONLY` hoặc `BLOCK` khi thiếu điều kiện. |

Dự án dùng hai trục phase:

- **S0–S7**: vòng đời strategy — baseline, selection, routing, portfolio và
  operational evidence.
- **P0–P3**: readiness cho vận hành và phát hành — an toàn vốn, chất lượng thực
  nghiệm, soak và approval.

Hoàn thành S-phase không tự động hoàn thành P-phase tương ứng.

## 2. Bảng tiến độ hiện tại

### 2.1. Strategy lifecycle (S0–S7)

| Phase | Phạm vi | Trạng thái | Đã đạt | Việc còn lại để đóng phase |
| --- | --- | --- | --- | --- |
| **S0** | Baseline truth, dữ liệu và report correctness | **GREEN / COMPLETE** | Canonical data-quality gate; manifest gap có provenance; replay/report schema nhất quán | Chỉ mở lại khi thay đổi data contract hoặc nguồn dữ liệu |
| **S1** | Canonical strategy contract và registry | **GREEN / COMPLETE** | Registry, strategy adapter, risk/execution contract và regression tests | Giữ compatibility; mọi strategy mới phải qua cùng contract |
| **S2** | Tournament và execution-equivalent comparison | **AMBER / EVIDENCE PENDING** | 10 pair × 1h, execution simulation, attribution/health checks; STR-0208 process isolation/resource budget hoàn tất; bounded signal generation giữ causal history | Khóa campaign evidence từ clean commit và chạy lại matrix bất biến |
| **S3** | Nested WFO và statistical selection | **AMBER / EVIDENCE COMPLETE** | Nested expanding WFO, purge/embargo, trial registry, DSR/PBO/CI, sensitivity (cost_2x, slippage_stress, drop_best_trade, delay_1_bar, parameter_neighbors) và formal `NO_TRADE` path đã có; **WFO medium campaign 2026-09-04 chạy xong với 168 cells + verdict NO_TRADE có provenance đầy đủ** (see `docs/vi/WFO_MEDIUM_EVIDENCE_2026_09_04.md`) | Multi-dim backing by regime/year/vol_bucket populated ✅; outer persistence atomic ✅; chờ re-run với clean worktree để có provenance_eligible=1 |
| **S4** | Selection policy, promotion và provenance | **AMBER / EVIDENCE PENDING** | Research lifecycle, content identity, signed policy, rollback và release-attestation validators đã có | Dọn adapter `ArtifactLifecycle` cũ (`S4-0403`); tạo promotion artifact thật có code/data/features/params/cost hash và approval |
| **S5** | Regime router và safe switching | **AMBER / EVIDENCE PENDING** | Posterior versioning, OOD/entropy guard, persistence, dwell/cooldown, position ownership và fail-closed adaptive runtime | Re-evaluate adaptive trên locked OOS; chứng minh adaptive thắng incumbent hoặc chủ động abstain; hoàn tất shadow evidence |
| **S6** | Shared-capital allocator | **AMBER / EVIDENCE PENDING** | Aggregate cap, correlation cluster, pro-rata scaling, duplicate-key rejection và attribution đã có | Chạy multi-pair shared-capital campaign, partial-fill/correlation stress và reconciliation evidence |
| **S7** | Shadow, testnet, canary và production promotion | **RED / NO-GO** | Shadow guard, promotion stages, drift/calibration/reality-gap modules và release gate code đã có; **multi-party approval chain implemented (research/risk/compliance/operator/admin) với 13 tests passing** (see `src/trading_agent/authority/approval.py`) | Testnet/shadow/canary soak, 100 lifecycle, calibration, named approvals và exact release attestation; ed25519 signature wiring |

### 2.2. Production readiness (P0–P3)

| Phase | Trạng thái | Mục tiêu đóng phase |
| --- | --- | --- |
| **P0 — Safety/data foundations** | **GREEN / COMPLETE** | Permission gate, fail-closed data trust, strict OHLCV quality, provenance exception, regression và static gates xanh. |
| **P1 — Operations** | **PARTIAL** | Monitoring/alert được nối vào runner; account hardening, backup/restore, incident drill và broker/testnet runbook có evidence. |
| **P2 — Empirical quality** | **AMBER / EVIDENCE PENDING** | Outer-OOS net result, execution scenarios, calibration, drift và reality-gap đều dùng dữ liệu held-out thật; synthetic chỉ là diagnostic. |
| **P3 — Release gates** | **RED / NO-GO** | Testnet và shadow tối thiểu 30 ngày, canary tối thiểu 30 ngày, không unresolved critical event, approval và rollback drill. |

## 3. Đầu việc còn lại theo thứ tự thực hiện

### Workstream A — Đóng S2 và chuẩn bị campaign (ưu tiên P0/P1)

- [x] Đưa timeout/retry và resource budget vào chính `run_cell()`, không phụ
  thuộc orchestration bên ngoài. **(STR-0208 COMPLETE — 2026-09-01)**
- [ ] Khóa danh sách pair, timeframe, strategy registry, cost model và commit.
- [ ] Chạy lại tournament bằng cùng data manifest; lưu run identity và hash.
- [x] Kiểm tra cell failure được cô lập, không làm mất toàn campaign và không
  biến lỗi thành kết quả hợp lệ.

**Đầu ra:** tournament matrix bất biến, health summary, execution/economic
attribution và run manifest có thể replay.

**Bằng chứng 2026-09-01:** worker bị dừng thật tại deadline; retry/fatal/resource
exit đều fail-closed; strict 1-cell smoke hoàn thành trong 8,8 giây và artifact
ghi đầy đủ execution-control policy. Tail-10 inline giảm từ 211,7 giây xuống
4,4 giây. Regression tournament/control/fault đạt 48 test; full fast suite đạt
1.302 passed, 9 skipped.

### Workstream B — Đóng S3 bằng evidence thật (ưu tiên cao nhất)

- [ ] Tạo clean release commit; không chạy promotion từ dirty worktree.
- [ ] Freeze untouched final holdout và study manifest.
- [ ] Chạy nested WFO cho canonical registry trên toàn bộ scope đã khóa.
- [ ] Kiểm tra leakage, purge/embargo, feature availability và trial count từ
  registry thật.
- [ ] Tính outer-OOS net return, Sharpe, PF, MDD, Calmar, DSR, PBO/CSCV, CI,
  parameter stability, positive folds/pairs, concentration và cost stress 2×.
- [ ] Chạy sensitivity: bỏ best trade, delay một bar và parameter neighbours.
- [ ] Phát hành đúng một kết luận: `FINAL_PASS` nếu vượt toàn bộ hard gate, hoặc
  `NO_TRADE` nếu không có candidate đạt.

**Hard gate mặc định:** outer-OOS net return > 0; Sharpe ≥ 0,80; PF ≥ 1,20;
MDD ≤ 10%; Calmar ≥ 0,50; DSR ≥ 0,95; PBO ≤ 0,20; stability ≥ 0,70; ≥60% outer
fold dương; ≥60% pair dương; concentration ≤35%; cost stress 2× vẫn có net
positive và PF > 1; tối thiểu 30 trade cho mỗi pair-strategy OOS và 200 trade
cho portfolio aggregate.

**Đầu ra:** WFO evidence artifact, holdout result, formal gate decision và
`NO_TRADE` explanation nếu không promote được.

### Workstream C — Đóng S4: policy và promotion identity

- [ ] Chuẩn hóa một state machine `ResearchLifecycle`; chỉ giữ
  `ArtifactLifecycle` cũ ở compatibility boundary có test.
- [ ] Bind exact commit, image digest, data/features/parameters/cost/search-space
  identity vào artifact.
- [ ] Ký và verify policy/artifact; kiểm tra Cosign, SBOM, SLSA và provenance
  cùng một release identity.
- [ ] Bắt buộc actor, ticket, reason và approval; thiếu bất kỳ trường nào thì
  fail-closed.
- [ ] Tạo rollback artifact và replay kiểm tra rằng artifact cũ không thể chạy
  khi identity đã lệch.

**Đầu ra:** promotion artifact bất biến, signature/provenance bundle, approval
record và rollback proof.

### Workstream D — Đóng S5: adaptive routing có kiểm chứng

- [ ] Dùng locked OOS để so sánh adaptive với từng incumbent/fixed strategy và
  baseline `NO_TRADE`.
- [ ] Kiểm tra không look-ahead trong posterior, regime feature và route event.
- [ ] Stress stale posterior, OOD, entropy cao, thiếu chữ ký, restart/replay,
  duplicate observation và switch trong lúc còn position.
- [ ] Đo switch rate, dwell time, exposure, turnover, latency, abstention và
  contribution theo pair/regime.
- [ ] Giữ nguyên nguyên tắc: adaptive không vượt gate thì không được quay về một
  strategy mặc định để che kết quả; phải `NO_TRADE` hoặc giữ incumbent hợp lệ.

**Đầu ra:** adaptive routing artifact, OOS comparison, shadow replay và policy
quyết định `activate / hold / abstain`.

### Workstream E — Đóng S6: shared-capital và attribution

- [ ] Chạy portfolio campaign nhiều pair với cap theo strategy, symbol và
  correlation cluster.
- [ ] Stress correlation spike, volatility spike, partial/unknown fill,
  conflicting forecasts và duplicate request key.
- [ ] Reconcile cash, gross/net exposure, fee, slippage, turnover và ledger theo
  từng event.
- [ ] Kiểm tra attribution strategy/pair/regime/factor; dữ liệu thiếu phải ghi
  `unattributed`, không suy diễn từ outcome.

**Đầu ra:** portfolio evidence artifact, constraint report, reconciliation proof
và attribution report.

### Workstream F — Đóng P1/P2/P3 và S7 vận hành

- [ ] Nối metrics/alerts vào live runner; thử kill switch, stale data, broker
  disconnect, unknown order và restore từ snapshot.
- [ ] Testnet tối thiểu 30 ngày và ≥100 order lifecycles hoàn chỉnh, không có
  unresolved critical event.
- [ ] Shadow tối thiểu 30 ngày, ≥30 observation cho calibration, ECE ≤0,10;
  reality-gap/tracking-error/drift không vượt threshold.
- [ ] Canary tối thiểu 30 ngày, loss budget không vi phạm; có rollback drill.
- [ ] Hoàn tất operator approval, ticket, incident review và release attestation
  đúng exact commit/image digest.

**Đầu ra:** operational evidence bundle, calibration/drift/reality-gap report,
soak logs, approval record và quyết định `CANARY-READY` hoặc `NO-GO`.

## 4. Phụ thuộc và thứ tự triển khai

```text
S2 cell isolation
      ↓
S3 real nested WFO + frozen holdout
      ↓
S4 signed selection/promotion artifact
      ├──→ S5 adaptive OOS/shadow validation
      └──→ S6 shared-capital validation
                 ↓
P1 observability + operational drills
                 ↓
S7 / P2 / P3 testnet → shadow → canary → manual production approval
```

S5 và S6 có thể chạy song song sau khi S3 tạo được artifact/model set hợp lệ,
nhưng không được bỏ qua S4 identity và policy. S7 không được bắt đầu soak với
artifact chưa có exact provenance.

## 5. Tiêu chí hoàn thành chung

Một phase chỉ được chuyển sang **COMPLETE** khi có đủ:

1. Code và test contract tương ứng.
2. Evidence artifact có `commit`, `data`, `features`, `parameters`, `cost` và
   version identity.
3. Kết quả replay deterministic và có thể truy ngược từ report về ledger/event.
4. Open risks, owner, ticket và quyết định `PASS`, `NO_TRADE` hoặc `NO-GO` rõ ràng.
5. Nếu là promotion/operation: chữ ký, approval và rollback proof tương ứng.

Không dùng các kết quả sau để đóng phase: synthetic benchmark đơn lẻ, backtest
đẹp nhưng thiếu OOS, số liệu do caller tự khai báo, hoặc test pass nhưng thiếu
provenance.

## 6. Nguồn kiểm chứng và nơi cập nhật

- [Adaptive roadmap status](../ADAPTIVE_ROADMAP_STATUS.md) — chi tiết S0–S7 và
  các item kỹ thuật.
- [S7 operational evidence runbook](../S7_OPERATIONAL_EVIDENCE_RUNBOOK.md) —
  payload testnet/shadow/canary và exact release attestation.
- [Live trading TODO](../LIVE_TRADING_TODO.md) — P0–P3 release checklist.
- [Capability matrix](../CAPABILITY_MATRIX.md) — phân biệt Implemented, Tested,
  Paper/Testnet và Production Validated.
- [Data quality exception manifest](../../config/data_quality/binance_spot_gap_exceptions.json)
  — provenance cho gap 1h đã được phê duyệt.
- [Data quality verifier](../../scripts/verify_data_quality.py) — lệnh kiểm tra
  lại gate dữ liệu trên dataset cụ thể.

Sau mỗi campaign phải cập nhật snapshot ngày, commit, artifact IDs và trạng thái
gate trong tài liệu này và `ADAPTIVE_ROADMAP_STATUS.md`. Mainnet chỉ có thể rời
`NO-GO` sau khi toàn bộ P3/S7 evidence và operator approval đã hoàn tất.
