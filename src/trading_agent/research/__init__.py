"""Research governance package (Wave B).

Immutable strategy artifacts, promotion state machine, uncertainty gate,
abstention codes, drift/health detection and multiple-testing governance.
"""

from trading_agent.research.artifact import (
    ArtifactStore,
    StrategyArtifact,
    build_strategy_artifact,
    canonical_params,
    data_manifest_hash,
    hash_file,
    sha256_hex,
)
from trading_agent.research.lifecycle import (
    ArtifactLifecycle,
    PromotionError,
    PromotionEvent,
    PromotionState,
)
from trading_agent.research.uncertainty import (
    ABSTENTION_CODES,
    Abstention,
    AbstentionReason,
    UncertaintySignal,
    UncertaintyState,
    should_abstain,
    uncertainty_gate,
)
from trading_agent.research.drift import (
    DriftLevel,
    DriftMonitor,
    DriftResult,
    StrategyHealthState,
    psi,
)
from trading_agent.research.trials import (
    TrialRecord,
    TrialsRegistry,
    param_hash,
    search_space_hash,
)

__all__ = [
    "ABSTENTION_CODES",
    "Abstention",
    "AbstentionReason",
    "ArtifactLifecycle",
    "ArtifactStore",
    "DriftLevel",
    "DriftMonitor",
    "DriftResult",
    "PromotionError",
    "PromotionEvent",
    "PromotionState",
    "StrategyArtifact",
    "StrategyHealthState",
    "TrialRecord",
    "TrialsRegistry",
    "UncertaintySignal",
    "UncertaintyState",
    "build_strategy_artifact",
    "canonical_params",
    "data_manifest_hash",
    "hash_file",
    "param_hash",
    "psi",
    "search_space_hash",
    "sha256_hex",
    "should_abstain",
    "uncertainty_gate",
]