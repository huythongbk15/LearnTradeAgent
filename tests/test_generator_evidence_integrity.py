"""No generator may write a promotable policy it did not measure.

The measured-evidence gate added in 19ec13c refuses a policy whose scores
lack the OOS metric family, and _require_attributable_code refuses one
whose code_sha matches no registered source. Four generator scripts were
fixed for violating both. Two more were found a day later by grepping for
`SelectionPolicyArtifact(` rather than for the stores the audit had
enumerated — `tournament_paper_trader.py` wrote the exact shape
POLICY_RETURN_AUDIT.md describes and its memory notes record it running in
live paper-trading mode.

Enumerating the damage fixed sixteen symptoms. This test is the
detection: every script that builds a policy artifact and marks it
VALIDATED or ACTIVE must supply the metric family, so the next one is
caught by a test rather than by an audit.

Scripts that build DRAFT policies are excluded — the gate does not apply
to them, and a bootstrap that measures nothing is a legitimate way to wire
a registry.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

# Metrics the promotion gate reads. A promotable policy needs all of them.
REQUIRED_METRICS = {
    "median_oos_return_pct",
    "median_oos_trades",
    "n_passing_folds",
    "total_folds",
}

PROMOTABLE_STATUSES = {"VALIDATED", "ACTIVE"}


def _producers() -> list[Path]:
    """Every file that constructs a SelectionPolicyArtifact.

    tests/ is included deliberately. The router test fixture used
    code_sha="e" * 64 with strategy ids that were never on the allowlist,
    so the promotion gate raised inside the fixture and 12 router tests
    were reported as passing-by-collection while never exercising the
    router at all. They had been failing since 8870e03 without anyone
    running the suite after that change.
    """
    out = []
    for base in (ROOT / "scripts", ROOT / "src", ROOT / "tests"):
        pattern = "test_generator_evidence_integrity.py"
        for f in base.rglob("*.py"):
            if "__pycache__" in f.parts or f.name == pattern:
                continue
            try:
                tree = ast.parse(f.read_text())
            except (SyntaxError, UnicodeDecodeError):
                continue
            if any(
                isinstance(n, ast.Call)
                and getattr(n.func, "id", None) == "SelectionPolicyArtifact"
                for n in ast.walk(tree)
            ):
                out.append(f.relative_to(ROOT))
    return out


def _scores_shape(call: ast.Call) -> tuple[str, set[str]]:
    """Classify the scores argument.

    Returns (kind, keys) where kind is:

      ``literal``  a dict written inline — its keys can be checked here
      ``computed``  a name or attribute holding a dict built elsewhere
                   (selection_policy.py's builder, strategy_tournament's
                   activator). These cannot be verified by parsing, so they
                   are skipped rather than reported: flagging them would be a
                   false positive on the two places that legitimately derive
                   scores.
      ``absent``    no scores keyword
    """
    for kw in call.keywords:
        if kw.arg != "scores":
            continue
        if not isinstance(kw.value, ast.Dict):
            return "computed", set()
        return "literal", {
            k.value
            for k in kw.value.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
    return "absent", set()


PRODUCERS = _producers()


def test_producers_were_found():
    # A zero-result scan would make every check below pass vacuously.
    assert len(PRODUCERS) >= 8, f"only found {len(PRODUCERS)}: {PRODUCERS}"


@pytest.mark.parametrize("path", PRODUCERS, ids=lambda p: str(p))
def test_promotable_policy_carries_measured_metrics(path: Path):
    """A VALIDATED/ACTIVE artifact must state the metrics it was scored on."""
    tree = ast.parse((ROOT / path).read_text())
    problems: list[str] = []

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "SelectionPolicyArtifact"):
            continue
        status = None
        for kw in node.keywords:
            if kw.arg == "status" and isinstance(kw.value, ast.Attribute):
                status = kw.value.attr
        if status not in PROMOTABLE_STATUSES:
            continue

        kind, keys = _scores_shape(node)
        if kind == "computed":
            continue  # derived elsewhere; see _scores_shape docstring
        if kind == "absent":
            problems.append(
                f"line {node.lineno}: promotable policy with no scores "
                f"argument — pass measured scores or use DRAFT"
            )
            continue
        missing = REQUIRED_METRICS - keys
        if missing:
            problems.append(
                f"line {node.lineno}: promotable policy missing "
                f"{sorted(missing)}"
            )

    assert not problems, (
        f"{path} builds a promotable policy without measured metrics:\n  "
        + "\n  ".join(problems)
    )


@pytest.mark.parametrize("path", PRODUCERS, ids=lambda p: str(p))
def test_no_literal_placeholder_code_sha(path: Path):
    """Promotable policies must not carry a repeated-character placeholder.

    The gate checks code_sha against the canonical registry at runtime, so a
    placeholder is caught then — but only if the script is actually run.
    These two scripts were missed for a day because the audit enumerated
    stores rather than producers.
    """
    src = (ROOT / path).read_text()
    tree = ast.parse(src)
    problems: list[str] = []

    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "SelectionPolicyArtifact"):
            continue
        status = None
        for kw in node.keywords:
            if kw.arg == "status" and isinstance(kw.value, ast.Attribute):
                status = kw.value.attr
        if status not in PROMOTABLE_STATUSES:
            continue
        for kw in node.keywords:
            if kw.arg != "incumbent":
                continue
            for sub in ast.walk(kw.value):
                if (isinstance(sub, ast.Call)
                        and getattr(sub.func, "name", None) == "code_sha"):
                    if isinstance(sub.value, ast.BinOp):
                        problems.append(
                            f"line {sub.lineno}: code_sha is a repeated-"
                            f"character literal"
                        )

    assert not problems, (
        f"{path} attaches a placeholder code_sha to a promotable policy:\n  "
        + "\n  ".join(problems)
    )