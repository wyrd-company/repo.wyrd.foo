#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

manifest="${1:?manifest path is required}"
: "${AUR_ACCOUNT:?AUR_ACCOUNT is required}"
: "${AUR_PRIVATE_KEY:?AUR_PRIVATE_KEY is required}"
: "${RUNNER_TEMP:?RUNNER_TEMP must identify the GitHub runner temporary directory}"

if [[ "$RUNNER_TEMP" != /* || "$RUNNER_TEMP" == / || ! -d "$RUNNER_TEMP" ]]; then
  echo 'RUNNER_TEMP must be an existing absolute directory other than root.' >&2
  exit 1
fi
runner_temp="$(realpath -e -- "$RUNNER_TEMP")"
if [[ "$runner_temp" == / || "$runner_temp" == *','* || "$runner_temp" == *$'\n'* ]]; then
  echo 'The canonical RUNNER_TEMP cannot be root or contain commas or newlines.' >&2
  exit 1
fi
work_root="$(mktemp -d "${runner_temp}/repo-wyrd-foo-aur.XXXXXX")"
work_root="$(realpath -e -- "$work_root")"
case "$work_root" in
  "${runner_temp}"/repo-wyrd-foo-aur.*) ;;
  *)
    echo 'The AUR work directory escaped RUNNER_TEMP.' >&2
    exit 1
    ;;
esac
trap 'rm -rf -- "$work_root"' EXIT

ssh_root="${work_root}/ssh"
mkdir -p "$ssh_root"
private_key="${ssh_root}/aur"
known_hosts="${ssh_root}/known_hosts"
printf '%s' "$AUR_PRIVATE_KEY" | python3 scripts/prepare-ssh-private-key.py "$private_key"
if ! ssh-keygen -y -P '' -f "$private_key" >/dev/null 2>&1; then
  echo 'AUR private key is invalid or encrypted after line-ending normalization.' >&2
  exit 1
fi

package="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["publish"]["aur"]["package"])' "$manifest")"
version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$manifest")"
rendered="${work_root}/rendered"
mkdir -p "$rendered"
rendered="$(realpath -e -- "$rendered")"
if [[ "$rendered" != "${work_root}/rendered" || "$rendered" != /* ]]; then
  echo 'The rendered AUR directory escaped its work directory.' >&2
  exit 1
fi
python3 scripts/repository.py render-aur "$manifest" "$rendered"

aur_build_image='archlinux@sha256:412efebb0eeef0ef322ff24ad73f82b1ba2d3b12377db4c5fbe3074c7e7e8678'
docker run --rm --mount "type=bind,src=${rendered},dst=/source,readonly" "$aur_build_image" \
  bash -euc 'useradd --create-home builder; cp -R /source/. /home/builder/package; chown -R builder:builder /home/builder/package; su builder -c "cd /home/builder/package && makepkg --printsrcinfo"' \
  > "${rendered}/.SRCINFO"

ssh-keyscan -t ed25519 aur.archlinux.org > "$known_hosts" 2>/dev/null
fingerprint="$(ssh-keygen -lf "$known_hosts" -E sha256 | awk '{ print $2 }')"
if [[ "$fingerprint" != 'SHA256:RFzBCUItH9LZS0cKB5UE6ceAYhBD5C8GeOBip8Z11+4' ]]; then
  echo "AUR SSH host fingerprint mismatch." >&2
  exit 1
fi

export GIT_SSH_COMMAND="ssh -i ${private_key} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -o UserKnownHostsFile=${known_hosts}"
checkout=''
for attempt in 1 2 3 4; do
  candidate="$(mktemp -d "${work_root}/checkout.XXXXXX")"
  if git clone "ssh://aur@aur.archlinux.org/${package}.git" "$candidate"; then
    checkout="$candidate"
    break
  fi
  rm -rf -- "$candidate"
  sleep "$attempt"
done
if [[ -z "$checkout" ]]; then
  echo 'Unable to clone the AUR package after four attempts.' >&2
  exit 1
fi
find "$checkout" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf -- {} +
cp "${rendered}/PKGBUILD" "${rendered}/.SRCINFO" "$checkout/"
git -C "$checkout" config user.name "$AUR_ACCOUNT"
git -C "$checkout" config user.email 'support@wyrd.company'
git -C "$checkout" add PKGBUILD .SRCINFO
if git -C "$checkout" diff --cached --quiet; then
  exit 0
fi
git -C "$checkout" commit -m "Update ${package} to ${version}"
for attempt in 1 2 3 4; do
  if git -C "$checkout" push origin HEAD:master; then
    exit 0
  fi
  if [[ "$attempt" -eq 4 ]]; then
    echo 'Unable to push the AUR package after four attempts.' >&2
    exit 1
  fi
  sleep "$attempt"
done
