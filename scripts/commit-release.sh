#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

: "${APP_SLUG:?APP_SLUG is required}"
: "${APP_TOKEN:?APP_TOKEN is required}"
: "${RESULT:?RESULT is required}"

git config user.name "${APP_SLUG}[bot]"
git config user.email "${APP_SLUG}[bot]@users.noreply.github.com"
git add -- "$RESULT"
git commit -m "publish: add ${RESULT#releases/}"
authorization="$(printf 'x-access-token:%s' "$APP_TOKEN" | base64 -w0)"
export GIT_CONFIG_COUNT=1
export GIT_CONFIG_KEY_0='http.https://github.com/.extraheader'
export GIT_CONFIG_VALUE_0="AUTHORIZATION: basic ${authorization}"
for attempt in 1 2 3 4; do
  git fetch origin release-manifests
  git rebase origin/release-manifests
  if git push origin HEAD:release-manifests; then
    exit 0
  fi
  if [[ "$attempt" -eq 4 ]]; then
    echo "Unable to submit release manifest after concurrent updates." >&2
    exit 1
  fi
done
