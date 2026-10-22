# Ledger correction — implementation only, verification pending

Source changed after rev14. Prior A/B/C and prior test results must not be
represented as verification of this change. Existing manifests and approvals
were not rewritten. No market run or approval was performed.

## Changes

- Evaluation snapshots derive counts and outcomes from unique attempt paths;
  repeated publication/cache hits do not append previous snapshots again.
- Snapshot publication uses a same-directory temporary file, file fsync,
  atomic replace and (POSIX) directory fsync. Immutable attempt receipts are
  unchanged.
- Registry accounting is `registry_counted: null` with explicit
  `UNKNOWN_NOT_RECONCILED`, not a fabricated false value.
- Added `tests/test_attempt_ledger_snapshot.py`: repeated snapshots, duplicate
  input paths, newly added attempts, failed replace preserving the old row,
  and file/directory fsync calls. Synthetic fixtures only.

## Verification limits and next steps

No tests or shell commands were executed: the required tool named
`execute_shell_command` is unavailable in this session. Source was read via
filesystem APIs and edited with apply_patch. A read-only /proc cmdline scan
found no run_wfo/full_system_backtest/run_calibration process before editing.

Run the new fast regression first through controlled_exec with a unique result
file, the workspace .venv/bin/python, and pytest `-o addopts=""`. Stop on the
first failure. Do not silently spend or extend the proposed test budget.

Then run the applicable binding/ledger tests, AC01–AC15 on the final source,
and the single launcher integration only within an explicitly approved test
envelope. No gate files were edited in this correction.

After successful verification, recompute source A, update payload B and evidence
C with preserved revisions, regenerate the working set and obtain review.
Do not change operator approval from PENDING. Old manifests remain historical;
their source identity does not describe this unverified working tree.

Market execution remains unauthorized. Qualification/live claims remain false.
