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

from trading_agent.research.strategy_catalog import (
    StrategySpec,
    STRATEGY_CATALOG,
    get_strategy_spec,
    get_available_strategies,
)
from trading_agent.research.protocol import ResearchProtocol
from trading_agent.research.portfolio_analysis import (
    compute_pnl_correlation,
    compute_diversification_ratio,
    compute_marginal_sharpe_contribution,
)

__all__ = [
    "StrategySpec",
    "STRATEGY_CATALOG",
    "get_strategy_spec",
    "get_available_strategies",
    "ResearchProtocol",
    "compute_pnl_correlation",
    "compute_diversification_ratio",
    "compute_marginal_sharpe_contribution",
]
