"""The wire format: the reports the package emits, checked against the schema."""

import json
import tempfile
import unittest
from pathlib import Path

from fluxrecord import wire
from fluxrecord.core import Ledger


class WireTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"], title="A title")
        started = self.ledger.start("M-1", "terminal-1", 900)
        self.ledger.checkpoint(started["session_id"], "a summary", "an evidence", "next", 10)
        self.ledger.add_context("M-1", "some context", "a source")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_the_wire_format_is_versioned(self):
        self.assertEqual(wire.WIRE_FORMAT, 1)
        self.assertEqual(wire.SCHEMA["wire_format"], wire.WIRE_FORMAT)

    def test_a_ticket_matches_the_shape(self):
        self.assertTrue(wire.validate("ticket", self.ledger.task_view("M-1")))

    def test_a_packet_matches_the_shape(self):
        packet = self.ledger.pack("M-1")["packet"]
        self.assertTrue(wire.validate("packet", packet))

    def test_a_session_matches_the_shape(self):
        session = self.ledger.sessions()[0]
        self.assertTrue(wire.validate("session", session))

    def test_a_board_row_matches_the_shape(self):
        row = self.ledger.board()[0]
        self.assertTrue(wire.validate("board_row", row))

    def test_a_missing_field_is_a_problem(self):
        task = self.ledger.task_view("M-1")
        del task["seed_key"]
        self.assertIn("ticket.seed_key is missing", wire.problems("ticket", task))

    def test_a_wrong_type_is_a_problem(self):
        task = self.ledger.task_view("M-1")
        task["priority"] = "one"
        self.assertTrue(wire.problems("ticket", task))

    def test_an_extra_field_is_a_problem_in_a_frozen_shape(self):
        task = self.ledger.task_view("M-1")
        task["something_new"] = 1
        self.assertIn(
            "ticket.something_new is not part of the frozen shape",
            wire.problems("ticket", task),
        )

    def test_the_published_schema_matches_the_code(self):
        published = json.loads(
            (
                Path(__file__).resolve().parent.parent / "docs" / "wire-format.schema.json"
            ).read_text()
        )
        self.assertEqual(published["x-wire-format"], wire.WIRE_FORMAT)
        for shape, schema in wire.SCHEMA.items():
            if shape == "wire_format":
                continue
            self.assertIn(shape, published["$defs"], shape)
            self.assertEqual(
                sorted(published["$defs"][shape]["required"]),
                sorted(schema["required"]),
                shape,
            )

    def test_the_published_schema_is_valid_json(self):
        published = json.loads(
            (
                Path(__file__).resolve().parent.parent / "docs" / "wire-format.schema.json"
            ).read_text()
        )
        self.assertIn("$defs", published)
        self.assertIn("$schema", published)


if __name__ == "__main__":
    unittest.main()
