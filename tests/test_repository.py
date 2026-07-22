# ---
# relationships:
#   verifies: package-repository-publishing
# ---

from __future__ import annotations

import copy
import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("repository", ROOT / "scripts" / "repository.py")
assert SPEC is not None and SPEC.loader is not None
repository = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(repository)
FIXTURE = ROOT / "tests" / "fixtures" / "sample-tool-1.2.3.json"


class ManifestTests(unittest.TestCase):
    def manifest(self) -> dict:
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def git(self, root: Path, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()

    def allowlist(self, root: Path, manifest: dict) -> Path:
        path = root / "allowlist.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "x-relationships": {"implements": ["package-repository-publishing"]},
                    "products": {
                        manifest["product"]: {
                            "source_repository": manifest["source"]["repository"],
                            "package": manifest["package"],
                            "publish": manifest["publish"],
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        return path

    def queue_commit(self, root: Path, manifest: dict, extra_file: bool = False) -> tuple[str, str, Path]:
        subprocess.run(["git", "init", "--quiet", "-b", "release-manifests", str(root)], check=True)
        self.git(root, "config", "user.name", "Package Test")
        self.git(root, "config", "user.email", "package-test@example.invalid")
        (root / "base.txt").write_text("base\n", encoding="utf-8")
        self.git(root, "add", "base.txt")
        self.git(root, "commit", "--quiet", "-m", "base")
        relative_path = f"releases/{manifest['product']}/{manifest['version']}.json"
        path = root / relative_path
        path.parent.mkdir(parents=True)
        path.write_bytes(repository.canonical_bytes(manifest))
        self.git(root, "add", relative_path)
        if extra_file:
            (root / "extra.txt").write_text("extra\n", encoding="utf-8")
            self.git(root, "add", "extra.txt")
        self.git(root, "commit", "--quiet", "-m", "add manifest")
        return self.git(root, "rev-parse", "HEAD"), relative_path, self.allowlist(root, manifest)

    def test_fixture_is_valid(self) -> None:
        repository.validate_manifest(self.manifest())

    def test_requires_exact_artifact_roles(self) -> None:
        manifest = self.manifest()
        manifest["artifacts"][1]["arch"] = "amd64"
        with self.assertRaisesRegex(repository.ManifestError, "unexpected or duplicate"):
            repository.validate_manifest(manifest)

    def test_artifact_filename_must_match_format(self) -> None:
        manifest = self.manifest()
        artifact = manifest["artifacts"][0]
        artifact["filename"] = artifact["filename"].removesuffix(".tar.gz") + ".rpm"
        artifact["url"] = artifact["url"].removesuffix(".tar.gz") + ".rpm"
        with self.assertRaisesRegex(repository.ManifestError, "does not match its format"):
            repository.validate_manifest(manifest)

    def test_confines_download_to_exact_github_release(self) -> None:
        for url in (
            "https://example.invalid/payload",
            "https://github.com/wyrd-company/sample-tool/releases/download/1.2.3/other.rpm",
            "https://github.com/wyrd-company/sample-tool/releases/download/1.2.3/sample-tool_1.2.3_linux_x86_64.tar.gz?mutable=1",
        ):
            with self.subTest(url=url):
                manifest = self.manifest()
                manifest["artifacts"][0]["url"] = url
                with self.assertRaisesRegex(repository.ManifestError, "exact public GitHub Release"):
                    repository.validate_manifest(manifest)

    def test_rejects_control_characters(self) -> None:
        manifest = self.manifest()
        manifest["package"]["description"] = "unsafe\nvalue"
        with self.assertRaisesRegex(repository.ManifestError, "control character"):
            repository.validate_manifest(manifest)

    def test_submit_is_canonical_idempotent_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "manifest.json"
            source.write_text(json.dumps(self.manifest()), encoding="utf-8")
            state, result = repository.submit_manifest(source, root / "target")
            self.assertEqual((state, result), ("created", "releases/sample-tool/1.2.3.json"))
            destination = root / "target" / result
            self.assertEqual(destination.read_bytes(), repository.canonical_bytes(self.manifest()))
            self.assertEqual(
                repository.submit_manifest(source, root / "target"),
                ("unchanged", "releases/sample-tool/1.2.3.json"),
            )

            changed = self.manifest()
            changed["source"]["commit"] = "f" * 40
            source.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaisesRegex(repository.ManifestError, "immutable release manifest"):
                repository.submit_manifest(source, root / "target")

    def test_parses_only_full_r2_bucket_urls(self) -> None:
        self.assertEqual(
            repository.parse_r2_api("https://012345.r2.cloudflarestorage.com/repository"),
            ("https://012345.r2.cloudflarestorage.com", "repository"),
        )
        for value in (
            "https://012345.r2.cloudflarestorage.com",
            "https://012345.r2.cloudflarestorage.com/one/two",
            "http://012345.r2.cloudflarestorage.com/repository",
            "https://storage.invalid/repository",
        ):
            with self.subTest(value=value), self.assertRaises(repository.ManifestError):
                repository.parse_r2_api(value)

    def test_redirect_policy_accepts_only_final_github_https_hosts(self) -> None:
        for value in (
            "https://github.com/file",
            "https://release-assets.githubusercontent.com/file",
        ):
            with self.subTest(value=value):
                repository.validate_download_target(value)
        for value in (
            "http://github.com/file",
            "https://example.invalid/file",
            "https://githubusercontent.com.example.invalid/file",
        ):
            with self.subTest(value=value), self.assertRaises(repository.ManifestError):
                repository.validate_download_target(value)

    def test_renders_deterministic_aur_package(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            repository.render_aur(FIXTURE, output)
            pkgbuild = (output / "PKGBUILD").read_text(encoding="utf-8")
            self.assertIn("pkgname='sample-tool-bin'", pkgbuild)
            self.assertIn("pkgver='1.2.3'", pkgbuild)
            self.assertIn("arch=('x86_64' 'aarch64')", pkgbuild)
            self.assertIn("https://repo.wyrd.foo/artifacts/sample-tool/1.2.3/", pkgbuild)
            self.assertIn("sha256sums_x86_64=('111111", pkgbuild)
            self.assertIn("/usr/share/licenses/${pkgname}/LICENSE", pkgbuild)
            self.assertNotIn("install=", pkgbuild)
            self.assertFalse((output / "sample-tool-bin.install").exists())

    def test_queued_manifest_path_must_match_its_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "releases" / "other-tool" / "1.2.3.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(self.manifest()), encoding="utf-8")
            with self.assertRaisesRegex(repository.ManifestError, "belongs at"):
                repository.load_queued_manifest(root, "releases/other-tool/1.2.3.json")

    def test_queue_accepts_one_canonical_allowlisted_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit, path, allowlist = self.queue_commit(root, self.manifest())
            manifest = repository.validate_queue_commit(root, commit, path, allowlist)
            self.assertEqual(manifest["product"], "sample-tool")

    def test_queue_accepts_production_detached_checkout_with_remote_inbox_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit, path, allowlist = self.queue_commit(root, self.manifest())
            self.git(root, "update-ref", "refs/remotes/origin/release-manifests", commit)
            self.git(root, "checkout", "--quiet", "--detach", commit)
            self.git(root, "branch", "--delete", "--force", "release-manifests")

            local_ref = subprocess.run(
                ["git", "-C", str(root), "show-ref", "--verify", "refs/heads/release-manifests"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.assertNotEqual(local_ref.returncode, 0)
            self.assertEqual(
                self.git(root, "rev-parse", "refs/remotes/origin/release-manifests"),
                commit,
            )

            manifest = repository.validate_queue_commit(root, commit, path, allowlist)
            self.assertEqual(manifest["product"], "sample-tool")

    def test_queue_rejects_additional_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit, path, allowlist = self.queue_commit(root, self.manifest(), extra_file=True)
            with self.assertRaisesRegex(repository.ManifestError, "exactly the requested"):
                repository.validate_queue_commit(root, commit, path, allowlist)

    def test_queue_rejects_reusing_a_deleted_release_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit, path, allowlist = self.queue_commit(root, self.manifest())
            self.git(root, "rm", "--quiet", path)
            self.git(root, "commit", "--quiet", "-m", "delete")
            manifest_path = root / path
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_bytes(repository.canonical_bytes(self.manifest()))
            self.git(root, "add", path)
            self.git(root, "commit", "--quiet", "-m", "re-add")
            readded = self.git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(repository.ManifestError, "existed earlier"):
                repository.validate_queue_commit(root, readded, path, allowlist)

    def test_queue_rejects_a_commit_outside_the_inbox_branch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit, path, allowlist = self.queue_commit(root, self.manifest())
            parent = self.git(root, "rev-parse", f"{commit}^")
            self.git(root, "checkout", "--quiet", "--detach", commit)
            self.git(root, "branch", "--force", "release-manifests", parent)
            with self.assertRaisesRegex(repository.ManifestError, "not reachable"):
                repository.validate_queue_commit(root, commit, path, allowlist)

    def test_queue_rejects_modified_and_merge_commits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit, path, allowlist = self.queue_commit(root, self.manifest())
            manifest_path = root / path
            changed = self.manifest()
            changed["package"]["description"] = "Changed example utility"
            manifest_path.write_bytes(repository.canonical_bytes(changed))
            self.git(root, "add", path)
            self.git(root, "commit", "--quiet", "-m", "modify")
            modified = self.git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(repository.ManifestError, "add exactly"):
                repository.validate_queue_commit(root, modified, path, allowlist)

            self.git(root, "checkout", "--quiet", "-b", "side", commit)
            (root / "side.txt").write_text("side\n", encoding="utf-8")
            self.git(root, "add", "side.txt")
            self.git(root, "commit", "--quiet", "-m", "side")
            self.git(root, "checkout", "--quiet", "release-manifests")
            self.git(root, "merge", "--quiet", "--no-ff", "side", "-m", "merge")
            merged = self.git(root, "rev-parse", "HEAD")
            with self.assertRaisesRegex(repository.ManifestError, "exactly one parent"):
                repository.validate_queue_commit(root, merged, path, allowlist)

    def test_allowlist_binds_every_privileged_product_field(self) -> None:
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            allowlist = self.allowlist(root, manifest)
            repository.validate_allowed_product(manifest, allowlist)
            mutations = (
                ("source", "repository", "wyrd-company/another-tool"),
                ("package", "name", "another-tool"),
                ("package", "binary", "another-tool"),
                ("package", "description", "Another example utility"),
                ("package", "homepage", "https://example.invalid"),
                ("package", "license", "MIT"),
                ("package", "maintainer", "Another Maintainer <other@example.invalid>"),
                ("publish", "apt", {"suite": "testing", "component": "main"}),
                ("publish", "rpm", {"channel": "testing"}),
                ("publish", "aur", {"package": "another-tool-bin"}),
            )
            for section, field, value in mutations:
                changed = copy.deepcopy(manifest)
                changed[section][field] = value
                with self.subTest(section=section, field=field), self.assertRaisesRegex(
                    repository.ManifestError, "disagrees with the allowlist"
                ):
                    repository.validate_allowed_product(changed, allowlist)

    def test_checked_in_allowlist_is_exactly_wyrwood(self) -> None:
        products = repository.load_product_allowlist(ROOT / "config" / "products.json")
        self.assertEqual(set(products), {"wyrwood"})
        self.assertEqual(products["wyrwood"]["source_repository"], "wyrd-company/wyrwood")
        self.assertEqual(products["wyrwood"]["package"]["name"], "wyrwood")
        self.assertEqual(products["wyrwood"]["package"]["binary"], "wyrwood")
        self.assertEqual(products["wyrwood"]["package"]["license"], "Apache-2.0")
        self.assertEqual(products["wyrwood"]["publish"]["apt"], {"suite": "stable", "component": "main"})
        self.assertEqual(products["wyrwood"]["publish"]["rpm"], {"channel": "stable"})
        self.assertEqual(products["wyrwood"]["publish"]["aur"], {"package": "wyrwood-bin"})

    def test_resolver_reuses_original_addition_for_unchanged_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            commit, path, _ = self.queue_commit(root, self.manifest())
            (root / "later.txt").write_text("later\n", encoding="utf-8")
            self.git(root, "add", "later.txt")
            self.git(root, "commit", "--quiet", "-m", "later")
            github_output = root / "github-output"
            environment = os.environ | {"GITHUB_OUTPUT": str(github_output), "RESULT": path}
            subprocess.run(
                [str(ROOT / "scripts" / "resolve-inbox.sh")],
                cwd=root,
                env=environment,
                check=True,
            )
            self.assertEqual(github_output.read_text(encoding="utf-8"), f"commit={commit}\n")

    def test_source_tag_must_resolve_to_declared_commit(self) -> None:
        manifest = self.manifest()
        commit = manifest["source"]["commit"]
        output = f"{'f' * 40}\trefs/tags/1.2.3\n{commit}\trefs/tags/1.2.3^{{}}"
        with mock.patch.object(repository, "command_output", return_value=output):
            repository.verify_source_tag(manifest)
        with mock.patch.object(
            repository,
            "command_output",
            return_value=f"{'f' * 40}\trefs/tags/1.2.3",
        ), self.assertRaisesRegex(repository.ManifestError, "does not resolve"):
            repository.verify_source_tag(manifest)

    def test_rejects_unknown_fields_at_every_privileged_boundary(self) -> None:
        mutations = []
        manifest = self.manifest()
        manifest["unexpected"] = True
        mutations.append(manifest)
        manifest = self.manifest()
        manifest["source"]["ref"] = "main"
        mutations.append(manifest)
        manifest = self.manifest()
        manifest["artifacts"][0]["headers"] = {"Authorization": "value"}
        mutations.append(manifest)
        for manifest in mutations:
            with self.subTest(fields=list(manifest)), self.assertRaises(repository.ManifestError):
                repository.validate_manifest(copy.deepcopy(manifest))


if __name__ == "__main__":
    unittest.main()
