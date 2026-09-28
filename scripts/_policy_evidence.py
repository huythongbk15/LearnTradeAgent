"""Helpers for writing policy artifacts that are honest about their evidence.

Several generator scripts predate the measured-evidence gate and wrote
literal constants into ``SelectionPolicyArtifact.scores``:

    "median_oos_return_pct": 0.05,  "median_oos_trades": 40, ...

Those values are not measurements. Because promotion only checked the
stage, the artifacts reached ``status=active`` and were treated as
evidence-backed by every downstream consumer — see POLICY_RETURN_AUDIT.md,
which found 2690 policies built from three distinct return constants.

``unmeasured_policy_kwargs`` is the replacement for those blocks: it marks
the artifact DRAFT with empty scores, which the gate refuses to validate.
Infrastructure scripts that need a placeholder now get an honest one, and
a real evaluation has to write the metrics before promotion can proceed.
"""

from __future__ import annotations

from typing import Any

from trading_agent.research.selection_policy import PolicyStatus


def unmeasured_policy_kwargs(
    *, now: Any, validity_end: Any, risk_cap: float = 0.25
) -> dict[str, Any]:
    """Constructor kwargs for a policy that has not been evaluated.

    Returns an empty ``scores`` mapping and DRAFT status. Callers must pass
    these to ``SelectionPolicyArtifact`` instead of a fabricated score block.

    The artifact is still usable for registry wiring, signature tests and
    shadow plumbing; it just cannot be activated, which is the point.
    """
    return {
        "scores": {},
        "status": PolicyStatus.DRAFT,
        "promotion_stage": "exploratory",
        "created_at": now,
        "validity_end": validity_end,
        "risk_cap": risk_cap,
    }
