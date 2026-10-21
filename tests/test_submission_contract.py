# ---
# relationships:
#   verifies: package-repository-publishing
# ---

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/release-process"
ACTION_PATH = ".github/actions/submit-release/action.yml"


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.caller_text = (FIXTURES / "caller.yml").read_text()
        self.caller = yaml.safe_load(self.caller_text)
        self.action_text = (ROOT / ACTION_PATH).read_text()
        self.action = yaml.safe_load(self.action_text)

    def test_in_tree_caller_validates_against_publish_job_schema(self):
        # Preserve external version comments; in-tree Actions need no Git pin.
        contribution = copy.deepcopy(self.caller)
        pins = re.findall(r"uses: (.+)$", self.caller_text, re.MULTILINE)
        for step, pin in zip(
            (s for s in contribution["job"]["steps"] if "uses" in s), pins, strict=True
        ):
            self.assertEqual(pin, "./.github/actions/submit-release")
            step["uses"] = pin
        schema = json.loads((FIXTURES / "schemas/publish-job.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        self.assertEqual(
            list(Draft202012Validator(schema).iter_errors(contribution)), []
        )
        self.assertEqual(contribution["job"]["permissions"], {"contents": "read"})
        for step in contribution["job"]["steps"]:
            self.assertNotIn("${{", step.get("run", ""))

    def test_credentials_validate_and_agree_with_caller(self):
        schema = json.loads((FIXTURES / "schemas/credential.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        declarations = json.loads((FIXTURES / "credentials.json").read_text())[
            "credentials"
        ]
        declared = set()
        for credential in declarations.values():
            Draft202012Validator(schema).validate(credential)
            declared.add(credential["name"])
        used = set(re.findall(r"secrets\.([A-Z_]+)", self.caller_text))
        self.assertEqual(declared, used)
        self.assertEqual(len(used), 2)
        self.assertTrue(all(c["kind"] == "secret" for c in declarations.values()))

    def test_vendored_schemas_match_immutable_source_digests(self):
        provenance = json.loads((FIXTURES / "provenance.json").read_text())
        self.assertEqual(provenance["repository"], "wyrd-company/.github")
        self.assertRegex(provenance["revision"], r"^[0-9a-f]{40}$")
        for filename, source in provenance["schemas"].items():
            self.assertEqual(source["source"], "release/schemas/" + filename)
            self.assertEqual(
                hashlib.sha256(
                    (FIXTURES / "schemas" / filename).read_bytes()
                ).hexdigest(),
                source["sha256"],
            )

    def test_in_tree_caller_executes_checked_out_action_runtime(self):
        selected = self.caller["job"]["steps"][-1]["uses"]
        self.assertEqual(selected, "./.github/actions/submit-release")
        action_dir = ROOT / selected
        action = yaml.safe_load((action_dir / "action.yml").read_text())
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            # Observe the actual Bash invocation without issuing GitHub requests.
            python = work / "python3"
            python.write_text(
                "#!" + sys.executable + "\n"
                "import json, os, pathlib, sys\n"
                "pathlib.Path(os.environ['INVOCATION']).write_text(json.dumps(sys.argv[1:]))\n"
            )
            python.chmod(0o755)
            output = work / "invocation.json"
            subprocess.run(
                ["bash", "-euo", "pipefail", "-c", action["runs"]["steps"][-1]["run"]],
                env={
                    **os.environ,
                    "PATH": str(work) + os.pathsep + os.environ["PATH"],
                    "GITHUB_ACTION_PATH": str(action_dir),
                    "INVOCATION": str(output),
                },
                check=True,
            )
            arguments = json.loads(output.read_text())
        self.assertEqual(len(arguments), 1)
        runtime = Path(arguments[0]).resolve()
        # Compare with the module actually exercised by the behavioral fixtures.
        from test_submission_action import submission

        self.assertEqual(runtime, Path(submission.__file__).resolve())
        self.assertEqual(
            Path(sys.modules["repository"].__file__).resolve(),
            runtime.parent / "repository.py",
        )

    def test_published_example_agrees_with_development_caller(self):
        example = yaml.safe_load(
            (ROOT / "docs/examples/submit-release.yml").read_text()
        )
        example["job"]["steps"][-1]["uses"] = self.caller["job"]["steps"][-1]["uses"]
        self.assertEqual(example, self.caller)

    def test_caller_reads_parent_release_environment_without_interpolation(self):
        release = {
            "sample-tool": {"tag": "1.2.3", "version": "1.2.3", "prerelease": False}
        }
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            output = work / "output"
            step = self.caller["job"]["steps"][0]
            environment = {
                **os.environ,
                **step["env"],
                "RELEASE_JSON": json.dumps(release),
                "GITHUB_OUTPUT": str(output),
            }
            subprocess.run(
                ["bash", "-euo", "pipefail", "-c", step["run"]],
                cwd=work,
                env=environment,
                check=True,
            )
            result = dict(
                line.split("=", 1) for line in output.read_text().splitlines()
            )
        self.assertEqual(result["tag"], release["sample-tool"]["tag"])
        self.assertEqual(result["version"], release["sample-tool"]["version"])
        self.assertEqual(set(result), {"tag", "version"})
        self.assertEqual(
            set(self.caller["job"]["steps"][-1]["with"]),
            set(self.action["inputs"]) - {"release-assets"},
        )

    def test_action_wiring_keeps_authority_and_runtime_closed(self):
        self.assertEqual(
            set(self.action["inputs"]),
            {
                "manifest",
                "tag",
                "version",
                "release-assets",
                "app-client-id",
                "app-private-key",
            },
        )
        steps = self.action["runs"]["steps"]
        self.assertEqual(len(steps), 2)
        token = steps[0]
        self.assertRegex(
            token["uses"], r"^actions/create-github-app-token@[0-9a-f]{40}$"
        )
        self.assertRegex(
            self.action_text,
            r"uses: actions/create-github-app-token@[0-9a-f]{40} # v[0-9]+",
        )
        self.assertEqual(
            token["with"],
            {
                "client-id": "${{ inputs.app-client-id }}",
                "private-key": "${{ inputs.app-private-key }}",
                "owner": "wyrd-company",
                "repositories": "repo.wyrd.foo",
                "permission-contents": "write",
            },
        )
        submit = steps[1]
        self.assertEqual(
            submit["run"],
            'python3 "$GITHUB_ACTION_PATH/../../../scripts/submit-release.py"',
        )
        self.assertEqual(
            submit["env"],
            {
                "MANIFEST": "${{ inputs.manifest }}",
                "RELEASE_TAG": "${{ inputs.tag }}",
                "RELEASE_VERSION": "${{ inputs.version }}",
                "RELEASE_ASSETS": "${{ inputs.release-assets }}",
                "SOURCE_TOKEN": "${{ github.token }}",
                "APP_TOKEN": "${{ steps.token.outputs.token }}",
            },
        )
        self.assertEqual(
            set(self.action["outputs"]),
            {"submission-id", "manifest-path", "inbox-commit", "state"},
        )
        for key, definition in self.action["outputs"].items():
            self.assertEqual(
                definition["value"], "${{ steps.submit.outputs." + key + " }}"
            )
        for step in steps:
            self.assertNotIn("${{", step.get("run", ""))


if __name__ == "__main__":
    unittest.main()
