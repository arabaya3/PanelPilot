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

# The only picomatch copies the runner may hold, exactly, both tracked separately in the
# record (not resolved by it): Next's vendored copy and the base image's npm copy, pinned
# to the version recorded. Any change (another copy, a moved copy, a new npm version)
# fails, so it is looked at again rather than passed silently.
ALLOWED_COPIES='/app/node_modules/next/dist/compiled/picomatch
/usr/local/lib/node_modules/npm/node_modules/picomatch'
# The files holding picomatch code, exactly: the two copies above and the copy Next
# bundles into its file tracer (@vercel/nft), also tracked separately.
ALLOWED_CODE='/app/node_modules/next/dist/compiled/@vercel/nft/index.js
/app/node_modules/next/dist/compiled/picomatch/index.js
/usr/local/lib/node_modules/npm/node_modules/picomatch/lib/constants.js
/usr/local/lib/node_modules/npm/node_modules/picomatch/lib/parse.js'
NPM_PICOMATCH_VERSION='4.0.3'

# Run as root inside a throwaway container; no network, nothing written back.
listing=$(docker run --rm --network none --user 0 --entrypoint /bin/sh "$image" -c '
  set -eu
  test -f /app/apps/web/server.js || { echo "MISSING /app/apps/web/server.js"; exit 3; }
  echo "## watcher"
  find / -xdev -path "*@parcel/watcher*" -prune -print 2>/dev/null || true
  echo "## picomatch"
  # Any file, directory or symlink named picomatch, wherever it is.
  find / -xdev -name "picomatch*" ! -name "*.d.ts" -prune -print 2>/dev/null | sort || true
  echo "## code"
  # picomatch code wherever it is, bundled, copied or renamed, in any file under the app
  # and in JS under /usr/local: its constants and parser.
  { grep -rlE "POSIX_REGEX_SOURCE|analyzeRepeatedExtglob" /app 2>/dev/null || true
    find /usr/local -xdev -type f \( -name "*.js" -o -name "*.cjs" -o -name "*.mjs" \) \
      -exec grep -lE "POSIX_REGEX_SOURCE|analyzeRepeatedExtglob" {} + 2>/dev/null || true
  } | sort
  echo "## links"
  # Symbolic links in the app that lead to picomatch or the watcher (a link to an allowed
  # copy would let the app load it under another name).
  find /app -xdev -type l 2>/dev/null | while read -r link; do
    target=$(readlink -f "$link" 2>/dev/null || true)
    case "$target" in *picomatch*|*@parcel*) echo "$link -> $target" ;; esac
  done
  echo "## bundle"
  grep -rlE "@parcel/watcher|SourceFileWatcher" /app/apps/web 2>/dev/null || true
  echo "## npm"
  sed -n "s/^ *\"version\": *\"\\([^\"]*\\)\".*/\\1/p" \
    /usr/local/lib/node_modules/npm/node_modules/picomatch/package.json 2>/dev/null | head -1
  echo "## end"
') || { echo "error: could not inspect $image (exit $?)" >&2; exit 1; }

case "$listing" in *"## end"*) ;; *) echo "error: incomplete listing from $image" >&2; exit 1 ;; esac

section() { printf '%s\n' "$listing" | awk -v s="## $1" '$0==s{on=1;next} /^## /{on=0} on&&NF'; }

watcher=$(section watcher)
picomatch=$(section picomatch)
code=$(section code)
bundle=$(section bundle)
links=$(section links)
npm_version=$(section npm)

echo "picomatch in $image:"
printf '%s\n' "${picomatch:-(none)}" | sed 's/^/  /'
echo "picomatch code in $image:"
printf '%s\n' "${code:-(none)}" | sed 's/^/  /'

status=0
fail() { echo "error: $1" >&2; [ -z "${2:-}" ] || printf '%s\n' "$2" | sed 's/^/  /' >&2; status=1; }

[ -z "$watcher" ] || fail "@parcel/watcher is in the runner image:" "$watcher"
[ -z "$links" ] || fail "symbolic links in the app lead to picomatch or the watcher:" "$links"
[ -z "$bundle" ] || fail "the app references @parcel/watcher / SourceFileWatcher:" "$bundle"
[ "$picomatch" = "$ALLOWED_COPIES" ] ||
  fail "picomatch copies differ from the recorded ones (expected exactly the two tracked copies):" "$picomatch"
[ "$code" = "$ALLOWED_CODE" ] ||
  fail "picomatch code differs from the recorded files (another copy, bundled or moved):" "$code"
[ "$npm_version" = "$NPM_PICOMATCH_VERSION" ] ||
  fail "the base image's npm picomatch is '${npm_version:-unknown}', recorded $NPM_PICOMATCH_VERSION: review the record"

if [ "$status" -eq 0 ]; then
  echo "runner image check: passed (the excluded @parcel/watcher / picomatch path is not shipped)"
else
  echo "runner image check: FAILED" >&2
fi
exit "$status"
