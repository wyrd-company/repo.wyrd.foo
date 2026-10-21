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

## Release the Submission Action

The `submission-action` release unit covers `.github/actions/submit-release`
and the runtime files `scripts/submit-release.py` and `scripts/repository.py`.
It has no version projections. Its primary tag is the bare semantic version,
and `release-record: required` means each release tag must have a published
GitHub Release with the same tag. The measured first release plan is `0.1.0`
from baseline `0.0.0`; the approved source tree and current plan must confirm
those values before apply. Intentional writes neither the GitHub Release nor a
publisher-service deployment.

Before a public release operation, Bob approves a release record that names the
reviewed source tree, exact version, any required baseline tag, and the ordered
public operations. That approval covers only the named operations. If the
source tree, version, or operation order changes, stop and request approval for
the revised release record. Bob retains final review and merge authority for
every pull request to `main`.

Use this order for the first release and every later Action release:

1. Bob reviews and merges the prepared source pull request to `main`.
2. Check out the merged `main` commit, fetch its tags, and run
   `intentional status`. If the unit has no matching baseline tag, preview it
   with `intentional tag --baseline --version submission-action=0.0.0
   --dry-run`. After review, create it with the same command without
   `--dry-run`, then verify it with `intentional verify 0.0.0 --json`. A
   baseline is not a release and needs no GitHub Release. Push it only when the
   approved release record names that public tag operation. Run
   `intentional check` after baseline verification.
3. Save `intentional plan` output as `release-plan.json`. Review its digest,
   source tree, release notes, and computed version against the approved
   release record. Run `intentional apply --dry-run`, then `intentional apply`.
   Inspect the changelog and consumed intent. Submit those generated changes
   in a pull request; Bob reviews and merges it before the release tag is
   made.
4. From the merged release commit, preview the primary tag with
   `intentional tag --plan <saved-plan> --dry-run`, then create it with the same
   command without `--dry-run`. Verify it with
   `intentional verify <tag> --json`. Confirm that it peels to the reviewed
   commit on remote `main`, then push that exact tag after approval.
5. Create a published GitHub Release for the same bare tag and version. Read it
   back and confirm its tag name, reviewed source commit, and published (not
   draft) state. This is required by the unit policy; no workflow in this
   repository creates the release record. The existing `publish.yml` workflow
   starts on `repository_dispatch` with event `release-manifest` or
   `workflow_dispatch`; a `main` merge or tag push does not start it.
6. After the real tag and GitHub Release are available, update a candidate
   `docs/examples/submit-release.yml` and the rendered publication workflow to
   use the same full main commit SHA and exact bare tag:

   ```text
   wyrd-company/repo.wyrd.foo/.github/actions/submit-release@<main-SHA> # <tag>
   ```

   Render the publication workflow and run
   `task submission:qualify PUBLICATION_CALLER=<rendered-workflow>`. The gate
   must report `QUALIFICATION_RESULT=PASS` before either pin update is merged.
   Submit both pin updates in a pull request for Bob's review and merge. Keep
   the example's placeholders until the actual approved tag and release record
   exist.

The release authorization must identify each public tag push and GitHub Release
creation. Agents may perform those named operations after approval without a
separate command-by-command approval. Recheck the workflow triggers on the
target `main` before approval because a later workflow change can add a push
side effect. The submission Action release does not deploy the publisher
service.

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
