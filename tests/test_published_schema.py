"""The published schema, checked against the real output.

`docs/wire-format.schema.json` is the artefact a third-party implementation
reads. It is the one document in this project that exists for somebody
else, so it has to be true.

Two things could make it untrue, and both are checked here.

The first is drift: the schema says one thing and the code does another.
The code's own `wire.SCHEMA` is checked against the published file, field
by field, so a type that changes in one and not the other is a failure.

The second is that the published file was never used to check anything. It
was published, and its required lists were compared with the code's, and
then it sat there. So the real output of the real CLI is validated against
the published file with a real JSON Schema validator. If the file were
wrong about a type, or missing a property the code emits, this fails.

The validator is `jsonschema`, which is a development dependency and not a
runtime one: the package stays stdlib-only, and the standard's own
artefact is checked by the standard's own tooling rather than by a
hand-written imitation of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fluxrecord import wire

REPO = Path(__file__).resolve().parent.parent
PUBLISHED = REPO / "docs" / "wire-format.schema.json"

#: The types the code's schema uses, and the JSON Schema type each one is.
_JSON_TYPES = {
    "string": "string",
    "integer": "integer",
    "number": "number",
    "string[]": "array",
    "object[]": "array",
}


def published() -> dict:
    return json.loads(PUBLISHED.read_text(encoding="utf-8"))


def parse_front_matter(text: str) -> dict:
    """The front matter of a ticket file, as values.

    The file is markdown with YAML-ish front matter, and the front matter
    is the part the standard freezes. It is parsed here rather than
    imported because nothing in the package reads an exported ticket back:
    the ledger is the source of truth, and the file is a projection of it.
    """
    if not text.startswith("---"):
        raise ValueError("the file has no front matter")
    block = text.split("---", 2)[1]
    fields: dict = {}
    for line in block.strip().splitlines():
        name, _, raw = line.partition(":")
        name = name.strip()
        raw = raw.strip()
        if raw.startswith("[") and raw.endswith("]"):
            fields[name] = [
                part.strip().strip('"') for part in raw[1:-1].split(",") if part.strip()
            ]
        elif raw.startswith('"') and raw.endswith('"'):
            fields[name] = raw[1:-1]
        elif raw.lstrip("-").isdigit():
            fields[name] = int(raw)
        else:
            fields[name] = raw
    return fields


def validator_for(shape: str):
    """A validator for one shape of the published schema."""
    from jsonschema import Draft202012Validator

    schema = published()
    fragment = {
        "$schema": schema["$schema"],
        "$ref": f"#/$defs/{shape}",
        "$defs": schema["$defs"],
    }
    return Draft202012Validator(fragment)


class PublishedSchemaTest(unittest.TestCase):
    """The published file, against the code and against the real output."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def flux(self, *args, expect=0):
        environment = dict(os.environ)
        environment["FLUX_DB"] = str(self.db)
        result = subprocess.run(
            [sys.executable, "-m", "fluxrecord", *args],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.returncode, expect, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def build(self) -> None:
        """A ledger holding a ticket in every state a ticket can be in."""
        self.flux(
            "task-add",
            "M-000001",
            "--objective",
            "the first",
            "--acceptance",
            "it is done",
            "--scope",
            "repo/one",
            "--title",
            "First",
        )
        self.flux(
            "task-add",
            "M-000002",
            "--objective",
            "the second",
            "--acceptance",
            "it is reviewed",
            "--scope",
            "repo/two",
        )
        started = self.flux("start", "M-000002", "--owner", "terminal-1")
        self.flux(
            "checkpoint",
            started["session_id"],
            "--summary",
            "a summary",
            "--evidence",
            "an evidence",
            "--next",
            "a next step",
            "--tokens",
            "100",
        )
        self.flux("finish", started["session_id"], "--evidence", "the evidence")
        self.flux("decide", "M-000001", "cancel", "--reason", "not wanted")

    # ------------------------------------------------------------- agreement

    def test_the_published_file_is_a_valid_json_schema(self):
        from jsonschema import Draft202012Validator

        Draft202012Validator.check_schema(published())

    def test_the_published_file_is_what_the_script_would_write(self):
        """Two documents describing one thing drift.

        The published file is generated from the code's shapes, and this
        checks that it is still the file the generator would write. If the
        shapes change and the file is not regenerated, this fails rather
        than the file quietly describing the previous release.
        """
        result = subprocess.run(
            [sys.executable, "scripts/publish_schema.py", "--check"],
            capture_output=True,
            text=True,
            cwd=REPO,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_the_published_file_describes_every_shape_the_code_knows(self):
        file = published()
        for shape in wire.SCHEMA:
            if shape == "wire_format":
                continue
            self.assertIn(shape, file["$defs"], f"{shape} is missing from the published schema")

    def test_the_published_types_agree_with_the_code(self):
        """A type that changes in one and not the other is a failure.

        The published file is what somebody else reads, so a type that
        drifts apart is worse than a type that was never written down.
        """
        file = published()
        for shape, schema in wire.SCHEMA.items():
            if shape == "wire_format":
                continue
            properties = file["$defs"][shape].get("properties", {})
            for field, kind in schema["types"].items():
                self.assertIn(field, properties, f"{shape}.{field} is missing from the schema")
                expected = _JSON_TYPES.get(kind)
                if expected is None:
                    continue
                actual = properties[field].get("type")
                self.assertEqual(
                    actual,
                    expected,
                    f"{shape}.{field} is {kind} in the code and {actual} in the schema",
                )

    def test_the_published_file_closes_the_shapes_the_code_freezes(self):
        file = published()
        for shape, schema in wire.SCHEMA.items():
            if shape == "wire_format" or not schema.get("frozen"):
                continue
            self.assertIs(
                file["$defs"][shape].get("additionalProperties"),
                False,
                f"{shape} is frozen in the code but open in the published schema",
            )

    # ------------------------------------------------------------ the real thing

    def assert_matches(self, shape: str, instance: object) -> None:
        errors = sorted(
            validator_for(shape).iter_errors(instance),
            key=lambda error: list(error.path),
        )
        if errors:
            where = "/".join(str(part) for part in errors[0].path) or "(the whole document)"
            raise AssertionError(
                f"the published schema does not match the real {shape} at {where}: "
                f"{errors[0].message}"
            )

    def test_a_real_ticket_matches_the_published_schema(self):
        self.build()
        for task_id in ("M-000001", "M-000002"):
            self.assert_matches("ticket", self.flux("show", task_id)["ticket"])

    def test_a_real_packet_matches_the_published_schema(self):
        self.build()
        self.assert_matches("packet", self.flux("pack", "M-000002")["packet"])

    def test_a_real_session_matches_the_published_schema(self):
        self.build()
        for session in self.flux("sessions")["sessions"]:
            self.assert_matches("session", session)

    def test_a_real_board_row_matches_the_published_schema(self):
        self.build()
        for row in self.flux("status", "--summary")["board"]:
            self.assert_matches("board_row", row)

    def test_the_schema_catches_a_shape_the_code_stopped_producing(self):
        """A published schema that accepts everything is not a schema.

        If the published file were loosened to allow any extra field, the
        checks above would still pass — so the looseness itself is checked
        here, by feeding the validator something the code must not emit.
        """
        self.build()
        task = dict(self.flux("show", "M-000001")["ticket"])
        task["a_field_the_standard_does_not_define"] = "surprise"
        with self.assertRaises(AssertionError):
            self.assert_matches("ticket", task)

    def test_a_real_exported_ticket_matches_the_published_schema(self):
        """The file is what a human reviews and what a third party reads.

        Its front matter is a frozen shape of its own: the times are
        strings in the file, where the ledger keeps them as numbers.
        """
        self.build()
        self.flux("export", "--project", str(self.root / "project"))
        path = self.root / "project" / "tickets" / "M-000001.md"
        self.assertTrue(path.exists(), "the ticket file was not written")
        fields = parse_front_matter(path.read_text(encoding="utf-8"))
        self.assert_matches("ticket_file", fields)

    def test_the_exported_file_has_no_fields_the_standard_does_not_name(self):
        self.build()
        self.flux("export", "--project", str(self.root / "project"))
        path = self.root / "project" / "tickets" / "M-000001.md"
        fields = parse_front_matter(path.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(fields),
            sorted(wire.SCHEMA["ticket_file"]["required"]),
            "the exported file and the published shape name different fields",
        )


if __name__ == "__main__":
    unittest.main()
