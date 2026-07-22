#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
: "${RESULT:?RESULT is required}"

commit="$(git log --diff-filter=A --format='%H' -1 -- "$RESULT")"
if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
  echo 'Unable to resolve the commit that added the release manifest.' >&2
  exit 1
fi
printf 'commit=%s\n' "$commit" >> "$GITHUB_OUTPUT"
