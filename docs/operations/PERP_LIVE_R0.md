# Perp live tier R0: ruling page

Status: **APPROVED IN CHAT by the operator on 2026-11-11 (08:54Z), in force when this page's PR is merged.** The operator answered the decisions below in their own words. Lines marked *[filled]* were not answered one by one; they hold the review's proposal under the operator's "các ý còn lại đồng ý với đề xuất", and merging the PR approves them. Until the merge, mainnet stays NO-GO everywhere.
Owner: operator
Written: 2026-11-11, from the review `data/review_20261111/REVIEW.md` (git-ignored; 21 agents, verified, with a critic).

Research description and engineering rules. This page is not investment, tax or legal advice. Money amounts never go in git: the page says who sets them and where they are kept.

## 1. What this page decides
- **One tier, R0, on the perp path only.** One book trades on Binance USD-M mainnet at the venue's minimum size, in a dedicated futures sub-account, until a written end. Everything not on this page stays NO-GO, the spot runner's mainnet path included.
- **Purpose: mechanics and cost.** R0 measures what Demo cannot: the mainnet order path, stop handling, real fills, fees and funding, and whether the system runs unattended. No researched rule is PASS (`data/triage_20261104_01/REGISTER.md`: 0 PASS), so R0 claims no edge.
- **What it replaces, for R0 only and by name:**
  - the 30-day testnet soak and checklist of `docs/LIVE_TRADING_RUNBOOK.md` "Go/no-go rule" and the release gates of `docs/LIVE_TRADING_TODO.md`. They gate the spot runner's promotion ladder, which nothing can feed (no producer of `strategy_artifact_ids`; only `enhanced_ma` is accepted, and research rejected it). They stay in force for the spot runner.
  - the sentence "never money" in `data/krw_fade_20261108_01/KILL_RULES.md` "The book", since book KRW is chosen (D4).
  - the tier condition of the register, "Small real money ... comes only after demo fills match the cost assumptions" (`REGISTER.md` line 12), waived by D6.

## 2. Decisions

| # | Question | Ruling | Operator's words, 2026-11-11 |
|---|---|---|---|
| D1 | Lift NO-GO for tier R0 alone, under this page? | **Yes.** | "D1: có" |
| D2 | Where does the system trade? | **A dedicated futures sub-account never traded by hand.** The hand-traded account cannot work: a position no book owns stops every run, exits included (`book.py` "venue positions do not match the book"), and one cross-margin pool would join the system's 1x legs to the hand book. | *[filled]* |
| D3 | What is R0 for? | **Mechanics and cost.** No register row supports a profit book. | *[filled]* |
| D4 | Which book? | **KRW alone** (H-KRW-FADE-16, `--only krw`). It already runs alone, places its exchange stop before the hedge, and exercises every order path. Against it: research net +160.7 bps, t 0.78; stopped events average -6,473 bps; entries need the host up in a 15-minute window; about 39 event clusters a year, so about 47% of weeks have none. | *[filled]* |
| D5 | Loss budget | **Computed by the system from the sub-account's equity, read at every signed run** (section 3, "Loss bound"). The operator chooses only how much to move into the sub-account; the system refuses entries when that equity is below what one event needs. The amount is kept outside git (section 6). | "ngân sách lỗ tự tính toán theo tài sản hiện có chứ" |
| D6 | The register's "demo fills match cost" condition | **Waived for R0.** R0 measures that cost on mainnet (K-cost). | "miễn điều kiện 'Demo khớp chi phí'" |
| D7 | Vietnam law and tax | **Proceed, check weekly for the first licensed crypto asset provider, and stop under K-law.** The operator looks weekly; an agent also checks when asked in a session. Open questions kept on record: does Resolution 05/2025 Art. 7(2) reach offshore perpetuals; foreign-exchange rules for offshore margin; does the 0.1% personal income tax reach a futures close. | "đi tiếp và theo dõi tin cấp phép hàng tuần" |
| D8 | Funding the sub-account after the 2026-10-29 Binance incident | **Now.** Funding is needed only from stage C; stage B uses a read-only key. | "nạp tiền ngay" |
| D9 | Key | **Self-generated Ed25519, futures trade only, no withdrawal, no IP list.** Binance FAQ: HMAC keys without an IP list are read-only; a self-generated key is one of the stated ways around it. Not yet tried on a sub-account: stage C is the test. | *[filled]* |
| D10 | Who can read the key | **A separate WSL user owns the live checkout and the key file (0600); the Windows task runs as that user.** Agent sessions run as the usual user and cannot read it. | *[filled]* |
| D11 | Host | **The home PC:** no sleep, automatic logon after power loss, clock resync at boot, outside heartbeat. A VPS would need the "no new scheduler" rule lifted, puts the key on a rented machine, and Upbit's notice backend blocked most cloud ranges tested on 2026-11-11. | *[filled]* |
| D12 | Stop trigger | **Last price (`CONTRACT_PRICE`), set explicitly, on Demo and mainnet alike.** Research triggers on the 1h high of traded prices, and Demo has run on the venue default, which is last price. At 1x cross in a sub-account, liquidation is far from the 1.5x stop. | *[filled]* |
| D13 | Blinding | **The live record is separate.** Book KRW's K1 and read stay on the shadow rows and the Demo record as approved. The operator avoids the sub-account's P&L page and reads counts. | *[filled]* |
| D14 | Alert channel | **A Telegram bot the operator creates, plus an outside dead-man check.** | *[filled]* |
| D15 | End of R0 | **The first of: 10 real round trips, or 2027-03-31.** The date is about 13 weeks after the earliest unattended start; the operator may move it before stage D. | *[filled]* |
| D16 | What may change size after R0 | **An operator ruling that cites the measured numbers**, up or down: cost per side, missed share, stop fills, mechanics faults, funding, and book KRW's K1 and read. Nothing changes size automatically. Note on record: at about 10 events, R0's P&L has a standard error of about 660-740 bps against a research net of +161, so a ruling should not rest on R0's P&L alone. | "tùy các số liệu chọn tăng giảm hợp lý" |

## 3. The tier
- **Account:** the sub-account of D2 in one-way position mode, single-asset mode, USDT only (no BNB, so fees are paid in USDT), cross margin, leverage setting 1 on each symbol used. The run checks these at start and stops if any differs.
- **Book:** KRW, its frozen rule unchanged except the changes listed in section 5, each approved by this page.
- **Size: the venue minimum.** `--krw-usd` is the smallest value at which both legs pass the venue's filters. The BTC hedge is never rounded below one step (`venue.py` `normalize_nearest`); the BTCUSDT step is 0.001 BTC with a 50 USDT minimum notional (exchangeInfo, 2026-11-11). The run reads the filters from exchangeInfo; nothing hard-codes them. The value lives in the live env file (section 6), not in a tracked wrapper.
- **One event at a time** (KRW `max_open` 1 on the live root).
- **Loss bound (D5), computed by the system:**
  - **Per event:** the size is the venue minimum whatever the sub-account holds. A stopped event loses about two thirds of one leg (research: -6,473 bps on one leg), more in a squeeze that gaps past the stop.
  - **Per tier:** K3 halts entries when the sleeve's realized net is below -15% of 4 x `--krw-usd`, so one stopped event trips it; K-pause then holds entries until the operator signs. So R0 is bounded to about one stopped event between signatures, not by the sub-account's size.
  - **Floor:** at each signed run the system reads the sub-account's equity and refuses an entry when it is below 2.5 x `--krw-usd` (the 40% alt-short cap in `risk.py` needs that much for one event) or below the K-budget floor in section 7. It logs `ok` or `below_floor`, never the amount.
  - A larger transfer does not raise the size; it only adds room before the floor.
- **Hedge drift:** the BTC leg is one step while the alt leg is a USDT amount, so the pair is not equal-legged as BTC moves. One more reason R0's P&L is not the research trade.
- **Separation from Demo and testnet:**
  - its own checkout pinned to a tag, its own state root (`data/perp_live/`), lock, log, wrapper, env file, kill switches and credential names;
  - one more task in the existing `scripts/ops/windows/register_tasks.ps1`; no new scheduler;
  - Demo book KRW and its shadow rows keep running, so each event gives a shadow row, a Demo fill and a mainnet fill.

## 4. Stages
Each stage starts only when the one before is done as written. Mechanics only: no stage reads P&L.

| Stage | What | Done when |
|---|---|---|
| A, Demo | The section 5 code, built and merged; each new path drilled once on Demo; book KRW's first Demo exit (due about 2026-11-13T05:00Z) observed, including the reduce-only buy under an open reduce-only stop; the perp tests pass against both venue classes; an independent review of the mainnet diff | all listed items done, with the PR numbers recorded here |
| B, read-only soak | The live task runs on mainnet in the sub-account with a **read-only** key for at least 7 days. The venue refuses any order. | every slot accounted for (ran, named skip, or an alert that reached the phone); start-up checks pass; one forced outage (PC off) pages the phone |
| C, attended rehearsal | The operator, not an agent, types the commands. Trade key in place, sub-account funded. At venue minimum: one round trip of the book's shape, one stop fired on purpose with a tight rehearsal stop, one hand close in the app followed by the settle command. | the system's records equal the venue's order history, every intent is settled, the account ends flat, and no code changed after the clean pass; the live checkout is tagged |
| D, unattended | The live task is enabled. | "live" is counted when one full lifecycle (entry in the window, stop listed in the same run, exit by time or stop) completes with no operator action. A week with no event is endurance only and is reported as such. |
| End | At D15, or a kill rule | the read (section 8) and teardown are done |

## 5. Code to build in stage A
Each item is a PR the operator merges.
1. **Mainnet venue:** a second venue class beside `PerpDemoVenue`, unchanged, that checks the exact mainnet host **before** `connect()` (on mainnet, `load_markets` signs requests); its own credential names; the live confirmation gates of `live_safety.py`; a live mode that runs book KRW and nothing else.
2. **Separation:** the live root, lock, log, env file, kill switches and wrapper of section 3. The live wrapper never passes `--krw-resume`; a live-safe resume path for K3 (today `--krw-resume` refuses `--only`).
3. **Stop path (changes to the approved KRW book, approved here):** the D12 trigger type set explicitly on Demo and live; a fired stop read back as an algo order by its algo client id (today the read-back probably falls back to 1.05x the trigger every time); a close the book did not order (liquidation, ADL, a hand close) kept as its own state and never booked as a fired stop; a signed check every hour while an event is open (the 5-minute mode cannot see a fired stop, so the BTC leg would wait); a daily signed check while flat (so a revoked key, a foreign position or a wrong mode is found before an entry window); a marked rehearsal mode with a tight stop.
4. **Way out:** a perp command over `LiveRiskStateStore.resolve_unsubmitted_order` to clear `manual_intervention` under the evidence rule of section 6; errors raised before an order reaches the network recorded as rejected; a settle command for the signed record after an outside close; a book-driven "exit this event now".
5. **Alerts that leave the PC:** a dead-man ping sent only after a run that ended rc 0 (never on a lock skip); a failure message on rc not 0, a halt, a fired stop, an unexplained position or an unsettled order; Upbit 403 and 429 as named skips, not rc 1; one notice read shared by the Demo and live roots. Messages carry counts and statuses only.
6. **Records and the loss floor:** per order, the mainnet mark at send, fill, fee and fee asset; per event, funding at the real settlement interval (capped contracts can switch to hourly without notice), run delay against E, stop level and its fill; the equity floor check of section 3.

## 6. Rules for people and agents
- **Agents** never read the live key or the live env file, never run a command against the live root with `--execute`, and compute no return from `data/perp_live/` before book KRW's K1 look. Counts, statuses and errors are fine. The operator types every mainnet command of stage C.
- **Amounts** (transfers, `--krw-usd`, the starting equity) are kept in the live env file and the operator's own notes, never in git, logs or messages. Reports use bps, shares and counts.
- **Evidence to clear `manual_intervention`:** the venue shows no order with that client key after at least 10 minutes, the operator confirms in the app, and the clearing is written to the audit log with both facts.
- **No hand trades in the sub-account.** One is a K-safety stop by design.

## 7. Kill rules
- **K-budget** *[filled]*: entries stop when the sub-account's equity is below half of its equity at the start of stage D, read by the system (D5). No top-up without a new written ruling. Exits always run.
- **K-safety (automatic; pages at once; entries stop until the operator signs here):** a position no book owns; a short without a listed stop after its entry run; an order with an unknown outcome; a store failing its signature; the account not in one-way, single-asset mode; a host check failure.
- **K-repeat:** two K-safety stops in R0 end the tier for review.
- **K-pause:** after any fired stop, no new entry until the operator has read its record and signed here. A stop filled worse than 1.575x the entry is reported as a broken research assumption.
- **K-cost** *[filled]*: after 5 real round trips, if the median alt cost per side (fee plus |fill - mainnet mark at send| / mark) is above 37 bps, real orders stop; Demo and the shadow rows continue. At venue minimum this cost is a lower bound: top of book, not depth.
- **K-miss** *[filled]*: if 2 of any 4 consecutive qualifying events are missed for host or feed reasons, real orders stop until the cause is fixed (two hours late kept +28 of research's +161 bps).
- **K-ops** *[filled]*: a hung run, or no heartbeat for 2 hours while an event is open, means the operator closes the event with the book's "exit now", else in the app followed by the settle command.
- **K3 and K1 stay as approved:** K3 halts entries on the live sleeve; K1 at 30 settled shadow events kills the book on Demo and live.
- **K-law and K-venue:** a credible report of the first licensed crypto asset provider in Vietnam, or a Binance restriction on the account, on futures or on moving the funding asset: entries stop, exits run, and the operator rules again.
- **K-change:** any code change to the live checkout means stop, a new tag, one attended cycle, then unattended again.
- **K-time:** R0 ends at D15 even if nothing fired. No automatic extension.
- **Size:** changes only by D16.

## 8. Read and teardown
- **Read, at the end:** counts and cost first. Slots run, skipped and failed with reasons; events seen, entered and missed with reasons; alerts sent and acknowledged; stops fired and their fill against the trigger; alt cost per side against 37 bps; funding per event against the 8-hour assumption; Demo-minus-mainnet fill per event. Under D16 the operator may also read the live record's P&L at the end, stated as unblinded for the live record only; KRW's K1 and read stay on the shadow rows. The operator rules on what follows.
- **Teardown:** disable the task; confirm the sub-account is flat with no open order (conditional orders included); revoke the key; move the balance back; archive the live root.

## 9. Known open facts
Unverified when this page was written; stages B and C are where they get answered:
- a self-generated Ed25519 key with no IP list keeps futures trade permission on a sub-account;
- a sub-account's margin, liquidation and position mode are its own, and its loss cannot reach the master account;
- transfers from the master account to the sub-account work after the 2026-10-29 incident;
- ccxt 4.5.90 places, lists, cancels and reads back an algo stop on mainnet;
- a reduce-only market buy passes while a reduce-only stop is open on the same symbol;
- what Binance does to a listed conditional order when it liquidates or deleverages the position;
- the real fee rate and fee asset of the sub-account;
- why the PC was off for 57.5 hours (hourly runs stopped after 2026-11-05 16:15Z and resumed 2026-11-08 01:46Z), and why the 2026-11-05 13:01Z testnet run hung.

## Operator steps
1. Merge this page's PR. That approves the *[filled]* lines too; change any of them in the PR first if wanted.
2. Before stage B: open the futures sub-account (one-way, single-asset, USDT only); create a **read-only** key for it; create the Telegram bot and the dead-man check; set the PC to never sleep and to log on after power loss; say why the PC was off on 2026-11-05 to 11-08; create the separate WSL user of D10 (an agent writes the exact commands when stage A is done).
3. Before stage C: create the Ed25519 trade key (no withdrawal) as that user; transfer the amount of your choice into the sub-account (D8).
4. Every week: look for news of the first licensed crypto asset provider in Vietnam (K-law).

## Change log
- 2026-11-11: drafted from the review at the operator's request ("Mở, soạn trang quy định").
- 2026-11-11 08:54Z: the operator answered D1, D5, D6, D7, D8 and D16 and agreed to the other proposals ("các ý còn lại đồng ý với đề xuất"). D5 is read as a loss bound the system computes from the sub-account's equity; the end date of D15 and the values of K-budget, K-cost, K-miss and K-ops were filled from the review's proposals and are marked *[filled]*.
