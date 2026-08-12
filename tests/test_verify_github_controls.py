"""Tests for GitHub controls verifier (P0.6)."""

from __future__ import annotations

import json
import urllib.error


from scripts.verify_github_controls import (
    check_branch_protection,
    check_environment,
    default_repo,
)


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = json.dumps(payload).encode()

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None


def _stub_get_json(monkeypatch, payload: dict | None = None, status: int = 200):
    import scripts.verify_github_controls as module

    def fake(url, token):
        if status != 200:
            raise urllib.error.HTTPError(url, status, "err", {}, None)
        return payload or {}

    monkeypatch.setattr(module, "_get_json", fake)


def test_branch_protection_full(monkeypatch):
    _stub_get_json(
        monkeypatch,
        {
            "required_pull_request_reviews": {
                "require_last_push_approval": True,
            },
            "enforce_admins": {"enabled": True},
            "required_status_checks": {"contexts": ["Lint & Test"]},
            "allow_force_pushes": {"enabled": False},
        },
    )
    findings, missing = check_branch_protection("owner/repo", "master", "tok")
    assert findings["require_pull_request"] is True
    assert missing == []


def test_branch_protection_missing_controls(monkeypatch):
    _stub_get_json(monkeypatch, {})
    findings, missing = check_branch_protection("owner/repo", "master", "tok")
    assert set(missing) == {
        "require_pull_request",
        "require_last_push_approval",
        "enforce_admins",
        "require_ci",
    }


def test_branch_protection_not_enabled(monkeypatch):
    _stub_get_json(monkeypatch, status=404)
    _, missing = check_branch_protection("owner/repo", "master", "tok")
    assert missing == ["branch protection not enabled for master (HTTP 404)"]


def test_environment_full(monkeypatch):
    _stub_get_json(
        monkeypatch,
        {
            "protection_rules": [
                {"type": "required_reviewers", "prevent_self_review": True},
                {"type": "deployment_branch_policy"},
            ]
        },
    )
    findings, missing = check_environment("owner/repo", "production", "tok")
    assert findings["required_reviewers"] is True
    assert findings["prevent_self_review"] is True
    assert missing == []


def test_environment_missing(monkeypatch):
    _stub_get_json(monkeypatch, status=404)
    _, missing = check_environment("owner/repo", "production", "tok")
    assert missing == ["environment 'production' not configured (HTTP 404)"]


def test_default_repo_parses_https_remote():
    assert default_repo() == "" or "/" in default_repo()