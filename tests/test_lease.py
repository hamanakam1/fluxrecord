"""Lease renewal, reconciliation, and the automatic move to done."""

import tempfile
import time
import unittest
from pathlib import Path

from fluxrecord.core import Conflict, Ledger


class LeaseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_renewing_extends_the_lease(self):
        started = self.ledger.start("M-1", "terminal-1", lease_seconds=60)
        before = started["lease_until"]
        renewed = self.ledger.renew(started["session_id"], 900)
        self.assertGreater(renewed["lease_until"], before)
        self.assertEqual(renewed["task_id"], "M-1")

    def test_renewing_records_an_event(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.renew(started["session_id"], 900)
        kinds = [event["type"] for event in self.ledger.events()]
        self.assertIn("session.renewed", kinds)

    def test_an_expired_session_cannot_be_renewed(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=? WHERE id=?",
            (time.time() - 1, started["session_id"]),
        )
        with self.assertRaises(Conflict):
            self.ledger.renew(started["session_id"], 900)

    def test_a_non_positive_lease_is_refused(self):
        started = self.ledger.start("M-1", "terminal-1")
        with self.assertRaises(ValueError):
            self.ledger.renew(started["session_id"], 0)

    def test_reconcile_reports_what_it_expired(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=? WHERE id=?",
            (time.time() - 1, started["session_id"]),
        )
        result = self.ledger.reconcile()
        self.assertEqual(result["count"], 1)
        self.assertEqual(len(result["expired"]), 1)
        self.assertEqual(self.ledger.task("M-1")["status"], "blocked")

    def test_reconcile_on_a_healthy_ledger_expires_nothing(self):
        self.ledger.start("M-1", "terminal-1", lease_seconds=900)
        result = self.ledger.reconcile()
        self.assertEqual(result["count"], 0)


class DoneTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_acceptance_records_the_move_to_done(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        self.ledger.finish(started["session_id"], "evidence")
        decided = self.ledger.decide("M-1", "accept", "reviewed")
        self.assertEqual(decided["status"], "accepted")
        kinds = [event["type"] for event in self.ledger.events()]
        self.assertIn("operator.accept", kinds)
        self.assertIn("task.done", kinds)

    def test_a_retry_does_not_record_a_done(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        self.ledger.finish(started["session_id"], "evidence")
        self.ledger.decide("M-1", "retry", "not yet")
        kinds = [event["type"] for event in self.ledger.events()]
        self.assertNotIn("task.done", kinds)
        self.assertEqual(self.ledger.task("M-1")["status"], "ready")


if __name__ == "__main__":
    unittest.main()
