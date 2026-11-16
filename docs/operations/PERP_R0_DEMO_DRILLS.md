# Perp R0 stage A: Demo drills

Stage A of [PERP_LIVE_R0.md](PERP_LIVE_R0.md) needs each new path run once on Binance USD-M Demo before stage B. This page lists the drills, the operator's commands, and what passes. The operator types every command; an agent never runs `--execute`. Rules of the paths: [PERP_DEMO_RUNNER.md](PERP_DEMO_RUNNER.md), "Book KRW".

## Before every drill
- Run from the main checkout, as the usual user, on the current `master`:
  ```bash
  cd ~/.qwenpaw/workspaces/trading
  ```
- **When:** the Demo wrapper skips from :50 of an even UTC hour to :06 of the odd hour, and while another run holds `logs/.perp_demo.lock` (the hourly run at :15, the 5-minute runs from :02). A skipped run exits 0 and writes nothing, so start at about :20 to :45 and check that the log has your run's start line:
  ```bash
  grep -E '^=== ' logs/perp_demo.log | tail -2
  ```
- **The symbol of a rehearsal (PERP):** a USDT perp on Demo with no position and no open order in the Demo app (Positions and Open Orders, conditional orders included). A symbol another book holds is refused (`shadow_only`, `held_by_...`).
- **One rehearsal at a time.** Each drill below starts after the one before is closed.
- **Event id:** a rehearsal's id is `e<minute>-<TOKEN>`. The newest is:
  ```bash
  grep '"perp_krw_rehearsal"' data/perp_demo/audit.jsonl | tail -1
  ```
- **What an event holds,** mechanics only (no price, no P&L). Replace `EID` with the event id:
  ```bash
  .venv/bin/python - EID <<'EOF'
  import json, sys
  ev = json.load(open("data/perp_demo/krw.json"))["pairs"][sys.argv[1]]
  print({k: ev.get(k) for k in ("status", "close_reason", "rehearsal", "stop_x", "hold_ms", "outside", "stale_stop")})
  for f in ev.get("fills") or []:
      print({k: f.get(k) for k in ("key", "side", "symbol", "avg_from", "fees_from", "fee_read", "from_stop", "from_outside", "fired_ms")})
  EOF
  ```
- **Book KRW's counts:**
  ```bash
  .venv/bin/python -c "import json; d = json.load(open('data/perp_demo/status.json')); print({k: v for k, v in d.items() if k.startswith('krw')})"
  ```

## Drills

### 1. A rehearsal whose stop fires (rehearsal, stop read-back, fees)
1. Make and enter a rehearsal with a stop 0.2% above its fill and a 30-minute hold:
   ```bash
   scripts/ops/run_perp_demo.sh --krw-rehearse PERP --krw-rehearse-stop-x 1.002 --krw-rehearse-hold-min 30
   ```
2. Check that the run reported `"rehearsal": "made"` and `"entered": 1` in its `krw` section, and that the Demo app shows the short with its conditional stop and the BTC long:
   ```bash
   tail -n 30 logs/perp_demo.log | grep -o '"krw": {[^}]*}'
   ```
3. When the app shows that the stop fired, run a signed check by hand (the 5-minute runs cannot see a fired stop):
   ```bash
   scripts/ops/run_perp_demo.sh
   ```
4. **Passes when:**
   - the event is `closed` with `close_reason` "stop";
   - its stop row has `from_stop` true, `avg_from` "algo" and a `fired_ms`;
   - every fill has `fees_from` "trades" (a fee not readable yet is read again by later runs, up to 5);
   - `krw_rehearsal_stops` is 1 and `krw_stops_fired` did not change.
5. **If it ends otherwise:**
   - **The stop did not fire in 30 minutes:** the 5-minute runs exit it at its hold (`close_reason` "hold"). That passes the hold exit but not the stop. Repeat with `--krw-rehearse-stop-x 1.001`.
   - **The event is `outside` with `algo_status` "not_found":** Demo does not answer the algo read-back. This is a finding to report. Settle the event as in drill 3, steps 4 and 5.

### 2. Exit now
Exit now closes every open event of book KRW in that run, real ones too. Run it only when `krw_open` is 1, the rehearsal alone.
1. Make a rehearsal with the book's stop and a day's hold:
   ```bash
   scripts/ops/run_perp_demo.sh --krw-rehearse PERP --krw-rehearse-hold-min 1440
   ```
2. In a later run, exit it now:
   ```bash
   scripts/ops/run_perp_demo.sh --krw-exit-now
   ```
3. **Passes when:**
   - the event is `closed` with `close_reason` "operator";
   - the Demo app shows no position and no open order on PERP;
   - the run's `entered` is 0 for every book (exit now puts the run under the entry lock).

### 3. A hand close and the settle (outside close)
1. Make a rehearsal with the book's stop and a day's hold:
   ```bash
   scripts/ops/run_perp_demo.sh --krw-rehearse PERP --krw-rehearse-hold-min 1440
   ```
2. In the Demo app, close **only the PERP position** at market (Positions, Close, Market). Leave its conditional stop and the BTC position alone.
3. Run a signed check:
   ```bash
   scripts/ops/run_perp_demo.sh
   ```
   - **Passes when** the event is `outside`, its `outside` record shows `venue_qty` 0.0, `krw_outside` is 1, and no order went out for it.
   - Record `stop_listed` and `algo_status` too. Whether Binance keeps a reduce-only stop listed after its position is closed by hand is not known (PERP_LIVE_R0.md section 9). True with "NEW" means it keeps it; false with "CANCELED" or "EXPIRED" means it cancels it. Either result passes.
   - While it is `outside`, any new Demo KRW event is `shadow_only` with `outside_close`. Keep this drill short.
4. Report what closed it. This only reads the venue and writes nothing:
   ```bash
   PYTHONPATH=src:. .venv/bin/python scripts/perp_krw_settle.py --venue demo --event EID
   ```
   - **Passes when** `refusals` is empty, `alt.orders` is 1, `alt.bought` equals the short, and `then` is "closing".
5. Look at the app's order history for PERP to check that the close was yours, then apply:
   ```bash
   PYTHONPATH=src:. .venv/bin/python scripts/perp_krw_settle.py --venue demo --event EID --apply --operator-checked-app
   ```
6. The next 5-minute run signs for it (`settle`). To finish at once, run `scripts/ops/run_perp_demo.sh` instead.
   - **Passes when:**
     - the event is `closed` with `close_reason` "outside";
     - its BTC is sold;
     - the app shows no conditional order left on PERP (a stop still listed is cancelled by that run);
     - `krw_outside` is 0 and `krw_outside_closes` is 1.

### 4. Clearing `manual_intervention` (report only)
An intent in `manual_intervention` cannot be made on purpose without a fault, so this drill runs the command's venue reads on a settled key and expects it to refuse:
```bash
PYTHONPATH=src:. .venv/bin/python scripts/perp_resolve_intent.py --venue demo --key krw-6758-HYPE-c0
```
- **Passes when** it exits 2 with refusals: the status is not one it clears, and the venue knows the key. Nothing is written.

### 5. Alerts that reach the phone
This drill needs the Telegram bot and the dead-man check of the operator steps before stage B. It uses a test env file and an empty test root, not the live user's.
1. Make the env file, mode 600, then write into it with an editor the three names `PERP_LIVE_TELEGRAM_BOT_TOKEN`, `PERP_LIVE_TELEGRAM_CHAT_ID` and `PERP_LIVE_DEADMAN_URL` (https). Never paste their values into a chat with an agent.
   ```bash
   install -m 600 /dev/null ~/.r0_alert_drill.env
   ```
   ```bash
   mkdir -p ~/r0_alert_drill
   ```
2. A failed run:
   ```bash
   PYTHONPATH=src:. .venv/bin/python scripts/perp_live_alert.py --env-file ~/.r0_alert_drill.env --root ~/r0_alert_drill --rc 1 --since-ms "$(date +%s%3N)"
   ```
   - **Passes when** the phone gets "R0 live: run failed rc=1: ..." and the last line shows `"telegram": "ok"` and `"ping": "no"`.
3. A run that ended rc 0:
   ```bash
   PYTHONPATH=src:. .venv/bin/python scripts/perp_live_alert.py --env-file ~/.r0_alert_drill.env --root ~/r0_alert_drill --rc 0 --since-ms "$(date +%s%3N)"
   ```
   - **Passes when** the phone gets "runs end rc 0 again ..." and the line shows `"ping": "ok"`.
   - The dead-man service then pages the phone when no ping arrives within its period. Wait for that page once.
4. Remove the test root. Keep or delete the test env file as you choose:
   ```bash
   rm -r ~/r0_alert_drill
   ```

## Not drilled on Demo
- **The shared Upbit page (item 5b)** has a writer only on the live root. It is drilled in stage B, when the live user makes `/var/tmp/perp_notice_share`.
- **The live venue's host guard and the read-only key** are drilled in stage B.

## Record
Record each drill's date and result here. Write statuses only, never an amount.

| Drill | Date | Result |
|---|---|---|
| 1. Stop fires | | |
| 2. Exit now | | |
| 3. Hand close and settle | | |
| 4. Clear `manual_intervention` (report) | | |
| 5. Alerts | | |
