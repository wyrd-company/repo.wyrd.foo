#!/usr/bin/env bash
# ---
# relationships:
#   verifies: package-repository-publishing
# ---

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
runner_temp="$(mktemp -d "${TMPDIR:-/tmp}/repo-wyrd-foo-aws-cli-smoke.XXXXXX")"
readonly root runner_temp

cleanup() {
  find "$runner_temp" -mindepth 1 -delete
  rmdir "$runner_temp"
}
trap cleanup EXIT

export RUNNER_TEMP="$runner_temp"
export GITHUB_PATH="${runner_temp}/github-path"
touch "$GITHUB_PATH"

"${root}/scripts/install-aws-cli.sh"
first_version="$("${runner_temp}/repo-wyrd-foo-aws-cli-bin/aws" --version 2>&1)"

"${root}/scripts/install-aws-cli.sh"
second_version="$("${runner_temp}/repo-wyrd-foo-aws-cli-bin/aws" --version 2>&1)"

test "$first_version" = "$second_version"
case "$second_version" in
  "aws-cli/2.36.6 "*) ;;
  *)
    printf 'Unexpected repeated-install version: %s\n' "$second_version" >&2
    exit 1
    ;;
esac

test "$(wc -l <"$GITHUB_PATH")" -eq 2
test "$(sort -u "$GITHUB_PATH" | wc -l)" -eq 1
