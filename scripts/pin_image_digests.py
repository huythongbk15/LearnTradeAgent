#!/usr/bin/env python3
"""Pin digest for every infra service image in the docker-compose files.

Queries each registry's manifest API and rewrites ``image: repo:tag`` to
``image: repo:tag@sha256:...`` (Docker Compose accepts the combined form).
Local build images (trading-agent, learntradeagent) are left untouched.

Usage: python scripts/pin_image_digests.py [--write]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request

COMPOSE_FILES = [
    "docker-compose.yml",
    "docker-compose.prod.yml",
    "docker-compose.oracle.yml",
]

SKIP = {"trading-agent:latest", "learntradeagent:oracle"}

# ghcr.io/timescaledb/timescaledb is not anonymously pullable (DENIED); the
# same project publishes on Docker Hub as timescale/timescaledb.
# prometheus/node-exporter moved namespaces on Docker Hub.
ALIASES = {
    "ghcr.io/timescaledb/timescaledb": "timescale/timescaledb",
    "prometheus/node-exporter": "prom/node-exporter",
}

IMAGE_RE = re.compile(r"^\s*image:\s*(.+?)\s*$")


def hub_digest(repo: str, tag: str) -> str:
    if "/" not in repo:
        repo = f"library/{repo}"
    url = f"https://hub.docker.com/v2/repositories/{repo}/tags/{tag}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        # Short tags like "v2.53" often do not exist; resolve the newest
        # matching patch release (e.g. v2.53.5) instead of failing the pin.
        data = _hub_latest_matching(repo, tag)
    images = data.get("images") or []
    for image in images:
        digest = image.get("digest", "")
        if digest and image.get("architecture") in ("amd64", None):
            return digest
    raise RuntimeError(f"no amd64 digest for {repo}:{tag}")


def _hub_latest_matching(repo: str, tag: str) -> dict:
    """Return the tag payload for the newest version matching a prefix."""
    url = (
        f"https://hub.docker.com/v2/repositories/{repo}/tags"
        f"?page_size=100&name={tag}"
    )
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.load(resp)
    results = data.get("results") or []
    if not results:
        raise RuntimeError(f"no Docker Hub tag matches {repo}:{tag}")
    return results[0]


def registry_digest(image: str) -> str:
    """Generic OCI registry manifest digest (ghcr.io, gcr.io, quay.io).

    ``image`` is a full ``registry/namespace/repo:tag`` reference.
    """
    registry, _, rest = image.partition("/")
    rest, _, tag = rest.rpartition(":")
    namespace, _, repo = rest.rpartition("/")
    name = f"{namespace}/{repo}"
    if registry == "quay.io":
        token_url = f"https://quay.io/v2/auth?service=quay.io&scope=repository:{name}:pull"
    else:
        token_url = (
            f"https://{registry}/token?scope=repository:{name}:pull&service="
            f"{registry.replace('.', '') if registry == 'ghcr.io' else ''}"
        )
    headers = {}
    try:
        with urllib.request.urlopen(token_url, timeout=30) as resp:
            token = json.load(resp).get("token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception:
        pass
    url = f"https://{registry}/v2/{name}/manifests/{tag}"
    headers["Accept"] = (
        "application/vnd.oci.image.index.v1+json,"
        "application/vnd.docker.distribution.manifest.list.v2+json,"
        "application/vnd.docker.distribution.manifest.v2+json"
    )
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        digest = resp.headers.get("Docker-Content-Digest")
    if not digest:
        raise RuntimeError(f"no digest header for {image}")
    return digest


def resolve(image_ref: str) -> str:
    if "@" in image_ref:
        return image_ref  # already pinned
    repo, _, tag = image_ref.partition(":")
    if not tag:
        raise RuntimeError(f"tag required for {image_ref}")
    if repo in ALIASES:
        alias = ALIASES[repo]
        if alias.startswith(("ghcr.io/", "gcr.io/", "quay.io/")):
            digest = registry_digest(f"{alias}:{tag}")
            return f"{alias}:{tag}@{digest}"
        return f"{alias}:{tag}@{hub_digest(alias, tag)}"
    if repo.startswith(("ghcr.io/", "gcr.io/", "quay.io/")):
        digest = registry_digest(image_ref)
    else:
        digest = hub_digest(repo, tag)
    return f"{image_ref}@{digest}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    resolved: dict[str, str] = {}
    for path in COMPOSE_FILES:
        with open(path, encoding="utf-8") as handle:
            lines = handle.readlines()
        changed = 0
        for i, line in enumerate(lines):
            match = IMAGE_RE.match(line)
            if not match:
                continue
            ref = match.group(1).strip()
            if ref in SKIP or "@" in ref or ref.startswith("${"):
                continue
            if ref not in resolved:
                print(f"{ref} -> resolving...", file=sys.stderr)
                resolved[ref] = resolve(ref)
            pinned = resolved[ref]
            if pinned != ref:
                lines[i] = line.replace(ref, pinned)
                changed += 1
                print(f"  pinned {ref}", file=sys.stderr)
        if args.write:
            with open(path, "w", encoding="utf-8") as handle:
                handle.writelines(lines)
            print(f"{path}: {changed} image(s) pinned", file=sys.stderr)
        else:
            print(f"{path}: {changed} image(s) would be pinned", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
