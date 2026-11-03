#!/usr/bin/env python3
# ---
# relationships:
#   verifies: package-repository-publishing
# ---

"""Release/UAT gate for the consumer example and rendered publication part."""

import os
import re
import subprocess
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
REMOTE = "git@github.com:wyrd-company/repo.wyrd.foo.git"
ACTION = "wyrd-company/repo.wyrd.foo/.github/actions/submit-release"
RUNTIME = (
    ".github/actions/submit-release/action.yml",
    "scripts/submit-release.py",
    "scripts/repository.py",
)
VERSION = r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
PIN = re.compile(
    r"^[ \t]*(?:- )?uses: "
    + re.escape(ACTION)
    + r"@([0-9a-f]{40}) # ("
    + VERSION
    + r")[ \t]*$",
    re.MULTILINE,
)


class QualificationError(Exception):
    pass


def published_reference(path):
    text = path.read_text()
    document = yaml.safe_load(text)
    references = []

    def visit(value):
        if isinstance(value, dict):
            if "uses" in value and "submit-release" in str(value["uses"]):
                references.append(value["uses"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    pins = PIN.findall(text)
    if len(pins) != 1 or references != [ACTION + "@" + pins[0][0]]:
        raise QualificationError(
            "Exactly one published SHA and bare release tag comment are required"
        )
    return pins[0]


def git(repository, *args):
    # This gate runs only at release/UAT. Never use the caller's Git object store.
    environment = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if result.returncode:
        raise QualificationError(
            "Required remote main, tag, commit, or runtime is unavailable"
        )
    return result.stdout


def qualify(root, consumer, remote=REMOTE):
    revision, version = published_reference(root / "docs/examples/submit-release.yml")
    if published_reference(consumer) != (revision, version):
        raise QualificationError(
            "Publication part and consumer example references differ"
        )
    with tempfile.TemporaryDirectory(prefix="submission-qualification-") as directory:
        repository = Path(directory)
        git(repository, "init", "--quiet", "--bare")
        git(
            repository,
            "fetch",
            "--quiet",
            "--no-tags",
            remote,
            "refs/heads/main:refs/heads/main",
            f"refs/tags/{version}:refs/tags/{version}",
        )
        tagged = (
            git(repository, "rev-parse", f"refs/tags/{version}^{{commit}}")
            .decode()
            .strip()
        )
        if tagged != revision:
            raise QualificationError("Release tag does not identify the published pin")
        git(repository, "merge-base", "--is-ancestor", revision, "refs/heads/main")
        for relative in RUNTIME:
            published = git(repository, "show", revision + ":" + relative)
            if published != (root / relative).read_bytes():
                raise QualificationError("Published runtime differs: " + relative)
    return revision, version


def main(root=ROOT, remote=REMOTE):
    try:
        consumer = Path(os.environ["PUBLICATION_CALLER"])
        revision, version = qualify(root, consumer, remote)
    except (KeyError, OSError, ValueError, yaml.YAMLError, QualificationError) as error:
        print("QUALIFICATION_RESULT=FAIL " + str(error))
        return 1
    print(f"QUALIFICATION_RESULT=PASS revision={revision} tag={version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
