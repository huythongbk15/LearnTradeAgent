# Workspace page (M2)

One local page that answers, for the whole system: what is being done now,
what has been done, and what exists. It serves gate M2 of
[OPERABLE_SYSTEM_PLAN.md](OPERABLE_SYSTEM_PLAN.md): within 10 s, what runs,
which stage, why it is blocked, where the evidence is.

```bash
.venv/bin/python scripts/ops/workspace_server.py          # http://127.0.0.1:8765
.venv/bin/python scripts/ops/workspace_status.py doing    # the same data as JSON
```

## Decisions (2026-10-30)

| Question | Decision |
| --- | --- |
| Rights | Observe only. No control of any kind; a waiting item shows the command, the operator runs it. |
| Scope v1 | Testnet operations and research campaigns. Test runs and Alpaca paper later. |
| Refresh | "Doing" about every 10 s; "Done" when the page opens; "Have" daily. |
| Where | A browser on this machine. The server binds 127.0.0.1 and nothing leaves the machine. |

## Sections

**Doing.** Three groups, in this order:

1. *Waiting for you*: what the system cannot continue without the operator. Each
   item says why, since when, and the condition or earliest time when it is not
   actionable yet. Derived only, never a hand-kept list: research packets whose
   `approval.approval_state` is `PENDING_OPERATOR_APPROVAL` (one per packet
   family, newest first), commits not on `origin/master`, recent local branches
   not merged, and the M3 authorization once M1 allows it.
2. *Automatic*: processes alive in `/proc` with their current step; the
   scheduled testnet cycle with the timeline of its last run from the audit log;
   the Windows health check from its own log; M1 and M3 progress from
   `scripts/ops/daily_check.py`; ledger and lifecycle consistency.
3. *In progress without you*: research processes that are alive.

**Done.** One line per outcome, not per commit: pull requests merged into
`origin/master`, the incident log of the operable-system plan, the dated entries
of [CAMPAIGN_STATUS.md](../CAMPAIGN_STATUS.md), and the evidence cards. Each line
carries its outcome label (FIXED, PASSED, NOT_QUALIFIED, ABSTAIN, ...) and its
source. Failed and negative results stay.

**Have.** Code packages by role (operational core, research, strategies, outside
the core, unused) with file counts; the canonical strategy ids and whether any is
promoted; research packets (how many, how many have results, the newest);
environments (scheduled, code only, locked).

## Module map

The page's first section is a map of the process modules: the testnet cycle, the
health and daily checks, data, the research flows and the qualification path,
with the human gates between them. It shows the milestones, what is blocked and
who can clear it, then one card per module.

The map's shape is declared in `workspace_page.html`. Every state on it comes from
`/api/doing` and `/api/have`, so it needs no new process, port or event. The lines
are declarations and start nothing: only the clock starts a module, and only a
person opens a gate. The map shows gates and their state, never a command or a
button.

## Rules

- A percentage is shown only against a denominator fixed in advance (24 M1
  cycles, 10 M3 lifecycles, a packet's planned cells). A cycle shows "step k of
  N", never a percentage. Without a denominator the page shows "—" and why.
- "Running" means alive in `/proc` now. The process registry is not used: it
  holds rows marked running whose heartbeat stopped on 2026-10-20.
- UNKNOWN is never shown as success. Every value carries the time it was read
  and its source path; a stale source is shown as stale. When the server stops
  answering the chips collapse to one unknown chip, BUY reads allowed only after
  a run has said so, and the books read unknown without the HMAC key.
- Times computed from the schedule (next cycle, earliest M1 pass) are labelled
  as projections.

## Safety

- The server answers GET only, on a fixed set of routes, bound to 127.0.0.1,
  and checks the Host header (no DNS rebinding). It serves no file other than
  its own page, and takes no path from a request.
- The page loads nothing from outside (`Content-Security-Policy: default-src 'self'`).
- Nothing it returns contains an API key, a secret, an account ID or `.env`
  content. The HMAC key is read only to verify the ledger, as `daily_check`
  does. The account shows as testnet, never by its ID or fingerprint.
- It writes nothing: SQLite opens read-only, files are only read, and git runs
  read-only commands (no fetch).
- It is not a scheduler: the operator starts and stops it.
