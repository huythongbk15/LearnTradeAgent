#!/usr/bin/env python3
"""Verify GitHub branch ruleset + production environment controls (P0.6).

Reads the live GitHub repository settings via the REST API and reports
whether the controls documented in ``.github/BRANCH_PROTECTION.md`` are
actually applied server-side. Requires ``GITHUB_TOKEN`` (read-only scope
is enough) and ``GITHUB_REPO`` (default: owner/repo from git remote).

Exit codes:
  0 = all required controls verified present
  1 = at least one required control is missing/not verifiable
  2 = cannot verify (no token / not a repo / API error)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REQUIRED_RULESET_CHECKS = {
    "pull requests": "require_pull_request",
    "branch up to date": "require_last_push_approval",
}
REQUIRED_ENV_CHECKS = {
    "required reviewers": "required_reviewers",
    "prevent self-review": "prevent_self_review",
    "deploy only from master": "deployment_branch_policy",
}


def default_repo() -> str:
    try:
        remote = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return ""
    for prefix in ("https://github.com/", "git@github.com:"):
        if remote.startswith(prefix):
            return remote[len(prefix):].removesuffix(".git")
    return ""


def _get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(url)
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("Accept", "application/vnd.github+json")
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def check_branch_protection(repo: str, branch: str, token: str) -> tuple[dict, list[str]]:
    url = f"https://api.github.com/repos/{repo}/branches/{branch}/protection"
    try:
        data = _get_json(url, token)
    except urllib.error.HTTPError as exc:
        if exc.code in (404, 403):
            return {}, [f"branch protection not enabled for {branch} (HTTP {exc.code})"]
        raise
    findings: dict[str, bool] = {
        "require_pull_request": bool(data.get("required_pull_request_reviews")),
        "require_last_push_approval": bool(
            (data.get("required_pull_request_reviews") or {}).get("require_last_push_approval")
        ),
        "enforce_admins": bool(data.get("enforce_admins", {}).get("enabled")),
        "require_ci": bool(
            (data.get("required_status_checks") or {}).get("contexts")
            or (data.get("required_status_checks") or {}).get("checks")
        ),
        "block_force_push": bool((data.get("allow_force_pushes") or {}).get("enabled")) is False,
    }
    missing = [name for name, ok in findings.items() if not ok]
    return findings, missing


def check_environment(repo: str, env: str, token: str) -> tuple[dict, list[str]]:
    url = f"https://api.github.com/repos/{repo}/environments/{env}"
    try:
        data = _get_json(url, token)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {}, [f"environment '{env}' not configured (HTTP 404)"]
        raise
    rules = {rule.get("type"): rule for rule in data.get("protection_rules", [])}
    findings: dict[str, bool] = {
        "required_reviewers": bool(rules.get("required_reviewers")),
        "prevent_self_review": bool(
            (rules.get("required_reviewers") or {}).get("prevent_self_review")
        ),
        "deployment_branch_policy": bool(rules.get("deployment_branch_policy")),
    }
    missing = [name for name, ok in findings.items() if not ok]
    return findings, missing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPO", ""))
    parser.add_argument("--branch", default="master")
    parser.add_argument("--environment", default="production")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = args.repo or default_repo()
    if not token:
        print("cannot verify: GITHUB_TOKEN not set", file=sys.stderr)
        return 2
    if not repo:
        print("cannot verify: no --repo and could not parse git remote", file=sys.stderr)
        return 2

    code = 0
    try:
        findings, missing = check_branch_protection(repo, args.branch, token)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"cannot verify: GitHub API error: {exc}", file=sys.stderr)
        return 2
    print(f"branch protection [{repo}:{args.branch}]")
    for name, ok in sorted(findings.items()):
        print(f"  {'[OK]' if ok else '[MISSING]'} {name}")
    if missing:
        code = 1

    try:
        findings, missing = check_environment(repo, args.environment, token)
    except (urllib.error.URLError, urllib.error.HTTPError) as exc:
        print(f"cannot verify: GitHub API error: {exc}", file=sys.stderr)
        return 2
    print(f"environment [{repo}:{args.environment}]")
    for name, ok in sorted(findings.items()):
        print(f"  {'[OK]' if ok else '[MISSING]'} {name}")
    if missing:
        code = 1

    if code == 0:
        print("ALL GITHUB CONTROLS VERIFIED")
    else:
        print("MISSING CONTROLS — see above", file=sys.stderr)
    return code


if __name__ == "__main__":
    raise SystemExit(main())