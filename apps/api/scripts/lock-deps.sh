#!/usr/bin/env bash
# Regenerate the dependency lock files from pyproject.toml.
#
#   scripts/lock-deps.sh             # after editing dependencies: adds and
#                                    # removes what changed, keeps every
#                                    # other pin where it is
#   scripts/lock-deps.sh --upgrade   # move every pin to its newest allowed
#   scripts/lock-deps.sh --upgrade-package fastapi   # move one
#
# Two files, because the runtime image must not carry dev tooling:
#   requirements.lock      [project.dependencies]          -> the image
#   requirements-dev.lock  the same, plus the dev extra   -> CI, local dev
#
# Both are hashed, so `pip install --require-hashes` refuses any file that is
# not the one locked. `--universal` so one file serves the Linux image, CI,
# and a developer's Mac or Windows machine.
#
# Needs uv at the version CI's `lock drift` job pins (UV_VERSION in
# .github/workflows/ci.yml); another version may format the files
# differently and fail that check. `pip install uv==<that version>`.
set -euo pipefail
cd "$(dirname "$0")/.."

compile() {
  uv pip compile pyproject.toml \
    --universal \
    --python-version 3.12 \
    --generate-hashes \
    --custom-compile-command "scripts/lock-deps.sh" \
    --quiet \
    "$@"
}

compile --output-file requirements.lock "$@"
compile --extra dev --output-file requirements-dev.lock "$@"
