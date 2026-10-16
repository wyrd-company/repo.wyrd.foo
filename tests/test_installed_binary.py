# ---
# relationships:
#   verifies: package-repository-publishing
# ---

"""The installed fixture must reach later Arch lifecycle assertions reliably."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "run-installed-binary.sh"


class InstalledBinaryTests(unittest.TestCase):
    def run_fixture(self, root, failures, status=126, message="Text file busy"):
        binary = root / "example command"
        binary.write_text('''#!/usr/bin/env bash
set -eu
count=0
if [[ -f "$COUNTER" ]]; then read -r count < "$COUNTER"; fi
count=$((count + 1))
printf '%s\\n' "$count" > "$COUNTER"
if [[ "$count" -le "$FAILURES" ]]; then
  printf '%s\\n' "$FAIL_MESSAGE" >&2
  exit "$FAIL_STATUS"
fi
printf '%s\\n' "$@" > "$ARGUMENTS"
printf 'ready\\n'
''')
        binary.chmod(0o755)
        sleep = root / "sleep"
        sleep.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> "$DELAYS"\n')
        sleep.chmod(0o755)
        return subprocess.run(
            [str(RUNNER), str(binary), "one argument", "", "--flag"],
            env=os.environ | {
                "PATH": str(root) + os.pathsep + os.environ["PATH"],
                "COUNTER": str(root / "count"), "FAILURES": str(failures),
                "FAIL_STATUS": str(status), "FAIL_MESSAGE": message,
                "ARGUMENTS": str(root / "arguments"), "DELAYS": str(root / "delays"),
            },
            text=True, capture_output=True,
        )

    def test_success_preserves_output_and_arguments_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_fixture(root, 0)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "ready\n")
            self.assertEqual((root / "arguments").read_text().splitlines(), ["one argument", "", "--flag"])
            self.assertEqual((root / "count").read_text(), "1\n")
            self.assertFalse((root / "delays").exists())

    def test_transient_busy_exec_reaches_the_binary(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_fixture(root, 2)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "ready\n")
            self.assertEqual((root / "count").read_text(), "3\n")
            self.assertEqual((root / "delays").read_text().splitlines(), ["1", "2"])

    def test_other_command_failures_are_not_retried(self):
        for status, message in ((126, "Permission denied"), (1, "Text file busy"), (17, "ordinary failure")):
            with self.subTest(status=status, message=message), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                result = self.run_fixture(root, 9, status, message)
                self.assertEqual(result.returncode, status)
                self.assertEqual(result.stderr, message + "\n")
                self.assertEqual((root / "count").read_text(), "1\n")
                self.assertFalse((root / "delays").exists())

    def test_persistent_busy_exec_fails_after_four_attempts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.run_fixture(root, 9)
            self.assertEqual(result.returncode, 126)
            self.assertEqual((root / "count").read_text(), "4\n")
            self.assertEqual((root / "delays").read_text().splitlines(), ["1", "2", "3"])
            self.assertEqual(result.stderr.count("Text file busy"), 4)
            self.assertFalse((root / "arguments").exists())


if __name__ == "__main__":
    unittest.main()
