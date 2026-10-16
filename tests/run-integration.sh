#!/usr/bin/env bash
# ---
# relationships:
#   verifies: package-repository-publishing
# ---

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
work="$(mktemp -d)"
docker_config="$(mktemp -d)"
trap 'rm -rf "$work" "$docker_config"' EXIT
export DOCKER_CONFIG="$docker_config"

integration_image='repo-wyrd-foo-integration'
release_project="${work}/release-project"
cp -R "${root}/tests/fixtures/release-project" "$release_project"
git -C "$release_project" init --quiet
git -C "$release_project" config user.name 'Package Test'
git -C "$release_project" config user.email 'package-test@example.invalid'
git -C "$release_project" add .
git -C "$release_project" commit --quiet -m 'fixture'
goreleaser_image='goreleaser/goreleaser@sha256:054eefd282c02233a2556ce2d1a60cd2f51dc565ffc2520dc38b5deb4dd1ad30'
docker run --rm --user "$(id -u):$(id -g)" \
  --env HOME=/tmp --env GOCACHE=/tmp/go-build \
  --volume "${release_project}:/project" --workdir /project \
  "$goreleaser_image" release --snapshot --clean
expected_artifacts=(
  sample-tool_1.2.3_linux_x86_64.tar.gz
  sample-tool_1.2.3_linux_aarch64.tar.gz
  sample-tool_1.2.3_linux_x86_64.deb
  sample-tool_1.2.3_linux_aarch64.deb
  sample-tool_1.2.3_linux_x86_64.rpm
  sample-tool_1.2.3_linux_aarch64.rpm
)
for artifact in "${expected_artifacts[@]}"; do
  [[ -f "${release_project}/dist/${artifact}" ]]
done

docker build --quiet --network host \
  --secret id=host_ca,src=/etc/ssl/certs/ca-certificates.crt \
  --file "${root}/tests/integration.Containerfile" \
  --tag "$integration_image" "$root" >/dev/null
docker run --rm --volume "${root}:/repo:ro" \
  --volume "${release_project}/dist:/goreleaser-dist:ro" "$integration_image"

"${root}/tests/run-aur-integration.sh" "${release_project}/dist"
