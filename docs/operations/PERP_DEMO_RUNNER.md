# New perp pair book on Binance USD-M Demo Trading

The first perp order path of the core, on Binance Demo Trading only. Research description, not advice. Mainnet stays NO-GO, and nothing here grants a market right.

The same Demo Trading futures account also carries book LV (from 2026-11-04) and, from 2026-11-05, book LEARN (H-LEARN-11), book FLOW02 (H-FLOW-BTC-02) and book FLOW01 (H-FLOW-BTC-01). They share one order path and one set of account caps: see "Books on one account" and "Shared risk caps".

## Why
On 2026-11-03 the operator ruled to open USD-M perpetuals as a second market type of the core, next to spot, so that the measured new perp short can be run as a real order path.

The research behind the book is git-ignored:
- `data/deep_20261103_01`: event-time short, +195 bps a perp over 16 cells. After the hindsight fix, +130 for all new perps from 2022-11.
- `data/critique_20261103`: the attacks on that result.
- `data/fills_20261103`: entry 4 h after listing costs a median of 10 to 14 bps a side up to $10k.

None of it is validated forward. The NL-P paper log (`docs/operations/NEWPERP_PAPER_RUN.md`) stays the evidence for the strategy.

## Venue
- **Binance Demo Trading:** `demo-fapi.binance.com`, keys from demo.binance.com.
- **Why not the old futures testnet:** ccxt 4.5.90 refuses private calls on `testnet.binancefuture.com` and points to Demo Trading.
- **Refused hosts:** `PerpDemoVenue.assert_demo_only` refuses to run unless every futures API root is demo-fapi, so mainnet and the old testnet are refused before anything is signed.
- **Listings:** on 2026-11-03 the demo venue listed 604 symbols, but only 1 of the 20 mainnet USDT perps listed in the previous 180 days.
  - Real listings will rarely be tradable there.
  - The venue mainly proves the mechanics, and `--rehearse SYMBOL` opens one pair on an existing demo symbol for that.

## Book, frozen
Constants are in `src/trading_agent/execution/perp/rules.py`. A change needs the operator's ruling on this page.

- **Candidates:** USDT perpetuals, trading, `underlyingType` COIN, not tagged Alpha, from the public mainnet exchangeInfo.
- **Entry:** short at listing + 4 h with a market order, if a run sees it within 2 h of that time and the symbol trades on demo.
  - A listing past its window, or not on demo, is recorded as skipped and never retried.
- **Hedge:** a long BTCUSDT perp of the short's filled notional.
- **Stop:** an exchange-side STOP_MARKET, reduce-only, at 1.5x the short's average fill.
  - It is a Binance algo order; ccxt routes it.
  - When it fires, the next run closes the hedge.
- **Exit:** both legs reduce-only at market 168 h after entry, after the stop is cancelled.
- **Size:**
  - `--pair-usd` per leg, default 100 USDT, at most 1,000. The hourly wrapper passes 500 (operator ruling, 2026-11-04), option C of a 10k account at 1x.
  - At most 10 open pairs.
  - **Capacity:** set by the shared risk caps below, not by MAX_OPEN.
    - An entry adds two legs of gross and one alt short. After it, the whole account, every book included, must stay under both caps, or the entry is skipped.
    - On a 10k account that holds nothing else, the 40% alt-short cap allows 8 pairs of 500 a leg. Beside the other books it allows fewer.
    - Until 2026-11-05 this check counted only the pair book's own pairs, not book LV's legs. The shared caps close that gap (operator ruling, 2026-11-05).
  - Cross margin at 1x, one-way position mode required.

## Safety chain
- **Gates:** `require_execution_authorization` (TRADING_MODE=testnet, TRADING_EXECUTION_ENABLED, TRADING_KILL_SWITCH). `TRADING_ENTRY_KILL_SWITCH` stops entries but not exits.
- **Single writer:** one writer at a time, through `LiveExecutionLock` on `data/perp_demo/writer.lock`. The hourly and 5-minute wrappers also share one lock file (`logs/.perp_demo.lock`).
- **Signed order ledger:** every order intent is reserved in a signed ledger (`LiveRiskStateStore`, `order_state.json`) before it is sent, with the same client key Binance receives.
  - An outcome that is not known stops the run.
  - The next run settles it by that key first.
- **Signed stores:** pairs live in a signed store (`pairs.json`, `LIVE_SAFETY_HMAC_KEY`). A changed file is refused. Each other book has its own signed store beside it: `lv_blocks.json`, `learn_book.json`, `flow02.json` and `flow01.json`.
- **Position check:**
  - Each run checks venue positions against every book's record before any entry.
  - Any position the book cannot explain stops the run.
  - The operator decides what to do with it.
- **Failed open:** if anything fails once the short was sent, the run takes off at market the short the venue holds on that symbol, and the hedge if it filled, then stops.
- **Exit cut off:** each try of an exit order gets a fresh client key (`…-sx0`, `…-sx1` for the buy-back, `…-hx0` for the hedge sell). Before a try, the run reads the order ledger for what the pair's earlier tries filled.
  - A buy-back that filled before a lost reply or a killed run goes straight to the hedge at the next run, recorded `hold`.
  - A hedge sell that filled is not sent again. The pair is recorded closed at the ledger's price.
  - A venue short smaller than the book's by exactly what those tries bought back is explained.
  - A run that dies after the stop's cancel and before the buy-back leaves holds a short without a stop until the next run buys it back.
- **Fill price:** a market order can come back filled with an average price of 0, as on Demo Trading on 2026-11-03. The run then reads the order back by its key before sizing the hedge and the stop.
- **Audit:** every event goes to `data/perp_demo/audit.jsonl`. `status.json` holds counts only.
- **Hedge size:** the hedge is sized to the nearest amount step, at least one step. Truncating left the first $100 rehearsal hedge 6% short.
- **Write boundary:** orders leave only through `execution/perp/venue.py`, listed in `tests/test_direct_broker_write_guard.py`.

## Operator steps
1. Create an API key on Binance Demo Trading (demo.binance.com, API management) and enable futures on it. Keep the account in one-way position mode. Then add the key to `.env` yourself.
   - **Self-generated (Ed25519), preferred:**
     - Make the key pair outside the repository and paste only the public key into Binance:
       ```
       mkdir -p ~/.binance_demo && chmod 700 ~/.binance_demo
       openssl genpkey -algorithm ed25519 -out ~/.binance_demo/ed25519_private.pem
       chmod 600 ~/.binance_demo/ed25519_private.pem
       openssl pkey -in ~/.binance_demo/ed25519_private.pem -pubout -out ~/.binance_demo/ed25519_public.pem
       ```
     - Set `BINANCE_DEMO_FUTURES_API_KEY` and `BINANCE_DEMO_FUTURES_PRIVATE_KEY_FILE` (the private key's path).
     - The run refuses a file readable by anyone but its owner.
   - **System-generated (HMAC):** set `BINANCE_DEMO_FUTURES_API_KEY` and `BINANCE_DEMO_FUTURES_API_SECRET`.
2. Read only, nothing signed:
   ```
   PYTHONPATH=src:. .venv/bin/python scripts/live_perp_demo.py
   ```
3. Rehearse the mechanics once, with a 2 h hold:
   ```
   TRADING_MODE=testnet TRADING_EXECUTION_ENABLED=true PYTHONPATH=src:. .venv/bin/python scripts/live_perp_demo.py --execute --rehearse ETHUSDT
   ```
4. Run `--execute` again after 2 h: the pair closes.
5. Register the hourly task from Windows PowerShell, after a fast forward of the checkout. Add `-WhatIf` first to see the start time.
   ```
   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1 -Only PerpDemo
   ```
6. **Books LEARN, FLOW02 and FLOW01 (2026-11-05).** The hourly wrapper runs them from the first hourly run after the fast forward that brings this page. The task itself does not change.
   - **Book LEARN:** nothing to do. It reads the bar store and quarter models that the H-LEARN-11 paper run keeps in `data/learn_paper/`.
   - **Book FLOW01:** nothing to do for the hourly run. Its 5-minute task is step 7.
   - **Book FLOW02:** seed its data store once, after the fast forward and before the next 01:00 UTC window. Until then each day waits and is recorded missed (`missing:store`).
     - On 2026-11-05 the research packets are only in the worktree where the book was built. Copy them into this checkout's `data/` (git-ignored) before that worktree is removed. From the repository root:
       ```
       W=/home/huythong/.qwenpaw/workspaces/trading/.claude/worktrees/doc-sau-phan-tich-du-an-cc1551/data
       cp -a $W/flow_btc_20261104_01 $W/flow_btc_20261104_02 $W/perp_funding_20261102 data/
       nice -n 19 .venv/bin/python -I tools/flowbtc/run.py --init-from data/flow_btc_20261104_01/panel --funding data/perp_funding_20261102/parsed/BTC.parquet
       nice -n 19 .venv/bin/python -I tools/flowbtc/run.py --parity
       nice -n 19 .venv/bin/python -I tools/flowbtc/run.py --update
       ```
     - `--parity` must print `"n": 1758` and `"net": 3.778035761550993`. `--init-from` refuses to reseed an existing store.
     - **Deadline:** the seeded metrics end 2026-11-02 23:55, and Binance keeps about 30 days of them. Run `--update`, or let the book run, before 2026-12-01, or that gap stays.
     - **Clock:** run these outside :50 of an even UTC hour to :06 of the next, and outside :15 to :20. The hourly run writes the same files.
     - **Lock:** `--init-from` and `--update` take the wrappers' lock, `logs/.perp_demo.lock`. If a run holds it, they print that the lock is held, write nothing and exit 3. Run the command again after that run ends. While they run, the hourly and 5-minute runs skip.
7. **Register book FLOW01's 5-minute task** from Windows PowerShell, after step 6. Add `-WhatIf` first to see the start time.
   ```
   powershell -ExecutionPolicy Bypass -File \\wsl.localhost\Ubuntu\home\huythong\.qwenpaw\workspaces\trading\scripts\ops\windows\register_tasks.ps1 -Only FlowFlush
   ```
   - This task is never in the script's default list. Running the script without `-Only` does not register it, so it starts only when you name it.
   - Until it is registered, only the hourly run trades FLOW01, and most events are recorded stale (see "Book FLOW01"). On the research panel, that hourly-only entry is the negative row of FLOW01's timing table.

## Schedule
- **Hourly:** `scripts/ops/run_perp_demo.sh` runs `live_perp_demo.py --execute --shadow --lv --learn --flow02 --flow01 --pair-usd 500`, then `live_spot_hold_demo.py --execute` (book HOLD), then the H-LEARN-11 paper log. It runs every hour at :15 local, at nice 19, and logs to `logs/perp_demo.log`.
  - `OMP_NUM_THREADS=4` is set for the perp run: once a quarter, book LEARN fits the quarter's model in it.
- **Every 5 minutes:** `scripts/ops/run_flow_flush.sh` runs `live_perp_demo.py --execute --only flow01`, with the same book flags and sizes as the hourly run. It runs at :02, :07, :12, ... local, at nice 19, and logs to `logs/flow_flush.log`. See "Book FLOW01".
- No run of either wrapper starts from :50 of an even UTC hour to :06 of the odd hour.
- **One lock:** both wrappers take `logs/.perp_demo.lock`. A run is skipped while a run of either wrapper is going.
  - The hourly run holds the lock to its end, so the 5-minute runs during it are skipped.
  - A 5-minute run still going at :15 makes that hourly run skip. The quiet pre-check, run through the runner's own functions on public data, took about 2 s with warm caches and 12 s from a cold start (one run each, 2026-11-05).
  - The windows of the listings, LV, LEARN and FLOW02 are 2 h, so each holds two hourly runs. One skipped hourly run delays a step by an hour but misses nothing.
- The wrappers set no timeout, so they never cut an `--execute` run between an order and its record. The next run reconciles first.
  - Task Scheduler has its own limit: `register_tasks.ps1` gives every task `-ExecutionTimeLimit` 1 h, the PerpDemo and FlowFlush tasks included. A run still going after an hour can be stopped there. The next run then reconciles first, as after any killed run.
- `TRADING_MODE=testnet` is set by the wrappers. The other switches and the demo key come from `.env`, as for the spot runner.
- A listing's entry window is 2 h, so an hourly run sees every listing.

## Shadow ledger on mainnet prices
The demo venue lists almost none of the new mainnet perps, so `--shadow` records the same frozen rule for every due listing on public mainnet data. This is paper only, through the NL-P allowlisted client on `fapi.binance.com` (`tools/newperp/shadow.py`).
- **Open:** the first minute a run sees the listing inside its window, at the 1m open. The delay from listing + 4 h is recorded. A BTC 1m open is recorded the same minute.
- **Stop:** the first 1m bar whose high reaches 1.5x entry. The fill ratios F1 (max of trigger and open), F2 (minute VWAP) and F3 (minute high) are recorded.
- **Hold:** the 1m open at entry + 168 h, for the perp and for BTC.
- **Funding:** the funding sums of both legs over the hold.
- **Files:**
  - events go to `data/perp_demo/shadow_ledger.jsonl` (append only);
  - state goes to `shadow.json`;
  - `status.json` shows counts only.
- **Read** (ruled by merging this page, 2026-11-04; it replaces the proposal below where they differ). At about 8 qualifying listings a year, 30 closed pairs take about 4 years. KEEP needs t ≥ 2 from a bootstrap by listing month. KILL if the one-sided 95% upper bound of the mean net is below 0. Otherwise UNRESOLVED.
- **Earlier proposal:**
  - once, at 30 closed shadow pairs;
  - primary: the mean net bps per pair (BTC return minus perp return, minus 74 + 8 bps, plus funding), stop at F2;
  - pass: positive with t >= 2 from a bootstrap by listing month.
  - Do not compute returns from the ledger before the read.

## Book LV on the same account
On 2026-11-04 the operator asked for book LV to trade on Demo Trading as well. Book LV is the frozen low volatility paper book of `docs/operations/SELECTION_PAPER_RUN.md`. The paper book stays as it is and keeps its own read. The demo book measures what paper cannot: real fills and funding on perps, the exchange stop on the short leg, and a weekly multi-leg roll.

Code is in `src/trading_agent/execution/perp/lowvol.py`. The hourly run trades it with `--lv`.

- **Pick:** read from `data/selection/lv_book/picks.jsonl`, which the weekly selection run writes on Monday at 00:07 UTC. The first is 2026-11-09.
  - A masked coin in a pick stops the run before any order.
  - Weeks before the first pick are not counted as missed.
- **Names:** each coin maps to its demo USDT perp, including rebased units such as `1000PEPEUSDT`.
  - A coin without a demo perp is skipped and recorded. On the last stored list, 16 of 18 trade on demo; OPN and XAUT do not.
  - A coin another book holds is skipped too (`held_by_<book>`), and so is BTCUSDT.
  - So is a coin whose symbol still carries a stop that a failed LV block could not cancel (`stale_stop`, see "A stop the venue still lists after the cancel"), until a run cancels that stop.
- **Roll:** at the first run from Monday 04:00 UTC, within 2 h.
  - **Size:** `--lv-usd`, both legs together, default 1,000 USDT, at most 5,000.
  - **Legs:** half a side, equal weight over the names it trades.
  - **Trades:** only the difference to the targets. Reductions go first and are reduce-only, and a side flip goes through flat. A difference below the venue minimum is skipped.
  - **Caps:** the roll must fit the shared risk caps, with LV's current legs counted as gone, or the week is missed (`equity` or `alt_short_cap`).
- **Stop:** each short gets an exchange STOP_MARKET, reduce-only, at 1.5x its mark price at the roll.
  - This differs from paper, where the stop fills at the first 4 h open at or above 1.5x entry. Real money would use the exchange stop.
  - A short whose stop fired stays flat until the next roll.
- **End of a block:** the next roll. Without one, both legs close reduce-only at Monday 06:00 UTC, when the next window ends with no pick, and the week is recorded missed.
  - Under `TRADING_ENTRY_KILL_SWITCH` there is no roll, but the block still closes.
  - Funding on the block's symbols is recorded at its close.
- **Shared account:**
  - One process runs both books, with one order ledger and one reconcile.
  - **Position checks:** book LV checks its own legs first: a fired stop, a quantity that differs, or a short without its stop.
  - **The pair book:** its check then counts LV's legs as explained, so anything else is still refused.
  - **No shared symbols:** no book enters a symbol another book holds ("Books on one account"). Book LEARN also leaves this week's pick alone, so the Monday roll finds those coins free.
- **Failures:**
  - **A roll that fails:** it takes off everything the venue holds on the block's symbols, cancels its stops, and marks the block failed. The week is not retried.
    - The new block takes the current legs over in its first save, before any cancel or order, and the old block's legs are marked `rolled`. So a roll that fails at the first cancel of an old short's stop still records every flatten fill on the new block's legs.
    - **Order:** the flatten buys back the symbols the venue holds short first, then sells the rest. A roll cancels the current shorts' stops before anything else, so these shorts are the ones without a stop.
    - **Buy-back before cancel:** on each symbol the flatten lists the LV stops, then reads the position. A short is bought back, reduce-only at market, before any of its stops is cancelled. The stops are cancelled only once that order has filled in full. This is the order of the pair book's failed entry (`Book.enter`).
      - A position read that fails, or a buy-back that fails, loses its reply or fills only in part, leaves every stop of that symbol at the venue. They keep protecting what is left, and a reduce-only stop larger than the position cannot open anything. The error is recorded, and the next run's flatten lists and cancels them.
      - A short with no stop listed whose own position read fails is bought back by the quantity of the read of all positions that set the order, but only when the record has no open stop on that symbol that the venue took: this block's stop or the previous block's, with its send answered (`acknowledged` or `open`). That is the case when the roll had cancelled the old stop and the new one was never sent or its send raised. Nothing but the flatten moves that symbol then, and it would otherwise wait an hour without a stop. A stop the venue took that it no longer lists may have fired since that first read, and the venue rejects a reduce-only buy on a flat symbol, so such a symbol waits for the next run, which reads the venue again.
      - A long carries no LV stop of its own: any LV stop listed on it is cancelled, then the long is sold, reduce-only. An error while those stops are cancelled or listed again is recorded and does not hold up the sale. On a flat symbol the listed stops are cancelled.
      - Before 2026-11-05 the flatten cancelled a symbol's stops before its buy-back. Two failing position steps in one flatten (position reads, buy-backs, or thin Demo books that fill each buy-back in part) then left every such short without a stop.
      - **Not checked on Demo:** no run has yet sent a reduce-only market buy while a reduce-only STOP_MARKET buy was open on the same symbol. The pair book's hold-end exit and LV's close cancel the stop first. The pair book's failed entry buys back first, but it has a stop open then only when the stop's reply was lost, and no record in the code or docs shows that case. If Demo refused the buy-back while the stop is open, the short would stay under its stop, and the refused order, recorded with an unknown outcome, would stop every book for operator review at the next reconcile.
    - **One symbol at a time:** an error on one symbol (a read, an order whose reply is lost, an order filled in part or filled without a price) does not stop the others. A cancel is not such an error: the adapter answers False when the venue refuses it or the call fails, and the list read after it decides. After every symbol was tried, the run raises the first error and the block stays `rolling` (`perp_lv_flatten_unfinished`). The next run's check records what the venue filled and takes off what it still holds.
    - **Run length:** each flatten order can wait up to 15 s for its fill and 15 s more for its price (`FILL_WAIT_S`). Since the flatten tries every symbol, a Demo venue that answers every order without a price adds about 30 s a symbol: a flatten over 14 symbols takes about 7 minutes. The run holds `logs/.perp_demo.lock` all that time, and neither wrapper has a timeout. The 5-minute FLOW01 runs skip while the lock is held. Before 2026-11-05 the flatten stopped at the first such error and left the later shorts without a stop.
    - **Stops:** once a short's buy-back has filled in full (at once on a long or a flat symbol), the flatten cancels every LV stop the venue listed on the symbol, then reads the list again. A stop the venue no longer lists has its intent closed:
      - cancelled, when this flatten's cancel took it off;
      - filled, when that cancel did not take it off (it was already gone, or the venue refused the cancel) while the venue was flat at the flatten's position read and the leg still holds a short after the fills of the block's own orders on that symbol are added. These are fills the leg does not hold yet, such as a buy-back whose reply was lost; their filled quantities are counted, with no price read. An own order whose outcome is still unknown rules this case out. The flatten takes the stop as fired: the leg is marked `stopped`, as the check records a fired stop, and nothing is sent on that symbol. This is read from the venue's list and position, not from a fill report of the stop. The leg is saved `stopped` before the intent is closed: a run cut off between the two leaves the intent open, and the next flatten closes it as filled;
      - rejected, for the block's own stop when its send raised (the venue never acknowledged it) and no earlier flatten pass found it listed (`stale_stops`);
      - otherwise cancelled: an old short's stop (the roll cancelled it and its read-back was cut off), or a stop of this block that the venue took and no longer lists.
    - **A stop the venue still lists after the cancel:** its intent stays open and the block lists it (`stale_stops`, audit `perp_lv_stop_not_cancelled`). The position is still taken off.
      - On a failed block, each later run's check tries the cancel again and closes the intent once the venue no longer lists the stop. A cancel that still fails does not stop the run.
      - On a block still `rolling` (another symbol had an error), the next run's flatten lists and cancels that symbol's stops again in any case, and rebuilds the list.
      - While a failed block lists the stop, the next LV roll leaves that symbol out (`stale_stop`), so no LV short sits under it.
      - **Known limit, not handled in code:** while the stop is listed, it is a reduce-only buy at the venue that no other book counts. A failed block holds nothing (LV's `held()` is empty), so the symbol is free for the other books. Another book that shorts alts (book LEARN) can short it, and the stale stop then sits under that short as a second stop. If it triggers, it buys that book's short back in part or in full: that book's next check would find the venue differs from its record and stop for operator review, and LV's next check records the stale stop's intent as cancelled. No change inside `lowvol.py` alone covers this: `held()` reports quantities the pair book's checks use, and book LEARN reads only the pick file (`lowvol.pick_symbols`), not LV's record.
  - **A roll killed midway:** it is left as `rolling`, and the next run flattens it the same way after the reconcile.
  - **A close, cut or flatten left unfinished:** a run can stop mid-order (killed, a lost reply, a failed read), and a thin Demo book can fill part of a market order and then expire or cancel the rest. After the reconcile, the next run's check records each fill of the block's orders that no leg holds yet, from the order ledger, as a fill row, and moves the leg's quantity by it. A leg those fills bring to zero, with the venue flat, is marked closed. For a short, its stop's intent is closed as cancelled; a cancel cut off before its read-back left that intent open. Then:
    - **A short whose close cancelled its stop** (the close is marked on the leg before the cancel): when the venue holds exactly the leg's rest, the check buys it back, reduce-only, with a fresh key (`perp_lv_close_finished`).
    - **Any other leg the close did not finish** (a long, or a short the close had not reached, its stop still open): the leg matches the venue again. At the hold end the same run's close sends the rest, reduce-only, with a fresh key. After the close of a roll that missed its week (too few names, or the caps), the next run's step rolls from the rest or closes it.
    - **A roll's cut or close:** the same run flattens the roll and marks the block failed, with the filled part and each flatten fill recorded on the legs. If an order's reply was lost, the block is marked `unsettled` and the next run records its fill.
    - **A fill that cannot be priced:** a price read never holds up a flatten. The flatten records the ledger's fills only after the positions are off. A fill with no price in the ledger, at the venue or from its quote cost, and an unreadable mark, is left out and audited (`perp_lv_fill_unpriced`). The failed block stays `unsettled` until a run can price it, and a run that still cannot goes on. On the live block the same failure stops the run with nothing sent, because its legs must hold every fill before they are compared with the venue.
    - **A flatten order filled in part, or filled without a price:** the flatten goes on with the other symbols, then the block stays `rolling`. The next run records that part and takes off what the venue still holds.
  - **Stopped for operator review, with nothing sent:** a short without its stop whose close was never started, or whose venue quantity differs from the leg's rest after those fills (`short has no open stop`); any leg whose venue quantity differs from the book after them (`differs from book`); an order the reconcile cannot find at the venue, or finds still open; a short gone at the venue while its stop is still open, including one whose recorded fills bring it to zero. Known ways to reach these cases:
    - **A stop that triggers just as the hold-end close cancels it:** the venue answers the cancel with no such order and no longer lists the stop, so its intent is closed as cancelled although it filled. The reduce-only buy-back is then rejected, because the venue is already flat, and the order path records it with an unknown outcome. The next run's reconcile cannot find that order at the venue and stops every book for operator review (`intent ... is not at the venue`). Nothing is left unprotected, but no book exits or enters until the operator acts.
    - **A stop that triggers between a flatten's read of the position and its buy-back** (known limit, not handled in code): the buy-back is rejected the same way, since the venue is already flat, and the next run's reconcile stops every book for operator review. The stop's intent stays open until then. Before 2026-11-05 the flatten cancelled the stop first and read the position after, so this case read as a fired stop.
    - **A stop whose send raised although the venue took it, and that triggers after the flatten's first read of all positions and before it lists that symbol's stops, while the symbol's own position read then fails** (known limit, not handled in code): the record cannot tell that stop from one the venue never took, so the flatten buys the short back by its first read. The venue is already flat and rejects the buy-back, and the next run's reconcile stops every book for operator review. Nothing is left unprotected. It takes a lost stop reply, a stop that fires within a few calls, and a failed read in one flatten. A stop whose send was answered does not reach this case: the flatten leaves that symbol to the next run, which records the stop as fired.
    - **A stop that triggers between the run's check and the roll's cancel of it** (known limit, not handled in code): the roll finds the stop gone and closes its intent as cancelled although it filled. The roll then trades from a leg the venue no longer holds.
      - A smaller short, or a flip to long: the reduce-only buy is rejected and recorded with an unknown outcome. The flatten sends nothing on that symbol, and the next run's reconcile stops every book as above.
      - A larger short: only the difference opens, under a stop sized for the whole target. The next check finds the venue differs from the leg (`differs from book`).
      - The same size: no trade, and a new stop sits on a flat symbol. The next check finds the short gone with its stop open.
      - Every path ends in operator review with nothing left unprotected. The record shows that stop cancelled. These paths are read from the code; no test covers them.
    - **A run killed after an intent is saved `submitted` but before its order reaches the venue:** the next run's reconcile stops every book for operator review before LV's check runs. If the run was rolling, the block stays `rolling`, and the shorts whose stops the roll had already cancelled stay at the venue without a stop until the operator acts.
- **Record:** `data/perp_demo/lv_blocks.json`, signed like `pairs.json`. `status.json` shows counts only.
  - Its fills and funding are returns of the LV forward read. Do not compute them before that read (the 2028-11-13 run).
  - The demo account's own P&L view shows them too.
- **Rehearsal:** this rolls a hand-given pick now, with a 2 h hold, to test the mechanics before 2026-11-09. Use coins from the list, never masked ones.
  ```
  TRADING_MODE=testnet TRADING_EXECUTION_ENABLED=true PYTHONPATH=src:. .venv/bin/python scripts/live_perp_demo.py --execute --lv --lv-rehearse "LTC,XLM:SUI,WLD"
  ```
  - A second rehearsal before the hold ends rolls into the first, which tests the netting.
  - A rehearsal is refused while a weekly block is live.
- **Left for the operator** (from the 2026-11-05 review of the flatten; each needs a ruling or a change outside `lowvol.py`):
  - **Check the buy-back under an open stop on Demo** (see "Not checked on Demo" above): open a small short with its reduce-only STOP_MARKET buy, then buy the short back reduce-only at market while the stop is still open, and confirm that Demo fills it. The runner has no step that does this on purpose; it could become a step of the next LV rehearsal, before the first weekly roll on 2026-11-09. If Demo refuses that order, every failed roll with a short would leave the short under its stop and stop every book at the next reconcile until the operator acts.
  - **A reduce-only order the venue rejects is recorded with an unknown outcome** (`Book._send` in `book.py`, shared by every book). The next reconcile cannot find it at the venue and stops every book (`intent ... is not at the venue`). This is how each case above where a stop fires just before a reduce-only buy-back ends. Recording a definite venue rejection as rejected would let the next run go on; that is a change to the shared order path, not to `lowvol.py`.
  - **A stale LV stop under a book LEARN short** (the known limit under "A stop the venue still lists after the cancel"): covering it needs book LEARN, or the shared position checks, to read LV's `stale_stops`.

## Books on one account
On 2026-11-05 the operator decided to put three more books on the same Demo Trading futures account: H-LEARN-11 (book LEARN), H-FLOW-BTC-02 (book FLOW02) and H-FLOW-BTC-01 (book FLOW01). The pair book and book LV are senior. All run at 1x, on Demo Trading only. The kill rules were written in the triage register before the books start (`data/triage_20261104_01/REGISTER.md`, section 5, rows 4 to 6). Research description, not advice. Mainnet stays NO-GO.

- **One process:** one order ledger, one reconcile, one writer lock. Orders still leave only through `execution/perp/venue.py`.
- **Flags:** `--lv`, `--learn`, `--flow02` and `--flow01` add each book to a run. Each book has its own signed store under `data/perp_demo/`. With `--execute` the stores are verified. Without it they are read unverified, and no store is written.
- **Order of a run:**
  1. reconcile unfinished orders by client key;
  2. each book checks its own legs, in the order LV, LEARN, FLOW02, FLOW01;
  3. the pair book checks the venue against every book;
  4. pair exits;
  5. each book steps: exits and reductions always, additions only without the entry lock;
  6. pair entries.
- **A book whose step raises ends the run.** The books after it in the order do not step in that run, and no pair entry runs. Their exits and sells wait for a run that gets past it.
  - So a fault that repeats in one book holds back the books after it. Examples: a halted LEARN whose close the venue expires on every run, or a LEARN model or data fault, which is retried at each run of its window. FLOW02's sells and its 01:15 call, and FLOW01's hourly exits, then wait.
  - FLOW02 keeps its own data and model faults inside the book. LEARN does not.
  - FLOW01's 5-minute mode steps FLOW01 alone, so its exits still run there once that task is registered.
  - Isolating the steps from each other is a change of this rule. It needs the operator's ruling.
- **A run of one book** (`--only flow01`, book FLOW01's 5-minute mode): steps 1 to 3 as above, then FLOW01's step alone. No pair entry, no scheduled pair exit and no other book's step. The checks of steps 2 and 3 still act as in every run: they close the hedge of a pair whose stop fired, and they flatten an LV roll that was cut off.
- **Symbols other than BTCUSDT are exclusive:**
  - A book never enters a symbol another book holds. It skips it and records `held_by_<book>`.
  - Two books holding the same symbol stop the run for operator review.
- **BTCUSDT is shared by long legs only:** the pair hedges, book FLOW02 and book FLOW01.
  - Each book records its own quantity.
  - The venue's BTCUSDT must equal the sum, or the run stops.
  - Every BTC exit is a reduce-only sell of the book's own quantity.
  - A negative BTC quantity in any book stops the run.
- **Client keys:** each book has its own prefix: `pp-` (pair book), `lv` (LV), `lrn-` (LEARN), `fb2-` (FLOW02) and `fb1-` (FLOW01). Every key is unique in the order ledger and at most 36 characters.
- **Halts:** books LEARN, FLOW02 and FLOW01 each halt on their own drawdown rule (sections below).
  - A halted book closes its legs reduce-only, trades nothing more and records `halted`. Reports show only `halted` or `ok`, and `new` (book LEARN, before its first step).
  - Resuming is the operator's call: once the book is flat, move its store aside. The next run starts a fresh one.
  - Never move a store aside while its book holds a leg: the position check would then refuse that leg and stop every run.
  - **A new size is a new book:** each halt measures the size stored at the book's first run. With a different `--learn-usd`, `--flow02-usd` or `--flow01-usd`, the book adds nothing and records `size_changed`; exits still run. Move its store aside, once it is flat, to start at the new size.
- **A fill the ledger holds without a price:** Demo Trading can answer a filled market order with average 0 (seen 2026-11-03). When a book settles such a fill from the order ledger, it reads the order back by its key, else takes its quote cost over its quantity, else the mark at send. Book LEARN keeps no mark per order, so it takes the mark when it settles, and its row's `mark` is empty.
  - The fill row then carries `avg_from` (`venue`, `quote_cost` or `mark`). Book LV's row is a list (key, quantity, average): it carries the source as a fourth field, and the leg carries `exit_from` when that row closed it.
  - A cost read leaves the `quote_cost` and `mark` rows out.
- **Exact closes** send the leg's quantity rounded to 10 decimals. Summed fills drift (0.3 − 0.2 = 0.09999999999999998), and ccxt truncates an amount to the step, so an unrounded close would leave one step at the venue.
  - Books FLOW02 and FLOW01 round their BTC quantity the same way after each fill. A partial sell of 0.010 from 0.013 then leaves 0.003, not 0.0029999999999999996, which ccxt would send as 0.002.
- **Code:** `execution/perp/book.py` (the run and the contract), `risk.py` (the caps), `legs.py` (trading toward a target), and the books `lowvol.py`, `learnbook.py`, `flowbtc.py` and `flowflush.py`.

## Shared risk caps
Operator ruling, 2026-11-05. Code: `src/trading_agent/execution/perp/risk.py`. A change of a cap needs the operator's ruling on this page.

Both caps hold across all books. They are measured on the venue's positions at mark price, not on what each book thinks it holds.

- **Gross, 90% (`EQUITY_USE`):** after any trade that adds exposure, the account's gross notional stays at or under 90% of equity. Gross notional is the sum over positions of quantity times mark.
- **Alt shorts, 40% (`ALT_SHORT_CAP`):** after any trade that adds a short on a symbol other than BTCUSDT, the account's short notional on those symbols stays at or under 40% of equity. This bounds the short-alt group: the pair book's shorts, book LV's shorts and book LEARN's shorts.
- **Exits and reductions** are never blocked by a cap.
- **Order:** a book makes its reductions first, then reads the room left, then adds.
- **When a cap binds:**
  - **Pair book:** skips the entry and records `equity` or `alt_short_cap`. Later runs inside the listing's 2 h window try again.
  - **Book LV:** misses the week, recorded `equity` or `alt_short_cap`. The roll counts LV's current legs as gone, so the check sees the roll's net effect.
  - **Book LEARN:** scales every adding leg down by the same factor, so the additions fit, and records the day `capped`.
  - **Books FLOW02 and FLOW01:** skip the entry and record `equity`. FLOW02 does not try again that day; FLOW01 does not try that event again. A BTC long adds no alt short.
- **What it replaces:** the pair book's check counted only its own pairs, and book LV's counted LV plus the pairs. Neither counted everything on the account. The shared caps close that gap.
- **Worked example** (arithmetic at the default sizes on a 10,000 USDT account, not a measurement):
  - LV 1,000, LEARN up to 2,000, FLOW02 1,000 and FLOW01 1,000 use up to 5,000 of the 9,000 gross. That leaves room for 4 pairs of 500 a leg.
  - LV's shorts are 500. If LEARN is short all 20 coins, its shorts are 2,000. The alt shorts are then 2,500 of the 4,000 allowed, so 3 pairs fit.
- **Limits:**
  - The pair book counts an entry at its nominal size. Its hedge is rounded to the nearest BTC step, so it can exceed that by up to one step.
  - A short whose price rises counts more at mark. The caps then block new additions, but they never force a reduction.

## Book LEARN on the same account
On 2026-11-05 the operator ruled to trade the H-LEARN-11 daily decisions on Demo Trading as well (triage register, section 5, row 5). The paper book of `docs/operations/LEARN_PAPER_RUN.md` stays as it is and keeps its own looks. The demo book measures what paper cannot: real fills and fees on up to 20 perps a day, the daily netting, and the account caps.

Code is in `src/trading_agent/execution/perp/learnbook.py`. It imports the paper's own functions (`tools/learnpaper`) and changes none of them. The hourly run trades it with `--learn`.

- **Rule:** the paper's.
  - The hourly bar opening at 23:00 UTC decides the next day.
  - The quarter's model predicts each coin. Long above +20 bps, short below -20 bps, else flat.
- **Bars:** each decision first appends closed hourly bars to the paper's bar store (`data/learn_paper/bars/`, set by `--learn-root`), through the NL-P allowlisted public client. The demo never writes the paper's ledger.
- **Model:** the paper's quarter model, loaded through `model_for` with its recorded hash.
  - When the quarter's model does not exist yet, the demo fits and records it through the same function, at the 00:15 run of the quarter's second day. It does so only when every coin's bars reach the quarter start: then every label the fit uses has closed, and the paper's later fit would be the same. Until then the day waits (`model_wait`).
  - **Checked on 2026-11-05, on a copy of the paper's 2026Q4 data:** the training rows the demo would see at the quarter's second day 00:15 equal the rows the paper sees later: 845,544 of 845,544, with X and y equal. With bars that end before the quarter start, 20 rows are missing; that case waits.
  - **Checked the same day, on five October decision bars:** predictions from bars that end at the decision bar equal the paper's predictions from later bars, bit for bit (100 of 100).
  - **Not covered by that check:** a coin whose 23:00 bar is missing at the 00:15 run but arrives by the paper's 01:15 run. The features rank across the coins present, so every coin's prediction then differs from the paper's. The day is still traded. The day record lists the coins without the bar (`missing`), so the read can compare it with the paper's record for that day and leave such days out.
- **Timing:**
  - The first run in [00:00, 02:00) UTC trades the day.
  - **Deviation:** the paper enters at the 00:00 open. The hourly run at :15 enters about 15 minutes later, at market.
  - The position is held until the next day's decision. Each day trades only the difference to the new targets.
  - **Missed day:** a day whose window ends without a decision closes every leg, reduce-only, at the first run after 02:00. The day is recorded missed with its reason: no run, a failed bar fetch, a model not ready, a size change, or an interrupted run.
- **Size:** `--learn-usd`, default 2,000 USDT, at most 4,000. A larger value is refused when the flags are read.
  - Each coin gets learn_usd / 20, whatever coins are present, at the mark price at send, rounded down to the venue step.
  - A coin without its 23:00 bar is flat, and recorded (`missing_bar`).
  - **A new size is a new book:** with a different `--learn-usd` the book decides nothing (`size_changed`) and goes flat at the window end. Move `learn_book.json` aside to start at the new size.
- **Trades:** reductions first, reduce-only. A flip goes through flat. Additions last. A difference below the venue minimum is skipped and recorded.
- **Shared account:**
  - **Exclusive symbols:** a coin is not traded when another book holds it (`held_by_<book>`), when it is in this week's LV pick (`lv_pick`), or when it has no demo perp (`not_on_demo`). Each skip is recorded.
  - **The Monday roll:** a coin the book holds that enters the new LV pick is closed at the Monday decision, so LV's 04:00 roll finds it free. The selection run writes the pick at 00:07 UTC. If it is not there yet at the 00:15 decision, LV skips that coin for the week (`held_by_learn`).
  - **Caps:** additions must fit both account caps. When they do not, every adding leg is scaled down by the same factor and the day is recorded `capped`. Reductions are never blocked.
  - **Position check:** before the pair book's check, book LEARN checks its own legs against the venue. A quantity that differs stops the run.
  - **Entry lock:** under `TRADING_ENTRY_KILL_SWITCH` the day is still decided, but only reductions run. The additions held back are recorded. A missed day still closes.
- **Halt:** the register's demo rule (section 5, row 5), run automatically.
  - Each hourly run values the book from its own fills: learn_usd, plus realized, plus unrealized at the venue mark, less fees.
  - Funding is not counted. Only USDT fees count.
  - Below 75% of its high, the book records `halted`, then closes every leg reduce-only, and trades nothing more.
  - The halt is saved before the first close. A close cut off (a timeout, a killed run) is finished at the next run, and a rebound does not undo the halt. A mark that cannot be read does not stop a close: it is recorded as unknown.
  - The value and the high stay in the signed store. Reports show only `halted` or `ok`.
  - The 5-minute runs do not step this book, so the halt is checked hourly.
  - **Resuming:** the operator's call. Move `data/perp_demo/learn_book.json` aside while the book is flat; the next run starts a fresh book.
- **Kill rules (register, section 5, row 5, written 2026-11-04):**
  - **Written for the paper:** looks 2027-04-26 (futility), 2027-10-26, 2028-10-26 and 2029-10-26; KEEP at t ≥ 2.4.
  - **Added for the demo:** kill if the median demo cost per side is above 15 bps after 200 position changes. This is read later from the records; the paper's 00:00 open in the bar store is the reference price. The halt above is the drawdown rule.
- **A run killed midway:**
  - Each batch of orders is recorded as running before its first order.
  - After the reconcile, the next run reads the order ledger for that batch's keys and records every fill the store missed. It then checks the legs against the venue.
  - A decision cut off inside its window is decided again.
- **A decision that fails:** the day is recorded failed and the run stops. No order is sent from the failure: the order that raised may have filled with its reply lost, so the legs are not known yet.
  - A duty to go flat (`flat_due`) is saved first.
  - The next run reconciles every order and records every fill the book missed. Then it closes every leg reduce-only, also under the entry lock.
  - A close that fails keeps the duty. Each later run tries again until the book is flat.
  - The day is not retried.
- **Records:** `data/perp_demo/learn_book.json`, signed like `pairs.json`.
  - Per day: the decision bar, the model quarter, predictions, positions, targets, and skips with reasons.
  - For every order: its client key, side, quantity, average fill, fees and mark at send.
    - Keys are `lrn-<tag>-<symbol>-c<n>` for a reduction and `-o<n>` for an addition. The symbol is cut to 18 characters.
    - The tag is the day (`yymmdd`) for a decision, with `h` added for a halt close, `m` for a missed day's close and `f` for a failed day's close.
  - `status.json` shows counts only (`learn_status`, `learn_legs`, `learn_days`, `learn_capped`).
- **Not checked:** whether all 20 coins have a perp on Demo Trading. A coin without one is skipped and recorded `not_on_demo`.
- **Blinding:** the book's fills, fees and value are returns of the H-LEARN-11 read. Compute none of them before the 2027-04-26 look ("Blinding of forward returns").

## Book FLOW02 on the same account
On 2026-11-05 the operator decided to put H-FLOW-BTC-02 on the Demo Trading account (triage register, section 5, row 4). It is a lead, not a result: one of 32 research rows, with a placebo p of .08 and a median t of about 1.2 over decision hours (`data/flow_btc_20261104_02/RESULTS.md`). The demo tests mechanics and cost; one year cannot confirm the edge.

Code: `src/trading_agent/execution/perp/flowbtc.py` (the book) and `tools/flowbtc/` (store, features, model, call). The hourly run trades it with `--flow02`.

- **Rule (frozen, `data/flow_btc_20261104_02`):**
  - Ridge, alpha 10, on the FULL set of 28 hourly features: price, open interest, taker, activity, positioning (top-trader and all-account long/short), funding and calendar. The research write-up says 30; `combo.py`'s FULL list and the port both have 28.
  - **Decision bar:** the hourly bar opening 00:00 UTC. Its features use data up to its close at 01:00.
  - **Call:** long BTCUSDT when the prediction is above the 80th percentile of |prediction| on the model's own training rows; otherwise flat. Never short.
  - **Refit:** at each calendar quarter start, on rows from 2020-09-01 whose 24 h label ends by the quarter start.
    - Each fit is written once to `data/flowbtc/models/<quarter>.json`, with its sha256 in `manifest.json`.
    - The hash is written first, then the file. A run killed between the two leaves a hash with no file: the next run fits the same rows and records them again.
    - A changed file is refused, and the day is missed.
- **Parity (2026-11-05):** on a store seeded from the packet, `tools/flowbtc/run.py --parity` replays the research walk: 2022-01-01 to 2026-10-25, the 00:00 bar, long-only, 5.5 bps a side. It gives n 1,758 and the net per decision of `combo.json` "h24|ridge|FULL|long", to every digit printed. The 2026Q4 fit made on the live path equals the walk's 2026Q4 fit.
- **Data and store:** `data/flowbtc/` (set by `--flow02-root`), git-ignored.
  - **Seed:** once, from the research packets (operator step 6): hourly bars built from 1 m klines (the minute count is kept), 5 m metrics and funding.
    - `--init-from` reports the seed's holes, before and after the research end (2026-10-26): hours missing or under 55 minutes, 5 m stamps missing or with a series empty, and funding settlements missing. Holes after the end are also printed to stderr. The next `--update` fills them (see Gaps).
    - **The packet's hole (2026-11-05):** its 1 m klines have no bars for 2026-10-28, which is 24 of the 144 hours after the research end. Its metrics and funding have no hole after the end. Before the end, 634 5 m stamps are missing, as in research.
  - **Update:** before each call, the book appends closed public data through `tools/newperp/fetch.py`: 1 h klines, the four 5 m series and funding.
  - **Lock:** `run.py --init-from` and `--update` write the store, so they first take `logs/.perp_demo.lock`, the wrappers' lock, exclusive and without waiting.
    - It is the lock of the checkout that holds the store. For a `--root` of the form `<checkout>/data/flowbtc`, the default included, it is `<checkout>/logs/.perp_demo.lock`, also when `run.py` runs from another worktree. For any other `--root`, it is the lock of the checkout `run.py` sits in.
    - So a store the hourly run writes somewhere else (`--flow02-root`) is guarded only when `run.py` runs from that run's checkout.
    - While a wrapper's run or another writing `run.py` holds it, they write nothing and exit 3. While they hold it, the wrappers' runs skip.
    - `--parity` and `--check-feed` only read and take no lock.
    - The book's own update runs inside the hourly run, under its wrapper's lock.
  - **Stamps:** the 5 m series are kept on the research archive's stamps. Measured 2026-11-05: open interest and both long/short ratios carry the API timestamp less 5 minutes; the taker ratio carries the API timestamp itself.
  - **Feed check (2026-11-05, 30 days, `run.py --check-feed`):** klines and funding matched the packet exactly. Open interest matched exactly on 99% of stamps; the rows that differ are all on 2026-10-15, by up to 3%. The other three series do not all match:
    - top-trader ratio: within the rounding of its 4 published decimals (median relative difference about 1.4e-5);
    - all-account ratio: median relative difference about 1.0e-4, at most about 2.6e-4. That is more than rounding explains;
    - taker ratio: median relative difference about 6e-5, at most about 1.4e-3.
    - **Effect on the calls (measured 2026-11-05):** on the 18 decision bars from 2026-10-08 to 10-25, before the holdout, the API values of all four series were put in place of the archive's. The 2026Q4 predictions moved by at most 0.1% of the threshold, and none of the 18 calls changed.
  - **Gaps:** the 5 m series keep about 30 days at Binance. A longer outage leaves a gap, recorded in `meta.json`. Its features stay unknown, and the model fills them with its train median, as in research.
    - **Holes after the research end are filled (2026-11-05).** Each update appends after the last row, then asks again for what is missing from 2026-10-26 on:
      - hours with no bar or under 55 minutes, from the public 1 h klines. They are marked like any appended bar (`src` 1, 60 minutes);
      - 5 m stamps, or one series on a stamp, with no value, inside the last 29 days. Only the series that lacks the value is asked, and a value on file is never replaced;
      - funding settlements missing between two on file, after the one at 2026-10-26 00:00.
    - Rows before 2026-10-26, and the 00:00 settlement that day, belong to research and never change. So `--parity` stays exact.
    - Each fill is recorded in `meta.json` (`filled`: series, from, to, count). The update output counts it: `filled_bars` in hours, `filled_metrics` in 5 m stamps, `filled_funding` in settlements.
      - The record is saved before the fill's rows are written, as the model's hash goes before its file. A run killed between the two keeps the record; the next update finds the hole again, fills it and records it a second time. A record save or a write that fails takes the record back out, and the fill counts as failed.
      - The append's gaps are saved the same way, before the rows around them. After a killed run:
        - a gap found again unchanged is not recorded twice;
        - a gap found changed, because the venue has served more of its rows since, is recorded again as found. The first record stays, so it can name stamps that are now on file in full. `gaps` is a log: only the update's check for a repeat reads it.
      - A gap at the end of the data, with no row after it yet, is found again at each update. Each time it is recorded again, with the same start and a later end. The last record, made when rows arrive after the gap, has its true end.
    - Each fill runs on its own. One that fails leaves its file as it was, and the other fills still run. The failed one is listed in `fill_failed` in the output, and in `last_fill_failure` in `meta.json` (series and error). It is asked again at the next update, and the next update whose fills all succeed removes `last_fill_failure`. `run.py --update` exits 1 while a fill fails.
    - A 418 or 429 inside a fill stops all asking for that run: the fills after it are skipped, `meta.json` is saved, and the update raises. The book then records `perp_flow02_update_failed`, as for a failed append: the day's call uses what the store holds, and a halted book's daily append tries again at its next run in the window.
    - A hole the venue does not serve is not a failure. It is asked again at each update; a 5 m hole only while it is inside the 29 days.
    - **Why:** before this, the update only appended after the last row, so the packet's 2026-10-28 hole stayed. `dhi30` would have seen it until about 2026-11-27, and the 168 h features until about 2026-11-05. The seeded rows of 2026-10-28 to 10-31 had unknown features and would have entered the 2027Q1 training set.
    - **Measured 2026-11-05 on a copy of a seeded store:**
      - one `--update` filled the 24 hours (`src` 1, 60 minutes), and `--parity` still gave n 1,758 and net 3.778035761550993;
      - on 2026-10-28 to 10-31, the rows with an unknown feature went from 96 of 96 (up to 14 a row) to none;
      - one 5 m stamp stays partly empty: the taker ratio at 2026-11-04 17:35, which the endpoint did not serve. It is asked again until about 2026-12-03.
  - **Wait for data:** the call waits until the store holds the 00:00 bar, the metric stamped 00:55, and a funding rate settled in the 8 h before 01:00.
  - **Faults:** a data or model fault stays inside this book. The day waits, and the other books still run.
- **Schedule:** the first run in [01:00, 03:00) UTC each day, normally the 01:15 run.
  - **Long call, flat book:** buy.
  - **Long call, long book:** keep the position, no trade.
  - **Flat call, long book:** sell the book's own quantity, reduce-only.
  - **No call in the window:** when data are missing, an error occurs or the entry lock holds back a buy, the day is recorded missed with the reason. A held position is sold reduce-only at the first run after 03:00.
  - **A sell that fails** before the venue takes it (a ticker timeout, say), or that the venue answers expired or cancelled, is sent again at the next run, and at each run after, until the book is flat. The duty is saved before the sell.
  - **A sell the venue refuses with an error** (Binance -2022 or -2019, say) is not retried. ccxt raises it, so the run records an unknown outcome. The next reconcile does not find the order at the venue and stops every run for operator review, as for any order whose send raised.
  - Days before the book's first run are not counted.
- **Timing deviation:** research enters at the 01:00 open. The demo fills at about 01:16, after the run has updated the store. The fill against the 01:00 open is part of the cost read.
  - The metric rows the call uses became public about 20 s to 2 min after 01:00 (two boundaries probed, 2026-11-05). Research's 01:00 entry used them before they were public; the demo does not.
  - **Measured 2026-11-05 on the packet (first numbers, unverified):** the research walk's own calls, entered later and held 24 h from the entry minute, give per decision (n 1,758, before the holdout):

    | Entry | Net bps | t |
    | --- | ---: | ---: |
    | 01:00 (research) | +3.78 | 2.23 |
    | 01:05 | +3.47 | 2.09 |
    | 01:16 (the demo) | +3.66 | 2.14 |
    | 01:30 | +3.52 | 1.92 |
    | 02:00 | +3.95 | 2.11 |

    At 01:16 every metric row the call uses is public, so that row is a fillable version of the research number. The edge does not depend on the first minutes after 01:00.
- **Size:** `--flow02-usd`, default 1,000 USDT, at most 3,000. The buy is `usd / mark`, cut to the BTC step, at 1x.
  - **A new size is a new book:** with a different `--flow02-usd` from the store's, a long call on a flat book buys nothing and is recorded skipped (`size_changed`). Sells still run. Move `flow02.json` aside to start at the new size.
- **Shared BTCUSDT:** the book records its own quantity. Every sell is reduce-only and sells this book's quantity only. The pair book's check counts it in the venue's BTCUSDT sum.
- **Caps:** a buy must keep the account's gross notional at or under 90% of equity. When it does not fit, the day's entry is skipped and recorded `equity`; it is not retried that day. Sells are never blocked.
- **Halt (register row 4, drawdown):**
  - **Sleeve value:** the size at the book's first run, plus realized and unrealized at mark, less USDT fees. Funding is not counted.
  - **Rule:** each hourly run checks the value. Below 65% of its high (a 35% drawdown), the book sells its quantity reduce-only, records `halted` and trades nothing more.
  - **The data store while halted:** the book still appends public data once a day, in its [01:00, 03:00) window, so a later resume finds no gap. A run without `--flow02` does not; after more than about 30 days without one, run `tools/flowbtc/run.py --update` before a resume.
  - **Resuming is the operator's call:** once the book is flat, move `data/perp_demo/flow02.json` aside. The next run starts a fresh store, and the high starts again at the size.
- **Kill rules (register, section 5, row 4, written 2026-11-04):**
  - **Futility:** at 365 decisions (about one year, about 47 held days), kill if the mean net per decision is below 0.
  - **Cost:** after 20 round trips, kill if the median demo round trip is above 22 bps (2x the measured 11).
  - **Drawdown:** kill if the sleeve falls 35% from its high. This one is automated, above.
  - The futility and cost rules are read from the records, not by the runner.
- **Records:** `data/perp_demo/flow02.json`, signed like `pairs.json`.
  - **Each day:** the call, the model, the prediction and threshold, the count of unknown features, the action and its order key. A missed day has its reason and attempts instead.
  - **Each order:** client key (`fb2-<yymmdd>-o<n>` for a buy, `-c<n>` for a sell), side, quantity, average fill, fees and the mark at send.
  - **Reference price:** the research price (the 01:00 open) is in the store's bars. The cost read takes it from there.
  - `status.json` shows counts only (`flow02_*`).
- **Blinding:** its fills are returns of the FLOW02 read, sealed until 365 decisions ("Blinding of forward returns").

## Book FLOW01 on the same account
On 2026-11-05 the operator decided to trade H-FLOW-BTC-01's primary cell as a book on the Demo Trading account (triage register, section 5, row 6). The research is `data/flow_btc_20261104_01` (git-ignored). Its primary cell failed its bar: test n 29, +24.2 bps net, t 0.59. The demo tests the mechanics, the timing and the cost. It cannot confirm the edge.

Code:
- `tools/flowflush/`: the rule (`rules.py`), the public feed (`feed.py`), the parity run (`parity.py`) and a keyless poll that prints the pre-check alone (`poll.py`).
- `src/trading_agent/execution/perp/flowflush.py`: the book.
- Record: `data/perp_demo/flow01.json`, signed like `pairs.json`.

The hourly run trades it with `--flow01`. The 5-minute task runs it with `--only flow01`.

- **Rule (frozen):** at a 5-minute open interest stamp t:
  - `ret60` is the log of the close of the 1m bar ending at t, over the close an hour earlier.
  - `doi60` is the log of the open interest at t, over the open interest an hour earlier. An open interest at or below 0 is unknown.
  - It is an event when both are known, `ret60` ≤ −0.021138149153270704 and `doi60` ≤ −0.03324631085383747. These are the train 1% quantiles in `train.json`.
  - After an event, the next one needs t past the previous t + 5 min + 4 h.
  - The trade: long BTCUSDT at 1x, held 4 h.
- **Parity (2026-11-05):** `parity.py` runs the live rule over the research panel. It gives the research cell exactly: train n 125, +19.9 bps net; test n 29, +24.2. Declustering the way the book does it, before the outcome is known, gives the same counts.
- **Data:** public mainnet data on `fapi.binance.com`, through the NL-P allowlisted client: 1m klines and `/futures/data/openInterestHist` 5m over the last 3 h. No key.
- **Stamps:** the endpoint stamps each snapshot 5 minutes later than the archive the research used.
  - The endpoint's row T equals the archive row T − 5 min. From 2026-10-07 to 10-26, 5,370 of 5,448 stamps are equal; the other 78 are all on 2026-10-15.
  - The feed shifts the endpoint's stamps back 5 min. Over that window the live `ret60` equals the panel's, and so does `doi60` except on 2026-10-15. No event fell in that window.
- **Timing, and why it matters:**
  - The snapshot of research stamp t is taken at t + 5 min, the minute research enters.
  - The endpoint published the snapshot some seconds to over a minute after its stamp. Four stamps were observed on 2026-11-05: 10 s and 18 s (this book's poll), 22 s and 84 s (book FLOW02's probe of the same series).
  - So the demo always enters after the research price, by that lag plus the time to its next run.
  - On the panel, the same events entered later give (first numbers, unverified):

    | Entry | Train net bps | Test net bps |
    | --- | ---: | ---: |
    | t + 5 min (research) | +19.9 | +24.2 |
    | t + 6 min | +9.5 | +43.8 |
    | t + 7 min (the expected demo entry) | +4.2 | +45.2 |
    | t + 8 min | +2.8 | +33.4 |
    | t + 9 min | +4.0 | +42.8 |
    | t + 10 min | +6.3 | +41.9 |
    | t + 15 min | +3.3 | +20.1 |
    | t + 20 min | +1.4 | +23.3 |
    | first hourly :15 run | −5.8 | −1.6 |

  - On train, most of the mean comes in the first minute after the snapshot, before the data are public. Expect the demo below the research number.
  - Rechecked 2026-11-05 with the packet's own `measure.py`, the event set fixed and only the entry minute moved: the rows above reproduce.
    - Every fillable minute row from t + 6 to t + 20 min is still positive after the 11 bps cost on both train and test, but its month-block t is under 0.4 on train and under 1.2 on test.
    - The first hourly :15 run is negative on both train and test. That is the mode the book trades in until the 5-minute task is registered (operator step 7).
    - The research +19.9 rests on an entry no live run can fill.
- **Cadence:**
  - **5-minute task** (`scripts/ops/run_flow_flush.sh`, every 5 minutes from :02 local): `live_perp_demo.py --execute --only flow01`.
    - It first asks public data and the book's record, without `.env` or any key, whether a signed run is needed: an exit due, an order left unsettled, or a fresh event.
    - A quiet moment ends there. Nothing is signed, sent or written, apart from the log.
    - When a signed run is needed, it reconciles, checks every book's legs and the venue's positions as the hourly run does, then steps FLOW01 alone. No other book steps. The checks can still send orders, as in every run: the hedge of a pair whose stop fired is sold, and an LV roll that was cut off is flattened.
    - So it passes every book's flag (`--lv --learn --flow02 --flow01`) with the hourly run's sizes: every book's legs must be explained.
  - **Hourly run:** also steps the book. It makes exits due and records events the 5-minute runs missed.
  - **Expected delays** (an estimate from the schedule, not measured):
    - The runs start 2 minutes after each 5-minute boundary, after the largest publication lag observed (84 s). So the run first sees a stamp's row 2 minutes after the snapshot, and the demo enters at about t + 7 min. The entry window lets the runs at t + 7, t + 12 and t + 17 min enter.
    - In the testnet window (:50 of an even UTC hour to :06 of the odd hour), the stamp at :45 of the even hour goes stale, and the stamps at :50 and :55 enter late (t + 17 and t + 12 min).
    - No 5-minute run starts between :13 and :16, so a signed run at :12 has about 3 minutes before the hourly run. With the exit tolerance below, an entry made by the hourly run exits at the hourly run 4 h later, and an entry made at :12 exits at the :12 run.
    - Runs while the hourly run holds the lock are skipped. An event goes stale then only if the hourly run lasts past about :27.
    - Without the 5-minute task, the hourly :15 run can enter only the stamps :00, :05 and :10 (:10 only if it is published before the run reads it). The run's `at` is some seconds past :15:00, so the :55 stamp is already more than 20 min old. About three of four events are then stale, and the ones it enters are late.
  - The phase was moved from :04 to :02 on 2026-11-05, before the task was first registered: it keeps a signed run away from the hourly :15 run and cuts about 2 minutes. On the panel the train mean barely moves between t + 7 and t + 9 min (table above).
- **Entry:**
  - At the first run with now ≥ t + 5 min and now − t ≤ 20 min. Otherwise the event is recorded stale.
  - An event inside an earlier event's cluster is not recorded.
  - Under `TRADING_ENTRY_KILL_SWITCH` the book does not look for events.
- **Exit:** at the first run with now ≥ entry + 4 h − 2.5 min. It is a reduce-only sell of the book's own quantity, also under the entry lock and after a halt.
  - The entry time is the entry run's `at`. The 2.5 min (half the 5-minute cadence) keeps a run's own start-up from pushing the exit one run later. The 5-minute pre-check reads its clock seconds before the signed start-up that set the entry time, and two hourly runs differ by seconds of start-up.
  - The hold is then 4 h within about 2.5 min on the 5-minute task, and 4 h, not 5 h, on hourly runs alone.
- **Size:** `--flow01-usd`, default 1,000 USDT, at most 3,000. The quantity is the size over the mark price, down to the venue step.
  - **A new size is a new book:** the size is stored at the book's first run. With a different `--flow01-usd`, a fresh event is skipped and recorded `size_changed`. Move `flow01.json` aside, once the book is flat, to start at the new size.
- **Shared account:**
  - BTCUSDT is shared with the pair hedges and book FLOW02. The venue's BTCUSDT must equal the sum of every book's BTC quantity, or the run stops.
  - After the entry, the account's gross notional must stay at or under 90% of equity. If it would not, the event is skipped and recorded `equity`. The alt-short cap does not apply to a BTC long.
- **Halt (automated):**
  - Trigger: cumulative realized net after fees below −10% of the sleeve (−1,000 bps, register row 6). The sleeve is the size stored at the book's first run.
  - Fees are the venue's USDT fees, or 0.05% a side when a fill reports none. Funding is ignored: a 4 h hold crosses at most one funding time.
  - A halted book closes any leg reduce-only, trades nothing more and shows only `halted`.
  - To resume, the operator moves `flow01.json` aside while the book is flat. The next run starts a fresh record, which sees only the last 2 h.
- **Kill rules (register, section 5, row 6, written 2026-11-04),** read from the records later:
  - **Futility:** at 20 events (about 2 years), kill if the mean net per event is below 0.
  - **Cost:** after 10 events, kill if the median all-in entry cost per side is above 5.5 bps. The cost is the fee plus slippage against the 1m open at t + 5 min.
    - That open is the research entry, at the snapshot minute, before the data are public. The measured cost will include the publication delay and the time to the next run, so this rule may fire on timing alone.
    - The record also holds the open of the minute each order was sent, so the read can separate the two. Each fill's `sent_ms` is the wall clock just before its order went out; `ms` is the run's start, which can be a minute or more earlier in the hourly run.
    - A change of this rule needs the operator's ruling on this page.
  - **Drawdown:** cumulative net below −1,000 bps of the sleeve. This one is automated, above.
- **Record:**
  - Each event: t, `ret60`, `doi60`, the detection delay and its status (open, closed, stale, skipped, before_start or failed).
  - Each order: client key (`fb1-…`), side, quantity, average fill, fees, mark price at send, the run's start (`ms`) and the send time (`sent_ms`). A fill settled from the ledger after a cut has neither time.
  - The mainnet 1m opens at t + 5 min, at the entry's and the exit's send minutes, and at t + 5 min + 4 h.
  - Stamps the feed could not see are recorded as gaps.
  - `status.json` shows counts only (`flow01_*`).
- **Blinding:** the book's returns are sealed until its first look at 20 events ("Blinding of forward returns").

## Book HOLD and the three options for 10k
On 2026-11-04 the operator asked to try every option for a 10k USD real account on Demo Trading first, with no real money. The options and the evidence behind them are in the 2026-11-04 discussion (research description, not advice):
- **A, cash:** hold USDT. Nothing trades. It is the benchmark the other two are read against, at a stated cash rate.
- **B, BTC held:** buy BTC spot once, no leverage, no timing, no rebalancing. This is book HOLD below.
- **C, NL-P sleeve:** the pair book and the shadow ledger above, at 1x of a 10k account: 500 USDT a leg, 5% of 10k per name. Under the shared caps at most 8 pairs fit on a 10k wallet that holds nothing else, and fewer beside the other books ("Shared risk caps"). Its KEEP/KILL read is in `docs/operations/NEWPERP_PAPER_RUN.md`.

Book HOLD runs on the spot wallet of the same Demo Trading account (`demo-api.binance.com`, same key). That wallet is separate from the futures wallet, so its BTC never nets against the pair book's BTCUSDT hedges.
- **Code:** `src/trading_agent/execution/demo_spot/`. Its `venue.py` is the only place its orders leave, and it refuses any Binance host but Demo Trading. `scripts/live_spot_hold_demo.py` runs it, and the hourly wrapper runs it after the perp run.
- **Size:** `--hold-usd`, default 2,500 USDT, at most 4,500. The demo spot wallet held 5,000 USDT and 0.05 BTC on 2026-11-04, so the default is a 50/50 split of a 5,000 sleeve: half option B, half option A.
- **Buy:** once, at market, reserved in a signed order ledger first. A buy whose outcome is unknown is settled by its client key on the next run. A buy that ends rejected or cancelled marks the book failed and is not retried.
- **Check:** the wallet came with BTC, so the book records the wallet's BTC before its buy. Each run checks that the wallet still holds at least that plus what the book bought. Less stops the run for the operator.
- **No exit rule.** Closing the book is the operator's call.
- **Records:** `data/spot_hold_demo/{hold.json (signed), order_state.json, audit.jsonl, status.json}`.
- **Read:** its return is BTC's price change less one buy fee, so it is not sealed. What the demo adds is the mechanics: the order path, the fee, and the wallet check.

## Blinding of forward returns
These books' returns belong to sealed reads: the NL-P paper and shadow ledgers, the pair book, the LV demo book, and books LEARN, FLOW02 and FLOW01.
- **First looks:** book LV at its read (2028-11-13); book LEARN at the H-LEARN-11 futility look (2027-04-26); book FLOW02 after 365 decisions; book FLOW01 after 20 events.
- **Agents:** compute no return, P&L or price change from `audit.jsonl`, `order_state.json`, `pairs.json`, `lv_blocks.json`, `learn_book.json`, `flow02.json`, `flow01.json`, `shadow.json` or `shadow_ledger.jsonl` before a book's read or first look. Counts, statuses and errors are fine.
  - The same holds for book FLOW02's calls joined with the bars in `data/flowbtc/`, and for the H-LEARN-11 bar store (`docs/operations/LEARN_PAPER_RUN.md`).
- **Runner output:** `status.json`, the dry-run plan, the printed report and the audit events show counts, statuses, quantities and keys only. `perp_run_started` also carries each book's configured size (`pair_usd`, `lv_usd`, `learn_usd`, `flow02_usd`, `flow01_usd`), which are flag values, not results. The halts compute a book value internally and show only `halted` or `ok`.
- **Operator:** the Demo Trading website's P&L, position and order-history pages show the same returns. Avoid them for the demo account; use `status.json` and the logs, which show counts only. A look before a read is recorded in this page's log, and that book's read is then reported as unblinded.
- Book HOLD is not sealed: its return is BTC's price change less one fee.

## Limits
- Demo fills come from the demo venue's matching, not mainnet depth.
- The demo enters later than research: book LEARN about 15 minutes after the paper's 00:00 open, book FLOW02 about 15 minutes after research's 01:00 open, and book FLOW01 some minutes after research's entry (see its section).
- The book's evidence is discovery on read data. The NL-P forward read decides it.
- A run that cannot reach the venue does nothing. The exchange-side stop still protects an open short.
- **Client rate limit:** a run's adapter lets 100 rate-limited calls through at once, then 100 a minute (`RateLimitManager` in `src/trading_agent/exchanges/ccxt_adapter.py`). Before 2026-11-05 its tokens never came back, so the 101st call of a run waited for ever while the run held the lock. A call now waits at most 0.6 s. Counted offline on the test fakes, one run with a pair entry, LEARN's first 20 legs, the FLOW02 and FLOW01 entries and a 4+4 LV roll makes 76 such calls when each market order comes back filled with a price, 140 when each needs one read-back, and 178 when the LV roll's stop fails and the roll flattens. The LV roll in that count is a `--lv-rehearse` roll in the same run. A scheduled run cannot hold both LEARN's trade and an LV roll: LEARN trades in [00:00, 02:00) UTC and LV rolls on Monday in [04:00, 06:00) UTC. A scheduled run with the other entries makes fewer calls. The limit adds at most (calls - 100) x 0.6 s, less the time the calls take themselves: 24 s for 140 calls at most, none at 0.2 s a request.
