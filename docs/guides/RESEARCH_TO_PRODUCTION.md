# Research-to-Production Guide

> Status: **CURRENT workflow with TARGET gates marked** · Owner: research and operations
> · Verified: 2026-08-26

This guide is the end-to-end route for evaluating and promoting strategy logic.
It deliberately separates available tooling from stages that still require a
closed exit gate.

## 1. Establish a reproducible baseline

Before comparing strategies, prove that repeated runs with the same inputs agree.
Start with a single-pair connectivity smoke:

```bash
.venv/bin/python scripts/full_system_backtest.py \
  --fresh --symbol BTC/USDT --timeframe 1h --tail-bars 2000 \
  --allow-new-exposure \
  --state-dir data/backtests/baseline_a/state \
  --report-path data/backtests/baseline_a/report.json \
  --run-id baseline_a
```

This single report proves that the local path is connected; it is not the S0
multi-pair replay contract. For a golden replay, run the controlled multi-pair
batch twice. **Do not call this script with `--help`: it has no help-only mode and
any invocation starts the batch.** Because it is long-running, use the workspace
controller:

```bash
python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 14400 --heartbeat 30 \
  --result-file data/backtests/multi_pair_replay_a.control.json \
  -- .venv/bin/python scripts/multi_pair_1h_backtest.py
```

Repeat with a different control result filename. Each invocation creates a new
directory under `data/backtests/multi_pair_1h/`. Compare those two directories:

```bash
.venv/bin/python scripts/verify_golden_replay.py \
  --run-a data/backtests/multi_pair_1h/<RUN_A> \
  --run-b data/backtests/multi_pair_1h/<RUN_B>
```

The verifier expects this multi-pair run-directory contract. Do not point it at
two arbitrary single report files. Existing S0 evidence is recorded in
[`artifacts/golden/golden_replay_s0.json`](../../artifacts/golden/golden_replay_s0.json).

Acceptance:

- data/config identity is recorded;
- state directories are isolated;
- decisions, ledger and non-volatile metrics agree;
- no timestamp gap is hidden by the runner.

## 2. Register canonical strategies

Each candidate requires a `StrategyDescriptor` and an allowlisted registry entry.
The descriptor defines identity, warmup, required features and supported
parameters. Feature windows must contain closed bars only.

Current canonical candidates live under
`src/trading_agent/strategies/canonical/`. The registry must reject duplicates,
unknown IDs and descriptor/adapter identity mismatches.

Acceptance:

- deterministic outputs for identical observations;
- explicit `NO_TRADE`/flat behavior;
- insufficient history fails closed;
- legacy/canonical parity is demonstrated where parity is claimed;
- strategy parameters affect computation, not only artifact naming.

## 3. Run a smoke tournament

First list the cells without writing results:

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies enhanced_ma,rsi \
  --symbols BTC/USDT,ETH/USDT \
  --scenarios 1x \
  --tail-bars 2000 \
  --out data/backtests/tournament_smoke \
  --dry-run
```

Remove `--dry-run` to execute. The command reads local market data and writes
isolated cell state, reports and `tournament_index.json` below `--out`.

Acceptance:

- expected cell count equals strategy × symbol × scenario;
- every cell is `COMPLETED` or has a visible `FAILED` artifact;
- no exception silently drops a cell;
- report and artifact strategy identities agree;
- execution health is included in selection inputs.

## 4. Run the locked evaluation matrix

Only expand to the full matrix after the smoke matrix passes. Lock before launch:

- symbols and timeframe;
- evaluation window/data manifest;
- candidate registry version;
- parameter search space;
- normal and stressed cost scenarios;
- fault profiles;
- random seeds and concurrency policy;
- output root.

Do not edit the candidate list or scoring rule after seeing holdout results.

## 5. Perform statistical selection — TARGET gate

A completed tournament is not a selection policy. The selection layer must add:

- nested walk-forward or equivalent train/validation/test separation;
- minimum trade/effective sample requirements;
- uncertainty and multiple-testing correction;
- parameter stability and neighborhood checks;
- cost/fault robustness;
- incumbent comparison and explicit `no winner` outcome.

Until this gate has an immutable artifact and passing tests, selection remains
**TARGET** and tournament rankings must not be promoted automatically.

## 6. Publish policy and promotion evidence — TARGET gate

The policy binds eligible strategies to pair/regime conditions and risk limits.
Promotion is a separate operator-controlled transition. The runtime resolver must
verify both artifact integrity and environment eligibility.

Required controls:

- immutable/content-addressed artifact;
- dataset, code, config and evaluation binding;
- creator/reviewer identity;
- environment and promotion stage;
- expiry/revocation/rollback information;
- explicit abstain behavior.

## 7. Validate shadow, paper, testnet and canary

Advance one environment at a time:

```text
research evidence
  → shadow decisions (no orders)
  → internal paper execution
  → broker testnet/paper
  → constrained canary
  → production eligibility
```

At every boundary compare expected decisions, actual orders, fills, costs, rejects,
protective coverage and reconciliation. A development phase marked complete is not
a substitute for soak evidence.

## 8. Operate and roll back

Runtime selection changes must be explainable and reversible. Preserve the prior
eligible policy until the new policy passes its canary gates. Rollback should
change the selected policy/promotion, not mutate historical evidence.

Use [Main-flow Validation](../operations/MAIN_FLOW_VALIDATION.md) for checks and
[Live Trading Runbook](../LIVE_TRADING_RUNBOOK.md) for operational actions.
