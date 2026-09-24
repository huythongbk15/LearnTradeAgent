# CLEANUP — Replace hardcoded evidence pipeline with StrategyCatalog framework

## Phase 1: Audit & Cleanup (DONE)

- [x] Audit tất cả scripts hardcode (19 violations, 7 files)
- [x] Ghi nhận audit trong STRATEGY_RESEARCH_AUDIT.md
- [x] Commit audit
- [x] Xóa `evidence_workstream_b.py` (720 lines hardcode + mock patch)
- [x] Xóa `evidence_workstream_b_verify.py` (chỉ import từ ewb)
- [x] Sửa `scope_lock.py:30-35` — R04_LOCKED_STRATEGIES → 8 catalog IDs
- [x] Thay `R04_LOCKED_PAIRS` → 5 assets (BTC/ETH/SOL/BNB/XRP)
- [x] `wfo_tier_c.py` — thay legacy TIER_C_STRATEGIES bằng catalog S1-S3+S6+S7
- [x] `evidence_ac04.py` — `volatility_breakout_v2` → `volatility_breakout`
- [x] Update `param_grids.py` — `get_strategy_code_name` nhận cs_variant param
- [x] Update `run_campaign.py` — pass cs_variant + `--cost "all"`
- [x] Update `strategy_catalog.py` — 6 TODO → READY, code_class set
- [x] `.gitignore` thêm `CLEANUP_TODO.md`
- [x] Commit cleanup

## Phase 2: Smoke Test (running)

- [ ] Verify pipeline: vol_target + regime_ensemble on BTC/USDT 1h
- [ ] Check campaign_summary.json structure
- [ ] Verify no mock patching, workers=4

## Phase 3: Full Campaign

- [ ] `--phase single-asset` (6 strategies × 5 assets × 2 TFs = 60 cells)
- [ ] `--phase cross-asset` (2 strategies × 2 variants × 2 TFs = 8 cells)
- [ ] `--phase portfolio-analysis` (correlation, diversification)

## Phase 4: Verification

- [ ] Không còn `unittest.mock.patch` trong evidence scripts
- [ ] Không còn `LOCKED_STRATEGIES` hardcode legacy
- [ ] Param grids từ `param_grids.py` (12-24 combos per strategy)
- [ ] Workers = 4 (theo ResearchProtocol)
- [ ] Cost scenarios = all 3 (1x, 2x, slip_stress)
- [ ] Generate SelectionPolicyArtifact từ passing strategies