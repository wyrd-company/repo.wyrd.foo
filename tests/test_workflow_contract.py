# ---
# relationships:
#   verifies: package-repository-publishing
# ---

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION = (ROOT / ".github" / "actions" / "submit-release" / "action.yml").read_text(
    encoding="utf-8"
)
WORKFLOW = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
COMMIT_SCRIPT = (ROOT / "scripts" / "commit-release.sh").read_text(encoding="utf-8")
AWS_CLI_INSTALLER = (ROOT / "scripts" / "install-aws-cli.sh").read_text(encoding="utf-8")


class WorkflowContractTests(unittest.TestCase):
    def test_product_app_writes_only_the_inbox_flow(self) -> None:
        self.assertIn("ref: release-manifests", ACTION)
        self.assertIn("repository_dispatch", WORKFLOW)
        self.assertIn("HEAD:release-manifests", COMMIT_SCRIPT)
        self.assertNotIn("HEAD:main", COMMIT_SCRIPT)

    def test_every_checkout_disables_credential_persistence(self) -> None:
        self.assertEqual(ACTION.count("persist-credentials: false"), 1)
        self.assertEqual(WORKFLOW.count("persist-credentials: false"), 2)
        self.assertNotIn("persist-credentials: true", ACTION + WORKFLOW)

    def test_publisher_and_queue_are_separate_exact_checkouts(self) -> None:
        self.assertIn("path: publisher", WORKFLOW)
        queue_checkout = re.search(
            r"- name: Check out exact inbox commit as untrusted data(?P<body>.*?)(?=\n      - name:)",
            WORKFLOW,
            re.DOTALL,
        )
        self.assertIsNotNone(queue_checkout)
        assert queue_checkout is not None
        self.assertIn("path: queue", queue_checkout["body"])
        self.assertIn("ref: ${{ env.INBOX_COMMIT }}", queue_checkout["body"])
        self.assertIn("fetch-depth: 0", queue_checkout["body"])
        match = re.search(r"ref: ([0-9a-f]{40}) # PUBLISHER_CODE_SHA", WORKFLOW)
        self.assertIsNotNone(match)
        self.assertNotIn("queue/scripts/", WORKFLOW)

    def test_ubuntu_bootstrap_uses_the_pinned_aws_bundle(self) -> None:
        install_step = re.search(
            r"- name: Install repository tooling(?P<body>.*?)(?=\n      - name:)",
            WORKFLOW,
            re.DOTALL,
        )
        self.assertIsNotNone(install_step)
        assert install_step is not None
        self.assertNotRegex(install_step["body"], r"\bawscli\b")
        self.assertIn("publisher/scripts/install-aws-cli.sh", install_step["body"])
        self.assertIn("runs-on: ubuntu-24.04", WORKFLOW)

    def test_aws_cli_installer_is_version_and_digest_pinned(self) -> None:
        version = re.search(r'^readonly AWS_CLI_VERSION="([0-9]+\.[0-9]+\.[0-9]+)"$', AWS_CLI_INSTALLER, re.MULTILINE)
        self.assertIsNotNone(version)
        digests = re.findall(r'^    readonly aws_cli_sha256="([0-9a-f]{64})"$', AWS_CLI_INSTALLER, re.MULTILINE)
        self.assertEqual(len(digests), 2)
        self.assertEqual(len(set(digests)), 2)
        self.assertIn("awscli-exe-linux-${aws_cli_arch}-${AWS_CLI_VERSION}.zip", AWS_CLI_INSTALLER)
        self.assertIn("sha256sum --check --status", AWS_CLI_INSTALLER)
        self.assertIn('"aws-cli/${AWS_CLI_VERSION} "*', AWS_CLI_INSTALLER)
        self.assertNotIn("awscli-exe-linux-${aws_cli_arch}.zip", AWS_CLI_INSTALLER)


if __name__ == "__main__":
    unittest.main()
