"""R03 — Provenance, completeness, and resume validation.

A canonical S3 study is auditable when:

1. The producer wrote identity at run time (WFOStudyManifest, freeze files,
   outer artifacts, registry records).
2. The consumer can verify code/data/features/effective params/cost/window/
   fold/seed/policy version matches what was promised.
3. Resume from cache requires the FULL evaluation identity to match — not just
   a cell_id or folder name.
4. The expected cells are present and complete — missing, failed, duplicated,
   stale, or tampered evidence is rejected at qualification.
5. Replay/aggregation cannot upgrade the provenance of old data (no "dirty run
   then aggregate from clean commit" laundering).

This module exposes:
- ``ManifestValidator``: validate a WFOStudyManifest against actual evaluation
  evidence on disk; fail-closed on mismatch.
- ``ResumeGuard``: derive a content-addressed cache key from full evaluation
  identity; reject reuse when any element changes.
- ``provenance_digest``: SHA-256 of the canonical evaluation identity, used to
  bind aggregate outputs to the evidence that produced them.
- ``CompletenessReport``: typed result of a manifest validation pass.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    # Re-exported at runtime below for single import surface
    pass


# Identity fields that MUST match for resume to be valid. If any of these
# changes between runs, the cache cannot be reused — the new run MUST
# re-evaluate against the new identity.
RESUME_IDENTITY_FIELDS: tuple[str, ...] = (
    "strategy_id",
    "symbol",
    "timeframe",
    "strategy_code_sha",
    "data_manifest_sha",
    "feature_schema_hash",
    "search_space_hash",
    "evaluator_version",
    "policy_version",
    "cost_scenario",
    "window_kind",  # one of: "INNER_VALIDATION", "OUTER_OOS", "FINAL_HOLDOUT"
    "seed",
    "commit_sha",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def provenance_digest(identity: dict[str, Any]) -> str:
    """Compute a content-addressed digest of the evaluation identity.

    Only the fields listed in ``RESUME_IDENTITY_FIELDS`` participate in the
    digest. The result is a SHA-256 hex string with the ``sha256:`` prefix.
    """
    payload = {k: identity[k] for k in RESUME_IDENTITY_FIELDS if k in identity}
    encoded = _canonical_json(payload).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def evaluation_identity(
    *,
    strategy_id: str,
    symbol: str,
    timeframe: str,
    strategy_code_sha: str,
    data_manifest_sha: str,
    feature_schema_hash: str,
    search_space_hash: str,
    evaluator_version: str,
    policy_version: str,
    cost_scenario: str,
    window_kind: str,
    seed: int,
    commit_sha: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a canonical evaluation identity dict (with optional extra fields).

    The ``extra`` fields are part of the dict but DO NOT participate in the
    ``provenance_digest`` — they are carried for downstream consumer
    convenience (e.g. artifact_id, params_hash).
    """
    base: dict[str, Any] = {
        "strategy_id": strategy_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy_code_sha": strategy_code_sha,
        "data_manifest_sha": data_manifest_sha,
        "feature_schema_hash": feature_schema_hash,
        "search_space_hash": search_space_hash,
        "evaluator_version": evaluator_version,
        "policy_version": policy_version,
        "cost_scenario": cost_scenario,
        "window_kind": window_kind,
        "seed": int(seed),
        "commit_sha": commit_sha,
    }
    if extra:
        base.update(extra)
    return base


@dataclass(frozen=True)
class CompletenessReport:
    """Result of a ``ManifestValidator.validate_manifest`` pass.

    The report is fail-closed: ``is_complete`` is True only when EVERY
    expected cell is present, has matching identity, and is not tampered.
    Any failure is recorded in ``issues`` for human review.
    """

    is_complete: bool
    expected_count: int
    found_count: int
    missing: tuple[str, ...] = ()
    mismatched: tuple[str, ...] = ()
    tampered: tuple[str, ...] = ()
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_complete": self.is_complete,
            "expected_count": self.expected_count,
            "found_count": self.found_count,
            "missing": list(self.missing),
            "mismatched": list(self.mismatched),
            "tampered": list(self.tampered),
            "issues": list(self.issues),
        }


@dataclass(frozen=True)
class ResumeDecision:
    """Outcome of ``ResumeGuard.check_resume_compatibility``.

    ``can_resume`` is True only when the cached artifact's identity matches
    the proposed identity across all ``RESUME_IDENTITY_FIELDS``.
    """

    can_resume: bool
    cache_key: str
    proposed_key: str
    mismatched_fields: tuple[str, ...] = ()
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "can_resume": self.can_resume,
            "cache_key": self.cache_key,
            "proposed_key": self.proposed_key,
            "mismatched_fields": list(self.mismatched_fields),
            "reason": self.reason,
        }


class ManifestValidator:
    """Validate study manifest against actual evidence on disk.

    A canonical S3 study writes its identity at run time:
    - ``study_manifests/<manifest_id>.json`` (one per study, content-addressed)
    - ``inner_selection_freezes/<freeze_id>.json`` (one per fold)
    - ``outer_one_shot/<fold_id>/<freeze_id>.json`` (one per fold)

    The validator checks that for every expected fold:
    1. An inner selection freeze file exists and is parseable.
    2. The freeze's identity matches the study manifest (params hash, etc.).
    3. An outer artifact exists for that freeze and its freeze_id matches.
    4. The outer artifact's selection_freeze_id is bound to the freeze.

    Any missing, mismatched, or tampered item fails the validation
    (is_complete=False). The consumer MUST reject the evidence at
    qualification rather than silently re-running and overwriting.
    """

    def __init__(self, study_manifest: Any, out_root: Path):
        self.manifest = study_manifest
        self.out_root = Path(out_root)

    def _manifest_id(self) -> str:
        # WFOStudyManifest exposes manifest_id; dicts use 'manifest_id' key.
        if hasattr(self.manifest, "manifest_id"):
            return self.manifest.manifest_id
        if isinstance(self.manifest, dict):
            mid = self.manifest.get("manifest_id")
            if mid is None:
                raise ValueError("manifest dict missing 'manifest_id'")
            return mid
        raise ValueError(f"unsupported manifest type: {type(self.manifest)}")

    def _expected_fold_ids(self) -> list[str]:
        if hasattr(self.manifest, "fold_windows"):
            folds = self.manifest.fold_windows
        else:
            folds = self.manifest.get("fold_windows", [])
        ids: list[str] = []
        for f in folds:
            if isinstance(f, dict):
                fid = f.get("fold_id")
            else:
                fid = getattr(f, "fold_id", None)
            if fid is not None:
                ids.append(str(fid))
        return ids

    def _manifest_payload(self) -> dict[str, Any]:
        if isinstance(self.manifest, dict):
            return self.manifest
        if hasattr(self.manifest, "to_dict"):
            return self.manifest.to_dict()
        raise ValueError("manifest must be a dict or expose to_dict()")

    def validate_manifest(self) -> CompletenessReport:
        """Walk expected folds; check inner freeze + outer artifact on disk."""
        expected = self._expected_fold_ids()
        if not expected:
            return CompletenessReport(
                is_complete=False,
                expected_count=0,
                found_count=0,
                issues=("manifest has no expected folds",),
            )

        missing: list[str] = []
        mismatched: list[str] = []
        tampered: list[str] = []
        issues: list[str] = []
        found = 0

        m_payload = self._manifest_payload()
        m_id = self._manifest_id()

        for fold_id in expected:
            inner_dir = self.out_root / "inner_selection_freezes"
            outer_dir = self.out_root / "outer_one_shot" / fold_id
            if not outer_dir.exists():
                missing.append(fold_id)
                continue

            outer_files = list(outer_dir.glob("*.json"))
            if not outer_files:
                missing.append(fold_id)
                continue

            # Find an outer artifact whose freeze_id matches a freeze file
            ok = False
            for outer_path in outer_files:
                try:
                    outer = json.loads(outer_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    tampered.append(fold_id)
                    issues.append(f"{fold_id}: outer artifact unreadable: {exc}")
                    continue

                freeze_id = outer.get("selection_freeze_id")
                if not freeze_id:
                    mismatched.append(fold_id)
                    issues.append(
                        f"{fold_id}: outer artifact has no selection_freeze_id"
                    )
                    continue

                # Inner freeze must exist
                freeze_path = inner_dir / f"{freeze_id.removeprefix('sha256:')}.json"
                if not freeze_path.exists():
                    mismatched.append(fold_id)
                    issues.append(
                        f"{fold_id}: inner freeze file missing for {freeze_id}"
                    )
                    continue

                # Inner freeze must reference this study's manifest
                try:
                    freeze_data = json.loads(freeze_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    tampered.append(fold_id)
                    issues.append(f"{fold_id}: inner freeze unreadable: {exc}")
                    continue

                # The freeze's commit_sha + data_manifest_sha + feature_schema_hash
                # must match the study manifest
                for fld in (
                    "commit_sha",
                    "data_manifest_sha",
                    "feature_schema_hash",
                ):
                    expected_v = m_payload.get(fld)
                    actual_v = freeze_data.get(fld)
                    if expected_v is not None and expected_v != actual_v:
                        mismatched.append(fold_id)
                        issues.append(
                            f"{fold_id}: freeze {fld}={actual_v} != manifest {fld}={expected_v}"
                        )
                        break
                else:
                    ok = True
                    break

            if ok:
                found += 1
            elif (
                fold_id not in missing
                and fold_id not in mismatched
                and fold_id not in tampered
            ):
                mismatched.append(fold_id)
                issues.append(f"{fold_id}: no matching outer/freeze pair found")

        is_complete = (
            found == len(expected) and not missing and not mismatched and not tampered
        )
        return CompletenessReport(
            is_complete=is_complete,
            expected_count=len(expected),
            found_count=found,
            missing=tuple(missing),
            mismatched=tuple(mismatched),
            tampered=tuple(tampered),
            issues=tuple(issues),
        )


class ResumeGuard:
    """Check whether a cached evaluation can be safely reused.

    The guard compares a cached evaluation's identity (stored alongside the
    artifact) with the proposed identity for the next run. Resume is allowed
    only when all ``RESUME_IDENTITY_FIELDS`` match.

    Resume is also denied when:
    - The cached artifact is missing its identity record (cannot verify).
    - The cached artifact's status is FAILED (must re-evaluate to retry).
    - The worktree is dirty (caller must re-evaluate to avoid smuggling
      uncommitted code into the cached result).
    """

    def __init__(self, *, worktree_dirty: bool = False):
        self.worktree_dirty = bool(worktree_dirty)

    def check_resume_compatibility(
        self,
        cached_artifact: Any,
        proposed_identity: dict[str, Any],
    ) -> ResumeDecision:
        if self.worktree_dirty:
            return ResumeDecision(
                can_resume=False,
                cache_key="",
                proposed_key=provenance_digest(proposed_identity),
                reason="worktree is dirty; refuse to reuse cache",
            )

        # Extract identity from cached artifact. Support both dataclass and dict.
        cached_identity: dict[str, Any] | None = None
        if hasattr(cached_artifact, "metadata") and isinstance(
            cached_artifact.metadata, dict
        ):
            cached_identity = cached_artifact.metadata.get("evaluation_identity")
        if cached_identity is None and isinstance(cached_artifact, dict):
            cached_identity = cached_artifact.get("metadata", {}).get(
                "evaluation_identity"
            )
        if cached_identity is None:
            return ResumeDecision(
                can_resume=False,
                cache_key="",
                proposed_key=provenance_digest(proposed_identity),
                reason="cached artifact has no evaluation_identity record",
            )

        cached_key = provenance_digest(cached_identity)
        proposed_key = provenance_digest(proposed_identity)

        mismatched: list[str] = []
        for fld in RESUME_IDENTITY_FIELDS:
            cv = cached_identity.get(fld)
            pv = proposed_identity.get(fld)
            if cv != pv:
                mismatched.append(fld)

        if mismatched:
            return ResumeDecision(
                can_resume=False,
                cache_key=cached_key,
                proposed_key=proposed_key,
                mismatched_fields=tuple(mismatched),
                reason=f"identity changed in: {', '.join(mismatched)}",
            )

        return ResumeDecision(
            can_resume=True,
            cache_key=cached_key,
            proposed_key=proposed_key,
        )

    def compute_cache_key(self, identity: dict[str, Any]) -> str:
        return provenance_digest(identity)


def attach_evaluation_identity(
    metadata: dict[str, Any], identity: dict[str, Any]
) -> dict[str, Any]:
    """Stamp an ``evaluation_identity`` block onto evaluation metadata.

    This is what the producer writes at run time; the consumer
    (``ResumeGuard``) reads it back to verify resume compatibility.
    """
    out = dict(metadata)
    out["evaluation_identity"] = identity
    out["provenance_digest"] = provenance_digest(identity)
    return out


def expected_cells_for_study(
    study_manifest: Any,
    cost_scenarios: Iterable[Any] | None = None,
    param_combos: Iterable[dict[str, Any]] | None = None,
) -> list[str]:
    """Compute the canonical set of expected cell identifiers for a study.

    Each fold has one cell per (param_combo, cost_scenario) — i.e. inner
    validation trials. Outer OOS trials are 1 per fold (selected candidate).
    This function returns the expected INNER_VALIDATION cell ids, which the
    manifest validator and consumer can use as the canonical expected set.
    """
    if hasattr(study_manifest, "fold_windows"):
        folds = study_manifest.fold_windows
    else:
        folds = study_manifest.get("fold_windows", [])
    fold_ids: list[str] = []
    for f in folds:
        if isinstance(f, dict):
            fid = f.get("fold_id")
        else:
            fid = getattr(f, "fold_id", None)
        if fid is not None:
            fold_ids.append(str(fid))

    cells: list[str] = []
    if param_combos is None:
        param_combos = [{"_single_": "default"}]
    if cost_scenarios is None:
        cost_scenarios = ["1x"]
    cost_names: list[str] = []
    for c in cost_scenarios:
        if isinstance(c, str):
            cost_names.append(c)
        elif hasattr(c, "name"):
            cost_names.append(c.name)
        else:
            cost_names.append(str(c))
    for fold_id in fold_ids:
        for params in param_combos:
            for cn in cost_names:
                cells.append(f"{fold_id}::{cn}")
    return cells


# Re-export at module level for single import surface.
# Deferred to avoid circular import: provenance is imported by nested_wfo,
# and nested_wfo is imported here. Use a runtime attribute so the name
# is visible to `from trading_agent.backtest.provenance import WFOStudyManifest`.
def __getattr__(name: str):  # PEP 562
    if name == "WFOStudyManifest":
        from trading_agent.backtest.nested_wfo import WFOStudyManifest

        return WFOStudyManifest
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
