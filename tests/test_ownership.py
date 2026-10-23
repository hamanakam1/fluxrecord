"""Ticket ownership stays consistent with the session that holds its lease."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fluxrecord.core import Conflict, Ledger


class OwnershipTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.temp.name) / "ledger.sqlite")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def assert_owner(self, task_id, owner):
        self.assertEqual(self.ledger.task_view(task_id)["owner"], owner)
        self.assertEqual(self.ledger.pack(task_id)["packet"]["owner"], owner)
        self.assertEqual(self.ledger.show(task_id)["active_session"]["owner"], owner)
        self.assertIn(task_id, [task["id"] for task in self.ledger.list_tasks(owner=owner)])
        row = next(task for task in self.ledger.board() if task["id"] == task_id)
        self.assertEqual(row["owner"], owner)

    def test_start_assigns_the_current_worker_to_the_ticket(self):
        for index, assigned in enumerate(("", "previous-worker")):
            with self.subTest(assigned=assigned):
                task_id = f"M-{index + 1}"
                self.ledger.add_task(task_id, "Objective", "Acceptance", owner=assigned)
                self.ledger.start(task_id, "terminal-1")
                self.assert_owner(task_id, "terminal-1")
        self.assertEqual(self.ledger.list_tasks(owner="previous-worker"), [])

    def test_handoff_and_adoption_update_every_ownership_view(self):
        for index, operation in enumerate(("handoff", "adopt")):
            with self.subTest(operation=operation):
                task_id = f"M-{index + 1}"
                self.ledger.add_task(task_id, "Objective", "Acceptance", owner="terminal-1")
                started = self.ledger.start(task_id, "terminal-1")
                getattr(self.ledger, operation)(started["session_id"], "terminal-2", "Handover")
                self.assert_owner(task_id, "terminal-2")
        self.assertEqual(self.ledger.list_tasks(owner="terminal-1"), [])

    def test_a_rejected_claim_preserves_the_ticket_owner(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["shared"])
        self.ledger.add_task(
            "M-2", "Other", "Acceptance", scope=["shared"], owner="assigned-worker"
        )
        self.ledger.start("M-1", "terminal-1")
        with self.assertRaises(Conflict):
            self.ledger.start("M-2", "terminal-2")
        self.assertEqual(self.ledger.task_view("M-2")["owner"], "assigned-worker")
        self.assertEqual(self.ledger.show("M-2")["sessions"], [])

    def test_start_rolls_back_ownership_when_its_event_cannot_be_recorded(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance", owner="assigned-worker")
        before = self.ledger.show("M-1")
        with patch.object(self.ledger, "event", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                self.ledger.start("M-1", "terminal-1")
        self.assertEqual(self.ledger.show("M-1"), before)

    def test_transfers_roll_back_ticket_and_session_ownership_together(self):
        self.ledger.add_task("M-1", "Objective", "Acceptance", owner="terminal-1")
        started = self.ledger.start("M-1", "terminal-1")
        for operation in ("handoff", "adopt"):
            with self.subTest(operation=operation):
                before = self.ledger.show("M-1")
                with patch.object(self.ledger, "event", side_effect=RuntimeError("write failed")):
                    with self.assertRaises(RuntimeError):
                        getattr(self.ledger, operation)(
                            started["session_id"], "terminal-2", "Handover"
                        )
                self.assertEqual(self.ledger.show("M-1"), before)


if __name__ == "__main__":
    unittest.main()
