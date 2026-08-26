# Main-flow Validation Runbook

> Status: **CURRENT** · Owner: release/operator · Verified: 2026-08-26

Purpose: validate the critical research and execution path without turning a smoke
test into an unsafe production action.

## Safety envelope

- Commands below use local data and isolated output directories unless explicitly stated.
- They do not authorize mainnet trading.
- Use `.venv/bin/python` from the repository root.
- Run important or long commands through `scripts/qwenpaw_control/controlled_exec.py`
  so timeout, heartbeat and the final result are recorded.
- Do not reuse a live state directory for backtests.
- Stop on missing data, identity mismatch, schema failure or unexplained cell loss.

## Validation ladder

| Level | Scope | Expected duration | Release meaning |
| --- | --- | --- | --- |
| L0 | CLI/import and contract tests | minutes | Tooling is callable |
| L1 | One pair/one strategy smoke | minutes | Main local path is connected |
| L2 | Small tournament matrix | tens of minutes | Cross-strategy isolation and artifacts work |
| L3 | Full locked research matrix | hours | Research evidence candidate |
| L4 | Repeated replay + fault/stress suite | hours | Determinism and failure behavior evidenced |
| L5 | Shadow/paper/testnet soak | days/weeks | Operational evidence; separate approval required |

Passing a lower level does not imply a higher level.

## L0 — environment and contracts

```bash
.venv/bin/python scripts/run_strategy_tournament.py --help
.venv/bin/python scripts/full_system_backtest.py --help
.venv/bin/python scripts/verify_golden_replay.py --help
```

Targeted tests:

```bash
.venv/bin/python -m pytest \
  tests/test_backtest_report_v2.py \
  tests/strategies/test_s1_exit_gate.py \
  tests/backtest/test_tournament.py
```

Pass condition: zero failures and no import fallback to a different environment.

## L1 — single-cell smoke

Preview the cell:

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies rsi --symbols BTC/USDT --scenarios 1x \
  --tail-bars 2000 --out data/backtests/validation_l1 --dry-run
```

Execute by removing `--dry-run`. Pass condition:

- exactly one cell is accounted for;
- the cell has a report and evaluation artifact;
- status is `COMPLETED`, otherwise the failure is explicit;
- strategy, symbol, timeframe, parameter and scenario identity agree;
- report schema validates.

## L2 — small cross-strategy matrix

```bash
.venv/bin/python scripts/run_strategy_tournament.py \
  --strategies enhanced_ma,rsi,bbands \
  --symbols BTC/USDT,ETH/USDT \
  --scenarios 1x,slip_stress \
  --tail-bars 3000 \
  --out data/backtests/validation_l2
```

Expected inventory: `3 × 2 × 2 = 12` cells. Inspect
`data/backtests/validation_l2/tournament_index.json` and reconcile completed plus
failed cells to 12. Any missing cell is a run failure.

## L3 — full locked matrix

Before execution, save the exact command and identities in a release/research
record. Do not rely on changing defaults. At minimum lock strategies, symbols,
scenarios, parameter sets, timeframe, data manifest and output directory.

Pass condition is not “best Sharpe”. It is:

- complete matrix accounting;
- stable identities and valid reports;
- sufficient sample size;
- stress/fault behavior recorded;
- no selection performed on holdout leakage;
- results reviewed against an incumbent and an abstain option.

## L4 — deterministic replay and faults

Run the same locked matrix twice into separate output roots. Compare the generated
decisions, ledgers and metrics within documented tolerances. Use
`verify_golden_replay.py` for run directories that implement its multi-pair
contract.

Fault validation must cover, where implemented:

- stale/gapped market data;
- partial fills;
- rejection bursts;
- cancel/fill races;
- protective-order outage;
- abnormal fee/slippage/impact.

Pass condition: failure is contained, visible and does not create unauthorized
new exposure.

## L5 — operational validation

Follow [Live Trading Runbook](../LIVE_TRADING_RUNBOOK.md) and
[Operational Drills](../OPERATIONAL_DRILLS.md). Require explicit environment
approval, soak duration, reconciliation evidence and rollback readiness. Local
backtests do not close this level.

## Result record

Record for every validation:

```text
release/commit:
environment:
command/config identity:
data manifest:
output root:
expected/observed cell count:
completed/failed/missing:
schema validation:
determinism result:
known warnings:
reviewer:
decision: PASS | FAIL | CONDITIONAL
```

## Failure policy

- Missing cell: `FAIL`.
- Unknown strategy or artifact mismatch: `FAIL`.
- Missing/invalid data manifest: `FAIL`.
- Metric unavailable: do not coerce to zero; mark unavailable or fail the gate.
- One promising strategy with incomplete matrix: no promotion.
- Test passes only in isolation but not in the suite: investigate shared state;
  do not waive it as flaky without evidence.
