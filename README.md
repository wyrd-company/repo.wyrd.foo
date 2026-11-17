---
relationships:
  references:
    - package-repository-publishing
    - use-release-manifest-handoffs
---

# repo.wyrd.foo

`repo.wyrd.foo` publishes Wyrd Company's signed APT and RPM repositories and
maintains matching prebuilt packages in the Arch User Repository (AUR).

## APT

Install the repository key and source definition:

```console
sudo install -d -m 0755 /etc/apt/keyrings
curl -fsSL https://repo.wyrd.foo/pubkey.asc |
  sudo tee /etc/apt/keyrings/wyrd-company.asc >/dev/null
echo "deb [signed-by=/etc/apt/keyrings/wyrd-company.asc] \
https://repo.wyrd.foo/apt stable main" |
  sudo tee /etc/apt/sources.list.d/wyrd-company.list >/dev/null
sudo apt update
```

`pubkey.asc` is the ASCII-armored consumer key. The equivalent `pubkey.gpg` is
a binary OpenPGP keyring for consumers that use a `.gpg` keyring path. Both
forms have the same primary fingerprint.

### envconsul

After adding the APT repository, install envconsul with:

```console
sudo apt install envconsul
```

The **Publish envconsul for APT** workflow checks the latest stable
[`hashicorp/envconsul` release](https://github.com/hashicorp/envconsul/releases)
daily at 06:43 UTC and supports manual runs on `main`. It verifies HashiCorp's
signed checksums and the Linux ZIP digests, then packages the upstream binaries
and license for amd64 and arm64. Packages use the upstream version with Debian
revision `-1`. Published package paths are immutable.

The workflow uses the existing repository signing and R2 secrets. It shares the
product publisher's concurrency group and preserves existing packages. RPM and
AUR packages are not produced for envconsul.

Both publishers queue pending runs and use the same 30-minute job limit.
Already-published envconsul versions do not trigger index regeneration or uploads.

## RPM

Install the repository definition:

```console
sudo curl -fsSL https://repo.wyrd.foo/wyrd.repo -o /etc/yum.repos.d/wyrd.repo
```

The RPM repository verifies both package signatures and repository metadata
with an ASCII-armored copy of the same repository key at `pubkey.asc`.

## AUR

Prebuilt Arch packages are published to the AUR as `<product>-bin`.

## Submit a release

Product repositories submit a release manifest with the
`wyrd-company/repo.wyrd.foo/.github/actions/submit-release` action, pinned by
commit. It validates the manifest, commits it to the `release-manifests` inbox
and requests publication of that commit.

The action takes the installation token of the repo.wyrd.foo publisher App
as `token`. Request the token for exactly:

- Repository: `wyrd-company/repo.wyrd.foo` only, in the `wyrd-company`
  organization.
- Permissions: Contents `write`. Metadata `read` comes with every installation
  token. Request no other permission.

With `token` set, the action reads no private key and mints no token. It
names the commit author `<app-slug>[bot]`, where `app-slug` defaults to
`repo-wyrd-foo-publisher`; pass `app-slug` if the App's slug differs.

```yaml
- uses: wyrd-company/repo.wyrd.foo/.github/actions/submit-release@<commit>
  with:
    manifest: release-manifest.json
    token: ${{ steps.app-repo-wyrd-foo.outputs.token }}
```

Callers that pass `app-client-id` and `app-private-key` instead keep working:
without `token`, the action mints its own token from them with the same
repository and permission. That path puts the App's private key on the
runner, which the organization's delivery standard does not allow, so use
`token`. The key inputs will be removed in a later change.

## Documentation

The publishing design, contract, and repository rulesets are described in
[package-repository-publishing](docs/technical-designs/package-repository-publishing.yml).
Development instructions are in [CONTRIBUTING.md](CONTRIBUTING.md).
