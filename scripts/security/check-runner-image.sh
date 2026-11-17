#!/bin/sh
# Production-artifact check for exception GHSA-fc5q-39xc-vjpf
# (security/exceptions/GHSA-fc5q-39xc-vjpf.json).
#
# Fails if the excluded dependency path reaches the shipped web runner image: any
# @parcel/watcher, or any picomatch package installed from the app's dependency tree,
# anywhere under the app (/app), or the watcher's code in the server bundle. It also
# fails if the image cannot be inspected (fail closed).
#
# Every picomatch copy in the image is listed, so the copies this exception does NOT
# cover stay visible: Next's vendored copy (node_modules/next/dist/compiled/picomatch)
# and npm's own copy in the node base image. Both are tracked separately in the record;
# this check does not resolve them.
#
# Usage: scripts/security/check-runner-image.sh IMAGE
set -eu

image="${1:?usage: check-runner-image.sh IMAGE}"

# Run as root inside a throwaway container; no network, nothing written back.
listing=$(docker run --rm --network none --user 0 --entrypoint /bin/sh "$image" -c '
  set -eu
  test -f /app/apps/web/server.js || { echo "MISSING /app/apps/web/server.js"; exit 3; }
  echo "## watcher"
  find / -xdev -path "*/node_modules/@parcel/watcher*" -prune -print 2>/dev/null || true
  echo "## picomatch"
  find / -xdev -type d -name picomatch -path "*node_modules*" 2>/dev/null || true
  echo "## bundle"
  grep -rl --include=*.js -e "@parcel/watcher" -e "SourceFileWatcher" /app/apps/web/.next/server 2>/dev/null || true
  echo "## end"
') || { echo "error: could not inspect $image (exit $?)" >&2; exit 1; }

case "$listing" in *"## end"*) ;; *) echo "error: incomplete listing from $image" >&2; exit 1 ;; esac

section() { printf '%s\n' "$listing" | awk -v s="## $1" '$0==s{on=1;next} /^## /{on=0} on&&NF'; }

watcher=$(section watcher)
picomatch=$(section picomatch)
bundle=$(section bundle)

echo "picomatch copies in $image:"
printf '%s\n' "${picomatch:-  (none)}" | sed 's/^/  /'

status=0
if [ -n "$watcher" ]; then
  echo "error: @parcel/watcher is in the runner image:" >&2
  printf '%s\n' "$watcher" | sed 's/^/  /' >&2
  status=1
fi
if [ -n "$bundle" ]; then
  echo "error: the server bundle references @parcel/watcher / SourceFileWatcher:" >&2
  printf '%s\n' "$bundle" | sed 's/^/  /' >&2
  status=1
fi
# Allowed, and tracked separately: Next's vendored copy and the base image's npm.
app_copies=$(printf '%s\n' "$picomatch" | grep '^/app/' | grep -v '^/app/node_modules/next/dist/compiled/picomatch$' || true)
if [ -n "$app_copies" ]; then
  echo "error: a picomatch package from the app's dependency tree is in the runner image:" >&2
  printf '%s\n' "$app_copies" | sed 's/^/  /' >&2
  status=1
fi
other=$(printf '%s\n' "$picomatch" | grep -v '^/app/' | grep -v '^/usr/local/lib/node_modules/npm/' | grep . || true)
if [ -n "$other" ]; then
  echo "error: picomatch outside the app and the base image's npm (not recorded):" >&2
  printf '%s\n' "$other" | sed 's/^/  /' >&2
  status=1
fi
if [ "$status" -eq 0 ]; then
  echo "runner image check: passed (the excluded @parcel/watcher / picomatch path is not shipped)"
else
  echo "runner image check: FAILED" >&2
fi
exit "$status"
