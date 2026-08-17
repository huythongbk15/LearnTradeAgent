# Research Methodology and Evidence Policy

Status as of 2026-08-17: methodology is implemented and unit tested; no strategy
has earned production status. Mainnet remains **NO-GO**.

## Canonical research path

```text
immutable input manifest
  -> content-addressed feature artifact
  -> append-only experiment registry
  -> nested expanding walk-forward selection
  -> untouched outer OOS evaluation with costs
  -> calibration/conformal/uncertainty artifacts
  -> Forecast -> RiskDecision -> TargetExposure
  -> evidence-gated promotion ladder
```

Research code never owns a broker. The canonical runtime contract is:

```text
MarketObservation -> ForecastStrategy -> Forecast -> ForecastRiskPolicy
                  -> RiskDecision -> TargetExposure
                  -> OrderPermission -> Execution
```

The strategy receives only `MarketObservation` and returns a frozen `Forecast`.
Research, backtest, paper, testnet and shadow use the same strategy/risk pipeline;
their adapters only publish or execute the already-governed decision. Legacy
DataFrame strategies remain for compatibility but are not the production contract.
An unimplemented legacy plugin adapter raises explicitly and cannot silently emit
an empty signal list.

## Return and transaction-cost accounting

For target exposure \(w_t\), forward asset return \(r_{t+1}\), and proportional
cost \(c\):

```text
gross_return[t] = w[t] * forward_return[t]
turnover[t]     = abs(w[t] - w[t-1])
cost[t]         = turnover[t] * cost_bps * 1e-4
net_return[t]   = gross_return[t] - cost[t]
```

Sharpe, drawdown, volatility and return are computed from the resulting return
series, not from rank correlation. Annualization is derived from timeframe and
forward horizon. Cost stress and OOS objective use net results.

## Leakage controls and model selection

- Robust center/scale and sign are fit on train only, then frozen for validation/test.
- Outer folds are expanding chronological folds with purge and embargo.
- Candidate selection and parameter choice occur only in inner folds.
- Each outer test fold is evaluated once after selection.
- The composite is an equal-weight mean of selected, train-standardized factors;
  no grade-string ordering or unverified learned weighting is used.
- PBO/CSCV operates over the full searched trial space.
- Effective trial counts come from the append-only SQLite experiment registry;
  callers cannot reduce multiplicity with a manual number.
- The final holdout manifest stays immutable and cannot be used for iterative tuning.

## Provenance

An `ExperimentSpec` freezes code/config/data/search-space identity. Evaluation
records are append-only. Feature identity binds code, parameters, input manifest,
schema, symbol, timeframe and framework version with SHA-256. Ambiguous feature
lookups fail; Parquet/CSV reads are manifest-driven and symmetric.

Every promoted result must identify:

- full commit SHA and model artifact ID;
- dataset/feature manifest hash and exact windows;
- experiment/trial records and full searched space;
- costs, timeframe, horizon and trade counts;
- inner-selection and outer-OOS metrics;
- calibration/conformal/drift artifacts where applicable.

## Online adaptation and regime methods

Market observations and realized outcomes are separate events. Fixed fast/medium/
slow EMA experts keep stable identities and independent state. The online allocator
uses delayed outcomes, non-negative capped-simplex weights, minimum observations,
turnover penalty, uncertainty shrinkage and an audit log. No indicator is updated
twice for one market event.

Regime decisions use the full posterior, not only the argmax label. Expert forecasts
are mixed by posterior probability and then shrunk by normalized entropy. Duplicate
latent labels aggregate probability mass. High-entropy/unknown states abstain or
reduce exposure; uncertainty cannot increase conviction.

## Calibration, conformal uncertainty and drift

- Isotonic, Platt and temperature calibrators fit on train and are scored on a
  disjoint validation window with Brier, ECE and reliability-bin data.
- Split conformal intervals use a frozen calibration artifact.
- Calibration states are `CALIBRATED`, `DEGRADED`, `UNCALIBRATED` or `STALE`.
- A governed risk increase requires current calibrated evidence and an interval
  that does not cross zero.
- Exposure multipliers are monotone non-increasing in ECE, OOD score, interval
  width and regime entropy.
- Drift bins are fit on reference only. Monitoring includes PSI, Wasserstein, KS,
  volatility log ratio, Fisher-z correlation distance, ECE/Brier, spread, fill,
  latency, adverse selection and Page-Hinkley change detection.

## Execution-simulator evidence

Calibration observations carry exchange, symbol, timestamp, book snapshot, order
and fill identifiers, latency, slippage, fill ratio, partial/time-to-fill and
adverse-selection horizons. Dataset/profile IDs are content-addressed and source is
one of `SYNTHETIC`, `TESTNET`, `SHADOW` or `LIVE`.

Synthetic observations are always `HEURISTIC`. They cannot be relabeled testnet or
used as empirical promotion evidence. Reality-gap gates compare distributions and
tails (p50/p90/p95/p99/CVaR95 and Wasserstein), fail on missing critical evidence,
and apply stage-specific thresholds.

## Promotion ladder

```text
EXPLORATORY
  -> RESEARCH_VALIDATED
  -> PAPER_ELIGIBLE
  -> TESTNET_ELIGIBLE
  -> SHADOW_ELIGIBLE
  -> CANARY_ELIGIBLE
  -> CANARY
  -> PRODUCTION
```

Stages cannot be skipped. Promotion accepts only immutable, content-addressed
`EvidenceArtifact` records whose subject, source, payload and validator match their
hash. Boolean assertions such as `artifact_ok=True` are forbidden.

| Target | Required evidence |
| --- | --- |
| RESEARCH_VALIDATED | Positive outer OOS net return, minimum trades, DSR, PBO, positive cost stress, parameter stability |
| PAPER_ELIGIBLE | Verified artifact integrity |
| TESTNET_ELIGIBLE | Artifact integrity, execution simulation with zero invariant breach, empirical reality gap |
| SHADOW_ELIGIBLE | Empirical shadow calibration and acceptable drift/uncertainty health |
| CANARY_ELIGIBLE / CANARY | 30-day testnet and shadow operational evidence plus named operator approval/ticket |
| PRODUCTION | 30-day canary with zero safety breach plus separate production approval/ticket |

## Deterministic methodology benchmark

Command:

```bash
python scripts/benchmark_methodology.py
```

Seed `20260817`, synthetic diagnostics only:

| Comparison | Candidate | Baseline | Honest status |
| --- | ---: | ---: | --- |
| Selected standardized alpha ensemble vs all-factor equal standardized | net Sharpe 0.919794 | 0.311397 | Candidate wins this synthetic fixture only |
| Soft regime mixture vs trend×vol | net Sharpe 2.603149 | 0.051799 | Oracle-assisted hidden synthetic state; not deployable evidence |
| Adaptive experts vs fixed | net Sharpe -0.118410 | fixed equal 0.114789; best fixed ex-post 0.354157 | Adaptive loses; no superiority claim |
| MPC vs TWAP/POV | N/A | N/A | Not empirically benchmarkable without calibrated impact and held-out order data |
| Platt calibrated vs uncalibrated | Brier 0.210666, ECE 0.054718 | Brier 0.226423, ECE 0.114750 | Improves independent synthetic test only |

These benchmarks test behavior and falsify unjustified sophistication claims. They
are not OOS market evidence and cannot satisfy any empirical promotion gate.
