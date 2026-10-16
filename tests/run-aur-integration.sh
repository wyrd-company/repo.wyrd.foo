#!/usr/bin/env bash
# ---
# relationships:
#   verifies: package-repository-publishing
# ---
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
artifacts="$(realpath -e -- "${1:?release artifacts are required}")"
work="$(mktemp -d)"
docker_config="$(mktemp -d)"
trap 'rm -rf "$work" "$docker_config"' EXIT
export DOCKER_CONFIG="$docker_config"
aur_manifest="${work}/aur-manifest.json"
python3 - \
  "${root}/tests/fixtures/sample-tool-1.2.3.json" \
  "${artifacts}" \
  "$aur_manifest" <<'PY'
import hashlib
import json
import pathlib
import sys

source, artifacts, destination = map(pathlib.Path, sys.argv[1:])
manifest = json.loads(source.read_text(encoding="utf-8"))
manifest["publish"]["aur"]["installation"] = {
    "files": {
        "packaging/sample.service": "/usr/lib/systemd/system/sample.service",
        "packaging/sample.tmpfiles": "/usr/lib/tmpfiles.d/sample.conf",
        "packaging/sample.hook": "/usr/share/libalpm/hooks/sample.hook",
        "packaging/pre-install.sh": "/usr/lib/sample-tool/pre-install.sh",
    },
    "hooks": {
        name: f"packaging/{name}.sh"
        for name in ("pre-install", "post-install", "pre-remove", "post-remove")
    },
}
for artifact in manifest["artifacts"]:
    if artifact["format"] == "tar.gz":
        artifact["sha256"] = hashlib.sha256(
            (artifacts / artifact["filename"]).read_bytes()
        ).hexdigest()
destination.write_text(json.dumps(manifest), encoding="utf-8")
PY

rendered="${work}/rendered package"
python3 "${root}/scripts/repository.py" render-aur \
  "$aur_manifest" "$rendered" --artifacts "$artifacts"
cp "${root}/tests/run-installed-binary.sh" "$rendered/"
cp "${artifacts}/sample-tool_1.2.3_linux_x86_64.tar.gz" \
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
    bsdtar -tf "$package_file" | grep -Fxq "usr/lib/systemd/system/sample.service"
    bsdtar -tf "$package_file" | grep -Fxq "usr/share/libalpm/hooks/sample.hook"
    bsdtar -tf "$package_file" | grep -Fxq ".INSTALL"
    # Bootstrap the incoming aborting hook before the first package transaction.
    install -Dm644 /home/builder/package/src/packaging/pre-install.sh /etc/sample-tool/pre-install.sh
    install -d /etc/pacman.d/hooks
    sed "s|/usr/lib/sample-tool/pre-install.sh|/etc/sample-tool/pre-install.sh|" /home/builder/package/src/packaging/sample.hook > /etc/pacman.d/hooks/sample.hook
    touch /run/sample-stop-fails
    if pacman -U --noconfirm "$package_file"; then
      echo "Failed stop did not abort first installation." >&2
      exit 1
    fi
    [[ ! -e /usr/bin/sample-tool ]]
    grep -Fxq pre-install /run/sample-lifecycle
    rm /run/sample-stop-fails
    pacman -U --noconfirm "$package_file"
    bash /source/run-installed-binary.sh /usr/bin/sample-tool | grep -Fxq sample-tool
    rm /etc/pacman.d/hooks/sample.hook /etc/sample-tool/pre-install.sh
    rm /run/sample-lifecycle
    # An existing daemon executable must survive a failed upgrade unchanged.
    printf "old executable\n" > /usr/bin/sample-tool
    touch /run/sample-stop-fails
    if pacman -U --noconfirm "$package_file"; then
      echo "Failed stop did not abort upgrade." >&2
      exit 1
    fi
    grep -Fxq "old executable" /usr/bin/sample-tool
    grep -Fxq pre-install /run/sample-lifecycle
    rm /run/sample-stop-fails
    pacman -U --noconfirm "$package_file"
    bash /source/run-installed-binary.sh /usr/bin/sample-tool | grep -Fxq sample-tool
    pacman -R --noconfirm sample-tool-bin
    [[ ! -e /usr/lib/systemd/system/sample.service ]]
    grep -Fxq pre-remove /run/sample-lifecycle
    grep -Fxq post-remove /run/sample-lifecycle
  '
