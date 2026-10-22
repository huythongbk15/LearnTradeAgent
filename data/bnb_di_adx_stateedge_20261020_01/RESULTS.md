# `di_adx_state_edge` — contract + synthetic evidence — 2026-10-20 (rev24)

Path B **step 1 (contract) and step 2 (synthetic)**. Step 3 (preregistration) is
drafted and lives in `../bnb_di_adx_stateedge_prereg_20261020_01/PREREGISTRATION.md`;
**no run is authorised**. Step 4 (bounded evaluation) has not started.

**Status: PROPOSAL.** The candidate is implemented but **not registered** in any
strategy registry, so it cannot be selected by name by a campaign, worker or
qualification run. It inherits **no** result, metric or parameter from the
frozen `ma_adx` crossover control, which stays unchanged and remains the
control.

No market run, no campaign, no new data, no holdout read, no policy/gate/risk
change, no live or paper action.

---

## 0. What changed in rev2 (reviewer feedback)

| # | Reviewer point | What was done |
|---|---|---|
| R1 | The id `state_entry_ma_adx` advertised an MA component the candidate does not have; MA must not be re-added in the same experiment | **Renamed to `di_adx_state_edge`** (class `DiAdxStateEdgeStrategy`, file `di_adx_state_edge.py`). The contract now carries a first-class **MA-1…MA-4** rule block: no MA parameter, no MA computation, and re-introducing the MA pair would be a **new candidate with a new identity**, not a revision. |
| R2 | The stop must be a **real native** engine stop, with "no BUY again in that episode" and "BUY allowed at a new rising edge" — a simulated "stopped" state is not enough | New **S5**: the fixture produces a genuine engine close with `reason == "stop_loss"` (native protective stop, 5 % distance, `current_price_trigger_with_slippage_not_prior_low_next_open`), and the test asserts — from the engine's own action record — that no BUY action occurs between the stop and the next rising edge and that a BUY **does** occur at the next rising edge. A simulated state is not used anywhere. |
| R3 | The k+2 timing must be checked by **timestamp and real fill**, not by comparing two series built from the same helper | New **S9**: for every real trade, the engine's `entry_time` is matched to the **timestamp of bar k+2** of a real rising edge, and the engine's `entry_price` is checked against that bar's **own open** within the native slippage band. The expected side comes from the edge list, the observed side from the trade record. |
| R4 | Keep the guard that a real trade occurred, to avoid a vacuous pass | **S10** guard added on every engine-level test (`assert trades`), plus an explicit non-vacuity note. The first version of the engine test injected the full-frame series instead of the canonical series, shifted every action by one bar, produced **0 trades** and still satisfied the identity assertion — the suite now catches that class of failure. |

## 1. The contract

Full text: [`STATE_EDGE_CONTRACT.md`](STATE_EDGE_CONTRACT.md). Load-bearing clauses:

| Element | Rule |
|---|---|
| State | `B(k) := plus_di(k) > minus_di(k) AND adx(k) > 25.0` — native ADX/DI arithmetic, unchanged from the control |
| BUY | rising edge of `B` only: `B(k)` and `B(k−1)` is False |
| SELL | falling edge of the same state — **not** an MA down-crossing |
| Predecessor rule | a transition needs a prior observation; a null predecessor is **not** False, so bar 0 asserts no edge |
| Re-entry | a BUY after a protective-stop exit requires a **new rising edge**; no re-entry inside the same episode |
| Cooldown | native 24 h circuit breaker, unchanged meaning (`risk_circuit_breaker_not_every_sell_fill`); the candidate adds no lockout |
| Stop / authority / sizing / cost model / accounting | native, unchanged — the full frozen field list is in contract §4 |
| Terminal inventory | open position at window end is marked and carried out, never force-liquidated |
| MA | **absent** (MA-1…MA-4) |
| Timing | a trigger observed on bar `k` is actionable no earlier than bar **`k+2`'s open**. The frozen native contract already states this in its own field: `signal_timing = "canonical_i_minus_1_to_open_i"`. |

## 2. Implementation

`src/trading_agent/strategies/di_adx_state_edge.py` — `DiAdxStateEdgeStrategy`.

- **No `@register_strategy`** and **not imported** by `trading_agent/strategies/__init__.py` → absent from the legacy registry.
- Absent from the **default** canonical registry → no campaign, worker or CLI can name it.
- **Controlled execution path (rev2)**: resolvable only with the exact scope token
  `bnb_di_adx_stateedge_prereg_20261020_01`, via
  `build_default_registry(scope_token=...)` /
  `build_legacy_candidate(..., scope_token=...)` /
  `build_parameterized_adapter(..., scope_token=...)`, plumbed through
  `run_cell(scope_token=...)` → `_run_cell_impl` → registry/adapter/simulator and
  through the worker's execution context. A missing or wrong token **fails
  closed** (the cell becomes a FAILED artifact with a resolution reason — never
  a silent substitution).
- Verified at runtime: candidate absent from the legacy and default registries;
  the control `ma_adx` present and unchanged; the scoped registry resolves the
  candidate only with the right token. Regression:
  `tests/test_di_adx_state_edge_scoped_path.py` (11 tests).
- Broad registration remains a **separate, later** decision and is not
  preregistered here. The scope token is a **scope control, not a capability
  control** — it is not a security boundary.

## 3. Synthetic evidence

`tests/test_di_adx_state_edge_contract.py` — **15 tests, all PASS**.
Synthetic prices only: no market data, no parquet, no storage read, no network.

| # | Property | Result |
|---|---|---|
| S1 | No look-ahead — full-frame path and canonical path | PASS |
| S2 | Canonical == full frame, shifted by one, above warmup | PASS |
| S3 | No repeated BUY — exactly one BUY per state episode; a new episode yields a new BUY | PASS |
| S4 | Exit on falling edge — exactly one SELL per episode end, never without a prior BUY | PASS |
| S5 | **Native protective stop, engine level** — a real `stop_loss` close, no BUY between the stop and the next rising edge, a BUY at the next rising edge | PASS |
| S5b | **"Stop while the state is still ON", directly** — the state is asserted **ON at the stop bar** from the candidate's own state column, and no BUY occurs in the rest of that episode | PASS |
| S5c | **Isolated state-machine check** — for a state that stays on across a stop, the candidate is flat and emits nothing until the state cycles off and on; no price path, no engine, no fill | PASS |
| S6 | Cooldown is a circuit breaker; bar-0 rule; no cooldown-specific signal | PASS |
| S7 | Terminal inventory — `runtime BUY fills − carry_out == closed trades`; a carried-out position is still open, not counted as a closed trade, and is marked into the equity basis | PASS |
| S8 | Parameter / registry separation — unregistered, no MA parameter, control untouched | PASS |
| S9 | **Timing by timestamp and real fill** — every trade's `entry_time` equals the timestamp of bar k+2 of a real rising edge, and `entry_price` equals that bar's open within the native slippage band | PASS |
| S10 | Non-vacuity — every engine-level test asserts a trade actually occurred | PASS |

### 3.1 The fixture

A deterministic synthetic path: a flat, drift-free warmup (so the state is
observably OFF), a short strong uptrend that turns the state ON, a **deep
intrabar low** that trips the native 5 % protective stop while the position is
open, a decline, a pullback, and a rally that turns the state ON again. The
engine records: 2 BUY actions, 1 SELL action, 1 closed trade with
`reason == "stop_loss"`, 1 carry-out position.

### 3.2 The "stop while the state is still ON" case — the earlier claim was wrong
An earlier revision of this packet recorded that the case could not be
constructed, because a deep intrabar low creates a large `down_move` and the DI
flips. **That was wrong and the reviewer was right to push back.** The case is
constructible: a single deep low inside a **continuous strong uptrend** creates
one large `minus_dm`, and the 14-bar DI rolling mean dilutes that one-off
contribution below the sustained `plus_dm`, so the state survives the bar while
the native stop fires on its low. The fixture now does exactly that, and the
test asserts the state is **ON at the stop bar** (S5b) in addition to the
isolated state-machine proof (S5c).

The general no-re-entry property is asserted at the level where it holds for
**every** sequence: **the engine's BUY actions are a subset of the real rising
edges**, so the engine can never invent a re-entry.

## 4. What this step does and does not establish

**Established.** The candidate has a precise, testable contract; its signal
semantics are deterministic, causal and non-repeating; the state/exit/re-entry
rules behave as specified on synthetic fixtures; the **native** protective stop
fires at engine level and the candidate neither re-enters inside the stopped
episode nor re-enters spontaneously; the k+2 timing is confirmed by real
timestamps and real fill prices; the candidate is unreachable by any campaign
until it is registered; and it inherits nothing from the control.

**Not established — and not claimed.**
- **No profitability claim of any kind.**
- **No trade count.** 35–39 state episodes per 1080-bar window (measured on the
  exposed prefix) is evidence about **signal opportunity only**. The control's
  own funnel shows the funnel on real data: 35 BUY actions → 30 fills → 28
  closed trades + 2 carry-out.
- **No projection.** The count in any longer, unrun window is UNKNOWN.
- **No change** to the control, the native authority, the protective stop, the
  24 h breaker, the cost model or any gate threshold.

## 4b. rev3 — the three points the reviewer asked to be locked

| Point | State in this revision |
|---|---|
| **Scope token bound to the experiment/execution identity**, not a free parameter | **Holds, and is now asserted.** The token travels inside the frozen execution context; `execution_scope_identity` hashes the complete context, so contexts differing only in the token have different scope identities; the campaign cache key is a hash over the same context, so a report from another context is never a cache candidate; and a missing/wrong token fails at **registry resolution, before the data read and before the engine is constructed** — the test asserts that no `events.db`, `paper_binance.json` or `report.json` is produced. Three new tests cover it. The residual risk is stated in the prereg: the token is a **scope control, not a capability control**. |
| **Ledger records only evaluations actually attempted** | **Specified in prereg §7.2b (rev3).** Statuses are separated into `planned` / `attempted` / `completed` / `failed`; a planned arm that never ran is reported as **not attempted**, never as a run; the record is written **durably after each attempt** — the append-only per-attempt records (`cell_timings.jsonl`, `cell_attempts/*`) are the durable source and the ledger is **derived** from them, so it cannot drift from the receipts; the study's **actual** evaluation total and the **accounted** (registry) total are reported separately and never conflated. |
| **A manifest change is not always bookkeeping** | **Rule C corrected (rev3).** A change to a report, receipt, verdict or logged output is a change to the **evidence**: the affected part must be re-verified and re-reviewed, and the revision recorded. Only a change touching none of those is bookkeeping, and **rule C may not be used to legitimise a content edit after acceptance**. |

Plus the two old regressions: the envelope test now **pins the approved policy
values as literals** (600 / 7200 / 1 / 128 / 4096) in addition to deriving the
boundary from the constants, and the validation-only CLI test keeps literal
cases (`--cell-timeout 601`, `--total-timeout 7201`) beside the constant-derived
ones — so a silent change to a cap fails the suite instead of following the
implementation.

## 4c. rev4 — the integration review, and what it found

The reviewer asked for the token to be traced **through the intended
entrypoint** rather than unit-tested in pieces. Doing that found **five hops
where the token was dropped** — the scoped candidate was **not executable
through the sanctioned entrypoint at all**:

| Hop | Was | Now |
|---|---|---|
| `build_canonical_cells` | registry without the token — the cell plan could not be built | token-aware |
| `prepare_parallel_protocol` → `audit_parallel_trial_history` | registry without the token (feature-schema hash) | token-aware |
| `QualificationCellRunner` cell-runner context | no `scope_token` in the context — the token was lost at the worker and therefore **not in the cache key** | included |
| `_run_measured` → `_run_cell_worker` | not forwarded to the cell | forwarded, and written into the per-cell `started.json` request record |
| `verify_parallel_result_coverage` (artifact rebuild) | registry without the token | token-aware |

All five are fixed (default-off: a study with no token behaves exactly as
before). The integration test
(`tests/test_di_adx_state_edge_entrypoint_integration.py`, 4 tests, synthetic)
now drives the real chain and proves: the token survives launcher → protocol →
trial context; **the correct token runs the scoped candidate to COMPLETED
through the intended entrypoint**; a wrong token is blocked at **plan time**;
and a token **swapped between plan and execution** fails at **resolution**,
before the data is read and before the engine is constructed, with no engine
artifact.

The same checklist found a **prereg-vs-code discrepancy**: `max_retries` was
hard-coded to 1 in the protocol producer while the preregistration required
0. It is now an explicit parameter (historical default preserved) and the
registered command (§8.3) pins it to 0. The data-scope wording was also
imprecise — "not used" is now "read at the loader level, not evaluated by any
cell".

**The two pre-run conditions are now EXERCISED, not inspected**
(`tests/test_validation_timeout_and_ledger_faults.py`, 2 tests):
- **F1** — a session spawns a descendant that **ignores SIGTERM** and the wall
  deadline fires: the receipt says `TIMEOUT`, the descendant **and** the session
  leader are both dead, the owned group has **no** live PID, and no completion
  receipt (`execution_finished.json` / `exact_coverage.json`) exists;
  `max_retries = 0` is carried in the attempt record.
- **F2** — a killed attempt carries `execution_started.json`, **no**
  `result.json`, `max_retries = 0`, and no `report.json` /
  `execution_finished.json` / `exact_coverage.json` — so it can be reconciled
  only to **INTERRUPTED**, never to completed. The binding rule is prereg §7.2b.

**The data boundary was checked directly, and the earlier wording was wrong.**
The frozen research loader reads the whole file **bytes** (mandatory: the
sha256 binding covers the whole file) and then `pl.read_parquet` **decodes
every row of every column**, applying the `start`/`end` filters only
afterwards. So the prices of **[5600, 6795) are decoded**, not merely
"not evaluated". A **timestamp-only** probe (the same discipline as the
control's metadata probe) shows the file's last bar is **2026-02-06 08:00**,
i.e. **8 hours before** the reserved holdout opens (2026-02-06 16:00), and
counts **0** bars at or after the holdout start. The registered scope is
therefore restated as: **evaluation confined to [0, 5600); the read covers the
whole pre-holdout file**. Whether a whole-file decode is acceptable under
this registration — after the earlier raw-loader incident — is a **reviewer
decision**, not an assertion of this packet.

**F2b/F2c — INTERRUPTED is handled by the aggregation, not only documented.**
`tests/test_validation_timeout_and_ledger_faults.py` (4 tests):
- **F2b** an interrupted cell (FAILED artifact, no result record) → the fold's
  selection decision is **ABSTAIN, never SELECT**, and the packet carries no
  `exact_coverage.json` / `parallel_canonical_summary.json`;
- **F2c** the **real** interrupted cell of the control run was never certified:
  its `execution_finished.json` says `WORKER_ERROR` with `live_authorized:
  false`, and the coverage/summary receipts that certify a completed study were
  **never written**.

**The whole-file decode is a scope change request, not a settled fact**
(prereg §13.3): the registered scope said "only the confirmed-exposed part"; the
truth is that the **read** covers the whole pre-holdout file and only the
**evaluation** is confined to [0, 5600). The condition for acceptance is that
the reviewer verifies the file path + sha256 are the pre-holdout artifact and
that the timestamp-only probe shows no reserved-holdout bar. If refused, a
loader that limits the decode would be required — a framework change, not
proposed here.

**Test scope, stated exactly.** The core-path regression run covered **96
tests: 95 passed, 1 failed**; the failure was an existing test whose stub did
not accept the new optional `scope_token` keyword, the stub was fixed and that
file was re-run (**27/27**), and the remaining 68 were **not** re-run — so the
correct statement is "95/96 passed, the one failure fixed and re-verified on its
own file", **not** "96/96 passed". Which minimum re-run the reviewer requires
is the reviewer's decision.

## 5. D6 — the three reviewer checks, answered against the implementation

The reviewer asked for three things to be verified against the code rather than
asserted. All three were checked; two of them changed the plan.

### 5.1 Do the probes satisfy the sensitivity contract? — **partly no, and the plan changed**

Checked in `nested_wfo._compute_sensitivity_analysis` and
`wfo_sensitivity.sensitivity_requests`:

- The neighbour arm needs **≥ 3 values on the probed axis** (`index±1`); a
  2-value axis is **silently skipped**. The draft's `adx_period ∈ {14, 21}`
  would have produced **nothing** — it is removed from the probe set.
- The arm is built from **outer results** and raises
  `ValueError("sensitivity requires every distinct planned outer fold")` without
  a complete set. It **re-runs the outer test window** — market data outside
  this packet's scope.
- `parameter_stability_ge_070` is computed from `inner_results` (the mode of
  the params **selected per fold**), not from the probes. With one fixed
  parameter set it is **1.0 by construction** — a **vacuous pass** that
  measures no robustness.

**Consequence:** in this packet the parameter-neighbour arm is **NOT EXECUTED**
(a structural consequence of decision D3, not an oversight), `sensitivity_verified`
**cannot** be satisfied here, and `parameter_stability_ge_070` is **not
evaluable as a robustness statement**. The probes are preregistered for the
later outer-OOS preregistration, on `adx_threshold ∈ {20, 25, 30}` only.

### 5.2 The multiple-trial accounting — the earlier claim was wrong, here is the method

`raw_trial_count = Σ over registered experiments of max(1, #distinct execution
scopes)`; the scope is the frozen execution-context hash and **folds do not
multiply it**; the cost scenario is part of the experiment identity.

| Item | Registered | Raw-trial contribution |
|---|---|---|
| Central candidate × 3 costs (5 folds each) | yes (INNER_VALIDATION) | **3** |
| `cost_2x` / `slippage_stress` / `delay_1_bar` arms | **no** — executed via `run_cell` directly, no registry evaluation | **0** |
| `parameter_neighbor__*` probes | **no** — same path; not executed here at all | **0** (would be +1 each if registered) |

So the earlier sentence "the multiple-trial burden is unchanged" was the wrong
way to put it. The correct statement: the probes create **no selection
opportunity** (which limits selection bias), but the DSR's `trials` input does
**not** represent the sensitivity work either — because that execution path
bypasses the registry. The DSR is therefore **not conservative with respect to
the sensitivity work**. This is a framework-level characteristic of every
sensitivity arm, **flagged, not changed** here: altering the trial accounting
would move every campaign's DSR and is outside this scope.

### 5.3 Does the budget cover all the work? — now enumerated

| Work item | Cells | Counted in 5400 s |
|---|---:|---|
| Central inner-validation cells (5 folds × 3 costs) | **15** | yes |
| Budget probe | **0 extra** — it **is** the first central cell (`fold_000`/1x), **reused**, no double execution | yes, once |
| Sensitivity arms | **0** in this packet | n/a |
| Orchestration (spawn, freeze, dispatch, coverage, cleanup) | — | yes |

Worst case 15 × 300 s = 4500 s; the control's own overhead for 15 cells at 90 s
was 440 s, so ≈ 4940 s < 5400 s (≈ 460 s headroom).

Timeout semantics are the existing ones and are sufficient: the worker is
spawned `start_new_session=True` and leads its own process group, so the group
is killed `SIGTERM` → `SIGKILL` and every PID is reaped, with a **fail-closed**
`RuntimeError` if a descendant survives; the session returns
`status`/`worker_exit_code`/`elapsed_seconds`/`sampled_group_peak_rss_mb`; every
cell keeps its own `COMPLETED`/`FAILED` record and `report.json`; and any
failure leaves the packet **FAILED / INCOMPLETE** — never "complete". No
automatic retry (`max_retries = 0`).

### 5.4 Anti-vacuous assertions for the stop test

The engine-level stop test now asserts the four requirements separately: the
stop **really happened** (`reason == "stop_loss"` and `close_reasons.stop_loss
>= 1`); the state was **still ON** at the crash bar and at the stop exit bar;
**no re-entry inside that episode** (no BUY action between the stop and the end
of the episode, and the episode contains exactly one rising edge); and a
**real** BUY at the new edge (a BUY action that produced an actual position — a
closed trade or an open carried position — not merely a signal). The
non-vacuity guard (at least one trade) precedes all of them.

## 6. Next — step 3 is drafted, and needs a reviewer decision

The preregistration is complete as a document:
`../bnb_di_adx_stateedge_prereg_20261020_01/PREREGISTRATION.md`. It carries the
scope the operator set (5 folds / 6-month validation / same windows as the
control; only the confirmed-exposed data; frozen costs; unchanged gates with
the level of each recorded; P2 sensitivity; a re-estimated budget; and an
explicit "preregistration is not a run authorisation").

**Items that need a reviewer decision before it can be signed:**

1. **Parameter-neighbour arm (§7.2, §5.1).** Option B is preregistered, but
   the arm **cannot execute in this packet** — it needs outer OOS, which D3
   excludes. It is preregistered for the later outer-OOS preregistration, on
   `adx_threshold ∈ {20, 25, 30}` only. `parameter_stability_ge_070` is
   **vacuous** with a one-point parameter set and must not be presented as
   robustness evidence.
2. **Budget envelope (§8.1).** `run_wfo_parallel.py --validation-only` enforces
   `--cell-timeout <= 180` and `--total-timeout <= 1800`. The re-estimated
   budget is **300 s per cell** (3.3× the control's worst observed 90.7 s, whose
   90 s budget had no margin and killed one cell) and **5400 s total**. That
   does not fit the sanctioned envelope, so the envelope has to be changed
   explicitly or a different entrypoint named. The fit is measured, not
   guessed: `elapsed ≈ 4.87 + 0.015337 × sim_bars`, max residual 2.6 s over the
   control's 14 completed cells.
3. **Outer-OOS data scope (§3.2).** The control's outer test windows reach bar
   6340, beyond the confirmed-exposed [0, 5600). This packet is therefore
   **inner validation only**; any outer-OOS step needs its own data-scope
   decision.

## 6. Evidence

| File | Content |
|---|---|
| `STATE_EDGE_CONTRACT.md` | the contract (step 1), with the MA exclusion and the frozen native-contract field list |
| `acceptance_receipt.json`, `acceptance_stdout.txt` | AC01–AC15 as captured **inside this packet** — bound to identity `30283d31…` (an EARLIER source identity, not the current one) |
| `acceptance_run_rev24/acceptance_stdout.txt`, `acceptance_run_rev24/source_identity_{before,after}.json` | AC01–AC15 **re-run on the current identity A** `d82bd081…` — all 15 acceptance checks PASS, rc 0 |
| `source_identity_before.json`, `source_identity_after.json` | identical — the capture of the EARLIER acceptance run (identity `30283d31…`); the capture of the current one is in `acceptance_run_rev24/` |
| `../bnb_di_adx_stateedge_prereg_20261020_01/PREREGISTRATION.md` | the single preregistration (step 3) |
| `../bnb_native_review_20261020_01/RESULTS.md` | the control review the candidate derives its motivation from |
| `src/trading_agent/strategies/di_adx_state_edge.py` | implementation (unregistered) |
| `tests/test_di_adx_state_edge_contract.py` | synthetic suite, 15 passed |

No market run. No holdout read. No policy, gate, sizing, authority or
protection change. No registration. No publish, push or merge. The control
candidate and its packets are untouched.
