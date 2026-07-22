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


if __name__ == "__main__":
    unittest.main()
