"""Lease renewal, reconciliation, and the automatic move to done."""

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

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

    def test_lease_extensions_preserve_a_later_existing_deadline(self):
        for operation in ("renew", "adopt", "checkpoint"):
            with self.subTest(operation=operation):
                task_id = f"lease-{operation}"
                with patch("fluxrecord.core.time.time", return_value=1000):
                    self.ledger.add_task(task_id, "Objective", "Acceptance")
                    started = self.ledger.start(task_id, "terminal-1", lease_seconds=3600)
                sid = started["session_id"]
                with patch("fluxrecord.core.time.time", return_value=1100):
                    if operation == "adopt":
                        result = self.ledger.adopt(sid, "terminal-2", "Confirmed handover")
                    elif operation == "checkpoint":
                        result = self.ledger.checkpoint(sid, "Progress", "Diff", "Review", 10)
                    else:
                        result = self.ledger.renew(sid)
                    session = next(row for row in self.ledger.sessions() if row["id"] == sid)
                self.assertEqual(session["lease_until"], started["lease_until"])
                if operation != "checkpoint":
                    self.assertEqual(result["lease_until"], started["lease_until"])
                self.assertEqual(session["tokens"], 10 if operation == "checkpoint" else 0)
                self.assertEqual(
                    self.ledger.task(task_id)["owner"],
                    "terminal-2" if operation == "adopt" else "terminal-1",
                )
                with patch("fluxrecord.core.time.time", return_value=2001):
                    self.assertEqual(self.ledger.reconcile()["expired"], [])
                    self.assertEqual(self.ledger.task(task_id)["status"], "in_progress")
                with patch("fluxrecord.core.time.time", return_value=4600):
                    self.assertEqual(self.ledger.reconcile()["expired"], [sid])

    def test_lease_extensions_still_extend_a_nearly_expired_session(self):
        for operation in ("renew", "adopt", "checkpoint"):
            with self.subTest(operation=operation):
                task_id = f"lease-{operation}"
                with patch("fluxrecord.core.time.time", return_value=1000):
                    self.ledger.add_task(task_id, "Objective", "Acceptance")
                    sid = self.ledger.start(task_id, "terminal-1", lease_seconds=60)["session_id"]
                with patch("fluxrecord.core.time.time", return_value=1030):
                    if operation == "adopt":
                        self.ledger.adopt(sid, "terminal-2", "Confirmed handover")
                    elif operation == "checkpoint":
                        self.ledger.checkpoint(sid, "Progress", "Diff", "Review", 10)
                    else:
                        self.ledger.renew(sid)
                    session = next(row for row in self.ledger.sessions() if row["id"] == sid)
                self.assertEqual(session["lease_until"], 1930)
                with patch("fluxrecord.core.time.time", return_value=1060):
                    self.assertEqual(self.ledger.reconcile()["expired"], [])
                with patch("fluxrecord.core.time.time", return_value=1930):
                    self.assertEqual(self.ledger.reconcile()["expired"], [sid])

    def test_invalid_extension_durations_preserve_session_and_events(self):
        sid = self.ledger.start("M-1", "terminal-1")["session_id"]
        before = self.ledger.show("M-1")
        for operation in ("renew", "checkpoint"):
            for duration in (0, -1, True, 1.5):
                with self.subTest(operation=operation, duration=duration):
                    with self.assertRaises(ValueError):
                        if operation == "renew":
                            self.ledger.renew(sid, duration)
                        else:
                            self.ledger.checkpoint(
                                sid, "Progress", "Diff", "Review", 10, lease_seconds=duration
                            )
                    self.assertEqual(self.ledger.show("M-1"), before)

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
        self.assertEqual(decided["status"], "done")
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
