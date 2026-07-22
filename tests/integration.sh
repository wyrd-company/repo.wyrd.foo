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

if grep -aq '^-----BEGIN PGP PUBLIC KEY BLOCK-----$' "${stage}/pubkey.gpg"; then
  echo 'pubkey.gpg must be a binary OpenPGP keyring.' >&2
  exit 1
fi
grep -aq '^-----BEGIN PGP PUBLIC KEY BLOCK-----$' "${stage}/pubkey.asc"
binary_fingerprint="$(
  gpg --batch --show-keys --with-colons "${stage}/pubkey.gpg" |
    awk -F: '$1 == "fpr" { print $10; exit }'
)"
armored_fingerprint="$(
  gpg --batch --show-keys --with-colons "${stage}/pubkey.asc" |
    awk -F: '$1 == "fpr" { print $10; exit }'
)"
[[ -n "$binary_fingerprint" && "$binary_fingerprint" == "$armored_fingerprint" ]]

gpgv --keyring "${work}/pubkey.gpg" \
  "${stage}/apt/dists/stable/InRelease" >/dev/null
gpgv --keyring "${work}/pubkey.gpg" \
  "${stage}/apt/dists/stable/Release.gpg" \
  "${stage}/apt/dists/stable/Release" >/dev/null
if grep -Fq "$work" "${stage}/apt/dists/stable/Release"; then
  echo 'APT Release contains a build-host path.' >&2
  exit 1
fi

verify_apt_consumer() {
  local extension="${1:?key extension is required}"
  local attempt="${2:?attempt name is required}"
  local keyring="/etc/apt/keyrings/wyrd-company.${extension}"
  local apt_state="${work}/apt-state-${extension}-${attempt}"
  local sources_list="${work}/sources-${extension}-${attempt}.list"

  install -d -m 0755 /etc/apt/keyrings
  cp "${stage}/pubkey.${extension}" "$keyring"
  mkdir -p "${apt_state}/lists/partial"
  printf 'deb [signed-by=%s] file:%s stable main\n' \
    "$keyring" "${stage}/apt" > "$sources_list"
  apt-get \
    -o "Dir::Etc::sourcelist=${sources_list}" \
    -o 'Dir::Etc::sourceparts=-' \
    -o "Dir::State::lists=${apt_state}/lists" \
    -o 'APT::Get::List-Cleanup=0' \
    update >/dev/null
}

# The binary .gpg path is the immutable Wyrwood 0.1.0 documentation contract;
# the armored .asc path is the preferred contract for current documentation.
verify_apt_consumer gpg initial
verify_apt_consumer asc initial
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
# their unsigned release inputs, and must not alter any immutable artifact that
# would already have been restored from R2 after a prior partial publication.
find "${stage}/artifacts" "${stage}/apt/pool" "${stage}/rpm" \
  -type f \( -name '*.tar.gz' -o -name '*.deb' -o -name '*.rpm' \) \
  -print0 | sort -z | xargs -0 sha256sum > "${work}/immutable-before"
sha256sum "${stage}/pubkey.gpg" "${stage}/pubkey.asc" > "${work}/keys-before"
python3 "${root}/scripts/repository.py" stage \
  "$manifest" "$artifacts" "$stage" "${work}/pubkey.gpg"
"${root}/scripts/build-indexes.sh" "$stage"
find "${stage}/artifacts" "${stage}/apt/pool" "${stage}/rpm" \
  -type f \( -name '*.tar.gz' -o -name '*.deb' -o -name '*.rpm' \) \
  -print0 | sort -z | xargs -0 sha256sum > "${work}/immutable-after"
sha256sum "${stage}/pubkey.gpg" "${stage}/pubkey.asc" > "${work}/keys-after"
cmp "${work}/immutable-before" "${work}/immutable-after"
cmp "${work}/keys-before" "${work}/keys-after"
verify_apt_consumer gpg retry
verify_apt_consumer asc retry
