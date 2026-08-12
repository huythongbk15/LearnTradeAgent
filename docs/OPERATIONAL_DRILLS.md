# Operational Drills (P1.4 / P3 release gates)

Each drill verifies one failure mode of the live-trading system and is safe
by default: **local checks run always**, anything that touches the network or
exchange only runs with `--execute` (and requires the testnet acceptance env).

Run all drills:

```bash
for drill in scripts/drills/*_drill.sh; do
  echo ">>> $drill"
  bash "$drill" || echo "!!! drill FAILED"
done
```

| Drill | Script | Verifies |
|---|---|---|
| Restart / idempotency | `scripts/drills/restart_drill.sh` | no duplicate protective client order IDs; risk state JSON valid; testnet runner adopts existing stops |
| Network / API / stale data | `scripts/drills/network_drill.sh` | fail-closed on stale/absent data; clock-skew gate rejects drifted server time |
| Backup / restore | `scripts/drills/backup_restore_drill.sh` | backup → destroy → restore → checksum + content match (local fixture) |
| Credential revocation | `scripts/drills/credential_revocation_drill.sh` | missing HMAC key rejected; audit records failed runs; manual Binance revoke steps |

## Pass criteria (P3 gate)

- `restart_drill.sh`: audit log valid JSONL, **zero** duplicate protective
  `client_order_id`s, risk state valid JSON; with `--execute` on testnet the
  restarted run must not place a new protective stop for an already-protected
  position.
- `network_drill.sh`: module imports, clock-skew gate rejects a drifted server
  clock, runner aborts before submission when data is stale (unit-tested);
  with `--execute` a blocked exchange API must yield non-zero exit and no new
  orders.
- `backup_restore_drill.sh`: restored files match the backup SHA-256 manifest
  and content is byte-identical. Runs fully locally.
- `credential_revocation_drill.sh`: missing HMAC key is rejected (fail-closed
  config), `run_failed` events are recorded; manual revocation steps are
  printed for the operator.

## Scheduling

Recommended: restart drill weekly, backup/restore drill weekly, network drill
after any data-feed change, credential-revocation drill quarterly or after any
key rotation. All drills are non-destructive in dry-run mode and may be run in
CI as a smoke step (`bash scripts/drills/backup_restore_drill.sh`).
