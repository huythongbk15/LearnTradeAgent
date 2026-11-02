"""The DRAFT policy of a Monday through the real artifact and registry classes, in a temporary folder. It can never become VALIDATED here."""
from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from trading_agent.research.selection_policy import PolicyStatus, SelectionPolicyArtifact, SelectionPolicyRegistry

from tools.selection import config, policy

AS_OF = "2026-11-02"
SHA = "a" * 64
STAMP = datetime(2026, 11, 2, tzinfo=UTC)


def decision(**change) -> dict:
    books = {"0": None, "1": {"since": AS_OF, "slots": ["B_ma1d_LS", "dhi30_3d", "cash"], "best_so_far": ["alt_btc", "cash", "cash"]}, "2": None, "3": None}
    return {"as_of": AS_OF, "due_book": 1, "selected": True, "reasons": ["NO_KEPT_SELECTOR"], "books": books, **change}


def test_a_draft_is_the_portfolio_with_no_trade_as_its_fallback() -> None:
    p = policy.draft(decision(), SHA)
    assert (p.symbol, p.timeframe, p.regime, p.fallback) == ("PORTFOLIO", "4h", "ALL", "NO_TRADE")
    assert p.status is PolicyStatus.DRAFT and p.promotion_stage == "exploratory" and p.evidence_ids == () and dict(p.scores) == {}
    assert p.incumbent.strategy_id == "selector_4w_90d_top3" and p.incumbent.params["paper_only"] is True
    assert p.incumbent.params["books"]["1"]["slots"] == ["B_ma1d_LS", "dhi30_3d", "cash"]


def test_a_draft_runs_eight_days_from_the_stamp_and_reads_no_wall_clock() -> None:
    p = policy.draft(decision(), SHA)
    assert p.validity_start == p.created_at == STAMP and p.validity_end == STAMP + timedelta(days=8)


def test_a_draft_is_bound_to_its_decision() -> None:
    a = policy.draft(decision(), SHA)
    assert a.policy_data_manifest_sha == SHA and a.policy_id != policy.draft(decision(), "b" * 64).policy_id
    assert a.policy_id != policy.draft(decision(selected=False), SHA).policy_id


def test_equal_inputs_write_an_equal_draft() -> None:
    assert policy.draft(decision(), SHA).to_json() == policy.draft(decision(), SHA).to_json()


def test_a_draft_round_trips_through_the_registry(tmp_path) -> None:
    p = policy.draft(decision(), SHA)
    pid = policy.publish(tmp_path, p)
    assert pid == p.policy_id and sorted(x.name for x in config.registry_dir(tmp_path).iterdir()) == sorted([f"{pid}.json", "index.json"])
    got = SelectionPolicyRegistry(config.registry_dir(tmp_path)).get(pid)
    assert got.verify_integrity() and got.to_json() == p.to_json()
    assert SelectionPolicyArtifact.from_json(p.to_json()).policy_id == pid


def test_adding_the_same_draft_again_changes_nothing(tmp_path) -> None:
    p = policy.draft(decision(), SHA)
    policy.publish(tmp_path, p)
    before = {x.name: x.read_bytes() for x in config.registry_dir(tmp_path).iterdir()}
    policy.publish(tmp_path, p)
    assert {x.name: x.read_bytes() for x in config.registry_dir(tmp_path).iterdir()} == before


def test_a_draft_is_never_the_active_policy(tmp_path) -> None:
    policy.publish(tmp_path, policy.draft(decision(), SHA))
    reg = SelectionPolicyRegistry(config.registry_dir(tmp_path))
    assert reg.get_active("PORTFOLIO", "4h", "ALL", now=STAMP + timedelta(hours=1)) is None


def test_validated_is_refused_without_evidence() -> None:
    p = policy.draft(decision(), SHA)
    with pytest.raises(ValueError, match="evidence_ids"):
        replace(p, status=PolicyStatus.VALIDATED)


def test_validated_is_refused_even_with_an_evidence_id_and_invented_scores() -> None:
    p = policy.draft(decision(), SHA)
    scores = {"selection_score": 1.0, "median_oos_return_pct": 1.0, "median_oos_trades": 30.0, "n_passing_folds": 4.0, "total_folds": 4.0}
    with pytest.raises(ValueError, match="allowlist"):
        replace(p, status=PolicyStatus.VALIDATED, evidence_ids=("e",), scores=scores)


def test_a_draft_cannot_be_activated() -> None:
    with pytest.raises(ValueError, match="validated"):
        policy.draft(decision(), SHA).activate("operator", "T-1")


def test_a_draft_is_plain_json_with_no_nan() -> None:
    assert json.loads(policy.draft(decision(), SHA).to_json())["status"] == "draft"
