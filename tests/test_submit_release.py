# ---
# relationships:
#   verifies: package-repository-publishing
# ---

from __future__ import annotations

import base64
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTION_DIRECTORY = ROOT / ".github" / "actions" / "submit-release"
ACTION = (ACTION_DIRECTORY / "action.yml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
MANIFEST = ROOT / "tests" / "fixtures" / "sample-tool-1.2.3.json"


def real_git() -> str:
    """The first git on PATH that is a program, not a wrapper script around one."""
    for directory in os.environ["PATH"].split(os.pathsep):
        candidate = Path(directory) / "git"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            with candidate.open("rb") as handle:
                if handle.read(2) != b"#!":
                    return str(candidate)
    raise RuntimeError("no git program on PATH")


GIT = real_git()
TOKEN = "installation-token-sentinel-0123456789"
INPUT_TOKEN = "${{ inputs.token || steps.token.outputs.token }}"


def step(name: str) -> str:
    """Return the text of the named step of the submit-release action."""
    match = re.search(
        rf"^    - name: {re.escape(name)}\n(?P<body>.*?)(?=^    - name: |\Z)",
        ACTION,
        re.DOTALL | re.MULTILINE,
    )
    assert match is not None, name
    return match["body"]


def input_block(name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>(?:    .*\n)+)", ACTION, re.MULTILINE
    )
    assert match is not None, name
    return match["body"]


class SubmitReleaseTokenWiringTests(unittest.TestCase):
    def test_token_is_an_optional_input_and_key_inputs_are_no_longer_required(self) -> None:
        self.assertIn("Installation token", input_block("token"))
        for name in ("token", "app-client-id", "app-private-key", "app-slug"):
            with self.subTest(name=name):
                self.assertNotIn("required: true", input_block(name))
        self.assertIn("required: true", input_block("manifest"))

    def test_callers_that_pass_the_private_key_still_mint_their_own_token(self) -> None:
        mint = step("Create repository-scoped installation token")
        self.assertIn("id: token", mint)
        self.assertIn("client-id: ${{ inputs.app-client-id }}", mint)
        self.assertIn("private-key: ${{ inputs.app-private-key }}", mint)
        self.assertIn("repositories: repo.wyrd.foo", mint)
        self.assertIn("permission-contents: write", mint)

    def test_with_a_token_nothing_reads_the_private_key_or_mints_a_token(self) -> None:
        mint = step("Create repository-scoped installation token")
        self.assertIn("if: inputs.token == ''", mint)
        self.assertEqual(ACTION.count("inputs.app-private-key"), 1)
        self.assertEqual(ACTION.count("inputs.app-client-id"), 1)
        self.assertEqual(ACTION.count("create-github-app-token"), 1)

    def test_every_use_of_the_token_prefers_the_supplied_one(self) -> None:
        self.assertEqual(ACTION.count("steps.token.outputs.token"), 3)
        self.assertEqual(ACTION.count(INPUT_TOKEN), 3)
        self.assertIn(f"token: {INPUT_TOKEN}", step("Check out package repository"))
        self.assertIn(f"APP_TOKEN: {INPUT_TOKEN}", step("Commit release manifest"))
        self.assertIn(f"GH_TOKEN: {INPUT_TOKEN}", step("Request package publication"))

    def test_a_supplied_token_is_masked_before_use(self) -> None:
        mask = step("Mask supplied installation token")
        self.assertIn("if: inputs.token != ''", mask)
        self.assertIn("TOKEN: ${{ inputs.token }}", mask)
        self.assertIn("::add-mask::", mask)
        self.assertLess(
            ACTION.index("Mask supplied installation token"),
            ACTION.index("Check out package repository"),
        )

    def test_commit_identity_comes_from_the_minted_app_or_the_slug_input(self) -> None:
        self.assertIn(
            "APP_SLUG: ${{ steps.token.outputs.app-slug || inputs.app-slug }}",
            step("Commit release manifest"),
        )
        self.assertIn("default: repo-wyrd-foo-publisher", input_block("app-slug"))

    def test_readme_states_the_repository_and_permissions_the_token_needs(self) -> None:
        section = README.split("## Submit a release", 1)[1].split("\n## ", 1)[0]
        self.assertIn("`token`", section)
        self.assertIn("wyrd-company/repo.wyrd.foo", section)
        self.assertIn("Contents", section)
        self.assertIn("write", section)
        self.assertIn("Metadata", section)
        self.assertIn("app-private-key", section)


class SubmitReleaseWithTokenTests(unittest.TestCase):
    """Run the action's scripts, in its step order, with a supplied token."""

    def run_step(self, script: str, env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [str(ROOT / "scripts" / script)],
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(TOKEN, result.stdout + result.stderr)
        return result

    def test_submission_with_a_token_commits_to_the_inbox_and_requests_publication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            origin = root / "origin.git"
            workspace = root / "workspace"
            checkout = workspace / ".repo-wyrd-foo"
            fake_bin = root / "fake-bin"
            output = root / "github-output"
            push_header = root / "push-header"
            gh_log = root / "gh-log"
            workspace.mkdir()
            fake_bin.mkdir()
            output.touch()

            def git(*arguments: str, cwd: Path = root) -> str:
                return subprocess.run(
                    [GIT, *arguments],
                    cwd=cwd,
                    stdout=subprocess.PIPE,
                    text=True,
                    check=True,
                    env=os.environ | {"GIT_CONFIG_GLOBAL": "/dev/null"},
                ).stdout.strip()

            git("init", "--quiet", "--bare", "--initial-branch=release-manifests", str(origin))
            seed = root / "seed"
            git("clone", "--quiet", str(origin), str(seed))
            git("-c", "user.name=seed", "-c", "user.email=seed@example.invalid",
                "commit", "--quiet", "--allow-empty", "-m", "seed", cwd=seed)
            git("push", "--quiet", "origin", "HEAD:release-manifests", cwd=seed)
            git("clone", "--quiet", "--branch", "release-manifests", str(origin), str(checkout))

            # The git wrapper records the credential header a push carries and
            # then runs the real git; the gh stand-in records what it is given.
            (fake_bin / "git").write_text(
                '#!/usr/bin/env bash\n'
                'if [[ " $* " == *" push "* ]]; then\n'
                '  printf \'%s\' "$GIT_CONFIG_VALUE_0" > "$PUSH_HEADER"\n'
                'fi\n'
                f'exec {GIT} "$@"\n',
                encoding="utf-8",
            )
            (fake_bin / "gh").write_text(
                '#!/usr/bin/env bash\n'
                '{ printf \'token=%s\\n\' "$GH_TOKEN"; printf \'args=%s\\n\' "$*"; cat; } > "$GH_LOG"\n',
                encoding="utf-8",
            )
            for name in ("git", "gh"):
                path = fake_bin / name
                path.chmod(path.stat().st_mode | stat.S_IXUSR)

            environment = os.environ | {
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GITHUB_ACTION_PATH": str(ACTION_DIRECTORY),
                "GITHUB_OUTPUT": str(output),
                "GITHUB_WORKSPACE": str(workspace),
                "PUSH_HEADER": str(push_header),
                "GH_LOG": str(gh_log),
            }
            environment.pop("GH_TOKEN", None)

            self.run_step("submit-action.sh", environment | {"MANIFEST": str(MANIFEST)}, workspace)
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(outputs["state"], "created")
            path = outputs["path"]
            self.assertEqual(path, "releases/sample-tool/1.2.3.json")

            self.run_step(
                "commit-release.sh",
                environment | {"APP_SLUG": "repo-wyrd-foo-publisher", "APP_TOKEN": TOKEN, "RESULT": path},
                checkout,
            )
            header = push_header.read_text(encoding="utf-8")
            self.assertEqual(header.split("AUTHORIZATION: basic ", 1)[1].strip(), base64.b64encode(
                f"x-access-token:{TOKEN}".encode()).decode())
            self.assertEqual(
                git("log", "-1", "--format=%an <%ae>|%s", "release-manifests", cwd=origin),
                "repo-wyrd-foo-publisher[bot] <repo-wyrd-foo-publisher[bot]@users.noreply.github.com>"
                f"|publish: add {path.removeprefix('releases/')}",
            )
            self.assertEqual(git("ls-tree", "-r", "--name-only", "release-manifests", cwd=origin).splitlines(),
                             [path])

            self.run_step("resolve-inbox.sh", environment | {"RESULT": path}, checkout)
            outputs = dict(line.split("=", 1) for line in output.read_text().splitlines())
            self.assertEqual(outputs["commit"], git("rev-parse", "release-manifests", cwd=origin))

            self.run_step(
                "dispatch-release.sh",
                environment | {"GH_TOKEN": TOKEN, "INBOX_COMMIT": outputs["commit"], "MANIFEST_PATH": path},
                workspace,
            )
            token_line, args_line, payload = gh_log.read_text().split("\n", 2)
            self.assertEqual(token_line, f"token={TOKEN}")
            self.assertEqual(
                args_line, "args=api --method POST repos/wyrd-company/repo.wyrd.foo/dispatches --input -"
            )
            self.assertEqual(
                json.loads(payload),
                {"event_type": "release-manifest", "client_payload": {"commit": outputs["commit"], "path": path}},
            )


if __name__ == "__main__":
    unittest.main()
