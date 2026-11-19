# ---
# relationships:
#   verifies: package-repository-publishing
# ---

import contextlib
import io
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import qualify_submission as gate

# Independent inventory: dropping a file from the verifier must not drop its test.
RUNTIME_FILES = (
    ".github/actions/submit-release/action.yml",
    "scripts/submit-release.py",
    "scripts/repository.py",
)


class QualificationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.remote = self.work / "remote"
        self.root = self.work / "checkout"
        self.remote.mkdir()
        self.root.mkdir()
        for relative in RUNTIME_FILES:
            for directory in (self.remote, self.root):
                path = directory / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(gate.ROOT / relative, path)
        self.git("init", "--quiet", "--initial-branch=main")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@example.invalid")
        self.git("add", ".")
        self.git("commit", "--quiet", "-m", "Action runtime")
        self.revision = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "1.2.3")
        # A released ancestor of main is valid; equality to main HEAD is not required.
        self.git("commit", "--quiet", "--allow-empty", "-m", "Later main")
        self.example = self.root / "docs/examples/submit-release.yml"
        self.example.parent.mkdir(parents=True)
        self.consumer = self.work / "publication.yml"
        self.reference()

    def git(self, *args):
        return gate.git(self.remote, *args).decode()

    def reference(self, revision=None, version="1.2.3"):
        line = f"uses: {gate.ACTION}@{revision or self.revision} # {version}\n"
        self.example.write_text(line)
        self.consumer.write_text("jobs:\n  publish:\n    steps:\n      - " + line)

    def run_gate(self):
        output = io.StringIO()
        with patch.dict(
            os.environ, {"PUBLICATION_CALLER": str(self.consumer)}
        ), contextlib.redirect_stdout(output):
            status = gate.main(self.root, str(self.remote))
        return status, output.getvalue()

    def assert_refused(self):
        status, output = self.run_gate()
        self.assertEqual(status, 1, output)
        self.assertIn("QUALIFICATION_RESULT=FAIL", output)
        self.assertNotIn("QUALIFICATION_RESULT=PASS", output)

    def test_remote_main_release_and_identical_runtime_qualify(self):
        status, output = self.run_gate()
        self.assertEqual(status, 0, output)
        self.assertIn(
            f"QUALIFICATION_RESULT=PASS revision={self.revision} tag=1.2.3", output
        )

    def test_annotated_remote_release_tag_qualifies(self):
        self.git("tag", "-f", "-a", "1.2.3", self.revision, "-m", "Release")
        self.assertEqual(self.run_gate()[0], 0)

    def test_invalid_sha_has_no_published_identity(self):
        self.reference("PUBLISHED_MAIN_SHA")
        with self.assertRaises(gate.QualificationError):
            gate.published_reference(self.example)

    def test_invalid_version_has_no_published_identity(self):
        self.reference(version="RELEASE_TAG")
        with self.assertRaises(gate.QualificationError):
            gate.published_reference(self.example)

    def test_action_reference_must_be_executable_yaml(self):
        self.consumer.write_text("description: |\n  " + self.example.read_text())
        self.assert_refused()

    def test_non_executable_duplicate_pin_is_refused(self):
        self.consumer.write_text(
            self.consumer.read_text() + "description: |\n  " + self.example.read_text()
        )
        self.assert_refused()

    def test_missing_remote_main_is_refused(self):
        self.git("update-ref", "-d", "refs/heads/main")
        self.assert_refused()

    def test_unqualified_example_is_refused(self):
        self.reference("PUBLISHED_MAIN_SHA", "RELEASE_TAG")
        self.assert_refused()

    def test_missing_pin_version_comment_is_refused(self):
        self.example.write_text(self.example.read_text().replace(" # 1.2.3", ""))
        self.assert_refused()

    def test_missing_or_duplicate_action_reference_is_refused(self):
        original = self.consumer.read_text()
        for text in ("jobs: {}\n", original + "other:\n  " + self.example.read_text()):
            with self.subTest(text=text):
                self.consumer.write_text(text)
                self.assert_refused()

    def test_publication_part_reference_mismatch_is_refused(self):
        self.consumer.write_text(
            self.consumer.read_text().replace(" # 1.2.3", " # 1.2.4")
        )
        self.assert_refused()

    def test_unpublished_release_tag_is_refused(self):
        self.git("tag", "-d", "1.2.3")
        self.assert_refused()

    def test_local_only_tag_cannot_qualify_remote_pin(self):
        gate.git(self.root, "init", "--quiet")
        gate.git(
            self.root,
            "fetch",
            "--quiet",
            str(self.remote),
            "refs/tags/1.2.3:refs/tags/1.2.3",
        )
        self.git("tag", "-d", "1.2.3")
        self.assert_refused()

    def test_release_tag_must_identify_pin(self):
        self.reference(self.git("rev-parse", "main").strip())
        self.assert_refused()

    def test_tagged_branch_only_commit_is_refused(self):
        self.git("checkout", "--quiet", "-b", "unmerged")
        self.git("commit", "--quiet", "--allow-empty", "-m", "Branch only")
        branch = self.git("rev-parse", "HEAD").strip()
        self.git("tag", "-f", "1.2.3", branch)
        self.reference(branch)
        self.assert_refused()

    def test_each_published_runtime_file_must_match(self):
        for relative in RUNTIME_FILES:
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                try:
                    path.write_bytes(original + b"\n# Changed runtime\n")
                    self.assert_refused()
                finally:
                    path.write_bytes(original)

    def test_missing_publication_part_fails_closed(self):
        self.consumer.unlink()
        self.assert_refused()

    def test_interrupted_qualification_cannot_report_pass(self):
        output = io.StringIO()
        with patch.dict(
            os.environ, {"PUBLICATION_CALLER": str(self.consumer)}
        ), patch.object(
            gate, "qualify", side_effect=KeyboardInterrupt
        ), contextlib.redirect_stdout(
            output
        ):
            with self.assertRaises(KeyboardInterrupt):
                gate.main(self.root, str(self.remote))
        self.assertNotIn("QUALIFICATION_RESULT=PASS", output.getvalue())


if __name__ == "__main__":
    unittest.main()
