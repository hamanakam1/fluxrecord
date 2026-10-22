"""Dependencies: the specification puts "dependencies met" on the way in."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Conflict, Ledger


class DependencyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "First", "Done", scope=["repo/a"])
        self.ledger.add_task("M-2", "Second", "Done", scope=["repo/b"], depends_on=["M-1"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def accept(self, task_id):
        started = self.ledger.start(task_id, "terminal-1")
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        self.ledger.finish(started["session_id"], "evidence")
        self.ledger.decide(task_id, "accept", "reviewed")

    def test_work_refuses_a_dependency_that_is_not_accepted(self):
        with self.assertRaises(Conflict) as caught:
            self.ledger.start("M-2", "terminal-1")
        self.assertIn("M-1", str(caught.exception))

    def test_work_refuses_a_dependency_that_does_not_exist(self):
        self.ledger.add_task("M-3", "Third", "Done", scope=["repo/c"], depends_on=["M-999"])
        with self.assertRaises(Conflict) as caught:
            self.ledger.start("M-3", "terminal-1")
        self.assertIn("not a known ticket", str(caught.exception))

    def test_work_starts_once_the_dependency_is_accepted(self):
        self.accept("M-1")
        started = self.ledger.start("M-2", "terminal-1")
        self.assertEqual(started["task_id"], "M-2")

    def test_a_ticket_without_dependencies_is_unaffected(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.assertEqual(started["task_id"], "M-1")

    def test_the_dependencies_are_reported_on_the_ticket(self):
        task = self.ledger.task_view("M-2")
        self.assertEqual(task["depends_on"], ["M-1"])


if __name__ == "__main__":
    unittest.main()
