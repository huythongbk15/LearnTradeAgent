# L2 selection rule

**Status:** TARGET specification for D01. Freeze before any campaign is
run. Changing it afterwards is a review event and requires a new decision
record, the same rule the plan applies to thresholds.

Fills a gap `LIVE_READINESS_AGENT_PLAN.md` leaves open: it requires D01 to
*"freeze eligibility, ranking và tie-break trước campaign"* without saying
what any of those are. An unfrozen selection rule is how a five-trade
result becomes the live candidate.

---

## Why the rule has to be mechanical

Two candidates have been measured. Neither has an edge:

```
enhanced_ma         32 folds,  5 trades,  median return  0.000%
stat_arbitrage_lo   32 folds, 45 trades,  median return -0.029%
                    20/28 folds traded, 6/28 profitable, payoff ratio 0.50
```

Leaving the subject unset means the first person to reach L2 picks by
preference against those numbers. Every stage of this system has the same
history: a judgement made after seeing the result, then defended as a
design choice. The rules below exist to make that impossible rather than
discouraged.

---

## 1. Eligibility — evaluated before any campaign

A strategy may be campaigned only when all hold. Every item is measurable
from the repository before compute is spent.

| # | condition | check |
|---|---|---|
| E1 | registered on the canonical allowlist | `build_default_registry().has(id)` |
| E2 | resolvable to a source-hashed adapter | `registry.get(id, env)` succeeds |
| E3 | emits signals on the target data | ≥1 signal over the first fold's test window |
| E4 | window is long enough to hold the strategy's slowest feature | test window bars ≥ descriptor warmup |
| E5 | fold structure reaches the gate's minimum | `fold_count` ≥ gate `n_min` |
| E6 | enough windows contain a trade | ≥50% of trading folds non-empty, measured by probe |
| E7 | data provenance is declared | one canonical input path with sha256 |

E4–E6 exist because the measured candidates failed precisely here.
`enhanced_ma` produced trades in 5 of 23 folds; `stat_arbitrage_lo` in 20 of
28. E6 is the machine form of that observation.

A strategy failing any of these is **not run**. Running it produces a
number that cannot satisfy the gate, at full compute cost.

---

## 2. Ranking — order of campaigns, decided before running

Applied to eligible candidates only. **No holdout or campaign outcome
enters this step**, because none exists yet.

Primary key, in order:

| rank | key | direction | rationale |
|---|---|---|---|
| 1 | `fold_count` | descending | more folds resolves the spread gate better |
| 2 | `trading_window_share` | descending | more windows that trade gives the gate something to count |
| 3 | `median_trades_per_window` | descending | directly evidences the cost-adjusted edge |
| 4 | `max_feature_lookback_bars` | ascending | shorter lookback suits a shorter test window |

Ranks 1–2 dominate because they decide whether the gate is answerable at
all. A strategy with better returns but 4 usable folds ranks below one with
12, because the 4-fold campaign cannot produce a verdict.

---

## 3. Tie-break — deterministic, pre-registered

Applied when two candidates share all four ranking keys, in this order:

1. Lower `median_trades_per_window` wins — fewer trades means the result is
   less dependent on a single fill.
2. Shorter `max_feature_lookback_bars` wins — less to go wrong at the
   window edge.
3. **Lexicographic on `strategy_id`.** Final and arbitrary on purpose.

The third tie-break exists so the rule is total. A rule that can end in a
draw is a rule that gets resolved by preference later.

---

## 4. Abstention — mandatory

If no candidate is eligible, or every campaigned candidate fails hard
gates:

- record `NOT_QUALIFIED` with the reason per candidate
- do **not** open L3 or L4
- do not re-rank using outcome, and do not adjust the gate threshold
- propose a new experiment in D01's successor, with its own freeze

This is the plan's rule (`Nếu tất cả fail: lưu NOT_QUALIFIED và đề xuất
experiment mới`) stated so it cannot be satisfied by loosening a
threshold after seeing a result.

---

## 5. What selection may not use

- campaign outcomes, holdout results, or any figure from a run
- a threshold chosen after seeing an outcome
- fold count or trade density measured on a *different* symbol or window
  than the one being campaigned

The last is the trap this repository already fell into twice: the 852-cell
review concluded SOL was the good symbol from hourly data, and the daily
campaigns then disagreed. A ranking key measured elsewhere does not
transfer.

---

## Machine form

`scripts/select_l2_candidate.py` evaluates eligibility and ranking from
the repository and emits a D01 fragment. It takes no campaign results as
input — the signature makes that structural rather than a promise.

```bash
.venv/bin/python scripts/select_l2_candidate.py \
    --symbol BTC/USDT --timeframe 1d --train-months 12 \
    --val-months 2 --test-months 2 --step-months 2
```

Output is the eligibility table, the ranking, and either a chosen
`strategy_id` or `NOT_QUALIFIED` with per-candidate reasons.

Tests in `tests/test_l2_selection_rule.py` cover each eligibility item by
constructing a candidate that fails exactly that condition, and verify the
selector rejects any input that carries campaign outcomes.

---

## Relationship to the contract

`CAMPAIGN_EVIDENCE_CONTRACT.md` decides whether a campaign's numbers may be
cited. This document decides which campaign to run. Together they mean L2
cannot start on a hunch and cannot accept a number it cannot re-derive.

The thresholds referenced here — gate `n_min`, `p_max`, trade-density
floor — remain unresolved in `GATE_SEMANTICS_AND_RERANK.md`. The plan puts
that resolution at L1, before any campaign. This rule assumes they exist and
states that E5, E6 and the abstention path must be re-read once they are
set, because they are expressed in those terms.
## Recorded first run

`scripts/select_l2_candidate.py` on BTC/USDT 1d, 12/2/2/2, window 60 bars:

```
strategy                      folds  trade%  medTrd  eligible
cross_sectional_momentum_lo     32     88%      3   no  (E4)
cross_sectional_momentum_ls     32     88%      3   no  (E4)
rsi                            32     84%      2   YES
bbands                         32     69%      2   YES
stat_arbitrage_lo               32     69%      2   YES
enhanced_ma                    32     41%      1   no  (E6)
```

RESULT: campaign `rsi` first. D01 fragment at `data/l2_selection.json`.

**The two cross-sectional strategies fail E4 by one bar.** Their descriptor
warmup is 61 and a 2-month daily test window is 60. At 12/3/3 the window is 90
bars and both become eligible; at 12/3/2 it is 90 bars with 31 folds and they
also clear E5.

That is a one-bar margin, and it is the exact shape of the outcome this rule
exists to discipline: after reading the table, it is tempting to widen the
window and reclaim the two highest-ranked strategies. Per §4 and §5 of this
document that is a review event requiring a new decision record before any
campaign runs, not an edit after the fact. The fold structure is part of what
D01 freezes, and it is frozen with the observation above rather than after it.

It also shows the rule is not theatre. Ranking on trades-per-window alone
would have put the cross-sectional strategies first and sent 20 hours at
strategies that cannot satisfy the gate on this window.
