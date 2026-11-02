"""A DRAFT policy of the paper book, in a registry of its own under the selection root. It never goes to data/promotion_store and it cannot leave DRAFT here: the artifact refuses VALIDATED without evidence, measured folds and a strategy on the canonical allowlist, and this run has none of them.

The dates are the Monday's stamp, not the wall clock, so equal inputs write an equal draft. The stage is exploratory: the artifact's default would claim research_validated, which no selector has earned. fallback is NO_TRADE and validity is 8 days, so a draft runs out if the next Monday has no valid run.
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from trading_agent.research.selection_policy import ParamArtifact, PolicyStatus, SelectionPolicyArtifact, SelectionPolicyRegistry

from tools.selection import config, decide

VARIANT = "4w_90d_top3"
SYMBOL = "PORTFOLIO"
TIMEFRAME = "4h"
REGIME = "ALL"
STAGE = "exploratory"


def draft(decision: dict, decision_sha256: str) -> SelectionPolicyArtifact:
    """The draft of a Monday, bound to its decision by hash."""
    stamp = decide.stamp_of(decision["as_of"])
    params = {
        "variant": VARIANT,
        "look_days": config.PRIMARY_LOOK,
        "top": config.PRIMARY_K,
        "cadence_weeks": config.PRIMARY_CADENCE_WEEKS,
        "as_of": decision["as_of"],
        "due_book": decision["due_book"],
        "selected": decision["selected"],
        "reasons": decision["reasons"],
        "books": decision["books"],
        "paper_only": True,
    }
    return SelectionPolicyArtifact(
        symbol=SYMBOL,
        timeframe=TIMEFRAME,
        regime=REGIME,
        incumbent=ParamArtifact(f"selector_{VARIANT}", params),
        validity_start=stamp,
        validity_end=stamp + timedelta(days=config.POLICY_VALIDITY_DAYS),
        status=PolicyStatus.DRAFT,
        created_at=stamp,
        fallback=decide.DECISION,
        promotion_stage=STAGE,
        policy_data_manifest_sha=decision_sha256,
    )


def publish(root: Path, artifact: SelectionPolicyArtifact) -> str:
    """Add a draft to the run's own registry. Adding the same draft again changes nothing."""
    return SelectionPolicyRegistry(config.registry_dir(root)).add(artifact)
