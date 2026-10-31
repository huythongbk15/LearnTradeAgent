# Validation closure — 2026-10-16

Scope: calibration input contract, existing BTC/USDT 4h inner-validation
packets, and current-state documentation. Not qualification or live acceptance.

## Latest combined closure — LOCAL_VERIFIED_REVIEW_PENDING

**Current research follow-up:** diagnostic
`data/calibration_telemetry_btc4h_20261016_01` was subsequently authorized and
executed: ACCEPTED_CALIBRATION,2cells completed, both ABSTAIN. Detailed metrics
and authority history are in [tracker](../CAMPAIGN_STATUS.md). No qualification
or live acceptance. Next work follows [multi-strategy plan](MULTI_STRATEGY_RESEARCH_PLAN.md),
starting with raw/accounting review and compatibility inventory, not another
automatic campaign. Freeze/approval blockers below describe earlier snapshots.

Current integrated revision: `cdf39ce132a22bac32e5daff8c95f009bf9ff567`.
Fresh verification:139 regression tests, AC01–AC15 and Ruff PASS;350protected
files and execution-source identity stable. Source scope clean, not a claim that
all unrelated workspace files are clean. Authority:
`data/retire_wfo_wrapper_20261016_01/integrated_verified.json`.
Read-only preflight12/12PASS in `preflight_current.json` in the same directory.
No strategy qualifies from this engineering evidence. Next authority boundary:
explicit fresh diagnostic freeze authorization, followed by operator approval
of the frozen fingerprint before measurement. No freeze/execution was performed.
The older receipts and blockers below are retained as historical snapshots.

Selected-code integration follow-up: automated read-only reviewer
`/root/review_readiness_integration` found no blocking integration issues;
optional lifecycle lookup profiling is deferred. This is not a human/market
approval. Receipt and subsequent commit verification are stored under
`data/readiness_integration_20261016_01/`. Only reviewed code/tests/docs are
selected; operational DB/state, raw packets, unrelated .gitignore edits and
QwenPaw environment-specific tests are excluded.
Implementation commit2fc02acae9e02ee259718afa76312411ed354867 contains15selected
files on codex/readiness-integration-20261016. Source tree hash remains04ba6a07…;
post-commit QA, rather than pre-commit evidence alone, is required and recorded
under the integration evidence directory. Nothing is pushed.

Wrapper follow-up explicitly authorized by the user: scripts/run_wfo_b_phase.sh
is retired as an inert exit64 stub. Original bytes are backed up under
data/retire_wfo_wrapper_20261016_01/; five focused tests cover syntax and rejection
of normal/execute/force-resume/help calls without packet changes. Separate review
and post-commit source identity are recorded there. Earlier dirty-source receipts
remain historical; whole-workspace cleanliness is not claimed. No packet deletion,
diagnostic freeze, campaign execution or push is authorized by this change.

`data/readiness_closure_20261016_01/verified.json`:134 regression tests PASS,
AC01–AC15:15PASS, Ruff PASS. Before/after source/config/tests and348protected
files match. Source tree04ba6a07cc3e66a8311191093f32b14761b33bf85150b9dbbaca7e21193eba64,
dirty master9b69462. AC packet: `data/acceptance_runs/ac01_15_20261016_081845_utc`.
Earlier statements below about no new AC apply to earlier telemetry snapshots,
not this combined run. No full-repository or independent acceptance is claimed.

Additional engineering fixes: plan_promotable_campaign.py now accepts an explicit
frozen protocol, verifies its fingerprint/native gate and input byte binding,
reads ONLY timestamp columns, and plans on pre-holdout history. It uses canonical
history, minimum-fold semantics and p<=0.10, not stale p<=0.20/n>=14 heuristics.
It does not recommend a winner, disable sensitivity or invent an ETA. Legacy
build_campaign_policy.py exits before old data/registry/policy activity even
with --dry-run. Historical code is retained but its CLI route is retired.

Metadata-only outputs: `data/funnel_trace_20261016/readiness_geometry_ma_adx.json`
and readiness_geometry_vol_target.json. They expose the sample-size tradeoff:
3/6/9/12-month validation yields6/5/4/3folds under the examined history; p<=0.10
needs6/5/4/impossible cost-clearing folds. Trade adequacy is unmeasured, and
overlap/independence/statistical power need separate review. A geometry-pass
field is intentionally NOT a qualification or execution authorization.

Next bounded proposal is `data/readiness_closure_20261016_01/diagnostic_proposal.json`,
not an executed/frozen calibration packet. It uses an existing6795-bar input,
not the broader7946-bar historical file that extends into/past holdout. Metadata
reads are not a holdout price evaluation; historical measurement windows remain
earlier and this finding alone does not prove look-ahead contamination. (Correction
2026-10-31: true for the parallel WFO protocols that bind the 7946-bar file, whose folds
end at bar 6340 or earlier. Not true for the 2026-10-12 calibration packets
`calibration_20261012_013330_utc` and `_031500_utc`: 21 reports simulate past the
boundary and one has measurement window [5984,7064]; see `docs/RESEARCH_HOLDOUT.md`.)

Remaining external/empirical prerequisites: independent review/integration;
operator-approved fresh source/protocol/budget; actual measured funnel; a
qualified strategy; exact policy-to-live-runtime identity binding; then separately
approved operational acceptance. Existing Binance runner is Enhanced-MA-bound;
do not send ma_vol_target evidence to it or create placeholder qualification.

## Engineering

`freeze_plan()` invokes the verifier's strategy validator before input resolution,
Parquet reads, registry access or output creation. Empty, duplicate, unknown and
wrong-type requests fail with ValueError. None keeps five default families;
valid synthetic requests remain RSI-only. No threshold, ADX preset, holdout or
source/reuse rule changed. Invalid synthetic strategy names are now rejected too.

The regression calls the producer with a nonexistent input, makes Parquet and
registry access fail if reached, and asserts no output directory exists. Five
invalid cases are covered. Module result: **70 passed**, rc0, 98.32s pytest /
102.87s supervisor. Evidence: `data/calibration_closure_20261016_tests.json`.
No mandatory-gate source file was edited; no new integrated AC claim is made.

## Existing market evidence — read-only diagnosis

Both packets used source `c9a255d2cf0a3a217a344191b306143d76fca5554cec66cab3c2bd59b77a5800`
at dirty HEAD `9b694620ec759ef4441307f8c162d6d23b744e71`. These historical
measurements are not results of the input-contract patch above.

| Packet | Candidates/fold | Folds / inner cells | Closed trades by fold | Verdict |
|---|---:|---:|---|---|
| `data/wfo_ma_adx_btc_4h_v4` | 9 | 2 / 18 | 2–4; 0–4 | ABSTAIN / ABSTAIN |
| `data/wfo_vol_target_btc_4h_v1` | 4 | 2 / 8 | 5; 4–6 | ABSTAIN / ABSTAIN |

Each validation window contains 540 four-hour bars (90 nominal days). Resolved
frozen folds have purge=100 and embargo=100 despite zero in input spec fields;
reason about actual geometry, not only the unexpanded input. Selection requires
>=30 trades and Sharpe >0. All candidates fail trade count; some also fail Sharpe.

All 26 raw hashes match the declared inventory. Report total_trades equals
attributed_closed_trades; unattributed_cross_boundary_trades and circuit_breakers
are zero throughout. Some reports end with one open position. Do not count an
open position as enough completed independent trades to manufacture eligibility.

The immediate refusal is explained: insufficient closed-trade density under the
registered windows/threshold. This does NOT establish the deeper cause of sparse
signals or exclude every risk/filter bug. An empty signals_seen logging collection
is not proof of no forecasts when a report contains filled trades.

Audit: `data/calibration_closure_20261016/review_result.json` (controlled result;
stdout contains machine-readable analysis). This checks hashes and attribution,
not full independent verifier acceptance. No packet bytes were rewritten.

## Next bounded decision

### Measurement telemetry implementation (2026-10-16)

Implemented observation-only `execution_funnel` in FullSystemSimulator and
isolated it through the tournament measurement exporter. `signals_seen` retains
its old meaning; consumers should not reinterpret the legacy zero count.

Every completed simulator bar has one exclusive terminal outcome: bootstrap,
no_signal, cadence, breaker, cooldown, position skip, or the runtime result.
Each row includes decision/execution bar indices, pre/post position state,
runtime prepare status/signal and authority reason when available. Strategy
returned orders include order/intent identities and cumulative fill snapshots;
measured closes include trade/entry-order/exit-order identities and close reason.

Semantics and limits:
- Scope is execution bar [start,end), not forecast generation timestamp.
  A decision immediately before start is marked decision_precedes_scope.
- Input action is the simulator gating signal, not a raw posterior or all
  internal strategy computations. For adaptive routing it may be a gate sentinel;
  runtime_signal is separate. Adaptive parity is not established by fixed tests.
- One intent is not one fill. Snapshots are NOT incremental lifecycle fills.
  Protective orders are excluded from strategy-returned-order counters; stop
  exits are visible in closed-trade attribution. Event-store data remains the
  source for complete fill/retry/protection histories.
- Missing old telemetry is None, not fabricated zero evidence. Warm-up rows and
  their order IDs are absent from measurement export. Open positions at scope
  boundaries are carry-in/out flags, not additional closed trades.
- Telemetry does not feed selection, permission, order planning or sizing.
  No market packet is rewritten and old evidence cannot be reused cross-source
  merely because this is intended as observation-only.

Tests exercise the real simulator loop with a fake broker for branch coverage,
and a real paper engine with synthetic OHLCV and canonical runtime for economic
parity. They compare metrics, equity and trade economics with telemetry on/off;
generated IDs may differ. A tournament integration case checks that only measured
bar records survive and close counts match the isolated report.

Verification receipts are in `data/funnel_trace_20261016/`:
`telemetry_tests_01.json` (12 PASS), `telemetry_tests_02.json` (49 PASS),
and `telemetry_tests_final.json` (**120 PASS**, 124.81s pytest, rc0): telemetry,
CLI, tournament, cadence/sensitivity and calibration regressions.
Ruff PASS: `telemetry_lint_final.json`. A test lambda was changed to a def;
`telemetry_post_lint_tests.json` records the focused rerun after that cleanup.
That focused rerun is9PASS. Final source capture `telemetry_source.json` records
dirty HEAD9b69462, tree e416285c0f2168fefe086a228815aeb43c109c47c787f76e521f3024374bf917.
This is a post-verification identity capture, not a before/after stability proof
for every test invocation. It does not retroactively certify old market packets.
Diagnostic runs overlap; never add their test counts. Full-repo, adaptive routing
parity, performance/memory benchmarking and independent review remain outside
this verification. No mandatory-gate source file was edited.

Usage: FullSystemSimulator defaults funnel_enabled=True; False is available for
controlled parity comparisons. Read actions and terminals as separate bar
partitions (each sums to bars), runtime_calls as a subset, returned_orders as
order observations and closes as attributed trades. Do not sum overlapping
folds/candidates into independent samples. No live runner is instrumented.

Next: review/integrate this source and approve an exact bounded diagnostic
packet/budget before obtaining empirical funnel counts. This implementation
does not itself authorize that market measurement or strategy promotion.

### Completed follow-up: persisted funnel trace (2026-10-16)

Evidence: `data/funnel_trace_20261016/result.json`; analyzer:
`data/funnel_trace_20261016/trace.py`. All26 raw hashes match and observed
report/paper-state/SQLite bytes remained stable across reads. Databases were
opened mode=ro. No price data or simulator was invoked.

| Stage | Established | Unknown |
|---|---|---|
| Forecast | Crossover events; MA ADX adds ADX/trend filters | Raw forecast counts per measured window |
| Risk/position | Code skips already-long BUY, flat SELL, breaker/cooldown | Per-reason rejection counts |
| Intent/fill | Lifecycle records contain authorized intents and fills | Forecast-to-fill conversion denominator |
| Close | Signal and stop-loss exits exist | Future sample density and alpha |

Across measured candidate/fold cells: MA ADX has46 trade rows (21 signal exits,
25 stop-loss exits); Vol Target has41 (22 signal,19 stop-loss). These combine
overlapping experiments: NOT independent samples or portfolio performance.
Holding-bar metadata spans1–295 and2–167; cross-boundary durations can include
pre-measurement history.

Lifecycle totals are FULL simulation, including warm-up/train: MA ADX has826
intent-created and634 fill rows; Vol Target705 and540. Protective/cancel orders
are distinct operations. These are NOT measurement-only forecast conversion rates.

Confirmed observability loss: tournament.py deliberately resets signals=[] and
signals_seen=0 when isolating measurement. full_system_backtest.py appends
signal_log only AFTER cadence, breaker/cooldown and position filters, so even
that collection is not the raw forecast denominator. Its authority_trace is
discarded with the list. Event stores cannot recover pre-intent rejections.
Do not fabricate missing historical counters or claim which filter dominates.

Semantic finding: MaVolTargetCrossover computes realized_vol but returns only
signed crossover signals. The traced canonical bridge maps actions to +/-1,
passes default_target_exposure_pct=0.25 and runtime scales by that target.
These packets do not demonstrate volatility-dependent sizing. Clarifying the
name/documentation or implementing real vol sizing is a separate reviewed
strategy/protocol change, not grounds to reinterpret old evidence.

Synthetic tests:45 passed across test_canonical_contract.py and
test_canonical_wave_c.py. Receipt: `data/funnel_trace_20261016/contract_tests.json`.
These are contract/adapter tests, not full funnel or live acceptance.

Next implementation proposal: measurement-scoped telemetry with per-bar
evaluated/BUY/SELL/HOLD counts, exclusive terminal skip reasons, authority
outcomes, intent/order/fill identities and close reasons. Include decision and
execution bar indices, warm-up/measured scope, carry-in/out. Synthetic tests
must cover already-long, flat SELL, cooldown, authority block, stop loss and
measurement boundaries, and assert unchanged orders/trades with telemetry
on/off. Do not equate intent counts with fills or protective orders with entries.
A market diagnostic still requires a separately approved bounded protocol.

1. Trace one candidate's existing forecast/entry/exit/risk evidence. If the funnel
   counters are absent, design and synthetically test forecast -> intent ->
   rejected/accepted -> fill -> close observability before a market replay.
2. Review fold feasibility: available pre-holdout history, minimum trades,
   warmup, resolved purge/embargo and sample independence. A new window requires
   a new frozen research protocol, not reinterpretation of these packets.
3. Operator chooses whether to authorize a bounded diagnostic or new protocol.
   Preserve trial burden; no lower thresholds, automatic ADX changes or holdout.
   Do not extrapolate these short-window trade rates into guaranteed sample sizes.
4. Only eligible candidates proceed to the separate OOS/sensitivity/holdout,
   review and runtime acceptance stages. No strategy qualified here.

Prior AC evidence `data/acceptance_runs/ac01_15_20261015_091209_utc` is 15/15 PASS
on c9a255d2, not acceptance of the changed source. The 70 tests are synthetic
engineering verification, not empirical alpha evidence or a full-repo suite.

No market campaign, data download, holdout, broker, policy publication,
commit/push or QwenPaw restart. Mainnet remains NO-GO.
