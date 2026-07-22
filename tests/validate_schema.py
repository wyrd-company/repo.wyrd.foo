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
validator_class(schema, format_checker=validator_class.FORMAT_CHECKER).validate(
    json.loads(FIXTURE.read_text(encoding="utf-8"))
)
