---
relationships:
  references:
    - package-repository-publishing
---

# Contributing

Changes to `main` require a pull request; an owner performs the final merge.

## Development

The checks need Python with `jsonschema` and `PyYAML`, Task, ShellCheck,
`rumdl`, `ryl`, and Docker. The submission Action itself uses only the Python
standard library.

```console
task check
task submission:guards
```

`submission:guards` mutates isolated copies and requires each named assertion
to fail. The active checkout stays unchanged.

When Action runtime files change, commit them before updating the caller
fixture pin. The contract test compares the pinned files with the files under
test. The schema provenance file records the source revision and digests;
update it only when adopting a new contract schema.

## Signing-key rotation

After a signing-key rotation, import the public key corresponding to
`REPO_WYRD_FOO_GPG_KEY` into an isolated GnuPG home, verify its full primary
fingerprint with the key owner, and set `SIGNING_FINGERPRINT` to that value.
Create the reviewed armored input and checked-in binary keyring with:

```console
gpg --batch --armor --export "$SIGNING_FINGERPRINT" >pubkey.asc
gpg --batch --yes --dearmor --output pubkey.gpg pubkey.asc
```
