#!/usr/bin/env bash
# Run the end-to-end browser tests (tests/e2e) in Microsoft's Playwright container, so the host needs no
# browser or browser libraries. Works with podman or docker. The repository is mounted read-only; the
# tests copy the app into a temp directory inside the container.
#
#   scripts/e2e_in_container.sh            # all e2e tests
#   scripts/e2e_in_container.sh -k round   # extra arguments go to pytest
#
# The image tag must match the playwright version pinned in requirements-dev.txt. The image ships
# Python 3.12 (production and CI use 3.11), so this is a convenience check; CI is the reference run.
set -euo pipefail

cd "$(dirname "$0")/.."
version=$(grep -oP '^playwright==\K[0-9.]+' requirements-dev.txt)
image="mcr.microsoft.com/playwright/python:v${version}-noble"
engine=$(command -v podman || command -v docker || { echo "podman or docker is required" >&2; exit 1; })

exec "$engine" run --rm \
    -v "$PWD":/work:ro,Z -w /work \
    -e PYTHONDONTWRITEBYTECODE=1 -e EEK_E2E_REQUIRED=1 \
    "$image" \
    bash -c 'pip install -q --break-system-packages --disable-pip-version-check -r requirements-dev.txt \
             && python -m pytest tests/e2e -p no:cacheprovider "$@"' _ "$@"
