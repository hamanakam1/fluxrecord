"""The dashboard is another source of work."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.intake import intake_dashboard

PREVIEW = {
    "origin": "http://127.0.0.1:8080",
    "tickets": [
        {"id": "M-500001", "title": "Dashboard item", "status": "planned", "priority": 1},
        {"id": "EXT-9", "title": "External item", "status": "backlog"},
        {"title": "No id", "status": "progress"},
    ],
}


class DashboardIntakeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_dashboard_ticket_becomes_a_ledger_ticket(self):
        result = intake_dashboard(self.ledger, PREVIEW, "http://127.0.0.1:8080")
        self.assertEqual(result["created_count"], 2)
        self.assertEqual(result["seen"], 3)
        ticket = self.ledger.task_view("M-500001")
        self.assertEqual(ticket["title"], "Dashboard item")
        self.assertEqual(ticket["scope"], ["dashboard:M-500001"])
        self.assertIn("dashboard", ticket["tags"])
        self.assertIn("planned", ticket["tags"])

    def test_a_dashboard_id_that_is_not_a_ticket_id_gets_one(self):
        intake_dashboard(self.ledger, PREVIEW, "http://127.0.0.1:8080")
        ticket = self.ledger.task_view("EXT-9")
        self.assertEqual(ticket["id"], "EXT-9")

    def test_intake_is_idempotent(self):
        first = intake_dashboard(self.ledger, PREVIEW, "http://127.0.0.1:8080")
        second = intake_dashboard(self.ledger, PREVIEW, "http://127.0.0.1:8080")
        self.assertEqual(first["created_count"], 2)
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(len(self.ledger.status()["tasks"]), 2)

    def test_a_record_without_an_id_is_skipped_with_a_reason(self):
        result = intake_dashboard(self.ledger, PREVIEW, "http://127.0.0.1:8080")
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("no id", result["skipped"][0]["reason"])

    def test_the_statuses_are_summarised(self):
        result = intake_dashboard(self.ledger, PREVIEW, "http://127.0.0.1:8080")
        self.assertEqual(result["statuses"], ["backlog", "planned", "progress"])

    def test_an_empty_dashboard_is_fine(self):
        result = intake_dashboard(self.ledger, {"tickets": []}, "http://127.0.0.1:8080")
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["seen"], 0)


if __name__ == "__main__":
    unittest.main()
