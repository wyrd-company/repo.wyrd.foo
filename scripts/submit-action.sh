#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

: "${GITHUB_ACTION_PATH:?GITHUB_ACTION_PATH is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"
: "${GITHUB_WORKSPACE:?GITHUB_WORKSPACE is required}"
: "${MANIFEST:?MANIFEST is required}"

submission="$(python3 "$GITHUB_ACTION_PATH/../../../scripts/repository.py" \
  submit "$MANIFEST" "$GITHUB_WORKSPACE/.repo-wyrd-foo")"
IFS=$'\t' read -r state path <<< "$submission"
if [[ "$state" != 'created' && "$state" != 'unchanged' ]]; then
  echo 'Unexpected release-manifest submission result.' >&2
  exit 1
fi
{
  printf 'state=%s\n' "$state"
  printf 'path=%s\n' "$path"
} >> "$GITHUB_OUTPUT"
