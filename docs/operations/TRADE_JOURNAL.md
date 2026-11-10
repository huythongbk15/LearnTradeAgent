# Trade journal

Status: CURRENT. In use from 2026-11-10.
Owner: operator
Verified: 2026-11-10 against `tools/journal/journal.py` and `tests/test_trade_journal.py`.

The operator trades real money by hand, outside the project. This journal records those trades and measures them forward. It answers what research on old data cannot: how the operator's own judgement does.

It is a measuring tool. It places no order, reads no account and uses no key. It is not a bot and gives no advice.

## Why
- **Operator's choice, 2026-11-10:** after an INJ round on 1m bars found no rule that pays (`data/inj1m_20261110_01`), the operator chose "Nhật ký lệnh của tôi": record trades by hand or from a screenshot, sizes included.
- **The account history** says size and leverage decided more than the signal (sizing study `data/sizing_20261109_01`), so the journal keeps both.
- **Design background:** `docs/proposals/SIGNAL_JOURNAL_DESIGN.md` (TARGET, 2026-10-31). This tool is its operator-trade part only, without system-detected signals.

## Commands
Run from a checkout with `PYTHONPATH=src:.`:

```bash
python -m tools.journal.journal add INJ short --entry 8.1867 --time 2026-11-09T19:49:44+07:00 --notional 47589.55 --lev 30 --sl 8.284 --tp 7.836 --liq 8.3255 --reason "..." --tags feel btc_down --book inj-20261109
```

- `add`: records one fill or position at entry. `--time` is ISO and UTC unless an offset is given; the venue screen shows UTC+7. Each fill of a position built in steps is its own record with a shared `--book`.
  - It also takes a snapshot of the coin and BTC from public bars that closed before the entry: returns over 15m, 1h, 4h, 1d and 7d; the residual against BTC with a 1-day beta; z over 240 and 1,440 minutes; 1-day volatility; distance to the 7-day high and low; funding.
  - `entry_vs_last` compares the fill with the last 1m close, which checks the time zone.
- `close ID --price P --how manual|sl|tp|liq|all|tool|other`: records the exit.
- `note ID "text"`: a correction or remark. Records are never edited; the chain makes an edit visible.
- `score`: adds the return at 15m, 1h, 4h, 24h and 72h, MAE and MFE over the first 72 hours or to the close, and which of SL, TP or the liquidation price was touched first. All in bps before costs. Run it any time; a trade past 72 hours, or closed with all its horizons read, is final.
- `status`: evaluates every open trade now. It shows:
  - the price, P&L and distance to each level;
  - the coin against BTC;
  - in its last 365 days of 1h bars, how often the coin reached the TP before the SL or liquidation level within 24h, 72h and 168h.
- `report` prints every trade; `report --sizes` adds notional. `verify` checks the hash chain.

## Storage and rules
- **The file:** `~/trading-journal-data/journal.jsonl`, outside git, owner-only (0600 in a 0700 folder). It is append-only and each record carries the hash of the one before. `JOURNAL_DIR` moves it.
- **Network:** public GETs only, through `tools/newperp/fetch.py` (one host, allowlisted paths, no credential).
- **Seal:** a coin masked by the operator's ruling (`tools/selection/config.SEALED`, the H-XSEC-01 cohort, for example BNB and NEAR) is recorded without market data. It is never scored or evaluated while the seal holds.
- **No schedule:** the journal runs only when called. A scheduled `score` would need its own task entry, which is the operator's decision.
- **Levels added later:** SL, TP or liquidation prices added after the entry describe the position as it is now. A `first_touch` before they were set is a description, not a stop that fired. Note this with `note`.

## Reading
- One reading is planned. It happens after 60 trades with a 24h outcome, or on the operator's call: mean and median return by horizon, by tag and by effective leverage, against the same coin's unconditional return over the same horizons.
- No cut by coin, hour or tag beyond what is written here. Most likely the answer is "cannot tell" for a long time; the record is the value.
- `status` odds come from the last 365 days, which may hold a trend (INJ fell for most of it), so they describe the past and are not a forecast.
