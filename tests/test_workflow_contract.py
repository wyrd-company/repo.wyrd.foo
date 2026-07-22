# ---
# relationships:
#   verifies: package-repository-publishing
# ---

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION = (ROOT / ".github" / "actions" / "submit-release" / "action.yml").read_text(
    encoding="utf-8"
)
WORKFLOW = (ROOT / ".github" / "workflows" / "publish.yml").read_text(encoding="utf-8")
COMMIT_SCRIPT = (ROOT / "scripts" / "commit-release.sh").read_text(encoding="utf-8")
AWS_CLI_INSTALLER = (ROOT / "scripts" / "install-aws-cli.sh").read_text(encoding="utf-8")
AUR_PUBLISHER = (ROOT / "scripts" / "publish-aur.sh").read_text(encoding="utf-8")
AUR_KEY_PREPARER = ROOT / "scripts" / "prepare-ssh-private-key.py"
README = (ROOT / "README.md").read_text(encoding="utf-8")
R2_PUBLISHER = (ROOT / "scripts" / "publish-r2.sh").read_text(encoding="utf-8")
PUBLIC_KEY = ROOT / "pubkey.gpg"
INTEGRATION = (ROOT / "tests" / "run-integration.sh").read_text(encoding="utf-8")


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
        self.assertIn("shell: bash", install_step["body"])
        self.assertIn("set -euo pipefail", install_step["body"])
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
        self.assertIn("--proto '=https'", AWS_CLI_INSTALLER)
        self.assertIn("--proto-redir '=https'", AWS_CLI_INSTALLER)
        self.assertIn('"${work_dir}/aws/install" --update', AWS_CLI_INSTALLER)

    def test_aur_render_uses_a_canonical_runner_temp_bind_mount(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner_temp = root / "runner temp"
            fake_bin = root / "fake-bin"
            runner_temp.mkdir()
            fake_bin.mkdir()
            capture = root / "docker-arguments"

            commands = {
                "docker": """#!/usr/bin/env bash
printf '%s\\0' "$@" > "$DOCKER_ARGUMENTS"
printf 'pkgbase = sample-tool-bin\\npkgname = sample-tool-bin\\n'
""",
                "ssh-keyscan": """#!/usr/bin/env bash
printf 'aur.archlinux.org ssh-ed25519 test-key\\n'
""",
                "ssh-keygen": """#!/usr/bin/env bash
printf '256 SHA256:RFzBCUItH9LZS0cKB5UE6ceAYhBD5C8GeOBip8Z11+4 aur.archlinux.org (ED25519)\\n'
""",
                "git": """#!/usr/bin/env bash
if [[ "$1" == clone ]]; then
  mkdir -p "$3/.git"
  exit 0
fi
if [[ "$1" == -C && "$3" == diff ]]; then
  exit 0
fi
exit 0
""",
            }
            for name, content in commands.items():
                command = fake_bin / name
                command.write_text(content, encoding="utf-8")
                command.chmod(0o755)

            environment = os.environ | {
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(runner_temp),
                "AUR_ACCOUNT": "package-publisher",
                "AUR_PRIVATE_KEY": "test-private-key",
                "DOCKER_ARGUMENTS": str(capture),
            }
            subprocess.run(
                [
                    str(ROOT / "scripts" / "publish-aur.sh"),
                    str(ROOT / "tests" / "fixtures" / "sample-tool-1.2.3.json"),
                ],
                cwd=ROOT,
                env=environment,
                check=True,
            )
            arguments = capture.read_bytes().rstrip(b"\0").split(b"\0")
            self.assertEqual(arguments[:3], [b"run", b"--rm", b"--mount"])
            mount = arguments[3].decode("utf-8")
            source = re.fullmatch(
                r"type=bind,src=(?P<source>/.*),dst=/source,readonly", mount
            )
            self.assertIsNotNone(source)
            assert source is not None
            self.assertTrue(source["source"].startswith(f"{runner_temp.resolve()}/"))
            self.assertIn("runner temp", source["source"])
            self.assertNotIn(b"--volume", arguments)

    def test_aur_render_rejects_an_unsafe_runner_temp(self) -> None:
        environment = os.environ | {
            "RUNNER_TEMP": "relative-runner-temp",
            "AUR_ACCOUNT": "package-publisher",
            "AUR_PRIVATE_KEY": "test-private-key",
        }
        result = subprocess.run(
            [
                str(ROOT / "scripts" / "publish-aur.sh"),
                str(ROOT / "tests" / "fixtures" / "sample-tool-1.2.3.json"),
            ],
            cwd=ROOT,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("existing absolute directory", result.stderr)

    def test_aur_private_key_normalizes_common_secret_line_endings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            subprocess.run(
                ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source)],
                check=True,
            )
            canonical = source.read_bytes()
            variants = {
                "without-newline": canonical.rstrip(b"\n"),
                "crlf": canonical.replace(b"\n", b"\r\n"),
                "bare-cr": canonical.replace(b"\n", b"\r"),
                "extra-newlines": canonical.rstrip(b"\n") + b"\n\n\n",
            }

            for name, key_material in variants.items():
                with self.subTest(name=name):
                    prepared = root / name
                    subprocess.run(
                        ["python3", str(AUR_KEY_PREPARER), str(prepared)],
                        input=key_material,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        check=True,
                    )
                    self.assertEqual(prepared.read_bytes(), canonical)
                    self.assertEqual(prepared.stat().st_mode & 0o777, 0o600)
                    subprocess.run(
                        ["ssh-keygen", "-y", "-P", "", "-f", str(prepared)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.PIPE,
                        check=True,
                    )

    def test_aur_private_key_file_errors_are_generic_and_non_destructive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "existing-key"
            destination.write_bytes(b"existing-content")
            secret = b"not-a-private-key-secret-sentinel"
            result = subprocess.run(
                ["python3", str(AUR_KEY_PREPARER), str(destination)],
                input=secret,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, b"")
            self.assertEqual(result.stderr, b"Unable to prepare AUR private key file.\n")
            self.assertNotIn(secret, result.stderr)
            self.assertNotIn(b"Traceback", result.stderr)
            self.assertEqual(destination.read_bytes(), b"existing-content")

            missing_parent = Path(directory) / "missing" / "key"
            missing_result = subprocess.run(
                ["python3", str(AUR_KEY_PREPARER), str(missing_parent)],
                input=secret,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            self.assertNotEqual(missing_result.returncode, 0)
            self.assertEqual(missing_result.stdout, b"")
            self.assertEqual(
                missing_result.stderr,
                b"Unable to prepare AUR private key file.\n",
            )
            self.assertNotIn(secret, missing_result.stderr)
            self.assertNotIn(b"Traceback", missing_result.stderr)

    def test_aur_private_key_fails_before_container_or_network_access(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner_temp = root / "runner-temp"
            fake_bin = root / "fake-bin"
            runner_temp.mkdir()
            fake_bin.mkdir()
            external_marker = root / "external-accessed"

            commands = {
                "docker": """#!/usr/bin/env bash
printf 'called\\n' > "$EXTERNAL_MARKER"
exit 1
""",
                "ssh-keygen": """#!/usr/bin/env bash
exit 255
""",
                "ssh-keyscan": """#!/usr/bin/env bash
printf 'called\\n' > "$EXTERNAL_MARKER"
exit 1
""",
                "git": """#!/usr/bin/env bash
printf 'called\\n' > "$EXTERNAL_MARKER"
exit 1
""",
            }
            for name, content in commands.items():
                command = fake_bin / name
                command.write_text(content, encoding="utf-8")
                command.chmod(0o755)

            secret = "not-a-private-key-secret-sentinel"
            environment = os.environ | {
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(runner_temp),
                "AUR_ACCOUNT": "package-publisher",
                "AUR_PRIVATE_KEY": secret,
                "EXTERNAL_MARKER": str(external_marker),
            }
            result = subprocess.run(
                [
                    str(ROOT / "scripts" / "publish-aur.sh"),
                    str(ROOT / "tests" / "fixtures" / "sample-tool-1.2.3.json"),
                ],
                cwd=ROOT,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid or encrypted", result.stderr)
            self.assertNotIn(secret, result.stdout + result.stderr)
            self.assertFalse(external_marker.exists())

    def test_documented_apt_key_uses_its_armored_extension(self) -> None:
        self.assertIn("https://repo.wyrd.foo/pubkey.asc", README)
        self.assertIn("/etc/apt/keyrings/wyrd-company.asc", README)
        self.assertNotIn("/etc/apt/keyrings/wyrd-company.gpg", README)
        self.assertIn("upload_mutable pubkey.asc", R2_PUBLISHER)
        self.assertIn("upload_mutable pubkey.gpg", R2_PUBLISHER)

    def test_checked_in_gpg_key_is_binary_and_parseable(self) -> None:
        self.assertFalse(PUBLIC_KEY.read_bytes().startswith(b"-----BEGIN PGP"))
        result = subprocess.run(
            ["gpg", "--batch", "--show-keys", "--with-colons", str(PUBLIC_KEY)],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertRegex(result.stdout, r"(?m)^fpr:{9}[0-9A-F]{40}:$")

    def test_repository_docs_use_the_confirmed_product_secret_names(self) -> None:
        self.assertIn("REPO_WYRD_FOO_PUBLISHER_APP_ID", README)
        self.assertIn("REPO_WYRD_FOO_PUBLISHER_PRIVATE_KEY", README)
        legacy_names = (
            "REPO_WYRD_FOO_" + "APP_CLIENT_ID",
            "REPO_WYRD_FOO_" + "APP_PRIVATE_KEY",
        )
        checked_suffixes = {".json", ".md", ".py", ".sh", ".yaml", ".yml"}
        for path in ROOT.rglob("*"):
            if path.is_file() and path.suffix in checked_suffixes:
                content = path.read_text(encoding="utf-8")
                for legacy_name in legacy_names:
                    self.assertNotIn(legacy_name, content, path)

    def test_r2_retry_never_deletes_restored_immutable_objects(self) -> None:
        self.assertNotIn("--delete", R2_PUBLISHER)
        self.assertIn("--cache-control 'public,max-age=31536000,immutable'", R2_PUBLISHER)

    def test_workflow_invokes_aur_publisher_without_a_relative_work_root(self) -> None:
        self.assertIn('run: scripts/publish-aur.sh "../queue/${MANIFEST_PATH}"', WORKFLOW)
        self.assertNotIn(".tmp/aur", WORKFLOW)
        self.assertIn("mktemp -d", AUR_PUBLISHER)
        self.assertIn('realpath -e -- "$RUNNER_TEMP"', AUR_PUBLISHER)
        self.assertIn('type=bind,src=${rendered},dst=/source,readonly', AUR_PUBLISHER)

    def test_real_docker_gate_uses_the_exact_read_only_aur_mount(self) -> None:
        self.assertIn('rendered="${work}/rendered package"', INTEGRATION)
        self.assertIn(
            'docker run --rm --mount "type=bind,src=${rendered},dst=/source,readonly"',
            INTEGRATION,
        )
        self.assertIn("touch /source/write-probe", INTEGRATION)


if __name__ == "__main__":
    unittest.main()
