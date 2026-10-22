# BNB native ADX25 — bottleneck review — 2026-10-20 (rev2, corrected)

**rev2 supersedes rev1 of the same day.** Reviewer found four defects in rev1's
analysis; all four are corrected here and the affected claims are retracted.
No new market run, no holdout read, no policy/gate/risk change, no live action.

Source identity (before == after, unchanged across both revisions):
`tree_fingerprint = 1b1650196a8907ebc7c5cbf354b0761d11a2c112fa8b9afd14d3adb46927d21a`,
`code_commit = 4078892d738790872dc2d4f494e3973a62612347`, `worktree_dirty = true`.

> **Source-identity caveat (added 2026-10-21, independent review round 1).**
> The measurements in this document were produced under source `1b165019…`. The
> manifest of this packet now declares a later identity, because the packet was
> re-sealed when the scoped-candidate work landed. The three analysis scripts
> here import only `trading_agent.strategies.enhanced_ma.MaAdxCrossover`
> (`enhanced_ma.py` is unchanged against HEAD) and `layer_accounting.py` imports
> no `trading_agent` module at all, so the results are *plausibly*
> source-stable — but that is a static argument, not a re-execution.
> **Re-executing the three scripts on the frozen pre-holdout input and
> diffing the output against this document is still OPEN.**
No gate file touched; the AC01–AC15 receipt from rev1 still binds this identity.

---

## 0. What was wrong in rev1 (and what replaces it)

| # | Defect in rev1 | Evidence | Correction in rev2 |
|---|---|---|---|
| D1 | **SELL gate direction inverted.** `_split()` used `trend_up` for BUY *and* SELL; the candidate's SELL gate requires `NOT trend_up`. | rev1 reported "33 down-crossovers fail ADX" — but the campaign recorded **33 SELL actions**. The inverted branch had counted SELL-gate *passes* as ADX failures. | Correct gates: BUY = `adx>25 AND plus_di>minus_di`; SELL = `adx>25 AND plus_di<=minus_di`. |
| D2 | **Rejection buckets mislabelled and not a partition.** "rejected_adx_le_25" actually counted DI failures and vice versa, and the "both fail" case fell through uncounted. | rev1 BUY showed 35+2+6 = **43 against 48 raw**; SELL showed 33+2+3 = 38 against 47. | Explicit 4-way partition (pass / fail-ADX-only / fail-DI-only / fail-both) with an **assertion that the buckets sum to the raw count** in every window and direction. |
| D3 | **Count matching is not per-bar proof.** rev1 validated 10/10 per-window counts. | The counts are **identical under both candidate alignments**, so the check could not detect an off-by-one. Per-bar validation of the recorded action vector exposed 388/15 120 bar mismatches under rev1's alignment. | Primary gate is now the **full per-bar action vector** from `execution_funnel.records` of all 14 completed cells: **15 120 bar-level assertions, 0 mismatches**, with the alignment *derived* by scanning candidate shifts (exactly one shift is clean). |
| D4 | **"Arithmetically impossible" claimed from a linear extrapolation.** | A mean density extrapolated over window length does not bound every feasible geometry, and the companion "≈32 if the filter is removed" number does not support an impossibility claim either. | No extrapolation and no impossibility claim. Densities are reported **only on the measured windows**; the count for any longer, unrun window is stated **UNKNOWN**. |

A fifth item from the reviewer — that 41 signal bars and 17 closed trades are
different layers — is adopted: rev2 labels four layers explicitly and never
substitutes one for another.

**Alignment, corrected and now derived from data (not assumed).**

```
action at execution_bar j  ==  full_frame_signal[j - 2]
```

Two one-bar lags compose: (1) `canonical_signal_series` decides on the window
that ends *strictly before* bar j, so `canonical_signal[j] == full_frame_signal[j-1]`;
(2) the simulator executes a signal one bar later than its index. The recorded
`decision_bar` is the *canonical signal index* (`execution_bar - 1`), **not** the
bar on which the MA crossover occurred (`decision_bar - 1`).

Shift scan over all 15 120 bar records:
`shift 0 → 388 mismatches · shift 1 → 388 · shift 2 → 0 · shift 3 → 388`.
Shift 2 is unique. rev1's shift-1 derivation was wrong.

The chain is now locked by synthetic regression
(`tests/test_indicator_window_equivalence.py`, **3 passed**, no market data),
which asserts both (a) the simulator's action at bar j comes from signal index
j−1 and (b) outside the warmup region that is crossover bar + 2.

---

## Round 1 — Current state verification and blocker map (unchanged by rev2)

**Question.** Does the tracker state match the artifacts on disk, and is the
candidate's source / data / process identity the one the evidence was produced
under?

**Result.**
- Tracker `docs/CAMPAIGN_STATUS.md` (Current 2026-10-20) agrees with all three
  RESULTS.md: 14/15 cells, 4–7 trades per cell, gate 30 unchanged, holdout
  incident recorded, old freeze stale, P2 sensitivity open.
- **Source drift — one file.** Audits recorded `tree_fingerprint 6f5bd4f4…`;
  current is `1b165019…`. Every file in `src/` and `scripts/` has mtime ≤
  2026-10-19 23:18:45, i.e. *before* the 2026-10-19 23:22:02 snapshot, and the
  three individually hashed files match their recorded SHA256 exactly. The single
  exception is `scripts/run_wfo_validation.py`, rewritten 2026-10-20 10:34 by the
  earlier research session — a research-analysis script, **not** the
  qualification entrypoint and **not** under `src/`.
  `src/trading_agent/strategies/enhanced_ma.py` (the candidate's signal source)
  is unchanged vs HEAD since 2026-10-14.
  ⇒ BNB native evidence identity for `src/**` is intact; the whole-tree
  fingerprint is stale and any new packet must record `1b165019…`.
- Input binding verified: `data/existing_research_audit_20261008/BNB_USDT__4h__pre_holdout.parquet`,
  sha256 `379677360902c9734780e496398abb02ea6390531b840e2262bd9d7881e6485f`
  — matches the campaign protocol exactly. 6795 rows, all `is_closed`,
  **pre-holdout** (no reserved holdout bars in the file).
- No campaign or research process running.

**Classification.** Evidence/provenance finding (5).

**Blocker map.**
| # | Blocker | Status after rev2 |
|---|---|---|
| B1 | Per-cell trades 4–7 « gate 30 | **Cause re-measured correctly** — Round 2 |
| B2 | Overlapping windows inflate apparent sample | **Confirmed and quantified** — 68 occurrences → 41 unique |
| B3 | Geometry: 6795 bars cap validation length | Confirmed (unchanged from the feasibility audit) |
| B4 | Raw-forecast rejection rate unknown | **Closed for the crossover path** — Round 2 |
| B5 | P2 sensitivity preregistration | Open — untouched |
| B6 | Holdout-access incident | Recorded; no untouched claim made |
| B7 | Old freeze stale after dispatcher fix | Confirmed; not resumed |

---

## Round 2 — The core question: geometry-bound or policy-bound?

**Question (falsifiable).** Is the low trade count caused by (a) sparse MA20/80
crossover *events*, or (b) the ADX>25 + DI-alignment *filter* rejecting events
that did occur?

**Hypotheses.** (H1) policy-bound — many crossovers occur, the filter kills
them. (H2) geometry-bound — crossovers themselves are rare. (H3)
execution/authority-bound — signals exist but are vetoed downstream.

**Smallest check (no market run).** Recompute the candidate's own indicators on
the hash-bound pre-holdout prefix, restricted to the already-declared exposed
scope `[0,5600)`, and require the recomputed **per-bar action vector** to
reproduce the action vector recorded by the failed campaign before trusting any
decomposition.

**Method.** The candidate's own class (`MaAdxCrossover`) with the frozen params
`fast 20 / slow 80 / adx_period 14 / adx_threshold 25.0`; no re-implementation.
`ma_adx` `warmup_bars = 100` exceeds the 40-bar maximum indicator dependency and
every indicator is a non-recursive `rolling_mean`, so full-frame evaluation is
numerically identical to the native per-bar window evaluation above warmup.

**Validation gate: PASS.** 15 120 bar-level assertions across all 14 completed
cell reports, **0 mismatches**, alignment derived uniquely. The action vector is
also **identical across all cost variants** of a window (1x / 2x / slip_stress),
as it must be since signals are cost-independent.

### Corrected decomposition (aligned range = the crossovers that can produce an action inside the window)

| Window | UP raw | UP pass | fail ADX only | fail DI only | fail both | DN raw | DN pass | fail ADX only | fail DI only | fail both | recorded actions B/S | closed trades |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| [2360,3440) | 12 | **7** | 4 | 0 | 1 | 12 | **9** | 1 | 0 | 2 | 7 / 9 | 7 |
| [2900,3980) | 8 | **6** | 0 | 0 | 2 | 8 | **7** | 1 | 0 | 0 | 6 / 7 | 6 |
| [3440,4520) | 11 | **8** | 1 | 0 | 2 | 11 | **7** | 3 | 1 | 0 | 8 / 7 | 6 |
| [3980,5060) | 10 | **8** | 1 | 1 | 0 | 10 | **6** | 3 | 1 | 0 | 8 / 6 | 5 |
| [4520,5600) | 7 | **6** | 0 | 1 | 0 | 6 | **4** | 1 | 1 | 0 | 6 / 4 | 4 |
| **total** | **48** | **35** | **6** | **2** | **5** | **47** | **33** | **9** | **3** | **2** | 35 / 33 | 28 |

Every partition sums exactly to its raw count, and every window's `gate_pass`
equals the recorded action count for that direction (`gate_pass_equals_recorded_actions`
= true for all 5).

**Deduplicated union** (aligned crossover range [2358,5598) = 3240 bars):

| | raw crossovers | gate pass | fail ADX only | fail DI only | fail both |
|---|---:|---:|---:|---:|---:|
| up | 30 | **21** | 5 | 1 | 3 |
| down | 29 | **20** | 5 | 2 | 2 |

68 signal occurrences summed over the 5 overlapping windows → **41 unique signal
bars** (21 BUY + 20 SELL) → **27 overlap duplicates**.

**Event supply inside each window's own bar range** (raw crossovers, window-local):
up 12 / 8 / 11 / 10 / 7 (mean 9.6), down 12 / 9 / 11 / 9 / 6 (mean 9.4).

### Layer accounting — each rung on its own scope (rev3 correction)

The rungs below are **not** a single chain: L1/L2 are deduplicated over the
union, L3–L5 are summed over five **overlapping** windows, and cost scenarios
are alternative assumptions that must not be pooled. The 1x column is missing
one cell (fold004/1x timed out and was not estimated), so it is not
comparable with 2x/slip_stress.

| Layer | Quantity | Scope | Value |
|---|---|---|---|
| L1 | raw MA crossovers | union, deduplicated | **59** (30 up + 29 down) |
| L2 | gate-passing signals | union, deduplicated | **41** (21 BUY + 20 SELL) |
| L3 | simulator gate actions | 5 overlapping windows, one cost | **68** (35 BUY + 33 SELL) |
| L4 | runtime BUY fills | same | **30** (35 BUY actions − 5 `already_long` skips) |
| L5 | closed trades | same | **28** (30 fills − 2 carry-out positions) |

Corrections carried by this table:
- **33 is not the total gate actions** — that is 68 (35 BUY + 33 SELL). 33 is
  the SELL half of L3.
- **28 is not the deduplicated independent trade count.** It is the sum of
  closed trades over five overlapping windows. The sample-feasibility audit's
  deduplicated figure for the same scope is **17 unique closed events**
  (deduplicated by symbol/side/entry/exit time, separately per cost).
- 41 (L2) and 28/17 (L5) are different layers: 41 counts *signal bars*, 28 and
  17 count *completed round trips*. Neither replaces the per-cell 30-trade gate.

Source: `layer_accounting.py` in this packet (`round6_layer_execution.json`);
the L4/L5 identity `runtime BUY fills − carry_out == closed trades` holds for
every cost scenario.

### Result

- **The filter is a minority effect, and it is symmetric.** Across the union the
  gate rejects 9 of 30 up-crossovers (30 %) and 9 of 29 down-crossovers (31 %).
  On the BUY side the rejections split 5 ADX-only / 1 DI-only / 3 both.
- **rev1's "70 % of down-crossovers fail ADX (trend exhaustion)" is retracted.**
  It was an artefact of the inverted condition. Corrected: down-crossovers pass
  the SELL gate at essentially the same rate as up-crossovers pass the BUY gate
  (20/29 vs 21/30 at union level; 33/47 vs 35/48 across the windows).
- **H3 is narrowed, not stated as a blanket rejection.** The recorded gate
  action vector is reproduced bar-for-bar from the candidate's own indicators,
  so the *signal generation* stage loses nothing. The precise statement is
  narrower: **in the 14 checked reports, BUY actions that reached the runtime
  were not rejected by authority** — the BUY terminals are `already_long` and
  `runtime_submitted` only. This does **not** mean every signal becomes a
  trade: `already_long` skips and `flat_exit` discards are downstream skips,
  and only the fills that became closed round trips are trades.
- **What the decomposition does and does not establish.** It quantifies the
  *filter's* contribution to the low action count. It does **not** bound what a
  longer, unrun validation window would contain, and no such bound is claimed.
  The per-cell 30-trade gate is still failed by every measured cell, and the
  reason it is failed is still **sample size** — but the arithmetic that
  rev1 used to claim impossibility is withdrawn, and the trade count of a longer
  window is **UNKNOWN**.

**Classification.** (3) Sample/data inadequacy for the observed cells; (2)
policy semantics contributing a minority of rejections; (6) UNKNOWN for any
window that has not been run.

---

## Round 3 — Event availability for the operator's policy choice (measured only)

**Question (falsifiable).** Does a *state-entry / re-entry* policy have more
entry events available than the current crossover candidate on the same
measured windows? (Event availability only — **not** a candidate evaluation.)

**Method.** Count maximal contiguous runs of
`plus_di > minus_di AND adx > threshold` using the candidate's own indicator
columns, inside each window's own bar range. One entry per episode is the
**upper bound** for a state-entry policy. No returns, no Sharpe, no trades, no
backtest, no strategy code changed.

| Window | episodes @ADX25 | trend-state bars % | mean episode bars |
|---|---:|---:|---:|
| [2360,3440) | 35 | 39.1 % | 12.1 |
| [2900,3980) | 35 | 35.0 % | 10.8 |
| [3440,4520) | 37 | 34.0 % | 9.9 |
| [3980,5060) | 39 | 33.3 % | 9.2 |
| [4520,5600) | 38 | 38.0 % | 10.8 |

Measured range **35–39 episodes per 1080-bar window** (mean 36.8), against
**7–12 raw up-crossovers** for the current candidate. At ADX20 and ADX15 the
episode counts are higher still, so the comparison is not knife-edge on the
threshold.

**Limits (mandatory reading).**
1. Measured on the measured windows only. The episode count of any longer, unrun
   window is **UNKNOWN**. No projection table is offered and no claim is made
   that any geometry would or would not clear the 30-trade gate.
2. Episodes are an **upper bound** on entries (one per episode). Realised closed
   trades depend on authority, protective stop and flat-time, and a SELL gate
   action in a long-only strategy is frequently a flat exit rather than a close.
3. **No economic claim.** Availability says nothing about profitability; a
   state-entry policy over these episodes may be strongly negative.
4. Re-entry inside one episode would raise the entry count further, and the cost
   drag with it.
5. Event-availability measurement on already-exposed data. Not a backtest, not a
   candidate evaluation, not a preregistration.

**Classification.** (6) UNKNOWN on economics; (3) informative on sample
availability, measured not projected.

---

## Round 4 — Verification

- Synthetic regression `tests/test_indicator_window_equivalence.py`: **3 passed**
  — canonical per-bar window equals full-frame shifted by one; the shift is
  observable (non-vacuous); the simulator action bar equals the crossover bar
  +2 outside warmup. Synthetic prices only, no market data, no parquet, no
  storage read.
- `scripts/run_acceptance_ac01_15.py`: **rc 0, AC01–AC15 ALL PASS** (AC14 12/12,
  AC15 11/11). Identity captured before and after and identical. No gate file
  touched in either revision, so the rev1 AC receipt continues to bind.
- Ruff clean; docs `git diff --check` clean.
- Analysis scripts: `decompose_crossover_bottleneck.py` (rev2),
  `trend_state_event_availability.py` (rev2, measured-only).

### Self-critique
- Wrong dataset? No — the exact `input_binding` path+hash from the failed
  campaign's protocol, re-verified at run time.
- Holdout read? No — the file is pre-holdout; analysis restricted to
  `[0,5600)`, the scope already declared as exposed. `holdout_rows_read = 0`
  in every receipt.
- Trades/events double counted? Explicitly corrected: 68 occurrences → 41 unique
  signal bars (27 overlap duplicates removed). Costs are never pooled; the
  action vector is identical across costs, as it must be.
- Unrealized inventory called realized PnL? No PnL is reported in this review.
- Simulator-gate signal confused with raw forecast posterior? No — scope field
  set to `simulator_gate_signal_not_raw_forecast_posterior`; raw crossovers are
  reconstructed from the candidate's own indicator code and validated per bar.
- Order snapshot confused with lifecycle fill? Not applicable.
- Policy changed while calling it a bugfix? No — no threshold, sizing, authority
  or protection touched; the state-entry policy was **not implemented**, only
  counted.
- Losing window or failed attempt dropped? No — all 5 windows and all 14
  completed cells are represented; the missing fold004/1x cell is not estimated.
- Frozen identity consistent? Yes — source identity, data hash and protocol
  fingerprint recorded in the receipts.

---

## Terminal verdict

**RESEARCH_HOLD / NOT_QUALIFIED for the native BNB/USDT 4h ADX25
event-crossover candidate.** The candidate is not qualified and this review
does not qualify it. What changed in rev2 is the *quality of the reasoning*,
not the direction of the verdict.

- **Proven.**
  1. The recorded gate-action vector of all 14 completed cells is reproduced
     **bar-for-bar** (15 120/15 120) from the candidate's own indicators, with
     the action alignment derived from data (crossover bar + 2) and locked by a
     synthetic regression. The measurement is sound.
  2. The ADX/DI filter is a **minority** effect and is **symmetric**: it rejects
     30 % of up-crossovers and 31 % of down-crossovers on the union. It is not
     what makes the cells fail.
  3. Therefore the low per-cell trade count is **sample size**, and the gate
     30 is failed by every measured cell for that reason.
  4. Overlap inflation quantified, **with each rung on its own scope** (see the
     layer table below — these numbers are not a single chain and must not be
     added to one another).
  5. `src/**` identity for this candidate is intact; the whole-tree fingerprint
     drift is one research script.
- **Refuted.** "Relax the ADX/DI filter to get more trades" (falsified: it
  would recover only ~30 % of raw crossovers). "The signal stage loses
  actions" (falsified: the action vector matches bar-for-bar). "Authority
  rejects BUYs" (falsified *for the BUY path in the checked reports* — every
  BUY that reached the runtime was submitted; the observed skips are
  `already_long` and `flat_exit`, which are position-state skips, not
  authority vetoes). "A campaign is still running" (falsified). "The
  candidate's source changed" (falsified for `src/**`). "The candidate is
  arithmetically incapable of ever passing the gate" — **withdrawn**; it
  rested on an extrapolation and no such bound is supported.
- **Still UNKNOWN.** The trade count of any longer, unrun validation window;
  whether a state-entry / re-entry policy is profitable; the P2 sensitivity
  preregistration, still open for any full qualification.
- **Candidate may proceed to:** nothing beyond further analysis. It is closed
  at this evidence stage. It may **not** be re-run with relaxed thresholds,
  resized, or re-labelled, and it cannot inherit evidence into a new policy.

## Next decision required from the operator

**Path A — keep event-crossover, obtain eligible observations.** Needs
additional eligible history or an approved prospective observation plan. On the
present bars every measured cell is ~4–7 trades against a 30-trade gate, and
whether more history closes that gap is **UNKNOWN** — it must be established by
measurement, not by extrapolation. New data acquisition is outside this scope.

**Path B — authorise a separate state-entry / re-entry candidate.** Measured
event availability on the same windows is 35–39 trend episodes per 1080 bars
versus 7–12 raw up-crossovers for the current candidate. That is a fact about
availability on the measured windows, **not** a projection and **not** a
profitability claim. It would still be a **new candidate**: own contract
(entry/exit/stop/re-entry/cooldown), own preregistration (data/window/folds/
cost/sensitivity/gates/budget/multiple-trial accounting), synthetic contract
tests first, and **no inheritance** of this candidate's evidence. Expected
profitability is unknown and may be negative.

**Path C — stop.** Keep the candidate closed and the exposure / holdout history
recorded, and spend no further budget.

Recommended next bounded experiment if Path B is chosen: write the state-entry
contract and validate it on synthetic fixtures **before** any market budget is
proposed.

## Evidence

| File | Content |
|---|---|
| `decompose_crossover_bottleneck.py` | rev2 method (reproducible) |
| `round5_execution.json` | rev2 controlled receipt (rc 0, PASS, 15 120/15 120) |
| `trend_state_event_availability.py` | rev2 measured-only event availability |
| `round3_execution.json` | controlled receipt (rc 0, PASS) |
| `round2_execution.json` | rev1 receipt, retained for history (superseded) |
| `acceptance_receipt.json`, `acceptance_stdout.txt` | AC01–AC15 rc 0 |
| `source_identity_before.json`, `source_identity_after.json` | identical |
| `tests/test_indicator_window_equivalence.py` | synthetic regression (3 passed) |

No new market run. No holdout read. No policy, gate, sizing, authority or
protection change. No live or paper action. No publish, push or merge. Old
packets untouched.
