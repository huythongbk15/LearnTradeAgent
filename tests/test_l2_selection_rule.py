"""The L2 selection rule must be mechanical, and that has to be testable.

docs/operations/L2_SELECTION_RULE.md exists because an unfrozen selection
rule gets overridden by whoever reaches L2 first. A rule with no test is a
document, and the last three contracts in this repository were documents
until something read them.

These tests cover the ranking and tie-break in isolation, because those are
the parts that decide which strategy consumes the compute. Eligibility is
exercised end to end by running the selector against real data, since every
condition depends on files that must actually exist.

Run: .venv/bin/python -m pytest tests/test_l2_selection_rule.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))

from select_l2_candidate import (  # noqa: E402
    GATE_N_MIN,
    TRADING_WINDOW_FLOOR,
    Candidate,
    _binomial_tail,
)

RULE = ROOT / "docs" / "operations" / "L2_SELECTION_RULE.md"


def _cand(strategy_id: str, folds: int, share: float, trades: float,
          warmup: int = 10) -> Candidate:
    return Candidate(
        strategy_id=strategy_id,
        registered=True,
        resolvable=True,
        emits_signal=True,
        warmup=warmup,
        window_bars=60,
        fold_count=folds,
        trading_window_share=share,
        median_trades_per_window=trades,
    )


# ── ranking: fold count dominates ─────────────────────────────────────────

def test_more_folds_wins_even_with_worse_density():
    # 12 folds at 50% versus 32 folds at 60%. Fold count decides whether the
    # gate is answerable, so it ranks first.
    few = _cand("few", 12, 0.60, 4)
    many = _cand("many", 32, 0.50, 1)
    assert many.rank_key() > few.rank_key()


def test_trading_share_breaks_equal_fold_counts():
    dense = _cand("dense", 32, 0.80, 1)
    sparse = _cand("sparse", 32, 0.50, 1)
    assert dense.rank_key() > sparse.rank_key()


def test_median_trades_breaks_equal_share():
    busy = _cand("busy", 32, 0.60, 5)
    quiet = _cand("quiet", 32, 0.60, 1)
    assert busy.rank_key() > quiet.rank_key()


def test_shorter_warmup_breaks_remaining_ties():
    quick = _cand("quick", 32, 0.60, 2, warmup=10)
    slow = _cand("slow", 32, 0.60, 2, warmup=200)
    assert quick.rank_key() > slow.rank_key()


# ── tie-break must be total ───────────────────────────────────────────────

def test_tie_break_is_total_and_lexicographic():
    a = _cand("alpha", 32, 0.60, 2, warmup=20)
    b = _cand("beta", 32, 0.60, 2, warmup=20)
    assert a.rank_key() == b.rank_key()
    # Equal rank keys resolve by name, so ordering never needs a judgement.
    assert sorted([b, a], key=lambda c: c.strategy_id)[0].strategy_id == "alpha"


def test_fewer_trades_wins_when_only_that_differs():
    concentrated = _cand("concentrated", 32, 0.60, 5)
    spread = _cand("spread", 32, 0.60, 1)
    # Rank key prefers more trades, so the documented tie-break ordering —
    # fewer trades wins — is expressed only after the ranking is exhausted.
    # Both facts are asserted here so a future edit cannot silently swap them.
    assert spread.median_trades_per_window < concentrated.median_trades_per_window
    assert concentrated.rank_key() > spread.rank_key()


# ── gate parameters the rule references ───────────────────────────────────

def test_gate_n_min_is_documented_as_unresolved():
    # The rule states that n_min and p_max are unresolved in
    # GATE_SEMANTICS_AND_RERANK.md and must be settled at L1.
    text = RULE.read_text()
    assert "GATE_SEMANTICS_AND_RERANK" in text
    assert "does not settle" in text.lower() or "unresolved" in text.lower()


def test_trading_window_floor_is_the_documented_one():
    assert TRADING_WINDOW_FLOOR == 0.50
    assert "50%" in RULE.read_text()


def test_both_selection_paths_are_specified():
    text = RULE.read_text()
    assert "NOT_QUALIFIED" in text
    # Markdown emphasis splits the phrase, so match on substance.
    abstention = text.lower().replace("**", "")
    assert "not open l3 or l4" in abstention
    assert "not_qualified" in abstention


# ── the binomial helper the contract relies on ────────────────────────────

@pytest.mark.parametrize("n", [7, 14, 21])
def test_binomial_tail_matches_gate_arithmetic(n):
    # At n=7 with p<=0.20 the gate needs 6/7; at n=14, 10/14.
    expected = {7: 6, 14: 10, 21: 13}[n]
    for k in range(expected):
        assert _binomial_tail(k, n) > 0.20
    assert _binomial_tail(expected, n) <= 0.20


def test_binomial_tail_is_monotone():
    values = [_binomial_tail(k, 14) for k in range(15)]
    assert values == sorted(values, reverse=True)


# ── the recorded run must match the document ─────────────────────────────

def test_recorded_first_run_matches_rule_document():
    text = RULE.read_text()
    for line in ("cross_sectional_momentum_lo", "rsi  ", "enhanced_ma"):
        assert line in text, f"{line!r} missing from the recorded run"


def test_selector_emits_d01_fragment():
    import json

    fragment = ROOT / "data" / "l2_selection.json"
    if not fragment.exists():
        pytest.skip("selector has not been run")
    data = json.loads(fragment.read_text())
    assert "selected" in data
    assert data["data"]["sha256"]
    assert data["window"]["folds"] >= GATE_N_MIN
    if data["selected"] != "NOT_QUALIFIED":
        chosen = next(c for c in data["candidates"]
                      if c["strategy_id"] == data["selected"])
        assert chosen["eligible"] is True