#!/usr/bin/env python3
# relationships:
#   implements: package-repository-publishing
"""Build APT packages from authenticated upstream envconsul release binaries."""

import argparse
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from urllib.request import Request, urlopen
import zipfile


LATEST_RELEASE = "https://api.github.com/repos/hashicorp/envconsul/releases/latest"
RELEASES = "https://releases.hashicorp.com/envconsul"
PUBLIC_KEY = "https://www.hashicorp.com/.well-known/pgp-key.txt"
# https://developer.hashicorp.com/well-architected-framework/verify-hashicorp-binary
SIGNING_KEY = "C874011F0AB405110D02105534365D9472D7468F"
PLATFORMS = ("linux_amd64", "linux_arm64")


def download(url, *, github=False):
    headers = {"User-Agent": "repo.wyrd.foo-envconsul"}
    if github:
        headers["Accept"] = "application/vnd.github+json"
        token = os.environ.get("GH_TOKEN")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    with urlopen(Request(url, headers=headers)) as response:
        return response.read()


def verify_signature(directory):
    keyring = directory / "keyring"
    keyring.mkdir(mode=0o700)
    command = [
        "gpg", "--no-options", "--batch", "--no-auto-key-retrieve",
        "--homedir", str(keyring),
    ]
    subprocess.run(
        [*command, "--import", str(directory / "key.asc")],
        check=True, capture_output=True,
    )
    result = subprocess.run(
        [*command, "--status-fd", "1", "--verify",
         str(directory / "SHA256SUMS.sig"), str(directory / "SHA256SUMS")],
        check=True, capture_output=True, text=True,
    )
    good_signers = {
        line.split()[2]
        for line in result.stdout.splitlines()
        if line.startswith("[GNUPG:] GOODSIG ")
    }
    if not good_signers:
        raise ValueError("SHA256SUMS signature is not currently valid")
    if not any(
        line.startswith("[GNUPG:] VALIDSIG ") and line.split()[-1] == SIGNING_KEY
        and any(line.split()[2].endswith(signer) for signer in good_signers)
        for line in result.stdout.splitlines()
    ):
        raise ValueError("SHA256SUMS was not signed by the HashiCorp signing key")


def release_checksums(version):
    base = f"{RELEASES}/{version}/envconsul_{version}"
    with tempfile.TemporaryDirectory(prefix="envconsul-") as temporary:
        directory = Path(temporary)
        for name, url in (
            ("SHA256SUMS", f"{base}_SHA256SUMS"),
            ("SHA256SUMS.sig", f"{base}_SHA256SUMS.sig"),
            ("key.asc", PUBLIC_KEY),
        ):
            (directory / name).write_bytes(download(url))
        verify_signature(directory)
        checksums = {}
        for line in (directory / "SHA256SUMS").read_text().splitlines():
            match = re.fullmatch(r"([0-9a-f]{64})\s+\*?(\S+)", line)
            if not match:
                raise ValueError(f"Invalid SHA256SUMS entry: {line!r}")
            digest, filename = match.groups()
            if filename in checksums:
                raise ValueError(f"Duplicate SHA256SUMS entry: {filename}")
            checksums[filename] = digest
        verified = {}
        for platform in PLATFORMS:
            filename = f"envconsul_{version}_{platform}.zip"
            expected = checksums[filename]
            archive = download(f"{base}_{platform}.zip")
            if hashlib.sha256(archive).hexdigest() != expected:
                raise ValueError(f"SHA256 mismatch: {filename}")
            verified[platform] = archive
        return verified

def build_package(version, platform, archive, output):
    arch = platform.removeprefix("linux_")
    with tempfile.TemporaryDirectory(prefix="envconsul-deb-") as temporary:
        root = Path(temporary) / "package"
        binary = root / "usr/bin/envconsul"
        license_file = root / "usr/share/doc/envconsul/copyright"
        control = root / "DEBIAN/control"
        for path in (binary, license_file, control):
            path.parent.mkdir(parents=True, exist_ok=True)
        # Read only the expected regular files; never extract archive paths.
        with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
            for member, destination in (("envconsul", binary), ("LICENSE.txt", license_file)):
                if zipped.namelist().count(member) != 1:
                    raise ValueError(f"Expected exactly one {member} in {platform}")
                destination.write_bytes(zipped.read(member))
        control.write_text(
            f"Package: envconsul\nVersion: {version}-1\nArchitecture: {arch}\n"
            "Maintainer: Wyrd Company <support@wyrd.company>\n"
            "Section: utils\nPriority: optional\n"
            f"Installed-Size: {(binary.stat().st_size + license_file.stat().st_size + 1023) // 1024}\n"
            "Homepage: https://github.com/hashicorp/envconsul\n"
            "Description: Launch a subprocess with environment variables from Consul and Vault\n"
        )
        for path in [*root.rglob("*"), root]:
            path.chmod(0o755 if path.is_dir() or path == binary else 0o644)
            os.utime(path, (0, 0))
        destination = output / f"envconsul_{version}-1_{arch}.deb"
        subprocess.run(
            ["dpkg-deb", "--root-owner-group", "-Zxz", "--build", str(root), str(destination)],
            env=os.environ | {"SOURCE_DATE_EPOCH": "0"}, check=True,
        )


def build(output):
    release = json.loads(download(LATEST_RELEASE, github=True))
    tag = release["tag_name"]
    if release["draft"] or release["prerelease"] or not re.fullmatch(r"v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", tag):
        raise ValueError(f"Expected a stable release tag, got {tag!r}")
    version = tag.removeprefix("v")
    archives = release_checksums(version)
    output.mkdir(parents=True, exist_ok=True)
    for platform, archive in archives.items():
        build_package(version, platform, archive, output)


def stage(packages, repository):
    sources = sorted(packages.glob("envconsul_*.deb"))
    if len(sources) != 2:
        raise ValueError("Expected exactly two envconsul DEB packages")
    identities = []
    destination = repository / "apt/pool/main/e/envconsul"
    for source in sources:
        fields = subprocess.run(
            ["dpkg-deb", "--field", str(source), "Package", "Version", "Architecture"],
            check=True, capture_output=True, text=True,
        ).stdout
        identity = dict(line.split(": ", 1) for line in fields.splitlines())
        if identity["Package"] != "envconsul" or identity["Architecture"] not in ("amd64", "arm64"):
            raise ValueError(f"Unexpected package metadata: {source.name}")
        if source.name != f"envconsul_{identity['Version']}_{identity['Architecture']}.deb":
            raise ValueError(f"Package filename disagrees with metadata: {source.name}")
        identities.append(identity)
        target = destination / source.name
        if target.exists() and target.read_bytes() != source.read_bytes():
            raise ValueError(f"Immutable package already has different content: {source.name}")
    if {item["Architecture"] for item in identities} != {"amd64", "arm64"} or len({item["Version"] for item in identities}) != 1:
        raise ValueError("Expected matching amd64 and arm64 package versions")
    missing = [source for source in sources if not (destination / source.name).exists()]
    if not missing:
        return False
    destination.mkdir(parents=True, exist_ok=True)
    for source in missing:
        shutil.copyfile(source, destination / source.name)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "pubkey.gpg", repository / "pubkey.gpg")
    (repository / ".new-rpms").write_text("")
    # The shared index builder also refreshes the existing RPM metadata.
    (repository / "rpm").mkdir(exist_ok=True)
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    builder = commands.add_parser("build")
    builder.add_argument("output", type=Path)
    stager = commands.add_parser("stage")
    stager.add_argument("packages", type=Path)
    stager.add_argument("repository", type=Path)
    stager.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    if args.command == "build":
        build(args.output)
    else:
        changed = stage(args.packages, args.repository)
        if args.github_output:
            with args.github_output.open("a") as output:
                output.write(f"changed={str(changed).lower()}\n")
        print("Staged new envconsul packages" if changed else "envconsul is already published")
