"""LLM client module.

Provides both the async pool-based client (``LLMClient``/``LLMPool``)
and the LLM Context Enrichment Layer (``ContextEnricher``, ``MarketContext``,
``ResearchMemory``, ``DecisionTraceBuffer``).

The enrichment layer is enrichment-only: LLM produces MarketContext
(regime classification, anomaly flags) to adjust confidence weighting
of deterministic signals — never to generate signals directly.
"""

from trading_agent.llm.client import LLMClient, LLMConfig, create_llm_client
from trading_agent.llm.pool import (
    LLMPool,
    PoolError,
    PoolProvider,
    PoolRateLimitError,
    QuotaTracker,
    create_llm_pool,
)
from trading_agent.llm.context_enrichment import (
    ContextEnricher,
    MarketContext,
)
from trading_agent.llm.decision_trace import (
    DecisionTraceBuffer,
    LLMDecisionTrace,
)
from trading_agent.llm.research_memory import ResearchMemory

__all__ = [
    # Async client
    "LLMClient",
    "LLMConfig",
    "create_llm_client",
    "LLMPool",
    "PoolError",
    "PoolProvider",
    "PoolRateLimitError",
    "QuotaTracker",
    "create_llm_pool",
    # Context enrichment layer
    "ContextEnricher",
    "MarketContext",
    "DecisionTraceBuffer",
    "LLMDecisionTrace",
    "ResearchMemory",
]
