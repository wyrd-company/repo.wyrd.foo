#!/usr/bin/env bash
# ---
# relationships:
#   verifies: package-repository-publishing
# ---

set -euo pipefail
export LC_ALL=C

errors="$(mktemp)"
trap 'rm -f "$errors"' EXIT

# Overlay filesystems can briefly reject exec after pacman replaces an inode.
# Retry only that fixture failure; preserve all ordinary command failures.
for attempt in 1 2 3 4; do
  status=0
  "$@" 2>"$errors" || status=$?
  cat "$errors" >&2
  if [[ "$status" -eq 0 ]]; then
    exit 0
  fi
  if [[ "$status" -ne 126 ]] || ! grep -Fq 'Text file busy' "$errors" || [[ "$attempt" -eq 4 ]]; then
    exit "$status"
  fi
  sleep "$attempt"
done
