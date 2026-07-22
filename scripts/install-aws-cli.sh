#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

readonly AWS_CLI_VERSION="2.36.6"

case "$(uname -m)" in
  x86_64)
    readonly aws_cli_arch="x86_64"
    readonly aws_cli_sha256="1bfec455a394ae18e1b89cce7acf38bb85cf6b7b24e31305c3cfde079534827f"
    ;;
  aarch64 | arm64)
    readonly aws_cli_arch="aarch64"
    readonly aws_cli_sha256="8a8d1a1b2c48636a526fabb5ea4d7163114df25ffe40579da0a7e46296a6cf33"
    ;;
  *)
    printf 'Unsupported AWS CLI architecture: %s\n' "$(uname -m)" >&2
    exit 1
    ;;
esac

: "${RUNNER_TEMP:?RUNNER_TEMP must identify the GitHub runner temporary directory}"
: "${GITHUB_PATH:?GITHUB_PATH must identify the GitHub Actions path file}"

readonly install_dir="${RUNNER_TEMP}/repo-wyrd-foo-aws-cli"
readonly bin_dir="${RUNNER_TEMP}/repo-wyrd-foo-aws-cli-bin"
readonly archive_url="https://awscli.amazonaws.com/awscli-exe-linux-${aws_cli_arch}-${AWS_CLI_VERSION}.zip"
work_dir="$(mktemp -d "${RUNNER_TEMP}/repo-wyrd-foo-aws-cli-install.XXXXXX")"
readonly work_dir

cleanup() {
  find "$work_dir" -mindepth 1 -delete
  rmdir "$work_dir"
}
trap cleanup EXIT

curl \
  --fail \
  --location \
  --proto '=https' \
  --retry 3 \
  --show-error \
  --silent \
  --tlsv1.2 \
  "$archive_url" \
  --output "${work_dir}/awscliv2.zip"

printf '%s  %s\n' "$aws_cli_sha256" "${work_dir}/awscliv2.zip" | sha256sum --check --status
unzip -q "${work_dir}/awscliv2.zip" -d "$work_dir"
"${work_dir}/aws/install" --install-dir "$install_dir" --bin-dir "$bin_dir"

aws_cli_version="$("${bin_dir}/aws" --version 2>&1)"
case "$aws_cli_version" in
  "aws-cli/${AWS_CLI_VERSION} "*) ;;
  *)
    printf 'Installed an unexpected AWS CLI version: %s\n' "$aws_cli_version" >&2
    exit 1
    ;;
esac

printf '%s\n' "$bin_dir" >>"$GITHUB_PATH"
printf 'Installed %s from the checksum-pinned AWS bundle.\n' "$aws_cli_version"
