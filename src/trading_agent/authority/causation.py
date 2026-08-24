"""
CausationID & CausationChain — End-to-end traceability for every decision.

Design goals:
- Content-addressed: CausationID = hash of the decision's authoritative inputs
- Chainable: Each authority appends its ID, forming an immutable audit trail
- Compact: 24-char base64url IDs (sha256 truncated)
- Structured: JSON-serializable for logging and replay
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator


# ── Type aliases ────────────────────────────────────────────────────────

AuthorityName = Annotated[str, Field(min_length=1, max_length=64)]
CausationIDStr = Annotated[str, Field(pattern=r"^ca_[A-Za-z0-9_-]{22}$")]


# ── CausationID ─────────────────────────────────────────────────────────


def _sha256_base64url(payload: bytes) -> str:
    """sha256 → base64url (no padding), 43 chars. We use first 22 for ID."""
    digest = hashlib.sha256(payload).digest()
    import base64

    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def generate_causation_id(
    *,
    authority: str,
    inputs: dict[str, Any],
    prev_id: str | None = None,
) -> str:
    """
    Generate a content-addressed causation ID.

    The ID is derived from:
    - authority name (which authority produced this)
    - inputs (the authoritative inputs to this decision)
    - prev_id (previous causation ID in the chain, if any)

    This makes the ID tamper-evident: any change to inputs or chain breaks the ID.
    """
    payload = {
        "authority": authority,
        "inputs": _canonicalize_inputs(inputs),
        "prev": prev_id,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    hash_suffix = _sha256_base64url(encoded)[:22]
    return f"ca_{hash_suffix}"


def _canonicalize_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Convert inputs to canonical form for hashing (sort keys, stringify)."""

    def _convert(v: Any) -> Any:
        if isinstance(v, dict):
            return {
                str(k): _convert(v)
                for k, v in sorted(v.items(), key=lambda kv: str(kv[0]))
            }
        if isinstance(v, (list, tuple)):
            return [_convert(item) for item in v]
        if isinstance(v, (datetime, uuid.UUID)):
            return str(v)
        if hasattr(v, "model_dump"):  # Pydantic models
            return _convert(v.model_dump())
        if hasattr(v, "__dict__"):  # dataclasses
            return _convert(v.__dict__)
        return v

    return _convert(inputs)


def validate_causation_id(cid: str) -> bool:
    """Validate causation ID format."""
    return isinstance(cid, str) and cid.startswith("ca_") and len(cid) == 25


# ── CausationChain ──────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CausationLink:
    """Single link in the causation chain."""

    authority: str
    causation_id: str
    inputs_hash: str  # sha256 of the inputs this authority received
    outputs_hash: str  # sha256 of the outputs this authority produced
    timestamp: datetime
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not validate_causation_id(self.causation_id):
            raise ValueError(f"Invalid causation_id format: {self.causation_id}")
        if self.timestamp.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class CausationChain:
    """
    Immutable chain of causation links.

    Represents the complete authority chain for a decision:
    Research → Promotion → DecisionAuthority → ExposureAuthority → ExecutionAuthority

    Each link is content-addressed and references the previous link's output hash.
    """

    links: tuple[CausationLink, ...] = field(default_factory=tuple)
    root_inputs_hash: str = ""  # Hash of the original research artifact inputs

    def __post_init__(self) -> None:
        # Verify chain integrity
        for i, link in enumerate(self.links):
            if not validate_causation_id(link.causation_id):
                raise ValueError(f"Link {i}: invalid causation_id {link.causation_id}")
            if link.timestamp.tzinfo is None:
                raise ValueError(f"Link {i}: timestamp must be timezone-aware")

    @property
    def length(self) -> int:
        return len(self.links)

    @property
    def latest_id(self) -> str | None:
        return self.links[-1].causation_id if self.links else None

    @property
    def latest_authority(self) -> str | None:
        return self.links[-1].authority if self.links else None

    def append(
        self,
        *,
        authority: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> "CausationChain":
        """Append a new authority link, returning a new chain."""
        prev_outputs_hash = (
            self.links[-1].outputs_hash if self.links else self.root_inputs_hash
        )
        inputs_hash = _sha256_base64url(
            json.dumps(_canonicalize_inputs(inputs), sort_keys=True).encode()
        )
        outputs_hash = _sha256_base64url(
            json.dumps(_canonicalize_inputs(outputs), sort_keys=True).encode()
        )

        causation_id = generate_causation_id(
            authority=authority,
            inputs=inputs,
            prev_id=self.latest_id,
        )

        new_link = CausationLink(
            authority=authority,
            causation_id=causation_id,
            inputs_hash=inputs_hash,
            outputs_hash=outputs_hash,
            timestamp=datetime.now(UTC),
            metadata=metadata or {},
        )

        return CausationChain(
            links=self.links + (new_link,),
            root_inputs_hash=self.root_inputs_hash or inputs_hash,
        )

    def to_json(self) -> str:
        """Serialize to JSON for logging."""
        return json.dumps(
            {
                "root_inputs_hash": self.root_inputs_hash,
                "links": [
                    {
                        "authority": link.authority,
                        "causation_id": link.causation_id,
                        "inputs_hash": link.inputs_hash,
                        "outputs_hash": link.outputs_hash,
                        "timestamp": link.timestamp.isoformat(),
                        "metadata": link.metadata,
                    }
                    for link in self.links
                ],
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "CausationChain":
        """Deserialize from JSON."""
        data = json.loads(json_str)
        links = tuple(
            CausationLink(
                authority=link["authority"],
                causation_id=link["causation_id"],
                inputs_hash=link["inputs_hash"],
                outputs_hash=link["outputs_hash"],
                timestamp=datetime.fromisoformat(link["timestamp"]),
                metadata=link.get("metadata", {}),
            )
            for link in data["links"]
        )
        return cls(links=links, root_inputs_hash=data.get("root_inputs_hash", ""))

    def verify_chain(self) -> tuple[bool, str | None]:
        """
        Verify the entire chain integrity.

        Returns (True, None) if valid, (False, error_message) if broken.
        """
        if not self.links:
            return True, None

        prev_outputs = self.root_inputs_hash
        for i, link in enumerate(self.links):
            if link.inputs_hash != prev_outputs:
                return (
                    False,
                    f"Link {i} ({link.authority}): inputs_hash {link.inputs_hash} != prev_outputs {prev_outputs}",
                )
            # Note: We can't verify causation_id without re-hashing (would need original inputs/outputs)
            prev_outputs = link.outputs_hash

        return True, None

    def get_link(self, authority: str) -> CausationLink | None:
        """Get the link for a specific authority (last occurrence)."""
        for link in reversed(self.links):
            if link.authority == authority:
                return link
        return None

    def authorities(self) -> tuple[str, ...]:
        """Tuple of authority names in chain order."""
        return tuple(link.authority for link in self.links)


# ── Pydantic models for API/serialization ───────────────────────────────


class CausationLinkModel(BaseModel):
    authority: str
    causation_id: CausationIDStr
    inputs_hash: str
    outputs_hash: str
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _validate_tz(cls, v):
        if isinstance(v, str):
            v = datetime.fromisoformat(v)
        if v.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return v


class CausationChainModel(BaseModel):
    root_inputs_hash: str = ""
    links: list[CausationLinkModel] = Field(default_factory=list)

    def to_domain(self) -> CausationChain:
        links = tuple(
            CausationLink(
                authority=link.authority,
                causation_id=link.causation_id,
                inputs_hash=link.inputs_hash,
                outputs_hash=link.outputs_hash,
                timestamp=link.timestamp,
                metadata=link.metadata,
            )
            for link in self.links
        )
        return CausationChain(links=links, root_inputs_hash=self.root_inputs_hash)

    @classmethod
    def from_domain(cls, chain: CausationChain) -> "CausationChainModel":
        return cls(
            root_inputs_hash=chain.root_inputs_hash,
            links=[
                CausationLinkModel(
                    authority=link.authority,
                    causation_id=link.causation_id,
                    inputs_hash=link.inputs_hash,
                    outputs_hash=link.outputs_hash,
                    timestamp=link.timestamp,
                    metadata=link.metadata,
                )
                for link in chain.links
            ],
        )


# ── Convenience functions ───────────────────────────────────────────────


def new_chain(root_inputs: dict[str, Any]) -> CausationChain:
    """Create a new causation chain with root inputs hash."""
    root_hash = _sha256_base64url(
        json.dumps(_canonicalize_inputs(root_inputs), sort_keys=True).encode()
    )
    return CausationChain(links=(), root_inputs_hash=root_hash)


def authority_id(
    authority: str, inputs: dict[str, Any], prev_chain: CausationChain | None = None
) -> str:
    """Generate a causation ID for an authority decision."""
    prev = prev_chain.latest_id if prev_chain else None
    return generate_causation_id(authority=authority, inputs=inputs, prev_id=prev)


__all__ = [
    "CausationLink",
    "CausationChain",
    "CausationLinkModel",
    "CausationChainModel",
    "generate_causation_id",
    "validate_causation_id",
    "new_chain",
    "authority_id",
    "AuthorityName",
    "CausationIDStr",
]
