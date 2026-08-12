"""Wave E — supply-chain provenance gate (spec §23).

Tests scripts/verify_provenance.py with an injectable runner so the 7
rejection criteria can be exercised without docker/cosign installed locally.
"""

from __future__ import annotations

import base64
import json
import subprocess

from scripts.verify_provenance import verify_provenance

REPO = "huythongbk15/LearnTradeAgent"
COMMIT = "a" * 40
IMAGE = f"ghcr.io/{REPO.lower()}@sha256:{'b' * 64}"


class FakeRunner:
    """Deterministic fake subprocess runner for cosign/docker commands."""

    def __init__(self, **behaviors):
        self.behaviors = behaviors
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(list(cmd))
        prog = cmd[0]
        if prog == "docker":
            return self._docker(cmd)
        if prog == "cosign":
            if cmd[1] == "verify":
                return self.behaviors.get("verify", _ok("identity OK"))
            if cmd[1] == "verify-attestation":
                type_ = cmd[cmd.index("--type") + 1]
                return self.behaviors.get(f"attest:{type_}", _ok("attestation OK"))
        raise AssertionError(f"unexpected command: {cmd}")

    def _docker(self, cmd):
        labels = self.behaviors.get(
            "labels",
            {"org.opencontainers.image.source": f"https://github.com/{REPO}",
             "org.opencontainers.image.revision": COMMIT},
        )
        out = "{}" if labels is None else json.dumps(labels)
        return subprocess.CompletedProcess(cmd, 0, out, "")


def _ok(out: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["cosign"], 0, out, "")


def _fail(err: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["cosign"], 1, "", err)


def slsa_payload(*, repo: str = REPO, commit: str = COMMIT) -> str:
    statement = {
        "_type": "https://in-toto.io/Statement/v0.1",
        "subject": [
            {"name": f"ghcr.io/{repo.lower()}", "digest": {"sha256": commit}},
        ],
        "predicateType": "https://slsa.dev/provenance/v0.2",
        "predicate": {
            "builder": {"id": f"https://github.com/{repo}/.github/workflows/ci.yml@refs/heads/master"},
            "metadata": {"completeness": {"sha256": commit}},
            "invocation": {},
        },
    }
    payload = base64.b64encode(json.dumps(statement).encode()).decode()
    return json.dumps([{"payload": payload}])


def ok_behaviors(**overrides) -> dict:
    behaviors = {
        "verify": _ok(".github/workflows/ci.yml@refs/heads/master identity verified"),
        "attest:spdxjson": _ok("spdx attestation"),
        "attest:slsaprovenance": _ok(slsa_payload()),
        "labels": {
            "org.opencontainers.image.source": f"https://github.com/{REPO}",
            "org.opencontainers.image.revision": COMMIT,
        },
    }
    behaviors.update(overrides)
    return behaviors


class TestVerifyProvenance:
    def test_all_criteria_pass(self):
        report = verify_provenance(
            image=IMAGE, repo=REPO, commit=COMMIT,
            workflow=".github/workflows/ci.yml", ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors()),
        )
        assert report.all_ok
        assert all(report.checks.values())

    def test_wrong_commit_rejected(self):
        labels = {
            "org.opencontainers.image.source": f"https://github.com/{REPO}",
            "org.opencontainers.image.revision": "f" * 40,
        }
        report = verify_provenance(
            image=IMAGE, repo=REPO, commit=COMMIT,
            workflow=".github/workflows/ci.yml", ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors(labels=labels)),
        )
        assert not report.all_ok
        assert report.checks["commit"] is False

    def test_wrong_repository_rejected(self):
        labels = {
            "org.opencontainers.image.source": "https://github.com/evil/repo",
            "org.opencontainers.image.revision": COMMIT,
        }
        report = verify_provenance(
            image=IMAGE, repo=REPO, commit=COMMIT,
            workflow=".github/workflows/ci.yml", ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors(labels=labels)),
        )
        assert not report.all_ok
        assert report.checks["repository"] is False

    def test_invalid_signature_rejected(self):
        report = verify_provenance(
            image=IMAGE, repo=REPO, commit=COMMIT,
            workflow=".github/workflows/ci.yml", ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors(verify=_fail("no matching signatures"))),
        )
        assert not report.all_ok
        assert report.checks["signature"] is False
        assert report.checks["workflow"] is False
        assert report.checks["branch"] is False

    def test_missing_sbom_rejected(self):
        report = verify_provenance(
            image=IMAGE, repo=REPO, commit=COMMIT,
            workflow=".github/workflows/ci.yml", ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors(**{"attest:spdxjson": _fail("no attestation")})),
        )
        assert not report.all_ok
        assert report.checks["sbom"] is False

    def test_missing_provenance_rejected(self):
        report = verify_provenance(
            image=IMAGE, repo=REPO, commit=COMMIT,
            workflow=".github/workflows/ci.yml", ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors(**{"attest:slsaprovenance": _fail("no attestation")})),
        )
        assert not report.all_ok
        assert report.checks["provenance"] is False

    def test_provenance_subject_mismatch_rejected(self):
        report = verify_provenance(
            image=IMAGE, repo=REPO, commit=COMMIT,
            workflow=".github/workflows/ci.yml", ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors(**{"attest:slsaprovenance": _ok(slsa_payload(commit="c" * 40))})),
        )
        assert not report.all_ok
        assert report.checks["provenance"] is False

    def test_summary_renders(self):
        report = verify_provenance(
            image=IMAGE, repo=REPO, commit=COMMIT,
            workflow=".github/workflows/ci.yml", ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors()),
        )
        summary = report.summary()
        assert "repository" in summary
        assert "PASS" in summary
        assert "FAIL" not in summary