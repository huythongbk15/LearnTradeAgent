# Scheduling the Binance testnet runner on WSL

WSL has no systemd here and does not start `cron` by itself. The crontab kept
calling the runner while nothing ran it, from 2026-08-12 to 2026-10-29. These
scripts move the schedule to Windows Task Scheduler, which also wakes the WSL VM,
and add a health check that alerts from Windows.

| File | Runs where | What it does |
| --- | --- | --- |
| `run_binance_testnet_cycle.sh` | WSL, every 2 h | One `--execute` testnet cycle, unbuffered, into `logs/binance_testnet.log` |
| `check_binance_testnet_health.sh` | WSL | `scripts/check_live_audit.py`: exit 2 when execution runs stop arriving, fail, or die without a terminal event |
| `windows/check_health.ps1` | Windows, every 30 min | Runs the check through `wsl.exe`; raises a toast and logs to `%LOCALAPPDATA%\TradingAgent\health.log` on failure, including when WSL cannot be reached. With `-Check`, `-Title`, `-LogName` and `-Always` it runs the daily check too |
| `windows/register_tasks.ps1` | Windows, once | Registers the tasks (`-Only Selection` registers one alone; `-WhatIf` prints them only) |
| `daily_check.py` | WSL, daily | Read-only report: cycles, outcomes, ledger/lifecycle consistency, M1 and M3 progress |
| `run_daily_check.sh` | WSL, daily at 08:10 local | `daily_check.py --report logs/daily_check/<UTC date>.txt`; its one line (first issue, M1, M3) is the day's toast, logged to `%LOCALAPPDATA%\TradingAgent\daily_check.log` |
| `run_selection_weekly.sh` | WSL, Monday 00:07 UTC | One paper selection run at nice 19 with a limit of 50 minutes, into `logs/selection_weekly.log`. It writes records under `data/selection` only and places no order ([SELECTION_PAPER_RUN.md](../../docs/operations/SELECTION_PAPER_RUN.md)) |
| `run_newperp_weekly.sh` | WSL, Monday 04:07 UTC | One NL-P paper log run at nice 19 with a limit of 25 minutes, into `logs/newperp_weekly.log`. It writes records under `data/newperp` only and places no order ([NEWPERP_PAPER_RUN.md](../../docs/operations/NEWPERP_PAPER_RUN.md)) |
| `run_flowbid.sh` | WSL, every 5 min | One forced selling paper log run at nice 19 with a limit of 4 minutes, into `logs/flowbid.log`; skipped from :50 of an even UTC hour to :06 of the odd hour and while a run is going. It writes records under `data/flowbid` only and places no order ([FLOWBID_PAPER_RUN.md](../../docs/operations/FLOWBID_PAPER_RUN.md)) |
| `run_perp_demo.sh` | WSL, hourly at :15 | The perp demo books on Binance USD-M Demo Trading: the pair book, LV, LEARN, FLOW02 and FLOW01 (`live_perp_demo.py --execute --shadow --lv --learn --flow02 --flow01`) at nice 19 with no timeout, into `logs/perp_demo.log`; skipped from :50 of an even UTC hour to :06 of the odd hour and while a run is going. Orders go to demo-fapi only; the shadow ledger on mainnet prices is paper ([PERP_DEMO_RUNNER.md](../../docs/operations/PERP_DEMO_RUNNER.md)) |
| `run_flow_flush.sh` | WSL, every 5 min from :02 | Book FLOW01's 5-minute mode (`live_perp_demo.py --execute --only flow01` with every book's flag) at nice 19 with no timeout, into `logs/flow_flush.log`; skipped from :50 of an even UTC hour to :06 of the odd hour and while either perp demo wrapper is going (shared `logs/.perp_demo.lock`). A quiet moment reads public data only and signs nothing. Registered only with `-Only FlowFlush` ([PERP_DEMO_RUNNER.md](../../docs/operations/PERP_DEMO_RUNNER.md)) |
| `run_perp_live.sh` | WSL as the live user (`-LiveUser`), every 5 min from :03 | Perp tier R0: book KRW alone on Binance USD-M **mainnet** (`live_perp_r0.py --execute --confirm-live ... --env-file ~/.perp_live.env`) from that user's own checkout, no nice and no timeout, into `logs/perp_live.log`; skipped while a run is going (`logs/.perp_live.lock`). Signs only when book KRW needs it, hourly while it may hold legs and daily while flat. Takes no arguments, so `--resume` is never scheduled. Registered only with `-Only PerpLive` ([PERP_LIVE_R0.md](../../docs/operations/PERP_LIVE_R0.md)) |
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
crash past the loss limit, fees in the base asset, a quote off the candle that
holds back one entry or exit). No network IO.

```bash
.venv/bin/python -m pytest tests/test_operational_rehearsal.py -m "slow or not slow"
```
