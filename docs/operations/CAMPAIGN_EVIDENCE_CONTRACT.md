# Campaign evidence contract (L1.4)

**Status:** TARGET specification. No validator exists yet, and none should
be claimed. This defines what a walk-forward campaign must record so that
L1.4 and L2 can verify a result rather than trust it.

Distinct from §7 of `LIVE_READINESS_AGENT_PLAN.md`, which specifies the
bundle a *task* produces. A campaign is a measurement, and a measurement
that cannot be re-derived is not evidence. Three campaigns in this
repository produced numbers nobody could reconcile, and the reason was
identical each time: the output recorded a status string and bound nothing.

---

## Why this exists

| produced | recorded | missing |
|---|---|---|
| `campaign_summary.json` | `{"status": "hard_gates_failed"}` | which revision, which data, which folds, which cost schedule |
| fold artifacts | return, trades, Sharpe | fold identity, so the same fold cannot be distinguished from a neighbour |
| policy scores | return, trades, folds | which run produced them, so a score cannot be traced to a measurement |

Every gap is the same shape: a field existed and was never checked for
being true. `POLICY_RETURN_AUDIT.md` found 2,690 promoted policies whose
scores matched no source code. This contract is the same check applied
upstream, where it is cheap.

---

## Required fields

### 1. Identity

```
schema_version          integer, incremented on any field removal or semantic change
campaign_id             content hash of (1.2, 2.3, 3.1, 4.1)
subject                 {strategy_id, symbol, market_type, timeframe, params_hash}
```

`params_hash` is a hash of the selected parameter set, not the grid. Two
campaigns that differ only in grid must not collide.

### 2. Code binding

```
code_commit             exact commit of the tree that produced these numbers
tree_fingerprint        sha256 of the relevant source subset
dirty_diff_sha256       hash of the uncommitted diff, or null when clean
gate_version            identifier of the gate set that judged the result
```

A campaign on a dirty tree is admissible only if the dirty diff is
recorded. A header SHA is not sufficient — the same reasoning as §7.

### 3. Data binding

```
data_manifest           sha256 over the resolved OHLCV window
input_path              which file was actually read
input_rows              row count in that file
window                  {start, end, bars, timeframe, market}
cutoff_policy           how the end was chosen: fixed, as_of, or trailing
gaps                    [{start, end, bars_missing, classification}]
```

`input_path` is not optional. Three hourly files exist for every symbol
with different coverage, and campaigns have measured a window 46% shorter
than the same symbol had available without recording that they did.

`gaps` must classify, not just count — missing bars, exchange halt, and
instrument listing are different facts and produce different evidence.

### 4. Cost schedule

```
cost_schedule           {commission_bps, slippage_bps, spread_bps, borrow_bps?, fx_bps?}
cost_source             constants, file, or venue-derived
round_trip_bps          computed value, so the arithmetic is auditable
```

The schedule is part of the result, not a parameter of the run. A margin
cleared at one cost schedule and not another is a different result.

### 5. Fold identities

```
folds                   [{fold_id, train_span, val_span, test_span,
                          purge_bars, embargo_bars, overlap_group}]
fold_count              integer
overlap_policy          "independent" | "overlapping", with the group id
```

`overlap_group` is required whenever a step is shorter than the test
window. Two folds sharing bars are not independent trials, and any
statistic that assumes independence — including the spread gate's binomial
test — is optimistic without saying so.

### 6. Per-fold outcome

```
results                 [{fold_id, trades, gross_pnl, net_pnl, fees,
                          slippage, return_pct, sharpe, max_dd_pct,
                          cost_cleared: bool, entry_price, exit_price}]
aggregate               {median_return_pct, median_sharpe, total_trades, ...}
trades                  per-trade rows, or null with an explicit reason
```

`trades: null` is allowed and must carry a reason. The payoff script
built in this session had to fall back to fold-level ratios because trades
were absent, which produced a break-even constant that was wrong — the
per-fold and per-trade hit rates differ, and only one of them was measured
at the right population.

### 7. Verdict

```
verdict                 PASS | FAIL | INCONCLUSIVE
failed_gates            [{gate_id, observed, unit, threshold, comparison}]
gate_set_fingerprint    hash of every threshold the run judged against
```

`failed_gates` names each one. `"status": "hard_gates_failed"` does not.

---

## Admissibility rules

A campaign is admissible as L2 evidence only when all hold:

1. `code_commit` matches the revision L2 is run at, or the diff is recorded
2. `data_manifest` resolves against the raw files
3. `input_path` names one file, and its row count matches `input_rows`
4. `cost_schedule` is populated and `round_trip_bps` recomputes
5. every `fold_id` is unique and appears in `results`
6. `overlap_policy` is declared, and non-independent folds carry a group id
7. `trades` present, or `null` with a reason
8. `verdict` is derivable from `failed_gates` and `gate_set_fingerprint`

Rule 8 is the one that would have caught this repository's history: a
verdict that cannot be recomputed from the recorded thresholds is an
assertion, not a measurement.

---

## What this does not settle

**The thresholds themselves.** p≤0.20, n≥14, and the trade-count floor
were chosen as judgement and recorded in `GATE_SEMANTICS_AND_RERANK.md`.
This contract requires them to be *recorded and fixed before* a campaign
runs, which is what makes a later change a review event rather than a
tune-to-fit. It does not say they are correct.

**Overlapping folds.** A contract can require the overlap group be
declared. It cannot make 32 overlapping folds into 32 independent trials.
That is a methodology decision for L1.

---

## Implementation order

1. `campaign_integrity.py` — extend the coverage check added in
   `54906f6` to verify these eight rules against a campaign directory
2. `build_campaign_policy.py` — emit every field above alongside the
   existing summary
3. A test per rule, each failing against a bundle with that field removed
4. Point L2's selection rule at `campaign_id` so a subject is chosen from
   admissible campaigns only

Item 3 matters most: a contract with no test is a document, and the last
three contracts in this repository were all documents until something read
them.