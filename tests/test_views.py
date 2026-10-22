"""Focused views: who is holding what, and what happened."""

import tempfile
import time
import unittest
from pathlib import Path

from fluxrecord.core import Ledger


class SessionViewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_session_reports_the_lease_it_has_left(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=900)
        sessions = self.ledger.sessions()
        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["owner"], "terminal-1")
        self.assertGreater(sessions[0]["lease_remaining"], 0)
        self.assertFalse(sessions[0]["expired"])

    def test_an_expired_lease_is_reported_as_expired(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=900)
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=? WHERE id=?",
            (time.time() - 10, started["session_id"]),
        )
        sessions = self.ledger.sessions()
        self.assertTrue(sessions[0]["expired"])
        self.assertLess(sessions[0]["lease_remaining"], 0)

    def test_only_active_sessions_can_be_asked_for(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        self.ledger.finish(started["session_id"], "evidence")
        self.assertEqual(len(self.ledger.sessions()), 1)
        self.assertEqual(len(self.ledger.sessions(active_only=True)), 0)


class EventViewTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        self.ledger.add_task("M-2", "Objective", "Acceptance", scope=["repo/y"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_events_can_be_filtered_by_type(self):
        found = self.ledger.events(kind="task.created")
        self.assertEqual(len(found), 2)
        self.assertTrue(all(event["type"] == "task.created" for event in found))

    def test_events_can_be_filtered_by_task(self):
        found = self.ledger.events(task_id="M-1")
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["task_id"], "M-1")

    def test_events_can_be_limited(self):
        self.assertEqual(len(self.ledger.events(limit=1)), 1)

    def test_the_cursor_still_works(self):
        everything = self.ledger.events()
        after = everything[0]["seq"]
        self.assertEqual(len(self.ledger.events(after=after)), len(everything) - 1)


if __name__ == "__main__":
    unittest.main()
