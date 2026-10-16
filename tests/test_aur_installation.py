# ---
# relationships:
#   verifies: package-repository-publishing
# ---
"""Execute the native AUR package and hooks from digest-bound release inputs."""

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

import test_repository
from test_repository import repository, FIXTURE


PROFILE = {
    "files": {
        "packaging/sample.service": "/usr/lib/systemd/system/sample.service",
        "packaging/sample.tmpfiles": "/usr/lib/tmpfiles.d/sample.conf",
        "packaging/sample.hook": "/usr/share/libalpm/hooks/sample.hook",
        "packaging/pre-install.sh": "/usr/lib/sample-tool/pre-install.sh",
    },
    "hooks": {
        name: f"packaging/{name}.sh"
        for name in ("pre-install", "post-install", "pre-remove", "post-remove")
    },
}


class InstallationTests(unittest.TestCase):
    def fixture(self, root, *, fault=None):
        manifest = json.loads(FIXTURE.read_text())
        manifest["publish"]["aur"]["installation"] = copy.deepcopy(PROFILE)
        content = {
            "sample-tool": b"#!/bin/sh\nexit 0\n",
            "LICENSE": b"sample license\n",
            "README.md": b"sample usage\n",
            "packaging/sample.service": b"[Service]\nExecStart=/usr/bin/sample-tool\n",
            "packaging/sample.tmpfiles": b"d /run/sample 0750 root root -\n",
            "packaging/sample.hook": b"[Action]\nWhen=PreTransaction\nAbortOnFail\n",
        }
        for name, path in PROFILE["hooks"].items():
            content[path] = (
                "#!/bin/sh\nset -eu\n"
                f"printf '%s\\n' '{name}' \"$@\" >> \"$HOOK_LOG\"\n"
                "if [ \"${FAIL_HOOK:-}\" = yes ]; then exit 17; fi\n"
            ).encode()
        for artifact in manifest["artifacts"]:
            if artifact["format"] != "tar.gz":
                continue
            path = root / artifact["filename"]
            with tarfile.open(path, "w:gz") as archive:
                for name, data in content.items():
                    if fault == "missing" and name == "packaging/sample.service":
                        continue
                    if fault == "mismatch" and artifact["arch"] == "arm64" and name == "packaging/sample.service":
                        data += b"# different\n"
                    if fault == "nul" and name == "packaging/pre-install.sh":
                        data += b"\0"
                    if fault == "encoding" and name == "packaging/pre-install.sh":
                        data += b"\xff"
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    if fault == "link" and name == "packaging/sample.service":
                        member.type = tarfile.SYMTYPE
                        member.linkname = "/etc/passwd"
                        member.size = 0
                        archive.addfile(member)
                    else:
                        archive.addfile(member, io.BytesIO(data))
                        if fault == "duplicate" and name == "packaging/sample.service":
                            archive.addfile(member, io.BytesIO(data))
            artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        path = root / "manifest.json"
        path.write_text(json.dumps(manifest))
        return manifest, path, content

    def test_package_and_all_native_lifecycle_functions_execute(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest, content = self.fixture(root)
            rendered = root / "rendered"
            repository.render_aur(manifest, rendered, root)
            first = {p.name: p.read_bytes() for p in rendered.iterdir()}
            repository.render_aur(manifest, rendered, root)
            self.assertEqual(first, {p.name: p.read_bytes() for p in rendered.iterdir()})
            src = root / "src"
            for name, data in content.items():
                target = src / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            log = root / "hooks"
            environment = os.environ | {"srcdir": str(src), "pkgdir": str(root / "pkg"), "HOOK_LOG": str(log)}
            subprocess.run(["bash", "-euc", 'source "$1"; package', "--", str(rendered / "PKGBUILD")], env=environment, check=True)
            self.assertFalse(log.exists(), "rendering and building must not run lifecycle code")
            for source, destination in PROFILE["files"].items():
                installed = root / "pkg" / destination.lstrip("/")
                self.assertEqual(installed.read_bytes(), content[source])
                self.assertEqual(installed.stat().st_mode & 0o777, 0o644)
            script = rendered / "sample-tool-bin.install"
            for function, expected in (
                ("pre_install", "pre-install"), ("pre_upgrade", "pre-install"),
                ("post_install", "post-install"), ("post_upgrade", "post-install"),
                ("pre_remove", "pre-remove"), ("post_remove", "post-remove"),
            ):
                with self.subTest(function=function):
                    subprocess.run(["bash", "-euc", 'source "$1"; "$2" 1.2.3 1.2.2', "--", str(script), function], env=environment, check=True)
                    arguments = [expected] + (["remove"] if function.endswith("_remove") else []) + ["1.2.3", "1.2.2"]
                    self.assertEqual(log.read_text().splitlines(), arguments)
                    log.unlink()
                    result = subprocess.run(["bash", "-c", 'source "$1"; "$2" 1.2.3', "--", str(script), function], env=environment | {"FAIL_HOOK": "yes"})
                    self.assertEqual(result.returncode, 17)
                    log.unlink()

    def test_publisher_transports_installation_sources_to_aur_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest, _ = self.fixture(root)
            binaries = root / "bin"
            binaries.mkdir()
            runner = root / "runner"
            runner.mkdir()
            sent = root / "sent"
            commands = {
                "docker": "printf 'pkgbase = sample-tool-bin\\n'",
                "ssh-keyscan": "printf 'host key\\n'",
                "ssh-keygen": "printf '256 SHA256:RFzBCUItH9LZS0cKB5UE6ceAYhBD5C8GeOBip8Z11+4 host (ED25519)\\n'",
                "git": '''if [[ "$1" == clone ]]; then
  mkdir -p "$3/.git"
elif [[ "$1" == -C && "$3" == add ]]; then
  [[ "$4" == --all ]] || exit 18
  mkdir -p "$SENT"
  cp "$2/PKGBUILD" "$2/.SRCINFO" "$2/sample-tool-bin.install" "$SENT/"
elif [[ "$1" == -C && "$3" == diff ]]; then
  exit 1
fi''',
            }
            for name, body in commands.items():
                path = binaries / name
                path.write_text("#!/usr/bin/env bash\nset -eu\n" + body + "\n")
                path.chmod(0o755)
            repository_root = Path(__file__).resolve().parents[1]
            subprocess.run([
                str(repository_root / "scripts/publish-aur.sh"),
                str(manifest), str(root),
            ], cwd=repository_root, env=os.environ | {
                "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
                "RUNNER_TEMP": str(runner), "AUR_ACCOUNT": "package-publisher",
                "AUR_PRIVATE_KEY": "test-private-key", "SENT": str(sent),
            }, check=True)
            self.assertIn("install='sample-tool-bin.install'", (sent / "PKGBUILD").read_text())
            self.assertIn("pre_upgrade()", (sent / "sample-tool-bin.install").read_text())
            self.assertTrue((sent / ".SRCINFO").is_file())

    def test_each_archive_must_match_its_digest(self):
        for arch in ("amd64", "arm64"):
            with self.subTest(arch=arch), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                manifest, path, _ = self.fixture(root)
                artifact = next(a for a in manifest["artifacts"] if a["format"] == "tar.gz" and a["arch"] == arch)
                archive = root / artifact["filename"]
                archive.write_bytes(archive.read_bytes() + b"tampered")
                with self.assertRaisesRegex(repository.ManifestError, "digest mismatch"):
                    repository.render_aur(path, root / "out", root)

    def test_archives_must_agree_and_contain_regular_unique_sources(self):
        for fault, expected in (("missing", "missing"), ("link", "non-regular"), ("duplicate", "duplicate"), ("mismatch", "disagree"), ("nul", "NUL"), ("encoding", "UTF-8")):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                _, manifest, _ = self.fixture(root, fault=fault)
                with self.assertRaisesRegex(repository.ManifestError, expected):
                    repository.render_aur(manifest, root / "out", root)

    def test_profile_needs_downloaded_archives(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest, _ = self.fixture(root)
            with self.assertRaisesRegex(repository.ManifestError, "requires downloaded"):
                repository.render_aur(manifest, root / "out")

    def test_profile_is_closed_and_paths_are_confined(self):
        for section, key, value in (
            ("files", "packaging/sample.service", "/etc/sudoers"),
            ("files", "packaging/sample.service", "/usr/lib/systemd/system/../sample.service"),
            ("files", "../sample.service", "/usr/lib/systemd/system/sample.service"),
            ("hooks", "pre-install", "packaging/../sample.sh"),
            ("hooks", "extra", "packaging/sample.sh"),
        ):
            profile = copy.deepcopy(PROFILE)
            profile[section][key] = value
            with self.subTest(section=section, key=key, value=value), self.assertRaises(repository.ManifestError):
                repository.validate_installation(profile)
        profile = copy.deepcopy(PROFILE)
        profile["files"]["packaging/another.service"] = profile["files"]["packaging/sample.service"]
        with self.assertRaisesRegex(repository.ManifestError, "duplicated"):
            repository.validate_installation(profile)

    def test_allowlist_binds_installation_and_accepts_historical_manifests(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _, _ = self.fixture(root)
            allowlist = test_repository.ManifestTests().allowlist(root, manifest)
            repository.validate_allowed_product(manifest, allowlist)
            changed = copy.deepcopy(manifest)
            changed["publish"]["aur"]["installation"]["hooks"]["pre-install"] = "packaging/alternate.sh"
            with self.assertRaisesRegex(repository.ManifestError, "disagrees"):
                repository.validate_allowed_product(changed, allowlist)
            del manifest["publish"]["aur"]["installation"]
            repository.validate_allowed_product(manifest, allowlist)

    def test_stage_rejects_bad_installation_before_staging_native_packages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, manifest, _ = self.fixture(root, fault="mismatch")
            with self.assertRaisesRegex(repository.ManifestError, "disagree"):
                repository.stage_artifacts(manifest, root, root / "stage", root / "key")
            self.assertFalse((root / "stage").exists())


if __name__ == "__main__":
    unittest.main()
