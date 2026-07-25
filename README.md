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

## RPM

Install the repository definition:

```console
sudo curl -fsSL https://repo.wyrd.foo/wyrd.repo -o /etc/yum.repos.d/wyrd.repo
```

The RPM repository verifies both package signatures and repository metadata
with an ASCII-armored copy of the same repository key at `pubkey.asc`.

## AUR

Prebuilt Arch packages are published to the AUR as `<product>-bin`.

## Documentation

The publishing design, contract, and repository rulesets are described in
[package-repository-publishing](docs/technical-designs/package-repository-publishing.yml).
Development instructions are in [CONTRIBUTING.md](CONTRIBUTING.md).
