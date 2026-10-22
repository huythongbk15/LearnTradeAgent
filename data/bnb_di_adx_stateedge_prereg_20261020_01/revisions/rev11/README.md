# Revision 11 — superseded, kept

rev11 froze source identity A `8d27b8f1…` and preregistration identity B
`6f500d70…`.

**What changed in rev12** — the operator's independent review returned
`CHANGES_REQUIRED` with four findings, all now addressed:

1. the registered command ran the **wrong geometry** (the CLI default is
   `--val-months 3`); the command now pins the full geometry, and a new
   launcher-path integration test asserts the CLI reproduces the five frozen
   windows and 15 cells, and that the default does not;
2. the **ledger was not proven** — the rule paired a runner-layer file with a
   worker-layer file; the two layers are now separated and linked by the cache
   key, and a new test exercises the real runner and the real worker;
3. the payload still claimed a wrong token is blocked **before the data read** at
   plan time — the claim is now split: plan time blocks after the loader read,
   execution time blocks before the data read and before the engine;
4. the integration evidence did not cover the **complete launcher** — a new
   synthetic integration drives the validation launcher end to end and asserts the
   terminal coverage.

That integration also found and fixed **seven** broken hops that the earlier
unit-level integration could not see: the CLI had no param grid for the scoped
candidate; the protocol producer and the protocol verifier still used the old
inline budget literals and rejected the registered budget; `nested_wfo` built
the registry without the token; `_compute_strategy_code_sha` degraded to
"unknown" (losing source attribution in the trial identity); the campaign
artifact rebuild and `selection_eligibility.verify_validation_reports` built
the registry without the token.

The fixes move the source identity, so the independent review must be
re-issued on the new A/B/C.
