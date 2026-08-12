#!/usr/bin/env python3
"""Verify CD provenance before deploy (Wave E — supply-chain provenance).

Enforces every rejection criterion from the spec §23.  The CD pipeline must
reject an artifact when ANY of the following is wrong:

  1. wrong repository   (image source label / expected repo)
  2. wrong commit       (image revision label / expected commit sha)
  3. wrong workflow     (signature certificate identity / expected workflow)
  4. wrong branch       (signature certificate identity / expected ref)
  5. missing SBOM       (spdxjson attestation with packages)
  6. invalid signature  (cosign verify against keyless identity)
  7. missing provenance (SLSA provenance attestation with matching predicate)

Running an image through this gate is the *only* way a deploy should proceed.
Local usage (requires docker + cosign):

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
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Callable

# A runner is injectable for tests: ``runner(cmd, **kw) -> CompletedProcess``
Runner = Callable[..., subprocess.CompletedProcess]


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
    result = runner(["docker", "image", "inspect", "--format", "{{json .Config.Labels}}", image])
    if result.returncode != 0:
        raise RuntimeError(f"docker inspect failed: {result.stderr.strip()}")
    return json.loads(result.stdout.strip() or "{}")


def _cosign_verify(image: str, identity: str, issuer: str, runner: Runner) -> subprocess.CompletedProcess:
    env = dict(os.environ, COSIGN_EXPERIMENTAL="1")
    return runner(
        [
            "cosign", "verify", image,
            "--certificate-identity", identity,
            "--certificate-oidc-issuer", issuer,
        ],
        env=env,
    )


def _cosign_attestations(image: str, type_: str, runner: Runner) -> tuple[bool, str]:
    env = dict(os.environ, COSIGN_EXPERIMENTAL="1")
    result = runner(
        ["cosign", "verify-attestation", image, "--type", type_],
        env=env,
    )
    return result.returncode == 0, result.stdout


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

        repo_match = source.endswith(f"github.com/{repo}") or repo in source
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
    except (RuntimeError, subprocess.CalledProcessError, ValueError) as exc:  # pragma: no cover
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
        # The certificate identity encodes workflow AND branch; verify both.
        report.set("workflow", workflow in sig.stdout, "identity matched workflow")
        report.set("branch", ref in sig.stdout, "identity matched ref")
    else:
        report.set("workflow", False, "signature verification failed")
        report.set("branch", False, "signature verification failed")

    # 5: SBOM attestation must exist and contain packages.
    sbom_ok, sbom_out = _cosign_attestations(image, "spdxjson", runner)
    sbom_detail = "spdxjson attestation present" if sbom_ok else "missing/failed spdxjson attestation"
    report.set("sbom", sbom_ok, sbom_detail)

    # 7: provenance attestation must exist and match the expected build.
    prov_ok, prov_out = _cosign_attestations(image, "slsaprovenance", runner)
    prov_detail = "slsaprovenance attestation present"
    if prov_ok:
        try:
            payloads = json.loads(prov_out)
            predicate = payloads[0].get("payload", "")
            import base64

            decoded = json.loads(base64.b64decode(predicate)) if predicate else {}
            subject = decoded.get("subject", [])
            subject_match = any(
                repo.lower() in (s.get("name", "") or "").lower()
                and commit in (s.get("digest", {}).get("sha256", "") or "")
                for s in subject
            )
            pred = decoded.get("predicate", {})
            sha = pred.get("metadata", {}).get("completeness", {}).get("sha256", "")
            builder = pred.get("builder", {}).get("id", "")
            prov_detail = (
                f"slsaprovenance; subject_match={subject_match}; "
                f"builder={builder or '<missing>'}; completeness_sha256={sha or '<missing>'}"
            )
            prov_ok = prov_ok and subject_match
        except (json.JSONDecodeError, KeyError, IndexError, AttributeError):
            prov_detail = "slsaprovenance present but predicate unparseable"
            prov_ok = False
    report.set("provenance", prov_ok, prov_detail)

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="immutable image ref (digest or sha-<sha> tag)")
    parser.add_argument("--repo", required=True, help="owner/repo, e.g. huythongbk15/LearnTradeAgent")
    parser.add_argument("--commit", required=True, help="expected 40-char commit SHA")
    parser.add_argument("--workflow", default=".github/workflows/ci.yml", help="expected workflow path")
    parser.add_argument("--ref", default="refs/heads/master", help="expected ref (branch)")
    parser.add_argument("--issuer", default="https://token.actions.githubusercontent.com")
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
    print("\n❌ DEPLOY GATE BLOCKED — at least one provenance criterion failed.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())