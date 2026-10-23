# Preregistration — `di_adx_state_edge` (single preregistration)

Status: **PREREGISTRATION — NOT a run authorisation.**
One preregistration, one packet. Prepared for independent review; execution
requires a separate, explicit scope/budget approval (§9).

Scope: **inner validation only**, on the same geometry and the same windows as
the frozen `ma_adx` control, so that a difference in outcome is attributable to
the **policy** and not to a changed window.

No market run, no campaign, no data load, no holdout read, no gate change, no
registry publication, no live or paper action has been performed.

---

## 0. Three identities, kept apart

A change to one is not a change to the others. Earlier revisions treated the
source fingerprint as if it covered the whole proposal; that was wrong, and the
reviewer was right to separate them.

| Identity | Covers | Changes when | Frozen value (current revision) |
|---|---|---|---|
| **A — source identity** | the code that will execute: `src/**/*.py` + `scripts/**/*.py` (what `capture_campaign_source` hashes) | any source or script changes | `tree_fingerprint c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74`, `dirty_diff_sha256 1865dc28229abb82fd28eab83c2e29d33901067a3b8896937954ddf68d5685ee`, `code_commit 4078892d738790872dc2d4f494e3973a62612347` |
| **B — experiment / preregistration identity** | the frozen decisions: candidate, parameter set, data binding, fold plan, cost set, gates, sensitivity rule, budget, terminal outcomes, scope — the §12.1 payload | any of those decisions changes | see §12.1 (`preregistration_fingerprint`) |
| **C — evidence manifest** | the bytes of the evidence packet: preregistration text, contract, receipts, scripts, results, the sealed assertion-file hashes (`evidence/assertion_files.json`), and the archived manifests under `revisions/` — identity A does **not** cover `tests/`, so the assertion files are pinned here instead | any packet file changes | the three `manifest.json` files |
| **C — evidence manifest** | the bytes of the evidence packet: preregistration text, contract, receipts, scripts, test files, results | any packet file changes | `data/bnb_di_adx_stateedge_20261020_01/manifest.json` (+ `manifest.json` of the prereg packet), regenerated per revision |

Rules that follow from the separation:

- A change in **A** invalidates **B** and **C** and requires a re-freeze and a
  re-verification of the identity.
- A change in **B** (a decision) invalidates **C** and requires a new
  preregistration revision; it does **not** change **A**.
- A change in **C** is **not automatically bookkeeping.** If a report, a
  receipt, a verdict or any logged output changed, then **the evidence
  changed**: the affected part must be re-verified and re-reviewed, and the
  revision recorded. Only a change that touches no report, receipt, verdict or
  logged output (for example refreshing a manifest over identical bytes) is
  bookkeeping. **Rule C may not be used to legitimise a content edit after
  acceptance.**
- The previous revision's manifest is **kept** under `revisions/rev1/`, so the
  earlier revision's evidence hashes stay verifiable. The old **control** packet
  (`bnb_native_adx25_validation_run_20261019_01`) is kept **unchanged** and
  stays FAILED/INCOMPLETE.

---

## 1. Candidate

| Field | Value |
|---|---|
| Identity | `di_adx_state_edge` — one separate identity |
| Parameter set | **fixed, exactly two values**: `adx_period = 14`, `adx_threshold = 25.0` |
| Parameter search | **none.** No grid, no tuning, no neighbour selection. The set is frozen before any run. |
| Registry | **not published.** Not in the legacy registry, not in `strategies/__init__.py`, not in the canonical candidate registry. Registration happens only if §9 is approved, and the registered id is this one. |
| Trigger | edge of the state `B(k) = plus_di(k) > minus_di(k) AND adx(k) > 25.0` |
| MA | **absent.** No MA parameter, no MA computation. The MA pair must not be reintroduced within this experiment. |
| Inherited from the control | ADX/DI arithmetic, warmup 100, the whole native execution contract (§4) |
| Inherited from the control | **nothing else** — no result, metric, score or prior |

## 2. Control

| Field | Value |
|---|---|
| Control | `ma_adx` (MA20/80 + ADX14/25 + DI), **unchanged** |
| Control packets | `bnb_native_adx25_validation_run_20261019_01` stays **FAILED / INCOMPLETE**; it is not re-run, not relabelled, not reused as a pass |
| Use | comparison arm only. A control failure is not remediated by this packet and the control's numbers are not adjusted. |

## 3. Geometry and data — identical to the control on purpose

The validation length is **not** chosen to reach 30 trades. The 8 / 14 / 20-month
options are **deferred**: keeping the control's geometry first is what separates
the effect of the new policy from the effect of a different window.

| Field | Value |
|---|---|
| Folds | **5** (same as the control) |
| Validation | **6 months = 1080 bars** (same as the control) |
| Train / test / step | 12 months / 3 months / 3 months (same) |
| Purge / embargo | 100 / 100 bars (same) |
| History | warmup 100 bars, buffer 100 bars (same) |
| Fold windows | identical to the control's `research_protocol.json` |

| Fold | train | inner validation | outer test (NOT in this scope, §3.2) |
|---|---|---|---|
| fold_000 | [0, 2160) | **[2360, 3440)** | [3640, 4180) |
| fold_001 | [0, 2700) | **[2900, 3980)** | [4180, 4720) |
| fold_002 | [0, 3240) | **[3440, 4520)** | [4720, 5260) |
| fold_003 | [0, 3780) | **[3980, 5060)** | [5260, 5800) |
| fold_004 | [0, 4320) | **[4520, 5600)** | [5800, 6340) |

### 3.1 Data — only the confirmed-exposed part

| Field | Value |
|---|---|
| Input file | `data/existing_research_audit_20261008/BNB_USDT__4h__pre_holdout.parquet` |
| sha256 | `379677360902c9734780e496398abb02ea6390531b840e2262bd9d7881e6485f` (re-verified at run time; a mismatch fails before the first cell) |
| Bars **evaluated** | **[0, 5600)** — every executed cell's simulation and measurement window lies inside this range. It is the scope already declared as exposed by the control's full-prefix native replay and re-verified by the review packet. |
| Bars **READ AND DECODED** | **all 6795 rows, including their OHLCV.** This is a property of the frozen loader and it is stated here because it must not be hidden behind the phrase "not evaluated": `load_ohlcv` (research path) reads the whole file **bytes** — mandatory, because the sha256 binding is verified over the whole file — and then `pl.read_parquet` **decodes every row of every column**, and only *afterwards* applies the `start`/`end` filters. So the price data of **[5600, 6795) is materialised in memory** even though no cell evaluates it. |
| Bars **read but not evaluated** | **[5600, 6795)** — decoded by the loader, never the window of any executed cell. The outer test windows, which would reach 6340, are **planned but never executed** in validation-only. |
| Reserved **holdout** bars | **zero, and verified.** The file's last bar is **2026-02-06 08:00:00**, i.e. **8 hours before** the reserved holdout opens (2026-02-06 16:00). A **timestamp-only** probe (the same discipline as the control's `dispatcher_dataset_metadata` probe, which decoded only timestamps) counts **0** bars at or after the holdout start. The file is pre-holdout by construction. |
| Scope statement | The registered scope is therefore: **evaluation is confined to [0, 5600); the read covers the whole pre-holdout file.** "Not evaluated" does **not** mean "not read" — it does not undo the decode. Given the earlier raw-loader incident, whether a whole-file decode is acceptable under this registration is a **reviewer decision**, recorded as such; it is not asserted here. |
| Holdout | **not** accessed. The file is pre-holdout. The 2026-10-19 raw-loader incident (which decoded the 7946-row raw file including reserved holdout dates) remains recorded; **no untouched-data claim is made** by this packet. |
| Freshness claim | **none.** Every bar in [0, 5600) has been exposed; the run is **reused-data research** and is labelled as such. |

### 3.2 Outer OOS and holdout are outside this packet — **decision taken**

**Decision (operator): outer OOS does not run in this packet.** The scope is
**validation-only, inside [0, 5600)**. The outer test windows are **not read**:
bars up to 6340 are not touched, and the holdout is not touched.

The control's outer test windows reach bar **6340**, beyond the confirmed-exposed
[0, 5600). This packet therefore covers the **inner validation only** scope,
exactly like the control's executed scope
(`scope = "inner_validation_only_not_qualification"`).

**Consequences, binding on the report of this run:**
- The result is **inner-validation screening**. It **must not** be called
  *qualification*, *OOS*, or *out-of-sample*, and no outer or holdout gate may
  be evaluated from it.
- The aggregate outer-OOS gates in §6.2 are **not** evaluated by this packet;
  they are recorded so the gate set is complete, and they are marked
  NOT-EVALUATED here.
- Any outer-OOS or qualification step is a **separate, later preregistration**
  and needs its own data-scope decision for [5600, 6340).

## 4. Execution contract — inherited unchanged

The candidate runs under the **frozen native execution contract** of the
control; the candidate adds no policy of its own. The contract id is
`ebee638a18d3604207fd47f21e2130692ef0b0423ef3711c0a13af1eeacddc02`
(`native_gate_contract`), and the per-cell contract body is the control's, with
only the `strategy.parameters` block replaced.

| Contract field | Frozen value | Changed for this candidate? |
|---|---|---|
| `engine` | `FullSystemSimulator_authority_PaperExchange_v1` | no |
| `signal_timing` | `canonical_i_minus_1_to_open_i` | no |
| `protective_stop` | `current_price_trigger_with_slippage_not_prior_low_next_open` | no |
| `protective_stop.stop_loss.distance_pct` | `5.0` | no |
| `protective_stop.trailing_stop.enabled` | `false` | no |
| `sizing` | requested 25 %, max target 25 %, max position 50 %, `artifact_target_authority_capped` | no |
| `authority.exposure` | single-strategy 0.25, portfolio 0.5, correlated 0.4, risk scale 0.25–1.0 | no |
| `risk` | `cooldown_hours 24.0`, daily loss 8 %, max DD 15 %, profile moderate | no |
| `cooldown_trigger` | `risk_circuit_breaker_not_every_sell_fill` | no |
| `accounting` | `cash_plus_inventory_marked_at_close_no_forced_liquidation` | no |
| `cost_model` | commission 0.001, slippage 0.0005, impact 0, spread 0, 30 bps round trip | no |
| `price_path` | `open_then_low_then_close` | no |
| `cadence_bars` | 1 | no |
| `strategy` | `strategy_id` **and** `parameters` | **yes** — identity and the two parameters only |

## 5. Costs — frozen definitions

| Scenario | fee_multiplier | slippage_multiplier |
|---|---|---|
| `1x` | 1.0 | 1.0 |
| `2x` | 2.0 | 2.0 |
| `slip_stress` | 1.0 | 5.0 |

**15 inner cells** = 5 folds × 3 cost scenarios. Cost scenarios are alternative
assumptions for the same market history and are **never pooled** into one
sample.

## 6. Gates — unchanged, with the level each one applies at

All thresholds are carried over **unchanged** from the control's frozen native
gate contract (20 gates) and its selection contract. No threshold is relaxed.

### 6.1 Per fold — inner validation (selection contract, fingerprint `fb4cd812…`)

| Rule | Threshold | Action |
|---|---|---|
| `min_validation_trades` | 30 trades in the fold's inner validation window | below ⇒ candidate not eligible in that fold |
| `min_validation_sharpe` | `> 0.0` | below ⇒ candidate not eligible in that fold |
| no eligible candidate | — | **ABSTAIN** (not a pass, not a fail of the strategy) |
| invalid candidate | — | **REJECT** |
| tie-break | `canonical_params_cost_ascending` | — |

### 6.2 Aggregate — outer OOS / qualification gates (not executed in this scope)

Recorded here so the gate set is complete and auditable; these gates are
evaluated only in a later outer-OOS preregistration.

| Gate | Rule | Level |
|---|---|---|
| `outer_oos_net_return_positive` | `> 0 %` | aggregate (outer test folds) |
| `outer_oos_sharpe_ge_080` | `>= 0.8` | aggregate |
| `outer_oos_profit_factor_ge_120` | `>= 1.2` | aggregate |
| `outer_oos_max_drawdown_le_10pct` | `<= 10 %` | aggregate |
| `outer_oos_calmar_ge_050` | `>= 0.5` | aggregate |
| `dsr_ge_095` | `>= 0.95` | aggregate (study) |
| `pbo_le_020` | `<= 0.2` | aggregate (study) |
| `parameter_stability_ge_070` | `>= 0.7` | aggregate (across folds) |
| `positive_outer_folds_ge_60pct` | `>= 60 %` | fold-level, evaluated as an aggregate fraction |
| `zero_trade_fold_pct_le_50` | `<= 50 %` | fold-level, evaluated as an aggregate percentage |
| `cost_clearing_folds_above_chance_p_le_010` | `<= 0.1` | fold-level → aggregate |
| `cost_clearing_single_window_le_60pct` | `<= 60 %` | fold-level → aggregate |
| `drop_best_trade_net_pnl_positive` | `> 0` | fold-level |
| `cost_2x_net_pnl_positive` | `> 0` | fold-level (2x scenario) |
| `cost_2x_profit_factor_gt_1` | `> 1.0` | fold-level (2x scenario) |
| `delay_1_bar_net_pnl_positive` | `> 0` | fold-level (delay-1-bar re-run) |
| `delay_1_bar_profit_factor_gt_1` | `> 1.0` | fold-level (delay-1-bar re-run) |
| `parameter_neighbors_net_pnl_positive` | `> 0` | fold-level (neighbour probe) → aggregate — **see §7.2** |
| `min_trades_per_pair_strategy_ge_30` | `>= 30` | aggregate (pair/strategy total) |
| `median_return_clears_cost_floor` | `> 0.32 × median(positive fold trade counts)` | aggregate |
| contract `requirements` | `canonical_stats_verified`, `sensitivity_verified` | both must hold before any qualification-grade claim |

## 7. Sensitivity — P2 status, the structural conflicts, and the trial accounting

### 7.1 The frozen rule
```
fold_scope   : all_selected_outer_folds
scenarios    : cost_2x, slippage_stress, delay_1_bar
neighbors    : adjacent_grid_values_each_axis_with_at_least_two_values
startup      : fresh_at_outer_test_start
missing_action : INCOMPLETE
```

### 7.2 Parameter neighbours — Option B, **checked against the real algorithm**

The earlier draft of this section asserted that three probes would satisfy the
frozen sensitivity contract. **That assertion was not checked against the
code.** It has now been, and the result changes the plan. The facts, taken
from the implementation:

| Fact | Source | Consequence |
|---|---|---|
| The neighbour arm needs **at least three values on the probed axis** | `nested_wfo._compute_sensitivity_analysis` skips an axis when `len(param_values) < 3`; the neighbour positions are `index-1` and `index+1` | a 2-value axis is **silently skipped** — no probe, no warning. `adx_period ∈ {14, 21}` would have produced **nothing**. |
| The neighbour arm is built from **outer results** | `wfo_sensitivity.sensitivity_requests(spec, outers, folds)` iterates `zip(outers, folds)` and raises `ValueError("sensitivity requires every distinct planned outer fold")` | the arm **cannot run at all** without a complete set of outer OOS results |
| Each neighbour probe **re-runs the outer test window** | `_run_outer_eval_with_params` → `_run_outer_eval(..., fold.outer_test_start, fold.outer_test_end)` | the probes consume **market data on the outer windows** — which this packet excludes by decision D3 |
| `parameter_stability_ge_070` does **not** use the probes | `_compute_parameter_stability(inner_results)` measures the **mode of the parameters selected per fold** | with a one-point parameter set every fold selects the same point → stability = 1.0 **trivially**. The gate is satisfied but measures **no robustness**. The probes do not feed it. |

**Therefore, in this packet the parameter-neighbour arm is NOT EXECUTED**, and
that is a structural consequence of decision D3 (validation-only, no outer OOS),
not an oversight.

**SN rules, as now preregistered (they bind the LATER outer-OOS preregistration):**

| Rule | Statement |
|---|---|
| SN-1 | The **central candidate** is fixed at `adx_period = 14`, `adx_threshold = 25.0`. Selection and evaluation use it and nothing else. |
| SN-2 | The probe axis is **`adx_threshold` with three values `{20, 25, 30}`** — the only axis with enough values for the `index±1` neighbour rule. The central value 25 then has a left (20) and a right (30) neighbour. |
| SN-3 | `adx_period` is **not probed**: a 2-value axis would be silently skipped by the algorithm, so the preregistration does not pretend to cover it. It is recorded as a known gap of the frozen contract, not papered over. |
| SN-4 | The neighbour arm is a **robustness probe only**. It is never eligible for selection, it does not replace the central candidate, and the central candidate's result is not recomputed from a neighbour. |
| SN-5 | `parameter_neighbors_net_pnl_positive` is evaluated over the probe points: every probe point must have positive net PnL in the fold. |
| SN-6 | `parameter_stability_ge_070` is evaluated on the **selected** parameters across folds (as the code defines it). With one fixed parameter set it is **1.0 by construction**; that is recorded as a **vacuous pass** and must not be presented as evidence of robustness. |
| SN-7 | **A failing neighbour arm is recorded as FAIL.** It is not re-declared NOT APPLICABLE, not waived, and not replaced by the screening observation. The frozen `missing_action = INCOMPLETE` stands: a missing arm is a terminal state, not a pass. |
| SN-8 | The probe points are fixed here, in advance; they are not a parameter search and they do not add a selection dimension. |

**What this packet can and cannot conclude.** This packet executes **zero**
sensitivity cells, so the contract requirement `sensitivity_verified` **cannot
be satisfied here**. That is consistent with D3: the result is inner-validation
screening and **must not** be called qualification. `parameter_stability_ge_070`
is **not evaluable as a robustness statement** in this packet (SN-6).

**Which probes use market data, and through which path.**
- `cost_2x`, `slippage_stress`, `delay_1_bar`, `parameter_neighbor__*` are all
  **real re-runs on market data** at the outer test windows.
- They are produced by `run_nested_wfo(..., real_sensitivity=True)`, which
  reaches `compute_sensitivity` with `out_root` and `descriptor` and therefore
  performs **real** re-runs (`real_computed`).
- The guards: `run_nested_wfo` raises in validation-only mode if
  `real_sensitivity` is set, and `run_wfo_parallel.py` rejects
  `--validation-only` together with `--real-sensitivity`; the qualification
  path in turn requires measured sensitivity (`elif not args.real_sensitivity:
  parser.error(...)`). **So the sensitivity arms can only ever run on the
  qualification path, which has outer OOS.**
- The **synthetic** suite in this packet proves the *mechanics* of the
  candidate's signal semantics and the engine's stop/timing/inventory
  behaviour. It is **not** a substitute for the economic evidence of the
  sensitivity arm, and it is not used as one anywhere.

### 7.2a Trial accounting — the method, not an assertion

An earlier draft said the "multiple-trial burden is unchanged" because the
probes are not used for selection. **That was the wrong way to put it**, and the
reviewer was right to reject it: not being used for selection limits *selection
bias*, but it does not by itself prove that the trial count feeding the DSR is
unchanged. Here is the actual method, taken from the implementation.

**How the count is built** (`trading_agent/research/trials.py`):

```
raw_trial_count = Σ over registered experiments of max(1, #distinct execution scopes)
```

- An **execution scope** is `execution_scope_identity(execution_context)["context_sha256"]`
  — the hash of the frozen execution context (protocol fingerprint, source,
  environment, runtime config). The scope note in the code is explicit:
  *"folds/phases/exact same-context replays are not new selection opportunities"*.
- The **cost scenario is part of the experiment identity** (`params_with_cost` →
  `param_hash` → `ExperimentSpec`), so each (params, cost) pair is its own
  experiment.
- The DSR's `trials` input is exactly this `raw_trial_count`
  (`nested_wfo`: `deflated_sharpe_ratio(..., trials=trial_counts.raw_trial_count, ...)`).

**What this candidate contributes, and what the probes would contribute:**

| Item | Registered? | Scope | Raw-trial contribution |
|---|---|---|---|
| Central candidate, cost 1x (5 folds) | yes — `trial_phase = INNER_VALIDATION` | 1 execution context | **1** (the 5 folds do not multiply) |
| Central candidate, cost 2x (5 folds) | yes | same context, different experiment (cost is in the identity) | **1** |
| Central candidate, cost slip_stress (5 folds) | yes | same | **1** |
| **Central candidate total** | | | **3** |
| `cost_2x` / `slippage_stress` / `delay_1_bar` sensitivity arms | **no** — executed via `run_cell` directly in `wfo_sensitivity`, which does not write registry evaluations | — | **0** |
| `parameter_neighbor__*` probes | **no** — same path; and in this packet not executed at all | — | **0** (would be **+1 each** if they were registered, since a different parameter set is a different experiment) |

**Status of the method above: it is *my reading of the code*, not an endorsed
statistical method.** The formula, the scope definition and the
"folds do not multiply" rule were read off the implementation; whether this is
the *right* way to count trials for a deflated Sharpe ratio is a **statistical
question the reviewer must answer**, and this preregistration does not claim
it.

**The honest statement, replacing the earlier claim:**

1. The probes create **no selection opportunity** for the reported candidate:
   they are not eligible for selection and cannot replace the central candidate.
   That limits *selection bias*.
2. They are nonetheless **additional evaluations**, and the accounting that feeds
   the DSR does **not** represent them — because the sensitivity execution path
   bypasses the append-only registry, not because they are free.
3. Consequently, for any study that executes the sensitivity arms, the DSR is
   computed over a **smaller** trial count than the work actually performed.
   The DSR is therefore **not conservative with respect to the sensitivity
   work**, and any qualification-grade claim that cites a DSR must say so.
4. This is a **framework-level characteristic of every sensitivity arm** — cost,
   slippage, delay and neighbours alike — not something this candidate
   introduces. It is **flagged here, not changed here**: changing the trial
   accounting would alter every campaign's DSR and is outside this scope and
   this packet's budget.
5. For **this** packet the point is moot in the strict sense: **zero**
   sensitivity cells run, so the central candidate's raw trial contribution is
   **3**, and `sensitivity_verified` is **not** satisfied (see §7.2 and §6.2).
6. **The DSR may not be used as qualification evidence** while this limit is
   unresolved. A DSR computed on the registry's count is a number over a
   *subset* of the work actually performed; it is a statement about the
   central candidate's counted trials, not about the study as a whole. Any
   qualification-grade claim must either (a) handle the limit — e.g. by
   counting the sensitivity work in the trial count — or (b) have the limit
   independently assessed and the assessment recorded. Until then the DSR is
   reported, with this caveat attached, and is **not** cited as evidence of
   qualification.

### 7.2b Evaluation ledger — two layers, and what actually reconciles

"Registry khong ghi nhan != khong co trial." The registry count describes only
what the framework accounts for. The ledger below records **every evaluation
that was actually attempted**, and the review found that the earlier rule was
wrong: it paired a **runner-layer** file with a **worker-layer** file.

| Layer | Records | Written by |
|---|---|---|
| **runner** | `cell_attempts/<cache_key>/execution_started.json` (the attempt STARTED) and `cell_failures/<cache_key>__<uuid>.json` (the attempt FAILED — carries the identity, the error and the artifact) | `campaign_execution.CampaignCellRunner._execute_cell` |
| **worker** | `cell_execution/<uuid>/started.json` (before execution) and `cell_execution/<uuid>/result.json` (after execution) | `run_wfo_parallel._run_cell_worker` |

**The link between the layers** is the cache key: the runner's attempt directory
contains the worker's `cell_execution/<uuid>/` directory.

**Reconciliation rule (binding).**
- The worker writes `started.json` **before** execution and `result.json`
  **after** it — and it writes the result **even when the cell failed**, so the
  result carries the artifact and its status.
- An attempt is **COMPLETED** only when `result.json` exists **and** its
  artifact status is COMPLETED; it is **FAILED** when `result.json` exists and
  the artifact is not; it is **INTERRUPTED** when `result.json` is **MISSING**,
  which means the worker was **killed**.
- The runner layer records the attempt start in
  `cell_attempts/<cache_key>/execution_started.json` and the failure in
  `cell_failures/<cache_key>__<uuid>.json`. The start record alone proves only
  that the attempt STARTED and is **not** sufficient to classify it.

**There is no separate reconciler function in the codebase.** The
reconciliation is the runner-layer `cell_failures` record plus the **absence** of
a completion receipt (`exact_coverage.json` / `parallel_canonical_summary.json`),
and the coverage verification fails closed when a fold is not SELECTed.

**Writing.** Records are durable per attempt (the append-only per-attempt
receipts), so a timeout or a kill cannot erase the history of what was
attempted. The ledger is derived from those receipts and cannot drift from them.

| Layer | Counted in the registry? |
|---|---|
| Central inner-validation cells | yes (`trial_phase = INNER_VALIDATION`) |
| Sensitivity arms (cost_2x / slippage_stress / delay_1_bar) | **no** — executed via `run_cell` directly, no registry evaluation record |
| Parameter-neighbour probes | **no** — same path |

## 8. Budget — re-estimated on the actual execution path

The control's 90 s timeout had essentially **no margin**: the fit over its 14
completed cells is

```
elapsed_sec ≈ 4.87 + 0.015337 × simulation_bars      (max |residual| = 2.6 s)
```

and at the largest window the measured elapsed was **90.7 s** against a 90 s
budget — the `fold004/1x` cell was **killed at 90.5 s** and the packet is
FAILED/INCOMPLETE for that reason.

| Item | Value | Basis |
|---|---|---|
| Simulation length per cell | identical to the control (3440…5600 bars) | same geometry (§3) |
| Baseline per-cell elapsed | 57.4 s … 90.7 s | control measurement, 14 cells |
| Candidate extra cost | **unquantified, and expected to be higher**: 3–4× more entry events per window ⇒ more orders, more event-store writes | not measurable without a run |
| **Per-cell timeout** | **300 s** | 3.3× the control's worst observed 90.7 s |
| Workers | 1 | control setting |
| Automatic retries | **0** | control setting; a timed-out cell stays FAILED |
| max RSS | 2048 MiB | control setting |
| **Total timeout** | **5400 s** | 15 cells × 300 s = 4500 s + startup/cleanup headroom |
| Budget-probe requirement | the **first** cell is a probe: its elapsed time is recorded and compared against 300 s. If the probe exceeds 300 s, the run **stops and reports** instead of letting 5 cells time out | new, to prevent a repeat of the control's failure mode |

### 8.1 Cell inventory — the budget covers ALL of the work

| Work item | Cells | Market data? | Counted in the 5400 s? |
|---|---:|---|---|
| Central inner-validation cells | **15** = 5 folds × 3 cost scenarios (1x, 2x, slip_stress) — the same 15 `expected_inner_cells` the control declared | yes, inner-validation windows inside [0, 5600) | **yes** |
| **Budget probe** | **0 extra** — the probe **is** the first central cell (`fold_000`, cost 1x). It is **reused as an official cell**, not a throwaway run | same cell | yes (once — **no double execution**) |
| Sensitivity arms (`cost_2x`, `slippage_stress`, `delay_1_bar`, `parameter_neighbor__*`) | **0** in this packet — the arm requires a complete set of outer OOS results, which D3 excludes | would be outer-window data | n/a — not executed |
| Orchestration | — session spawn, protocol freeze, cell dispatch, coverage verification, cleanup | no | **yes** |

The 2x and slip_stress cost scenarios inside the 15 central cells are the
**frozen cost definitions** of the study. They are **not** the frozen
sensitivity arm: the arm is a separate set of re-runs over the **outer** folds
and is not executed here.

**The budget is an EXPECTED ENVELOPE, not a guarantee.** 15 × 300 s = 4500 s of
cell budget, leaving **900 s** of the 5400 s total for orchestration and
cleanup. The control's own overhead for 15 cells at 90 s was
`1790 − 1350 = 440 s` — **indicative only**: it is one measurement on one
candidate, one machine and one day, on a different strategy with a different
number of orders per window. It is not a bound on this candidate's overhead,
and it must not be treated as one. The probe cell exists precisely because the
envelope may be wrong: if the first cell exceeds 300 s, or the session exceeds
its deadline, the run stops and reports rather than burning the remaining
budget on cells that cannot finish.

### 8.2 Timeout semantics — descendants, receipts, incompleteness

| Requirement | Mechanism (from the implementation) |
|---|---|
| The timeout must **stop descendants** | the validation worker is spawned with `start_new_session=True`, so it leads its own **process group**; on exit the group is killed with `os.killpg(pid, SIGTERM)` then `SIGKILL`, and `_wait_for_all_reaped` verifies every PID in the group is reaped. A surviving descendant raises `RuntimeError("owned validation session did not stop within cleanup grace; remaining PIDs: ...")` — **fail-closed**. Per cell, the worker itself escalates `process.terminate()` → `process.kill()`. |
| **Receipts must be kept** | the session returns `status` (`COMPLETED` / `WORKER_ERROR`), `worker_exit_code`, `elapsed_seconds`, `sampled_group_peak_rss_mb`, `budget_enforcement`; each cell records its own `COMPLETED` / `FAILED` status and its `report.json`; the packet keeps `cell_timings.jsonl`, the cell attempt directories and the worker stdout/stderr logs. The control's failed packet is the precedent. |
| The result must be marked **not complete** | any cell failure or a non-zero worker exit leaves the packet **FAILED / INCOMPLETE** — the same terminal state as the control's run. A partial packet is never reported as a completed study, and the missing cells are **not** re-run. |
| No automatic retry | `max_retries = 0`. A failed cell stays failed. |



**Budget-probe accounting (binding).**
- The probe cell is a **normal cell**: it **counts against the 5400 s total
  budget**, it produces a **receipt** (elapsed time, status, terminal), and it
  is kept like any other cell.
- If the probe exceeds its 300 s budget, the run **stops and reports**. The
  timeout is **not** raised and the run is **not** restarted with a larger
  timeout. A second attempt would be a new attempt and would need its own
  preregistration and approval; it may **not** be presented as the same
  experiment.
- The probe's elapsed time is recorded in the terminal receipt regardless of
  outcome, so a later reader can see what the candidate actually cost.

**The timing fit is an estimate, not a guarantee.** `elapsed ≈ 4.87 +
0.015337 × sim_bars` was fitted on the **control's** 14 completed cells. The
candidate's per-cell cost is **not** measured — it has 3–4× more entry events
per window, hence more orders and more event-store writes. The fit therefore
gives a *prior* for the budget, not a bound. The probe cell exists precisely
because that prior may be wrong.

### 8.3 Execution envelope — **closed**: named caps with a regression

`scripts/run_wfo_parallel.py --validation-only` used to enforce

```
--cell-timeout <= 180          and          1 <= --total-timeout <= 1800
```

as **inline literals**. The 300 s / 5400 s budget of §8 does not fit that
envelope, and the control's own 90 s budget already sat at its edge.

**What was changed, and what was not:**

| Change | Detail |
|---|---|
| The caps are now **named constants** | `VALIDATION_CELL_TIMEOUT_MAX_SECONDS = 600`, `VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS = 7200`, `VALIDATION_TOTAL_TIMEOUT_MIN_SECONDS = 1`, `VALIDATION_MAX_RSS_MB_MIN = 128`, `VALIDATION_MAX_RSS_MB_MAX = 4096` |
| The enforcement is one **pure function** | `validate_validation_only_budget(args)` — reads the parsed arguments, raises `ValueError` naming the offending limit, does no I/O, creates no directory, starts no cell |
| The guard is **not removed, not bypassed** | every previous validation-only guard is intact: holdout forbidden, real sensitivity forbidden, `workers != 1` forbidden, packet reuse forbidden, and the same budget bounds still enforced |
| **No entrypoint detour** | the change is in the sanctioned entrypoint itself; no alternative entrypoint is used |
| The cap is a **ceiling, not a default** | the CLI default is unchanged (`--cell-timeout` default 1800 for the non-validation path, `--total-timeout` default 1800) |
| Regression | `tests/test_wfo_validation_envelope.py` — **9 tests**: caps are named constants and the guard references them; one second above each cap is rejected; the values exactly **at** the cap are accepted by the pure validator; the CLI rejects an out-of-envelope value **before any packet directory is created**; the other validation-only guards are unchanged; the non-validation path keeps its own guard |

The raise is therefore an **explicit, capped, regression-tested budget policy**
— not a removal of a guard and not a detour around it. The preregistered run
still uses **300 s per cell / 5400 s total**, i.e. strictly inside the caps.

## 9. Controlled execution path (the candidate is not broadly registered)

The candidate must be executable under the native path **without** being
broadly selectable. The mechanism, implemented in this revision:

| Element | Where | Behaviour |
|---|---|---|
| Scope-token map | `candidates.py` — `SCOPED_CANDIDATE_SCOPE_TOKENS` | maps the exact scope token `bnb_di_adx_stateedge_prereg_20261020_01` to the single candidate id `di_adx_state_edge` |
| Scoped descriptor + class | `candidates.py` — `_SCOPED_CANDIDATE_DESCRIPTORS` / `_SCOPED_CANDIDATE_CLASSES` | the candidate's descriptor, parameter schema, defaults and warmup, **not** in `_CANDIDATE_CLASSES` / `_PARAM_SCHEMAS` / `_PARAM_DEFAULTS` / `_CANDIDATE_WARMUPS` as a broad entry |
| Scoped registry build | `candidates.py` — `build_default_registry(*, scope_token=None)` | with **no** token the registry is exactly the broad registry; with the correct token the one scoped candidate is added |
| Resolution | `candidates.py` — `build_legacy_candidate(..., scope_token=...)`, `build_parameterized_adapter(..., scope_token=...)` | a scoped id resolves **only** with the matching token |
| Cell plumbing | `tournament.py` — `run_cell(..., scope_token=...)` → `_run_cell_impl` → registry + adapter + simulator | default `None` ⇒ byte-identical behaviour |
| Simulator | `scripts/full_system_backtest.py` — `FullSystemSimulator(..., scope_token=...)` | default `None` ⇒ byte-identical behaviour |
| Worker | `campaign_execution.py` | the token travels in the execution context, so an approved packet resolves the candidate without touching the broad registry |

**Properties this gives, and how they are checked** (`tests/test_di_adx_state_edge_scoped_path.py`, 8 tests):
- the candidate is **absent** from the legacy `@register_strategy` registry and
  from the default canonical registry — no campaign, worker or CLI can name it;
- the **correct** token resolves it (legacy candidate + canonical adapter);
- a **missing or wrong** token **fails closed** — the cell becomes a FAILED
  artifact with a resolution reason, never a silent substitution;
- the **control** `ma_adx` still resolves with no token, and the broad registry
  is otherwise unchanged;
- the scope-token map names exactly one candidate per token.

**The token is bound to the experiment/execution identity, not a free parameter.**

| Requirement | How it is met | Verified by |
|---|---|---|
| The token is part of the frozen execution identity | the token travels **inside the execution context**, and `execution_scope_identity` hashes the **complete** context, so two contexts differing only in the token have different `context_sha256` values — the trial scope differs | `test_scope_token_is_inside_the_execution_scope_identity` |
| Cache / reuse cannot accept a report from another context | the campaign cache key is `content_hash` over the whole execution context, so a different token yields a different key and a different cache path — the other context's report is never a candidate | `test_resume_cache_key_covers_the_execution_context` |
| A missing or wrong token fails **before** the simulation | the cell resolves the descriptor at registry-resolution time, which happens **before** the data is read and before the engine is constructed; the failure is recorded as a FAILED artifact with a resolution reason | `test_wrong_token_fails_before_any_simulation_artifact` (asserts no `events.db`, no `paper_binance.json`, no `report.json` is produced) |

### 9.1 Integration review of the chain (rev4) — five hops were dropping the token

The reviewer asked for the token to be traced from launcher → worker → registry
→ simulator. Doing that **by inspection of the intended entrypoint** (not by
unit-testing the pieces) found **five places where the token was dropped** —
the scoped candidate was not executable through the sanctioned entrypoint at
all. All five now carry it, and a synthetic end-to-end test drives the real
chain:

| Hop | Was | Now |
|---|---|---|
| `build_canonical_cells` | `build_default_registry()` — the cell plan could not even be built | `build_default_registry(scope_token=...)` |
| `prepare_parallel_protocol` → `audit_parallel_trial_history` | `build_default_registry()` for the feature-schema hash | token-aware |
| `QualificationCellRunner` cell-runner context | the context dict had no `scope_token` — the token was lost at the worker, so it was not in the **cache key** | token included |
| `_run_measured` → `_run_cell_worker` | the token was not forwarded to the cell | forwarded, and recorded in the per-cell `started.json` request record |
| `verify_parallel_result_coverage` (artifact rebuild) | `build_default_registry()` | token-aware |

Verification: `tests/test_di_adx_state_edge_entrypoint_integration.py` (4 tests,
synthetic end-to-end through the intended entrypoint):
1. the token survives launcher → protocol → trial context, and the execution
   scope identity differs from the no-token context;
2. **the correct token runs the scoped candidate through the real chain** —
   the cell completes;
3. a wrong token is blocked at **plan time** (the cell plan cannot be built)
   and leaves no artifact;
4. a token **swapped between plan and execution** fails at **resolution** —
   before the data is read and before the engine is constructed — and leaves no
   `events.db` / `paper_binance.json`.

**The residual risk, stated plainly:** the token is a string. Anyone who reads
it can run the candidate. It is a *scope control*, not a capability control:
it prevents accidental or casual selection, and it ties the executable identity
to this preregistration. It is not a security boundary, and it must not be
presented as one.

## 8.3 The registered command (the packet is run with exactly this)

```
.venv/bin/python scripts/run_wfo_parallel.py \
  --strategy di_adx_state_edge --symbol BNB/USDT --timeframe 4h \
  --cost all --workers 1 \
  --train-months 12 --val-months 6 --test-months 3 --step-months 3 \
  --cell-timeout 300 --total-timeout 5400 --max-rss-mb 2048 --max-retries 0 \
  --scope-token bnb_di_adx_stateedge_prereg_20261020_01 \
  --validation-only --no-real-sensitivity \
  --out <NEW_PACKET_DIR>
```

Every element is pinned by this preregistration and each is checkable against
the code.

| Pin | Why it is in the command |
|---|---|
| `--val-months 6` | **load-bearing.** The CLI default is **3** (`run_wfo_parallel.py`, `--val-months` default), which would NOT reproduce the frozen six-month windows. A launcher-path integration test asserts that the CLI with this pin produces exactly the five frozen windows and 15 planned cells, and that the CLI *without* it produces different windows. |
| `--train-months 12 --test-months 3 --step-months 3` | the frozen geometry, pinned explicitly so a future default change cannot move it |
| `--cell-timeout 300 --total-timeout 5400` | the re-estimated budget, inside the named caps (§8.3a) |
| `--max-rss-mb 2048 --max-retries 0` | the control's resource settings; no automatic retry |
| `--workers 1` | the control's worker count |
| `--validation-only --no-real-sensitivity` | no outer OOS, no sensitivity arm, no holdout |
| `--scope-token …` | unlocks the scoped candidate and binds the run to this preregistration |
| `--out <NEW_PACKET_DIR>` | a **new** directory: the packet is immutable and an existing one is refused |

`purge` and `embargo` are **not** CLI options: they are derived from the
descriptor warmup as `max(warmup, horizon)` = **100/100**, which matches the
control. The command therefore needs no purge/embargo flags.

### 8.3a Where the envelope is enforced — and the gap that was found

The envelope is enforced in **three** places, all now using the same named
constants (`VALIDATION_CELL_TIMEOUT_MAX_SECONDS = 600`,
`VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS = 7200`,
`VALIDATION_MAX_RSS_MB_MIN = 128`, `VALIDATION_MAX_RSS_MB_MAX = 4096`):

1. the CLI guard in `main()`;
2. the **protocol producer** `prepare_parallel_protocol`;
3. the **protocol verifier** `_verify_validation_protocol`.

The review found that (2) and (3) still carried the old inline literals
(180 / 1800 / 128 / 4096) after only (1) had been raised — so the registered
budget was **rejected by the tool it was registered for**. All three now share
the constants, and the registered 300 / 5400 values sit inside them. The guards
remain fail-closed and no condition was removed.

`max_retries` was likewise found **hard-coded to 1** in the producer while this
preregistration requires 0; it is now an explicit parameter with the historical
default preserved, and the registered command pins it to 0.

## 8.4 Supplementary review budget (registered separately from the run budget)

The review budget of the analysis scope is **exhausted**. The two
fault-injection synthetics and the data-boundary probe below are therefore
registered under a **separate** budget, **not** taken from the market-run
budget in §8.

| Item | Value |
|---|---|
| Purpose | close the two pre-run conditions (timeout/cleanup, ledger after a killed attempt) and the data-boundary check |
| Scope | synthetic fault injection + a timestamp-only metadata probe; **no** market cell, **no** market data, **no** campaign |
| Registered test time | **180 s** for the two fault-injection synthetics (observed ≈ 4 s) and **60 s** for the metadata probe (observed ≈ 1 s) |
| Not taken from | the 5400 s market-run budget of §8 — that budget is untouched by this work |
| Receipts | `tests/test_validation_timeout_and_ledger_faults.py` (2 tests), the timestamp-only probe recorded in the packet |
| Stop condition | if a fault-injection test cannot be made deterministic, it is reported as UNKNOWN rather than approximated |

## 9. Run authority

| Statement | |
|---|---|
| This document is a **preregistration**, not an approval | it authorises **no** market execution |
| Execution requires | (a) independent review of the contract **and** the synthetic suite, (b) a preregistration sign-off, (c) an explicit operator scope/budget approval, (d) a frozen source + protocol fingerprint at that moment |
| If approved | a **fresh** packet directory; the old control packet is never resumed or relabelled |
| Automatic retries | 0. A failed cell is reported, not retried. |
| Live / paper / broker | not authorised at any point by this document |
| Publish / push / merge | not authorised by this document |
| On failure | the failed packet, receipts and logs are **kept** exactly as the control's were, and the verdict is INCOMPLETE / NOT_QUALIFIED |

## 10. Terminal outcomes — decided in advance

| Outcome | Condition | Meaning |
|---|---|---|
| `ACCEPTED_VALIDATION` | all 15 cells COMPLETED, every fold eligible under §6.1 | the candidate is **screening-positive** on the control's windows. **This is not a qualification** and not a live claim. |
| `ABSTAIN_INSUFFICIENT_SAMPLE` | any fold has < 30 validation trades (the expected case) | the sample is insufficient at this geometry. **Recorded as a missing sample** — the window is **not** extended afterwards and the extended result may **not** be presented as the same experiment. |
| `INCOMPLETE` | a cell times out, or a sensitivity arm is missing | terminal, as in the control. Receipts kept. |
| `NOT_QUALIFIED` | folds eligible but the aggregate gates fail | negative result kept. No retune, no threshold change. |
| `REJECT` | the protocol/plan does not match the frozen plan | fail-closed before the first bar. |

**No outcome authorises a market, paper or live action.**

## 11. What would falsify the hypothesis this candidate carries

The hypothesis is **not** "the state-edge policy is profitable". It is narrower
and falsifiable:

> On the control's own windows and with the control's own execution contract,
> the state-edge trigger produces **more eligible validation observations per
> fold** than the control's event-crossover trigger, without relaxing any gate.

Falsified if the candidate's per-fold eligible observations are **not** higher
than the control's (7/6/8/8/6 gate actions, 30 BUY fills, 28 closed trades at
2x). Note that this hypothesis is about **sample feasibility only**; it carries
no profitability claim, and a candidate that produces more trades but loses
money is a perfectly possible outcome.

### 12.1 Preregistration identity (identity B)

The frozen decisions, canonicalised and hashed. This is the **experiment
identity**; it is independent of the source identity (A) and of the evidence
manifest (C).

`preregistration_fingerprint = c2d9ec62d2532852f789dfd30457f7b2b04db63251dae9b9751a6d3b4cc00875`

```json
{
  "authority": "run_nested_wfo",
  "budget": {
    "cell_timeout_seconds": 300,
    "central_cells": 15,
    "envelope_checks": "the same named caps (VALIDATION_CELL_TIMEOUT_MAX_SECONDS=600, VALIDATION_TOTAL_TIMEOUT_MAX_SECONDS=7200, VALIDATION_MAX_RSS_MB_MIN=128, VALIDATION_MAX_RSS_MB_MAX=4096) are enforced in three places: the CLI guard, the protocol producer, and the protocol verifier. The registered 300/5400 values sit inside them.",
    "envelope_is_expected_not_guaranteed": true,
    "final_review_round": {
      "cap_seconds": 600,
      "check_list": [
        "five token hops: launcher -> worker -> registry -> simulator, and the execution/cache identity",
        "file path and sha256 are the pre-holdout artifact; accept the 6795-row decode scope explicitly, evaluation confined to [0,5600)",
        "fault receipts and the fact that the aggregator does not certify a packet without coverage",
        "test IDs, source and manifests match the revision under review"
      ],
      "may": [
        "read source/artifact/receipts",
        "run the existing targeted suites",
        "run a synthetic check",
        "recompute the identities",
        "compare test IDs"
      ],
      "may_not": [
        "change any strategy, gate, policy, budget or identity",
        "open a market cell",
        "extend the preregistration",
        "start an optimisation round"
      ],
      "purpose": "one bounded independent review of the five token hops, the data scope, the timeout/ledger receipts and identity A/B/C",
      "registered": true,
      "stop_condition": "cap reached or check list exhausted; the review stops and reports",
      "taken_from_market_run_budget": false,
      "verdict_kind": "AUTHOR_AUTOMATED_VERIFICATION — the script, its assertions and its runner are all the author's. It is NOT an independent review. An independent review requires a reviewer other than the author to read the source, the assertions and the receipts on the exact A/B/C and to reproduce the checks independently.",
      "verdicts": [
        "ACCEPTED_FOR_BOUNDED_VALIDATION",
        "CHANGES_REQUIRED",
        "BLOCKED"
      ]
    },
    "geometry_note": "the CLI default for --val-months is 3 and would NOT reproduce the frozen six-month windows; the geometry is therefore pinned explicitly. purge and embargo are not CLI options: they are derived from the descriptor warmup (100) as max(warmup, horizon) = 100, matching the control.",
    "integration_review_status": "entrypoint_chain_verified_synthetic",
    "max_retries": 0,
    "max_retries_note": "the validation-only protocol verification requires max_retries == 0; the protocol producer default is 1, so --max-retries 0 is mandatory in the registered command",
    "max_rss_mb": 2048,
    "probe_reuses_first_central_cell": true,
    "probe_stops_run_without_raising_timeout": true,
    "registered_command": ".venv/bin/python scripts/run_wfo_parallel.py --strategy di_adx_state_edge --symbol BNB/USDT --timeframe 4h --cost all --workers 1 --train-months 12 --val-months 6 --test-months 3 --step-months 3 --cell-timeout 300 --total-timeout 5400 --max-rss-mb 2048 --max-retries 0 --scope-token bnb_di_adx_stateedge_prereg_20261020_01 --validation-only --no-real-sensitivity --out <NEW_PACKET_DIR>",
    "sensitivity_cells_in_this_packet": 0,
    "shared_ceiling_impact": "raising the validation-only ceiling from 180 s/cell and 1800 s total to 600 s/cell and 7200 s total affects EVERY validation-only study, not only this candidate: the frozen control and any other workstream now accept a larger budget. The candidate's own registered budget (15 x 300 s, 5400 s total) does not fit the previous envelope, so the change is necessary for it, but the shared effect is disclosed here rather than left implicit.",
    "supplementary_review_budget": {
      "actual_spent_seconds": 290.3,
      "grant_record": "granted in chat by the operator; NOT recorded on disk",
      "granted_envelope_seconds": 420,
      "market_cells_run": 0,
      "overspend_reason": "the registered 240 s was an estimate made before the round; the round needed the 7-test control regression (98 s), the acceptance re-capture under the final identity (50 s) and the launcher integration (120 s). Disclosed rather than reconciled: the on-disk 240 s no longer matches the round that was actually run.",
      "overspend_vs_registered_seconds": 50.3,
      "purpose": "close the rev18 fixes of independent review #1 and re-seal the acceptance evidence on the final identity",
      "registered_test_seconds": 240
    },
    "total_timeout_seconds": 5400,
    "workers": 1
  },
  "candidate": {
    "params": {
      "adx_period": 14,
      "adx_threshold": 25.0
    },
    "registry_state": "scoped_only_not_broadly_registered",
    "scope_token": "bnb_di_adx_stateedge_prereg_20261020_01",
    "strategy_id": "di_adx_state_edge",
    "symbol": "BNB/USDT",
    "timeframe": "4h"
  },
  "claims": {
    "live": false,
    "oos": false,
    "profitability_claimed": false,
    "qualification": false,
    "trade_count_claimed": false
  },
  "control": {
    "packet_state": "FAILED_INCOMPLETE_unchanged",
    "strategy_id": "ma_adx"
  },
  "costs": {
    "1x": {
      "fee_multiplier": 1.0,
      "slippage_multiplier": 1.0
    },
    "2x": {
      "fee_multiplier": 2.0,
      "slippage_multiplier": 2.0
    },
    "slip_stress": {
      "fee_multiplier": 1.0,
      "slippage_multiplier": 5.0
    }
  },
  "data": {
    "decoded_but_not_evaluated": [
      5600,
      6795
    ],
    "evaluated_scope": [
      0,
      5600
    ],
    "file_last_bar": "2026-02-06T08:00:00Z",
    "freshness_claim": "none_reused_data_research",
    "holdout_accessed": false,
    "holdout_opens": "2026-02-06T16:00:00Z",
    "path": "data/existing_research_audit_20261008/BNB_USDT__4h__pre_holdout.parquet",
    "read_scope": "whole frozen pre-holdout file (6795 rows) - bytes read for the sha256 binding and all rows decoded by read_parquet before any start/end slice",
    "read_semantics_verified_by": "timestamp-only metadata probe (0 bars at/after the holdout start)",
    "reserved_holdout_bars_in_file": 0,
    "rows_not_used": [
      5600,
      6795
    ],
    "rows_used": [
      0,
      5600
    ],
    "scope_change_request": {
      "approval_fields": [
        "approval_state",
        "approved_scope",
        "approved_by",
        "approved_at"
      ],
      "approval_note": "approval_state is the SINGLE authoritative state. The procedure edits exactly these four fields; the binding gate consults only approval_state, approved_by, approved_at and approved_scope (which must equal expected_approved_scope). There is no second status field, so following the procedure cannot be rejected by a state the procedure never sets.",
      "approval_state": "PENDING_OPERATOR_APPROVAL",
      "approved_at": null,
      "approved_by": null,
      "approved_scope": null,
      "approver_role": "operator (scope approval) + a reviewer other than the author (code/assertion/receipt review)",
      "binding_rule": "this request is part of the payload identity B. Approving it means editing approval_state/approved_scope/approved_by/approved_at in this payload, which PRODUCES A NEW B. A market run must use the protocol fingerprint of the B that carries the APPROVED state; any later payload edit produces a new B and invalidates the approval, requiring a fresh approval before the run.",
      "change": "registered scope moves from 'only the confirmed-exposed part' to 'evaluation confined to [0,5600); the read covers the whole pre-holdout file (6795 rows decoded)'",
      "condition_for_acceptance": "reviewer verifies the file path and sha256 are the pre-holdout artifact and that the timestamp-only probe shows no reserved-holdout bar",
      "expected_approved_scope": "read+decode 6795 rows of the pre-holdout file; evaluate only [0,5600); 0 reserved-holdout bars (last bar 2026-02-06T08:00:00Z, holdout opens 2026-02-06T16:00:00Z)",
      "if_refused": "a decode-limiting loader change would be required - a framework change, not proposed here",
      "not_claimed": "[5600,6795) is not 'unexposed'; it is read and decoded, not evaluated",
      "requested": true
    },
    "scope_wording": "evaluation is confined to [0,5600); the read covers the whole pre-holdout file",
    "sha256": "379677360902c9734780e496398abb02ea6390531b840e2262bd9d7881e6485f"
  },
  "execution_binding": {
    "blocks_before_first_cell": true,
    "checked_fields": [
      "approval_state",
      "approved_by",
      "approved_at",
      "approved_scope",
      "identity_A_source",
      "identity_B_preregistration",
      "input_sha256",
      "params",
      "strategy_id",
      "symbol",
      "timeframe",
      "windows",
      "costs",
      "cell_timeout",
      "total_timeout",
      "max_rss_mb",
      "max_retries",
      "scope_token"
    ],
    "enforced_in": [
      "the launcher, before the worker is spawned",
      "the worker, independently, before its first cell"
    ],
    "fails_closed": true,
    "identity_B_note": "identity B is the sha256 of this payload; the payload records identity A (the source fingerprint) and the manifest records identity B. A payload edited after approval no longer matches the manifest's identity B and is refused, so an edit cannot be laundered through.",
    "note": "the binding applies ONLY to a scoped candidate: for any other study it is a no-op, so the control and every ordinary run are unaffected; a scoped candidate without its token still fails closed at the registry/grid resolution. The gate runs in the launcher AND in the worker, on the validation path AND on the qualification path.",
    "rule": "running is not running THE APPROVED PAYLOAD. Before the first cell the frozen protocol is verified field by field against the approved preregistration; any single differing field blocks the run."
  },
  "execution_contract": {
    "changed_fields": [
      "strategy.strategy_id",
      "strategy.parameters"
    ],
    "fingerprint": "ebee638a18d3604207fd47f21e2130692ef0b0423ef3711c0a13af1eeacddc02",
    "inherited_from": "control_native_execution_contract",
    "unchanged": [
      "engine",
      "signal_timing",
      "protective_stop",
      "sizing",
      "authority.exposure",
      "risk",
      "cooldown_trigger",
      "accounting",
      "cost_model",
      "price_path",
      "cadence_bars"
    ]
  },
  "gates": {
    "min_oos_trades": 30,
    "native_gate_contract_fingerprint": "ebee638a18d3604207fd47f21e2130692ef0b0423ef3711c0a13af1eeacddc02",
    "selection_contract": {
      "invalid_candidate_action": "REJECT",
      "min_validation_sharpe": 0.0,
      "min_validation_trades": 30,
      "no_eligible_action": "ABSTAIN"
    },
    "threshold_changes": "none"
  },
  "geometry": {
    "buffer_bars": 100,
    "embargo_bars": 100,
    "folds": 5,
    "inner_validation_windows": [
      [
        2360,
        3440
      ],
      [
        2900,
        3980
      ],
      [
        3440,
        4520
      ],
      [
        3980,
        5060
      ],
      [
        4520,
        5600
      ]
    ],
    "purge_bars": 100,
    "step_months": 3,
    "test_months": 3,
    "train_months": 12,
    "val_months": 6,
    "warmup_bars": 100
  },
  "hypothesis": {
    "control_reference": "7/6/8/8/6 gate actions, 30 BUY fills, 28 closed trades at 2x",
    "falsified_if": "the candidate's per-fold eligible observations are not higher than the control's",
    "scope_of_claim": "sample feasibility only - carries no profitability claim",
    "statement": "on the control's own windows and with the control's own execution contract, the state-edge trigger produces MORE eligible validation observations per fold than the control's event-crossover trigger, without relaxing any gate"
  },
  "identity_A_dirty_diff_sha256": "1865dc28229abb82fd28eab83c2e29d33901067a3b8896937954ddf68d5685ee",
  "identity_A_source_fingerprint": "c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74",
  "ledger": {
    "actual_total_reported_separately_from_accounted_total": true,
    "evaluation_attempt_roots": "the runner creates cell_attempts/<uuid>/ per ATTEMPT; the attempt history of one evaluation is the set of attempt roots whose recorded identity hashes to that evaluation's cache key.",
    "evaluation_vs_attempt": "the cache key identifies the EVALUATION (one planned cell). The worker's cell_execution/<uuid>/ directories under the runner's attempt directory are the ATTEMPTS of that evaluation. One row is written per evaluation (cell_ledger/<key>.json) with the attempt count and the attempt outcomes, so a retried or resumed evaluation stays ONE evaluation and is never counted twice.",
    "fields": [
      "evaluation_id",
      "kind",
      "fold_id",
      "measurement_window",
      "simulation_window",
      "params",
      "cost",
      "status",
      "elapsed_seconds",
      "registry_counted",
      "receipt_ref"
    ],
    "fresh_cache_failure": "the row records the execution path (fresh or cache_hit) and the artifact status; a cache hit has no attempt root of its own and is classified from the attempt history already on disk.",
    "interrupted_requires_stopped_process": "INTERRUPTED requires the worker process to be gone; a missing result.json while the worker is alive is RUNNING, not INTERRUPTED",
    "latest_attempt_ordering": "by the producer's real start time (started_at_epoch, falling back to the ISO started_at); never by directory name. The worker and the runner both write started_at_epoch.",
    "layers": {
      "runner_layer": {
        "failure": "cell_failures/<cache_key>__<uuid>.json",
        "started": "cell_attempts/<cache_key>/execution_started.json",
        "written_by": "campaign_execution.CampaignCellRunner._execute_cell"
      },
      "worker_layer": {
        "result": "cell_execution/<uuid>/result.json",
        "started": "cell_execution/<uuid>/started.json",
        "written_by": "run_wfo_parallel._run_cell_worker"
      }
    },
    "ledger_failure_not_silent": "a ledger write failure is appended to cell_ledger/_ledger_errors.jsonl (durable, append-only) and logged loudly; it is never only printed.",
    "ledger_merge": "one row per evaluation, MERGED across its attempts: the row keeps the attempt history and updates the counts and the outcome, so a retried or resumed evaluation stays one evaluation.",
    "no_reconciler_function": "there is no separate reconciler function in the codebase: the reconciliation is the runner-layer cell_failures record plus the absence of a completion receipt (exact_coverage.json / parallel_canonical_summary.json), and the coverage verification fails closed when a fold is not SELECTed.",
    "planned_source": "protocol.expected_inner_cells",
    "reconciler_note": "there is no separate reconciler function in the codebase: the reconciliation is the runner-layer cell_failures record plus the absence of a completion receipt (exact_coverage.json / parallel_canonical_summary.json), and the coverage verification fails closed when a fold is not SELECTed.",
    "reconciliation_rule": "the two layers are linked by the cache key: the runner's attempt directory holds the worker's cell_execution/<uuid>/ directory. The worker writes started.json BEFORE execution and result.json AFTER it -- and it writes the result even when the cell failed, so the result carries the artifact and its status. Therefore: an attempt is COMPLETED when result.json exists and its artifact status is COMPLETED; FAILED when result.json exists and the artifact is not completed; and INTERRUPTED only when result.json is MISSING AND the worker process is GONE. While the worker process is still alive the attempt is RUNNING, not INTERRUPTED. The runner layer records the attempt start in cell_attempts/<key>/execution_started.json and the failure in cell_failures/<key>__<uuid>.json; the start record alone proves only that the attempt STARTED and is NOT sufficient to classify it.",
    "records_only_attempted": true,
    "statuses": [
      "planned",
      "attempted",
      "completed",
      "failed"
    ],
    "verified_by": "src/trading_agent/backtest/attempt_ledger.py implements the classification; the runner writes one evaluation-level row per cell.",
    "written": "durably_after_each_attempt",
    "written_only_at_terminal": false
  },
  "manifest_change_rule": {
    "bookkeeping_only_if": "no report, receipt, verdict, or logged output changes",
    "may_not_be_used_to_legitimise_post_acceptance_content_edits": true,
    "otherwise": "evidence_changed_requires_reverification_and_rereview_of_the_affected_part"
  },
  "run_authority": {
    "authorises": [],
    "auto_retries": 0,
    "does_not_authorise": [
      "market run",
      "broad registration",
      "budget",
      "paper",
      "live",
      "broker",
      "publish",
      "push",
      "merge"
    ],
    "preregistration_is_not_run_permission": true,
    "run_requires": [
      "independent review passes",
      "explicit operator scope/budget approval",
      "a frozen source and preregistration fingerprint at that moment"
    ]
  },
  "scope": "inner_validation_only_not_qualification",
  "scope_token_binding": {
    "blocked_before_data_read_and_simulation": {
      "claim_corrected": "the earlier payload wording said wrong token is blocked before the data read; that is true only at execution time. The plan-time path reads the data first.",
      "execution_time": "BLOCKED BEFORE the data read and before the engine: the cell resolves the descriptor and the adapter before the loader is called, so a wrong token at execution time stops the cell with no engine artifact.",
      "plan_time": "BLOCKED AFTER the loader read, before any cell is planned: build_canonical_cells calls load_ohlcv first and resolves the registry afterwards, so a wrong token at plan time does not prevent the data read. The plan then fails closed and no cell runs."
    },
    "cache_key": "content_hash over the complete execution context",
    "carried_in": "execution_context",
    "covered_by": "execution_scope_identity(context_sha256) over the complete context",
    "failure_point": "registry resolution, before data read and before the engine",
    "hops_verified": [
      "launcher CLI --scope-token -> prepare_parallel_protocol -> protocol.execution.scope_token",
      "protocol -> _trial_execution_context -> context.scope_token",
      "context -> build_canonical_cells(scope_token=) -> registry resolution",
      "context -> prepare_parallel_protocol -> audit_parallel_trial_history -> feature schema hash",
      "context -> QualificationCellRunner cell-runner context -> cache key",
      "context -> _run_measured -> _run_cell_worker -> run_cell(scope_token=) -> registry/adapter/simulator",
      "protocol -> verify_parallel_result_coverage -> artifact rebuild"
    ],
    "integration_review_findings": {
      "hops_found_broken_and_fixed": [
        "CLI param grid: the scoped candidate had no grid, so the CLI planned 0 cells",
        "protocol producer budget guard still used the old 180/1800/128/4096 literals and rejected the registered budget",
        "protocol verifier budget guard still used the old literals",
        "nested_wfo.run_nested_wfo built the registry without the token",
        "nested_wfo._compute_strategy_code_sha degraded to 'unknown' for a scoped candidate, losing source attribution in the trial identity",
        "campaign_execution artifact rebuild/validation built the registry without the token",
        "selection_eligibility.verify_validation_reports built the registry without the token"
      ],
      "hops_outside_the_validation_only_scope": {
        "note": "these resolve the registry WITHOUT the token and therefore fail closed for a scoped candidate. None is executed by the registered command (--validation-only --no-real-sensitivity). The QUALIFICATION path is OUT OF SCOPE for this candidate: on that path the selection attribution rebuild at nested_wfo.py does not receive the token, so the path is not functional for this candidate and must be fixed before it is used. The DSR caveat and these paths do not block this validation round, because neither is executed nor used to claim qualification.",
        "sites": [
          "src/trading_agent/backtest/wfo_sensitivity.py (sensitivity arm)",
          "src/trading_agent/research/selection_policy.py (promotion gate)",
          "src/trading_agent/calibration/execution.py, src/trading_agent/calibration/packet.py (calibration)",
          "src/trading_agent/execution/live_admission.py (live admission)",
          "src/trading_agent/backtest/nested_wfo.py (selection attribution rebuild on the qualification path - not reached in validation-only)",
          "src/trading_agent/backtest/campaign_integrity.py, src/trading_agent/calibration/packet.py (trial identity rebuilds)"
        ]
      },
      "reviewer_verdict": "CHANGES_REQUIRED"
    },
    "integration_test": "tests/test_di_adx_state_edge_entrypoint_integration.py — synthetic end-to-end through the intended entrypoint chain",
    "token": "bnb_di_adx_stateedge_prereg_20261020_01",
    "wrong_or_missing_token": "fail_closed"
  },
  "sensitivity": {
    "executed_in_this_packet": false,
    "failure_action": "FAIL_recorded_never_N_A",
    "not_probed": [
      "adx_period"
    ],
    "probe_axis": {
      "name": "adx_threshold",
      "values": [
        20,
        25,
        30
      ]
    },
    "reason": "requires_outer_oos_results_which_scope_excludes",
    "rule_fingerprint": "4748b799a2f41ee6f1fc9e94c3b0164f279d0fc59d3c3f3f16c3f8e82aa17116",
    "sensitivity_verified_achievable_here": false
  },
  "terminal_outcomes": [
    "ACCEPTED_VALIDATION",
    "ABSTAIN_INSUFFICIENT_SAMPLE",
    "INCOMPLETE",
    "NOT_QUALIFIED",
    "REJECT"
  ],
  "version": 14
}
```

## 12. Evidence and identity

| Item | Value |
|---|---|
| Contract | `data/bnb_di_adx_stateedge_20261020_01/STATE_EDGE_CONTRACT.md` |
| Implementation | `src/trading_agent/strategies/di_adx_state_edge.py` (unregistered) |
| Synthetic suite | `tests/test_di_adx_state_edge_contract.py` — 13 tests, synthetic only |
| Control review this derives from | `data/bnb_native_review_20261020_01/RESULTS.md` (rev3) |
| Source fingerprint | recorded in the packet manifest; re-anchored at the moment of any approved run |
| Control protocol (geometry, contract, gates, sensitivity rule) | `data/bnb_native_adx25_validation_run_20261019_01/research_protocol.json`, fingerprint `4748b799a2f41ee6f1fc9e94c3b0164f279d0fc59d3c3f3f16c3f8e82aa17116` |

## 13. Decision ledger

| # | Item | Status |
|---|---|---|
| D1 | Parameter-neighbour conflict | **CLOSED as Option B, and re-checked against the implementation** (§7.2). The probe axis is `adx_threshold ∈ {20, 25, 30}` — the only axis with the ≥3 values the neighbour rule needs; `adx_period` is **not** probed and is recorded as a known gap. The arm **cannot execute in this packet** (it needs outer OOS), so it is **NOT EXECUTED** here and `sensitivity_verified` **cannot** be satisfied — consistent with D3. |
| D2 | Execution envelope (180 s / 1800 s) | **CLOSED** (§8.3). Named constants (600 s / 7200 s) enforced by one pure function, with a 9-test regression; no guard removed, no entrypoint detour. The preregistered run uses 300 s / 5400 s, strictly inside the caps. |
| D3 | Outer OOS | **CLOSED — not in this packet** (§3.2). Validation-only inside [0, 5600); outer windows up to 6340 are not read; the result must not be called qualification/OOS. |
| D4 | Candidate identity `di_adx_state_edge` and the two-value parameter set | **open for the reviewer** — confirm before registration (§1) |
| D5 | Terminal outcomes and the "no extension afterwards" rule | **open for the reviewer** — confirm §10 |
| D6 | Independent review of code + synthetic + this preregistration | **open** — the next step (§14). The three D6 checks the reviewer named have been answered against the implementation: **the probe-coverage question (§7.2)**, **the trial-accounting question (§7.2a)** and **the budget-coverage question (§8.1, §8.2)**. |

## 13.0 Second independent review (rev13) — `CHANGES_REQUIRED`, four findings, all fixed

The reviewer read the two new modules and the call sites **without running
tests**. Findings and the minimal fixes in this revision:

| Level | Finding | Fix |
|---|---|---|
| P1 | the binding compared the CLI's **grid** `{"adx_period": [14], "adx_threshold": [25.0]}` with the preregistration's **scalars**, so a correct run was blocked | `normalise_singleton_grid` reduces a one-element grid to its scalar; the check is **kept** and now compares like with like. A grid that is not a singleton per axis is rejected (it is a search, not a fixed set) |
| P1 | the approval required **both** `approval_state` and `status` to be `APPROVED`, but the procedure set only four fields — so following the procedure was still rejected; `approved_scope` was not checked at all | **one** authoritative state (`approval_state`); `status` removed from the payload; the procedure edits exactly `approval_state`, `approved_scope`, `approved_by`, `approved_at`; `approved_scope` is checked and must equal `expected_approved_scope` |
| P2 | the ledger picked the "latest" attempt by `started_at_epoch`, which the worker never wrote (it writes `started_at` ISO) — every attempt scored 0 and "latest" was really UUID order | the producer now writes **`started_at_epoch`** (worker request record and the runner's `execution_started.json`); ordering uses that, with the ISO `started_at` as fallback, never the directory name |
| P2 | the runner creates `cell_attempts/<uuid>` **per attempt**, so the ledger summarised only the UUID that had just run and overwrote the row — the multi-attempt history was not aggregated, a cache hit had no attempt root, and a ledger error was only printed | attempt roots are grouped **by the evaluation's cache key** (the hash of the recorded identity); the row is **merged** across the attempt history; `execution_path` records fresh vs cache hit; a ledger failure is appended to `cell_ledger/_ledger_errors.jsonl` (durable, append-only) and logged loudly |

The reviewer also noted that `REVIEWERS_WORKING_SET.md` still carried rev12's
A/B while containing rev13's modules. The generator now states **no identity at
all** — it points at the three manifests — so the index can never go stale
against the identity, and it is regenerated before any review.

## 13.1 Binding the run to the approved preregistration

Running is not running **the approved payload**. Before the first cell the frozen
protocol is verified field by field against the approved preregistration, in the
launcher **and** independently in the worker. Any single differing field blocks the
run and the mismatching fields are named:

`approval_state` · `approved_by` · `approved_at` · `approved_scope` ·
`identity_A_source` · `identity_B_preregistration` · `input_sha256` · `params` ·
`strategy_id` · `symbol` · `timeframe` · `windows` · `costs` · `cell_timeout` ·
`total_timeout` · `max_rss_mb` · `max_retries` · `scope_token`

(There is **no** `scope_change_status` field: `approval_state` is the single
authoritative state. The payload's `execution_binding.checked_fields` is the
machine-readable form of this list.)

Identity **B** is the sha256 of the preregistration payload; the payload records
identity **A** (the source fingerprint) and the manifest records identity B. A
payload edited after approval no longer matches the manifest's B and is refused,
so an edit cannot be laundered through.

## 13.2 The INTERRUPTED rule, and evaluation vs attempt

**INTERRUPTED requires the worker process to have stopped.** A start record
without a result record can simply mean the worker is **still running**:

| Attempt state | Classification |
|---|---|
| `result.json` present, artifact COMPLETED | COMPLETED |
| `result.json` present, artifact not completed | FAILED |
| `started.json` present, no `result.json`, **worker alive** | **RUNNING** (not INTERRUPTED) |
| `started.json` present, no `result.json`, **worker gone** | **INTERRUPTED** |

**The cache key identifies the EVALUATION, not each attempt.** The worker's
`cell_execution/<uuid>/` directories under the runner's attempt directory are the
*attempts* of one evaluation. The runner writes **one ledger row per
evaluation** (`cell_ledger/<key>.json`) carrying the attempt count and the
attempt outcomes, so a retried or resumed evaluation stays ONE evaluation and is
never double counted, nor joined to the wrong evaluation.

## 13.3 Budget overrun — recorded, execution stopped

The fix round exceeded the registered 900 s of test/synthetic execution
(~1500 s actual). The cause, the per-command seconds and the decision to stop
are recorded in `../bnb_di_adx_stateedge_20261020_01/BUDGET_OVERRUN.md`. No market
run was performed. **Execution is stopped** until a budget is approved; the rev13
code changes are therefore **unverified by execution** and are presented for a
**read-only** review.

## 13.4 Reviewer checklist — the three things to confirm

These are the confirmations the independent reviewer must make. They are
stated as questions with the evidence pointer, so a reviewer can answer each
one without re-deriving the packet.

| # | To confirm | Evidence pointer | What would falsify it |
|---|---|---|---|
| **R1** | **The candidate has a controlled execution path even though it is not broadly registered.** | §9; `tests/test_di_adx_state_edge_scoped_path.py` (8 tests): absent from the legacy and default registries; resolves only with the exact scope token; missing/wrong token fails closed; the control still resolves; the token is plumbed through `run_cell` → `_run_cell_impl` → registry/adapter/simulator and through the worker's execution context. | the candidate being resolvable **without** a token, or the token not reaching the cell's resolution. |
| **R2** | **A completed validation can carry the verdict "sufficient coverage, but not yet qualified", and a missing sensitivity is not hidden as a PASS.** | §10 terminal outcomes (`ACCEPTED_VALIDATION` = screening-positive, **not** qualification); §6.2 marks the outer gates NOT-EVALUATED in this scope; §7.2 records that the neighbour arm is NOT EXECUTED and `sensitivity_verified` is unreachable here; §7.2a states the DSR may not be cited as qualification evidence while the trial-count limit is unresolved; §7.2b requires the ledger to show every evaluation with its `registry_counted` flag. | a packet that reports a PASS on the outer/qualification gates, or that reports the DSR as qualification evidence, or that omits the uncounted evaluations from the ledger. |
| **R3** | **A new preregistration with a new manifest is locked, and the old failed packet/control is kept unchanged.** | §0 (the three identities); §12.1 (identity B); §14 (identity A + C); `revisions/rev1/` keeps the previous manifest; the control packet `bnb_native_adx25_validation_run_20261019_01` is untouched and still FAILED/INCOMPLETE. | the control packet being edited, resumed or relabelled, or the previous revision's manifest being overwritten instead of kept. |

**Two further items the reviewer should rule on** (they are judgement calls, not
facts this packet can settle):

1. **The trial-count method** (§7.2a) — the formula, the scope definition and
   the "folds do not multiply" rule were read off the implementation. Whether
   that is the *right* way to count trials for a deflated Sharpe ratio, and
   whether the uncounted sensitivity work invalidates the DSR, is a
   statistical question. This packet does not claim an answer; it records the
   limit and forbids citing the DSR as qualification evidence until it is
   handled or independently assessed.
2. **The scope token is a scope control, not a capability control** (§9). It
   prevents casual selection and ties the executable identity to this
   preregistration; it is not a security boundary.

**What the reviewer needs to read, in order:** the contract
(`../bnb_di_adx_stateedge_20261020_01/STATE_EDGE_CONTRACT.md`), the synthetic
suite (`../bnb_di_adx_stateedge_20261020_01/` results + the two test files),
the scoped-path test, and this preregistration — then the source diff implied
by identity A.

## 13.2 Review checklist — status of each item

| Review item | Status in rev4 | Evidence / limit |
|---|---|---|
| Trace the token launcher → worker → registry → simulator | **DONE, and it found five dropped hops** | §9.1; the five fixes; the hop list is frozen in the §12.1 payload |
| Identity and cache/reuse | **DONE** | the token is inside the frozen execution context ⇒ covered by `execution_scope_identity`; the campaign cache key hashes the whole context ⇒ a different token is a different key, never a cache candidate |
| Wrong token blocked before data read / simulation | **DONE, at two points** | plan time (the cell plan cannot be built) and execution time (token swapped between plan and execution ⇒ fails at resolution, no `events.db` / `paper_binance.json`) |
| Correct token runs via synthetic through the intended entrypoint | **DONE** | `tests/test_di_adx_state_edge_entrypoint_integration.py`: the cell **completes** through the real `QualificationCellRunner → _run_measured → _run_cell_worker → run_cell` chain on synthetic data |
| Timeout / cleanup | **EXERCISED by fault injection** | `tests/test_validation_timeout_and_ledger_faults.py::test_f1_timeout_stops_the_whole_group_including_the_descendant`: a session spawns a descendant that **ignores SIGTERM**, the wall deadline fires → the receipt says `TIMEOUT`, the descendant **and** the session leader are both dead, the owned group has **no** live PID, and no completion receipt (`execution_finished.json` / `exact_coverage.json`) exists. `max_retries = 0` is carried in the attempt record |
| Ledger after a failed attempt | **EXERCISED by fault injection** | `...::test_f1_killed_attempt_leaves_a_durable_record_and_no_completed_artifact`: a killed attempt carries `execution_started.json`, **no** `result.json`, `max_retries = 0`, and no `report.json` / `execution_finished.json` / `exact_coverage.json` — so it can be reconciled only to INTERRUPTED, never to completed. The binding rule is in §7.2b. The control's own failed cell shows the same shape (start record + a `cell_failures/` record, no result) |
| Data boundary | **CHECKED directly (timestamp-only probe)** | the frozen loader reads the whole file and decodes every row before slicing; the file's last bar is 2026-02-06 08:00, **8 h before** the reserved holdout opens, and a timestamp-only probe counts **0** bars at/after the holdout start. The registered scope is "**evaluation** confined to [0,5600); the **read** covers the whole pre-holdout file" — see §3.1; whether that read is acceptable is a reviewer decision |
| Verdict "sufficient coverage, but NOT_QUALIFIED" | **specified, and the paths are distinct** | §10: `ACCEPTED_VALIDATION` is explicitly *screening-positive*, not qualification; §6.2 marks the outer gates NOT-EVALUATED in this scope; §7.2 records that `sensitivity_verified` is unreachable here. The code enforces the scope side: `--validation-only` refuses `--real-sensitivity`, and `run_nested_wfo` raises in validation-only mode if `real_sensitivity` is set |
| Prereg vs scope [0,5600), 15 cells, no sensitivity/OOS, no retry, 5400 s | **cross-checked; one discrepancy found and fixed** | the registered command is §8.3; `max_retries` was hard-coded to 1 while the prereg required 0 → now an explicit parameter, pinned to 0 by the command. The data-scope wording was imprecise ("not used" vs "read but not evaluated") and is corrected above |
| Trial accounting | **stated as a limitation, not as a validated method** | §7.2a: the formula is read off the implementation; the DSR may not be cited as qualification evidence while the limit is unresolved; §7.2b keeps a separate ledger so the accounted count and the actual count are never conflated |

**Test-scope statement, stated exactly.** The core-path regression run covered
**96 tests: 95 passed, 1 failed**. The failure was an existing test whose
monkeypatched stub did not accept the new optional `scope_token` keyword; the
stub was updated and that test file was re-run (**27/27**). The remaining
**68** tests of that 96 were **not** re-run after the change, so the correct
statement is *"95/96 passed, the one failure was fixed and re-verified on its
own file"* — **not** "96/96 passed". Which minimum re-run the reviewer requires
is the reviewer's decision.

**What remains unverified by execution**: nothing in the two fault-injection
items — they are now exercised. Still open and outside this scope: the
behaviour of the *cell-level* timeout inside a real cell (`tournament`'s
`_run_isolated_process` → `terminate()`→`kill()`), which the session-level
fault injection exercises only at the session layer.

## 13.3 Scope change request — the whole-file decode (needs approval)

**Request.** Accept, for this packet only, that the frozen loader **reads and
decodes all 6795 rows** of the pre-holdout file while the **evaluation** stays
confined to **[0, 5600)**.

| Element | Statement |
|---|---|
| What changes | the registered data scope. It was written as "only the confirmed-exposed part"; the truth is that the **read** covers the whole pre-holdout file (bytes are mandatory for the sha256 binding, and `read_parquet` decodes every row before the `start`/`end` filters). |
| Why it is disclosed now | the review found it; it was previously described only as "read at the loader level, not evaluated", which **understated** the decode. After the earlier raw-loader incident, that understatement was not acceptable. |
| What is **not** claimed | that [5600, 6795) is "unexposed" — it is **read and decoded**. What is true: it is **not evaluated**, and the file contains **0** reserved-holdout bars (last bar 2026-02-06 08:00, holdout opens 16:00; timestamp-only probe). |
| Condition for acceptance | the reviewer verifies the **file path and its sha256** are the pre-holdout artifact and that the timestamp-only probe shows no reserved-holdout bar. Both are recorded in §3.1 and reproducible. |
| If refused | the run needs a loader that limits the decode to the registered range. That is a **framework change** to `load_ohlcv` and is **not** proposed here; it would need its own scope and review. |

## 13.4 Regression evidence — exact test IDs, and what was NOT re-run

The reviewer asked to compare test IDs rather than to trust a "96/96" line.

| File | Test | What changed | Re-run? |
|---|---|---|---|
| `tests/test_validation_only_workflow.py` | `test_schedule_mismatch_fails_before_any_cell_or_study` | the test's own stub for `worker.build_canonical_cells` did not accept the new optional `scope_token` keyword: `lambda s: (...)` -> `lambda s, *a, **k: (...)` — **a stub signature only**; no assertion was changed | **yes** — that file, **27/27** |
| `tests/test_validation_only_workflow.py` | `test_cli_rejects_invalid_validation_budget_before_preflight` | the parameters now come from the policy constants **and** keep the literal approved values (`601`, `7201`) so the policy value is pinned | **yes** — same file |
| `tests/test_wfo_validation_envelope.py` | whole file | new: caps pinned as literals, max+1 boundary, pure validator accepts at-cap values | **yes** — **10/10** |
| `tests/test_di_adx_state_edge_scoped_path.py` | whole file | new | **yes** — **11/11** |
| `tests/test_di_adx_state_edge_entrypoint_integration.py` | whole file | new — the five hops, end to end | **yes** — **4/4** |
| `tests/test_validation_timeout_and_ledger_faults.py` | whole file | new — F1/F2/F2b/F2c | **yes** — **4/4** |
| `tests/test_di_adx_state_edge_contract.py` | whole file | new | **yes** — **15/15** |
| `tests/test_indicator_window_equivalence.py` | whole file | new | **yes** — **3/3** |

**The 96-test core-path run**: 95 passed, 1 failed (the stub above). After the
stub fix, **that file** was re-run (27/27). The other **68** tests of the 96
were **not** re-run after the change. They are reported as **not re-run** — the
claim is *"95/96 passed; the single failure was a stub signature and was fixed
and re-verified on its own file"*, **not** "96/96 passed". Which minimum
re-run the reviewer requires is the reviewer's decision; this table is the
evidence for that decision.

## 13.5 Final review round — registered separately, with a cap

The review budget of the analysis scope is **exhausted**. The final independent
review round is therefore registered as its own bounded item.

| Field | Value |
|---|---|
| Purpose | one bounded independent review of: (a) the five token hops, (b) the data scope / whole-file decode request (§13.3), (c) the timeout/ledger behaviour and its receipts, (d) identity A/B/C |
| Cap | **600 s** of execution, **not** taken from the 5400 s market-run budget of §8 |
| May do | read source/artifact/receipts, run the existing targeted suites, run a synthetic check, recompute the identities, compare test IDs |
| May **not** | change any strategy, gate, policy, budget or identity; open a market cell; extend the preregistration; start an optimisation round |
| Output | one verdict: `ACCEPTED_FOR_BOUNDED_VALIDATION`, `CHANGES_REQUIRED` or `BLOCKED`, with evidence and the specific blockers if not accepted |
| Stop condition | on reaching the cap, or when the check list is exhausted — the review stops and reports, it does not continue into execution |

### 13.6 Verdict provenance — the check round is AUTHOR-RUN, not independent

The check round in §13.5 was **author-run**: the script, its assertions, the
fixtures and the agent that executed it all belong to the author of the
candidate and of the five token hops. Its PASS means the author's own checks
agree with the artifacts. It is **not** an independent review, and it is not
recorded as one. The payload carries this as
`budget.final_review_round.verdict_kind`.

| Question | Answer |
|---|---|
| Who reviewed? | the **author** of the candidate and of the five hops |
| Independence | **none** — self-verification. The script, the assertions and the runner share one author. |
| What was checked beyond running the suites? | the five hop call sites were located **by reading the source** and each is asserted by the presence of the scope-token plumbing at that site (string-level, not behavioural); identity A was recomputed from the working tree; identity B was recomputed from the payload; the manifests were re-hashed; the input file was re-hashed and probed for holdout bars with a **timestamp-only** read; the interrupted control packet was inspected for the absence of a coverage/summary receipt. |
| What it does **not** establish | that the assertions are the *right* assertions (only that they pass); that no defect exists outside the checked call sites; that the scope change is approved; that the candidate is qualified, tradable or live-ready. |
| What an independent verdict requires | a reviewer **other than the author** reading the code, the assertions and the receipts on the exact A/B/C and reproducing the identity recomputation. |

### 13.7 Scope approval — bound into B, and what it costs to approve

The scope change of §13.3 is **PENDING_OPERATOR_APPROVAL** inside the payload,
so it is part of identity B. Approving it means editing
`approval_state` / `approved_scope` / `approved_by` / `approved_at` in the
payload — which **produces a new B**.

| Rule | Statement |
|---|---|
| Approval changes B | the approval is a payload edit; the run must use the protocol fingerprint of the B that carries the APPROVED state |
| No stale B | a payload edit **after** approval produces a new B and **invalidates** the approval — a fresh approval is then required before the run |
| No approving by editing the verdict | the verdict artifact is an evidence file under manifest C; it cannot carry an approval, and editing it does not change B |
| Pending state today | `approval_state = PENDING_OPERATOR_APPROVAL`, `approved_scope = null`, `approved_by = null` — nothing is approved, and the bound verdict says so |

## 14. Freeze — revision and hash for the pending review

**Preregistration rev20.** Any change after this point invalidates the freeze
and requires a new revision record (and, if it touches source, a new identity A).

| Identity | Value |
|---|---|
| **A — source** | `tree_fingerprint c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74`, `dirty_diff_sha256 1865dc28229abb82fd28eab83c2e29d33901067a3b8896937954ddf68d5685ee`, `code_commit 4078892d738790872dc2d4f494e3973a62612347` |
| **B — preregistration** | `preregistration_fingerprint 1338572d57628e9ba1c29b655856547d1c945f4695ee7cc9aef0545c8ce88145` (§12.1) |
| **C — evidence manifest** | `data/bnb_di_adx_stateedge_20261020_01/manifest.json` and `data/bnb_di_adx_stateedge_prereg_20261020_01/manifest.json` |
| Previous revisions | kept under `revisions/` in both packets. **The archive is partial and the loss is not repairable.** Unrecoverable: rev8, rev10, rev12, rev13 entirely, and rev15 itself (its manifest was overwritten by rev14's). Surviving under **mislabelled** directories: rev9's manifest under `revisions/rev7/manifest.json`, rev14's under `revisions/rev15/manifest.json`. The current manifest cannot recover them — it is a per-revision snapshot with no delta against any prior manifest — and the `data/` tree is untracked, so git cannot either. The loss is recorded as unrecoverable history in `revisions/README.md` and in the tracker report, and it is **not** repaired by fabricating manifests. From this revision on, every manifest is archived before it is superseded, and the archived manifests are themselves sealed by manifest C. |
| AC01–AC15 | rc 0, ALL PASS, identity identical before/after the acceptance run |
| Control packet | `bnb_native_adx25_validation_run_20261019_01` — **unchanged**, still FAILED/INCOMPLETE |

**What changed since rev2** (the reason identity B moved): rule C was
corrected — a change to a report, receipt or verdict is a **change to the
evidence**, not bookkeeping, and rule C may not be used to legitimise a
content edit after acceptance; the evaluation ledger was refined to separate
`planned` / `attempted` / `completed` / `failed`, to record **only attempted**
evaluations, and to be written durably after each attempt; and the scope
token's binding to the execution identity was written down with its
verification pointers.

**What changed since rev1** (the reason identity A moved): the controlled
execution path was implemented — `candidates.py` (scoped descriptor, scope-token
map, `build_default_registry(scope_token=...)`), `tournament.py`
(`run_cell(scope_token=...)`), `scripts/full_system_backtest.py`
(`FullSystemSimulator(scope_token=...)`) and `campaign_execution.py` (the token
travels in the execution context). All four changes are **default-off**: with
no token the behaviour is byte-identical.

**The identity is re-verified at the moment of any approved run**; a mismatch
between the frozen fingerprints and the run's fingerprints is fail-closed.

**What the freeze does not do.** It does not authorise a run, it does not
register the candidate broadly, it does not approve a budget, and it does not
assert anything about profitability. It is the artefact the independent review
reads.
