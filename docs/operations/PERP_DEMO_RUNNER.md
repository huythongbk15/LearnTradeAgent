# New perp pair book on Binance USD-M Demo Trading

The first perp order path of the core, on Binance Demo Trading only. Research description, not advice. Mainnet stays NO-GO, and nothing here grants a market right.

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
  - `--pair-usd` per leg, default 100 USDT, at most 1,000. The hourly wrapper passes 500 (operator ruling, 2026-11-04): 10 open pairs of two 500 USDT legs make 10,000 USDT gross, option C of a 10k account at 1x;
  - at most 10 open pairs;
  - an entry needs every pair's two legs together under 90% of equity;
  - cross margin at 1x, one-way position mode required.

## Safety chain
- **Gates:** `require_execution_authorization` (TRADING_MODE=testnet, TRADING_EXECUTION_ENABLED, TRADING_KILL_SWITCH). `TRADING_ENTRY_KILL_SWITCH` stops entries but not exits.
- **Single writer:** one writer at a time, through `LiveExecutionLock` on `data/perp_demo/writer.lock`.
- **Signed order ledger:** every order intent is reserved in a signed ledger (`LiveRiskStateStore`, `order_state.json`) before it is sent, with the same client key Binance receives.
  - An outcome that is not known stops the run.
  - The next run settles it by that key first.
- **Signed pair store:** pairs live in a signed store (`pairs.json`, `LIVE_SAFETY_HMAC_KEY`). A changed file is refused.
- **Position check:**
  - Each run checks venue positions against the book before any entry.
  - Any position the book cannot explain stops the run.
  - The operator decides what to do with it.
- **Failed open:** if anything fails once the short was sent, the run takes off at market the short the venue holds on that symbol, and the hedge if it filled, then stops.
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

## Schedule
- `scripts/ops/run_perp_demo.sh` runs `live_perp_demo.py --execute --shadow --lv`, then `live_spot_hold_demo.py --execute` (book HOLD), every hour at :15 local, at nice 19. It logs to `logs/perp_demo.log`.
- No run starts from :50 of an even UTC hour to :06 of the odd hour, and a run is skipped while the previous one is going.
- There is no timeout, so an `--execute` run is never cut between an order and its record. The next run reconciles first.
- `TRADING_MODE=testnet` is set by the wrapper. The other switches and the demo key come from `.env`, as for the spot runner.
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
- **Read, proposed, to be ruled by the operator before the first read:**
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
  - A coin the pair book holds, or BTCUSDT, is skipped too.
- **Roll:** at the first run from Monday 04:00 UTC, within 2 h.
  - **Size:** `--lv-usd`, both legs together, default 1,000 USDT, at most 5,000.
  - **Legs:** half a side, equal weight over the names it trades.
  - **Trades:** only the difference to the targets. Reductions go first and are reduce-only, and a side flip goes through flat. A difference below the venue minimum is skipped.
  - **Equity:** the book plus every open pair must stay under 90% of equity, or the week is missed.
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
  - **No shared symbols:** the pair book never enters or rehearses on a symbol LV holds.
- **Failures:**
  - **A roll that fails:** it takes off everything the venue holds on the block's symbols, cancels its stops, and marks the block failed. The week is not retried.
  - **A roll killed midway:** it is left as `rolling`, and the next run flattens it the same way after the reconcile.
- **Record:** `data/perp_demo/lv_blocks.json`, signed like `pairs.json`. `status.json` shows counts only.
  - Its fills and funding are returns of the LV forward read. Do not compute them before that read (the 2028-11-13 run).
  - The demo account's own P&L view shows them too.
- **Rehearsal:** this rolls a hand-given pick now, with a 2 h hold, to test the mechanics before 2026-11-09. Use coins from the list, never masked ones.
  ```
  TRADING_MODE=testnet TRADING_EXECUTION_ENABLED=true PYTHONPATH=src:. .venv/bin/python scripts/live_perp_demo.py --execute --lv --lv-rehearse "LTC,XLM:SUI,WLD"
  ```
  - A second rehearsal before the hold ends rolls into the first, which tests the netting.
  - A rehearsal is refused while a weekly block is live.

## Book HOLD and the three options for 10k
On 2026-11-04 the operator asked to try every option for a 10k USD real account on Demo Trading first, with no real money. The options and the evidence behind them are in the 2026-11-04 discussion (research description, not advice):
- **A, cash:** hold USDT. Nothing trades. It is the benchmark the other two are read against, at a stated cash rate.
- **B, BTC held:** buy BTC spot once, no leverage, no timing, no rebalancing. This is book HOLD below.
- **C, NL-P sleeve:** the pair book and the shadow ledger above, at 1x of a 10k account: 500 USDT a leg, at most 10 pairs, so 10,000 USDT gross at most, and 5% of 10k per name. Its KEEP/KILL read is in `docs/operations/NEWPERP_PAPER_RUN.md`.

Book HOLD runs on the spot wallet of the same Demo Trading account (`demo-api.binance.com`, same key). That wallet is separate from the futures wallet, so its BTC never nets against the pair book's BTCUSDT hedges.
- **Code:** `src/trading_agent/execution/demo_spot/`. Its `venue.py` is the only place its orders leave, and it refuses any Binance host but Demo Trading. `scripts/live_spot_hold_demo.py` runs it, and the hourly wrapper runs it after the perp run.
- **Size:** `--hold-usd`, default 2,500 USDT, at most 4,500. The demo spot wallet held 5,000 USDT and 0.05 BTC on 2026-11-04, so the default is a 50/50 split of a 5,000 sleeve: half option B, half option A.
- **Buy:** once, at market, reserved in a signed order ledger first. A buy whose outcome is unknown is settled by its client key on the next run. A buy that ends rejected or cancelled marks the book failed and is not retried.
- **Check:** the wallet came with BTC, so the book records the wallet's BTC before its buy. Each run checks that the wallet still holds at least that plus what the book bought. Less stops the run for the operator.
- **No exit rule.** Closing the book is the operator's call.
- **Records:** `data/spot_hold_demo/{hold.json (signed), order_state.json, audit.jsonl, status.json}`.
- **Read:** its return is BTC's price change less one buy fee, so it is not sealed. What the demo adds is the mechanics: the order path, the fee, and the wallet check.

## Limits
- Demo fills come from the demo venue's matching, not mainnet depth.
- The book's evidence is discovery on read data. The NL-P forward read decides it.
- A run that cannot reach the venue does nothing. The exchange-side stop still protects an open short.
