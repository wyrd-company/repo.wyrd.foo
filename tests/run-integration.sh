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

aur_manifest="${work}/aur-manifest.json"
python3 - \
  "${root}/tests/fixtures/sample-tool-1.2.3.json" \
  "${release_project}/dist" \
  "$aur_manifest" <<'PY'
import hashlib
import json
import pathlib
import sys

source, artifacts, destination = map(pathlib.Path, sys.argv[1:])
manifest = json.loads(source.read_text(encoding="utf-8"))
for artifact in manifest["artifacts"]:
    if artifact["format"] == "tar.gz":
        artifact["sha256"] = hashlib.sha256(
            (artifacts / artifact["filename"]).read_bytes()
        ).hexdigest()
destination.write_text(json.dumps(manifest), encoding="utf-8")
PY

rendered="${work}/rendered package"
python3 "${root}/scripts/repository.py" render-aur \
  "$aur_manifest" "$rendered"
cp "${release_project}/dist/sample-tool_1.2.3_linux_x86_64.tar.gz" \
  "${rendered}/sample-tool-bin-1.2.3-x86_64.tar.gz"
aur_build_image='archlinux@sha256:412efebb0eeef0ef322ff24ad73f82b1ba2d3b12377db4c5fbe3074c7e7e8678'
docker run --rm --mount "type=bind,src=${rendered},dst=/source,readonly" "$aur_build_image" \
  bash -euc '
    if touch /source/write-probe 2>/dev/null; then
      echo "AUR source bind mount is writable." >&2
      exit 1
    fi
    useradd --create-home builder
    cp -R /source/. /home/builder/package
    chown -R builder:builder /home/builder/package
    su builder -c "cd /home/builder/package && makepkg --printsrcinfo"
  ' > "${work}/SRCINFO"
grep -Fxq 'pkgbase = sample-tool-bin' "${work}/SRCINFO"
grep -Fxq 'pkgname = sample-tool-bin' "${work}/SRCINFO"
grep -Fq 'source_x86_64 = sample-tool-bin-1.2.3-x86_64.tar.gz::https://repo.wyrd.foo/artifacts/sample-tool/1.2.3/sample-tool_1.2.3_linux_x86_64.tar.gz' "${work}/SRCINFO"
grep -Fq 'source_aarch64 = sample-tool-bin-1.2.3-aarch64.tar.gz::https://repo.wyrd.foo/artifacts/sample-tool/1.2.3/sample-tool_1.2.3_linux_aarch64.tar.gz' "${work}/SRCINFO"

docker run --rm --mount "type=bind,src=${rendered},dst=/source,readonly" "$aur_build_image" \
  bash -euc '
    useradd --create-home builder
    cp -R /source/. /home/builder/package
    chown -R builder:builder /home/builder/package
    su builder -c "cd /home/builder/package && makepkg --nodeps --noconfirm"
    package_file="$(find /home/builder/package -maxdepth 1 -type f -name "sample-tool-bin-1.2.3-1-x86_64.pkg.tar.*" -print -quit)"
    [[ -n "$package_file" ]]
    bsdtar -tf "$package_file" | grep -Fxq "usr/bin/sample-tool"
  '
