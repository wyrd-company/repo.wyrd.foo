#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${INBOX_COMMIT:?INBOX_COMMIT is required}"
: "${MANIFEST_PATH:?MANIFEST_PATH is required}"

payload="$(python3 - "$INBOX_COMMIT" "$MANIFEST_PATH" <<'PY'
import json
import sys

print(json.dumps({
    "event_type": "release-manifest",
    "client_payload": {"commit": sys.argv[1], "path": sys.argv[2]},
}))
PY
)"
printf '%s' "$payload" |
  gh api --method POST repos/wyrd-company/repo.wyrd.foo/dispatches --input -
