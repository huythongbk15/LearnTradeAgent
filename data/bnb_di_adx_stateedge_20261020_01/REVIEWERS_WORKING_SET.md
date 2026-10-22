# Reviewer's working set — `di_adx_state_edge`, revision under review

Generated from the artifacts themselves: line numbers are grepped out of the
source, hashes come from the sealed manifests. **This is a map to primary
material, not a summary of it.** A reviewer other than the author should read
the files below, not this document.

## Revision under review

**This file states no identity.** The identities live in the manifests,
which are sealed per revision:

- `data/bnb_di_adx_stateedge_20261020_01/manifest.json`
- `data/bnb_di_adx_stateedge_prereg_20261020_01/manifest.json`
- `data/bnb_native_review_20261020_01/manifest.json`

A reviewer must read the identities from those manifests (or recompute
them) and must not take this file as identity evidence.

## 1. The candidate implementation

`src/trading_agent/strategies/di_adx_state_edge.py` — sha256 `49758cac6e662691fe1b3611256eac7c057a91489034203f69f5545d803dd351`

## 2. The five token hops — every site that carries the scope token

These are the sites the review must judge. The surrounding diff against
HEAD also contains unrelated 2026-10-19/20 work; the scope-token lines
below are the candidate's own surface.

### `scripts/run_wfo_parallel.py`

- L227: `strategy_id: str, scope_token: str | None = None`
- L235: `from trading_agent.strategies.canonical.candidates import resolve_scoped_candidate_id`
- L237: `scoped_id = resolve_scoped_candidate_id(scope_token)`
- L331: `scope_token=(self.execution_context or {}).get("scope_token"),`
- L462: `scope_token: str | None = None,`
- L498: `"scope_token": scope_token,`
- L513: `scope_token=scope_token,`
- L534: `scope_token: str | None = None,`
- L544: `registry_canonical = build_default_registry(scope_token=scope_token)`
- L645: `scope_token = exec_config.get("scope_token")`
- L646: `if scope_token:`
- L647: `context["scope_token"] = scope_token`
- L674: `scope_token = (protocol.get("execution") or {}).get("scope_token")`
- L675: `strategy_sha = wfo._compute_strategy_code_sha(spec.strategy_id, scope_token)`
- L678: `build_default_registry(scope_token=scope_token).describe(spec.strategy_id))`
- L778: `validation_budget=None, scope_token=None, max_retries=1):`
- L805: `cells, folds = build_canonical_cells(spec, scope_token=scope_token)`
- L857: `**({"scope_token": scope_token} if scope_token else {}),`
- L937: `**({"scope_token": self.trial_context["scope_token"]}`
- L938: `if "scope_token" in self.trial_context else {}),`
- L961: `scope_token=self.trial_context.get("scope_token"),`
- L1125: `scope_token=(protocol.get("execution") or {}).get("scope_token")`
- L1343: `scope_token=(protocol.get("execution") or {}).get("scope_token"),`
- L1420: `def _verify_approved_payload_binding(protocol, scope_token):`
- L1449: `protocol, scope_token, root=resolved_root,`
- L1462: `_verify_approved_payload_binding(protocol, (protocol.get("execution") or {}).get("scope_token"))`
- L1463: `scope_token = (protocol.get("execution") or {}).get("scope_token")`
- L1464: `cells, actual_folds = build_canonical_cells(spec, scope_token=scope_token)`
- L1688: `and args.scope_token):`
- L1731: `param_grid=get_param_grid_for_strategy(args.strategy, args.scope_token),`
- L1753: `reuse_from=args.reuse_from, scope_token=args.scope_token,`
- L1765: `_verify_approved_payload_binding(protocol, args.scope_token)`

### `src/trading_agent/backtest/tournament.py`

- L613: `scope_token: str | None = None,`
- L672: `"scope_token": scope_token,`
- L698: `"scope_token": scope_token,`
- L1015: `scope_token = spec_primitives.get("scope_token")`
- L1016: `registry = build_default_registry(scope_token=scope_token)`
- L1028: `spec.strategy_id, spec.params, scope_token=scope_token`
- L1217: `scope_token=scope_token,`

### `scripts/full_system_backtest.py`

- L248: `scope_token: str | None = None,`
- L407: `self.strategy_name, self.strategy_params, scope_token=scope_token`

### `src/trading_agent/backtest/campaign_execution.py`

- L232: `scope_token=self.context.get("scope_token")`
- L339: `"scope_token": self.context.get("scope_token"),`

### `src/trading_agent/strategies/canonical/candidates.py`

- L794: `scope_token: str | None = None,`
- L801: `registry = build_default_registry(scope_token=scope_token)`
- L803: `scoped_id = resolve_scoped_candidate_id(scope_token)`
- L824: `scope_token: str | None = None,`
- L832: `strategy_id, params, scope_token=scope_token`
- L1019: `SCOPED_CANDIDATE_SCOPE_TOKENS: dict[str, str] = {`
- L1039: `def resolve_scoped_candidate_id(scope_token: str | None) -> str | None:`
- L1040: `"""Return the candidate id unlocked by ``scope_token``, or None.`
- L1045: `if not scope_token:`
- L1047: `return SCOPED_CANDIDATE_SCOPE_TOKENS.get(scope_token)`
- L1050: `def build_default_registry(*, scope_token: str | None = None) -> CanonicalStrategyRegistry:`
- L1053: ```scope_token`` is the controlled path: it unlocks exactly the one scoped`
- L1093: `scoped_id = resolve_scoped_candidate_id(scope_token)`

## 3. The assertions

Each file, then each test function it defines. The reviewer judges whether
these prove what the packet claims — not merely that they pass.

### `tests/test_di_adx_state_edge_contract.py` — sha256 `0e3f470e8ad50908fc96e6414d2be6779137729c27c15d2fc4b47d2326f28156`

- L140: `def test_s3_one_buy_per_bull_episode`
- L149: `def test_s3_second_episode_emits_a_new_buy`
- L157: `def test_s4_sell_only_on_falling_edge_of_an_episode`
- L168: `def test_s6_first_bar_has_no_predecessor_so_no_transition`
- L182: `def test_s6_candidate_emits_no_cooldown_specific_signal`
- L203: `def test_s1_future_perturbation_does_not_change_past_signals`
- L215: `def test_s1_canonical_path_is_causal_under_future_perturbation`
- L231: `def test_s2_canonical_path_matches_full_frame_shifted_by_one`
- L247: `def test_s8_candidate_is_not_registered_and_has_no_ma_parameters`
- L262: `def test_s8_control_candidate_is_untouched`
- L273: `def test_s5_native_protective_stop_no_reentry_then_new_edge`
- L381: `def test_s9_timing_verified_by_timestamp_and_real_fill`
- L426: `def test_s7_terminal_inventory_identity`
- L470: `def test_s5b_state_machine_no_buy_while_state_stays_on_after_a_stop`
- L501: `def test_s5c_state_machine_flat_position_emits_nothing_until_a_new_edge`

### `tests/test_di_adx_state_edge_scoped_path.py` — sha256 `82f66bba325c9e8772aa0d74894bb4badc4902843313e3c5d148bef6a9ce73c5`

- L47: `def test_candidate_is_absent_from_every_broad_registry`
- L56: `def test_control_still_resolves_without_a_token`
- L62: `def test_correct_scope_token_resolves_the_candidate`
- L71: `def test_scope_token_unlocks_the_canonical_adapter`
- L79: `def test_missing_or_wrong_token_fails_closed`
- L85: `def test_wrong_token_does_not_unlock_the_registry`
- L92: `def test_scope_token_map_names_exactly_one_candidate_per_token`
- L97: `def test_run_cell_without_the_token_fails_closed_on_resolution`
- L125: `def test_scope_token_is_inside_the_execution_scope_identity`
- L152: `def test_resume_cache_key_covers_the_execution_context`
- L174: `def test_wrong_token_fails_before_any_simulation_artifact`

### `tests/test_di_adx_state_edge_entrypoint_integration.py` — sha256 `f615b9957fc3f1e3a996afab2da690dbc4f54329ba7b2974223e3d697733d9bd`

- L96: `def test_scope_token_survives_launcher_protocol_and_trial_context`
- L114: `def test_correct_token_resolves_the_candidate_through_the_entrypoint_chain`
- L139: `def test_wrong_token_is_blocked_at_plan_time_before_any_cell`
- L155: `def test_token_swapped_between_plan_and_execution_is_blocked_before_simulation`

### `tests/test_validation_timeout_and_ledger_faults.py` — sha256 `f4d371a772c627ddbe19b59607873972b100915840972f176b0a7457f5bc383d`

- L86: `def test_f1_timeout_stops_the_whole_group_including_the_descendant`
- L120: `def test_f2_killed_attempt_leaves_a_durable_record_and_no_completed_artifact`
- L165: `def test_f2b_interrupted_attempt_is_refused_by_the_aggregation`
- L264: `def test_f2c_real_killed_cell_was_never_certified_by_the_aggregator`

### `tests/test_wfo_validation_envelope.py` — sha256 `0e61343674bd2ae715d88bb212c82b9e1d1a22c9cc5aa70fb59048cbcc9b0b6c`

- L53: `def test_approved_policy_values_are_pinned`
- L67: `def test_caps_are_named_constants_with_sane_ordering`
- L93: `def test_budget_guard_accepts_values_exactly_at_the_cap`
- L102: `def test_budget_guard_rejects_one_second_above_each_cap`
- L125: `def test_cell_timeout_above_the_cap_is_rejected`
- L135: `def test_total_timeout_above_the_cap_is_rejected`
- L146: `def test_rss_above_the_cap_is_rejected`
- L156: `def test_rss_below_the_floor_is_rejected`
- L166: `def test_other_validation_only_guards_are_intact`
- L184: `def test_cli_rejects_an_out_of_envelope_value_before_creating_a_packet`

### `tests/test_indicator_window_equivalence.py` — sha256 `9efccc9bc19fe530249f7bbf75bc8b60d51d6fa65251c9dbd1ce8ff4a06f73bc`

- L78: `def test_canonical_series_is_full_frame_signal_shifted_by_one`
- L119: `def test_canonical_series_is_not_equal_unshifted`
- L138: `def test_simulator_action_bar_is_two_bars_after_the_crossover_bar`

## 3b. The new modules the next review must read in full

### `src/trading_agent/backtest/attempt_ledger.py` — sha256 `4c73bf8da2ffff57b5527e797374502ee2ddd237c555976e2832f010bcdd18fc`

```python
"""Attempt and evaluation classification for the evaluation ledger.

Two distinctions the earlier ledger rule got wrong:

1. **A start record without a result record is not yet INTERRUPTED.** The
   worker writes ``started.json`` before it executes, so a missing
   ``result.json`` can simply mean the worker is **still running**. An attempt
   is INTERRUPTED only when the worker process is **gone**.

2. **The cache key identifies the EVALUATION, not each ATTEMPT.** One planned
   cell (one evaluation) may have several attempt records. Those attempts are
   the history of one evaluation and must not be counted as separate
   evaluations, nor joined to the wrong evaluation, nor silently dropped.

The evaluation identity is taken from the attempt's own request record
(``cell_id`` + ``cost_scenario``), so it is the same in the campaign path and
in the validation path, where the attempt directories live in different places.
"""
from __future__ import annotations

import json
import os
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

COMPLETED = "COMPLETED"
FAILED = "FAILED"
INTERRUPTED = "INTERRUPTED"
RUNNING = "RUNNING"
UNKNOWN = "UNKNOWN"


def _process_alive(pid: object) -> bool:
    """True when the pid still exists. A missing /proc entry means it is gone."""
    try:
        pid_int = int(pid)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    if pid_int <= 0:
        return False
    return Path(f"/proc/{pid_int}").exists()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def attempt_started_epoch(attempt_dir: Path) -> float:
    """The attempt's real start time.

    ``started_at_epoch`` is written by the producer and is the authoritative
    field. ``started_at`` (ISO-8601) is the fallback for records written
    before the epoch field existed. A record with neither is treated as the
    oldest, never as the newest.
    """
    started = _read_json(attempt_dir / "started.json")
    epoch = started.get("started_at_epoch")
    if isinstance(epoch, (int, float)) and epoch > 0:
        return float(epoch)
    iso = started.get("started_at")
    if isinstance(iso, str) and iso:
        try:
            return datetime.fromisoformat(iso).timestamp()
        except ValueError:
            return 0.0
    return 0.0


def attempt_sequence(attempt_dir: Path) -> int:
    """Producer-assigned ordering, when the record carries one."""
    started = _read_json(attempt_dir / "started.json")
    sequence = started.get("attempt_sequence")
    if isinstance(sequence, int) and sequence >= 0:
        return sequence
    return -1


def classify_attempt(attempt_dir: Path) -> str:
    """Classify ONE attempt directory (the worker's ``cell_execution/<uuid>``).

    COMPLETED   the worker wrote a result whose artifact completed
    FAILED      the worker wrote a result whose artifact did not complete
    INTERRUPTED the worker wrote a start record, no result, and the worker
                process is gone
    RUNNING     the worker wrote a start record, no result, and the worker
                process is still alive
    UNKNOWN     neither a start nor a result record
    """
    result_path = attempt_dir / "result.json"
    if result_path.exists():
        result = _read_json(result_path)
        artifact = result.get("artifact") or {}
        status = artifact.get("status")
        if status == COMPLETED:
            return COMPLETED
        return FAILED if status else UNKNOWN

    started_path = attempt_dir / "started.json"
    if not started_path.exists():
        return UNKNOWN
    started = _read_json(started_path)
    if _process_alive(started.get("worker_pid")):
        return RUNNING
    return INTERRUPTED


def attempt_identity(attempt_dir: Path) -> tuple[str, str]:
    """The evaluation identity of one attempt, from its own request record."""
    started = _read_json(attempt_dir / "started.json")
    if not started:
        result = _read_json(attempt_dir / "result.json")
        started = result.get("request") or {}
    return (
        str(started.get("cell_id") or ""),
        str(started.get("cost_scenario") or ""),
    )


def collect_attempt_dirs(roots: Iterable[Path]) -> list[Path]:
    """Every attempt directory under the given roots, newest first.

    The attempt directory is the worker's ``cell_execution/<uuid>``; a root may
    be a packet root, a ``cell_attempts/<key>`` directory or a
    ``cell_execution`` directory itself.
    """
    found: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.name == "cell_execution" and root.is_dir():
            found.extend(child for child in sorted(root.iterdir()) if child.is_dir())
            continue
        for candidate in root.rglob("cell_execution/*"):
            if candidate.is_dir():
                found.append(candidate)
    unique = {path: None for path in found}
    return sorted(unique, key=attempt_started_epoch, reverse=True)


def summarise_evaluation(attempt_dirs: list[Path]) -> dict[str, Any]:
    """Summarise ONE evaluation from all of its attempt directories.

    The evaluation's outcome is that of its **latest** attempt, ordered by the
    producer's real start time (epoch, then the producer sequence), never by
    directory name.
    """
    if not attempt_dirs:
        return {"attempts": 0, "outcome": UNKNOWN, "attempt_outcomes": []}
    ordered = sorted(
        set(attempt_dirs),
        key=lambda path: (attempt_started_epoch(path), attempt_sequence(path)),
        reverse=True,
    )
    outcomes = [classify_attempt(path) for path in ordered]
    return {
        "attempts": len(ordered),
        "outcome": outcomes[0],
        "attempt_outcomes": outcomes,
    }


def attempt_roots_for_evaluation(root: Path, evaluation_key: str) -> list[Path]:
    """Every attempt root of ONE evaluation (one cache key).

    The runner creates a fresh ``cell_attempts/<uuid>/`` per attempt, so the
    attempt history of one evaluation is spread over several uuid directories.
    They are matched by the identity recorded in each attempt's
    ``execution_started.json``, whose content hash IS the cache key.
    """
    from trading_agent.backtest.campaign_evidence import content_hash

    attempts_root = root / "cell_attempts"
    if not attempts_root.is_dir():
        return []
    matched: list[Path] = []
    for child in sorted(attempts_root.iterdir()):
        if not child.is_dir():
            continue
        record = _read_json(child / "execution_started.json")
        identity = record.get("identity")
        if not isinstance(identity, dict) or not identity:
            continue
        if content_hash(identity) == evaluation_key:
            matched.append(child)
    return matched


def evaluation_attempt_dirs(root: Path, evaluation_key: str) -> list[Path]:
    """The worker attempt directories of every attempt of one evaluation."""
    dirs: list[Path] = []
    for attempt_root in attempt_roots_for_evaluation(root, evaluation_key):
        dirs.extend(collect_attempt_dirs([attempt_root]))
    return dirs


def write_ledger_row(
    ledger_dir: Path,
    evaluation_key: str,
    identity: dict[str, Any],
    attempt_dirs: list[Path],
    extra: dict[str, Any] | None = None,
) -> Path:
    """Publish a derived snapshot of all unique attempts of one evaluation.

    Durable attempt receipts are authoritative. Rewriting this snapshot (for
    example on a cache hit) must not append the same history a second time.
    Registry accounting remains unknown until separately reconciled.
    """
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / f"{evaluation_key}.json"
    summary = summarise_evaluation(attempt_dirs)
    # ``extra`` may add context, but it may never overwrite the accounting
    # fields this module promises to keep unknown.
    merged: dict[str, Any] = dict(extra or {})
    merged.update({
        "identity": identity,
        "evaluation": "cell_id+cost_scenario",
        "evaluation_key": evaluation_key,
        "attempts": summary["attempts"],
        "attempt_outcomes": summary["attempt_outcomes"],
        "outcome": summary["outcome"],
        "ledger_updated_at": datetime.now(timezone.utc).isoformat(),
        "registry_counted": None,
        "registry_accounting_status": "UNKNOWN_NOT_RECONCILED",
        "live_authorized": False,
    })
    # A cache hit may have no local attempt root: the row then has no attempt
    # history, and its outcome must not contradict the artifact it reports.
    if not attempt_dirs:
        artifact_status = merged.get("artifact_status")
        if artifact_status in (COMPLETED, FAILED):
            merged["outcome"] = artifact_status
            merged["attempt_history_complete"] = False
    # The ledger is a replaceable index, unlike immutable attempt receipts.
    # Publish atomically so interruption preserves either the old or new row.
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=ledger_dir,
            prefix=f".{evaluation_key}.", suffix=".pending", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(merged, stream, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            fd = os.open(ledger_dir, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return path


def record_ledger_error(ledger_dir: Path, evaluation_key: str, error: BaseException) -> Path:
    """Durably record a ledger failure.

    A ledger gap must never disappear silently: the error is appended to an
    append-only error log so a reader can see that the ledger is incomplete.
    """
    ledger_dir.mkdir(parents=True, exist_ok=True)
    path = ledger_dir / "_ledger_errors.jsonl"
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({
            "evaluation_key": evaluation_key,
            "error": f"{type(error).__name__}: {error}",
            "at": datetime.now(timezone.utc).isoformat(),
            "epoch": time.time(),
        }) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return path
```

### `src/trading_agent/backtest/preregistration_binding.py` — sha256 `d229d2dfae97260acebe5e6faba430f052509617f7887968034c997250af0d14`

```python
"""Binding between an approved preregistration and the run that executes it.

Running is not the same as running **the approved payload**. A launcher that
happens to work proves nothing about which payload it executed. This module
closes that gap: it verifies, **before the first cell**, that the frozen
protocol of the run agrees with the approved preregistration on every field
that the preregistration fixes:

    approval state / approver / approval timestamp
    identity A (source)      identity B (preregistration)
    input binding hash       the candidate's parameter set
    the inner-validation windows
    the cost scenarios
    the execution budget     the scope token

Any single field that differs blocks the run. The checks are reported
individually so a mismatch names the field, not "something is wrong".
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from trading_agent.strategies.canonical.candidates import (  # noqa: E402
    SCOPED_CANDIDATE_PREREGISTRATION_PATHS,
    SCOPED_CANDIDATE_SCOPE_TOKENS,
)


def _canonical(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def normalise_singleton_grid(grid: Any) -> dict[str, Any] | None:
    """``{axis: [value]}`` -> ``{axis: value}``.

    A candidate registered with a single fixed value per axis is planned by the
    CLI as a one-element grid, while the preregistration states the scalar
    parameter. The two must be compared in one normalised form; a grid that is
    not a singleton per axis is rejected (it is a search, not a fixed set).
    """
    if not isinstance(grid, dict) or not grid:
        return None
    normalised: dict[str, Any] = {}
    for axis, values in grid.items():
        if not isinstance(values, (list, tuple)) or len(values) != 1:
            return None
        normalised[axis] = values[0]
    return normalised


def preregistration_fingerprint(payload: dict[str, Any]) -> str:
    """Identity B: the sha256 of the canonical preregistration payload."""
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()


def scoped_token_map_is_consistent() -> bool:
    """Every scope token must have exactly one preregistration packet.

    The two maps are keyed by the same tokens, so the check is that the key
    sets are equal AND that every token resolves to a candidate id that the
    token map and the candidate map agree on. It must not compare a map with
    itself.
    """
    tokens = set(SCOPED_CANDIDATE_SCOPE_TOKENS)
    paths = set(SCOPED_CANDIDATE_PREREGISTRATION_PATHS)
    if tokens != paths:
        return False
    for token, candidate in SCOPED_CANDIDATE_SCOPE_TOKENS.items():
        if not candidate:
            return False
        if candidate not in set(SCOPED_CANDIDATE_SCOPE_TOKENS.values()):
            return False
        if not SCOPED_CANDIDATE_PREREGISTRATION_PATHS.get(token):
            return False
    return True


def load_approved_payload(scope_token: str | None, root: Path) -> tuple[dict, dict]:
    """Load the approved payload and its manifest for ``scope_token``.

    Raises when the token names no preregistration, when the payload cannot be
    read, or when the manifest's recorded identity B does not match the
    recomputed one — a payload edited after approval is not an approved payload.
    """
    if not scoped_token_map_is_consistent():
        raise ValueError(
            "the scope-token map and the preregistration map disagree"
        )
    relative = SCOPED_CANDIDATE_PREREGISTRATION_PATHS.get(scope_token or "")
    if not relative:
        raise ValueError(
            f"scope token {scope_token!r} names no preregistration packet"
        )
    # the same packet must exist under the repository root, so a run cannot bind
    # to a packet that has no counterpart in the registered tree
    repository_root = Path(__file__).resolve().parent.parent.parent.parent
    if not (repository_root / relative).exists():
        raise ValueError(
            f"the preregistration packet {relative!r} does not exist under the "
            f"repository root {repository_root}"
        )
    packet = root / relative
    payload_path = packet / "preregistration_payload.json"
    manifest_path = packet / "manifest.json"
    if not payload_path.exists() or not manifest_path.exists():
        raise ValueError(f"preregistration packet is incomplete: {packet}")
    payload = json.loads(payload_path.read_text())
    manifest = json.loads(manifest_path.read_text())
    recomputed = preregistration_fingerprint(payload)
    recorded = manifest.get("identity_B_preregistration_fingerprint")
    if recomputed != recorded:
        raise ValueError(
            "preregistration identity B does not match the approved manifest: "
            f"recomputed {recomputed}, manifest {recorded}. The payload was "
            "edited after approval."
        )
    return payload, manifest


def verify_registered_payload_binding(
    protocol: dict[str, Any],
    scope_token: str | None,
    *,
    root: Path,
    source_fingerprint: str,
) -> list[str]:
    """Verify the run against the approved payload. Returns the failed fields.

    An empty list means every checked field agrees. A non-empty list means the
    run must not start: the caller raises.
    """
    spec = protocol.get("spec") or {}
    scoped_candidate_ids = set(SCOPED_CANDIDATE_SCOPE_TOKENS.values())
    if spec.get("strategy_id") not in scoped_candidate_ids:
        # Not a scoped candidate: the preregistration binding does not apply.
        # The control and every ordinary study run unchanged; a scoped candidate
        # without its token still fails closed at the registry/grid resolution.
        return []
    repository_root = Path(__file__).resolve().parents[3]
    if (root.resolve() != repository_root
            and spec.get("evidence_class") != "SYNTHETIC_TEST_ONLY"):
        return ["preregistration_root"]
    payload, manifest = load_approved_payload(scope_token, root)
    request = payload["data"]["scope_change_request"]
    execution = protocol.get("execution") or {}
    budget = protocol.get("validation_budget") or {}
    folds = protocol.get("folds") or []

    windows = [[f["inner_val_start"], f["inner_val_end"]] for f in folds]
    costs = {
        c["name"]: {"fee_multiplier": c["fee_multiplier"],
                    "slippage_multiplier": c["slippage_multiplier"]}
        for c in spec.get("cost_scenarios", [])
    }
    expected_costs = {
        name: {"fee_multiplier": value["fee_multiplier"],
               "slippage_multiplier": value["slippage_multiplier"]}
        for name, value in payload["costs"].items()
    }

    # The approval has ONE authoritative state: ``approval_state``. The
    # procedure edits exactly approval_state, approved_scope, approved_by and
    # approved_at; nothing else is consulted, so following the procedure
    # cannot be rejected.
    normalised_params = normalise_singleton_grid(spec.get("param_grid"))
    expected_scope = request.get("expected_approved_scope")
    approved_contract = payload.get("execution_contract") or {}
    expected_bodies = list((approved_contract.get("native_contract_bodies_by_cost") or {}).values())
    contracts = protocol.get("native_execution_contracts") or {}
    contracts_match = bool(expected_bodies) and isinstance(contracts, dict) and bool(contracts)
    if contracts_match:
        from trading_agent.backtest.execution_contract import require_execution_contract

        try:
            for contract in contracts.values():
                require_execution_contract(contract.get("contract_id"), contract)
                if contract.get("body") not in expected_bodies:
                    contracts_match = False
        except (TypeError, ValueError, AttributeError):
            contracts_match = False

    checks: dict[str, bool] = {
        "approval_state": request.get("approval_state") == "APPROVED",
        "approved_by": bool(request.get("approved_by")),
        "approved_at": bool(request.get("approved_at")),
        "approved_scope": (
            bool(request.get("approved_scope"))
            and bool(expected_scope)
            and request.get("approved_scope") == expected_scope
        ),
        "identity_A_source": (
            source_fingerprint == payload.get("identity_A_source_fingerprint")
            == manifest.get("identity_A_source_fingerprint")
        ),
        "identity_B_preregistration": (
            preregistration_fingerprint(payload)
            == manifest.get("identity_B_preregistration_fingerprint")
        ),
        "input_sha256": (
            (protocol.get("input_binding") or {}).get("sha256")
            == payload["data"]["sha256"]
        ),
        "params": (
            normalised_params is not None
            and normalised_params == payload["candidate"]["params"]
        ),
        "runtime_config": (
            bool(approved_contract.get("runtime_config"))
            and execution.get("runtime_config") == approved_contract["runtime_config"]
        ),
        "native_execution_contracts": contracts_match,
        # the SUBJECT, not only its parameter values
        "strategy_id": spec.get("strategy_id") == payload["candidate"]["strategy_id"],
        "symbol": spec.get("symbol") == payload["candidate"].get("symbol"),
        "timeframe": spec.get("timeframe") == payload["candidate"].get("timeframe"),
        "windows": windows == payload["geometry"]["inner_validation_windows"],
        "costs": costs == expected_costs,
        "cell_timeout": (
            execution.get("cell_timeout") == payload["budget"]["cell_timeout_seconds"]
        ),
        "total_timeout": (
            budget.get("total_timeout_seconds")
            == payload["budget"]["total_timeout_seconds"]
        ),
        "max_rss_mb": budget.get("max_rss_mb") == payload["budget"]["max_rss_mb"],
        "max_retries": (
            execution.get("max_retries") == payload["budget"]["max_retries"]
        ),
        "scope_token": execution.get("scope_token") == payload["scope_token_binding"]["token"],
    }
    return sorted(name for name, ok in checks.items() if not ok)
```

## 3c. The seven hops — read these call sites

| # | Site | What to check |
|---|---|---|
| 1 | `scripts/run_wfo_parallel.py` — `get_param_grid_for_strategy` + its caller | the scoped grid is reachable ONLY with the token; a scoped id without the token yields an empty grid |
| 2 | `scripts/run_wfo_parallel.py` — `prepare_parallel_protocol` budget guard | the same named caps as the CLI guard; no guard removed |
| 3 | `scripts/run_wfo_parallel.py` — `_verify_validation_protocol` budget guard | the same named caps; the producer and the verifier cannot disagree |
| 4 | `src/trading_agent/backtest/nested_wfo.py` — `run_nested_wfo` registry | the token comes from the execution context, so the worker resolves the candidate |
| 5 | `src/trading_agent/backtest/nested_wfo.py` — `_compute_strategy_code_sha` | the SHA must be the real one, NOT `"unknown"` — this is the source-attribution point |
| 6 | `src/trading_agent/backtest/campaign_execution.py` — artifact rebuild / validation | the registry is rebuilt with the token from the runner context |
| 7 | `src/trading_agent/backtest/selection_eligibility.py` — `verify_validation_reports` | the report attribution rebuild uses the token |

Plus, new in this revision:

| Site | What to check |
|---|---|
| `scripts/run_wfo_parallel.py` — `_verify_approved_payload_binding` | the binding gate runs in the launcher BEFORE the worker is spawned AND independently in the worker before its first cell; each checked field is named on mismatch |
| `scripts/run_wfo_parallel.py` — the summary writer | `qualification_status` / `all_folds_selected` / `sample_complete` / `validation_fold_rows` — `VALIDATION_COMPLETE` must not be readable as qualified or as sample-sufficient |
| `scripts/run_wfo_parallel.py` — the empty-grid guard | an unsupported strategy fails clearly instead of planning zero cells or falling back |
| `src/trading_agent/backtest/campaign_execution.py` — the `cell_ledger` write | one row per EVALUATION with the attempt count and the attempt outcomes |

**Still token-less by design (out of the validation-only scope, listed in the
payload):** `wfo_sensitivity.py`, `research/selection_policy.py`,
`calibration/execution.py`, `calibration/packet.py`, `execution/live_admission.py`.
They must fail clearly for this candidate; they are not a fallback path.

## 4. The receipts

- `data/bnb_di_adx_stateedge_20261020_01/acceptance_receipt.json` — sha256 `f19a0cbc5752f0dec0d9d8374d522e2f3ac27576530643c8e3f70fc753a22271`
- `data/bnb_di_adx_stateedge_20261020_01/pre_approval_payload.json` — sha256 `ccd809ed82d09f61fcdcc3a303e7d9a5c7ea212bcab7e5273d3cda83a393edf9`
- `data/bnb_di_adx_stateedge_20261020_01/source_identity_after.json` — sha256 `cd7494ec0adc37f7f7ae05bf92b28c35f858b9ba70a88135c3df193312fb988b`
- `data/bnb_di_adx_stateedge_20261020_01/source_identity_before.json` — sha256 `cd7494ec0adc37f7f7ae05bf92b28c35f858b9ba70a88135c3df193312fb988b`
- `data/bnb_di_adx_stateedge_prereg_20261020_01/manifest.json` — sha256 `b4119e93f7cb3ee3d5a7abde7c32f9aaed91adcfb0f7b51bb2629cb34c498a81`
- `data/bnb_di_adx_stateedge_prereg_20261020_01/pre_approval_payload.json` — sha256 `ccd809ed82d09f61fcdcc3a303e7d9a5c7ea212bcab7e5273d3cda83a393edf9`
- `data/bnb_di_adx_stateedge_prereg_20261020_01/preregistration_payload.json` — sha256 `ccd809ed82d09f61fcdcc3a303e7d9a5c7ea212bcab7e5273d3cda83a393edf9`

The three evidence manifests are named above by path; their hashes are
NOT repeated here, because a manifest cannot contain the hash of a
document that quotes the manifest's own hash. Take the identities
from the manifests themselves.
- `../bnb_native_adx25_validation_run_20261019_01/execution_finished.json` — sha256 `8993c88dd97db7a722857ab151f47e37b6a5aae2157a51524f59090a12326484`
- `../bnb_native_adx25_validation_run_20261019_01/activation_scope.json` — sha256 `12ab9b7414f2e675076904ec107396f470f48cd6a7c4e8e5e2436eebadf127a5`

## 5. What the author-run check round does and does not establish

The check round is the author's own script, assertions and runner. Its PASS
is a self-verification. The receipts to read are listed above; the verdict
artifact itself carries the provenance block and the list of what it does
not establish.

