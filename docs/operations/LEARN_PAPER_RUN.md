# H-LEARN-11 forward paper log

A daily paper record of the candle model that passed H-LEARN-11 on old data. Paper only: public mainnet data, no key, no order. Research description, not advice; mainnet stays NO-GO.

## Why
On 2026-11-04 the operator asked for rules learned from candle parameters rather than scans of rules written by hand, and for work with short, medium and long horizons together. H-LEARN-11 (packet `data/learn_candle_20261104_01/`, git-ignored, `RESULTS.md`) passed its frozen primary: +15.6 bps per daily decision, t 2.40. A blind rebuild from the protocol gave +16.5, t 2.75. It is not validated:
- It is timing of the alt basket, not coin choice; market neutral it is -5.8.
- It rests on calendar times market state; without calendar features it is +4.1, t .68.
- The design was chosen after about 170k earlier statistics on the same data.

Only bars from 2026-10-26 on are unseen, so this log is the test.

## Book, frozen
Code: `tools/learnpaper/model.py` (features, model, thresholds) and `tools/learnpaper/run.py`. A change needs the operator's ruling on this page.
- **Coins:** LTC BCH XLM FIL AAVE ETC UNI ATOM ALGO XTZ VET THETA CRV COMP SNX SAND MANA AXS CHZ HBAR, USDT-M perps. No masked coin.
- **Decision:** the hourly bar opening at 23:00 UTC each day, first 2026-10-26 23:00. Features use that bar's close and earlier.
- **Model:** sklearn HistGradientBoostingRegressor with the protocol's settings, pooled over the coins. It is refit at each quarter start on every row since 2021-12-01 whose 24 h label ends by then. Each model file is recorded with its sha256, and a changed file is refused.
  - The 2026Q4 refit reproduces the study's last fold exactly: 460 of 460 October predictions match.
- **Position:** long if the prediction is above +20 bps (the round trip), short if below -20 bps, else flat. Held from the 00:00 open for 24 h.
- **Cost at the read:** 10 bps a side on each change of a coin's position. Funding is not counted.

## Data and schedule
- **Bars:** the store `data/learn_paper/bars/` was seeded once from the study's archive zips, bars before 2026-10-26 (`--init-from`).
- **Fetching:** each run appends closed hourly bars from `fapi.binance.com` through the NL-P allowlisted public client (`tools/newperp/fetch.py`).
- **Schedule:** `scripts/ops/run_perp_demo.sh` runs `python -m tools.learnpaper.run` every hour after the demo books. A decision is recorded once its entry bar (00:00) has closed, so from 01:00 UTC.
  - A run that was missed records every due day later, from the same bars, with no look-ahead.
  - The first run, on 2026-11-04, recorded 2026-10-26 to 2026-11-03 this way (a replay accepted as a first run).
- **Files:**
  - `data/learn_paper/ledger.jsonl` (append only: predictions, positions, entry opens, missing coins, model quarter);
  - `models/` with `manifest.json`;
  - `status.json` (counts only).

## Blinding
Compute no return, P&L or price change from `ledger.jsonl` or the bar store's forward bars before a look below. Counts and statuses are fine. A look outside these dates is recorded here, and the read is then reported as unblinded.

## Decision rules (ruled by merging this page)
The statistic is the daily pooled net: the mean over the 20 coins of position times the 24 h open-to-open return, less cost, with a flat coin counting 0. The t is a month-block bootstrap. The upper bound is its one-sided 95% bound.

| Look | Date | Rule |
|---|---|---|
| Futility | 2027-04-26 (about 180 decisions) | KILL if the upper bound is below 0; otherwise go on |
| Read 1 | 2027-10-26 | KEEP if mean > 0 and t ≥ 2.4; KILL if the upper bound is below 0; else UNRESOLVED |
| Read 2 | 2028-10-26 | same |
| Final | 2029-10-26 | same; UNRESOLVED here means not adopted |

The bar of 2.4 instead of 2 pays for four looks.

On the study's own numbers (Sharpe about 1.2 a year, which a real forward edge would likely fall short of), the expected t is about 1.2 at one year and about 2.1 at three years. KEEP before the final read is unlikely even if the edge is real. Months of daily numbers will show early whether the book trades as designed, but not whether it earns.

## Limits
- Funding is not counted; the study found it changed the result by under 1 bps.
- Fills are 1h opens, not a book.
- Survivorship: the coins were chosen as still listed in 2026.
- A KEEP would lead to a demo-trading book, not to money.
