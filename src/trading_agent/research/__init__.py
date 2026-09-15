"""Strategy Research Framework for LearnTradeAgent.

Provides the infrastructure to evaluate a portfolio of strategy candidates
across multiple assets and timeframes using a unified walk-forward protocol
with statistical hardening (DSR/PBO/CSCV), regime breakdown, and
portfolio-level correlation analysis.

Modules:
- strategy_catalog: 8 candidate strategies with precise definitions
- protocol: Research protocol (extends WFOSpec)
- portfolio_analysis: PnL correlation, diversification contribution
- regime_breakdown: Returns × regime × strategy analysis
"""

from trading_agent.research.artifact import (
    ArtifactStore,
    PersistentArtifactStore,
    build_strategy_artifact,
)
from trading_agent.research.drift import (
    DriftLevel,
    DriftMonitor,
    StrategyHealthState,
    psi,
)
from trading_agent.research.lifecycle import PromotionError
from trading_agent.research.portfolio_analysis import (
    compute_marginal_sharpe_contribution,
    compute_diversification_ratio,
    compute_pnl_correlation,
)
from trading_agent.research.promotion import (
    EvidenceArtifact,
    ResearchLifecycle,
    ResearchStage,
)
from trading_agent.research.strategy_catalog import (
    STRATEGY_CATALOG,
    StrategySpec,
    get_available_strategies,
    get_strategy_spec,
)
from trading_agent.research.protocol import ResearchProtocol
from trading_agent.research.trials import (
    TrialsRegistry,
    param_hash,
    search_space_hash,
)
from trading_agent.research.uncertainty import (
    AbstentionReason,
    UncertaintySignal,
    UncertaintyState,
    should_abstain,
)

__all__ = [
    # strategy_catalog
    "StrategySpec",
    "STRATEGY_CATALOG",
    "get_strategy_spec",
    "get_available_strategies",
    # protocol
    "ResearchProtocol",
    # portfolio_analysis
    "compute_pnl_correlation",
    "compute_diversification_ratio",
    "compute_marginal_sharpe_contribution",
    # artifact
    "ArtifactStore",
    "PersistentArtifactStore",
    "build_strategy_artifact",
    # drift
    "DriftLevel",
    "DriftMonitor",
    "StrategyHealthState",
    "psi",
    # lifecycle
    "PromotionError",
    # promotion
    "EvidenceArtifact",
    "ResearchLifecycle",
    "ResearchStage",
    # trials
    "TrialsRegistry",
    "param_hash",
    "search_space_hash",
    # uncertainty
    "AbstentionReason",
    "UncertaintySignal",
    "UncertaintyState",
    "should_abstain",
]
