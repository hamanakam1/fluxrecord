"""The board: the ledger as something a person can read."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger


class BoardTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"], owner="terminal-1")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_board_has_one_line_per_ticket(self):
        self.ledger.add_task("M-2", "Another", "Acceptance", scope=["repo/y"])
        board = self.ledger.board()
        self.assertEqual(len(board), 2)
        self.assertEqual(board[0]["id"], "M-1")

    def test_a_line_carries_the_facts_about_a_ticket(self):
        line = self.ledger.board()[0]
        self.assertEqual(line["title"], "Objective")
        self.assertEqual(line["status"], "ready")
        self.assertEqual(line["owner"], "terminal-1")
        self.assertEqual(line["tokens"], 0)
        self.assertEqual(line["failures"], 0)
        self.assertEqual(line["last_evidence"], "")
        self.assertGreaterEqual(line["age_seconds"], 0)

    def test_the_owner_can_come_from_the_active_session(self):
        self.ledger.add_task("M-3", "Another", "Acceptance", scope=["repo/y"])
        self.ledger.start("M-3", "terminal-2")
        line = next(row for row in self.ledger.board() if row["id"] == "M-3")
        self.assertEqual(line["owner"], "terminal-2")
        self.assertGreater(line["lease_remaining"], 0)

    def test_the_last_evidence_is_reported(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "a summary", "e", "n", 25)
        line = self.ledger.board()[0]
        self.assertEqual(line["last_evidence"], "a summary")
        self.assertIsNotNone(line["last_evidence_at"])
        self.assertEqual(line["tokens"], 25)

    def test_failures_are_counted(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.fail(started["session_id"], "tool", "broken")
        self.assertEqual(self.ledger.board()[0]["failures"], 1)

    def test_the_board_can_be_filtered(self):
        self.ledger.add_task("M-2", "Another", "Acceptance", scope=["repo/y"])
        self.ledger.start("M-2", "terminal-2")
        board = self.ledger.board(status="in_progress")
        self.assertEqual([row["id"] for row in board], ["M-2"])

    def test_no_key_is_always_null(self):
        """A key that is always null is worse than an absent key.

        A ticket with work done on it has no empty fields at all: the
        evidence, the owner and the lease are all there.
        """
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "a summary", "e", "n", 25)
        line = self.ledger.board()[0]
        for key, value in line.items():
            self.assertIsNotNone(value, key)


if __name__ == "__main__":
    unittest.main()
