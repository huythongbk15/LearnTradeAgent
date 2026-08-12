#!/usr/bin/env bash
# Automated dependency-lock refresh (Wave E — dependency reproducibility).
#
# Usage: ./scripts/update_deps.sh [--upgrade]
#
#   * Always regenerates uv.lock (resolves exact transitive versions) and
#     re-exports the hashed production requirements.lock used by the
#     Docker build and CI freshness checks.
#   * With --upgrade, bumps the lowest allowed versions within the
#     pyproject.toml ranges (the daily/weekly maintenance path).
#
# After running, review and commit: uv.lock + requirements.lock
#   (and pyproject.toml if you changed ranges).
set -euo pipefail
cd "$(dirname "$0")/.."

command -v uv >/dev/null 2>&1 || { echo "uv is required: pip install uv"; exit 1; }

if [[ "${1:-}" == "--upgrade" ]]; then
    echo ">> uv lock --upgrade (bumping to newest allowed versions)"
    uv lock --upgrade
else
    echo ">> uv lock (refresh within existing ranges)"
    uv lock
fi

echo ">> uv export -> requirements.lock (hashed, all extras, no dev, no editable, no project)"
uv export --format requirements-txt --all-extras --no-dev --no-editable --no-emit-project -o requirements.lock

echo ">> Done. Review and commit: uv.lock + requirements.lock"