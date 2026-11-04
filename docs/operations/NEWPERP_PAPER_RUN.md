# NL-P paper log: short new perps against BTC

A weekly record of what rule NL-P would have done, with the order book it would have met. It writes records only, under `data/newperp/`: no order, no key, no private endpoint, nothing the testnet runner reads. Research description, not advice. Mainnet stays NO-GO.

## Why this log exists

On 2026-11-03 the operator froze rule NL-P and read it once on 455 USDT perps that never reached the project's coin lists, 2020-01 to 2026-10 (packet `data/nonlist_20261103_01/`, git-ignored). The verdict was CONSISTENT. A blind rebuild corrected it slightly, to +140.1 bps a week on weeks with a trade, t 3.26.

The result is fragile:

- With each name capped at .05 weight it falls to t 1.8.
- From 2024 on it is t 1.2.
- Most of the effect sits in the first week after listing.

What history cannot show is the cost of trading a perp in its first weeks: the order book, and fills near the stop. This log measures that, forward, on the frozen rule.

## Rule NL-P, frozen

- **Stamp:** Monday 00:00 UTC.
- **New:** a USDT perpetual on a crypto underlying (`underlyingType` COIN), trading, listed (`onboardDate`) in the 28 days before the stamp.
- **Two books** (operator's ruling of 2026-11-03): perps whose `underlyingSubType` carries Alpha (Binance Alpha tokens) go to book `alpha`, all others to book `core`.
- **Positions, per book:** short every new perp of the book at equal weight, .5 in total, against that book's own BTC perp long of .5. No new perp: the book is flat.
- **Entry and exit:** at the 1h open 4 h after the stamp, exit at the open 7 days later.
- **Stop:** at 1.5x entry, read from 1m bars.
- **Paper book:** 10,000 USD.

The rule's constants are in `tools/newperp/config.py`. A change needs the operator's ruling on this page.

## What a run records

`python -m tools.newperp.run`, Monday at 04:07 UTC, through `scripts/ops/run_newperp_weekly.sh`:

1. **Settle** each leg whose week ended:
   - exit open;
   - the first 1m bar at or past the stop, with fills F1 = max(trigger, open), F2 = max(F1, minute VWAP), F3 = the minute's high;
   - funding;
   - the exit book, if the run is within an hour of the exit bar.
2. **Open** this week's legs: entry open, and a depth-100 snapshot with the cost of a market order of the paper size, of 1,000 USD and of 10,000 USD against the mid, plus the spread.

The ledger `data/newperp/ledger.jsonl` is append only. `data/newperp/status.json` shows counts and entry book cost per book only, never a price change or a return. Do not compute returns from the ledger before the read.

## Why two books

A post hoc split of the frozen read by today's exchangeInfo tags, pair return per symbol-week (short the perp, long BTC):

- Not tagged Alpha: +396 bps, n 986, positive every year, 2026 +126.
- Tagged Alpha (from 2024): -177 mean, +327 median, n 527, negative every year (2024 -515, 2025 -115, 2026 -248), driven by squeezes.

So the fade after 2024 is the Alpha share growing. The split was found after reading, and the tags are today's, so it is a hypothesis: the core book is its first clean test, and the alpha book records the other side.

## Read, once

At the first Monday with 60 settled short symbol-weeks in the core book; the alpha book is read at the same date:

- **Primary:** mean weekly net in bps, with the measured entry and exit book cost and fill F2, funding paid.
- **Bounds:** the same with F3, and at twice the measured book cost.
- **Comparison:** the frozen read's +140.1, and its 2024-on figure of +57.7.

**Listing pace sets the date** (COIN perps still trading, by listing month):

- 2025: 9 to 31 a month.
- 2026: 15 in January, then 1 to 8 a month (October: 1).

**Corrected pace (2026-11-04):** the earlier estimate of 8 to 9 months was too optimistic.
- Only 4 non-Alpha COIN perps were listed in May to October 2026, about 2.7 core symbol-weeks a month.
- At that pace, 60 core symbol-weeks take about 22 months, so the read is expected around late 2028. That is an estimate, not a promise.

**Decision rule** (written 2026-11-04 before any look; merging this page is the operator's ruling):
- **Statistic:** the mean net per settled core symbol-week. The t and the one-sided 95% upper bound come from a bootstrap clustered by listing, with symbol-weeks of one listing kept together.
- **Futility look at 30 settled symbol-weeks:** KILL if the 95% upper bound is below 0. Otherwise keep recording, and look at nothing else.
- **Read at 60:**
  - KEEP if the mean is above 0 with t ≥ 2;
  - KILL if the upper bound is below +30 bps a symbol-week, which is about half of the hindsight-free +57.7;
  - otherwise UNRESOLVED, which continues to a final read at 120, where it is KEEP if t ≥ 2 and closed otherwise.
- **Meaning:** KEEP opens a discussion of small capital and grants no order path by itself. KILL closes the listing fade. The alpha book is description only.

## Network

- One public host, `fapi.binance.com`.
- Four paths: `exchangeInfo`, `klines`, `depth`, `fundingRate`.
- Allowlisted in `tools/newperp/fetch.py`, the same way as `tools/selection/fetch.py`.
- Symbols of the coins masked by option B are refused before a call.

## Operator steps

1. Merge, then fast forward the scheduled checkout outside the testnet window.
2. Register the task from Windows PowerShell:

   ```
   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1 -Only NewPerp
   ```

   Add `-WhatIf` first to see the time. The first stamp is 2026-11-09.

**Rehearsal:**

```
NEWPERP_ROOT=/tmp/np PYTHONPATH=src:. .venv/bin/python -m tools.newperp.run --as-of 2026-10-12T04:07:00
```

A replay records no order book.

## Limits

- The book is a snapshot a few minutes after the entry bar opens, not the queue at that open.
- Fills near the stop come from 1m bars, not the book.
- Borrow is not a factor on a perp, but the first hours after a listing can carry wider limits than the snapshot shows.
- A coin renamed into a new perp counts as new here; the read flags known renames.
