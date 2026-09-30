#!/usr/bin/env bash
# Install the locked dependencies, then this package, as CI and a developer do.
#
#   scripts/install-locked.sh                     # requirements-dev.lock
#   scripts/install-locked.sh requirements.lock   # runtime only
#
# Retries only one failure: "No matching distribution found". A lock can pin a
# release a few hours old, and a CI runner's view of the package index
# sometimes has not caught up with it yet -- the version is on PyPI and the
# runner cannot see it. That failed a CI install twice in one day
# (langchain-core 1.6.6, platformdirs 4.12.2) and passed on a re-run each time.
#
# Anything else fails at once. A hash mismatch is the lock refusing a file
# that is not the one it recorded, and retrying that would only delay the
# answer.
set -euo pipefail
cd "$(dirname "$0")/.."

lock="${1:-requirements-dev.lock}"
attempts="${INSTALL_ATTEMPTS:-3}"
delay="${INSTALL_RETRY_DELAY_S:-60}"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

for attempt in $(seq 1 "$attempts"); do
  # After the first attempt, bypass pip's cache: a cached index page is
  # exactly the stale view being waited out.
  cache_flag=()
  if [ "$attempt" -gt 1 ]; then cache_flag=(--no-cache-dir); fi

  if pip install "${cache_flag[@]}" --require-hashes -r "$lock" 2>&1 | tee "$log"; then
    pip install --no-deps -e .
    exit 0
  fi
  if ! grep -q "No matching distribution found" "$log"; then
    exit 1
  fi
  if [ "$attempt" -lt "$attempts" ]; then
    echo "::warning::package index is missing a locked version; retrying in ${delay}s (attempt ${attempt}/${attempts})"
    sleep "$delay"
  fi
done

echo "::error::a locked version is still missing from the package index after ${attempts} attempts"
exit 1
