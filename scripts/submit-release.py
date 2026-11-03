#!/usr/bin/env python3
# ---
# relationships:
#   implements: package-repository-publishing
#   references: use-release-manifest-handoffs
# ---

"""Submit a release-record-bound manifest without publisher credentials."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from repository import COMMIT, ManifestError, canonical_bytes, load_manifest

DESTINATION = "wyrd-company/repo.wyrd.foo"
INBOX = "release-manifests"


class APIError(Exception):
    def __init__(self, status: int):
        self.status = status
        super().__init__(f"GitHub API request failed (HTTP {status})")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an installation token, including to a GitHub redirect.
        return None


class GitHub:
    def __init__(self, token: str):
        if not token:
            raise ManifestError("GitHub authority is missing")
        self.token = token
        self.opener = urllib.request.build_opener(NoRedirect())

    def request(self, method: str, path: str, body=None):
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=None if body is None else json.dumps(body).encode(),
            method=method,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with self.opener.open(request) as response:
                data = response.read()
                return json.loads(data) if data else None
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
            raise APIError(status) from None
        except (OSError, ValueError):
            raise ManifestError(
                "Invalid response or transport failure from api.github.com"
            ) from None


def asset_map(assets):
    if not isinstance(assets, list):
        raise ManifestError("Release assets must be an array")
    result = {}
    for asset in assets:
        if not isinstance(asset, dict):
            raise ManifestError("Invalid release asset")
        name, digest = asset.get("name"), asset.get("digest")
        if not isinstance(name, str) or name in result:
            raise ManifestError("Invalid or duplicate release asset name")
        result[name] = digest
    return result


def verify_release(manifest, reader, environment):
    source = manifest["source"]
    if source["repository"] != environment["GITHUB_REPOSITORY"]:
        raise ManifestError("Manifest source is outside the caller repository")
    revision = environment.get("RELEASE_SOURCE_SHA") or environment["GITHUB_SHA"]
    if source["commit"] != revision:
        raise ManifestError("Manifest source does not match the pinned source revision")
    for key, field in (("RELEASE_TAG", "tag"), ("RELEASE_VERSION", "version")):
        if environment.get(key) and environment[key] != manifest[field]:
            raise ManifestError("Release input does not match the manifest")
    prefix = f"/repos/{source['repository']}"
    tag = urllib.parse.quote(manifest["tag"], safe="")
    target = reader.request("GET", f"{prefix}/git/ref/tags/{tag}")["object"]
    visited = set()
    while target["type"] == "tag":
        tag_sha = require_sha(target["sha"])
        if tag_sha in visited:
            raise ManifestError("Cyclic annotated tag response")
        visited.add(tag_sha)
        target = reader.request("GET", f"{prefix}/git/tags/{tag_sha}")["object"]
    if target["type"] != "commit" or target["sha"] != source["commit"]:
        raise ManifestError(
            "Release tag does not resolve to the pinned source revision"
        )
    release = reader.request("GET", f"{prefix}/releases/tags/{tag}")
    if release["tag_name"] != manifest["tag"] or release["draft"] is not False:
        raise ManifestError("A published release record is required")
    release_id = release["id"]
    if type(release_id) is not int or release_id <= 0:
        raise ManifestError("Invalid release record identity")
    assets = []
    page = 1
    while True:
        batch = reader.request(
            "GET", f"{prefix}/releases/{release_id}/assets?per_page=100&page={page}"
        )
        if not isinstance(batch, list):
            raise ManifestError("Invalid release assets response")
        assets.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    actual = asset_map(assets)
    supplied = environment.get("RELEASE_ASSETS")
    declared = asset_map(json.loads(supplied)) if supplied else actual
    for artifact in manifest["artifacts"]:
        name, digest = artifact["filename"], "sha256:" + artifact["sha256"]
        if actual.get(name) != digest or declared.get(name) != digest:
            raise ManifestError(
                "Manifest asset digest does not match the release record"
            )


def require_sha(value):
    if not isinstance(value, str) or not COMMIT.fullmatch(value):
        raise ManifestError("Invalid GitHub commit identity")
    return value


def submit(manifest, writer):
    authority = writer.request("GET", "/installation/repositories?per_page=100")
    if authority["total_count"] != 1 or [
        repo["full_name"] for repo in authority["repositories"]
    ] != [DESTINATION]:
        raise ManifestError("Installation token must cover only the package repository")
    prefix = f"/repos/{DESTINATION}"
    path = f"releases/{manifest['product']}/{manifest['version']}.json"
    content = canonical_bytes(manifest)
    encoded = base64.b64encode(content).decode()
    for _ in range(4):
        head = require_sha(
            writer.request("GET", f"{prefix}/git/ref/heads/{INBOX}")["object"]["sha"]
        )
        try:
            existing = writer.request("GET", f"{prefix}/contents/{path}?ref={head}")
        except APIError as error:
            if error.status != 404:
                raise
            existing = None
        if existing is not None:
            if (
                existing["type"] != "file"
                or existing["encoding"] != "base64"
                or base64.b64decode(existing["content"]) != content
            ):
                raise ManifestError(
                    "An immutable release manifest already exists with different content"
                )
            history = writer.request(
                "GET", f"{prefix}/commits?sha={head}&path={path}&per_page=1"
            )
            commit = require_sha(history[0]["sha"])
            state = "unchanged"
            break
        try:
            created = writer.request(
                "PUT",
                f"{prefix}/contents/{path}",
                {
                    "message": f"publish: add {manifest['product']}/{manifest['version']}.json",
                    "content": encoded,
                    "branch": INBOX,
                },
            )
        except APIError as error:
            if error.status not in (409, 422):
                raise
            continue
        commit = require_sha(created["commit"]["sha"])
        state = "created"
        break
    else:
        raise ManifestError("Unable to submit after concurrent inbox updates")
    # Replay dispatches the same handoff so a publisher failure is recoverable.
    writer.request(
        "POST",
        f"{prefix}/dispatches",
        {
            "event_type": "release-manifest",
            "client_payload": {"commit": commit, "path": path},
        },
    )
    return {
        "submission-id": path + "@sha256:" + hashlib.sha256(content).hexdigest(),
        "manifest-path": path,
        "inbox-commit": commit,
        "state": state,
    }


def main():
    try:
        manifest = load_manifest(Path(os.environ["MANIFEST"]))
        verify_release(manifest, GitHub(os.environ["SOURCE_TOKEN"]), os.environ)
        result = submit(manifest, GitHub(os.environ["APP_TOKEN"]))
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            for key, value in result.items():
                output.write(f"{key}={value}\n")
    except (
        ManifestError,
        APIError,
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        OSError,
    ) as error:
        # Input and provider text can contain credentials; emit fixed diagnostics.
        message = (
            str(error)
            if isinstance(error, APIError)
            else "Release submission refused: invalid input, authority, or release binding"
        )
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
