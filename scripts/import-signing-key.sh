#!/usr/bin/env bash
# ---
# relationships:
#   implements: package-repository-publishing
# ---

set -euo pipefail

if [[ -z "${REPO_WYRD_FOO_GPG_KEY:-}" ]]; then
  echo "REPO_WYRD_FOO_GPG_KEY is required." >&2
  exit 1
fi
if [[ -z "${GITHUB_ENV:-}" ]]; then
  echo "GITHUB_ENV is required." >&2
  exit 1
fi

public_key="${1:?public key path is required}"
gpg_home="$(mktemp -d)"
chmod 0700 "$gpg_home"
trap 'rm -rf "$gpg_home"' EXIT
private_key="${gpg_home}/private-key.asc"
printf '%s' "$REPO_WYRD_FOO_GPG_KEY" > "$private_key"
chmod 0600 "$private_key"
gpg --batch --homedir "$gpg_home" --import "$private_key" >/dev/null 2>&1
rm -f "$private_key"

public_fingerprint="$(gpg --batch --show-keys --with-colons "$public_key" | awk -F: '$1 == "fpr" { print $10; exit }')"
secret_fingerprint="$(gpg --batch --homedir "$gpg_home" --list-secret-keys --with-colons | awk -F: '$1 == "fpr" { print $10; exit }')"
if [[ -z "$public_fingerprint" || "$secret_fingerprint" != "$public_fingerprint" ]]; then
  echo "Configured signing key does not match pubkey.gpg." >&2
  exit 1
fi

{
  printf 'GNUPGHOME=%s\n' "$gpg_home"
  printf 'SIGNING_FINGERPRINT=%s\n' "$secret_fingerprint"
} >> "$GITHUB_ENV"
trap - EXIT
