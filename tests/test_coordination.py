"""Coordination: handing a lease over, taking one over, and watching."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.watch import format_event, watch, watch_json


class HandoffTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        self.started = self.ledger.start("M-1", "terminal-1", 900)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_lease_can_be_handed_over(self):
        result = self.ledger.handoff(self.started["session_id"], "terminal-2", "going home")
        self.assertEqual(result["owner"], "terminal-2")
        self.assertEqual(result["previous_owner"], "terminal-1")
        sessions = self.ledger.sessions()
        self.assertEqual(sessions[0]["owner"], "terminal-2")

    def test_a_handoff_is_recorded(self):
        self.ledger.handoff(self.started["session_id"], "terminal-2", "going home")
        kinds = [event["type"] for event in self.ledger.events()]
        self.assertIn("session.handed_off", kinds)
        event = next(e for e in self.ledger.events() if e["type"] == "session.handed_off")
        self.assertEqual(event["data"]["previous_owner"], "terminal-1")
        self.assertEqual(event["data"]["reason"], "going home")

    def test_a_handoff_needs_a_reason(self):
        with self.assertRaises(ValueError):
            self.ledger.handoff(self.started["session_id"], "terminal-2", "  ")

    def test_a_lease_can_be_adopted(self):
        result = self.ledger.adopt(
            self.started["session_id"], "terminal-3", "the old worker is gone"
        )
        self.assertEqual(result["owner"], "terminal-3")
        self.assertEqual(result["previous_owner"], "terminal-1")

    def test_an_adoption_extends_the_lease(self):
        before = self.started["lease_until"]
        result = self.ledger.adopt(self.started["session_id"], "terminal-3", "gone")
        self.assertGreater(result["lease_until"], before)

    def test_an_adoption_is_recorded(self):
        self.ledger.adopt(self.started["session_id"], "terminal-3", "the old worker is gone")
        kinds = [event["type"] for event in self.ledger.events()]
        self.assertIn("session.adopted", kinds)

    def test_an_adoption_needs_a_reason(self):
        with self.assertRaises(ValueError):
            self.ledger.adopt(self.started["session_id"], "terminal-3", "")

    def test_an_expired_session_cannot_be_handed_over(self):
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=0 WHERE id=?", (self.started["session_id"],)
        )
        with self.assertRaises(Exception):
            self.ledger.handoff(self.started["session_id"], "terminal-2", "too late")


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_watch_prints_what_is_already_there(self):
        lines: list[str] = []
        cursor = watch(self.ledger, once=True, emit=lines.append)
        self.assertEqual(len(lines), 1)
        self.assertIn("task.created", lines[0])
        self.assertEqual(cursor, 1)

    def test_watch_starts_from_a_cursor(self):
        lines: list[str] = []
        watch(self.ledger, after=1, once=True, emit=lines.append)
        self.assertEqual(lines, [])

    def test_watch_sees_what_lands_later(self):
        lines: list[str] = []
        cursor = watch(self.ledger, once=True, emit=lines.append)
        self.ledger.add_task("M-2", "Another", "Acceptance", scope=["repo/y"])
        watch(self.ledger, after=cursor, once=True, emit=lines.append)
        self.assertEqual(len(lines), 2)
        self.assertIn("task.created", lines[1])

    def test_watch_can_print_json(self):
        lines: list[str] = []
        watch_json(self.ledger, once=True, emit=lines.append)
        self.assertIn("task.created", lines[0])

    def test_an_event_is_formatted_for_a_person(self):
        event = self.ledger.events()[0]
        line = format_event(event)
        self.assertIn("task.created", line)
        self.assertIn("M-1", line)

    def test_watch_stops_at_a_limit(self):
        lines: list[str] = []
        self.ledger.add_task("M-2", "Another", "Acceptance", scope=["repo/y"])
        cursor = watch(self.ledger, once=True, limit=1, emit=lines.append)
        self.assertEqual(len(lines), 1)
        self.assertEqual(cursor, 1)


if __name__ == "__main__":
    unittest.main()
