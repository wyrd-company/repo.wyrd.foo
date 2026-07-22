#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

stage="${1:?stage directory is required}"
: "${GNUPGHOME:?GNUPGHOME is required}"
: "${SIGNING_FINGERPRINT:?SIGNING_FINGERPRINT is required}"

gpg --batch --homedir "$GNUPGHOME" --armor --export "$SIGNING_FINGERPRINT" \
  > "${stage}/pubkey.asc"

while IFS= read -r rpm_path; do
  [[ -n "$rpm_path" ]] || continue
  rpmsign --addsign \
    --define "__gpg /usr/bin/gpg" \
    --define "_gpg_name ${SIGNING_FINGERPRINT}" \
    --define "_gpg_path ${GNUPGHOME}" \
    --define "_gpg_digest_algo sha256" \
    "$rpm_path"
done < "${stage}/.new-rpms"
rm -f "${stage}/.new-rpms"

rpm_db="$(mktemp -d)"
trap 'rm -rf "$rpm_db"' EXIT
rpmkeys --dbpath "$rpm_db" --import "${stage}/pubkey.asc"
while IFS= read -r rpm_path; do
  rpmkeys --dbpath "$rpm_db" --checksig "$rpm_path" | grep -q 'digests signatures OK$'
done < <(find "${stage}/rpm" -type f -name '*.rpm' -print | sort)

for arch in amd64 arm64; do
  binary_dir="${stage}/apt/dists/stable/main/binary-${arch}"
  mkdir -p "$binary_dir"
  (
    cd "${stage}/apt"
    dpkg-scanpackages --arch "$arch" --multiversion pool /dev/null
  ) > "${binary_dir}/Packages"
  gzip -n -9 -c "${binary_dir}/Packages" > "${binary_dir}/Packages.gz"
  for index in Packages Packages.gz; do
    digest="$(sha256sum "${binary_dir}/${index}" | cut -d' ' -f1)"
    mkdir -p "${binary_dir}/by-hash/SHA256"
    cp "${binary_dir}/${index}" "${binary_dir}/by-hash/SHA256/${digest}"
  done
done

release_dir="${stage}/apt/dists/stable"
(
  cd "${stage}/apt"
  apt-ftparchive \
    -o APT::FTPArchive::Release::Origin='Wyrd Company' \
    -o APT::FTPArchive::Release::Label='Wyrd Company' \
    -o APT::FTPArchive::Release::Suite='stable' \
    -o APT::FTPArchive::Release::Codename='stable' \
    -o APT::FTPArchive::Release::Architectures='amd64 arm64' \
    -o APT::FTPArchive::Release::Components='main' \
    -o APT::FTPArchive::Release::Description='Wyrd Company Linux packages' \
    -o APT::FTPArchive::Release::Acquire-By-Hash='yes' \
    release dists/stable
) > "${release_dir}/Release"
gpg --batch --yes --homedir "$GNUPGHOME" --local-user "$SIGNING_FINGERPRINT" \
  --armor --detach-sign --output "${release_dir}/Release.gpg" "${release_dir}/Release"
gpg --batch --yes --homedir "$GNUPGHOME" --local-user "$SIGNING_FINGERPRINT" \
  --armor --clearsign --output "${release_dir}/InRelease" "${release_dir}/Release"

for arch in x86_64 aarch64; do
  rpm_root="${stage}/rpm/stable/${arch}"
  mkdir -p "${rpm_root}/Packages"
  createrepo_c --update --retain-old-md 5 "$rpm_root"
  gpg --batch --yes --homedir "$GNUPGHOME" --local-user "$SIGNING_FINGERPRINT" \
    --armor --detach-sign --output "${rpm_root}/repodata/repomd.xml.asc" \
    "${rpm_root}/repodata/repomd.xml"
done

cat > "${stage}/wyrd.repo" <<'EOF'
[wyrd-company]
name=Wyrd Company
baseurl=https://repo.wyrd.foo/rpm/stable/$basearch
enabled=1
gpgcheck=1
repo_gpgcheck=1
gpgkey=https://repo.wyrd.foo/pubkey.asc
metadata_expire=5m
EOF
