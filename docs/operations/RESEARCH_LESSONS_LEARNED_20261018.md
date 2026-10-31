# Research Lessons Learned — 2026-10-18

Two research cycles completed under RESEARCH_RUN_PROTOCOL.md:
1. **Phase A**: Regime-switched ensemble (RsiAdxRegimeFilter) — BTC/USDT 1h
2. **Phase B**: BNB/ADX25 comparison — BNB/USDT 4h

> **Correction dated 2026-10-31.** The Phase A figures below come from the runs in [RSI_REGIME_SWITCH_EVIDENCE_CARD.md](RSI_REGIME_SWITCH_EVIDENCE_CARD.md). Their 2000-bar window is 2026-06-30 to 2026-09-21 and their 5000-bar window is 2026-02-25 to 2026-09-21, so both overlap the frozen holdout. The threshold sweep listed under "What worked" chose t=30 on those bars, and its t=20 and t=40 rows have no 5000-bar BTC report with the same parameters. Read the card's correction before reusing a Phase A number.

## Key patterns cross-validated

### Regime dependence (dominant failure mode)
| Strategy | Sideway window | Trending/Mixed window | Finding |
|---|---|---|---|
| RSI (Phase A) | Sharpe 3.15 (30 trades) | Sharpe -0.12 (73 trades) | Regime flip kills performance |
| rsi_adx_regime_filter (Phase A) | Sharpe 1.47 (20 trades) | Sharpe 0.49 (54 trades) | Filter helps but not robust |
| ma_adx (Phase B) | 16.85% return (4 trades) | 16.99% return (7 trades) | Sparse signals, non-significant |

**Root cause**: Mean-reversion loses money in trending markets;
   trend-following has insufficient signal density (~24-54 trades).

### Single-window insufficiency
- Every strategy "passes" on 1 window, fails on another
- Buy-hold benchmark dominates in bull windows (BNB: 22.91%; BTC 2000b: +3.18%)
- **Single window cannot establish alpha** — need ≥3 windows (sideway + trending + mixed)

### Trade density constraint
- Gate: min_trades = 30 (contract, not lowered)
- ma_adx: 7 trades BNB (FAIL), 24 trades BTC 5000b (FAIL)
- rsi_adx_regime_filter: 54 trades BTC 5000b (PASS count, but Sharpe <1.0)
- **Low trade count → high variance → cost-sensitive**

### Cost robustness
| Strategy | PF @1x | PF @2x | PF @3x | Verdict |
|---|---|---|---|---|
| RSI (Phase A) | 3.29 (2000b) | — | 0.91 (2000b) | FAIL @3x |
| rsi_adx_filter (Phase A) | 1.07 (5000b) | 0.80 | — | FAIL @2x |
| ma_adx (Phase A, BTC) | 1.46 | 1.21 | — | survive @2x |

**Stop signal threshold**: PF < 1.0 at next cost level → not cost-robust → eliminate.

## Protocol workflow (effectiveness assessment)

### ✅ What worked, keep:
1. **Frozen decision rules** — sweep thresholds (t=20–40) before metric inspection
2. **Signal parity verification** — catch config drift from historical replay
3. **Cost/score lineage audit** — 0.32×trades hurdle != realized net edge
4. **Arithmetic self-check** separate từ independent human review
5. **Background execution + polling** — overcome shell/subagent timeout
6. **Protected file hashing** — verify source unchanged during execution

### ⚠️ What needs fixing in workflow:
1. **Config validation cross-check** — tournament vs sandbox config differs (25% entry, SL, cooldown)
   → Verify entrypoint config parity **trước** binding execution
2. **Result-file path collision** — controlled_exec result-file must differ from script output
   → Always use separate `--result-file data/.../controlled_exec.json`
3. **Shell reliability check** — shell tool offline → subagent-only with fork_worktree overhead
   → Pre-flight shell test before execution approval

### 🚨 What must never repeat:
1. **No post-hoc hypothesis change** — decision rule frozen before execution
2. **No threshold lowering** to force eligibility (min_trades: 30)
3. **No packet overwrite** — rename incomplete attempts
4. **No single-window conclusions** — always preregister multi-window

## Infrastructure reference

| Tool | Purpose | Constraint |
|---|---|---|
| `run_service_tournament.py` | 4-arm matrix (cash/bh/MA/ADX25) | config ≠ sandbox |
| `run_bnb_operational_sandbox.py` | Signal parity + admission check | no fills (kill switch) |
| `run_bnb_adx25_comparison.py` | **NEW**: actual fill comparison | 2 arms + 2 benchmarks |
| `controlled_exec.py` | Bounded execution wrapper | 120s timeout, 30s heartbeat |
| `process_registry.py` | Track running workers | poll every 30s min |

## Research decision framework (next hypothesis)

### Template:
```
Question: [specific economic question]
Primary metric: [paired delta, not single-arm return]
Hypothesis (preregister): [directional claim + bound]
Multi-window requirement: [≥3 windows across regimes]
Stop signal: [threshold to kill experiment early]
```

### Approved constraints (never violate):
- min_trades: 30 (contract)
- No threshold lowering / gate bypass
- Multi-window validation required (single window = descriptive only)
- Cost stress @2x minimum (PF >1.0)
- Independent human review before promotion
- No live/paper/broker/holdout expansion unprompted
