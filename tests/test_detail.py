"""The detail surfaces: what a reviewer reads about one ticket."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger


class DetailTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "First", "Done", scope=["repo/a"])
        self.ledger.add_task("M-2", "Second", "Done", scope=["repo/b"], depends_on=["M-1"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_dependencies_are_reported_with_their_state(self):
        dependencies = self.ledger.dependencies_of("M-2")
        self.assertEqual(len(dependencies), 1)
        self.assertEqual(dependencies[0]["id"], "M-1")
        self.assertEqual(dependencies[0]["status"], "ready")
        self.assertEqual(dependencies[0]["title"], "First")

    def test_an_unknown_dependency_is_reported_as_unknown(self):
        self.ledger.add_task("M-3", "Third", "Done", scope=["repo/c"], depends_on=["M-999"])
        dependencies = self.ledger.dependencies_of("M-3")
        self.assertEqual(dependencies[0]["status"], "unknown")

    def test_context_is_listed_without_its_bodies(self):
        self.ledger.add_context("M-1", "some context", "note", priority=5)
        context = self.ledger.context_of("M-1")
        self.assertEqual(len(context), 1)
        self.assertEqual(context[0]["source"], "note")
        self.assertEqual(context[0]["priority"], 5)
        self.assertEqual(context[0]["bytes"], len("some context"))
        self.assertNotIn("body", context[0])

    def test_scope_conflicts_are_reported(self):
        self.ledger.add_task("M-4", "Other", "Done", scope=["repo/a"])
        started = self.ledger.start("M-4", "terminal-1")
        conflicts = self.ledger.scope_conflicts("M-1")
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["id"], "M-4")
        self.assertEqual(conflicts[0]["scope"], ["repo/a"])
        del started

    def test_a_ticket_does_not_conflict_with_itself(self):
        self.assertEqual(self.ledger.scope_conflicts("M-1"), [])

    def test_show_reports_the_active_session(self):
        # The key is removed rather than null when there is no active
        # session: a key that is always null is worse than an absent key.
        self.assertNotIn("active_session", self.ledger.show("M-1"))
        started = self.ledger.start("M-1", "terminal-1")
        show = self.ledger.show("M-1")
        self.assertEqual(show["active_session"]["id"], started["session_id"])
        self.assertEqual(show["active_session"]["owner"], "terminal-1")

    def test_show_reports_dependencies_context_and_conflicts(self):
        self.ledger.add_context("M-2", "context", "note")
        show = self.ledger.show("M-2")
        self.assertEqual(show["dependencies"][0]["id"], "M-1")
        self.assertEqual(len(show["context"]), 1)
        self.assertIn("scope_conflicts", show)

    def test_the_packet_carries_the_dependencies(self):
        packet = self.ledger.pack("M-2")["packet"]
        self.assertEqual(packet["dependencies"][0]["id"], "M-1")
        self.assertEqual(packet["dependencies"][0]["status"], "ready")

    def test_the_packet_carries_scope_conflicts(self):
        self.ledger.add_task("M-5", "Rival", "Done", scope=["repo/b"])
        self.ledger.start("M-5", "terminal-1")
        packet = self.ledger.pack("M-2")["packet"]
        self.assertEqual([c["id"] for c in packet["scope_conflicts"]], ["M-5"])

    def test_the_packet_still_fits_its_budget_with_the_extra_detail(self):
        self.ledger.add_context("M-1", "x" * 400, "note")
        result = self.ledger.pack("M-1", max_bytes=1200)
        self.assertLessEqual(result["packet_bytes"], 1200)


if __name__ == "__main__":
    unittest.main()
