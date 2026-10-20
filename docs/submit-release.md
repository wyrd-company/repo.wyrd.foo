---
relationships:
  references:
    - package-repository-publishing
    - use-release-manifest-handoffs
---

# Submit a package release

Use `.github/actions/submit-release` as a step of a release-process publish job.
Pin the Action to a full commit SHA with a version comment. The caller needs
`contents: read` for its release record. The separate publisher owns signing,
storage, AUR credentials, and consumer observation.

The runner needs Python 3.10 or later and Bash. No package installation is
required. The Action uses the code at its pinned revision, not scripts from
the caller checkout or the inbox.

## Inputs

- `manifest` (required): path to the package selection manifest in the caller
  workspace. It follows `schemas/release-manifest.schema.json`: product,
  package metadata, APT/RPM/AUR destinations, and six Linux artifacts (archive,
  Debian, and RPM for amd64 and arm64). This is the publisher's supported
  selection; the Action does not build, change, or sign the packages.
- `tag`: released unit tag. Defaults to the manifest tag.
- `version`: released unit version. Defaults to the manifest version.
- `release-assets`: JSON array of asset objects with `name` and `digest`
  (`sha256:<64 lowercase hex>`). Extra GitHub asset fields are accepted. When
  omitted, the Action reads the asset list from the release record. When
  supplied, both the supplied and fetched digests must match the manifest.
  This is an optional cross-check for callers that hold release-record digests
  from an independent source. The recommended caller omits it.
- `app-client-id` (required): client ID of the publisher GitHub App.
- `app-private-key` (required): private key of that App.

Read `tag` and `version` from the unit's entry in the parent's `RELEASE_JSON`.
The parent sets `RELEASE_SOURCE_SHA`; the manifest source commit must match it.
For a caller outside the template, the Action uses `GITHUB_SHA` when
`RELEASE_SOURCE_SHA` is absent. The manifest repository must match
`GITHUB_REPOSITORY`, and its tag must resolve to that commit. Tags and versions
follow the manifest's stable, bare SemVer contract.

The Action reads the published GitHub Release and its paginated asset list
using the caller's GitHub token. Every selected asset name and SHA-256 digest
must match. Missing digests, draft records, and mismatches fail before inbox
writes. See `test_each_asset_digest_and_name_is_bound_to_release_record`,
`test_assets_after_first_page_are_checked`, and
`test_pinned_source_revision_is_required` in
[the executable fixtures](../tests/test_submission_action.py).

The [caller fixture](../tests/fixtures/release-process/caller.yml) shows the
part publish-job value. It takes tag and version from `RELEASE_JSON`; the
Action fetches release asset names and digests from GitHub. The fixture assumes
its package selection manifest is already in the workspace; the part that
prepares that file is outside this Action. This integration fixture uses a
branch-local pin and a provisional version comment, not a published tag.
Production callers need a commit on `main` and its released version comment.

## Credentials and authority

Declare these credentials in the part's `credentials` interface:

- `REPO_WYRD_FOO_PUBLISHER_APP_ID`: `kind: secret`; purpose: create the package
  inbox installation token.
- `REPO_WYRD_FOO_PUBLISHER_PRIVATE_KEY`: `kind: secret`; purpose: authenticate
  the package inbox App.

An optional `environment` declaration must name the environment that stores
those values; the publish job must use that environment. The [credential fixture](../tests/fixtures/release-process/credentials.json)
uses repository or organization secrets, with no environment.

The Action requests a Contents-write installation token only for
`wyrd-company/repo.wyrd.foo`. It checks that this token covers only that
repository. This check cannot reveal the full repository selection of the App
installation. The inbox is fixed to `release-manifests`; paths derive
from the validated product and version. No input selects a destination
repository, branch, code revision, API endpoint, or shell command. App branch
rules described in the publishing design remain required: only this App can
update the inbox, and it cannot bypass `main` protection. Operators must keep
the App installation limited to this repository. Epic UAT must verify the
installation repository selection and these branch rules; the token-scope
check does not establish either.

The caller's GitHub token stays read-only. The App token is used only for
token-scope and inbox reads, a create-only Contents API request, and the fixed
`release-manifest`
dispatch. Authenticated API requests do not follow redirects. Provider error
text and input values are excluded from failure output. Authority and redirect
checks are exercised by `test_wider_or_foreign_installation_token_scope_is_refused`
and `test_redirect_never_replays_credentials`.

## Outputs and retry

- `submission-id`: `releases/<product>/<version>.json@sha256:<digest>`;
  digest of the canonical manifest bytes.
- `manifest-path`: canonical inbox path.
- `inbox-commit`: immutable commit that added the manifest.
- `state`: `created` or `unchanged`.

Success means the immutable handoff was submitted and its publication dispatch
was accepted. It does not mean the packages are visible to consumers.

An identical replay succeeds without a new manifest or commit. A different
manifest for the same product/version fails. Each replay dispatches the same
commit and path so that a failed publisher can resume. If dispatch fails after
submission, rerun the job; outputs are written only after dispatch succeeds.
Concurrent create conflicts are retried up to four times; a later workflow
retry can resume a committed submission. See
`test_identical_replay_returns_same_identity_without_new_submission`,
`test_conflicting_manifest_is_refused`, and
`test_dispatch_failure_replay_does_not_create_another_commit`.
