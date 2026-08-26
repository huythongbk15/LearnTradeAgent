# Trading System Course V2 — Evidence-first Engineering

> Status: **CURRENT syllabus** · Owner: project maintainers · Verified: 2026-08-26

This course replaces the code-walkthrough material in top-level [`COURSE/`](../../COURSE/).
It teaches stable contracts and evidence flows instead of memorizing source line
numbers. The legacy course remains available only for historical context.

**Tiếng Việt:** [Khóa học thực hành đầy đủ — 12 bài, lab và capstone](../vi/khoa-hoc/README.md)

## Learning outcome

After completing the course, a learner should be able to explain and verify how a
market observation becomes either an authorized order or an explicit abstention,
and how every important decision is tied back to reproducible evidence.

## How to study each module

1. Read the concept/contract.
2. Locate the current implementation through `PROJECT_MAP.md`.
3. Run the smallest safe exercise.
4. Inspect the produced artifact, not only terminal output.
5. Trigger one failure path.
6. Answer the exit questions before moving on.

## Curriculum

| # | Module | Primary material | Practical outcome | Availability |
| --- | --- | --- | --- | --- |
| 1 | System invariants and authority | [Architecture](../ARCHITECTURE.md) | Draw the five planes and identify who may authorize exposure | **CURRENT** |
| 2 | Data trust and point-in-time features | [Research Methodology](../RESEARCH_METHODOLOGY.md) | Detect missing history, timestamp gaps and future leakage | **CURRENT** |
| 3 | Canonical strategy contract | [Strategy Lifecycle](../architecture/STRATEGY_LIFECYCLE.md) | Explain descriptor, registry, bridge and abstain behavior | **CURRENT** |
| 4 | Baseline and deterministic replay | [Main-flow Validation](../operations/MAIN_FLOW_VALIDATION.md) | Produce isolated reports and verify replay identity | **CURRENT** |
| 5 | Execution-aware backtesting | [Backtest Engine](../BACKTEST_ENGINE.md) | Separate alpha from fees, fills and execution failure | **CURRENT** |
| 6 | Strategy tournament | [Research-to-Production](../guides/RESEARCH_TO_PRODUCTION.md) | Reconcile a strategy × pair × scenario matrix | **CURRENT / hardening** |
| 7 | Nested WFO and statistical selection | [Adaptive Roadmap](../ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md) | Design a no-leakage selector with a valid “no winner” outcome | **TARGET until exit gate closes** |
| 8 | Evidence and promotion | [Evidence Artifacts](../reference/EVIDENCE_ARTIFACTS.md) | Trace evaluation → policy → promotion → runtime | **CURRENT foundation / TARGET policy** |
| 9 | Regime routing and safe switching | [Adaptive Roadmap](../ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md) | Explain posterior, hysteresis, incumbent and abstention | **TARGET until exit gate closes** |
| 10 | Shared-capital portfolio risk | [Production Policy](../PRODUCTION_POLICY.md) | Resolve strategy preference under portfolio constraints | **TARGET integration** |
| 11 | Execution lifecycle and protection | [Authority Chain](../AUTHORITY_CHAIN_OPS.md) | Trace intent, authorization, order, fill and protective ACK | **CURRENT foundation** |
| 12 | Shadow, canary and production operations | [Live Runbook](../LIVE_TRADING_RUNBOOK.md) | Build a staged validation and rollback decision | **CURRENT runbook; production remains gated** |

## Core lab — audit one tournament cell

### Prerequisites

- repository dependencies installed in `.venv`;
- local OHLCV data available for `BTC/USDT` at `1h`;
- no live broker credentials required.

### Preview

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies rsi --symbols BTC/USDT --scenarios 1x \
  --tail-bars 2000 --out data/backtests/course_v2_cell --dry-run
```

Expected: exactly one pending cell is listed and no report is written.

### Execute

Run the same command without `--dry-run`. It writes under
`data/backtests/course_v2_cell/`.

### Inspect

Confirm:

1. `tournament_index.json` contains exactly one accounted cell.
2. Cell strategy, pair, timeframe, params and cost scenario match the command.
3. `COMPLETED` has a report path and metrics; `FAILED` has reasons.
4. Missing data or an execution failure is not represented as zero return.
5. Re-running without `--rerun` does not silently replace completed evidence.

### Failure exercise

Use an unknown strategy ID in `--strategies`. The safe result is an explicit
failure or rejection, never substitution with another strategy.

## Exit questions

1. Why is the tournament index more trustworthy than copying a Sharpe value?
2. Which identities prove that parameters used for signals match the report?
3. Why can a completed tournament still be ineligible for promotion?
4. When should runtime keep the incumbent instead of switching?
5. Which evidence distinguishes a paper-safe strategy from a production-validated one?

## Course completion evidence

A learner submits:

- one validated smoke artifact;
- one explained failure artifact;
- an identity/lineage diagram;
- a review of cost and execution health;
- a written `PROMOTE`, `DO NOT PROMOTE` or `INSUFFICIENT EVIDENCE` decision.

The correct answer is allowed to be “insufficient evidence”. A course about trading
systems is incomplete if every exercise is designed to produce a winner.
