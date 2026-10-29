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

Before registering the tasks, comment out the runner line in the WSL crontab so
that a cron daemon started later cannot double-schedule it. Tasks run while the
user is logged on; a sleeping or logged-off machine misses cycles, and the next
health check reports them as stale.
