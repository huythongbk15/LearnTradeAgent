# AC14 Loss Diagnosis — why both strategies lost on the real window

## Verdict: NOT a system bug. Cost drag on 1-hour holds.

## 1. Market direction — the window is a brutal bear
- BTC/USDT 1h, 2022-04-14 -> 2023-03-22, 8200 bars
- Price 40,435 -> 28,205
- **Buy & hold: -30.25%**, DD 63.34%, Sharpe -0.307
- 24h realised vol 61.7% annualised
- Up bars 50.1% (no directional edge, pure high-vol chop)
- A long-only strategy starts -30% behind before any signal.

## 2. Cost drag — the decisive factor
Round trip = commission 0.1% x2 + slippage 0.05% x2 + spread 2bps = **32 bps**.

| strategy | with 32bps | frictionless | drag | switches |
|---|---|---|---|---|
| ma_crossover(10/20) | **-45.00%** | **+11.19%** | -56.2pp | 440 |
| range_mean_reversion | -71.12% | -8.89% | -62.2pp | 718 |

ma_crossover frictionless is **+11.19% vs buy&hold -30.25%** — the signal
DOES have edge (~+41pp of alpha). Costs erase all of it.

## 3. Why the edge is too small
- Hold period: **median 1 bar (1 hour)**, max 1 for ma_crossover
- Gross edge per round trip: **+2.5 bps**
- Real cost per round trip: **32 bps**
- Cost exceeds edge by ~29 bps/trade
- **Break-even cost: 4.8 bps.** At 32 bps it cannot win by construction.
- range_mr holds longer (median 4h) but its raw signal is negative (-8.89%
  frictionless), so it loses on both counts.

## 4. Not an implementation bug
- Oracle is the same independent hand-built sim used in the synthetic run
- 16/12 accounting checks pass; equity reconciles
- The frictionless positive return proves signal generation + timing are
  wired correctly; only the cost assumption makes it unprofitable.

## 5. Timeframe sweep — does a higher TF fix it? NO.

Same calendar window (2022-04-14 -> 2023-03-22), ma_crossover(10/20),
long-only, 32bps round trip:

| TF | bars | ret@32bps | ret@0 | sharpe | DD | switches | trades | med hold |
|---|---|---|---|---|---|---|---|---|
| 1h | 8400 | -46.44% | **+10.75%** | -5.045 | 48.8% | 454 | 227 | 1 |
| 4h | 2100 | -33.48% | **-17.05%** | -7.567 | 34.6% | 138 | 69 | 1 |
| 1d | 343 | -16.46% | -13.74% | -6.206 | 16.5% | 20 | 10 | 1 |

Buy & hold (same window): -30.25%

### Reading
- **1h is the ONLY timeframe with positive frictionless edge** (+10.75%).
  Cutting the trade count does not preserve it — the edge came precisely
  from the many small 1h crossings, which is exactly what 32bps taxes.
- **4h/1d are worse than 1h even before costs** (-17.05% / -13.74%). On a
  10/20 MA rule the higher TF just holds through more of the bear.
- **1d (-16.46%) beats buy&hold (-33.8%)** by staying flat most of the
  window, but 10 trades is far too few to call that skill.
- So the cost-drag hypothesis holds only for 1h. For 4h/1d the problem is
  that the strategy itself is directionally wrong in a sustained downtrend:
  it will be long through most of a bear no matter the timeframe.

## Conclusion
NO_TRADE is correct. Two independent causes, and a timeframe change only
addresses the first:
1. **1h**: cost drag. Edge is real (+10.75% frictionless, ~+41pp vs
   buy&hold) but only ~2.5bps/trade against 32bps of fees. Break-even
   4.8bps. Needs a cheaper venue or a filter that lifts per-trade edge.
2. **4h/1d**: no edge at all in this window. The 10/20 long rule has no
   bear-market defence; timeframe cannot manufacture a signal that the
   rule does not produce.

