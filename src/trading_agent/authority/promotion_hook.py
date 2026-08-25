"""Research → Runtime bridge (Milestone D).

Wires research promotion events into the runtime world:

    ResearchLifecycle.promote()
        └─ on_event callback (atomic — failure blocks the stage change)
            └─ PromotionHook.handle(event)
                ├─ PromotionStateStore.upsert_from_event()   ← authoritative record
                ├─ verify store stage == event.to_stage      ← fail-closed check
                └─ RuntimeLoader.load_by_artifact_id()       ← optional manifest + hot-reload

Design invariants
-----------------
- **Fail-closed**: any inconsistency (missing artifact, stage mismatch) raises
  ``BridgeError``; callers must treat a failed bridge as "promotion did not
  take effect".
- **Idempotent**: handling the same event twice converges to the same state.
- **Stage-aware**: works for every stage ≥ PAPER_ELIGIBLE (paper / testnet /
  shadow / canary). PRODUCTION is bridged too, but production runtime access
  remains gated elsewhere (mainnet NO-GO policy is NOT lifted here).
- **Single truth**: eligibility at runtime is ALWAYS read from the
  authoritative ``PromotionStateStore``, never from artifact metadata.

Example
-------
    hook = PromotionHook(artifact_store=store, promotion_store=promo_db)
    lifecycle.promote(
        ResearchStage.PAPER_ELIGIBLE,
        evidence=evidence,
        actor="research-bot",
        on_event=hook.as_callback(),
    )
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from trading_agent.authority.loader import RuntimeLoader
from trading_agent.authority.promotion_store import (
    PromotionStateStore,
    is_stage_compatible,
)
from trading_agent.research.artifact import PersistentArtifactStore
from trading_agent.research.promotion import ResearchPromotionEvent

logger = logging.getLogger(__name__)


class BridgeError(RuntimeError):
    """Raised when a promotion event cannot be bridged into the runtime.

    Fail-closed: callers must treat this as "the promotion never reached
    the runtime" and abort the surrounding promotion flow.
    """


@dataclass(frozen=True)
class BridgeOutcome:
    """Result of one :meth:`PromotionHook.handle` call."""

    artifact_id: str
    from_stage: str
    to_stage: str
    persisted: bool
    loaded_into_runtime: bool
    idempotent_replay: bool = False
    detail: str = ""


class PromotionHook:
    """Bridge promotion events from the research ladder into the runtime.

    Parameters
    ----------
    artifact_store:
        Authoritative immutable artifact store. The event's subject artifact
        MUST exist here or the bridge fails closed.
    promotion_store:
        Authoritative promotion registry. The event is upserted here BEFORE
        anything else; runtime eligibility is later read from this store only.
    runtime_loader:
        Optional :class:`RuntimeLoader`. When provided AND the promoted stage
        is compatible with the loader's environment, the strategy is loaded
        (manifest written, callbacks fired) immediately — no restart needed.
        When omitted, the resolver still discovers the artifact on its next
        store lookup (resolver-level hot reload).
    """

    def __init__(
        self,
        artifact_store: PersistentArtifactStore,
        promotion_store: PromotionStateStore,
        runtime_loader: RuntimeLoader | None = None,
    ) -> None:
        self.artifact_store = artifact_store
        self.promotion_store = promotion_store
        self.runtime_loader = runtime_loader

    # ── Callback adapter for ResearchLifecycle.promote(on_event=...) ────

    def as_callback(self) -> Callable[[ResearchPromotionEvent], Any]:
        """Return a plain callable suitable for ``promote(on_event=...)``."""
        return self.handle

    # ── Core bridge ──────────────────────────────────────────────────────

    def handle(self, event: ResearchPromotionEvent) -> BridgeOutcome:
        """Persist and (optionally) load a single promotion event.

        Raises :class:`BridgeError` on any inconsistency — fail-closed.
        Idempotent: re-handling an already-applied event succeeds with
        ``idempotent_replay=True``.
        """
        artifact_id = event.subject_artifact_id

        # 1. Artifact MUST exist in the immutable store (fail-closed).
        artifact = self.artifact_store.get(artifact_id)
        if artifact is None:
            raise BridgeError(
                f"promotion bridge failed: artifact {artifact_id} not found "
                f"in artifact store"
            )

        # 2. Persist to the authoritative store FIRST.
        previous_stage = self.promotion_store.get_stage(artifact_id)
        self.promotion_store.upsert_from_event(event)

        # 3. Verify the store now reflects exactly what the event claims.
        stored_stage = self.promotion_store.get_stage(artifact_id)
        if stored_stage != event.to_stage:
            raise BridgeError(
                f"promotion bridge failed: store stage {stored_stage!r} != "
                f"event stage {event.to_stage!r} for {artifact_id}"
            )

        idempotent = previous_stage == event.to_stage

        # 4. Optionally load into a RuntimeLoader (manifest + callbacks).
        loaded = False
        detail = ""
        if self.runtime_loader is not None:
            loader_env = getattr(self.runtime_loader.config, "environment", None)
            env_value = getattr(loader_env, "value", str(loader_env))
            if env_value is not None and is_stage_compatible(
                event.to_stage, str(env_value).lower()
            ):
                loaded_strategy = self.runtime_loader.load_by_artifact_id(artifact_id)
                if loaded_strategy is None:
                    raise BridgeError(
                        f"promotion bridge failed: RuntimeLoader could not "
                        f"load {artifact_id}"
                    )
                loaded = True
            else:
                detail = (
                    f"stage {event.to_stage.value} not compatible with loader "
                    f"environment {env_value}; persisted without loading"
                )
                logger.info(
                    "PromotionHook: %s persisted but not loaded (%s)",
                    artifact_id[:8],
                    detail,
                )

        logger.info(
            "PromotionHook: %s %s→%s persisted%s",
            artifact_id[:8],
            event.from_stage.value,
            event.to_stage.value,
            " + loaded" if loaded else "",
        )

        return BridgeOutcome(
            artifact_id=artifact_id,
            from_stage=event.from_stage.value,
            to_stage=event.to_stage.value,
            persisted=True,
            loaded_into_runtime=loaded,
            idempotent_replay=idempotent,
            detail=detail,
        )


__all__ = ["BridgeError", "BridgeOutcome", "PromotionHook"]
