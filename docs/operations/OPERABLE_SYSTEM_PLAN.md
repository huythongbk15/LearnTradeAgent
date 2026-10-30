# Operable testnet system: plan, gates and incident log

Status: CURRENT
Owner: operator + agent
Verified: 2026-10-29
Evidence: `scripts/ops/daily_check.py`, `data/execution/binance_live_audit.jsonl`, `logs/binance_testnet.log`

This plan is the single source of truth for the main flow. **The end goal is
one complete end-to-end run of the real system on testnet**, repeatable on a
schedule: data → signal → decision and risk → order → fill → exchange-native
stop → exit → reconciliation (venue = ledger = lifecycle) → report. Run it;
where it breaks, find the root cause and fix it there; run again, until it runs
clean. Mainnet stays **NO-GO**. Nothing here is a claim of alpha.

Whether a strategy fits (has an edge) is a separate research track (WFO
validation first). It never blocks this flow, and this flow never produces
research or promotion evidence. A strategy that passes the existing ladder up
to `TESTNET_ELIGIBLE` replaces the exerciser below; it then runs its own soak.

## Milestones

| Gate | Pass criteria (measured by `scripts/ops/daily_check.py`) | Status |
| --- | --- | --- |
| **M1 Stable schedule** | Over ≥ 48 h from the M1 start: ≥ 24 execution cycles; each has `run_completed`; 0 `run_failed`/`run_interrupted`; no gap > 2 h 15 min between execution starts; every cycle < 60 s; health check green; no manual intervention | **Running since 2026-10-29 17:41Z** (first clean headless cycle, 18 s) |
| **M2 Observable** | `status` command + one overview page answer within 10 s: what runs, which stage, why blocked, where the evidence is | **Built, read-only** ([WORKSPACE_UI.md](WORKSPACE_UI.md)): `scripts/ops/workspace_status.py` and the page of `scripts/ops/workspace_server.py`. Passes once the operator has used it through a real failure |
| **M3 E2E order lifecycle** | ≥ 10 complete lifecycles of the operational track, of which ≥ 3 exit by the venue stop and ≥ 3 by a sell; venue = ledger = lifecycle; 0 stuck reservations; 0 manual intervention | **Built and rehearsed offline, not enabled.** The rehearsal fixes are in the scheduled checkout; the operator signs after M1 passes (`scripts/ops/authorize_operational_track.py`) |
| **M4 L3 soak** | 30 days, 100 lifecycles, 0 unexplained failures (`docs/LIVE_TRADING_TODO.md`) | After M3 |

**M3 decision (2026-10-29): a testnet operational track.** BUY admission needs a
runtime promoted to `TESTNET_ELIGIBLE`, which needs `RESEARCH_VALIDATED`, which
no strategy has. And natural Enhanced MA signals give about 1 lifecycle a week
(4 trades in 68 days on BTC 1h with the live 10/60 parameters), too slow for
M3 and far too slow for M4. So the operator signs a time-limited authorization
(`src/trading_agent/execution/operational_authorization.py`) and the runner
drives a deterministic lifecycle exerciser
(`src/trading_agent/execution/operational_driver.py`) through the unchanged
pipeline: DecisionAuthority → allocator → ExposureAuthority → OrderPlanner →
permission → lifecycle, ledger and reservations → stops → reconciliation.

- Size: at most $20 per order, at most one position per symbol, a lifecycle
  cap (120), a $10 daily loss limit, 14-day expiry.
- Lifecycle: BUY when flat, exchange-native stop, SELL after 230 min; every
  third lifecycle gets a stop 0.3 % below the price so the venue closes it.
- Evidence stays honestly `UNKNOWN`. The permission gate admits UNKNOWN (never
  MISSING or STALE) only with an active testnet waiver from the authorization.
- New entries stop on their own after any failed or killed execution run since
  the authorization (or when the audit log cannot prove otherwise), at the cap
  or past the loss limit; exits continue. Re-signing after a review resumes.
- A position worth less than the venue minimum notional counts as flat.
- Never research or promotion evidence; the driver is not in the strategy
  registry; mainnet refuses the waiver.

**Offline rehearsal (2026-10-30, `tests/test_operational_rehearsal.py`).** The
real runner entrypoint over 40 simulated 2 h cycles against a local venue with
BTC, SOL and AVAX, moving prices, Binance-like filters and resting stops: 45
lifecycles closed (26 by the track's sell, 19 by the venue stop, 0 by anything
else), every cycle `run_completed`, venue = ledger = lifecycle after every
cycle. Fault cases: a stop that fills while the exit cancels it is booked and the
exit skipped; a lost BUY response is reconciled, the run fails and entries stay
blocked while exits finish; a run killed between a fill and its stop is
protected on the next cycle; a −30 % gap fills every stop and the loss limit
stops entries; a quote 1.5 % off the candle fails the run before any order.
The rehearsal found the first four incidents dated 2026-10-30 below. With the
code the scheduler ran before these fixes, the first M3 cycle would have failed
at its second BUY.

Known risks, to be confirmed or cleared by the run itself:
- The daily loss limit reads whole-account equity (the testnet account is
  otherwise all cash).
- Trading fees: cleared for testnet. The account reports every commission as
  0 (maker, taker, buyer, seller; standard and tax) for BTC, SOL and AVAX
  (2026-10-30). Mainnet charges spot fees in the base asset; the dust that
  leaves is handled since 2026-10-30 (incident below) and rehearsed.
- A quote more than 1 % off the last closed candle fails the run before any
  order, and the track then stays blocked until re-signed. At :01 that candle
  closed a minute earlier, so only a late cycle in a fast market is exposed.

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
| 2026-10-29 (E2E tests) | Producer SELL of a protected position blocked: `SELL_QUANTITY_EXCEEDS_AVAILABLE_INVENTORY` | OrderPlanner lattice `n * step` in binary exceeded the inventory (19801 × 1e-05 = 0.19801000000000002) | Lattice points rounded to the step's decimals |
| 2026-10-29 (E2E tests) | A stop the venue filled stayed open in the canonical lifecycle | Orphan-stop cleanup cleared local state but never booked the fill | Cleanup books the fill and records `protective_stop_filled` |
| 2026-10-29 (review) | Any canonical AVAX order would crash the whole batch | AVAX/USDT, a default runner symbol, had no instrument rules | Rules from Binance testnet filters; test that every default symbol has rules |
| 2026-10-30 (rehearsal) | The second BUY of a batch blocked with `protection_gap`; after any BUY, every later BUY would be too | The runner placed venue stops but never gave the lifecycle protective evidence, so a protected fill stayed `PROTECTION_REQUIRED` | `ensure_protective_stop` acknowledges the protection in the lifecycle with broker or reconciliation evidence |
| 2026-10-30 (rehearsal) | A position bought below the 48 h high was sold one cycle later (`ATR trail breached`) | The live ATR trail peaked at the 48-bar window high, bars before the entry included; the backtest trails from the entry | The trail peak counts only bars that closed after its last observation, which starts at the entry fill |
| 2026-10-30 (rehearsal) | An exit failed the run when its stop filled during the cancel; the stop intent stayed `cancel_requested` | A failed cancel was fatal even when the venue had already closed the position | The exit books the stop fill and is not sent |
| 2026-10-30 (rehearsal) | `daily_check` reported resting stops and the reconciliation aggregate as open lifecycle intents | Every non-terminal lifecycle aggregate counted | Stops the ledger holds as active, protection records and `reconciliation_resolved` are expected |
| 2026-10-30 (rehearsal) | With fees in the base asset, exits stopped for every symbol and later runs failed | Unsellable dust kept a breached software trail, whose forced exit skipped every other decision each cycle; a venue stop that filled but left dust was never booked, so the next BUY inherited its breached stop | Dust below the venue minimum notional has no trail and needs no stop, and orphan-stop cleanup treats it as no position (a stop still resting over it is kept) |
