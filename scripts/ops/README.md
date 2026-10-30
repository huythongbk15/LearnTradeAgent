# Scheduling the Binance testnet runner on WSL

WSL has no systemd here and does not start `cron` by itself. The crontab kept
calling the runner while nothing ran it, from 2026-08-12 to 2026-10-29. These
scripts move the schedule to Windows Task Scheduler, which also wakes the WSL VM,
and add a health check that alerts from Windows.

| File | Runs where | What it does |
| --- | --- | --- |
| `run_binance_testnet_cycle.sh` | WSL, every 2 h | One `--execute` testnet cycle, unbuffered, into `logs/binance_testnet.log` |
| `check_binance_testnet_health.sh` | WSL | `scripts/check_live_audit.py`: exit 2 when execution runs stop arriving, fail, or die without a terminal event |
| `windows/check_health.ps1` | Windows, every 30 min | Runs the check through `wsl.exe`; raises a toast and logs to `%LOCALAPPDATA%\TradingAgent\health.log` on failure, including when WSL cannot be reached |
| `windows/register_tasks.ps1` | Windows, once | Registers both tasks (`-WhatIf` prints them only) |
| `daily_check.py` | WSL, daily | Read-only report: cycles, outcomes, ledger/lifecycle consistency, M1 and M3 progress |
| `authorize_operational_track.py` | WSL, by the operator | Signs, shows (`--show`) or revokes (`--revoke`) the testnet operational track |
| `workspace_server.py` | WSL, started by the operator | Read-only page at `http://127.0.0.1:8765`: what is being done, what was done, what exists ([WORKSPACE_UI.md](../../docs/operations/WORKSPACE_UI.md)) |
| `workspace_status.py` | WSL | The page's data as JSON: `doing`, `done` or `have` |

Before registering the tasks, comment out the runner line in the WSL crontab so
that a cron daemon started later cannot double-schedule it. Tasks run while the
user is logged on; a sleeping or logged-off machine misses cycles, and the next
health check reports them as stale.

## Operational (E2E) track

Nothing is promoted to testnet, so the runner locks entries. To exercise the
whole order path anyway, the operator signs a time-limited authorization. The
runner then drives the lifecycle exerciser instead of a strategy: a small BUY
when flat (at most $20), an exchange-native stop, a SELL after about 4 h, and
every third lifecycle a stop close to the price so that the venue closes it.
It is testnet only and is never research or promotion evidence.

```bash
.venv/bin/python scripts/ops/authorize_operational_track.py --approver NAME --reason "M3 E2E"
.venv/bin/python scripts/ops/authorize_operational_track.py --show
.venv/bin/python scripts/ops/authorize_operational_track.py --revoke
```

The authorization is bound to the account and to the driver source, and
expires (14 days by default). New entries stop on their own after a failed or
killed execution run, at the lifecycle cap, or past the daily loss limit; exits
continue. Signing again after a review resumes them.

Before signing, rehearse the track offline: the real runner over 40 simulated
2 h cycles against a local three-symbol venue, plus fault cases (a stop that
fills during the exit, a lost order response, a run killed before its stop, a
crash past the loss limit, a quote off the candle). No network IO.

```bash
.venv/bin/python -m pytest tests/test_operational_rehearsal.py -m "slow or not slow"
```
