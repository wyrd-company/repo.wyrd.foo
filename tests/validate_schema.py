# ---
# relationships:
#   verifies: package-repository-publishing
# ---

"""Validate the release manifest schema and its checked-in fixture."""

from __future__ import annotations

import json
from pathlib import Path

from jsonschema.validators import validator_for


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "schemas" / "release-manifest.schema.json"
FIXTURE = ROOT / "tests" / "fixtures" / "sample-tool-1.2.3.json"


schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
validator_class = validator_for(schema)
validator_class.check_schema(schema)
validator = validator_class(schema, format_checker=validator_class.FORMAT_CHECKER)
fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
validator.validate(fixture)

safe = json.loads(json.dumps(fixture))
safe["package"]["maintainer"] = "Package Maintainers, Inc. <packages+aur@example.invalid>"
validator.validate(safe)

for line_break in ("\r", "\n", "\r\n"):
    injected = json.loads(json.dumps(fixture))
    injected["package"]["maintainer"] = (
        f"Package Maintainers{line_break}# injected <packages@example.invalid>"
    )
    if not list(validator.iter_errors(injected)):
        raise AssertionError("maintainer line-break injection passed JSON Schema validation")

installation = {
    "files": {"packaging/sample.service": "/usr/lib/systemd/system/sample.service"},
    "hooks": {
        name: f"packaging/{name}.sh"
        for name in ("pre-install", "post-install", "pre-remove", "post-remove")
    },
}
with_installation = json.loads(json.dumps(fixture))
with_installation["publish"]["aur"]["installation"] = installation
validator.validate(with_installation)
for source, destination in (
    ("../sample.service", "/usr/lib/systemd/system/sample.service"),
    ("packaging/sample.service", "/etc/sudoers"),
    ("packaging/sample.service", "/usr/lib/systemd/system/../sample.service"),
):
    invalid = json.loads(json.dumps(with_installation))
    invalid["publish"]["aur"]["installation"]["files"] = {source: destination}
    if not list(validator.iter_errors(invalid)):
        raise AssertionError("unsafe installation path passed JSON Schema validation")
