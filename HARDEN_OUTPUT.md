# HARDEN — Final Output

## A. HEAD SHA used
- `86a2f19` — fix(P0): execution lifecycle safety hardening
- Previous: `63ee806` — feat: P1 research governance hardening + SLSA provenance restore

## B. Confirmed bugs fixed

| # | Bug | Fix |
|---|-----|-----|
| 1 | Kill switch blocks ALL orders including risk-reducing exits | `ExposureEffect` gate: block INCREASE, allow REDUCE |
| 2 | `PriceSource` returns bare float — no timestamp/freshness validation | `TrustedPrice` dataclass with `is_fresh()` validation |
| 3 | Sell inventory check only on first fill — cumulative oversell possible | Cumulative `filled_size + new_fill <= free_inventory` |
| 4 | Manual intervention marks order MANUAL but system can still resolve reconciliation | `manual_blocked` global flag; `resolve_reconciliation` rejects if unresolved manual issues |
| 5 | `append_batch` validates seq against DB max before batch insert — false rejection for same-aggregate batches | Local `expected_by_aggregate` state during batch validation |
| 6 | Fill without protective order silently accepted, no explicit gap state | `ProtectionState.PROTECTION_REQUIRED` + `ExecutionHealth.PROTECTION_GAP` |
| 7 | SLSA provenance attestation skipped (cosign v3 issue) | Restored with cosign v3.1.3 bundle format |

## C. Files changed

- `src/trading_agent/execution/lifecycle/lifecycle.py`
- `src/trading_agent/execution/lifecycle/store.py`
- `src/trading_agent/execution/lifecycle/__init__.py`
- `src/trading_agent/execution/chaos_invariants.py`
- `src/trading_agent/research/artifact.py`
- `src/trading_agent/research/lifecycle.py`
- `src/trading_agent/research/uncertainty.py`
- `src/trading_agent/research/__init__.py`
- `src/trading_agent/execution/simulator/reality_gap.py`
- `src/trading_agent/execution/simulator/calibration.py`
- `.github/workflows/ci.yml`
- `tests/test_execution_lifecycle.py`
- `tests/test_chaos_invariants.py`
- `tests/test_execution_simulator.py`
- `tests/test_research_governance.py`
- `tests/test_calibrated_decision.py`
- `tests/test_simulator_calibration.py`
- `scripts/demo_wave_ef.py`
- `scripts/review_wave_ef.py`

## D. Safety invariants added/strengthened

1. **No increased exposure while kill switch blocks entry** — now typed with `ExposureEffect`; reduce-only exits preserved
2. **No entry on stale/untrusted market data** — `TrustedPrice` enforces finite, positive, fresh timestamps; future timestamps rejected
3. **No cumulative sell beyond inventory** — enforced on every fill, not just first
4. **Manual unresolved state blocks new exposure** — global `manual_blocked` flag; reconciliation cannot resolve with pending manual issues
5. **No uncovered position** — explicit `PROTECTION_GAP` state blocks new exposure
6. **Event batch sequence remains gap-free** — local per-aggregate expected seq prevents false batch rejection
7. **Missing research evidence cannot pass promotion** — evidence bundle mandatory for risk-sensitive states
8. **Reality gap missing evidence fail-closed** — missing critical metrics = breach

## E. Tests added

- 8 new P0 regression tests in `tests/test_execution_lifecycle.py`
- 8 new tests in `tests/test_chaos_invariants.py` (price source adaptation)
- 27 research governance tests
- 6 simulator calibration tests
- 6 calibrated decision tests

Total: **720 passed, 3 skipped**

## F. Compatibility impacts

- `PriceSource` type changed from `Callable[[str], float | None]` to `Callable[[str], TrustedPrice | None]` — **breaking change** for any external callers constructing `ExecutionLifecycle` with a float-returning price source
- All internal callers updated
- Snapshot schema extended with new fields (`execution_health`, `protection_state`, `manual_blocked`, `unresolved_manual_intents`) — backward compatible (missing fields default to safe values on replay)
- `append_batch` behavior tightened: previously a valid interleaved batch could raise `SequenceGapError`; now correctly accepted

## G. Remaining known risks

| Item | Risk | Mitigation |
|------|------|-----------|
| P1.13 RiskDecision semantics | `max_position_size_pct` still exists; not yet replaced with `RiskDecision` | Low — existing tests pass, but API still confusing |
| P1.14 Config secret validation | ENV merge not yet implemented in `loader.py` | Telegram secrets currently work via existing env loading; verify if any path misses merge |
| P2.17 Unified order permission gate | Safety checks distributed across lifecycle + simulator + live adapter | Central gate deferred; current invariants hold but review recommended |
| P2.16 Simulator calibration | `CalibrationProfile` created but not yet populated from empirical data | Marked as heuristic/unvalidated per design |

## H. Operational actions still required

1. **Verify SLSA provenance on CI** — push to master and confirm `cosign attest --bundle` succeeds on GitHub Actions
2. **Backfill calibration data** — collect testnet/L2 observations to populate `CalibrationProfile`
3. **Unified permission gate** — implement P2.17 if live trading scope expands
4. **Config secret audit** — verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` load from ENV in all deployment modes

## I. Mainnet decision

**MAINNET: NO-GO**

Reason: P2 operational evidence (calibration from empirical data, unified permission gate, soak test results) not yet collected. Code and tests demonstrate correctness under specified invariants, but the prompt explicitly prohibits claiming production-ready solely from passing tests.
