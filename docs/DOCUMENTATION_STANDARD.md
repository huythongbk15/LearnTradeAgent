# Documentation Standard

> Status: **CURRENT** · Owner: project maintainers · Review cadence: every release

This standard prevents documentation from becoming a second, contradictory
implementation of the system.

## Required front matter

Every new operational or architectural document starts with a short status block:

```text
Status: CURRENT | TARGET | HISTORICAL
Owner: team or role
Verified: YYYY-MM-DD or release/tag
Evidence: tests, artifact or runbook link
```

`Verified` means the examples and referenced paths were checked. It does not mean
that every capability is safe for production.

## Content types

| Type | Answers | Must contain |
| --- | --- | --- |
| Concept | Why does this exist? | Terms, invariants, boundaries, failure modes |
| Tutorial | How do I learn it? | Prerequisites, reproducible steps, expected artifact, cleanup |
| How-to | How do I complete one task? | Preconditions, command/action, verification, rollback |
| Reference | What is the exact contract? | Fields, types, ownership, versioning, consumers |
| Runbook | What do I do under operating pressure? | Trigger, diagnosis, safe action, escalation, evidence |
| Historical | What happened and why? | Date, scope, outcome and link to the current document |

A page should have one primary type. Do not combine a tutorial, roadmap and live
runbook into the same document.

## Stable-link rules

- Prefer module/class/function names over source line numbers.
- Use repository-relative links in Markdown.
- Refer to the installed package as `trading_agent`, whose source is under
  `src/trading_agent/`.
- Do not introduce the removed top-level `trading/` path.
- Link to `PROJECT_MAP.md` for generated directory listings rather than copying a
  large tree into several documents.

## Reproducible examples

All Python script examples use the project environment:

```bash
.venv/bin/python scripts/<script>.py --help
```

Important or potentially long commands must also show the controlled-execution
form required by this workspace:

```bash
python3 scripts/qwenpaw_control/controlled_exec.py \
  --timeout 3600 --heartbeat 30 --result-file <result.json> \
  -- .venv/bin/python scripts/<script>.py <args>
```

Use `trading-agent ...` only for registered CLI commands. Every example must state:

- whether it reads local data or contacts an external service;
- whether it writes state and where;
- whether it is smoke, research, paper, testnet or live;
- the expected exit condition or artifact;
- a safe cleanup/rollback action when applicable.

Never put an order-sending command in a beginner tutorial without an explicit
environment and permission gate.

## Evidence rules

Performance claims must identify, directly or through an artifact:

- dataset/manifest hash and evaluation window;
- code/commit identity;
- strategy and parameter identity;
- fee, spread, slippage and impact assumptions;
- fold/holdout policy;
- trade ledger and headline metrics;
- execution health and failure count.

Numbers without this identity are examples, not evidence. Avoid copying metric
tables from one run into evergreen documentation; link the immutable artifact.

## Status vocabulary

Use these exact maturity terms:

```text
DESIGNED → IMPLEMENTED → TESTED → RESEARCH_VALIDATED
         → PAPER_VALIDATED → TESTNET_VALIDATED → PRODUCTION_VALIDATED
```

Completion of a development phase does not automatically advance a capability to
production validation.

## Review checklist

- [ ] Status, owner and verification date are present.
- [ ] Paths and internal links resolve.
- [ ] Commands show help or pass their smoke check.
- [ ] Current and target behavior are visually distinct.
- [ ] No secret, private identifier or unsafe mainnet shortcut is present.
- [ ] Performance claims link to immutable evidence.
- [ ] The page does not duplicate another authoritative page.
- [ ] A superseded page links forward to its replacement.
