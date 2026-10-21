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
and all three runtime files: `.github/actions/submit-release/action.yml`,
`scripts/submit-release.py`, and `scripts/repository.py`. The last file is also
used by the publisher workflow. Every change to any of these three files needs
a `submission-action` Change Intent. The unit has no version projections. Its
primary tag is a bare semantic version, and `release-record: required` means
each release tag must have a published GitHub Release with the same tag.
Intentional does not create that GitHub Release or deploy the publisher service.

For the first release, the approved source pull request establishes the initial
unit configuration and Change Intent. After that merge, measure the release
from the reviewed `main` commit. If a baseline is missing, create and verify
the candidate baseline only in a disposable clone, then run `intentional
status`, `intentional check`, and `intentional plan` there. Save the command
outputs, plan digest, computed version, and reviewed commit. Do not push the
candidate tag. Do not carry first-release values into later releases; measure
each later release from its current `main` and plan output.

Before public release operations, Bob may approve a release approval naming the
reviewed source tree, exact version, any baseline tag, and the ordered public
operations, based on that measurement. After approval, create and verify the
baseline on the reviewed `main` commit if needed, then rerun status and plan.
If the tree, plan digest, version, or operation order differs from the
approval, stop before apply or publication and seek approval for the revised
release. Approval covers only the named operations. Agents may perform the
approved operations without separate approval for each routine command. Bob
retains final review and merge authority for each pull request to `main`.

Use this order for the first release and each later Action release:

1. Review and merge the prepared source pull request to `main`. Check out the
   merged `main` commit and fetch its tags. Measure the plan as described above.
   If the unit has no matching baseline tag, preview the baseline using the
   measured baseline version:

   ```console
   intentional tag --baseline --version submission-action=<baseline> --dry-run
   ```

   Include the reviewed tree, measured plan, version, any baseline tag, and
   ordered public operations in the release approval before any public tag
   push. After approval, create and verify the baseline on that `main` commit
   with `intentional tag --baseline --version submission-action=<baseline>` and
   `intentional verify <baseline> --json`. A baseline is not a release and has
   no GitHub Release. Push it only if that operation is named in the approval.
   Run `intentional check`, then rerun `intentional status` and `intentional
   plan`; confirm the output still matches the approval.
2. Save the approved `intentional plan` output as `release-plan.json`. Run
   `intentional apply --dry-run`, then `intentional apply`. Inspect the
   generated changelog and consumed intent. Commit only those generated
   release changes in one release pull request.
3. The saved plan binds to its source commit: the single apply commit must be
   its direct child, with no merge commit. Bob's merge of the release pull
   request must put that apply commit directly on `main`, with no other commit
   between the planned source and apply commit. From the merged
   release commit, preview the primary tag with
   `intentional tag --plan release-plan.json --dry-run`, then create it with
   the same command without `--dry-run`. Verify it with
   `intentional verify <tag> --json`; confirm that it peels to the reviewed
   commit on remote `main`; then push that exact tag only if the release
   approval names that push.
4. If `main` moves after planning and before the apply commit is merged, or if
   the saved plan no longer names the direct parent of the apply commit, stop.
   Discard the saved plan and release branch. Restart from the current `main`
   at step 1 or 2, make a new plan and release pull request, and renew approval
   for any changed tree, version, or ordered public operations. Do not force a
   stale plan onto `main`.
5. Create a published GitHub Release for the same bare tag and version after
   the primary tag is pushed. Read it back and confirm its tag, reviewed source
   commit, and published (not draft) state. The unit requires this release
   record. No workflow in this repository creates it. The existing
   `publish.yml` workflow starts on `repository_dispatch` with event
   `release-manifest` or `workflow_dispatch`; a `main` merge or tag push does
   not start it. Recheck the actual workflow triggers on target `main` before
   seeking release approval.
6. Once the approved primary tag and its GitHub Release are available, prepare
   pin updates in two repositories: update `docs/examples/submit-release.yml`
   here, and update the repo.wyrd.foo publication part in `wyrd-company/.github`.
   Use the same full `main` commit SHA and exact bare tag in both. Edit the
   publication part source and render its workflow; do not hand-edit the
   rendered workflow. Set `PUBLICATION_CALLER` to that rendered workflow and
   run:

   ```console
   task submission:qualify PUBLICATION_CALLER=/path/to/rendered-release.yml
   ```

   The gate must report `QUALIFICATION_RESULT=PASS` before either pin update
   is merged. Submit one pull request in each repository for Bob's review and
   merge. Keep the example placeholders until the approved tag and GitHub
   Release exist.

The release approval must name each public baseline or release tag push and
GitHub Release creation. Agents may perform only those named operations after
approval. Afterward, record the exact tag, peeled commit, version, and
published release readback. Never treat a local tag as public qualification.

## Submission Action release/UAT gate

The consumer example is `docs/examples/submit-release.yml`. Its explicit
`PUBLISHED_MAIN_SHA` and `RELEASE_TAG` placeholders mean it is unqualified.
Keep them until an approved release tag and its GitHub Release exist on public
`main`.

After publication, replace the placeholders with the full `main` commit SHA
and exact bare release tag. Set the publication part in `wyrd-company/.github`
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
release/UAT. Record the qualified SHA, tag, rendered publication file, and gate
result in the release/UAT handoff. Until then, live published qualification is
pending.

To verify integration, export the committed tree to a new Git repository,
commit it once on `main`, and clone only that `main` with `--no-local --no-tags`.
Run `task check` and `task submission:guards` in that clone. Do not copy the
original `.git` or fetch its task/epic branches. This simulates squash
integration without keeping branch-only objects that could hide a
self-reference.

## Signing-key rotation

After a signing-key rotation, import the public key corresponding to
`REPO_WYRD_FOO_GPG_KEY` into an isolated GnuPG home, verify its full primary
fingerprint with the key owner, and set `SIGNING_FINGERPRINT` to that value.
Create the reviewed armored input and checked-in binary keyring with:

```console
gpg --batch --armor --export "$SIGNING_FINGERPRINT" >pubkey.asc
gpg --batch --yes --dearmor --output pubkey.gpg pubkey.asc
```
