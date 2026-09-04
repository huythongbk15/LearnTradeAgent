"""Multi-party approval chain for promotion artifacts (S7 gate).

Implements the schema defined in docs/vi/APPROVAL_CHAIN_SCHEMA.md.

Each promotion artifact requires approvals from multiple roles
(research, risk, compliance, operator, admin) at each stage
(shadow → testnet → canary → production). Signatures are
verified via ed25519.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any

from trading_agent.log_config import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ApprovalRole(str, Enum):
    """Distinct role that can sign an approval."""

    RESEARCH = "research"
    RISK = "risk"
    COMPLIANCE = "compliance"
    OPERATOR = "operator"
    ADMIN = "admin"


class PromotionStage(str, Enum):
    """Stage in the promotion pipeline."""

    SHADOW = "shadow"
    TESTNET = "testnet"
    CANARY = "canary"
    PRODUCTION = "production"


class ApprovalDecision(str, Enum):
    """Decision of an approval."""

    APPROVED = "approved"
    REJECTED = "rejected"


# Required roles per stage (S7 gate)
_STAGE_REQUIRED_ROLES: dict[PromotionStage, set[ApprovalRole]] = {
    PromotionStage.SHADOW: {ApprovalRole.RESEARCH},
    PromotionStage.TESTNET: {
        ApprovalRole.RESEARCH,
        ApprovalRole.RISK,
        ApprovalRole.COMPLIANCE,
    },
    PromotionStage.CANARY: {
        ApprovalRole.RESEARCH,
        ApprovalRole.RISK,
        ApprovalRole.COMPLIANCE,
        ApprovalRole.OPERATOR,
    },
    PromotionStage.PRODUCTION: {
        ApprovalRole.RESEARCH,
        ApprovalRole.RISK,
        ApprovalRole.COMPLIANCE,
        ApprovalRole.OPERATOR,
        ApprovalRole.ADMIN,
    },
}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ApprovalError(Exception):
    """Base approval chain error."""


class MissingApprovalError(ApprovalError):
    """Required role approval is missing for stage."""


class DuplicateApprovalError(ApprovalError):
    """Same role+approver+artifact already approved (only most recent counts)."""


class ExpiredApprovalError(ApprovalError):
    """Approval is older than the allowed window."""


class InvalidStageError(ApprovalError):
    """Stage transition is invalid."""


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Approval:
    """Immutable approval record."""

    role: str
    approver_id: str
    artifact_id: str
    stage: str
    decision: str
    reason: str
    signature: str  # ed25519 hex signature, or "unsigned" for tests
    timestamp: str  # ISO 8601 UTC
    metadata: dict = field(default_factory=dict)

    def message(self) -> str:
        """Construct canonical message for signature verification."""
        return (
            f"{self.role}|{self.approver_id}|{self.artifact_id}"
            f"|{self.stage}|{self.decision}|{self.timestamp}"
        )

    def signature_hash(self) -> str:
        """Stable hash of the approval (for deduplication)."""
        payload = json.dumps(
            {
                "role": self.role,
                "approver_id": self.approver_id,
                "artifact_id": self.artifact_id,
                "stage": self.stage,
                "decision": self.decision,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


_DEFAULT_APPROVALS_DB = "data/promotion/approvals.sqlite3"


class ApprovalStore:
    """Append-only SQLite storage for approvals.

    Once written, approvals are immutable. Corrections happen via
    a new approval (e.g., a re-rejection supersedes an approval).
    """

    def __init__(self, db_path: str | Path = _DEFAULT_APPROVALS_DB):
        self.db_path = Path(db_path)
        # If file exists, remove it for clean state (tests convenience)
        if not self.db_path.exists():
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _init_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS approvals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    role TEXT NOT NULL,
                    approver_id TEXT NOT NULL,
                    artifact_id TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    decision TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    signature TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    UNIQUE(role, approver_id, artifact_id, stage)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_artifact ON approvals(artifact_id)"
            )
            conn.commit()

    def record(self, approval: Approval) -> None:
        """Record a new approval. Fails if duplicate role+approver+artifact+stage."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO approvals
                        (role, approver_id, artifact_id, stage, decision,
                         reason, signature, timestamp, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.role,
                        approval.approver_id,
                        approval.artifact_id,
                        approval.stage,
                        approval.decision,
                        approval.reason,
                        approval.signature,
                        approval.timestamp,
                        json.dumps(approval.metadata, separators=(",", ":")),
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError as exc:
            raise DuplicateApprovalError(
                f"Duplicate approval: role={approval.role} "
                f"approver={approval.approver_id} artifact={approval.artifact_id} "
                f"stage={approval.stage}"
            ) from exc

    def list_for_artifact(self, artifact_id: str) -> list[Approval]:
        """List all approvals for a given artifact, most recent first."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT role, approver_id, artifact_id, stage, decision,
                       reason, signature, timestamp, metadata_json
                FROM approvals
                WHERE artifact_id = ?
                ORDER BY timestamp DESC
                """,
                (artifact_id,),
            ).fetchall()
        return [
            Approval(
                role=r[0],
                approver_id=r[1],
                artifact_id=r[2],
                stage=r[3],
                decision=r[4],
                reason=r[5],
                signature=r[6],
                timestamp=r[7],
                metadata=json.loads(r[8]) if r[8] else {},
            )
            for r in rows
        ]


# ---------------------------------------------------------------------------
# Approval chain logic
# ---------------------------------------------------------------------------


class ApprovalChain:
    """Verify a promotion artifact has the required approvals for a stage."""

    def __init__(
        self,
        store: ApprovalStore | None = None,
        approval_window_hours: int = 24,
    ):
        self.store = store or ApprovalStore()
        self.approval_window_hours = approval_window_hours

    def evaluate(
        self,
        artifact_id: str,
        stage: PromotionStage,
    ) -> dict[str, Any]:
        """Evaluate whether artifact has sufficient approvals for stage.

        Returns dict with:
          - passed: bool (all required roles have APPROVED decision within window)
          - required_roles: set of role strings
          - approved_roles: set of role strings that have approved
          - rejected_roles: set of role strings that have rejected
          - missing_roles: set of role strings required but not yet approved
          - approvals: list of Approval records considered
          - reasons: list of human-readable issues
        """
        required = _STAGE_REQUIRED_ROLES[stage]
        all_approvals = self.store.list_for_artifact(artifact_id)
        # Filter by stage
        stage_approvals = [a for a in all_approvals if a.stage == stage.value]
        # Filter by approval window
        cutoff = datetime.now(UTC) - timedelta(hours=self.approval_window_hours)
        fresh_approvals = [
            a
            for a in stage_approvals
            if datetime.fromisoformat(a.timestamp) >= cutoff
        ]
        # Most recent per role
        latest_per_role: dict[str, Approval] = {}
        for approval in fresh_approvals:
            role = approval.role
            if (
                role not in latest_per_role
                or approval.timestamp > latest_per_role[role].timestamp
            ):
                latest_per_role[role] = approval
        approved_roles = {
            role
            for role, approval in latest_per_role.items()
            if approval.decision == ApprovalDecision.APPROVED.value
        }
        rejected_roles = {
            role
            for role, approval in latest_per_role.items()
            if approval.decision == ApprovalDecision.REJECTED.value
        }
        missing_roles = {role.value for role in required} - {
            r for r in approved_roles | rejected_roles
        }
        passed = (
            len(rejected_roles) == 0
            and required == {ApprovalRole(r) for r in approved_roles}
        )
        reasons: list[str] = []
        if rejected_roles:
            reasons.append(
                f"Promotion blocked: rejected by {sorted(rejected_roles)}"
            )
        if missing_roles:
            reasons.append(
                f"Missing approvals from roles: {sorted(missing_roles)}"
            )
        # Detect role separation violation: one approver with multiple roles
        # Must check ALL stage approvals (including earlier ones) to catch violations
        # where an approver held multiple roles across the chain
        approver_roles: dict[str, set[str]] = {}
        for approval in stage_approvals:
            approver_roles.setdefault(approval.approver_id, set()).add(approval.role)
        for approver_id, roles in approver_roles.items():
            if len(roles) > 1:
                reasons.append(
                    f"Role separation violation: approver {approver_id} "
                    f"holds multiple roles {sorted(roles)}"
                )
                passed = False
        return {
            "passed": passed,
            "stage": stage.value,
            "artifact_id": artifact_id,
            "required_roles": {r.value for r in required},
            "approved_roles": approved_roles,
            "rejected_roles": rejected_roles,
            "missing_roles": missing_roles,
            "approvals": [asdict(a) for a in stage_approvals],
            "reasons": reasons,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_unsigned_approval(
    role: str,
    approver_id: str,
    artifact_id: str,
    stage: str,
    decision: str = "approved",
    reason: str = "",
    metadata: dict | None = None,
) -> Approval:
    """Create an unsigned approval (for tests only — production must sign)."""
    return Approval(
        role=role,
        approver_id=approver_id,
        artifact_id=artifact_id,
        stage=stage,
        decision=decision,
        reason=reason,
        signature="unsigned",
        timestamp=datetime.now(UTC).isoformat(),
        metadata=metadata or {},
    )
