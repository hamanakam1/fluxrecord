"""Conformance: the reports this package emits, against the published schema.

This is the standard's teeth, in-repo for now. A separate
`fluxrecord-conformance` package can run the same checks against another
implementation; the shapes it checks are the ones in
`docs/wire-format.schema.json`.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fluxrecord import wire
from tests.cli_support import isolated_cli_environment


class ConformanceTest(unittest.TestCase):
    """Every report the CLI emits, in every state a ticket can be in."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def flux(self, *args, expect=0):
        result = subprocess.run(
            [sys.executable, "-m", "fluxrecord", *args],
            capture_output=True,
            text=True,
            env=isolated_cli_environment(self.root),
            cwd=self.root,
        )
        self.assertEqual(result.returncode, expect, result.stderr)
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def build(self):
        """A ledger holding one ticket in every state."""
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

    def test_the_ticket_shape_holds_for_every_state(self):
        self.build()
        for task_id in ("M-000001", "M-000002"):
            view = self.flux("show", task_id)
            self.assertTrue(wire.validate("ticket", view["ticket"]), task_id)

    def test_the_packet_shape_holds(self):
        self.build()
        packed = self.flux("pack", "M-000001")
        self.assertTrue(wire.validate("packet", packed["packet"]))

    def test_the_session_shape_holds(self):
        self.build()
        sessions = self.flux("sessions")
        self.assertTrue(sessions["sessions"])
        for session in sessions["sessions"]:
            self.assertTrue(wire.validate("session", session))

    def test_the_board_row_shape_holds(self):
        self.build()
        board = self.flux("status", "--summary")
        self.assertTrue(board["board"])
        for row in board["board"]:
            self.assertTrue(wire.validate("board_row", row))

    def test_the_list_shape_holds(self):
        self.build()
        listing = self.flux("list")
        for task in listing["tickets"]:
            self.assertTrue(wire.validate("ticket", task))

    def test_the_compact_list_shape_holds(self):
        self.build()
        listing = self.flux("list", "--compact")
        for row in listing["tickets"]:
            self.assertIn("id", row)
            self.assertIn("status", row)

    def test_the_cli_says_which_wire_format_it_speaks(self):
        version = self.flux("version")
        self.assertEqual(version["wire_format"], wire.WIRE_FORMAT)


if __name__ == "__main__":
    unittest.main()
