# Strategy Lifecycle and Authority Boundaries

> Status: **CURRENT with explicit TARGET stages** · Owner: research/runtime architecture
> · Verified: 2026-08-26

This document defines how a strategy moves from source code to an authorized
runtime decision. It is a contract map, not a claim that every target stage is
already production-validated.

## Lifecycle

```text
Strategy implementation
  → canonical descriptor + registry entry
  → point-in-time feature window
  → evaluation cell
  → execution-aware report
  → statistical selection evidence
  → selection policy artifact
  → promotion record
  → fail-closed runtime resolution
  → routing decision
  → portfolio/risk authorization
  → order lifecycle and fill attribution
```

## Stage contracts

| Stage | Producer | Output | Failure behavior | State |
| --- | --- | --- | --- | --- |
| Strategy declaration | `strategies/canonical/descriptor.py` | `StrategyDescriptor` | Reject invalid/duplicate identity | **CURRENT** |
| Registry lookup | `strategies/canonical/registry.py` | Registered adapter + descriptor | Unknown strategy error; no implicit fallback | **CURRENT** |
| Feature construction | `strategies/canonical/features.py` | Closed-bar OHLCV window | `FeatureUnavailableError` / abstain | **CURRENT** |
| Runtime compatibility | `strategies/canonical/bridge.py` | Canonical runtime bridge | Stay flat when history is insufficient | **CURRENT** |
| Evaluation matrix | `backtest/tournament.py` | `EvaluationArtifact` per cell | Emit `FAILED` evidence instead of hiding a cell | **CURRENT, S2 hardening ongoing** |
| Statistical selection | Nested WFO + multiple-testing policy | Candidate ranking/evidence | No eligible winner | **TARGET until its exit gate is evidenced** |
| Policy publication | Selection-policy builder | Immutable policy artifact | Refuse incomplete/unbound policy | **TARGET until its exit gate is evidenced** |
| Promotion | `authority/promotion_store.py` | `PromotionRecord` | No record means no runtime eligibility | **CURRENT foundation** |
| Runtime resolution | `authority/resolver.py` | `StrategyRuntime` | Fail closed on identity/integrity mismatch | **CURRENT foundation** |
| Regime routing | Runtime router | `RoutingDecision` | Hysteresis, abstain or incumbent fallback | **TARGET until its exit gate is evidenced** |
| Portfolio allocation | Shared-capital allocator | Constrained target exposures | Reduce/reject infeasible allocation | **TARGET until its exit gate is evidenced** |

## Identity chain

At minimum, a runtime decision must be attributable to:

```text
strategy_id
  + descriptor_id
  + parameter hash
  + data manifest hash
  + code/commit identity
  + cost scenario
  + evaluation artifact id
  + selection policy id
  + promotion record
  + routing decision id
```

If a downstream stage cannot reproduce this chain, it must not invent a default
identity such as `legacy_runner` or silently switch to a different strategy.

## Decision-time invariants

1. A decision at time `t` uses only observations closed before `t`.
2. Missing required history produces abstention, not a partially computed signal.
3. Parameters used to generate signals equal the parameters recorded in evidence.
4. Every tournament cell uses the same execution path and isolated state.
5. A failed cell remains visible in the index and cannot enter selection.
6. Runtime loads only an eligible, integrity-checked promotion.
7. Regime changes do not force immediate churn; switching is governed by
   confidence, hysteresis and minimum hold rules.
8. Portfolio constraints have authority over strategy preference.

## Research cadence versus runtime cadence

Research may evaluate many strategies, pairs, folds and cost scenarios. Runtime
must consume a small, versioned policy and make bounded decisions. Runtime must
not launch an optimizer, rewrite strategy parameters from recent P&L, or promote a
new winner inside the trading loop.

```text
Slow cadence: data lock → tournament → WFO → policy review → promotion
Fast cadence: observation → policy lookup → route/abstain → risk → execution
```

This separation limits overfitting and makes rollback deterministic.

## Safe fallback hierarchy

1. Keep the currently promoted eligible strategy when evidence remains valid.
2. Reduce exposure when uncertainty or execution health worsens.
3. Route to the explicit abstain strategy when no candidate is eligible.
4. Protective reduction may close risk under emergency policy.
5. Never open new exposure using an unverified default.

## Related evidence

- [Research Methodology](../RESEARCH_METHODOLOGY.md)
- [Research Holdout](../RESEARCH_HOLDOUT.md)
- [Promotion Binding](../PROMOTION_BINDING.md)
- [Runtime Resolver](../RUNTIME_RESOLVER.md)
- [Evidence Artifacts](../reference/EVIDENCE_ARTIFACTS.md)
- [Adaptive Strategy Selection Roadmap](../ADAPTIVE_STRATEGY_SELECTION_ROADMAP.md)

