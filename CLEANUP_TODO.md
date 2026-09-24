# CLEANUP — Replace hardcoded evidence pipeline with StrategyCatalog framework

## Audit complete: STRATEGY_RESEARCH_AUDIT.md

- [x] Audit tất cả scripts hardcode (19 violations, 7 files)
- [x] Ghi nhận audit trong STRATEGY_RESEARCH_AUDIT.md

## Cleanup

- [ ] Commit STRATEGY_RESEARCH_AUDIT.md + CLEANUP_TODO.md
- [ ] Xóa `evidence_workstream_b.py` (720 lines hardcode + mock)
- [ ] Xóa `evidence_workstream_b_verify.py` (chỉ import từ ewb)
- [ ] Sửa `scope_lock.py:30-35` — thay `R04_LOCKED_STRATEGIES` bằng catalog IDs
- [ ] Sửa `wfo_tier_c.py` — thay `TIER_C_STRATEGIES` bằng catalog IDs
- [ ] Sửa `evidence_ac04.py:36` — `volatility_breakout_v2` → `volatility_breakout`
- [ ] Cập nhật `.gitignore` thêm `CLEANUP_TODO.md`

## Run framework

- [ ] Chạy `python scripts/strategy_research/run_campaign.py --phase all`
- [ ] Xác nhận 8/8 strategies chạy đúng (6 single-asset + 2 cross-asset)
- [ ] Kiểm tra campaign_summary.json
- [ ] Generate SelectionPolicyArtifact từ passing strategies

## Verification

- [ ] Không còn `unittest.mock.patch` trong evidence scripts
- [ ] Không còn `LOCKED_STRATEGIES` hardcode
- [ ] Param grids từ `param_grids.py` (12-24 combos)
- [ ] Workers = 4 (theo ResearchProtocol)