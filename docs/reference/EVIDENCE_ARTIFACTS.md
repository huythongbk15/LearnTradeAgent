# Evidence Artifact Catalog

> Status: **CURRENT contracts with TARGET additions marked** · Owner: research governance
> · Verified: 2026-08-26

Artifacts are the boundary between claims and evidence. Filenames are navigation;
artifact identity must come from canonical content.

## Catalog

| Artifact | Producer | Primary consumer | Current contract |
| --- | --- | --- | --- |
| Data manifest | data/backtest loaders | evaluation runner, verifier | Dataset identity, window and quality summary |
| `BacktestReportV2` | full-system/portfolio backtest | evaluator, human review | Schema-validated metrics, ledger and execution reporting |
| Golden replay manifest | `verify_golden_replay.py` | S0/release review | Binds two deterministic runs and their identities |
| `StrategyDescriptor` | canonical registry | evaluator and runtime bridge | Strategy identity, features, warmup and parameter schema |
| `EvaluationArtifact` | tournament cell | tournament index/selector | Cell status, identities, cost/fault scenario, report and metrics |
| Tournament index | tournament CLI | selector and reviewer | Visible inventory of completed/failed cells |
| Selection policy | statistical selector | runtime router | **TARGET:** eligible mapping, score/uncertainty and abstain rules |
| `PromotionRecord` | promotion workflow | runtime resolver | Environment eligibility and promotion state |
| Routing decision | runtime router | risk/execution/audit | **TARGET:** regime evidence, incumbent/candidate and reason |
| Trade attribution | fills/ledger analytics | monitoring/research | **TARGET:** strategy, route, portfolio and execution cost attribution |

## Minimum identity fields

Every evaluation or downstream policy must bind:

| Identity | Why it matters |
| --- | --- |
| Schema version | Enables controlled migration |
| Artifact ID/content hash | Detects mutation and deduplicates content |
| Created time | Audit ordering; not part of strategy merit |
| Commit/code identity | Reproduces implementation |
| Data manifest hash | Reproduces observations and evaluation window |
| Strategy/descriptor ID | Prevents strategy substitution |
| Parameter hash | Proves which configuration produced signals |
| Timeframe/symbol universe | Defines evaluation scope |
| Cost/fault model identity | Prevents optimistic execution assumptions |
| Parent artifact IDs | Preserves lineage across selection and promotion |
| Status and failure reasons | Keeps failed evidence visible |

## Backtest report

The canonical report schema is implemented in
`src/trading_agent/backtest/report_v2.py` with its JSON schema under
`src/trading_agent/backtest/schemas/`. Consumers must validate the schema rather
than rely on optional dictionary keys.

Headline metrics alone are insufficient. A trustworthy report also exposes:

- evaluation period and data quality;
- capital/equity identity;
- trade or fill ledger;
- fee/slippage/impact attribution where supported;
- benchmark and drawdown context;
- execution rejects, partial fills and health signals;
- warnings and known limitations.

## Evaluation artifact

`EvaluationArtifact` in `src/trading_agent/backtest/tournament.py` represents one
strategy × symbol × timeframe × parameter set × cost scenario × fault profile.

Rules:

1. A cell has one stable identity.
2. `FAILED` is a valid evidence state, never an omitted row.
3. Metrics are read only from a validated completed report.
4. The report strategy identity must match the descriptor/cell identity.
5. A selector excludes failed or incomplete cells by construction.
6. Re-running an existing cell must be explicit.

## Selection and promotion separation

Selection answers “what is supported by research evidence?” Promotion answers
“what may this environment run now?” Combining them makes independent review,
expiry and rollback impossible.

```text
EvaluationArtifact[]
  → SelectionPolicyArtifact
  → reviewed PromotionRecord
  → StrategyRuntime
```

## Storage and retention

- Keep immutable evidence under `artifacts/` or a run-specific data directory.
- Never overwrite a passing artifact with a new run that happens to share a label.
- Store large ledgers by content hash and link them from the summary.
- Preserve failed artifacts for debugging and bias analysis.
- Redact credentials, account IDs and raw private broker payloads.
- A `latest` pointer is convenient but never authoritative.

## Verification questions

- Can another reviewer reconstruct the evaluated data and code?
- Do the recorded parameters actually control signal generation?
- Can a report be swapped without changing artifact identity?
- Are failed cells visible?
- Can runtime prove which promotion authorized a decision?
- Is rollback possible without editing historical artifacts?

