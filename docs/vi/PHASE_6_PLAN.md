# Phase 6 Plan — Multi-Pair Live Promotion

> Snapshot: **2026-09-04** · Scope: đóng S2/S3/S4/S5/S6 + P1 để mở khóa S7 testnet

## 0. Nguyên tắc chỉ đạo

1. **Fail-closed**: mọi evidence phải chạy qua pipeline thật (real data, real execution simulation), không chấp nhận synthetic stand-in.
2. **Provenance bắt buộc**: mỗi artifact phải bind với `commit_sha`, `data_manifest`, `feature_schema`, `cost_model`, `environment_hash`.
3. **NO_TRADE là kết quả hợp lệ**: nếu evidence không đủ mạnh, campaign phải fail-closed thay vì relax thresholds.
4. **No regression**: mọi thay đổi phải giữ 1020+ tests pass, ruff clean, mypy không tăng errors mới.

---

## 1. Trạng thái hiện tại (carry-over từ Phase 0–5)

| Phase | Status | Carried work |
|---|---|---|
| **S0** | ✅ GREEN | – |
| **S1** | ✅ GREEN | – |
| **S2** | 🟡 AMBER | STR-0208 (timeout/retry) ✅; còn: clean commit + lock evidence |
| **S3** | 🟡 AMBER | S3-1..S3-3, S3-7, S3-10 ✅; còn S3-4, S3-5, S3-6, S3-8, S3-9, S3-11 (mypy) |
| **S4** | 🟡 AMBER | Cần: cleanup `ArtifactLifecycle` legacy (S4-0403); tạo promotion artifact thật |
| **S5** | 🟡 AMBER | Adaptive re-eval trên locked OOS; shadow evidence; mean-reversion cho SIDEWAYS |
| **S6** | 🟡 AMBER | Multi-pair shared-capital campaign; correlation stress |
| **S7** | 🔴 NO-GO | Toàn bộ (testnet, canary, approvals) |
| **P0** | ✅ GREEN | – |
| **P1** | 🟡 AMBER | Monitoring chưa live; calibration gate; soak 100 lifecycles |

---

## 2. Phase 6 — Mục tiêu 4 tuần

### Week 1: Đóng S3 hoàn toàn (WFO campaign thật)

**Outcome:** Có `wfo_summary.json` với verdict `FINAL_PASS` hoặc `NO_TRADE` từ frozen holdout, provenance đầy đủ.

| Day | Task | Owner-agent | Verification |
|---|---|---|---|
| 1 | Kill WFO medium đang treo; re-launch với bounded config (1 cost, 3 params) | trading | `wfo_summary.json` xuất hiện trong 2h |
| 2 | Đợi WFO xong → record verdict vào `wfo_decision.json` | trading | Artifact ID matches, all 13 gates evaluated |
| 3 | S3-4: implement full sensitivity (delay-1-bar, param neighbors) + tests | trading | `test_s3_sensitivity.py` pass |
| 4 | S3-5: wire real regime/year/vol bucket metrics từ trade-level data | trading | Multi-dim metrics xuất hiện trong report |
| 5 | S3-6: outer persistence atomic (rename tmp → final) | trading | Crash test: kill mid-write, verify recover |
| 6–7 | S3-8 portfolio gates, S3-9 composite artifact (verify exists) | trading | All 5 portfolio gates evaluated end-to-end |

### Week 2: Đóng S4 + S5 (Promotion + Adaptive evidence)

**Outcome:** Có promotion artifact thật + adaptive shadow evidence.

| Day | Task | Owner-agent | Verification |
|---|---|---|---|
| 8 | S4-0403: clean legacy `ArtifactLifecycle`, dùng unified `ArtifactVersion` | trading | `tests/strategies/test_artifact_*.py` pass; legacy code removed |
| 9 | Build real promotion artifact: code SHA + data manifest + features + params + cost hash | trading | `tests/strategies/test_promotion_binding.py` pass |
| 10 | S5: implement mean-reversion strategy cho SIDEWAYS regime (RSI/BB) | trading | `test_s5_regime_specific_strategy.py` pass |
| 11 | S5: re-evaluate adaptive router trên locked OOS (sử dụng S3 evidence) | trading | Adaptive vs incumbent comparison table |
| 12 | S5: shadow evidence — chạy 1 phiên paper với regime switch enabled | trading | `shadow_regime_evidence.json` xuất hiện |
| 13–14 | S4 promotion binding → candidate registry (dry-run, không promote thật) | trading | `promotion_dry_run.json` + approval workflow defined |

### Week 3: Đóng S6 + P1 (Shared capital + Monitoring)

**Outcome:** Multi-pair portfolio campaign + live monitoring wiring.

| Day | Task | Owner-agent | Verification |
|---|---|---|---|
| 15 | S6: multi-pair shared-capital campaign (BTC/ETH/SOL cùng lúc) | trading | `s6_portfolio_evidence.json` |
| 16 | S6: correlation stress (force-correlated pairs → verify rejection) | trading | `test_s6_correlation_stress.py` pass |
| 17 | S6: partial-fill reconciliation evidence (slippage stress trên portfolio) | trading | `s6_reconciliation_evidence.json` |
| 18 | P1: wire monitoring → Telegram/prom metrics (latency, fill rate, error rate) | trading | `live_metrics.json` streaming |
| 19 | P1: calibration gate live (ECE < threshold) | trading | `live_calibration.json` |
| 20–21 | P1: 100-lifecycle soak trên paper (số lifecycles phải ≥100 fail-free) | trading | `soak_100_evidence.json` |

### Week 4: Đóng S7 (Testnet + Canary + Promotion)

**Outcome:** Có thể promote candidate lên testnet tự động với approval chain.

| Day | Task | Owner-agent | Verification |
|---|---|---|---|
| 22 | S7: testnet connection test (Binance testnet, OKX demo) | trading | `testnet_connect_evidence.json` |
| 23 | S7: shadow → testnet promotion stage (1 strategy, 1 symbol) | trading | `testnet_promotion_log.json` |
| 24 | S7: canary stage (5% capital, 1 strategy) với auto-rollback nếu DD > threshold | trading | `canary_evidence.json` |
| 25 | S7: release attestation (sign approval, multi-party) | trading | `release_attestation.json` |
| 26–28 | **NO_NEW_WORK** — buffer cho việc phát sinh, fix bug, hoặc viết tài liệu | – | – |

---

## 3. Definition of Done cho Phase 6

### Functional gates
- [ ] `wfo_summary.json` tồn tại với verdict từ real frozen holdout
- [ ] Promotion artifact có đủ 5 hash (code, data, features, params, cost)
- [ ] Adaptive router có shadow evidence ≥ 100 bars
- [ ] Multi-pair portfolio có evidence ≥ 50 trades OOS
- [ ] Testnet ổn định 7 ngày liên tiếp, không manual intervention

### Quality gates
- [ ] mypy errors **không tăng** so với baseline 494
- [ ] Tất cả test pass (≥ 1100 tests)
- [ ] ruff clean
- [ ] git diff --check clean
- [ ] 0 commit thiếu provenance (commit_sha, data_manifest, environment)

### Operational gates
- [ ] Live monitoring: latency, fill rate, error rate, calibration streaming
- [ ] Auto-rollback test: trigger DD threshold, verify rollback trong < 60s
- [ ] Approval chain: 2-party sign, mỗi artifact cần ≥ 2 approvals

---

## 4. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| WFO verdict `NO_TRADE` do scope quá nhỏ | High | Low | Tăng scope dần; minimum 5 params × 1 cost |
| Promotion artifact thiếu hash → fail validation | Medium | High | S4-0403 cleanup + unit test cover mọi field |
| Adaptive strategy không thắng incumbent | High | Medium | Có thể chấp nhận abstain; không force adapt |
| Testnet fill rate khác paper quá nhiều | Medium | High | Calibrate simulator trước khi canary |
| Calibration drift sau 1 tuần live | High | Medium | Daily recalibration gate, auto-pause nếu ECE > 0.1 |

---

## 5. Điểm cần làm NGAY hôm nay (chờ WFO)

1. **S3-11 mypy cleanup tiếp** (~494 errors còn lại; focus: exchange adapters)
2. **Verify promotion binding code** tồn tại (`tests/strategies/test_promotion_binding.py`)
3. **Review S5 mean-reversion strategy** đã có gì (`src/trading_agent/strategies/regime_switching.py`)
4. **Kiểm tra live monitoring wiring** (`src/trading_agent/cli/commands/live.py`)
5. **Draft approval chain schema** (multi-party sign trong `authority/`)

---

## 6. Sau Phase 6 → Mainnet gating

Phase 6 hoàn thành → mainnet được mở khóa nếu:
- 30 ngày testnet ổn định liên tiếp
- 0 manual intervention
- Sharpe ratio OOS > 0.5 với CI 95% > 0
- Max DD live ≤ 2x backtest DD
- Fill rate live ≥ 95% paper

Đây là gate cuối cùng trước khi mainnet.
