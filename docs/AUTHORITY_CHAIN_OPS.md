# Authority Chain Operations

## Overview

The authority chain is the core data backbone connecting every trading decision to its 
source. It enables full traceability from research predictions through to execution.

## Chain Structure

The authority chain is a sequence of `CausationLink` objects forming a `CausationChain`:

```python
class CausationLink:
    source: AuthorityName  # e.g., MODEL_BEST, DECISION_AUTHORITY
    input_id: str  # e.g., "DEFAULT_TARGET" or "2024-03-15T12:30:45"
    output_id: str  # e.g., "MARKET_ABLE" or "MAX_RISK_MODEL_PROD"
    timestamp: datetime
    metadata: dict
```

### Example Chain

```python
Chain: {
    DecisionAuthority.promote(
        input_id="RESEARCH_MODEL/best_model/v2",
        output_id="PROMOTED_RESEARCH/best_model/v2",
        timestamp=datetime.now(UTC)
    )
    ExposureAuthority.validate(
        input_id="PROMOTED_RESEARCH/best_model/v2",
        output_id="EXPOSURE_ALLOWED/SOL_USDT/v2",
        timestamp=datetime.now(UTC)
    )
    ExecutionAuthority.authorize(
        input_id="EXPOSURE_ALLOWED/SOL_USDT/v2",
        output_id="EXECUTABLE_SIGNAL/SOL_USDT/v2",
        timestamp=datetime.now(UTC)
    )
}
```

## Chain Usage

### In UnifiedRiskDecision
The `authority_chain` field stores the full causation chain as a tuple of `CausationLink` objects, preserved for audit trail and decision propagation.

```python
risk_decision = UnifiedRiskDecision(
    decision_id="DEX/ETH/USDT/v1",
    forecast_fingerprint="8a3b2c1d...",
    model_artifact_id="legacy_runner",
    requested_target_exposure=0.25,
    allowed_target_exposure=0.25,
    max_new_exposure=0.25,
    reduce_only=False,
    risk_level=RiskLevel.MEDIUM,
    reason_codes=(RiskReason.APPROVED,),
    calibration_state=EvidenceState.KNOWN,
    calibration_artifact_id="calib-legacy-202403",
    calibration_ece=0.012,
    ood_state=EvidenceState.MISSING,
    ood_score=0.0,
    regime_state=EvidenceState.UNKNOWN,
    regime_entropy=0.45,
    interval_width=0.1,
    created_at=datetime.now(UTC),
    authority_chain=chain_of_causation_links,
    metadata={"strategy": "momentum"},
    warnings=["moderate_risk"],
    authority_chain=chain_of_causation_links  # This field stores the full chain
)
```

## Chain Functions

### Adding Links
```python
def add_link(chain: CausationChain, link: CausationLink) -> CausationChain:
    """Append a link to the chain while preserving immutability."""
    return CausationChain(
        links=chain.links + (link,),
        created_at=chain.created_at,
        created_by=chain.created_by,
    )
```

### Finding Links
```python
def find_links_by_source(
    chain: CausationChain, source_name: str
) -> tuple[CausationLink, ...]:
    """Find all links from a specific authority name."""
    return tuple(link for link in chain.links if link.source.value == source_name)
```

### Complete Chain
```python
def get_complete_chain(pending_links: list[CausationLink]) -> CausationChain:
    """Convert a list of pending links to complete causality chain."""
    return CausationChain(
        links=tuple(pending_links),
        created_at=datetime.now(UTC),
        created_by="chain_completion",
    )
```

## Chain Lifecycle

1. **Creation**: When a decision is made by each authority
2. **Propagation**: Chain passes through each authority stage
3. **Validation**: Each link validates the previous link's output
4. **Storage**: Final chain stored in `UnifiedRiskDecision.authority_chain`
5. **Propagation**: Chain is carried forward to execution layer

## Chain Validation Rules

| Stage | Validation Check | Failure Action |
|-------|------------------|----------------|
| Decision→Exposure | Exposure output must be numeric, non-negative | Reject with EXPOSURE_BOUNDARY_VIOLATION |
| Exposure→Execution | Execution target must be within allowed exposure | Reject with EXECUTION_BOUNDARY_VIOLATION |
| All | Input ID must match expected format pattern | Reject with INVALID_INPUT_FORMAT |

## Chain Metadata Fields

| Field | Purpose | Example Values |
|-------|---------|----------------|
| `source` | Authority name | "DECISION_AUTHORITY", "EXPOSURE_AUTHORITY", "EXECUTION_AUTHORITY" |
| `input_id` | Identifier of input signal | "RESEARCH_MODEL/v2/best", "TARGET_RISK/MEDIUM" |
| `output_id` | Identifier of output decision | "DECISION_PROD/ETH/USDT/v1", "EXPOSURE_ALLOWED/SOL/USDT/v2" |
| `timestamp` | Decision timestamp | ISO 8601 format |
| `metadata` | Arbitrary key-value pairs | `{"model_version": "v2.1", "author": "researcher_7"}` |

## Chain Maintenance

The authority chain is immutable after creation. Any significant change requires:
1. Creating a new chain with updated links
2. Logging the change in audit trail
3. Notifying downstream systems via event bus

## Chain Queries

Common queries on the authority chain:
```python
# Get first DecisionAuthority link
first_decision_link = next(
    (link for link in chain.links if link.source == AuthorityName.DECISION_AUTHORITY),
    None,
)

# Check if chain contains EXECUTION_AUTHORITY
has_execution = any(
    link.source == AuthorityName.EXECUTION_AUTHORITY for link in chain.links
)

# Count links by authority
count_by_source = {
    link.source.value: getattr(chain.links, "__len__") for link in chain.links
}
```

The authority chain is the primary artifact for audit trails and decision traceability across the entire trading pipeline.