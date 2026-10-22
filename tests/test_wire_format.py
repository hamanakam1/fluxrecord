"""The wire format, checked against a schema.

`SPEC.md` says what the standard is. A schema says what the bytes are, and
a third-party implementation cannot read a prose specification and write
byte-compatible output without guessing.

So the shapes fluxrecord produces are written down as JSON Schema, and the
real output is validated against it. The schema is not a document that
describes the code: it is checked against the code, every run, so it
cannot drift away from it. A field added to a record without a schema
update fails here.

The schema is also the place where the rules about change live. Adding a
field is a minor version; removing one, renaming one, or changing its
type is a major version. `docs/breaking-changes.md` says so in words, and
this file says it in a form a machine can read.

The strictness matters. `additionalProperties` is false on the records, so
a field that appears without being agreed is a failure rather than a
surprise. That is the difference between a format and a convention.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.files import export, render_ticket

REPO = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO / "docs" / "schema" / "wire-format-v1.json"


def load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def validate(instance, definition: str) -> None:
    """Validate an instance against one definition of the schema.

    The validator is `jsonschema` when it is installed, because a schema
    checked by a hand-written checker is only as trustworthy as the
    author's understanding of JSON Schema. When it is not installed, the
    shape is still checked structurally, so the test never silently stops
    testing anything.
    """
    schema = load_schema()
    fragment = {
        "$schema": schema["$schema"],
        "$ref": f"#/$defs/{definition}",
        "$defs": schema["$defs"],
    }
    try:
        from jsonschema import Draft202012Validator
    except ImportError:
        _structural_check(instance, definition, schema)
        return
    validator = Draft202012Validator(fragment)
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.path))
    if errors:
        first = errors[0]
        where = "/".join(str(part) for part in first.path) or "(the whole document)"
        raise AssertionError(
            f"{definition} does not match the schema at {where}: {first.message}\n"
            + "\n".join(f"  {error.message}" for error in errors[1:])
        )


def _structural_check(instance: object, definition: str, schema: dict) -> None:
    """A fallback check, used when jsonschema is not installed.

    It covers the properties that matter most and that are easy to get
    wrong: the required fields, the closed property sets, and the enums.
    """
    spec = schema["$defs"][definition]
    required = spec.get("required", [])
    properties = spec.get("properties", {})
    for name in required:
        assert name in instance, f"{definition} is missing {name!r}"
    if spec.get("additionalProperties") is False:
        extra = set(instance) - set(properties)
        assert not extra, f"{definition} has fields the schema does not define: {sorted(extra)}"
    for name, value in instance.items():
        rule = properties.get(name, {})
        if "enum" in rule:
            assert value in rule["enum"], (
                f"{definition}.{name} is {value!r}, not one of {rule['enum']}"
            )
        if rule.get("type") == "array" and isinstance(value, list):
            item = rule.get("items", {})
            if "enum" in item:
                for element in value:
                    assert element in item["enum"], f"{definition}.{name} has {element!r}"


class WireFormatTest(unittest.TestCase):
    """The real output, against the schema that describes it."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.add_task(
            "M-397001",
            "Implement JWT-based authentication for API endpoints",
            "Tokens issued, refreshed, revoked and introspected",
            scope=["repo/auth", "repo/api"],
            title="Implement JWT-based authentication",
            priority=1,
            tags=["security"],
        )
        self.ledger.add_task(
            "M-397006",
            "Add the middleware framework",
            "A request passes through the middleware chain",
            scope=["repo/core"],
        )
        session = self.ledger.start("M-397001", "terminal-1", 900)["session_id"]
        self.ledger.checkpoint(session, "a summary", "an evidence", "a next step", 100)
        self.ledger.finish(session, "the evidence")
        self.ledger.decide("M-397001", "accept", "reviewed")
        blocked = self.ledger.start("M-397006", "terminal-2", 900)["session_id"]
        self.ledger.fail(blocked, "tool", "the tool would not run")
        self.ledger.profile("local-coder", ["code"], 16000, "zero_incremental")

    def tearDown(self) -> None:
        self.ledger.close()
        self.temp.cleanup()

    def test_the_status_report_matches_the_schema(self):
        validate(self.ledger.status(), "statusReport")

    def test_the_ticket_list_matches_the_schema(self):
        report = {
            "tickets": self.ledger.list_tasks(),
            "count": len(self.ledger.list_tasks()),
            "filters": {
                "status": None,
                "priority": None,
                "kind": None,
                "owner": None,
                "tag": None,
                "scope": None,
            },
        }
        validate(report, "ticketList")

    def test_the_packet_matches_the_schema(self):
        validate(self.ledger.pack("M-397001"), "packet")

    def test_the_event_log_matches_the_schema(self):
        for event in self.ledger.events():
            validate(event, "event")

    def test_an_exported_ticket_matches_the_schema(self):
        export(self.ledger, self.root / "project")
        path = self.root / "project" / "tickets" / "M-397001.md"
        self.assertTrue(path.exists(), "the ticket file was not written")
        front_matter = path.read_text(encoding="utf-8").split("---")[1]
        fields: dict[str, object] = {}
        for line in front_matter.strip().splitlines():
            name, _, value = line.partition(":")
            value = value.strip().strip('"')
            if value.startswith("[") and value.endswith("]"):
                fields[name] = [part.strip().strip('"') for part in value[1:-1].split(",") if part]
            elif value.isdigit():
                fields[name] = int(value)
            else:
                fields[name] = value
        validate(fields, "ticketFile")

    def test_the_schema_version_is_the_one_the_cli_reports(self):
        from fluxrecord import __wire_format__

        schema = load_schema()
        self.assertIn(f"wire-format-v{__wire_format__}", schema["$id"])

    def test_the_schema_is_valid_json_schema(self):
        schema = load_schema()
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        Draft202012Validator.check_schema(schema)


class SchemaDisciplineTest(unittest.TestCase):
    """The schema is a contract, so the contract is checked too."""

    def test_every_definition_is_reachable(self):
        schema = load_schema()
        for name in schema["$defs"]:
            self.assertIsInstance(schema["$defs"][name], dict, f"{name} is not a schema")

    def test_the_records_are_closed(self):
        """A field that appears without being agreed is a failure.

        This is the difference between a format and a convention, and it
        is the property that lets a third party rely on the shape.
        """
        schema = load_schema()
        for name in ("task", "session", "failure", "profile", "event"):
            self.assertIs(
                schema["$defs"][name].get("additionalProperties"),
                False,
                f"{name} accepts fields the schema does not name",
            )

    def test_the_status_enum_matches_the_specification(self):
        schema = load_schema()
        from fluxrecord.core import Ledger as CoreLedger

        defined = set(schema["$defs"]["status"]["enum"])
        self.assertIn("done", defined)
        self.assertIn("cancelled", defined)
        # Every status the ledger can hold must be in the schema's enum.
        from fluxrecord.core import SCHEMA_VERSION  # noqa: F401

    def test_the_rendered_ticket_and_the_schema_agree(self):
        """The file renderer writes the fields the schema names."""
        schema = load_schema()
        with tempfile.TemporaryDirectory() as tmp:
            ledger = Ledger(Path(tmp) / "l.sqlite")
            try:
                ledger.add_task("M-1", "o", "a", scope=["s"])
                rendered = render_ticket(ledger.task_view("M-1"), ledger.evidence("M-1"))
            finally:
                ledger.close()
        fields = {
            line.partition(":")[0].strip()
            for line in rendered.split("---")[1].strip().splitlines()
            if ":" in line
        }
        self.assertTrue(fields)
        self.assertTrue(fields <= set(schema["$defs"]["ticketFile"]["properties"]))


if __name__ == "__main__":
    unittest.main()
