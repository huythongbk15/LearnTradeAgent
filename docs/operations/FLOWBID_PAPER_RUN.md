# Forced selling paper log: resting bids in a market-wide crash

A record, every 5 minutes, of what the frozen crash-bid rule would have done. It writes records only, under `data/flowbid/`: no order, no key, no private endpoint, nothing the testnet runner reads. Research description, not advice. Mainnet stays NO-GO.

## Why this log exists

On 2026-11-03 a frozen replay of the rule on 2020 to 2026 passed (packet `data/design_review_20261103_01/forced-flow-sleeve/measure/`, git-ignored). Sharpe .88, against BTC .89 at the same volatility.

A walk-forward on the replay (`data/flowbid_wf_20261103/wf.out`) showed:

- **The effect repeats:** in most years, bids in a crash earned more than they cost.
- **The best depth and hold do not repeat:** the rank correlation of the nine cells, past years against the next, was -.55 to +.10.

What a replay cannot show is how fast a crash is seen and whether bids placed after it is seen still fill. This log measures that, forward, on the frozen rule.

## Rule, frozen

The constants are in `tools/flowbid/config.py`. A change needs the operator's ruling on this page.

- **Flag, past only:**
  - A coin is down when the lowest traded price of the trailing 60 minutes is at least 10% under its last traded close before that window. That close must be at most 24 h old.
  - The market flags when at least 15% of at least 15 counted coins are down.
  - An event lasts 24 h; flags inside it are ignored.
- **Bids:**
  - Three bids per coin: 15, 20 and 30% under the last traded close before 00:00 UTC of the flag date. That close must be at most 48 h old.
  - A bid is placed only if the last price is above it.
  - Bids go live 2 minutes after the later of the flag minute and the minute the run saw it, and stay live 24 h.
  - A bid fills on the first traded minute whose low reaches the bid less .5%.
- **Exit:** the last traded close 24, 72 and 168 h after the fill minute. No stop, unlevered spot.
- **Universe:** the 182 list coins of the study's spot 1m grid, less the coins masked by option B and one name that is not ASCII. A coin the exchange no longer has drops out.

## Read, once

On 2028-11-13, with books RS and LV:

**Decision rule** (written 2026-11-04 before any look; merging this page is the operator's ruling):
- **Statistic:** the primary below. Events are the cluster unit.
- **KEEP:** mean > 0 with t ≥ 2, and above the comparator 'buy the equal-weight basket of the flagged coins at the flag's first live minute, same holds'. The comparator is computed at the read from public bars.
- **KILL:** the one-sided 95% upper bound is below 0.
- **Otherwise UNRESOLVED.**
- **Sample size:** about 4 to 13 events a year are expected, so with fewer than 20 events the read can only KILL or stay UNRESOLVED.

- **Primary:** mean net bps per event over all nine depth and hold cells (`PRIMARY_CELLS`), at 148 bps a round trip. Equal weight across the filled coins of an event; events with at least one fill.
  - The operator chose the nine-cell mean on 2026-11-03 in place of the single cell k .20, H 72, because of the walk-forward above.
- **Replay figures for comparison** (event weighted, 2021 to 2026):
  - nine-cell mean +555.7 bps per event;
  - +405.5 when an event with no fill in a cell counts zero for that cell.
- **Described alongside:**
  - each cell;
  - the delay from flag to live bids;
  - the share of bids already through at placement;
  - fills per event;
  - sleeve return per bid placed.

The status file `data/flowbid/status.json` shows counts only, never a price change or a return. Do not compute returns from `data/flowbid/ledger.jsonl` before the read.

## What a run records

`python -m tools.flowbid.run`, every 5 minutes, through `scripts/ops/run_flowbid.sh`:

1. **Judge** every closed minute since the last covered one, at most 900 minutes back, so a skipped run is filled in later.
   - If fewer than 80% of the live coins could be fetched, the minutes are not judged and stay open.
2. **Event:** on a new flag, write the event and one bid record per coin and depth (or a count of why not).
3. **Settle** within 80 calls; anything left waits for the next run:
   - a fill or a cancel when a bid's 24 h end;
   - an exit at each hold.

The ledger `data/flowbid/ledger.jsonl` is append only.

## Scheduling and the testnet cycle

- No run starts from :50 of an even UTC hour to :06 of the odd hour; the testnet cycle starts at :01.
- A run is stopped after 4 minutes, so a run that started at :49 is over by :54.
- A run is skipped while the previous one is still going.
- Runs go at nice 19, fetching 4 coins at a time.
- A call took about .85 s here on 2026-11-03, mostly waiting on the network. Measured run times are in the rehearsal below.

## Network

- One public host, `data-api.binance.vision`, paths `/api/v3/klines` and `/api/v3/exchangeInfo`, through `tools/selection/fetch.py` and its allowlist.
- Symbols of masked coins are refused before a call.

## Operator steps

1. Merge, then fast forward the scheduled checkout outside the testnet window.
2. Register the task from Windows PowerShell. Add `-WhatIf` first to see the start time.

   ```
   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1 -Only FlowBid
   ```

**Rehearsal on a past crash** (the run covers the 900 minutes before `--now`):

```
FLOWBID_ROOT=/tmp/fb PYTHONPATH=src:. .venv/bin/python -m tools.flowbid.run --now 2025-10-10T22:00:00
```

Measured on 2026-11-03, 169 coins fetched, four workers:

| Run | Calls | Time |
|---|---|---|
| The run that saw the crash, with 900 minutes judged | 475 | 92 s |
| A run 5 minutes later | 169 | 28 s |
| Catch-up runs after a day and after six days with no run, settling fills and exits | 386–387 | 123–125 s |

The crash flagged at 20:59Z (54 of 137 counted coins down) and placed 81 bids. With one worker the first run took 368 s, over the 4 minute limit, which is why the fetch runs on four.

## Limits

- Fills come from 1m lows, not the queue: a bid at the low of a minute may not have filled.
- Depth at the bid is unknown.
- The universe is coins that once reached the list, so survivors are favoured.
- A crash shorter than the gap between two runs is still judged, but the bids go live later than in the replay; the log records that delay.
