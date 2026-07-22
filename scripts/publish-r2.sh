#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

stage="${1:?stage directory is required}"
: "${R2_ENDPOINT:?R2_ENDPOINT is required}"
: "${R2_BUCKET:?R2_BUCKET is required}"

destination="s3://${R2_BUCKET}"
aws_options=(--endpoint-url "$R2_ENDPOINT" --only-show-errors)

aws s3 sync "$stage" "$destination" "${aws_options[@]}" \
  --exclude '.new-rpms' \
  --exclude 'pubkey.gpg' \
  --exclude 'pubkey.asc' \
  --exclude 'wyrd.repo' \
  --exclude 'apt/dists/stable/InRelease' \
  --exclude 'apt/dists/stable/Release' \
  --exclude 'apt/dists/stable/Release.gpg' \
  --exclude 'apt/dists/stable/main/binary-*/Packages' \
  --exclude 'apt/dists/stable/main/binary-*/Packages.gz' \
  --exclude 'rpm/stable/*/repodata/repomd.xml' \
  --exclude 'rpm/stable/*/repodata/repomd.xml.asc' \
  --cache-control 'public,max-age=31536000,immutable'

upload_mutable() {
  local relative="$1"
  aws s3 cp "${stage}/${relative}" "${destination}/${relative}" "${aws_options[@]}" \
    --cache-control 'no-cache,max-age=0,must-revalidate'
}

upload_mutable pubkey.gpg
upload_mutable pubkey.asc
upload_mutable wyrd.repo
for arch in amd64 arm64; do
  upload_mutable "apt/dists/stable/main/binary-${arch}/Packages"
  upload_mutable "apt/dists/stable/main/binary-${arch}/Packages.gz"
done
upload_mutable apt/dists/stable/Release
upload_mutable apt/dists/stable/Release.gpg
upload_mutable apt/dists/stable/InRelease
for arch in x86_64 aarch64; do
  upload_mutable "rpm/stable/${arch}/repodata/repomd.xml.asc"
  upload_mutable "rpm/stable/${arch}/repodata/repomd.xml"
done
