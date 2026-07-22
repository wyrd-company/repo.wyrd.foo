---
relationships:
  references:
    - package-repository-publishing
    - use-release-manifest-handoffs
---

# repo.wyrd.foo

`repo.wyrd.foo` publishes Wyrd Company's signed APT and RPM repositories and
maintains matching prebuilt packages in the Arch User Repository (AUR).

Product release workflows build packages in their source repositories and
publish immutable GitHub Release assets. The `repo-wyrd-foo-publisher` GitHub
App then commits one release manifest to the pre-existing
`release-manifests` inbox branch at:

```text
releases/<product>/<version>.json
```

The manifest binds an exact source commit and release tag to every artifact URL
and SHA-256 digest. Binary packages are not committed to Git. The App dispatches
the exact inbox commit and path. The publisher executes only immutable code from
`main`, treats the inbox checkout as data, validates its commit shape and the
publisher-owned product allowlist, resolves the source tag to the declared
commit, then signs and publishes the artifacts.

## APT

Install the repository key and source definition:

```console
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://repo.wyrd.foo/pubkey.gpg |
  sudo tee /etc/apt/keyrings/wyrd-company.gpg >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/wyrd-company.gpg] \
https://repo.wyrd.foo/apt stable main" |
  sudo tee /etc/apt/sources.list.d/wyrd-company.list >/dev/null
sudo apt update
sudo apt install wyrwood
```

## RPM

Install the repository definition and package:

```console
sudo curl -fsSL https://repo.wyrd.foo/wyrd.repo -o /etc/yum.repos.d/wyrd.repo
sudo dnf install wyrwood
```

The RPM repository verifies both package signatures and repository metadata
with an ASCII-armored copy of the same repository key at `pubkey.asc`.

## AUR

The prebuilt Arch package is named `wyrwood-bin`:

```console
paru -S wyrwood-bin
```

## Publishing contract

Product repositories use `.github/actions/submit-release` with an installation
token for the `repo-wyrd-foo-publisher` GitHub App. The App is installed only on
`wyrd-company/repo.wyrd.foo`. It has Metadata read and Contents read/write. It
has no Actions, Administration, Secrets, Workflows, Pull requests, or other
repository permissions. Its token is never persisted in a checkout.

The App can update only the `release-manifests` inbox branch under the required
rulesets. It cannot push or bypass protection on `main`. Product workflows
provide:

- `REPO_WYRD_FOO_APP_CLIENT_ID`
- `REPO_WYRD_FOO_APP_PRIVATE_KEY`

This repository's publisher uses:

- `REPO_WYRD_FOO_GPG_KEY`
- `REPO_WYRD_FOO_S3_API`, the full R2 bucket URL
- `REPO_WYRD_FOO_S3_ACCESS_KEY`
- `REPO_WYRD_FOO_S3_SECRET`
- `AUR_ACCOUNT`
- `AUR_PRIVATE_KEY`

`REPO_WYRD_FOO_S3_API` has the form
`https://<account>.r2.cloudflarestorage.com/<bucket>`. The workflow derives the
S3 endpoint and bucket without logging the configured URL.

## Required repository rulesets

Create `release-manifests` from an existing commit before distributing App
credentials; do not create it as an orphan branch. The first manifest addition
must have exactly one parent, like every later inbox commit. Configure the
following rules as separate rulesets so each bypass has the narrow scope
described here:

- The branch-safety ruleset targets `main` and `release-manifests`, requires
  linear history, and blocks force pushes and deletion. The product App has no
  bypass.
- The branch-creation ruleset restricts creation to named operators and
  automation identities. The product App has no bypass, so it cannot create
  another branch.
- The main-review ruleset requires pull requests for `main`. The product App
  has no bypass. Named operators or a dedicated merge bot may bypass this rule
  only for the repository's reviewed, no-merge-commit workflow.
- The inbox-update ruleset permits direct updates to `release-manifests` only
  from `repo-wyrd-foo-publisher` and named operators. The App bypass applies
  only to this ruleset; the branch-safety baseline still applies.

The inbox is untrusted data: changes are publishable only when a single-parent
commit adds exactly one canonical manifest at its expected, never-previously-
used path. Modifications, deletions, re-additions, merges, additional files,
and commits not reachable from the inbox branch fail closed.

## Publication invariants

The publisher follows GitHub redirects because the initial release URL is
restricted to the declared `github.com` repository/tag/filename, the final host
must remain GitHub HTTPS infrastructure, and the downloaded bytes must match the
manifest SHA-256 digest. Intermediate redirect hosts therefore do not extend
artifact trust.

AUR packages use `pkgrel=1`. Published versions are immutable, so a metadata-only
correction requires a new upstream version rather than revising an existing AUR
package release.

## Development

```console
task check
```
