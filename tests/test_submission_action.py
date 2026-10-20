# ---
# relationships:
#   verifies: package-repository-publishing
# ---

from __future__ import annotations

import base64
import copy
import importlib.util
import io
import email.message
import urllib.response
import json
import os
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location(
    "submission", ROOT / "scripts/submit-release.py"
)
submission = importlib.util.module_from_spec(spec)
spec.loader.exec_module(submission)


class InboxAPI:
    """GitHub REST fixture backed by real single-parent Git commits."""

    def __init__(self, root, manifest):
        self.root = root
        self.manifest = manifest
        self.git("init", "--quiet", "--initial-branch=release-manifests")
        self.git("config", "user.name", "Package Test")
        self.git("config", "user.email", "packages@example.invalid")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Initialize inbox")
        self.calls = []
        self.authority = {
            "total_count": 1,
            "repositories": [{"full_name": submission.DESTINATION}],
        }
        self.assets = [
            {"name": a["filename"], "digest": "sha256:" + a["sha256"]}
            for a in manifest["artifacts"]
        ]
        self.release = {"id": 12, "tag_name": manifest["tag"], "draft": False}
        self.tag_commit = manifest["source"]["commit"]
        self.annotated = False
        self.head_override = None
        self.tag_cycle = False
        self.existing_type = "file"
        self.existing_encoding = "base64"
        self.denied = None
        self.denied_method = None
        self.conflicts = 0
        self.dispatch_failure = False
        self.created = 0
        self.dispatches = []

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True
        ).strip()

    def open(self, request):
        self.calls.append(request)
        url = urllib.parse.urlsplit(request.full_url)
        assert url.scheme == "https" and url.netloc == "api.github.com"
        path, query, method = url.path, urllib.parse.parse_qs(url.query), request.method
        auth = request.get_header("Authorization")
        if self.denied == auth or self.denied_method == method:
            raise urllib.error.HTTPError(
                request.full_url,
                403,
                "untrusted-secret",
                {},
                io.BytesIO(b"untrusted-secret"),
            )
        source = "/repos/" + self.manifest["source"]["repository"]
        target = "/repos/" + submission.DESTINATION
        if path.startswith(source):
            assert auth == "Bearer source-test-token"
        else:
            assert auth == "Bearer app-test-token"
        if path == "/installation/repositories":
            result = self.authority
        elif path == source + "/git/ref/tags/" + self.manifest["tag"]:
            result = {
                "object": {
                    "sha": "a" * 40 if self.annotated else self.tag_commit,
                    "type": "tag" if self.annotated else "commit",
                }
            }
        elif path == source + "/git/tags/" + "a" * 40:
            result = {
                "object": {
                    "sha": "a" * 40 if self.tag_cycle else self.tag_commit,
                    "type": "tag" if self.tag_cycle else "commit",
                }
            }
        elif path == source + "/releases/tags/" + self.manifest["tag"]:
            result = self.release
        elif path.startswith(source + "/releases/") and path.endswith("/assets"):
            page = int(query["page"][0])
            result = self.assets[(page - 1) * 100 : page * 100]
        elif path == target + "/git/ref/heads/release-manifests":
            result = {
                "object": {"sha": self.head_override or self.git("rev-parse", "HEAD")}
            }
        elif path.startswith(target + "/contents/"):
            relative = path.removeprefix(target + "/contents/")
            destination = self.root / relative
            if method == "GET":
                try:
                    content = subprocess.check_output(
                        [
                            "git",
                            "-C",
                            str(self.root),
                            "show",
                            query["ref"][0] + ":" + relative,
                        ],
                        stderr=subprocess.DEVNULL,
                    )
                except subprocess.CalledProcessError:
                    raise urllib.error.HTTPError(
                        request.full_url, 404, "Missing", {}, None
                    ) from None
                result = {
                    "type": self.existing_type,
                    "encoding": self.existing_encoding,
                    "content": base64.b64encode(content).decode(),
                }
            else:
                assert method == "PUT"
                if self.conflicts:
                    self.conflicts -= 1
                    raise urllib.error.HTTPError(
                        request.full_url, 409, "Conflict", {}, None
                    )
                body = json.loads(request.data)
                assert body["branch"] == "release-manifests" and "sha" not in body
                if destination.exists():
                    raise urllib.error.HTTPError(
                        request.full_url, 422, "Already exists", {}, None
                    )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(base64.b64decode(body["content"]))
                self.git("add", "--", relative)
                self.git("commit", "--quiet", "-m", body["message"])
                self.created += 1
                result = {
                    "commit": {
                        "sha": self.head_override or self.git("rev-parse", "HEAD")
                    }
                }
        elif path == target + "/commits":
            result = [
                {
                    "sha": self.git(
                        "log",
                        query["sha"][0],
                        "-1",
                        "--format=%H",
                        "--",
                        query["path"][0],
                    )
                }
            ]
        elif path == target + "/dispatches":
            if self.dispatch_failure:
                raise urllib.error.HTTPError(
                    request.full_url, 503, "Unavailable", {}, None
                )
            self.dispatches.append(json.loads(request.data))
            result = None
        else:
            raise AssertionError((method, request.full_url))
        return (
            io.BytesIO(json.dumps(result).encode())
            if result is not None
            else io.BytesIO()
        )


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest = json.loads(
            (ROOT / "tests/fixtures/sample-tool-1.2.3.json").read_text()
        )
        inbox = self.root / "inbox"
        inbox.mkdir()
        self.server = InboxAPI(inbox, self.manifest)
        self.environment = {
            "MANIFEST": str(self.root / "manifest.json"),
            "GITHUB_OUTPUT": str(self.root / "output"),
            "GITHUB_REPOSITORY": self.manifest["source"]["repository"],
            "GITHUB_SHA": self.manifest["source"]["commit"],
            "RELEASE_SOURCE_SHA": self.manifest["source"]["commit"],
            "RELEASE_TAG": "1.2.3",
            "RELEASE_VERSION": "1.2.3",
            "RELEASE_ASSETS": json.dumps(self.server.assets),
            "SOURCE_TOKEN": "source-test-token",
            "APP_TOKEN": "app-test-token",
        }
        self.errors = io.StringIO()

    def run_action(self):
        Path(self.environment["MANIFEST"]).write_text(json.dumps(self.manifest))
        with patch.dict(os.environ, self.environment, clear=True), patch.object(
            urllib.request.OpenerDirector, "open", self.server.open
        ), redirect_stderr(self.errors):
            result = submission.main()
        return result

    def output(self):
        return dict(
            line.split("=", 1)
            for line in Path(self.environment["GITHUB_OUTPUT"]).read_text().splitlines()
        )

    def assert_refused(self):
        self.assertEqual(self.run_action(), 1)
        self.assertEqual(self.server.created, 0)
        self.assertEqual(self.server.dispatches, [])
        self.assertFalse(Path(self.environment["GITHUB_OUTPUT"]).exists())

    def test_rpm_and_debian_submission_is_a_single_canonical_addition(self):
        self.assertEqual(self.run_action(), 0, self.errors.getvalue())
        output = self.output()
        submitted = self.server.root / output["manifest-path"]
        self.assertEqual(
            submitted.read_bytes(), submission.canonical_bytes(self.manifest)
        )
        formats = {a["format"] for a in json.loads(submitted.read_bytes())["artifacts"]}
        self.assertTrue({"deb", "rpm"} <= formats)
        commit = output["inbox-commit"]
        self.assertEqual(
            self.server.git(
                "diff-tree", "--no-commit-id", "--name-status", "-r", commit
            ),
            "A\t" + output["manifest-path"],
        )
        self.assertEqual(
            len(self.server.git("rev-list", "--parents", "-n1", commit).split()), 2
        )
        self.assertEqual(len(self.server.dispatches), 1)
        self.assertEqual(
            self.server.dispatches[0]["client_payload"],
            {"commit": commit, "path": output["manifest-path"]},
        )
        self.assertEqual(output["state"], "created")
        self.assertEqual(output["manifest-path"], "releases/sample-tool/1.2.3.json")
        self.assertEqual(
            output["submission-id"],
            output["manifest-path"]
            + "@sha256:"
            + submission.hashlib.sha256(submitted.read_bytes()).hexdigest(),
        )

    def test_identical_replay_returns_same_identity_without_new_submission(self):
        self.assertEqual(self.run_action(), 0)
        first = self.output()
        self.manifest = dict(reversed(list(self.manifest.items())))
        self.assertEqual(self.run_action(), 0)
        second = self.output()
        for key in ("submission-id", "inbox-commit", "manifest-path"):
            self.assertEqual(first[key], second[key])
        self.assertEqual(second["state"], "unchanged")
        self.assertEqual(self.server.created, 1)
        self.assertEqual(self.server.dispatches[0], self.server.dispatches[1])

    def test_conflicting_manifest_is_refused(self):
        self.assertEqual(self.run_action(), 0)
        self.manifest["package"]["description"] = "Changed description"
        self.assertEqual(self.run_action(), 1)
        self.assertEqual(self.server.created, 1)
        self.assertEqual(len(self.server.dispatches), 1)

    def test_each_asset_digest_and_name_is_bound_to_release_record(self):
        for index in range(6):
            with self.subTest(index=index):
                original = self.server.assets[index].copy()
                self.server.assets[index]["digest"] = "sha256:" + "0" * 64
                self.assert_refused()
                self.server.assets[index] = original
                self.server.assets[index]["name"] = "unselected-file"
                self.assert_refused()
                self.server.assets[index] = original

    def test_supplied_asset_digest_must_match(self):
        declared = copy.deepcopy(self.server.assets)
        declared[0]["digest"] = "sha256:" + "0" * 64
        self.environment["RELEASE_ASSETS"] = json.dumps(declared)
        self.assert_refused()

    def test_assets_after_first_page_are_checked(self):
        extra = [{"name": f"unselected-{i}", "digest": None} for i in range(100)]
        self.server.assets = extra + self.server.assets
        self.assertEqual(self.run_action(), 0)
        self.assertTrue(any("page=2" in r.full_url for r in self.server.calls))

    def test_duplicate_asset_names_are_refused(self):
        self.server.assets.append(self.server.assets[0])
        self.assert_refused()

    def test_missing_digest_is_refused(self):
        self.server.assets[0]["digest"] = None
        self.assert_refused()

    def test_pinned_source_revision_is_required(self):
        self.environment["RELEASE_SOURCE_SHA"] = "f" * 40
        self.assert_refused()

    def test_foreign_source_repository_is_refused(self):
        self.environment["GITHUB_REPOSITORY"] = "wyrd-company/another-tool"
        self.assert_refused()

    def test_annotated_tag_is_peeled_without_using_a_branch_alias(self):
        self.server.annotated = True
        self.assertEqual(self.run_action(), 0)
        self.assertTrue(any("/git/tags/" in r.full_url for r in self.server.calls))
        self.assertFalse(any("/commits/" in r.full_url for r in self.server.calls))

    def test_tag_must_resolve_to_source(self):
        self.server.tag_commit = "f" * 40
        self.assert_refused()

    def test_release_inputs_must_match(self):
        for key in ("RELEASE_TAG", "RELEASE_VERSION"):
            with self.subTest(key=key):
                self.environment[key] = "2.0.0"
                self.assert_refused()
                self.environment[key] = "1.2.3"

    def test_draft_or_foreign_release_record_is_refused(self):
        self.server.release["draft"] = True
        self.assert_refused()
        self.server.release["draft"] = False
        self.server.release["tag_name"] = "2.0.0"
        self.assert_refused()

    def test_malformed_release_record_identity_is_refused(self):
        for release_id in (True, -1, "foreign/identity"):
            self.server.release["id"] = release_id
            self.assert_refused()

    def test_invalid_inbox_commit_identity_is_refused(self):
        self.server.head_override = "not-a-commit"
        self.assert_refused()

    def test_cyclic_tag_response_is_refused(self):
        self.server.annotated = self.server.tag_cycle = True
        self.assert_refused()

    def test_nonregular_or_unencoded_existing_manifest_is_refused(self):
        self.assertEqual(self.run_action(), 0)
        self.server.existing_type = "symlink"
        self.assertEqual(self.run_action(), 1)
        self.server.existing_type = "file"
        self.server.existing_encoding = "none"
        self.assertEqual(self.run_action(), 1)
        self.assertEqual(len(self.server.dispatches), 1)

    def test_asset_input_shapes_are_closed(self):
        for assets in (
            {},
            [None],
            [{"name": 7}],
            [self.server.assets[0], self.server.assets[0]],
        ):
            self.environment["RELEASE_ASSETS"] = json.dumps(assets)
            self.assert_refused()

    def test_legacy_manifest_only_call_reads_release_record(self):
        for key in (
            "RELEASE_TAG",
            "RELEASE_VERSION",
            "RELEASE_ASSETS",
            "RELEASE_SOURCE_SHA",
        ):
            self.environment.pop(key)
        self.assertEqual(self.run_action(), 0)

    def test_missing_app_authority_is_refused(self):
        self.environment["APP_TOKEN"] = ""
        self.assert_refused()

    def test_denied_app_authority_is_refused_without_provider_text(self):
        self.server.denied = "Bearer app-test-token"
        self.assert_refused()
        self.assertIn("HTTP 403", self.errors.getvalue())
        self.assertNotIn("untrusted-secret", self.errors.getvalue())

    def test_app_without_contents_write_authority_is_refused(self):
        self.server.denied_method = "PUT"
        self.assert_refused()
        self.assertIn("HTTP 403", self.errors.getvalue())

    def test_wider_or_foreign_app_repository_authority_is_refused(self):
        for authority in (
            {"total_count": 2, "repositories": [{"full_name": submission.DESTINATION}]},
            {
                "total_count": 1,
                "repositories": [{"full_name": "wyrd-company/another-tool"}],
            },
            {"total_count": 0, "repositories": []},
        ):
            self.server.authority = authority
            self.assert_refused()

    def test_concurrent_inbox_conflict_retries(self):
        self.server.conflicts = 1
        self.assertEqual(self.run_action(), 0)
        self.assertEqual(self.server.created, 1)

    def test_exhausted_concurrent_updates_do_not_dispatch(self):
        self.server.conflicts = 4
        self.assert_refused()

    def test_dispatch_failure_replay_does_not_create_another_commit(self):
        self.server.dispatch_failure = True
        self.assertEqual(self.run_action(), 1)
        self.assertFalse(Path(self.environment["GITHUB_OUTPUT"]).exists())
        self.server.dispatch_failure = False
        self.assertEqual(self.run_action(), 0)
        self.assertEqual(self.output()["state"], "unchanged")
        self.assertEqual(self.server.created, 1)

    def test_untrusted_input_is_not_echoed(self):
        self.manifest["untrusted-secret"] = "app-test-token"
        self.assert_refused()
        self.assertNotIn("app-test-token", self.errors.getvalue())
        self.assertNotIn("untrusted-secret", self.errors.getvalue())

    def test_transport_refuses_redirects_before_a_second_origin(self):
        requests = []

        def https_open(handler, request):
            requests.append(request)
            headers = email.message.Message()
            headers["Location"] = "https://example.invalid/app-test-token"
            response = urllib.response.addinfourl(
                io.BytesIO(b"app-test-token"), headers, request.full_url, 302
            )
            response.msg = "app-test-token"
            return response

        with patch.object(urllib.request.HTTPSHandler, "https_open", https_open):
            with self.assertRaises(submission.APIError):
                submission.GitHub("app-test-token").request(
                    "GET", "/installation/repositories"
                )
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].host, "api.github.com")

    def test_redirect_never_replays_credentials(self):
        handler = submission.NoRedirect()
        request = urllib.request.Request(
            "https://api.github.com/repos/example",
            headers={"Authorization": "Bearer app-test-token"},
        )
        for code in (301, 302, 303, 307, 308):
            with self.subTest(code=code):
                self.assertIsNone(
                    handler.redirect_request(
                        request,
                        None,
                        code,
                        "app-test-token",
                        {},
                        "https://example.invalid/app-test-token",
                    )
                )


if __name__ == "__main__":
    unittest.main()
