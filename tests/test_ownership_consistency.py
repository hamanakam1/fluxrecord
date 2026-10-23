"""Ticket projections agree with session ownership changes."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord.core import Conflict, Ledger
from fluxrecord.files import export, read_ticket


class OwnershipConsistencyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.add_task(
            "T-1", "Synthetic objective", "Synthetic acceptance", scope=["repo/x"], owner="planned"
        )

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def assert_owner(self, owner):
        self.assertEqual(self.ledger.task("T-1")["owner"], owner)
        self.assertEqual(self.ledger.task_view("T-1")["owner"], owner)
        self.assertEqual(self.ledger.show("T-1")["ticket"]["owner"], owner)
        self.assertEqual(self.ledger.pack("T-1")["packet"]["owner"], owner)
        self.assertEqual(self.ledger.board()[0]["owner"], owner)
        self.assertEqual(self.ledger.list_tasks(compact=True)[0]["owner"], owner)
        self.assertEqual([task["id"] for task in self.ledger.list_tasks(owner=owner)], ["T-1"])
        project = self.root / "project"
        export(self.ledger, project)
        self.assertEqual(read_ticket(project / "tickets/T-1.md")["owner"], owner)
        self.assertEqual(json.loads((project / "index.json").read_text())["T-1"]["owner"], owner)

    def snapshot(self):
        return {
            "task": self.ledger.task("T-1"),
            "sessions": [dict(row) for row in self.ledger.db.execute("SELECT * FROM sessions")],
            "events": self.ledger.events(),
        }

    def test_start_replaces_the_planned_owner_everywhere(self):
        self.ledger.start("T-1", "worker-a")
        self.assert_owner("worker-a")
        self.assertEqual(self.ledger.list_tasks(owner="planned"), [])

    def test_start_makes_an_unassigned_ticket_findable_by_owner(self):
        self.ledger.add_task("T-2", "Another objective", "Acceptance", scope=["repo/y"])
        self.ledger.start("T-2", "worker-b")
        self.assertEqual([task["id"] for task in self.ledger.list_tasks(owner="worker-b")], ["T-2"])
        self.assertEqual(self.ledger.task_view("T-2")["owner"], "worker-b")

    def test_handoff_changes_every_owner_projection_without_changing_the_lease(self):
        started = self.ledger.start("T-1", "worker-a")
        result = self.ledger.handoff(started["session_id"], "worker-b", "Transfer work")
        self.assertEqual(result["previous_owner"], "worker-a")
        self.assertEqual(result["lease_until"], started["lease_until"])
        self.assert_owner("worker-b")
        self.assertEqual(self.ledger.list_tasks(owner="worker-a"), [])

    def test_adoption_changes_every_owner_projection_and_extends_the_lease(self):
        started = self.ledger.start("T-1", "worker-a", lease_seconds=60)
        result = self.ledger.adopt(started["session_id"], "worker-c", "Recover work")
        self.assertEqual(result["previous_owner"], "worker-a")
        self.assertGreater(result["lease_until"], started["lease_until"])
        self.assert_owner("worker-c")
        self.assertEqual(self.ledger.list_tasks(owner="worker-a"), [])

    def test_completion_keeps_the_worker_visible_for_review_and_acceptance(self):
        session = self.ledger.start("T-1", "worker-a")["session_id"]
        self.ledger.checkpoint(session, "Progress", "Change", "Review", 10)
        self.ledger.finish(session, "Verified")
        self.assert_owner("worker-a")
        self.ledger.decide("T-1", "accept", "Reviewed")
        self.assert_owner("worker-a")

    def test_retry_assigns_the_new_worker_without_changing_prior_session_or_usage(self):
        first = self.ledger.start("T-1", "worker-a")["session_id"]
        self.ledger.checkpoint(first, "Progress", "Change", "Continue", 10)
        self.ledger.fail(first, "tool", "Synthetic tool failure")
        self.assert_owner("worker-a")
        self.ledger.decide("T-1", "retry", "Try again")
        second = self.ledger.start("T-1", "worker-b")["session_id"]
        self.assert_owner("worker-b")
        sessions = {session["id"]: session for session in self.ledger.sessions()}
        self.assertEqual(sessions[first]["owner"], "worker-a")
        self.assertEqual(sessions[first]["tokens"], 10)
        self.assertEqual(sessions[second]["owner"], "worker-b")
        self.assertEqual(self.ledger.status()["metrics"]["reported_tokens"], 10)

    def test_rejected_operations_preserve_ticket_and_session_owners(self):
        session = self.ledger.start("T-1", "worker-a")["session_id"]
        before = self.snapshot()
        with self.assertRaises(Conflict):
            self.ledger.start("T-1", "worker-b")
        self.assertEqual(self.snapshot(), before)
        for operation in (self.ledger.handoff, self.ledger.adopt):
            with self.subTest(operation=operation.__name__, reason="missing"):
                with self.assertRaises(ValueError):
                    operation(session, "worker-b", "")
                self.assertEqual(self.snapshot(), before)
        self.ledger.db.execute("UPDATE sessions SET lease_until=0 WHERE id=?", (session,))
        before = self.snapshot()
        for operation in (self.ledger.handoff, self.ledger.adopt):
            with self.subTest(operation=operation.__name__, lease="expired"):
                with self.assertRaises(Conflict):
                    operation(session, "worker-b", "Too late")
                self.assertEqual(self.snapshot(), before)

    def test_failed_transactions_roll_back_both_owners_and_the_event(self):
        record_event = self.ledger.event

        def fail_after_recording(*args, **kwargs):
            record_event(*args, **kwargs)
            raise OSError("injected event failure")

        before = self.snapshot()
        with mock.patch.object(self.ledger, "event", side_effect=fail_after_recording):
            with self.assertRaisesRegex(OSError, "injected event failure"):
                self.ledger.start("T-1", "worker-a")
        self.assertEqual(self.snapshot(), before)

        session = self.ledger.start("T-1", "worker-a")["session_id"]
        for operation in (self.ledger.handoff, self.ledger.adopt):
            with self.subTest(operation=operation.__name__):
                before = self.snapshot()
                with mock.patch.object(self.ledger, "event", side_effect=fail_after_recording):
                    with self.assertRaisesRegex(OSError, "injected event failure"):
                        operation(session, "worker-b", "Transfer work")
                self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
