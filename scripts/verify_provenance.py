#!/usr/bin/env python3
"""Verify CD provenance before deploy (Wave E — supply-chain provenance).

Enforces every rejection criterion from the spec §23.  The CD pipeline must
reject an artifact when ANY of the following is wrong:

  1. wrong repository   (image source label / expected repo)
  2. wrong commit       (image revision label / expected commit sha)
  3. wrong workflow     (signature certificate identity / expected workflow)
  4. wrong branch       (signature certificate identity / expected ref)
  5. missing SBOM       (GitHub SPDX attestation with packages)
  6. invalid signature  (cosign verify against keyless identity)
  7. missing provenance (GitHub SLSA v1 attestation with matching predicate)

Running an image through this gate is the *only* way a deploy should proceed.
Local usage (requires docker, cosign, gh, and registry authentication):

    python scripts/verify_provenance.py \
        --image ghcr.io/owner/repo@sha256:... \
        --repo owner/repo --commit <sha> --workflow .github/workflows/ci.yml \
        --ref refs/heads/master

Exit codes:
  0 = all criteria verified
  1 = at least one criterion failed (deploy must NOT proceed)
  2 = environment error (tool missing / cannot run)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Callable

# A runner is injectable for tests: ``runner(cmd, **kw) -> CompletedProcess``
Runner = Callable[..., subprocess.CompletedProcess]

SPDX_PREDICATE_TYPE = "https://spdx.dev/Document/v2.3"
SLSA_PREDICATE_TYPE = "https://slsa.dev/provenance/v1"
GITHUB_WORKFLOW_BUILD_TYPE = "https://actions.github.io/buildtypes/workflow/v1"


def real_runner(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


@dataclass
class ProvenanceReport:
    """Per-criterion result.  ``ok`` must be True for every criterion."""

    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)

    def set(self, name: str, ok: bool, detail: str = "") -> None:
        self.checks[name] = bool(ok)
        self.details[name] = detail

    @property
    def all_ok(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def summary(self) -> str:
        lines = [f"{'CRITERION':<28} {'STATUS':<8} DETAIL"]
        for name, ok in self.checks.items():
            lines.append(
                f"{name:<28} {'PASS' if ok else 'FAIL':<8} {self.details.get(name, '')}"
            )
        return "\n".join(lines)


def _docker_image_labels(image: str, runner: Runner) -> dict[str, str]:
    result = runner(
        ["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image]
    )
    if result.returncode != 0:
        raise RuntimeError(f"docker inspect failed: {result.stderr.strip()}")
    return json.loads(result.stdout.strip() or "{}")


def _cosign_verify(
    image: str, identity: str, issuer: str, runner: Runner
) -> subprocess.CompletedProcess:
    return runner(
        [
            "cosign",
            "verify",
            image,
            "--certificate-identity",
            identity,
            "--certificate-oidc-issuer",
            issuer,
        ],
        env=dict(os.environ),
    )


def _github_attestation_verify(
    *,
    image: str,
    repo: str,
    workflow: str,
    ref: str,
    commit: str,
    predicate_type: str,
    issuer: str,
    runner: Runner,
) -> subprocess.CompletedProcess:
    """Verify an actions/attest bundle with certificate-level source binding."""
    oci_image = image if image.startswith("oci://") else f"oci://{image}"
    signer_workflow = f"{repo}/{workflow.lstrip('/')}"
    return runner(
        [
            "gh",
            "attestation",
            "verify",
            oci_image,
            "--repo",
            repo,
            "--bundle-from-oci",
            "--signer-workflow",
            signer_workflow,
            "--source-ref",
            ref,
            "--source-digest",
            commit,
            "--cert-oidc-issuer",
            issuer,
            "--deny-self-hosted-runners",
            "--predicate-type",
            predicate_type,
            "--format",
            "json",
        ],
        env=dict(os.environ),
    )


def _result_detail(result: subprocess.CompletedProcess, limit: int = 240) -> str:
    detail = (result.stderr or result.stdout or "no verifier output").strip()
    return detail[:limit]


def _attestation_statements(
    result: subprocess.CompletedProcess,
) -> list[dict[str, Any]]:
    payload = json.loads(result.stdout)
    if not isinstance(payload, list) or not payload:
        raise ValueError("attestation verifier returned no results")

    statements: list[dict[str, Any]] = []
    for entry in payload:
        if not isinstance(entry, dict):
            continue
        verification = entry.get("verificationResult")
        if not isinstance(verification, dict):
            continue
        statement = verification.get("statement")
        if isinstance(statement, dict):
            statements.append(statement)
    if not statements:
        raise ValueError("verified attestation contains no statement")
    return statements


def _expected_image_subject(image: str) -> tuple[str, str]:
    normalized = image.removeprefix("oci://")
    marker = "@sha256:"
    if marker not in normalized:
        raise ValueError("image must be pinned by sha256 digest")
    name, digest = normalized.rsplit(marker, 1)
    if not name or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("image must contain a full sha256 digest")
    return name, digest


def _subject_matches(statement: dict[str, Any], image: str) -> bool:
    expected_name, expected_digest = _expected_image_subject(image)
    subjects = statement.get("subject")
    if not isinstance(subjects, list):
        return False
    return any(
        isinstance(subject, dict)
        and subject.get("name") == expected_name
        and isinstance(subject.get("digest"), dict)
        and subject["digest"].get("sha256") == expected_digest
        for subject in subjects
    )


def _validate_sbom_attestation(
    result: subprocess.CompletedProcess, image: str
) -> tuple[bool, str]:
    if result.returncode != 0:
        return False, f"GitHub SBOM verification failed: {_result_detail(result)}"
    try:
        statements = _attestation_statements(result)
        for statement in statements:
            if statement.get("predicateType") != SPDX_PREDICATE_TYPE:
                continue
            if not _subject_matches(statement, image):
                continue
            predicate = statement.get("predicate")
            if not isinstance(predicate, dict):
                continue
            packages = predicate.get("packages")
            if (
                predicate.get("spdxVersion") == "SPDX-2.3"
                and isinstance(packages, list)
                and packages
            ):
                return True, f"verified SPDX 2.3 SBOM with {len(packages)} packages"
        return False, "verified bundle has no matching non-empty SPDX 2.3 statement"
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return False, f"invalid GitHub SBOM verification output: {exc}"


def _validate_slsa_attestation(
    result: subprocess.CompletedProcess,
    *,
    image: str,
    repo: str,
    workflow: str,
    ref: str,
    commit: str,
) -> tuple[bool, str]:
    if result.returncode != 0:
        return False, f"GitHub provenance verification failed: {_result_detail(result)}"

    expected_repo = f"https://github.com/{repo}"
    expected_builder = f"{expected_repo}/{workflow.lstrip('/')}@{ref}"
    expected_source = f"git+{expected_repo}@{ref}"
    try:
        statements = _attestation_statements(result)
        last_detail = "no SLSA v1 statement matched the deployment policy"
        for statement in statements:
            subject_match = _subject_matches(statement, image)
            predicate_type_match = statement.get("predicateType") == SLSA_PREDICATE_TYPE
            predicate = statement.get("predicate")
            if not isinstance(predicate, dict):
                continue
            build_definition = predicate.get("buildDefinition")
            run_details = predicate.get("runDetails")
            if not isinstance(build_definition, dict) or not isinstance(
                run_details, dict
            ):
                continue

            build_type_match = (
                build_definition.get("buildType") == GITHUB_WORKFLOW_BUILD_TYPE
            )
            external = build_definition.get("externalParameters")
            external_fields_match = isinstance(external, dict) and set(external) == {
                "workflow"
            }
            workflow_data = (
                external.get("workflow") if isinstance(external, dict) else None
            )
            workflow_match = isinstance(workflow_data, dict) and workflow_data == {
                "repository": expected_repo,
                "ref": ref,
                "path": workflow,
            }

            dependencies = build_definition.get("resolvedDependencies")
            source_match = isinstance(dependencies, list) and any(
                isinstance(dependency, dict)
                and dependency.get("uri") == expected_source
                and isinstance(dependency.get("digest"), dict)
                and dependency["digest"].get("gitCommit") == commit
                for dependency in dependencies
            )
            builder = run_details.get("builder")
            builder_match = (
                isinstance(builder, dict) and builder.get("id") == expected_builder
            )

            matches = {
                "subject": subject_match,
                "predicate_type": predicate_type_match,
                "build_type": build_type_match,
                "external_fields": external_fields_match,
                "workflow": workflow_match,
                "source": source_match,
                "builder": builder_match,
            }
            last_detail = ", ".join(f"{name}={ok}" for name, ok in matches.items())
            if all(matches.values()):
                return True, f"verified GitHub SLSA v1 provenance ({last_detail})"
        return False, last_detail
    except (json.JSONDecodeError, TypeError, ValueError, KeyError) as exc:
        return False, f"invalid GitHub provenance verification output: {exc}"


def verify_provenance(
    *,
    image: str,
    repo: str,
    commit: str,
    workflow: str,
    ref: str,
    issuer: str = "https://token.actions.githubusercontent.com",
    runner: Runner = real_runner,
) -> ProvenanceReport:
    """Run every provenance criterion and return the report."""
    report = ProvenanceReport()

    # 1+2: artifact-to-commit + repository binding via image labels.
    try:
        labels = _docker_image_labels(image, runner)
        source = labels.get("org.opencontainers.image.source", "")
        revision = labels.get("org.opencontainers.image.revision", "")

        expected_source = f"https://github.com/{repo}"
        normalized_source = source.rstrip("/").removesuffix(".git")
        repo_match = normalized_source.casefold() == expected_source.casefold()
        report.set(
            "repository",
            repo_match,
            f"source label: {source or '<missing>'} (expected repo {repo})",
        )
        report.set(
            "commit",
            revision == commit,
            f"revision label: {revision or '<missing>'} (expected {commit})",
        )
    except (
        RuntimeError,
        subprocess.CalledProcessError,
        ValueError,
    ) as exc:  # pragma: no cover
        report.set("repository", False, str(exc))
        report.set("commit", False, str(exc))

    # 3+4+6: keyless signature identity binds workflow + branch.
    identity = f"https://github.com/{repo}/{workflow}@{ref}"
    sig = _cosign_verify(image, identity, issuer, runner)
    sig_ok = sig.returncode == 0
    report.set(
        "signature",
        sig_ok,
        f"cosign verify vs identity {identity} — {sig.stdout.strip()[:120] if sig_ok else sig.stderr.strip()[:120]}",
    )
    if sig_ok:
        # cosign returned success only after enforcing the exact identity argument.
        report.set("workflow", True, f"signature identity enforced {workflow}")
        report.set("branch", True, f"signature identity enforced {ref}")
    else:
        report.set("workflow", False, "signature verification failed")
        report.set("branch", False, "signature verification failed")

    # 5: GitHub SBOM attestation must bind the digest and contain packages.
    sbom_result = _github_attestation_verify(
        image=image,
        repo=repo,
        workflow=workflow,
        ref=ref,
        commit=commit,
        predicate_type=SPDX_PREDICATE_TYPE,
        issuer=issuer,
        runner=runner,
    )
    sbom_ok, sbom_detail = _validate_sbom_attestation(sbom_result, image)
    report.set("sbom", sbom_ok, sbom_detail)

    # 7: GitHub SLSA v1 provenance must match the exact build and source.
    prov_result = _github_attestation_verify(
        image=image,
        repo=repo,
        workflow=workflow,
        ref=ref,
        commit=commit,
        predicate_type=SLSA_PREDICATE_TYPE,
        issuer=issuer,
        runner=runner,
    )
    prov_ok, prov_detail = _validate_slsa_attestation(
        prov_result,
        image=image,
        repo=repo,
        workflow=workflow,
        ref=ref,
        commit=commit,
    )
    report.set("provenance", prov_ok, prov_detail)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--image", required=True, help="immutable image ref (digest or sha-<sha> tag)"
    )
    parser.add_argument(
        "--repo", required=True, help="owner/repo, e.g. huythongbk15/LearnTradeAgent"
    )
    parser.add_argument("--commit", required=True, help="expected 40-char commit SHA")
    parser.add_argument(
        "--workflow", default=".github/workflows/ci.yml", help="expected workflow path"
    )
    parser.add_argument(
        "--ref", default="refs/heads/master", help="expected ref (branch)"
    )
    parser.add_argument(
        "--issuer", default="https://token.actions.githubusercontent.com"
    )
    args = parser.parse_args()

    try:
        report = verify_provenance(
            image=args.image,
            repo=args.repo,
            commit=args.commit,
            workflow=args.workflow,
            ref=args.ref,
            issuer=args.issuer,
        )
    except OSError as exc:  # pragma: no cover
        print(f"Environment error: {exc}", file=sys.stderr)
        return 2

    print(report.summary())
    if report.all_ok:
        print("\n✅ DEPLOY GATE PASSED — provenance fully verified.")
        return 0
    print(
        "\n❌ DEPLOY GATE BLOCKED — at least one provenance criterion failed.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
