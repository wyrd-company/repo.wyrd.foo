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

Use an isolated interpreter for the test dependencies so that Task finds them:

```console
python3 -m venv .tmp/test-venv
. .tmp/test-venv/bin/activate
python3 -m pip install jsonschema PyYAML
task check
task submission:guards
```

`submission:guards` mutates isolated copies and requires each named assertion
to fail. The active checkout stays unchanged.

The executable `tests/fixtures/release-process/caller.yml` uses
`./.github/actions/submit-release`. Its Bash invocation selects the runtime in
this checkout; `test_in_tree_caller_executes_checked_out_action_runtime` proves
that selection. Update the fixture with the Action interface. No intermediate
runtime commit, branch pin, or network fetch is needed for development checks.
The schema provenance file records the source revision and digests; update it
only when adopting a new contract schema.

## Submission Action release/UAT gate

The consumer example is `docs/examples/submit-release.yml`. Its explicit
`PUBLISHED_MAIN_SHA` and `RELEASE_TAG` placeholders mean it is unqualified.
Keep those placeholders until an approved release tag exists on the remote.
Public tag and release publication require the user's approval.

After that release exists, replace the placeholders with its full main commit
SHA and exact bare release tag. Set the publication part in `wyrd-company/.github`
to the identical reference:

```text
wyrd-company/repo.wyrd.foo/.github/actions/submit-release@<main-SHA> # <release-tag>
```

Use the tag's peeled commit, including for annotated tags. Do not use the epic
head, an invented version, or a moving branch/tag in `uses`.

Render the publication part's workflow and run the required release/UAT gate:

```console
task submission:qualify PUBLICATION_CALLER=/path/to/rendered-release.yml
```

This gate fetches only remote `main` and the named tag into a temporary object
store. It requires one identical published reference in each file, a tag that
peels to the pin, main reachability, and byte identity of `action.yml`,
`submit-release.py`, and `repository.py` with the checkout under qualification.
A local tag cannot satisfy it. Only `QUALIFICATION_RESULT=PASS` completes this
gate. Missing files, placeholders, missing remote refs, mismatches, and
interruption cannot complete it. Named cases in
`tests/test_submission_qualification.py` cover these boundaries;
`submission:guards` proves the load-bearing comparisons.

Run this gate before accepting the published example or publication part at
release/UAT. A green development `task check` does not qualify a public pin.
Record the qualified SHA, tag, rendered publication file, and gate result in
the release/UAT handoff. Until then, live published qualification is pending.

To verify integration, export the committed tree to a new Git repository,
commit it once on `main`, and clone only that `main` with `--no-local --no-tags`.
Run `task check` and `task submission:guards` in that clone. Do not copy the
original `.git` or fetch its task/epic branches. This simulates squash integration
without keeping branch-only objects that could hide a self-reference.

## Signing-key rotation

After a signing-key rotation, import the public key corresponding to
`REPO_WYRD_FOO_GPG_KEY` into an isolated GnuPG home, verify its full primary
fingerprint with the key owner, and set `SIGNING_FINGERPRINT` to that value.
Create the reviewed armored input and checked-in binary keyring with:

```console
gpg --batch --armor --export "$SIGNING_FINGERPRINT" >pubkey.asc
gpg --batch --yes --dearmor --output pubkey.gpg pubkey.asc
```
