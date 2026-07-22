#!/usr/bin/env bash
# ---
# relationships:
#   verifies: package-repository-publishing
# ---

set -euo pipefail

root='/repo'
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
artifacts="${work}/artifacts"
stage="${work}/public"
mkdir -p "$artifacts" "$stage"
cp /goreleaser-dist/*.tar.gz /goreleaser-dist/*.deb /goreleaser-dist/*.rpm "$artifacts/"
if [[ "$(find "$artifacts" -maxdepth 1 -type f | wc -l)" -ne 6 ]]; then
  echo 'GoReleaser did not produce the six expected package artifacts.' >&2
  exit 1
fi

manifest="${work}/manifest.json"
cp "${root}/tests/fixtures/sample-tool-1.2.3.json" "$manifest"
python3 - "$manifest" "$artifacts" <<'PY'
import hashlib
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
artifacts = Path(sys.argv[2])
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
for artifact in manifest["artifacts"]:
    artifact["sha256"] = hashlib.sha256(
        (artifacts / artifact["filename"]).read_bytes()
    ).hexdigest()
manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
PY

signing_home="${work}/signing-home"
mkdir -m 0700 "$signing_home"
gpg --batch --homedir "$signing_home" --passphrase '' \
  --quick-generate-key 'Package Test <package-test@example.invalid>' ed25519 sign 0
gpg --batch --homedir "$signing_home" --export > "${work}/pubkey.gpg"
export REPO_WYRD_FOO_GPG_KEY
REPO_WYRD_FOO_GPG_KEY="$(
  gpg --batch --homedir "$signing_home" --armor --export-secret-keys
)"
export GITHUB_ENV="${work}/github-env"
"${root}/scripts/import-signing-key.sh" "${work}/pubkey.gpg"
set -a
# shellcheck disable=SC1090
source "$GITHUB_ENV"
set +a

python3 "${root}/scripts/repository.py" stage \
  "$manifest" "$artifacts" "$stage" "${work}/pubkey.gpg"
"${root}/scripts/build-indexes.sh" "$stage"

gpgv --keyring "${work}/pubkey.gpg" \
  "${stage}/apt/dists/stable/InRelease" >/dev/null
gpgv --keyring "${work}/pubkey.gpg" \
  "${stage}/apt/dists/stable/Release.gpg" \
  "${stage}/apt/dists/stable/Release" >/dev/null
if grep -Fq "$work" "${stage}/apt/dists/stable/Release"; then
  echo 'APT Release contains a build-host path.' >&2
  exit 1
fi

apt_state="${work}/apt-state"
mkdir -p "${apt_state}/lists/partial"
printf 'deb [signed-by=%s] file:%s stable main\n' \
  "${work}/pubkey.gpg" "${stage}/apt" > "${work}/sources.list"
apt-get \
  -o "Dir::Etc::sourcelist=${work}/sources.list" \
  -o 'Dir::Etc::sourceparts=-' \
  -o "Dir::State::lists=${apt_state}/lists" \
  -o 'APT::Get::List-Cleanup=0' \
  update >/dev/null
for arch in amd64 arm64; do
  grep -Fq 'Package: sample-tool' \
    "${stage}/apt/dists/stable/main/binary-${arch}/Packages"
done

verification_db="${work}/rpm-verification"
mkdir -p "$verification_db"
rpmkeys --dbpath "$verification_db" --import "${stage}/pubkey.asc"
while IFS= read -r package_path; do
  rpmkeys --dbpath "$verification_db" --checksig "$package_path" |
    grep -q 'digests signatures OK$'
done < <(find "${stage}/rpm" -type f -name '*.rpm' -print | sort)
for arch in x86_64 aarch64; do
  gpgv --keyring "${work}/pubkey.gpg" \
    "${stage}/rpm/stable/${arch}/repodata/repomd.xml.asc" \
    "${stage}/rpm/stable/${arch}/repodata/repomd.xml" >/dev/null
done

# A retry must retain the already signed RPMs rather than replacing them with
# their unsigned release inputs.
python3 "${root}/scripts/repository.py" stage \
  "$manifest" "$artifacts" "$stage" "${work}/pubkey.gpg"
"${root}/scripts/build-indexes.sh" "$stage"
