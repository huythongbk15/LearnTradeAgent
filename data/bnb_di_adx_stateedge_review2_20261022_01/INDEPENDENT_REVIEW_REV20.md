# Independent review — rev20 — CHANGES_REQUIRED

Reviewer: review_rev20, fresh context, no inherited conversation or prior
implementation work. Read-only source/assertion/receipt review. No tests,
source changes, process execution or approval by the reviewer.

## Revision independently checked

A: c4b7479bd88dee4352dec7d9d2c03ab2ea2be18f65a0630bb6e543f9980b4b74

B: 1338572d57628e9ba1c29b655856547d1c945f4695ee7cc9aef0545c8ce88145

Source A recomputed from 480 Python files. Three manifests, 104 listed entries,
and verification-index receipt hashes match. B was read from production receipt
and manifests, not independently recomputed by executing Python. Git commit
and dirty-diff SHA were not independently recomputed.

## R20-1 — HIGH: runtime risk/stop/sizing not bound to approved experiment

preregistration_binding.py:185–209 does not compare runtime_config or native
execution contracts to preregistered/control values. full_system_backtest.py:
92–98 reads risk, sizing and stop settings from environment. run_wfo_parallel.py:
811–815 freezes whatever the current environment says; :1240–1241 only compares
the worker to that newly created protocol. Thus unchanged A/B can execute
different stop/exposure/risk than the registered inherited control policy.

Minimal remediation: freeze the concrete approved runtime/native contract
semantics in payload B, compare them in launcher and worker before execution.
Regressions change individual risk/stop/sizing environment settings and must
observe named binding rejection before any cell.

## R20-2 — HIGH: noncanonical root can substitute REAL_MARKET approval

run_wfo_parallel.py:50 accepts TRADING_PREREGISTRATION_ROOT. :863–864 and
:1430–1445 record/verify consistency of the redirection, not that the canonical
approval was used. preregistration_binding.py:100–110 requires only that the
canonical directory exists, then reads payload/manifest under the other root.
An approved copied packet with rehashed B can therefore substitute for canonical
PENDING approval for a REAL_MARKET run.

Minimal remediation: reject noncanonical preregistration roots for REAL_MARKET
at launcher and worker. Permit override only under explicitly frozen and
verified SYNTHETIC_TEST_ONLY classification if needed for fixtures. The CLI
currently hardcodes REAL_MARKET (run_wfo_parallel.py:1733), while launcher test
fixtures create approved copies (test_di_adx_state_edge_launcher_integration.py:
129–152). They must be explicitly synthetic, not a production approval bypass.
Regressions: REAL_MARKET redirect rejects; synthetic override works and remains
labelled synthetic through terminal evidence.

## Non-blocking observations and limits

Ledger is a derived unique snapshot with atomic replacement/fsync and UNKNOWN
registry accounting, and is not used to certify completion. Existing regression
receipts were read, not rerun. F9 has separate reproduction receipts. Historical
overwritten revision limitations remain disclosed.

Verdict CHANGES_REQUIRED; not ACCEPTED_FOR_BOUNDED_VALIDATION. No market, OOS,
sensitivity, holdout, paper, broker or live authorization is granted.
