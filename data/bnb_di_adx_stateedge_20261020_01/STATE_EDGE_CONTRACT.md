# Contract — `di_adx_state_edge` (proposed candidate)

Status: **PROPOSAL — not registered, not eligible for any campaign.**
Path B step 1 of 4 (contract → synthetic → preregistration → bounded evaluation).
Requires independent review before it may be preregistered or evaluated.
This candidate **inherits no evidence** from the `ma_adx` crossover control,
which stays frozen, unchanged, and remains the control.

---

## 0. Name and the MA exclusion (reviewer point 1)

The candidate id is **`di_adx_state_edge`**: a DI/ADX **state-edge** strategy.

> **The previous working name `state_entry_ma_adx` was rejected as
> misleading** — it advertised an MA component the candidate does not have.
> The id now names what the trigger actually is.

### 0.1 Hard rule — no moving average in this candidate

| Rule | Statement |
|---|---|
| MA-1 | The candidate has **no MA parameter and no MA computation**. `fast_period` / `slow_period` do not exist in its parameter set and are not accepted. |
| MA-2 | The trigger is **only** the edge of the state `B(k) := plus_di(k) > minus_di(k) AND adx(k) > 25.0`. |
| MA-3 | **The MA pair must not be reintroduced within this experiment.** Adding MA20/80 back — as an extra state component, as a filter, or as a tie-breaker — would make the result uninterpretable against this contract and would be a **new candidate with a new identity**, not a revision of this one. |
| MA-4 | The control's MA20/80 is retained **only inside the control**, unchanged, as the comparison arm. |

The control `ma_adx` used MA20/80 **as the trigger** and ADX/DI **as a filter**.
This candidate inverts that structure: DI+ADX **is** the trigger and the MA
pair is gone. The two candidates therefore differ in the trigger, not in a
parameter, and the change is intentional and measured against the same windows.

## 1. Why this candidate exists

The frozen control `ma_adx` (BNB/USDT 4h, MA20/80 + ADX14/25 + DI,
event-crossover) fails the 30-trade eligibility gate in every measured cell
(4–7 trades per 1080-bar window). The measured cause is **sample size, not
policy**: on the 3240-bar measured union the ADX/DI gate rejects only ~30 % of
raw crossovers, in both directions, and the per-bar action vector of all 14
completed cells is reproduced exactly (15 120/15 120) from the candidate's own
indicators. See `../bnb_native_review_20261020_01/RESULTS.md` (rev3).

On the same measured windows, the state `B` turns on **35–39 times per
1080-bar window**, against 7–12 raw up-crossovers for the control. That is
**evidence about signal opportunity only** — not a trade count, not a
profitability claim, and not a projection to any unrun window.

## 2. Identity

| Field | Value |
|---|---|
| Proposed id | `di_adx_state_edge` |
| Class | `DiAdxStateEdgeStrategy` (`src/trading_agent/strategies/di_adx_state_edge.py`) |
| Relationship to control | **Separate candidate.** No parameter, signal, metric or result is inherited. The control is frozen and unchanged. |
| Symbol / timeframe | BNB/USDT, 4h (unchanged from the control) |
| Parameter set (fixed) | `adx_period = 14`, `adx_threshold = 25.0` — **two values, no others** |
| ADX / DI definitions | **Unchanged** from `MaAdxCrossover` — same `rolling_mean` ADX, same `plus_di`/`minus_di` arithmetic, same `adx_period`. |
| Token binding | The scope token travels **inside the frozen execution context** and through every hop of the entrypoint chain (launcher → protocol → trial context → cell plan → cell-runner context → worker → `run_cell` → registry/adapter/simulator); the integration review found and fixed five hops where it was dropped, and a synthetic end-to-end test drives the real chain. `execution_scope_identity` hashes the complete context, and the campaign cache key is a hash over the same context — so the token is bound to the experiment/execution identity, and a report from a different context is never a cache candidate. A missing or wrong token fails at registry resolution, **before** the data is read and before the engine is constructed. |
| Registry | **Not broadly registered.** No `@register_strategy`, no entry in `strategies/__init__.py`, no entry in the default canonical registry — so no campaign, worker or CLI can select it by name. It is resolvable **only** through the **scoped** path: the exact scope token `bnb_di_adx_stateedge_prereg_20261020_01` presented to `build_default_registry(scope_token=...)` / `build_legacy_candidate(..., scope_token=...)` / `build_parameterized_adapter(..., scope_token=...)`. A missing or wrong token fails closed. Broad registration remains a separate, later decision and is not preregistered here. |
| Warmup | 100 bars (same as the control; exceeds the 40-bar maximum indicator dependency) |
| Cost model / authority | **Not this contract's business** — inherited unchanged from the native execution contract (§4). |

## 3. Signal semantics

On bar `k`:

- `plus_di(k)`, `minus_di(k)` — DI alignment, native arithmetic.
- `adx(k)` — ADX, native arithmetic.
- **State** `B(k) := plus_di(k) > minus_di(k) AND adx(k) > 25.0`
- `prev(k) := B(k−1)`

| Condition | Signal |
|---|---|
| `B(k)` and `prev(k)` is **False** (rising edge) | **+1 (BUY)** |
| `not B(k)` and `prev(k)` is **True** (falling edge) | **−1 (SELL)** |
| otherwise | 0 |

Three clauses are load-bearing:

1. **A BUY is emitted only on a rising edge of the state.** While the state
   stays on, no further BUY is emitted. This is what bounds entries per window
   to the number of state episodes.
2. **A transition requires a prior observation.** At `k = 0` there is no
   predecessor, so no edge can be established and no signal is emitted. A null
   predecessor is *not* treated as False.
3. **The SELL is the falling edge of the same state**, not an MA down-crossing.
   A position exits when the state ends — ADX fell to ≤ 25, or DI flipped
   against the position.

### 3.1 Re-entry

**A BUY after a protective-stop exit requires a new rising edge.** If the
position was stopped out while the state remained on, the candidate stays flat
until the state turns off and on again. There is no re-entry inside the same
state episode and no "re-buy on the next bar" behaviour.

This is the explicit, restrictive reading of "re-entry". The permissive variant
(re-entry after the native cooldown, inside the same episode) is **not** chosen
and is recorded as a reviewer decision, not a default.

### 3.2 Cooldown

The native 24 h circuit-breaker cooldown applies **unchanged** and keeps its
native meaning: the frozen native contract states
`cooldown_trigger = "risk_circuit_breaker_not_every_sell_fill"` and
`risk.cooldown_hours = 24.0`. It is a **circuit breaker, not a cooldown after
every SELL**. The candidate adds **no** additional cooldown of its own. A
protective-stop exit does not start a candidate-specific lockout; the only thing
that can produce a new BUY is a new rising edge.

## 4. Everything the candidate must NOT change

These are the frozen native execution-contract fields the candidate inherits
verbatim. The candidate adds no policy of its own here.

| Native contract field | Frozen value | Meaning |
|---|---|---|
| `engine` | `FullSystemSimulator_authority_PaperExchange_v1` | the execution engine |
| `protective_stop` | `current_price_trigger_with_slippage_not_prior_low_next_open` | stop semantics |
| `protective_stop.stop_loss.distance_pct` | `5.0` | native stop distance |
| `protective_stop.trailing_stop.enabled` | `false` | no trailing stop |
| `sizing` | `requested_target_exposure_pct 25.0`, `max_target_exposure_pct 25.0`, `max_position_pct 50.0`, `method artifact_target_authority_capped` | 25 % is a **requested allocation / cap**, not a fixed size; the observed moderate-profile sizing is not hard-coded |
| `authority.exposure` | `max_single_strategy_exposure 0.25`, `max_portfolio_exposure 0.5`, `max_correlated_exposure 0.4`, `risk_scale 0.25–1.0` | native exposure caps |
| `risk` | `cooldown_hours 24.0`, `daily_loss_limit_pct 8.0`, `max_drawdown_pct 15.0`, `risk_profile moderate` | native risk |
| `accounting` | `cash_plus_inventory_marked_at_close_no_forced_liquidation` | terminal inventory is **marked and carried**, never force-liquidated |
| `cost_model` | `commission_rate 0.001`, `slippage_rate 0.0005`, `market_impact_rate 0.0`, `spread_rate 0.0`, `estimated_round_trip_cost_bps 30.0` | native cost model |
| `price_path` | `open_then_low_then_close` | intrabar path |
| `cadence_bars` | `1` | evaluation cadence |

### 4.1 Terminal inventory

Unchanged native semantics: an open position at the end of a measurement window
is **marked and carried out**, not force-liquidated, and is not counted as a
closed trade. `runtime BUY fills − carry_out == closed trades` must hold in
every cell report.

## 5. Decision → execution timing (frozen by the native contract, verified)

The frozen native contract already states the rule in its own field:

```
signal_timing = "canonical_i_minus_1_to_open_i"
```

i.e. the canonical signal index `i−1` is executed at the **open of bar `i`**.
Combined with the canonical adapter's point-in-time window (the decision for
index `j` is taken from data strictly before bar `j`), the net rule is:

| Stage | Rule |
|---|---|
| State evaluation | `B(k)` uses only data through bar `k`. |
| Canonical placement | `canonical_signal[j]` is the decision taken from data through bar `j−1`. |
| Engine consumption | the engine reads `signals[i−1]` at execution bar `i` and may fill only at bar `i`'s open. The recorded `decision_bar` is `execution_bar − 1` — the canonical signal index, **not** the bar on which the trigger occurred. |
| **Net** | A trigger observed on bar `k` is actionable no earlier than bar **`k + 2`'s open**. |

Equivalently: `action at execution_bar j == full_frame_signal[j − 2]`.

This was measured on the control's own 14 completed cells (15 120/15 120
bar-level assertions) and must be verified **by timestamp and real fill**, not
by comparing two series produced by the same helper (§6, S9).

The two-bar lag is a property of the engine. The candidate must not attempt to
shorten it.

## 6. Acceptance criteria (step 1 + step 2)

| # | Property | Assertion |
|---|---|---|
| S1 | No look-ahead | Signals on bars ≤ k are unchanged when bars > k are perturbed — on both the full-frame and the canonical path |
| S2 | Canonical-path equivalence | The canonical per-bar path yields the same edges as the full-frame computation (outside warmup) |
| S3 | No repeated BUY | Exactly one BUY per state episode; a new episode yields a new BUY |
| S4 | Exit on falling edge | Exactly one SELL per episode end; no SELL without a prior BUY |
| S5 | **Native protective stop, engine level** | A real protective stop occurs in the engine (native stop order, close reason `stop_loss`); **no** BUY action is emitted between that stop and the next rising edge; a BUY action **does** occur at the next rising edge. Simulating a "stopped" state is explicitly **not** sufficient. |
| S5b | **The "stop while the state is still ON" case, directly** | The engine fixture must show the state **ON at the stop bar** (asserted from the candidate's own state column) and **no** BUY in the remainder of that episode. |
| S5c | **Isolated state-machine check** | Independently of any price path: for a state that stays on across a stop, the candidate is flat and emits nothing until the state turns off and on again. No price series, no engine, no fill. |
| S6 | Cooldown is a circuit breaker | The candidate emits no cooldown-specific signal; the native 24 h breaker semantics are unchanged and the candidate adds no lockout |
| S7 | Terminal inventory | An open position at window end is carried out and marked; `runtime BUY fills − carry_out == closed trades` |
| S8 | Parameter / registry separation | The candidate carries no MA parameter, is absent from every registry, and the control is untouched |
| S9 | **Timing by timestamp and real fill** | For a real trade: `entry_time` equals the timestamp of bar `k+2` where `k` is the trigger bar, and the entry price equals that bar's open within the native slippage band. The assertion must not be satisfiable by comparing two series derived from the same helper. |
| S10 | Non-vacuity | The engine-level tests must assert that trades actually occurred, so the identity and timing assertions cannot pass on an empty run |

## 7. Explicitly out of scope for this step

- No market run, no campaign, no new data, no holdout read.
- No change to the control candidate, to the native authority, to the protective
  stop, to the 24 h breaker, to the cost model, or to any gate threshold.
- No registration of the candidate in any registry.
- No preregistration of data, folds, costs, sensitivity, gates or budget — that
  is step 3 and needs a scope decision from the operator.
- No profitability claim of any kind. The 35–39 episodes/window figure is
  evidence about **signal opportunity only**.
- No MA component (§0.1).

### 7.1 The "stop while the state is still ON" case — constructible after all
An earlier draft of this contract recorded that the case could not be
constructed. **That was wrong**, and the reviewer was right to challenge it.
The case *is* constructible: a single deep intrabar low inside a **continuous
strong uptrend** creates one large `minus_dm`, and the 14-bar DI rolling mean
dilutes that one-off contribution below the sustained `plus_dm`, so the state
survives the bar while the native protective stop fires on its low. The
synthetic suite now proves the case directly (S5b) and independently of any
price construction (S5c).
