# Operable testnet system: plan, gates and incident log

Status: CURRENT
Owner: operator + agent
Verified: 2026-10-29
Evidence: `scripts/ops/daily_check.py`, `data/execution/binance_live_audit.jsonl`, `logs/binance_testnet.log`

This plan is the single source of truth for the main flow. It covers the Binance
testnet runner, which runs on a schedule, is observable and executes correctly,
before anything is expanded. Mainnet stays **NO-GO**. Nothing here is a claim of
alpha.

## Milestones

| Gate | Pass criteria (measured by `scripts/ops/daily_check.py`) | Status |
| --- | --- | --- |
| **M1 Stable schedule** | Over ≥ 48 h from the M1 start: ≥ 24 execution cycles; each has `run_completed`; 0 `run_failed`/`run_interrupted`; no gap > 2 h 15 min between execution starts; every cycle < 60 s; health check green; no manual intervention | Clock starts with the first clean headless cycle |
| **M2 Observable** | `status` command + one overview page answer within 10 s: what runs, which stage, why blocked, where the evidence is | After M1 holds for ~1–2 days |
| **M3 Order lifecycle** | ≥ 10 complete buy → fill → stop → exit cycles on testnet; venue = ledger = lifecycle; 0 stuck reservations | **Blocked:** nothing is promoted to testnet (see decision below) |
| **M4 L3 soak** | 30 days, 100 lifecycles, 0 unexplained failures (`docs/LIVE_TRADING_TODO.md`) | After M3 |

**Open decision for M3.** BUY admission requires a runtime promoted to
`TESTNET_ELIGIBLE`, and the promotion ladder requires `RESEARCH_VALIDATED`
first. No strategy has passed it. There are two ways forward. The first is a
testnet-only "operational soak" track with explicit operator approval, small
size, no research credit and no promotion beyond testnet. The second is to wait
for research. Until then the runner locks entries and still manages exits.

## Loops

1. **Every cycle (automatic).** Windows Task Scheduler runs
   `scripts/ops/run_binance_testnet_cycle.sh` every 2 h at :01 local and the
   health check every 30 min (`scripts/ops/windows/`). A Windows toast is raised
   on failure or silence.
2. **Daily.** Run `.venv/bin/python scripts/ops/daily_check.py`. It reports
   expected vs actual cycles, outcomes and durations, ledger/lifecycle
   consistency and M1 progress, and exits non-zero on any issue.
3. **Weekly.** Compare against the gates, decide continue/fix, and update this file.

Rules: fix only what breaks the current gate's criteria. CI, refactors and new
features wait until after M2. Every incident gets one line below.

## Incident log

| Date (UTC) | Incident | Root cause | Fix |
| --- | --- | --- | --- |
| 2026-08-12 → 10-29 | Runner silent for ~2.5 months | WSL cron daemon never started; crontab called a missing `python` | Windows Task Scheduler + health check from Windows |
| 2026-10-29 | Dry-run blocked: account/state mismatch | Identity scheme changed (API key → `LIVE_ACCOUNT_ID`); testnet balances reset by Binance | New epoch; old state archived (`backups/ops_restore_20261029/`) |
| 2026-10-29 | SOL intent stuck in `manual_intervention` | Operator session killed the run between reserve and submit | `scripts/resolve_unsubmitted_order.py` (operator recovery with venue evidence) |
| 2026-10-29 | ~5–6 min per order; quote-deviation risk | `ExchangeConfig.rate_limit=1200` passed as ccxt ms-per-request | ccxt per-exchange calibration (get_account 4.8 s → 0.2 s) |
| 2026-10-29 | Every buy-signal cycle ended `run_failed` | No promoted testnet runtime → FATAL | Testnet locks entries, still manages exits |
| 2026-10-29 17:01 | First scheduled cycle killed in seconds, no trace | Interactive wsl.exe console closed (0xC000013A) | Headless tasks, wrapper signal trap, runner handles SIGHUP |
