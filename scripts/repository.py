#!/usr/bin/env python3
# ---
# relationships:
#   implements: package-repository-publishing
# ---

"""Validate, stage, and render inputs for the package repository publisher."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


SEMVER = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z")
SLUG = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
REPOSITORY = re.compile(r"wyrd-company/[a-z][a-z0-9.-]*\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")
DIGEST = re.compile(r"[0-9a-f]{64}\Z")
FILENAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,254}\Z")
MAINTAINER = re.compile(r"[^<>]+ <[^<>@ ]+@[^<>@ ]+>\Z")
MANIFEST_PATH = re.compile(
    r"releases/[a-z][a-z0-9-]*/[0-9]+\.[0-9]+\.[0-9]+\.json\Z"
)
FULL_SHA = re.compile(r"[0-9a-f]{40}\Z")
EXPECTED_ARTIFACTS = {
    ("archive", "tar.gz", "amd64"),
    ("archive", "tar.gz", "arm64"),
    ("package", "deb", "amd64"),
    ("package", "deb", "arm64"),
    ("package", "rpm", "amd64"),
    ("package", "rpm", "arm64"),
}
MAX_ARTIFACT_BYTES = 512 * 1024 * 1024


class ManifestError(ValueError):
    """The release manifest does not satisfy the closed publication contract."""


def require_keys(value: dict[str, Any], expected: set[str], context: str) -> None:
    actual = set(value)
    if actual != expected:
        raise ManifestError(
            f"{context} fields must be {sorted(expected)}; got {sorted(actual)}"
        )


def require_text(value: Any, context: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ManifestError(f"{context} must be non-empty text of at most {maximum} characters")
    if not value.isprintable():
        raise ManifestError(f"{context} contains a forbidden control character")
    return value


def require_maintainer(value: Any) -> str:
    maintainer = require_text(value, "package.maintainer", 256)
    if not MAINTAINER.fullmatch(maintainer):
        raise ManifestError("package.maintainer must have the form Name <address>")
    return maintainer


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read release manifest: {error}") from error
    if not isinstance(value, dict):
        raise ManifestError("release manifest must be a JSON object")
    validate_manifest(value)
    return value


def validate_manifest(manifest: dict[str, Any]) -> None:
    require_keys(
        manifest,
        {"schema_version", "product", "version", "tag", "source", "package", "publish", "artifacts"},
        "manifest",
    )
    if manifest["schema_version"] != 1:
        raise ManifestError("schema_version must be 1")
    product = require_text(manifest["product"], "product", 64)
    if not SLUG.fullmatch(product):
        raise ManifestError("product is not a lowercase kebab slug")
    version = require_text(manifest["version"], "version", 64)
    if not SEMVER.fullmatch(version) or manifest["tag"] != version:
        raise ManifestError("version and tag must be the same stable bare semantic version")

    source = manifest["source"]
    if not isinstance(source, dict):
        raise ManifestError("source must be an object")
    require_keys(source, {"repository", "commit"}, "source")
    repository = require_text(source["repository"], "source.repository", 128)
    if not REPOSITORY.fullmatch(repository):
        raise ManifestError("source.repository must name a wyrd-company GitHub repository")
    if not isinstance(source["commit"], str) or not COMMIT.fullmatch(source["commit"]):
        raise ManifestError("source.commit must be a lowercase forty-character Git commit")

    package = manifest["package"]
    if not isinstance(package, dict):
        raise ManifestError("package must be an object")
    require_keys(
        package,
        {"name", "binary", "description", "homepage", "license", "maintainer"},
        "package",
    )
    for field in ("name", "binary"):
        text = require_text(package[field], f"package.{field}", 64)
        if not SLUG.fullmatch(text):
            raise ManifestError(f"package.{field} is not a lowercase kebab slug")
    require_text(package["description"], "package.description", 256)
    homepage = urllib.parse.urlsplit(require_text(package["homepage"], "package.homepage", 512))
    if homepage.scheme != "https" or not homepage.netloc or homepage.username or homepage.password:
        raise ManifestError("package.homepage must be an absolute HTTPS URL")
    require_text(package["license"], "package.license", 64)
    require_maintainer(package["maintainer"])

    publish = manifest["publish"]
    if not isinstance(publish, dict):
        raise ManifestError("publish must be an object")
    require_keys(publish, {"apt", "rpm", "aur"}, "publish")
    if publish["apt"] != {"suite": "stable", "component": "main"}:
        raise ManifestError("publish.apt must select stable/main")
    if publish["rpm"] != {"channel": "stable"}:
        raise ManifestError("publish.rpm must select stable")
    aur = publish["aur"]
    if not isinstance(aur, dict) or set(aur) != {"package"}:
        raise ManifestError("publish.aur must contain only package")
    aur_package = require_text(aur["package"], "publish.aur.package", 80)
    if aur_package != f"{package['name']}-bin":
        raise ManifestError("publish.aur.package must be the native package name plus -bin")

    artifacts = manifest["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(EXPECTED_ARTIFACTS):
        raise ManifestError("artifacts must contain the six Linux archive, DEB, and RPM records")
    combinations: set[tuple[str, str, str]] = set()
    filenames: set[str] = set()
    for index, artifact in enumerate(artifacts):
        context = f"artifacts[{index}]"
        if not isinstance(artifact, dict):
            raise ManifestError(f"{context} must be an object")
        require_keys(artifact, {"kind", "format", "os", "arch", "filename", "url", "sha256"}, context)
        if artifact["os"] != "linux":
            raise ManifestError(f"{context}.os must be linux")
        combination = (artifact["kind"], artifact["format"], artifact["arch"])
        if combination not in EXPECTED_ARTIFACTS or combination in combinations:
            raise ManifestError(f"{context} has an unexpected or duplicate artifact role")
        combinations.add(combination)
        filename = require_text(artifact["filename"], f"{context}.filename", 255)
        if not FILENAME.fullmatch(filename) or filename in filenames:
            raise ManifestError(f"{context}.filename is unsafe or duplicated")
        expected_suffix = {"tar.gz": ".tar.gz", "deb": ".deb", "rpm": ".rpm"}[
            artifact["format"]
        ]
        if not filename.endswith(expected_suffix):
            raise ManifestError(f"{context}.filename does not match its format")
        filenames.add(filename)
        digest = artifact["sha256"]
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ManifestError(f"{context}.sha256 must be a lowercase SHA-256 digest")
        validate_release_url(artifact["url"], repository, version, filename, context)
    if combinations != EXPECTED_ARTIFACTS:
        raise ManifestError("artifacts do not cover the required formats and architectures")


def validate_release_url(value: Any, repository: str, tag: str, filename: str, context: str) -> None:
    url = urllib.parse.urlsplit(require_text(value, f"{context}.url", 1024))
    expected_path = f"/{repository}/releases/download/{tag}/{filename}"
    if (
        url.scheme != "https"
        or url.netloc != "github.com"
        or url.path != expected_path
        or url.query
        or url.fragment
        or url.username
        or url.password
    ):
        raise ManifestError(f"{context}.url must be the exact public GitHub Release asset URL")


def canonical_bytes(manifest: dict[str, Any]) -> bytes:
    return (json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8")


def submit_manifest(manifest_path: Path, repository_root: Path) -> tuple[str, str]:
    manifest = load_manifest(manifest_path)
    destination = repository_root / "releases" / manifest["product"] / f"{manifest['version']}.json"
    content = canonical_bytes(manifest)
    if destination.exists():
        if destination.read_bytes() != content:
            raise ManifestError(f"immutable release manifest already exists with different content: {destination}")
        return "unchanged", str(destination.relative_to(repository_root))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(content)
    return "created", str(destination.relative_to(repository_root))


def load_queued_manifest(repository_root: Path, relative_path: str) -> dict[str, Any]:
    manifest_path = repository_root / relative_path
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ManifestError(f"release manifest must be a regular file: {relative_path}")
    manifest = load_manifest(manifest_path)
    expected = f"releases/{manifest['product']}/{manifest['version']}.json"
    if relative_path != expected:
        raise ManifestError(f"release manifest content belongs at {expected}, not {relative_path}")
    if manifest_path.read_bytes() != canonical_bytes(manifest):
        raise ManifestError(f"release manifest is not canonical JSON: {relative_path}")
    return manifest


def load_product_allowlist(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"cannot read product allowlist: {error}") from error
    if not isinstance(value, dict):
        raise ManifestError("product allowlist must be an object")
    require_keys(value, {"schema_version", "x-relationships", "products"}, "allowlist")
    if value["schema_version"] != 1 or not isinstance(value["products"], dict):
        raise ManifestError("product allowlist must use schema version 1")
    return value["products"]


def validate_allowed_product(manifest: dict[str, Any], allowlist_path: Path) -> None:
    products = load_product_allowlist(allowlist_path)
    allowed = products.get(manifest["product"])
    if not isinstance(allowed, dict):
        raise ManifestError(f"product is not approved for publication: {manifest['product']}")
    require_keys(allowed, {"source_repository", "package", "publish"}, "allowed product")
    expected = {
        "source_repository": manifest["source"]["repository"],
        "package": manifest["package"],
        "publish": manifest["publish"],
    }
    if allowed != expected:
        raise ManifestError(f"release manifest disagrees with the allowlist for {manifest['product']}")


def git_output(repository: Path, arguments: list[str]) -> str:
    return command_output(["git", "-C", str(repository), *arguments])


def inbox_ref(repository: Path) -> str:
    for reference in (
        "refs/remotes/origin/release-manifests",
        "refs/heads/release-manifests",
    ):
        try:
            git_output(repository, ["rev-parse", "--verify", f"{reference}^{{commit}}"])
            return reference
        except subprocess.CalledProcessError:
            continue
    raise ManifestError("release-manifests inbox branch is unavailable")


def validate_queue_commit(
    repository: Path,
    commit: str,
    relative_path: str,
    allowlist_path: Path,
) -> dict[str, Any]:
    if not FULL_SHA.fullmatch(commit):
        raise ManifestError("inbox commit must be a full lowercase Git SHA")
    if not MANIFEST_PATH.fullmatch(relative_path):
        raise ManifestError("invalid release manifest path")
    head = git_output(repository, ["rev-parse", "HEAD"])
    if head != commit:
        raise ManifestError("queue checkout does not match the requested inbox commit")
    parents = git_output(repository, ["rev-list", "--parents", "-n", "1", commit]).split()
    if len(parents) != 2:
        raise ManifestError("inbox commit must have exactly one parent")
    reference = inbox_ref(repository)
    try:
        git_output(repository, ["merge-base", "--is-ancestor", commit, reference])
    except subprocess.CalledProcessError as error:
        raise ManifestError("inbox commit is not reachable from release-manifests") from error
    changes = git_output(
        repository,
        [
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "--no-renames",
            "-r",
            f"{commit}^",
            commit,
        ],
    ).splitlines()
    if changes != [f"A\t{relative_path}"]:
        raise ManifestError("inbox commit must add exactly the requested release manifest")
    if git_output(repository, ["log", "--format=%H", f"{commit}^", "--", relative_path]):
        raise ManifestError("inbox release path existed earlier and is immutable")
    mode = git_output(repository, ["ls-tree", commit, "--", relative_path]).split()[0]
    if mode != "100644":
        raise ManifestError("inbox release manifest must be a regular file")
    manifest = load_queued_manifest(repository, relative_path)
    validate_allowed_product(manifest, allowlist_path)
    return manifest


def resolve_source_tag(manifest: dict[str, Any]) -> str:
    repository = manifest["source"]["repository"]
    tag = manifest["tag"]
    try:
        output = command_output(
            [
                "git",
                "ls-remote",
                "--exit-code",
                f"https://github.com/{repository}.git",
                f"refs/tags/{tag}",
                f"refs/tags/{tag}^{{}}",
            ]
        )
    except subprocess.CalledProcessError as error:
        raise ManifestError(f"cannot resolve source tag {tag}") from error
    refs = dict(line.split("\t", maxsplit=1)[::-1] for line in output.splitlines())
    resolved = refs.get(f"refs/tags/{tag}^{{}}", refs.get(f"refs/tags/{tag}", ""))
    if not FULL_SHA.fullmatch(resolved):
        raise ManifestError(f"cannot resolve source tag {tag}")
    return resolved


def verify_source_tag(manifest: dict[str, Any]) -> None:
    resolved = resolve_source_tag(manifest)
    if resolved != manifest["source"]["commit"]:
        raise ManifestError("source tag does not resolve to source.commit")


def parse_r2_api(value: str) -> tuple[str, str]:
    parsed = urllib.parse.urlsplit(value)
    parts = [urllib.parse.unquote(part) for part in parsed.path.split("/") if part]
    if (
        parsed.scheme != "https"
        or not parsed.netloc.endswith(".r2.cloudflarestorage.com")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or len(parts) != 1
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,62}", parts[0])
    ):
        raise ManifestError(
            "REPO_WYRD_FOO_S3_API must be https://<account>.r2.cloudflarestorage.com/<bucket>"
        )
    return f"https://{parsed.netloc}", parts[0]


def configure_r2(github_env: Path | None) -> dict[str, str]:
    value = os.environ.get("REPO_WYRD_FOO_S3_API", "")
    endpoint, bucket = parse_r2_api(value)
    result = {"R2_ENDPOINT": endpoint, "R2_BUCKET": bucket}
    if github_env is not None:
        with github_env.open("a", encoding="utf-8") as output:
            for key, item in result.items():
                output.write(f"{key}={item}\n")
    return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_artifacts(manifest_path: Path, output: Path) -> None:
    manifest = load_manifest(manifest_path)
    output.mkdir(parents=True, exist_ok=True)
    for artifact in manifest["artifacts"]:
        destination = output / artifact["filename"]
        if destination.exists() and sha256_file(destination) == artifact["sha256"]:
            continue
        with tempfile.NamedTemporaryFile(dir=output, delete=False) as temporary:
            temporary_path = Path(temporary.name)
            request = urllib.request.Request(artifact["url"], headers={"User-Agent": "repo.wyrd.foo/1"})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    validate_download_target(response.geturl())
                    total = 0
                    digest = hashlib.sha256()
                    while chunk := response.read(1024 * 1024):
                        total += len(chunk)
                        if total > MAX_ARTIFACT_BYTES:
                            raise ManifestError(f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes: {artifact['filename']}")
                        temporary.write(chunk)
                        digest.update(chunk)
                if digest.hexdigest() != artifact["sha256"]:
                    raise ManifestError(f"artifact digest mismatch: {artifact['filename']}")
                os.replace(temporary_path, destination)
            finally:
                temporary_path.unlink(missing_ok=True)


def validate_download_target(value: str) -> None:
    final_url = urllib.parse.urlsplit(value)
    if final_url.scheme != "https" or not (
        final_url.netloc == "github.com"
        or final_url.netloc.endswith(".githubusercontent.com")
    ):
        raise ManifestError("release download redirected outside GitHub HTTPS hosts")


def command_output(arguments: list[str]) -> str:
    completed = subprocess.run(arguments, check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.strip()


def archive_members(path: Path) -> set[str]:
    with tarfile.open(path, "r:gz") as archive:
        members: set[str] = set()
        for member in archive.getmembers():
            member_path = Path(member.name)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise ManifestError(f"archive contains an unsafe path: {path.name}")
            if member.isdir():
                continue
            if not member.isfile():
                raise ManifestError(f"archive contains a non-regular entry: {path.name}")
            normalized = member_path.as_posix()
            if normalized in members:
                raise ManifestError(f"archive contains a duplicate path: {path.name}")
            members.add(normalized)
        return members


def stage_artifacts(manifest_path: Path, artifacts_root: Path, stage: Path, public_key: Path) -> None:
    manifest = load_manifest(manifest_path)
    package = manifest["package"]
    new_rpms: list[str] = []
    (stage / "artifacts" / manifest["product"] / manifest["version"]).mkdir(parents=True, exist_ok=True)
    for artifact in manifest["artifacts"]:
        source = artifacts_root / artifact["filename"]
        if not source.is_file() or sha256_file(source) != artifact["sha256"]:
            raise ManifestError(f"missing or invalid downloaded artifact: {artifact['filename']}")
        if artifact["format"] == "tar.gz":
            members = archive_members(source)
            required = {package["binary"], "LICENSE", "README.md"}
            if not required.issubset(members):
                raise ManifestError(f"archive is missing required files {sorted(required - members)}: {source.name}")
            destination = stage / "artifacts" / manifest["product"] / manifest["version"] / source.name
        elif artifact["format"] == "deb":
            fields = [
                command_output(["dpkg-deb", "-f", str(source), field])
                for field in ("Package", "Version", "Architecture")
            ]
            expected_arch = {"amd64": "amd64", "arm64": "arm64"}[artifact["arch"]]
            if fields != [package["name"], manifest["version"], expected_arch]:
                raise ManifestError(f"DEB metadata disagrees with manifest: {source.name}")
            deb_filename = f"{package['name']}_{manifest['version']}_{expected_arch}.deb"
            destination = (
                stage
                / "apt"
                / "pool"
                / "main"
                / package["name"][0]
                / package["name"]
                / deb_filename
            )
        else:
            fields = command_output(
                ["rpm", "-qp", "--qf", "%{NAME}\n%{VERSION}\n%{ARCH}\n", str(source)]
            ).splitlines()
            expected_arch = {"amd64": "x86_64", "arm64": "aarch64"}[artifact["arch"]]
            if fields != [package["name"], manifest["version"], expected_arch]:
                raise ManifestError(f"RPM metadata disagrees with manifest: {source.name}")
            destination = stage / "rpm" / "stable" / expected_arch / "Packages" / source.name
            new_rpms.append(str(destination))
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if artifact["format"] == "rpm":
                existing = command_output(
                    ["rpm", "-qp", "--qf", "%{NAME}\n%{VERSION}\n%{ARCH}\n", str(destination)]
                ).splitlines()
                if existing != fields:
                    raise ManifestError(f"immutable RPM destination already contains another package: {source.name}")
                new_rpms.pop()
                continue
            if sha256_file(destination) != artifact["sha256"]:
                raise ManifestError(f"immutable destination already contains different content: {source.name}")
            continue
        shutil.copyfile(source, destination)
    shutil.copyfile(public_key, stage / "pubkey.gpg")
    (stage / ".new-rpms").write_text("".join(f"{path}\n" for path in new_rpms), encoding="utf-8")


def shell_single_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def render_aur(manifest_path: Path, output: Path) -> None:
    manifest = load_manifest(manifest_path)
    package = manifest["package"]
    aur_name = manifest["publish"]["aur"]["package"]
    archives = {item["arch"]: item for item in manifest["artifacts"] if item["format"] == "tar.gz"}
    base_url = f"https://repo.wyrd.foo/artifacts/{manifest['product']}/{manifest['version']}"
    output.mkdir(parents=True, exist_ok=True)
    maintainer = package["maintainer"]
    pkgbuild = f"""# Maintainer: {maintainer}
pkgname={shell_single_quote(aur_name)}
pkgver={shell_single_quote(manifest['version'])}
pkgrel=1
pkgdesc={shell_single_quote(package['description'])}
arch=('x86_64' 'aarch64')
url={shell_single_quote(package['homepage'])}
license=({shell_single_quote(package['license'])})
provides=({shell_single_quote(package['name'])})
conflicts=({shell_single_quote(package['name'])})
options=('!strip')
source_x86_64=("${{pkgname}}-${{pkgver}}-x86_64.tar.gz::{base_url}/{archives['amd64']['filename']}")
source_aarch64=("${{pkgname}}-${{pkgver}}-aarch64.tar.gz::{base_url}/{archives['arm64']['filename']}")
sha256sums_x86_64=({shell_single_quote(archives['amd64']['sha256'])})
sha256sums_aarch64=({shell_single_quote(archives['arm64']['sha256'])})

package() {{
  install -Dm755 "${{srcdir}}/{package['binary']}" "${{pkgdir}}/usr/bin/{package['binary']}"
  install -Dm644 "${{srcdir}}/LICENSE" "${{pkgdir}}/usr/share/licenses/${{pkgname}}/LICENSE"
  install -Dm644 "${{srcdir}}/README.md" "${{pkgdir}}/usr/share/doc/{package['name']}/README.md"
}}
"""
    (output / "PKGBUILD").write_text(pkgbuild, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    canonicalize = subparsers.add_parser("canonicalize")
    canonicalize.add_argument("manifest", type=Path)
    submit = subparsers.add_parser("submit")
    submit.add_argument("manifest", type=Path)
    submit.add_argument("repository", type=Path)
    queue = subparsers.add_parser("validate-queue")
    queue.add_argument("repository", type=Path)
    queue.add_argument("commit")
    queue.add_argument("manifest")
    queue.add_argument("allowlist", type=Path)
    r2 = subparsers.add_parser("configure-r2")
    r2.add_argument("--github-env", type=Path)
    download = subparsers.add_parser("download")
    download.add_argument("manifest", type=Path)
    download.add_argument("output", type=Path)
    stage = subparsers.add_parser("stage")
    stage.add_argument("manifest", type=Path)
    stage.add_argument("artifacts", type=Path)
    stage.add_argument("output", type=Path)
    stage.add_argument("public_key", type=Path)
    aur = subparsers.add_parser("render-aur")
    aur.add_argument("manifest", type=Path)
    aur.add_argument("output", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "validate":
            load_manifest(arguments.manifest)
        elif arguments.command == "canonicalize":
            sys.stdout.buffer.write(canonical_bytes(load_manifest(arguments.manifest)))
        elif arguments.command == "submit":
            state, path = submit_manifest(arguments.manifest, arguments.repository)
            print(f"{state}\t{path}")
        elif arguments.command == "validate-queue":
            manifest = validate_queue_commit(
                arguments.repository,
                arguments.commit,
                arguments.manifest,
                arguments.allowlist,
            )
            verify_source_tag(manifest)
        elif arguments.command == "configure-r2":
            result = configure_r2(arguments.github_env)
            if arguments.github_env is None:
                print(json.dumps(result, sort_keys=True))
        elif arguments.command == "download":
            download_artifacts(arguments.manifest, arguments.output)
        elif arguments.command == "stage":
            stage_artifacts(arguments.manifest, arguments.artifacts, arguments.output, arguments.public_key)
        elif arguments.command == "render-aur":
            render_aur(arguments.manifest, arguments.output)
    except (ManifestError, OSError, subprocess.CalledProcessError, tarfile.TarError) as error:
        print(f"repository publisher: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
