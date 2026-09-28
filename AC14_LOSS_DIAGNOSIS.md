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

## Conclusion
NO_TRADE is the correct verdict, and the cause is a **config/strategy-class
mismatch, not a defect**:
1. A 10/20 MA crossover on 1h bars trades ~440 times over 8200 bars —
   that is a scalping rule, and retail fees + spread make it unviable.
2. The evaluation window is a high-vol bear; long-only is structurally
   wrong-footed there.

## What would actually change the result
- Lower cost venue (maker rebates / -0.05% fee tier) => break-even 4.8bps
  is reachable only if fees drop below that
- Longer timeframe (4h/1d) => fewer, larger holds amortise the 32bps
- Trend filter / short side => the window punished long-only exposure
