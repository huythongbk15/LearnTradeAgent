# Approval Chain Schema — Multi-Party Release Attestation

> Snapshot: **2026-09-04** · Required cho S7 mainnet gating

## 1. Mục đích

Đảm bảo **không một cá nhân/agent đơn lẻ** nào có thể promote strategy lên production. Mỗi promotion cần ≥ 2 approvals từ các vai trò khác nhau, có cryptographic signature.

## 2. Vai trò (Roles)

| Role | Code | Permissions | Required for |
|---|---|---|---|
| **Researcher** | `research` | Tạo evidence, register candidate | `shadow` stage |
| **Risk Officer** | `risk` | Sign off risk metrics (Sharpe, DD, VaR) | `testnet` stage |
| **Compliance** | `compliance` | Sign off provenance + data quality | `testnet` stage |
| **Operator** | `operator` | Sign off operational readiness (calibration, monitoring) | `canary` stage |
| **Admin** | `admin` | Final release approval | `production` stage |

## 3. Approval Record Schema

```python
@dataclass(frozen=True)
class Approval:
    role: str  # One of: research, risk, compliance, operator, admin
    approver_id: str  # e.g. "user:huythong" or "agent:trading-bot"
    artifact_id: str  # sha256 of the promotion artifact
    stage: str  # shadow, testnet, canary, production
    decision: str  # "approved" or "rejected"
    reason: str  # Human-readable explanation
    signature: str  # ed25519 signature of (role|approver_id|artifact_id|stage|decision|timestamp)
    timestamp: str  # ISO 8601 UTC
    metadata: dict  # Optional context (e.g., evidence IDs, run IDs)
```

## 4. Promotion Artifact Approval Flow

```
1. Researcher creates promotion artifact
   ↓ signs with research role
   ↓ writes Approval(role="research", decision="approved", ...)
2. Artifact moves to testnet stage
   ↓ Risk Officer evaluates risk metrics
   ↓ signs with risk role (or rejects)
3. Compliance checks provenance + data quality
   ↓ signs with compliance role
4. Artifact moves to canary stage (5% capital)
   ↓ Operator verifies monitoring + calibration
   ↓ signs with operator role
5. Artifact moves to production (full capital)
   ↓ Admin gives final release approval
   ↓ signs with admin role
   ↓ Promotion is LIVE
```

## 5. Multi-Party Rules

### 5.1 Minimum approvals per stage

| Stage | Required roles | Min signatures |
|---|---|---|
| `shadow` | `research` | 1 |
| `testnet` | `research` + `risk` + `compliance` | 3 |
| `canary` | `research` + `risk` + `compliance` + `operator` | 4 |
| `production` | ALL 5 roles | 5 |

### 5.2 Conflict rules

- **Role separation**: One approver cannot hold multiple roles simultaneously
- **Rejection freezes**: Any `decision="rejected"` halts promotion until manually overridden
- **Time-bounded**: All approvals for a stage must occur within 24h window
- **Idempotency**: Same approver+role+artifact cannot approve twice (only most recent counts)

### 5.3 Override rules

- `admin` can override any rejection (with explicit `reason` containing "OVERRIDE:")
- Override creates new artifact version, requires new full approval chain
- All overrides logged to `audit_log.jsonl` with extra metadata

## 6. Cryptographic Requirements

### 6.1 Signature scheme

- **Algorithm**: ed25519 (fast, compact, no nonce needed)
- **Key storage**: 
  - Human approvers: hardware key (YubiKey) or password-encrypted keystore
  - Agent approvers: HSM-backed keys or env-injected (no plaintext in repo)
- **Message format**: `{role}|{approver_id}|{artifact_id}|{stage}|{decision}|{timestamp}`

### 6.2 Verification

```python
def verify_approval(approval: Approval, public_key: bytes) -> bool:
    msg = f"{approval.role}|{approval.approver_id}|{approval.artifact_id}|{approval.stage}|{approval.decision}|{approval.timestamp}"
    try:
        ed25519.verify(approval.signature, msg.encode(), public_key)
        return True
    except ed25519.BadSignatureError:
        return False
```

## 7. Storage

### 7.1 Where approvals live

- **Database**: `data/promotion/approvals.sqlite3` (append-only, immutable)
- **JSONL log**: `data/audit/promotion_log.jsonl` (human-readable mirror)
- **Per-artifact**: `data/promotion/{artifact_id}/approvals.json` (bundled for portability)

### 7.2 Schema migrations

- All schema changes require `admin` role approval
- Migrations written to `data/promotion/migrations/`
- Old approvals remain valid as long as they satisfy the schema at signing time

## 8. Failure modes

| Failure | Behavior |
|---|---|
| Missing approval | Promotion halts, requires sign-off |
| Expired approval (>24h) | Re-approval required |
| Invalid signature | Promotion rejected, security alert |
| Revoked key | Approval chain invalidated, must restart |
| Database corruption | Fallback to `approvals.json`, manual audit |

## 9. Implementation Status (TODO)

- [ ] `src/trading_agent/authority/approval.py` (Approval dataclass + signature)
- [ ] `src/trading_agent/authority/approval_chain.py` (multi-party logic)
- [ ] `data/promotion/approvals.sqlite3` schema + DAO
- [ ] `tests/authority/test_approval_chain.py` (≥20 tests)
- [ ] `tests/authority/test_approval_chain_security.py` (key revocation, replay attacks)
- [ ] CLI: `qwenpaw approvals sign/reject/list`
- [ ] Integration: hook into `PromotionBinding.promote()`

## 10. References

- `docs/PROMOTION_BINDING.md` — promotion artifact format
- `docs/AUTHORITY_CHAIN_OPS.md` — authority chain runtime
- `docs/RESEARCH_HOLDOUT.md` — frozen holdout evidence
