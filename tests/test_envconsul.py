# relationships:
#   verifies: package-repository-publishing
"""Verify upstream authentication, DEB contents, and immutable APT staging."""

import hashlib
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import tarfile
import threading
import unittest
import zipfile
from unittest.mock import patch
from urllib.error import HTTPError


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/build-envconsul.py"
SPEC = importlib.util.spec_from_file_location("build_envconsul", SCRIPT)
updater = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(updater)


class UpdaterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.keyring = cls.directory / "signing-keyring"
        cls.keyring.mkdir(mode=0o700)
        cls.gpg = ["gpg", "--no-options", "--batch", "--homedir", str(cls.keyring)]
        subprocess.run(
            [*cls.gpg, "--pinentry-mode", "loopback", "--passphrase", "",
             "--quick-generate-key", "Release Signer <signer@example.invalid>",
             "ed25519", "sign", "0"],
            check=True, capture_output=True,
        )
        listing = subprocess.run(
            [*cls.gpg, "--with-colons", "--list-keys"],
            check=True, capture_output=True, text=True,
        ).stdout
        cls.fingerprint = next(
            line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
        )
        cls.public_key = subprocess.run(
            [*cls.gpg, "--armor", "--export"], check=True, capture_output=True,
        ).stdout

    def setUp(self):
        self.release = {"tag_name": "v1.2.3", "draft": False, "prerelease": False}
        self.requests = []
        self.files = {"/key.asc": self.public_key}
        self.archives = {
            platform: self.archive(platform) for platform in updater.PLATFORMS
        }
        self.base = "/releases/1.2.3/envconsul_1.2.3"
        self.manifest = "".join(
            f"{hashlib.sha256(data).hexdigest()}  envconsul_1.2.3_{platform}.zip\n"
            for platform, data in self.archives.items()
        )
        for platform, data in self.archives.items():
            self.files[f"{self.base}_{platform}.zip"] = data
        self.sign_manifest(self.manifest)
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                owner.requests.append((self.path, self.headers.get("Authorization")))
                body = (
                    json.dumps(owner.release).encode()
                    if self.path == "/latest" else owner.files.get(self.path)
                )
                self.send_response(200 if body is not None else 404)
                self.end_headers()
                self.wfile.write(body or b"Not found")

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        self.addCleanup(server.server_close)
        self.addCleanup(worker.join)
        self.addCleanup(server.shutdown)
        origin = f"http://127.0.0.1:{server.server_port}"
        for name, value in (
            ("LATEST_RELEASE", f"{origin}/latest"),
            ("RELEASES", f"{origin}/releases"),
            ("PUBLIC_KEY", f"{origin}/key.asc"),
            ("SIGNING_KEY", self.fingerprint),
        ):
            patcher = patch.object(updater, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        formula_directory = tempfile.TemporaryDirectory()
        self.addCleanup(formula_directory.cleanup)
        self.output = Path(formula_directory.name) / "packages"
        self.repository = Path(formula_directory.name) / "public"

    def sign_manifest(self, manifest):
        path = self.directory / "SHA256SUMS"
        path.write_text(manifest)
        subprocess.run(
            [*self.gpg, "--yes", "--detach-sign", str(path)],
            check=True, capture_output=True,
        )
        self.files[f"{self.base}_SHA256SUMS"] = path.read_bytes()
        self.files[f"{self.base}_SHA256SUMS.sig"] = path.with_suffix(".sig").read_bytes()

    @staticmethod
    def archive(platform):
        data = io.BytesIO()
        with zipfile.ZipFile(data, "w") as zipped:
            zipped.writestr("envconsul", f"#!/bin/sh\nprintf 'sample-tool {platform}\\n'\n")
            zipped.writestr("LICENSE.txt", "Example license\n")
        return data.getvalue()

    def test_verified_packages_and_idempotent_staging(self):
        with patch.dict("os.environ", {"GH_TOKEN": "example-token"}):
            updater.build(self.output)
        self.assertEqual(self.requests[0], ("/latest", "Bearer example-token"))
        self.assertTrue(all(token is None for _, token in self.requests[1:]))
        self.assertEqual(len(list(self.output.iterdir())), 2)
        for arch in ("amd64", "arm64"):
            package = self.output / f"envconsul_1.2.3-1_{arch}.deb"
            fields = subprocess.check_output(
                ["dpkg-deb", "-f", str(package), "Package", "Version", "Architecture"], text=True,
            )
            self.assertEqual(fields, f"Package: envconsul\nVersion: 1.2.3-1\nArchitecture: {arch}\n")
            root = self.output.parent / arch
            subprocess.run(["dpkg-deb", "-x", str(package), str(root)], check=True)
            self.assertEqual(subprocess.check_output([str(root / "usr/bin/envconsul")], text=True), f"sample-tool linux_{arch}\n")
            self.assertEqual((root / "usr/share/doc/envconsul/copyright").read_text(), "Example license\n")
        existing = self.repository / "apt/pool/main/s/sample-tool/old.deb"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"existing package")
        self.assertTrue(updater.stage(self.output, self.repository))
        published = self.repository / "apt/pool/main/e/envconsul/envconsul_1.2.3-1_amd64.deb"
        os.utime(published, (123, 123))
        original = {p.name: p.read_bytes() for p in self.output.iterdir()}
        updater.build(self.output)
        self.assertEqual(original, {p.name: p.read_bytes() for p in self.output.iterdir()})
        self.assertFalse(updater.stage(self.output, self.repository))
        self.assertEqual(published.stat().st_mtime, 123)
        self.assertEqual(existing.read_bytes(), b"existing package")
        for name, data in original.items():
            self.assertEqual((self.repository / "apt/pool/main/e/envconsul" / name).read_bytes(), data)
        self.assertFalse(list(self.repository.rglob("*.rpm")))
        self.assertFalse(list(self.repository.rglob("PKGBUILD")))

    def test_package_bytes_and_modes_do_not_depend_on_umask(self):
        self.output.mkdir()
        original_mask = os.umask(0o022)
        try:
            original = None
            for mask in (0o022, 0o002, 0o077):
                os.umask(mask)
                updater.build_package("1.2.3", "linux_amd64", self.archives["linux_amd64"], self.output)
                package = self.output / "envconsul_1.2.3-1_amd64.deb"
                if original is None:
                    original = package.read_bytes()
                self.assertEqual(package.read_bytes(), original)
                data = subprocess.check_output(["dpkg-deb", "--fsys-tarfile", str(package)])
                with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                    for member in archive.getmembers():
                        expected = 0o755 if member.isdir() or member.name == "./usr/bin/envconsul" else 0o644
                        self.assertEqual(member.mode, expected, member.name)
        finally:
            os.umask(original_mask)

    def test_staging_rejects_changed_published_package(self):
        updater.build(self.output)
        updater.stage(self.output, self.repository)
        existing = self.repository / "apt/pool/main/e/envconsul/envconsul_1.2.3-1_arm64.deb"
        existing.write_bytes(b"existing content")
        with self.assertRaisesRegex(ValueError, "Immutable package"):
            updater.stage(self.output, self.repository)
        self.assertEqual(existing.read_bytes(), b"existing content")

    def test_staging_requires_both_architectures(self):
        updater.build(self.output)
        (self.output / "envconsul_1.2.3-1_arm64.deb").unlink()
        with self.assertRaisesRegex(ValueError, "exactly two"):
            updater.stage(self.output, self.repository)
        self.assertFalse(self.repository.exists())

    def test_rejects_unstable_or_invalid_release_tags(self):
        for values in (
            {"draft": True}, {"prerelease": True},
            {"tag_name": "v1.2.3-rc1"}, {"tag_name": "../example"},
        ):
            with self.subTest(values=values):
                self.release = {"tag_name": "v1.2.3", "draft": False, "prerelease": False, **values}
                with self.assertRaises(ValueError):
                    updater.build(self.output)
                self.assertFalse(self.output.exists())

    def test_corrupt_archive_does_not_build_packages(self):
        for platform, data in self.archives.items():
            with self.subTest(platform=platform):
                path = f"{self.base}_{platform}.zip"
                self.files[path] = b"corrupted archive"
                with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                    updater.build(self.output)
                self.assertFalse(self.output.exists())
                self.files[path] = data

    def test_missing_archive_does_not_build_packages(self):
        del self.files[f"{self.base}_linux_arm64.zip"]
        with self.assertRaises(HTTPError) as failure:
            updater.build(self.output)
        failure.exception.close()
        self.assertFalse(self.output.exists())

    def test_rejects_tampered_manifest(self):
        self.files[f"{self.base}_SHA256SUMS"] += b"tampered\n"
        with self.assertRaises(subprocess.CalledProcessError):
            updater.build(self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_wrong_signer(self):
        with patch.object(updater, "SIGNING_KEY", "0" * 40):
            with self.assertRaisesRegex(ValueError, "not signed by"):
                updater.build(self.output)
        self.assertFalse(self.output.exists())

    def revoked_public_key(self):
        with tempfile.TemporaryDirectory() as temporary:
            command = ["gpg", "--no-options", "--batch", "--homedir", temporary]
            subprocess.run(
                [*command, "--import"], input=self.public_key,
                check=True, capture_output=True,
            )
            revocation_path = self.keyring / "openpgp-revocs.d" / f"{self.fingerprint}.rev"
            revocation = revocation_path.read_bytes()
            revocation = revocation.replace(b":-----BEGIN", b"-----BEGIN")
            subprocess.run(
                [*command, "--import"], input=revocation,
                check=True, capture_output=True,
            )
            return subprocess.run(
                [*command, "--armor", "--export"], check=True, capture_output=True,
            ).stdout

    def test_rejects_revoked_signing_key(self):
        self.files["/key.asc"] = self.revoked_public_key()
        with self.assertRaisesRegex(ValueError, "not currently valid"):
            updater.build(self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_revoked_signer_even_with_another_good_signature(self):
        self.files["/key.asc"] = self.revoked_public_key()
        with tempfile.TemporaryDirectory() as temporary:
            command = ["gpg", "--no-options", "--batch", "--homedir", temporary]
            subprocess.run(
                [*command, "--pinentry-mode", "loopback", "--passphrase", "",
                 "--quick-generate-key", "Other Signer <other@example.invalid>",
                 "ed25519", "sign", "0"],
                check=True, capture_output=True,
            )
            self.files["/key.asc"] += subprocess.run(
                [*command, "--armor", "--export"], check=True, capture_output=True,
            ).stdout
            signature = subprocess.run(
                [*command, "--detach-sign", "--output", "-", str(self.directory / "SHA256SUMS")],
                check=True, capture_output=True,
            ).stdout
            self.files[f"{self.base}_SHA256SUMS.sig"] += signature
        with self.assertRaisesRegex(ValueError, "not signed by"):
            updater.build(self.output)
        self.assertFalse(self.output.exists())

    def test_requires_every_platform_in_signed_manifest(self):
        self.sign_manifest("\n".join(self.manifest.splitlines()[:-1]) + "\n")
        with self.assertRaisesRegex(KeyError, "envconsul_1.2.3_linux_arm64.zip"):
            updater.build(self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_duplicate_or_malformed_manifest_entries(self):
        for extra in (self.manifest.splitlines()[0] + "\n", "invalid checksum\n"):
            with self.subTest(extra=extra):
                self.sign_manifest(self.manifest + extra)
                with self.assertRaises(ValueError):
                    updater.build(self.output)
                self.assertFalse(self.output.exists())


if __name__ == "__main__":
    unittest.main()
