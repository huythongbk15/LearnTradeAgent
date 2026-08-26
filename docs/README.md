# Trading Agent System — Documentation Hub

> **Verified:** 2026-08-26 · **Mainnet:** `NO-GO` until the evidence in
> [Live Readiness](LIVE_TRADING_TODO.md) and
> [Capability Matrix](CAPABILITY_MATRIX.md) says otherwise.

This page is the canonical entry point for project documentation. Documents are
classified so that implemented behavior, target architecture and historical
plans cannot be confused.

**Tiếng Việt:** [Trung tâm tài liệu tiếng Việt](vi/README.md)

## Document status

| Label | Meaning |
| --- | --- |
| **CURRENT** | Describes code or an operational path that exists. Claims still require the linked evidence. |
| **TARGET** | Desired design or backlog. It must not be used as proof of implementation. |
| **HISTORICAL** | A completed phase, old report or superseded design retained for traceability. |

The rules for maintaining these labels are in
[Documentation Standard](DOCUMENTATION_STANDARD.md).

## Start here

| Reader | First document | Then read |
| --- | --- | --- |
| New developer | [Getting Started](getting-started.md) | [Architecture](ARCHITECTURE.md), [Development](DEVELOPMENT.md) |
| Strategy researcher | [Research-to-Production Guide](guides/RESEARCH_TO_PRODUCTION.md) | [Research Methodology](RESEARCH_METHODOLOGY.md), [Evidence Artifacts](reference/EVIDENCE_ARTIFACTS.md) |
| Runtime/execution engineer | [Strategy Lifecycle](architecture/STRATEGY_LIFECYCLE.md) | [Backtest Engine](BACKTEST_ENGINE.md), [Authority Chain](AUTHORITY_CHAIN_OPS.md) |
| Operator | [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md) | [Live Runbook](LIVE_TRADING_RUNBOOK.md), [Operational Drills](OPERATIONAL_DRILLS.md) |
| Auditor/reviewer | [Capability Matrix](CAPABILITY_MATRIX.md) | [Research Evidence](RESEARCH_EVIDENCE.md), [Promotion Binding](PROMOTION_BINDING.md) |
| Learner | [Course V2](tutorials/README.md) | Follow the modules in order |

## Canonical system flow

```text
Market data
   ↓ quality gate + point-in-time features
Canonical strategy registry
   ↓ deterministic evaluation cells
Tournament + realistic execution simulation
   ↓ statistical/OOS selection evidence
Selection policy + immutable promotion artifact
   ↓ fail-closed authority resolution
Runtime strategy/router
   ↓ portfolio + risk constraints
Order intent → lifecycle → broker → fills → reconciliation
   ↓
Attribution, monitoring, rollback and audit evidence
```

No stage may silently substitute missing evidence, missing data or an unknown
strategy. The safe fallback is abstention, rejection or reduced exposure.

## Source-of-truth map

### Current state and evidence

| Document | Status | Authority |
| --- | --- | --- |
| [Capability Matrix](CAPABILITY_MATRIX.md) | **CURRENT** | Maturity of each capability; distinguishes implemented from production-validated |
| [Live Readiness](LIVE_TRADING_TODO.md) | **CURRENT** | P0–P3 gates and current mainnet decision |
| [Research Evidence](RESEARCH_EVIDENCE.md) | **CURRENT** | Accepted research evidence and known limitations |
| [Research Holdout](RESEARCH_HOLDOUT.md) | **CURRENT** | Holdout isolation and locked evaluation rules |
| [Project Map](PROJECT_MAP.md) | **CURRENT / generated** | Physical repository layout; never edit manually |

### Architecture and contracts

| Document | Status | Scope |
| --- | --- | --- |
| [Architecture](ARCHITECTURE.md) | **CURRENT** | Five-plane system architecture |
| [Strategy Lifecycle](architecture/STRATEGY_LIFECYCLE.md) | **CURRENT + TARGET boundaries** | Research, selection, promotion and runtime authority |
| [Backtest Engine](BACKTEST_ENGINE.md) | **CURRENT** | Portfolio simulation, fill safety and parity boundary |
| [Promotion Binding](PROMOTION_BINDING.md) | **CURRENT** | Artifact integrity and research-to-runtime binding |
| [Runtime Resolver](RUNTIME_RESOLVER.md) | **CURRENT** | Fail-closed runtime strategy resolution |
| [Production Policy](PRODUCTION_POLICY.md) | **CURRENT** | Production eligibility rules |
| [Evidence Artifacts](reference/EVIDENCE_ARTIFACTS.md) | **CURRENT** | Artifact ownership, minimum fields and consumers |

### How-to guides and operations

| Document | Status | Scope |
| --- | --- | --- |
| [Research-to-Production](guides/RESEARCH_TO_PRODUCTION.md) | **CURRENT + TARGET boundaries** | End-to-end strategy lifecycle |
| [Main-flow Validation](operations/MAIN_FLOW_VALIDATION.md) | **CURRENT** | Reproducible checks from smoke to release evidence |
| [Live Trading Runbook](LIVE_TRADING_RUNBOOK.md) | **CURRENT** | Live-path operation and emergency response |
| [Runbook](RUNBOOK.md) | **CURRENT** | Service operations |
| [Local Runbook](RUNBOOK_LOCAL.md) | **CURRENT** | Local-only operation |
| [Deployment](DEPLOYMENT.md) | **CURRENT** | Deployment topology and rollback |
| [Security](SECURITY.md) | **CURRENT** | Credentials, release provenance and hardening |

### Learning material

| Document | Status | Scope |
| --- | --- | --- |
| [Course V2](tutorials/README.md) | **CURRENT syllabus** | Contract-first learning path tied to evidence |
| [`COURSE/`](../COURSE/) | **HISTORICAL** | Course V1; retained only for traceability |

## Target and historical material

[Adaptive Strategy Selection Roadmap](ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md)
contains both implemented phase notes and future design. Treat unchecked work as
**TARGET**. After all S0–S7 exit gates close, freeze it as a completion record;
do not use it as the runtime manual.

Phase reports, old architecture snapshots and superseded plans belong under
[`archive/`](archive/). A completed TODO is evidence of project history, not the
canonical description of current behavior.

## Truth rules

1. Code and tests establish behavior; documents explain it.
2. A passing historical run is not a standing production claim.
3. Backtest numbers are meaningful only with data, code, config and cost-model identity.
4. `Implemented`, `tested`, `paper-validated` and `production-validated` are different states.
5. Mainnet remains `NO-GO` unless the current release evidence explicitly closes every gate.
6. Never place real secrets, private account identifiers or unredacted broker payloads in docs.
