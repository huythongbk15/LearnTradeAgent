# Strategy Research Audit — Hardcode Findings

## Summary
The entire evidence pipeline (S3/S6/Workstream B) bypasses the StrategyCatalog framework,
running legacy strategies with hardcoded param grids, mock patches, and incorrect worker counts.
8 strategy candidates exist in `strategy_catalog.py` — only 4 are used across evidence scripts,
and never via the catalog's param grids or research protocol.

## Hardcoded LOCKED Strategies (3 legacy — NOT in catalog)
| Source File | Constants | Strategies |
|---|---|---|
| `src/trading_agent/backtest/scope_lock.py:30-35` | `R04_LOCKED_STRATEGIES` | rsi, ma_adx, enhanced_ma |
| `scripts/evidence_workstream_b.py:75-77` | `LOCKED_STRATEGIES` | rsi, ma_adx, enhanced_ma (duplicated) |
| `scripts/evidence_workstream_b_verify.py:69` | imports from `ewb._check_scope_lock` | same |
| `scripts/run_s3_campaign.py:50-56` | `r04_default_scope()` | inherits same 3 legacy |

## Param Grids: 1 combo each vs catalog's 12-24 combos
| Script | Strategy | Param combos | Should be |
|---|---|---|---|
| `evidence_workstream_b.py:88` | rsi | `period[14], oversold[30], overbought[70]` | S2: 9 combos (VWAP+z-score+BB) |
| `evidence_workstream_b.py:88` | ma_adx | `fast[20], slow[80], adx[25]` | S1: 12 combos (ADX+Fib+MA+vol) |
| `evidence_workstream_b.py:88` | enhanced_ma | `fast[20], slow[80], adx[25]` | S6: 9 combos (vol_target grid) |

## Mock/Monkeypatch Violations (§9.2: mock-based tests do NOT count)
| Script | Line | What's Monkeypatched |
|---|---|---|
| `evidence_workstream_b.py:26,38-48` | `unittest.mock.patch` → `_fast_run_cell` to disable multiprocessing inline | Bypasses real cell runner |
| `run_s6_campaign.py:~220` | Monkeypatches `load_ohlcv` for synthetic data mode | Fake data injection |
| `run_s3_campaign.py` | Uses `synthetic_wfo_spec()` when `--synthetic` flag set | Synthetic data in real evidence |

## Worker Count
| Script | Workers | Protocol says |
|---|---|---|
| `evidence_workstream_b.py:578` | 9 (WFO_WORKERS env) | ResearchProtocol.workers=4 |
| `wfo_tier_c.py:39` | 1 | Acceptable (cell-level) |
| `run_s3_campaign.py` | scope-based (from r04 scope) | 4 |

## Non-Catalog Strategy Names (should be from STRATEGY_CATALOG)
| Script | Strategy | In catalog? | Catalog equivalent |
|---|---|---|---|
| `wfo_tier_c.py:42` | `ensemble_ma_adx` | NO | S1 trend_pullback or S7 regime_ensemble |
| `wfo_tier_c.py:42` | `ma_adx_regime` | NO | S1 trend_pullback with regime filter |
| `wfo_tier_c.py:42` | `ma_crossover` | NO | S6 vol_target (has MA crossover) |
| `evidence_ac04.py:36` | `volatility_breakout_v2` | NO | S3 volatility_breakout (missing _v2) |
| `evidence_workstream_b.py` | `rsi` | NO | S2 range_mean_reversion (VWAP+z-score) |
| `evidence_workstream_b.py` | `ma_adx` | NO | S1 trend_pullback (Fib+vol+ADX) |
| `evidence_workstream_b.py` | `enhanced_ma` | NO | S6 vol_target (MaVolTargetCrossover) |

## Path Typos
| Script | Hardcoded path | Issue |
|---|---|---|
| `evidence_workstream_b.py:546,592,594,623` | `/tmp/wvo_workstream_b/` | typo: `wvo` vs `wfo` |
| `evidence_workstream_b.py:114` | `data/wvo/workstream_b_*.sqlite3` | same typo in registry |
| `evidence_workstream_b_verify.py` | `/tmp/wvo_workstream_b/` | same typo |

## Correct Workflow
Use `scripts/strategy_research/run_campaign.py`:
```bash
python scripts/strategy_research/run_campaign.py --phase all
```
- Phase 1: 6 single-asset strategies × 5 assets × 2 TFs = 60 cells
- Phase 2: 2 cross-asset strategies × 2 variants × 2 TFs = 8 cells
- Phase 3: Portfolio correlation + diversification analysis
- Output: `data/backtests/wfo/research/campaign_summary.json`

## Action Items
- [ ] Remove or update `R04_LOCKED_STRATEGIES` in `scope_lock.py` → use catalog strategy IDs
- [ ] Rewrite `evidence_workstream_b.py` to use `StrategyCatalog` + `param_grids.py`
- [ ] Remove `unittest.mock.patch` — use real cell runner
- [ ] Fix worker count to 4 (per ResearchProtocol)
- [ ] Fix path typos (`wvo` → `wfo`)
- [ ] Update `evidence_workstream_b_verify.py` to match
- [ ] Update `wfo_tier_c.py` to use only catalog strategy IDs
- [ ] Fix `evidence_ac04.py` strategy ID (`volatility_breakout_v2` → `volatility_breakout`)
- [ ] Commit all fixes before re-running any campaign