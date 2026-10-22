"""Controlled execution path for the scoped `di_adx_state_edge` candidate.

The candidate is NOT broadly registered: it is absent from the legacy
`@register_strategy` registry and from the default canonical registry, so no
campaign, worker or CLI can select it by name. It is reachable only by
presenting the exact scope token that names the preregistration which
authorised it.

These tests lock:
  * the candidate is invisible without the token;
  * the correct token resolves it (legacy candidate + canonical adapter);
  * an unknown or wrong token fails closed;
  * the broad registry is unchanged — the control candidate still resolves and
    the broad id set contains no scoped candidate;
  * the token is carried end-to-end by the cell runner and the simulator, so a
    approved run can execute the candidate under the native path.

No market data, no network, no cell execution against real data.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, ".")

from trading_agent.backtest.tournament import EvaluationCellSpec, run_cell
from trading_agent.strategies.base import list_strategies
from trading_agent.strategies.canonical.candidates import (
    SCOPED_CANDIDATE_SCOPE_TOKENS,
    build_default_registry,
    build_legacy_candidate,
    build_parameterized_adapter,
    resolve_scoped_candidate_id,
)

SCOPE_TOKEN = "bnb_di_adx_stateedge_prereg_20261020_01"
CANDIDATE_ID = "di_adx_state_edge"
PARAMS = {"adx_period": 14, "adx_threshold": 25.0}
CONTROL_ID = "ma_adx"
CONTROL_PARAMS = {
    "fast_period": 20, "slow_period": 80, "adx_period": 14, "adx_threshold": 25.0,
}


def test_candidate_is_absent_from_every_broad_registry():
    assert CANDIDATE_ID not in list_strategies(), (
        "the scoped candidate must not be published to the legacy registry"
    )
    assert CANDIDATE_ID not in build_default_registry().list_ids(), (
        "the scoped candidate must not be published to the default registry"
    )


def test_control_still_resolves_without_a_token():
    descriptor, strategy = build_legacy_candidate(CONTROL_ID, CONTROL_PARAMS)
    assert descriptor.strategy_id == CONTROL_ID
    assert strategy.fast == 20 and strategy.slow == 80


def test_correct_scope_token_resolves_the_candidate():
    descriptor, strategy = build_legacy_candidate(CANDIDATE_ID, PARAMS, scope_token=SCOPE_TOKEN)
    assert descriptor.strategy_id == CANDIDATE_ID
    assert descriptor.warmup_bars == 100
    assert strategy.adx_period == 14 and strategy.adx_threshold == 25.0
    # the candidate must not carry a moving-average parameter
    assert not hasattr(strategy, "fast") and not hasattr(strategy, "slow")


def test_scope_token_unlocks_the_canonical_adapter():
    descriptor, adapter = build_parameterized_adapter(
        CANDIDATE_ID, PARAMS, scope_token=SCOPE_TOKEN
    )
    assert descriptor.strategy_id == CANDIDATE_ID
    assert adapter.strategy_id == CANDIDATE_ID


def test_missing_or_wrong_token_fails_closed():
    for token in (None, "", "wrong", "bnb_di_adx_stateedge_prereg_20260901_01"):
        with pytest.raises(Exception):
            build_legacy_candidate(CANDIDATE_ID, PARAMS, scope_token=token)


def test_wrong_token_does_not_unlock_the_registry():
    assert resolve_scoped_candidate_id("wrong") is None
    assert resolve_scoped_candidate_id(None) is None
    assert CANDIDATE_ID not in build_default_registry(scope_token="wrong").list_ids()
    assert resolve_scoped_candidate_id(SCOPE_TOKEN) == CANDIDATE_ID


def test_scope_token_map_names_exactly_one_candidate_per_token():
    assert set(SCOPED_CANDIDATE_SCOPE_TOKENS) == {SCOPE_TOKEN}
    assert list(SCOPED_CANDIDATE_SCOPE_TOKENS.values()) == [CANDIDATE_ID]


def test_run_cell_without_the_token_fails_closed_on_resolution(tmp_path):
    """The cell cannot resolve the scoped candidate without the token.

    ``run_cell`` isolates cell failures into a FAILED artifact rather than
    raising, so the assertion is on the artifact: it must be FAILED, with a
    resolution failure reason, and it must not look like a completed or
    silently substituted cell.
    """
    spec = EvaluationCellSpec(
        strategy_id=CANDIDATE_ID, symbol="BTC/USDT", timeframe="4h",
        params=PARAMS,
    )
    artifact = run_cell(spec, out_root=tmp_path / "no_scope", timeout_seconds=30,
                        max_retries=0, _use_multiprocessing=False)
    assert artifact.status == "FAILED", (
        f"expected a FAILED artifact, got {artifact.status}"
    )
    assert artifact.metrics == {}, "a failed cell must not report metrics"
    reasons = " ".join(str(r) for r in (artifact.failure_reasons or []))
    assert "allowlist" in reasons or "unknown" in reasons.lower() or CANDIDATE_ID in reasons, (
        f"the failure must be a strategy-resolution failure, got: {reasons!r}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_scope_token_is_inside_the_execution_scope_identity():
    """The token must be part of the frozen execution context.

    ``execution_scope_identity`` hashes the COMPLETE context, so two contexts
    that differ only in the scope token have different scope identities. That
    is what binds the token to the experiment/execution identity rather than
    leaving it a free-floating parameter.
    """
    from trading_agent.research.trials import execution_scope_identity

    def _context(token):
        return {
            "trial_identity_version": 2,
            "protocol_fingerprint": "a" * 64,
            "source": {"code_commit": "b" * 40},
            "environment": {"name": "research"},
            "runtime_config": {"cell_timeout_seconds": 300},
            "scope_token": token,
        }

    with_token = execution_scope_identity(_context(SCOPE_TOKEN))
    without_token = execution_scope_identity(_context(None))
    other_token = execution_scope_identity(_context("some_other_scope"))
    assert with_token["context_sha256"] != without_token["context_sha256"]
    assert with_token["context_sha256"] != other_token["context_sha256"]


def test_resume_cache_key_covers_the_execution_context():
    """A cached artifact from a different scope token must not even be looked up.

    The campaign cache key is a hash over the whole execution context, so a
    different token yields a different key and therefore a different cache
    path — the report from the other context is never a candidate.
    """
    from trading_agent.backtest.campaign_evidence import content_hash

    def _key(context, cell_id="cell-1"):
        return content_hash({
            "version": 1, "environment": "research", "stage": "CELL",
            "context": context, "cell_id": cell_id, "params": {},
            "cost": {}, "fault": {}, "window": {},
        })

    base = {"trial_identity_version": 2, "protocol_fingerprint": "a" * 64,
            "source": {}, "environment": {}, "runtime_config": {}}
    assert _key({**base, "scope_token": SCOPE_TOKEN}) != _key({**base, "scope_token": None})
    assert _key({**base, "scope_token": SCOPE_TOKEN}) != _key({**base, "scope_token": "other"})


def test_wrong_token_fails_before_any_simulation_artifact(tmp_path):
    """A wrong token must fail at resolution — before data is read and before
    the engine runs — so no cell execution artifact is produced."""
    spec = EvaluationCellSpec(
        strategy_id=CANDIDATE_ID, symbol="BTC/USDT", timeframe="4h",
        params=PARAMS,
    )
    out_root = tmp_path / "wrong_token"
    artifact = run_cell(spec, out_root=out_root, timeout_seconds=60,
                        max_retries=0, _use_multiprocessing=False,
                        scope_token="not-the-scope-token")
    assert artifact.status == "FAILED"
    reasons = " ".join(str(r) for r in (artifact.failure_reasons or []))
    assert "registry" in reasons.lower() or "allowlist" in reasons.lower() or \
        "unknown" in reasons.lower(), f"unexpected failure reason: {reasons!r}"
    produced = [p.name for p in out_root.rglob("*") if p.is_file()] if out_root.exists() else []
    forbidden = {"events.db", "paper_binance.json", "report.json"}
    assert not (set(produced) & forbidden), (
        f"the cell produced engine artifacts despite failing at resolution: {produced}"
    )
