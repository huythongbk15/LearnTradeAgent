"""Wave E — supply-chain provenance gate (spec §23).

Tests scripts/verify_provenance.py with an injectable runner so the 7
rejection criteria can be exercised without docker/cosign/gh installed locally.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.verify_provenance import (
    GITHUB_WORKFLOW_BUILD_TYPE,
    SLSA_PREDICATE_TYPE,
    SPDX_PREDICATE_TYPE,
    verify_provenance,
)

REPO = "huythongbk15/LearnTradeAgent"
COMMIT = "a" * 40
IMAGE = f"ghcr.io/{REPO.lower()}@sha256:{'b' * 64}"
ROOT = Path(__file__).resolve().parents[1]
ATTEST_ACTION = "actions/attest@1e69f48acb82d1966a394da916b4c1698aa569d6"
COSIGN_INSTALLER = "sigstore/cosign-installer@6f9f17788090df1f26f669e9d70d6ae9567deba6"


class FakeRunner:
    """Deterministic fake subprocess runner for supply-chain commands."""

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
        if prog == "gh" and cmd[1:3] == ["attestation", "verify"]:
            predicate_type = cmd[cmd.index("--predicate-type") + 1]
            return self.behaviors.get(f"attest:{predicate_type}", _ok("attestation OK"))
        raise AssertionError(f"unexpected command: {cmd}")

    def _docker(self, cmd):
        labels = self.behaviors.get(
            "labels",
            {
                "org.opencontainers.image.source": f"https://github.com/{REPO}",
                "org.opencontainers.image.revision": COMMIT,
            },
        )
        out = "{}" if labels is None else json.dumps(labels)
        return subprocess.CompletedProcess(cmd, 0, out, "")


def _ok(out: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["cosign"], 0, out, "")


def _fail(err: str) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(["cosign"], 1, "", err)


def attestation_output(
    *, predicate_type: str, predicate: dict, image: str = IMAGE
) -> str:
    name, digest = image.rsplit("@sha256:", 1)
    statement = {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [{"name": name, "digest": {"sha256": digest}}],
        "predicateType": predicate_type,
        "predicate": predicate,
    }
    return json.dumps(
        [
            {
                "verificationResult": {
                    "statement": statement,
                    "signature": {"certificate": {"issuer": "github-actions"}},
                    "verifiedTimestamps": [{"type": "Tlog"}],
                }
            }
        ]
    )


def slsa_payload(
    *,
    repo: str = REPO,
    commit: str = COMMIT,
    workflow: str = ".github/workflows/ci.yml",
    ref: str = "refs/heads/master",
    image: str = IMAGE,
) -> str:
    predicate = {
        "buildDefinition": {
            "buildType": GITHUB_WORKFLOW_BUILD_TYPE,
            "externalParameters": {
                "workflow": {
                    "repository": f"https://github.com/{repo}",
                    "ref": ref,
                    "path": workflow,
                }
            },
            "internalParameters": {"github": {"runner_environment": "github-hosted"}},
            "resolvedDependencies": [
                {
                    "uri": f"git+https://github.com/{repo}@{ref}",
                    "digest": {"gitCommit": commit},
                }
            ],
        },
        "runDetails": {
            "builder": {"id": f"https://github.com/{repo}/{workflow}@{ref}"},
            "metadata": {"invocationId": "https://github.com/example/actions/runs/1"},
        },
    }
    return attestation_output(
        predicate_type=SLSA_PREDICATE_TYPE,
        predicate=predicate,
        image=image,
    )


def sbom_payload(*, packages: list[dict] | None = None, image: str = IMAGE) -> str:
    return attestation_output(
        predicate_type=SPDX_PREDICATE_TYPE,
        predicate={
            "spdxVersion": "SPDX-2.3",
            "packages": packages
            if packages is not None
            else [{"name": "trading-agent"}],
        },
        image=image,
    )


def ok_behaviors(**overrides) -> dict:
    behaviors = {
        "verify": _ok(".github/workflows/ci.yml@refs/heads/master identity verified"),
        f"attest:{SPDX_PREDICATE_TYPE}": _ok(sbom_payload()),
        f"attest:{SLSA_PREDICATE_TYPE}": _ok(slsa_payload()),
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
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
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
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
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
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors(labels=labels)),
        )
        assert not report.all_ok
        assert report.checks["repository"] is False

    def test_invalid_signature_rejected(self):
        report = verify_provenance(
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors(verify=_fail("no matching signatures"))),
        )
        assert not report.all_ok
        assert report.checks["signature"] is False
        assert report.checks["workflow"] is False
        assert report.checks["branch"] is False

    def test_missing_sbom_rejected(self):
        report = verify_provenance(
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=FakeRunner(
                **ok_behaviors(
                    **{f"attest:{SPDX_PREDICATE_TYPE}": _fail("no attestation")}
                )
            ),
        )
        assert not report.all_ok
        assert report.checks["sbom"] is False

    def test_empty_sbom_rejected(self):
        report = verify_provenance(
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=FakeRunner(
                **ok_behaviors(
                    **{f"attest:{SPDX_PREDICATE_TYPE}": _ok(sbom_payload(packages=[]))}
                )
            ),
        )
        assert not report.all_ok
        assert report.checks["sbom"] is False

    def test_missing_provenance_rejected(self):
        report = verify_provenance(
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=FakeRunner(
                **ok_behaviors(
                    **{f"attest:{SLSA_PREDICATE_TYPE}": _fail("no attestation")}
                )
            ),
        )
        assert not report.all_ok
        assert report.checks["provenance"] is False

    def test_provenance_commit_mismatch_rejected(self):
        report = verify_provenance(
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=FakeRunner(
                **ok_behaviors(
                    **{
                        f"attest:{SLSA_PREDICATE_TYPE}": _ok(
                            slsa_payload(commit="c" * 40)
                        )
                    }
                )
            ),
        )
        assert not report.all_ok
        assert report.checks["provenance"] is False

    def test_provenance_wrong_workflow_rejected(self):
        report = verify_provenance(
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=FakeRunner(
                **ok_behaviors(
                    **{
                        f"attest:{SLSA_PREDICATE_TYPE}": _ok(
                            slsa_payload(workflow=".github/workflows/evil.yml")
                        )
                    }
                )
            ),
        )
        assert not report.all_ok
        assert report.checks["provenance"] is False

    def test_attestation_verifier_enforces_certificate_source_and_oci_bundle(self):
        runner = FakeRunner(**ok_behaviors())
        report = verify_provenance(
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=runner,
        )
        assert report.all_ok
        gh_calls = [call for call in runner.calls if call[0] == "gh"]
        assert len(gh_calls) == 2
        for call in gh_calls:
            assert call[3] == f"oci://{IMAGE}"
            assert "--bundle-from-oci" in call
            assert call[call.index("--signer-workflow") + 1] == (
                f"{REPO}/.github/workflows/ci.yml"
            )
            assert call[call.index("--source-ref") + 1] == "refs/heads/master"
            assert call[call.index("--source-digest") + 1] == COMMIT
            assert "--deny-self-hosted-runners" in call

    def test_summary_renders(self):
        report = verify_provenance(
            image=IMAGE,
            repo=REPO,
            commit=COMMIT,
            workflow=".github/workflows/ci.yml",
            ref="refs/heads/master",
            runner=FakeRunner(**ok_behaviors()),
        )
        summary = report.summary()
        assert "repository" in summary
        assert "PASS" in summary
        assert "FAIL" not in summary


class TestSupplyChainWorkflowWiring:
    def workflow(self, name: str) -> str:
        return (ROOT / ".github" / "workflows" / name).read_text()

    def test_ci_uses_pinned_github_attest_for_slsa_and_sbom(self):
        ci = self.workflow("ci.yml")
        assert ci.count(f"uses: {ATTEST_ACTION}") == 2
        assert ci.count("create-storage-record: false") == 2
        assert "attestations: write" in ci
        assert "sbom-path: sbom.spdx.json" in ci
        assert "cosign attest" not in ci

    def test_every_cosign_job_uses_the_same_pinned_version(self):
        for name in (
            "ci.yml",
            "provenance-gate.yml",
            "cd-staging.yml",
            "cd-production.yml",
        ):
            workflow = self.workflow(name)
            assert f"uses: {COSIGN_INSTALLER}" in workflow
            assert "cosign-release: v3.1.3" in workflow

    def test_staging_waits_for_gate_and_repeats_full_verification(self):
        staging = self.workflow("cd-staging.yml")
        assert 'workflows: ["Provenance Gate"]' in staging
        assert "scripts/verify_provenance.py" in staging
        assert "name: provenance-verdict" in staging
        assert "run-id: ${{ github.event.workflow_run.id }}" in staging
        assert "ref: ${{ steps.verdict.outputs.head_sha }}" in staging
        assert "${{ steps.verdict.outputs.image }}" in staging
        assert "verify-attestation" not in staging

    def test_all_deploy_gates_use_the_shared_verifier(self):
        for name in (
            "provenance-gate.yml",
            "cd-staging.yml",
            "cd-production.yml",
        ):
            workflow = self.workflow(name)
            assert "scripts/verify_provenance.py" in workflow
            assert "attestations: read" in workflow
            assert "verify-attestation" not in workflow
