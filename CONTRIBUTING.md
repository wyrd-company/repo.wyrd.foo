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
from the reviewed `main` commit. If a baseline is missing, create and verify the
candidate baseline only in a disposable clone, then run `intentional status`,
`intentional check`, and `intentional plan` there. Save the command outputs,
plan digest, computed version, and reviewed commit. Do not push the candidate
tag. Do not carry first-release values into later releases; measure each later
release from its current `main` and plan output.

Save each exact `intentional plan` output at an explicit absolute
operator-selected path outside every source, release, and scratch
checkout. For example, `/tmp/repo-wyrd-foo-release-plan.json`. Record the path
with the plan measurement and keep that file available for the tag step. Never
put a plan file inside a checkout that will be staged for tree comparison.

Before public release operations, Bob may approve a release plan naming the
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
   Run `intentional check`, then rerun `intentional status` and
   `intentional plan`; confirm the output still matches the approval and
   save it at the recorded external plan path.
2. Run `intentional apply --dry-run`, then `intentional apply`. Inspect the
   generated changelog and consumed intent. Commit only those generated release
   changes in one release pull request.
3. Before Bob merges the release pull request, read remote `main` again and
   confirm its commit is exactly the source commit recorded in the saved plan.
   The apply commit must be that source commit's direct child, with no merge
   commit. If remote `main` moved before the merge, do not merge this release
   pull request. Discard its saved plan and release branch, then rebuild the
   release branch from the new `main`, repeat the measurement and apply steps,
   and prepare a new release pull request. Renew Bob's approval for the new
   reviewed tree, plan digest, version, and ordered public operations. Do not
   force a stale plan onto `main`.
4. After the release pull request merges, record the merged release commit `M`
   and its direct parent `X`. If `X` is the source commit in the approved plan,
   keep that original plan in force. If `X` differs because a squash or rebase
   merge landed after `main` moved, treat the old plan as stale. From a checkout
   at `M`, confirm the old plan refuses with
   `intentional tag --plan <old-plan-path> --dry-run`. Do not select a plan made
   at `M`: the merge consumed the intent, so that plan has no release units.
   Instead, make a disposable checkout at `X`, with the existing baseline tags
   available. Run `intentional check`, `intentional status`, and
   `intentional plan` there, saving the new plan outside every scratch checkout
   at a new absolute path. Confirm that it contains the release applied in
   `M`. Run `intentional apply --dry-run`, then run `intentional apply` only in
   that disposable checkout. Run `task docs:check` on its generated tree. Stage
   the complete generated result there and compare its tree ID with `M`'s tree
   ID; the trees must match exactly. If the old plan does not refuse, the new
   plan is empty, the checks fail, or the trees differ, stop and contact Bob or
   the repository maintainer. Do not apply, revert, or repair the release on
   `main`.

   A plan measured at `X` has a new source commit and digest. Renew Bob's
   approval for that source, digest, version, merged release commit `M`, and
   ordered public operations. The unchanged normal plan keeps its existing
   approval when its reviewed tree, digest, version, and operations still
   match.
5. Use one tag procedure for either plan path: the approved original plan when
   `X` is its source commit, or the renewed recovery plan when stale-merge
   recovery was required. Check out `M` and set `TAG` to the bare tag in the
   plan. Refresh the remote-tracking ref with
   `git fetch origin refs/heads/main:refs/remotes/origin/main`, then confirm
   `git merge-base --is-ancestor "$M" origin/main` succeeds. Preview the
   primary tag from the plan in force with
   `intentional tag --plan <plan-path> --dry-run`. Confirm that the version and
   tag match the applicable Bob approval. Immediately before creating the tag,
   refresh `origin/main` with the same fetch command and confirm the ancestry
   command succeeds again. Then run `intentional tag --plan <plan-path>` and
   verify it with
   `intentional verify $TAG --json`. Confirm the local tag peels to exactly `M`.
   Push only that tag, and only when the currently valid Bob approval names the
   push:

   ```console
   git push origin "refs/tags/$TAG:refs/tags/$TAG"
   ```

   Read the pushed tag back with
   `git ls-remote --tags origin "refs/tags/$TAG" "refs/tags/$TAG^{}"`.
   Confirm that the remote tag object ID matches `git rev-parse
   refs/tags/$TAG` and that its peeled ID matches `git rev-parse
   refs/tags/$TAG^{}` and equals `M`. Fetch remote `main` again with the same
   fetch command and confirm the ancestry command still succeeds. Stop if a
   remote tag row is missing or does not match. The normal path uses its
   unchanged approval; the recovery path
   uses its renewed approval. Never tag `M` with the stale plan from before the
   merge.
6. Create a published GitHub Release for the same bare tag and version after
   the primary tag is pushed. Read it back and confirm its tag, reviewed source
   commit, and published (not draft) state. The unit requires this release
   record. No workflow in this repository creates it. The existing
   `publish.yml` workflow starts on `repository_dispatch` with event
   `release-manifest` or `workflow_dispatch`; a `main` merge or tag push does
   not start it. Recheck the actual workflow triggers on target `main` before
   seeking release approval.
7. Once the approved primary tag and its GitHub Release are available, prepare
   pin updates in two repositories: update `docs/examples/submit-release.yml`
   here, and update the repo.wyrd.foo publication part in `wyrd-company/.github`.
   Use the same full `main` commit SHA and exact bare tag in both. Edit the
   publication part source and render its workflow; do not hand-edit the
   rendered workflow. Set `PUBLICATION_CALLER` to that rendered workflow and
   run:

   ```console
   task submission:qualify PUBLICATION_CALLER=/path/to/rendered-release.yml
   ```

   The gate must report `QUALIFICATION_RESULT=PASS` before either pin update is
   merged. Submit one pull request in each repository for Bob's review and
   merge. Keep the example placeholders until the approved tag and GitHub
   Release exist.

The release approval must name each public baseline or release tag push and
GitHub Release creation. Agents may perform only those named operations after
approval. Afterward, record the exact tag, peeled commit, version, and published
release readback. Never treat a local tag as public qualification.

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
