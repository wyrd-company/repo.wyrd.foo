#!/usr/bin/env python3
# ---
# relationships:
#   verifies: package-repository-publishing
# ---

"""Sole-kill verification in throwaway copies; never mutate the active worktree."""

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = (
    Path(sys.argv[1]).resolve()
    if len(sys.argv) > 1
    else Path(__file__).resolve().parents[1]
)
ACTION = "scripts/submit-release.py"
VALIDATOR = "scripts/repository.py"

# Each row names the exact assertion that must fail, not merely a red runner.
MUTATIONS = [
    (
        "caller repository",
        ACTION,
        'source["repository"] != environment["GITHUB_REPOSITORY"]',
        "False",
        "test_foreign_source_repository_is_refused",
    ),
    (
        "source revision",
        ACTION,
        'source["commit"] != revision',
        "False",
        "test_pinned_source_revision_is_required",
    ),
    (
        "release inputs",
        ACTION,
        "environment.get(key) and environment[key] != manifest[field]",
        "False",
        "test_release_inputs_must_match",
    ),
    (
        "tag binding",
        ACTION,
        'target["type"] != "commit" or target["sha"] != source["commit"]',
        "False",
        "test_tag_must_resolve_to_source",
    ),
    (
        "tag peeling",
        ACTION,
        'while target["type"] == "tag":',
        "while False:",
        "test_annotated_tag_is_peeled_without_using_a_branch_alias",
    ),
    (
        "published release",
        ACTION,
        'release["tag_name"] != manifest["tag"] or release["draft"] is not False',
        "False",
        "test_draft_or_foreign_release_record_is_refused",
    ),
    (
        "asset pagination",
        ACTION,
        "if len(batch) < 100:",
        "if True:",
        "test_assets_after_first_page_are_checked",
    ),
    (
        "release-record digest",
        ACTION,
        "actual.get(name) != digest or declared.get(name) != digest",
        "declared.get(name) != digest",
        "test_each_asset_digest_and_name_is_bound_to_release_record",
    ),
    (
        "supplied digest",
        ACTION,
        "actual.get(name) != digest or declared.get(name) != digest",
        "actual.get(name) != digest",
        "test_supplied_asset_digest_must_match",
    ),
    (
        "unique assets",
        ACTION,
        "not isinstance(name, str) or name in result",
        "not isinstance(name, str)",
        "test_duplicate_asset_names_are_refused",
    ),
    (
        "Token repository count",
        ACTION,
        'authority["total_count"] != 1',
        "False",
        "test_wider_or_foreign_installation_token_scope_is_refused",
    ),
    (
        "Token repository identity",
        ACTION,
        "] != [DESTINATION]:",
        "] == []:",
        "test_wider_or_foreign_installation_token_scope_is_refused",
    ),
    (
        "immutable replay",
        ACTION,
        'or base64.b64decode(existing["content"]) != content',
        "or False",
        "test_conflicting_manifest_is_refused",
    ),
    (
        "replay state",
        ACTION,
        'state = "unchanged"',
        'state = "created"',
        "test_identical_replay_returns_same_identity_without_new_submission",
    ),
    (
        "original inbox addition on replay",
        ACTION,
        'commit = require_sha(history[0]["sha"])',
        "commit = head",
        "test_identical_replay_returns_same_identity_without_new_submission",
    ),
    (
        "redirect containment",
        ACTION,
        "build_opener(NoRedirect())",
        "build_opener()",
        "test_transport_refuses_redirects_before_a_second_origin",
    ),
    (
        "error redaction",
        ACTION,
        'else "Release submission refused: invalid input, authority, or release binding"',
        "else str(error)",
        "test_untrusted_input_is_not_echoed",
    ),
    (
        "closed manifest fields",
        VALIDATOR,
        "if actual != expected:",
        "if False:",
        "test_rejects_unknown_fields_at_every_privileged_boundary",
    ),
    (
        "artifact roles",
        VALIDATOR,
        "combination not in EXPECTED_ARTIFACTS or combination in combinations",
        "combination not in EXPECTED_ARTIFACTS",
        "test_requires_exact_artifact_roles",
    ),
    (
        "artifact extension",
        VALIDATOR,
        "if not filename.endswith(expected_suffix):",
        "if False:",
        "test_artifact_filename_must_match_format",
    ),
    (
        "asset URL confinement",
        VALIDATOR,
        'or url.netloc != "github.com"',
        "or False",
        "test_confines_download_to_exact_github_release",
    ),
    (
        "control characters",
        VALIDATOR,
        "if not value.isprintable():",
        "if False:",
        "test_rejects_control_characters",
    ),
    (
        "cycle detection",
        ACTION,
        "if tag_sha in visited:",
        "if True:",
        "test_annotated_tag_is_peeled_without_using_a_branch_alias",
    ),
    (
        "release identity",
        ACTION,
        "if type(release_id) is not int or release_id <= 0:",
        "if False:",
        "test_malformed_release_record_identity_is_refused",
    ),
    (
        "commit identity",
        ACTION,
        "if not isinstance(value, str) or not COMMIT.fullmatch(value):",
        "if False:",
        "test_invalid_inbox_commit_identity_is_refused",
    ),
    (
        "regular inbox file",
        ACTION,
        'existing["type"] != "file"',
        "False",
        "test_nonregular_or_unencoded_existing_manifest_is_refused",
    ),
    (
        "inbox encoding",
        ACTION,
        'existing["encoding"] != "base64"',
        "False",
        "test_nonregular_or_unencoded_existing_manifest_is_refused",
    ),
    (
        "canonical submission",
        ACTION,
        "content = canonical_bytes(manifest)",
        "content = json.dumps(manifest).encode()",
        "test_rpm_and_debian_submission_is_a_single_canonical_addition",
    ),
    (
        "identity digest",
        ACTION,
        "hashlib.sha256(content).hexdigest()",
        '"0" * 64',
        "test_rpm_and_debian_submission_is_a_single_canonical_addition",
    ),
    (
        "fixed repository",
        ACTION,
        'DESTINATION = "wyrd-company/repo.wyrd.foo"',
        'DESTINATION = "wyrd-company/another-tool"',
        "workflow:test_product_app_writes_only_the_inbox_flow",
    ),
    (
        "fixed inbox",
        ACTION,
        'INBOX = "release-manifests"',
        'INBOX = "main"',
        "workflow:test_product_app_writes_only_the_inbox_flow",
    ),
    (
        "App permission",
        ".github/actions/submit-release/action.yml",
        "permission-contents: write",
        "permission-contents: read",
        "contract:test_action_wiring_keeps_authority_and_runtime_closed",
    ),
    (
        "output wiring",
        ".github/actions/submit-release/action.yml",
        "steps.submit.outputs.submission-id",
        "steps.submit.outputs.manifest-path",
        "contract:test_action_wiring_keeps_authority_and_runtime_closed",
    ),
    (
        "credential agreement",
        "tests/fixtures/release-process/credentials.json",
        '"REPO_WYRD_FOO_PUBLISHER_APP_ID"',
        '"ANOTHER_APP_ID"',
        "contract:test_credentials_validate_and_agree_with_caller",
    ),
    (
        "in-tree caller source",
        "tests/fixtures/release-process/caller.yml",
        "uses: ./.github/actions/submit-release",
        "uses: wyrd-company/repo.wyrd.foo/.github/actions/submit-release@branch-only",
        "contract:test_in_tree_caller_executes_checked_out_action_runtime",
    ),
    (
        "recommended caller omits asset snapshot",
        "tests/fixtures/release-process/caller.yml",
        "        manifest: release-manifest.json",
        "        manifest: release-manifest.json\n        release-assets: '[]'",
        "contract:test_caller_reads_parent_release_environment_without_interpolation",
    ),
    (
        "parent release input",
        "tests/fixtures/release-process/caller.yml",
        'release["version"]',
        'release["tag"] + ".invalid"',
        "contract:test_caller_reads_parent_release_environment_without_interpolation",
    ),
]

MUTATIONS += [
    (
        "selected Action invokes checked-out runtime",
        ".github/actions/submit-release/action.yml",
        "../../../scripts/submit-release.py",
        "../../../scripts/repository.py",
        "contract:test_in_tree_caller_executes_checked_out_action_runtime",
    ),
    (
        "consumer example interface agreement",
        "docs/examples/submit-release.yml",
        "manifest: release-manifest.json",
        "manifest: other-manifest.json",
        "contract:test_published_example_agrees_with_development_caller",
    ),
    (
        "published SHA syntax",
        "tests/qualify_submission.py",
        r"@([0-9a-f]{40})",
        r"@(\S+)",
        "qualification:test_invalid_sha_has_no_published_identity",
    ),
    (
        "published version comment is required",
        "tests/qualify_submission.py",
        "text = path.read_text()",
        'text = path.read_text()\n    if " # " not in text:\n        text = text.rstrip() + " # 1.2.3\\n"',
        "qualification:test_missing_pin_version_comment_is_refused",
    ),
    (
        "published version syntax",
        "tests/qualify_submission.py",
        'VERSION = r"(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)\\.(?:0|[1-9][0-9]*)"',
        'VERSION = r"\\S+"',
        "qualification:test_invalid_version_has_no_published_identity",
    ),
    (
        "published reference must be YAML execution",
        "tests/qualify_submission.py",
        ' or references != [ACTION + "@" + pins[0][0]]',
        "",
        "qualification:test_action_reference_must_be_executable_yaml",
    ),
    (
        "single published pin",
        "tests/qualify_submission.py",
        "len(pins) != 1 or ",
        "",
        "qualification:test_non_executable_duplicate_pin_is_refused",
    ),
    (
        "publication part reference agreement",
        "tests/qualify_submission.py",
        'published_reference(consumer) != (revision, version)',
        "False",
        "qualification:test_publication_part_reference_mismatch_is_refused",
    ),
    (
        "release tag identifies pin",
        "tests/qualify_submission.py",
        "if tagged != revision:",
        "if False:",
        "qualification:test_release_tag_must_identify_pin",
    ),
    (
        "published pin reaches main",
        "tests/qualify_submission.py",
        'git(repository, "merge-base", "--is-ancestor", revision, "refs/heads/main")',
        'git(repository, "rev-parse", revision)',
        "qualification:test_tagged_branch_only_commit_is_refused",
    ),
    (
        "published runtime identity",
        "tests/qualify_submission.py",
        "if published != (root / relative).read_bytes():",
        "if False:",
        "qualification:test_each_published_runtime_file_must_match",
    ),
    (
        "remote tag authority",
        "tests/qualify_submission.py",
        '            f"refs/tags/{version}:refs/tags/{version}",\n        )',
        '        )\n        git(repository, "fetch", "--quiet", str(root), f"refs/tags/{version}:refs/tags/{version}")',
        "qualification:test_local_only_tag_cannot_qualify_remote_pin",
    ),
]

# The inventory is an agreement too, separate from the byte comparison itself.
MUTATIONS += [
    (
        "published runtime inventory " + relative,
        "tests/qualify_submission.py",
        '    "' + relative + '",\n',
        "",
        "qualification:test_each_published_runtime_file_must_match",
    )
    for relative in (
        ".github/actions/submit-release/action.yml",
        "scripts/submit-release.py",
        "scripts/repository.py",
    )
]


def main():
    if not MUTATIONS:
        print("MUTATION_RESULT=FAIL empty mutation roster")
        return 1
    failures = []
    with tempfile.TemporaryDirectory(prefix="submission-guards-") as directory:
        work = Path(directory) / "repo"
        shutil.copytree(
            ROOT,
            work,
            ignore=shutil.ignore_patterns(".git", ".tmp", "__pycache__", "*.pyc"),
        )
        environment = {
            key: value
            for key, value in os.environ.items()
            if not key.startswith("GIT_")
        }
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        baseline = subprocess.run(
            [
                sys.executable,
                "-m",
                "unittest",
                "-v",
                "test_submission_action",
                "test_repository",
                "test_workflow_contract",
                "test_submission_contract",
                "test_submission_qualification",
            ],
            cwd=work / "tests",
            env=environment,
            capture_output=True,
            text=True,
        )
        if baseline.returncode:
            print(baseline.stdout + baseline.stderr)
            return 1
        count = re.search(r"^Ran ([1-9][0-9]*) tests? in ", baseline.stderr, re.MULTILINE)
        if not count or not baseline.stderr.rstrip().endswith("OK"):
            print("MUTATION_RESULT=FAIL incomplete baseline")
            return 1
        print(f"BASELINE_RESULT=PASS tests={count.group(1)}")
        for label, relative, old, new, test in MUTATIONS:
            path = work / relative
            original = path.read_text()
            if original.count(old) != 1:
                failures.append(label)
                print(f"INVALID {label}: replacement count {original.count(old)}")
                continue
            if test.startswith("qualification:"):
                module, test = (
                    "test_submission_qualification.QualificationTests",
                    test.split(":", 1)[1],
                )
            elif test.startswith("workflow:"):
                module, test = (
                    "test_workflow_contract.WorkflowContractTests",
                    test.split(":", 1)[1],
                )
            elif test.startswith("contract:"):
                module, test = (
                    "test_submission_contract.ContractTests",
                    test.split(":", 1)[1],
                )
            else:
                module = (
                    "test_repository.ManifestTests"
                    if relative == VALIDATOR
                    else "test_submission_action.SubmissionTests"
                )
            path.write_text(original.replace(old, new))
            try:
                result = subprocess.run(
                    [sys.executable, "-m", "unittest", "-v", module + "." + test],
                    cwd=work / "tests",
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                output = result.stdout + result.stderr
                killed = result.returncode != 0 and re.search(
                    r"^FAIL: " + re.escape(test) + r"[ (]", output, re.MULTILINE
                )
                if not killed:
                    failures.append(label)
                    print(f"SURVIVED/INVALID {label}\n{output}")
                else:
                    print(f"KILLED {label}: {test}")
            finally:
                path.write_text(original)
            if path.read_text() != original:
                raise AssertionError("Mutation restoration failed")
    print(
        f"MUTATION_RESULT={'FAIL' if failures else 'PASS'} killed={len(MUTATIONS) - len(failures)}/{len(MUTATIONS)}"
    )
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
