"""
Authority Chain — Fail-closed, auditable decision authority from Research → Execution.

Architecture:
┌─────────────────────────────────────────────────────────────────────────────┐
│                         AUTHORITY CHAIN (fail-closed)                       │
├──────────────────┬──────────────────┬──────────────────┬──────────────────┤
│  DecisionAuthority│ ExposureAuthority │ ExecutionAuthority │ PortfolioAllocator│
│  (1st)           │  (2nd)           │  (3rd)           │  (4th, multi-pair)│
│                  │                  │                  │                  │
│  Signal/Artifact │ TargetExposure   │ Intent           │ Allocation       │
│  → UnifiedRisk   │ → Validated      │ → Claimed →      │ → TargetExposure │
│    Decision +    │    Exposure      │   Submitted      │   per symbol     │
│    TargetExposure│                  │                  │                  │
└────────┬─────────┴────────┬─────────┴────────┬─────────┴────────┬─────────┘
         │                 │                 │                 │
         └─────────────────┴─────────────────┴─────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │  CausationChain   │
                    │  (immutable audit │
                    │   trail + replay) │
                    └───────────────────┘

No exposure-increasing order can bypass this chain.
Every decision is content-addressed and cryptographically chained.
"""

from trading_agent.authority.config import (
    AuthorityConfig,
    ExposureConfig,
    ExecutionConfig,
    ResearchConfig,
    SimulatorConfig,
    LiveConfig,
    LoggingConfig,
    Environment,
    RiskProfile,
    get_authority_config,
    set_authority_config,
    reset_authority_config,
)

from trading_agent.authority.causation import (
    CausationLink,
    CausationChain,
    CausationLinkModel,
    CausationChainModel,
    generate_causation_id,
    validate_causation_id,
    new_chain,
    authority_id,
    AuthorityName,
    CausationIDStr,
)

from trading_agent.authority.decision import (
    DecisionAuthority,
    DecisionInput,
    DecisionOutput,
)

from trading_agent.authority.exposure import (
    ExposureAuthority,
    ExposureValidationInput,
    ExposureValidationOutput,
)

from trading_agent.authority.execution import (
    ExecutionAuthority,
    ExecutionValidationInput,
    ExecutionValidationOutput,
)

from trading_agent.authority.portfolio import (
    PortfolioAllocator,
    PositionSizer,
    AllocationRequest,
    AllocationResult,
    StrategyBudget,
)

from trading_agent.authority.loader import (
    PromotedStrategy,
    PromotedStrategyManifest,
    RuntimeLoader,
    on_promotion_to_production,
)

from trading_agent.authority.resolver import (
    RuntimeStrategyResolver,
    StrategyType,
    StrategyRuntime,
    StrategyOutput,
)

from trading_agent.authority.decision import TargetExposure
from trading_agent.authority.audit import (
    CausationLogEntry,
    DecisionAuditRecord,
    CausationLogger,
    DecisionAuditCLI,
    get_causation_logger,
    log_authority_decision,
)

__all__ = [
    # Config
    "AuthorityConfig",
    "ExposureConfig",
    "ExecutionConfig",
    "ResearchConfig",
    "SimulatorConfig",
    "LiveConfig",
    "LoggingConfig",
    "Environment",
    "RiskProfile",
    "get_authority_config",
    "set_authority_config",
    "reset_authority_config",
    # Causation
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
    # Authorities
    "DecisionAuthority",
    "DecisionInput",
    "DecisionOutput",
    "ExposureAuthority",
    "ExposureValidationInput",
    "ExposureValidationOutput",
    "ExecutionAuthority",
    "ExecutionValidationInput",
    "ExecutionValidationOutput",
    "PortfolioAllocator",
    "PositionSizer",
    "AllocationRequest",
    "AllocationResult",
    "StrategyBudget",
    "TargetExposure",  # New
    # Loader
    "PromotedStrategy",
    "PromotedStrategyManifest",
    "RuntimeLoader",
    "on_promotion_to_production",
    # Resolver
    "RuntimeStrategyResolver",
    "StrategyType",
    "StrategyRuntime",
    "StrategyOutput",
    # Audit
    "CausationLogEntry",
    "DecisionAuditRecord",
    "CausationLogger",
    "DecisionAuditCLI",
    "get_causation_logger",
    "log_authority_decision",
]

# Version
__version__ = "1.0.0"
