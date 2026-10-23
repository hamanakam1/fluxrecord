"""Dashboard source identities survive local ID collisions without overwrites."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.intake import intake_dashboard

ORIGIN = "http://127.0.0.1:8080"
OTHER_ORIGIN = "http://127.0.0.1:8081"


class DashboardIdentityTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.ledger = Ledger(Path(self.temp.name) / "ledger.sqlite")
        self.addCleanup(self.ledger.close)

    def intake(self, tickets, origin=ORIGIN):
        return intake_dashboard(self.ledger, {"origin": origin, "tickets": tickets})

    def source_task(self, external_id, origin=ORIGIN):
        task = self.ledger.task_by_seed_key(f"dashboard:{origin}#{external_id}")
        self.assertIsNotNone(task)
        return task

    def test_local_collision_allocates_once_and_preserves_existing_task(self):
        original = self.ledger.add_task("EXT-9", "Local work", "Local result", scope=["local"])
        original_events = self.ledger.events(task_id="EXT-9")
        tickets = [{"id": "EXT-9", "title": "Dashboard work"}]

        first = self.intake(tickets)
        self.assertEqual(first["created_count"], 1)
        imported = self.source_task("EXT-9")
        self.assertNotEqual(imported["id"], "EXT-9")
        self.assertEqual(imported["title"], "Dashboard work")
        self.assertEqual(self.ledger.task_view("EXT-9"), original)
        self.assertEqual(self.ledger.events(task_id="EXT-9"), original_events)

        events = self.ledger.events()
        second = self.intake(tickets)
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(second["skipped"][0]["reason"], "already seeded")
        self.assertEqual(self.source_task("EXT-9")["id"], imported["id"])
        self.assertEqual(self.ledger.events(), events)

    def test_two_origins_with_the_same_external_id_create_independent_tasks(self):
        tickets = [{"id": "EXT-9", "title": "Dashboard work"}]
        self.assertEqual(self.intake(tickets)["created_count"], 1)
        self.assertEqual(self.intake(tickets, OTHER_ORIGIN)["created_count"], 1)
        first = self.source_task("EXT-9")
        second = self.source_task("EXT-9", OTHER_ORIGIN)
        self.assertEqual(first["id"], "EXT-9")
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["scope"], [f"dashboard:{ORIGIN}#EXT-9"])
        self.assertEqual(second["scope"], [f"dashboard:{OTHER_ORIGIN}#EXT-9"])
        self.ledger.start(first["id"], "worker-a")
        self.ledger.start(second["id"], "worker-b")

    def test_nonlocal_external_ids_do_not_share_scope_across_origins(self):
        tickets = [{"id": "remote/7", "title": "Dashboard work"}]
        self.intake(tickets)
        self.intake(tickets, OTHER_ORIGIN)
        first = self.source_task("remote/7")
        second = self.source_task("remote/7", OTHER_ORIGIN)
        self.assertNotEqual(first["scope"], second["scope"])
        self.ledger.start(first["id"], "worker-a")
        self.ledger.start(second["id"], "worker-b")

    def test_all_valid_incoming_local_ids_are_reserved_before_allocation(self):
        result = self.intake(
            [
                {"id": "remote/7", "title": "Needs allocation"},
                {"id": "M-000001", "title": "Keep this identifier"},
            ]
        )
        self.assertEqual(result["created_count"], 2)
        self.assertEqual(self.source_task("M-000001")["id"], "M-000001")
        self.assertEqual(self.source_task("remote/7")["id"], "M-000002")

    def test_allocation_avoids_occupied_and_incoming_ids(self):
        self.ledger.add_task("M-000001", "Local work", "Local result")
        result = self.intake(
            [
                {"id": "remote/7", "title": "Needs allocation"},
                {"id": "M-000002", "title": "Keep this identifier"},
            ]
        )
        self.assertEqual(result["created_count"], 2)
        self.assertEqual(self.source_task("M-000002")["id"], "M-000002")
        self.assertEqual(self.source_task("remote/7")["id"], "M-000003")

    def test_invalid_identifier_types_do_not_create_tasks_or_events(self):
        invalid_ids = [None, True, False, 1.5, {}, [], "", "  "]
        events = self.ledger.events()
        result = self.intake([{"id": value, "title": "Invalid source"} for value in invalid_ids])
        self.assertEqual(result["seen"], len(invalid_ids))
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["skipped_count"], len(invalid_ids))
        self.assertEqual(self.ledger.events(), events)
        self.assertEqual(self.ledger.db.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)

    def test_integer_and_trimmed_text_identifiers_remain_supported(self):
        result = self.intake(
            [{"id": value, "title": "Valid source"} for value in (0, 42, -7, " EXT-9 ")]
        )
        self.assertEqual(result["created_count"], 4)
        self.assertEqual(self.source_task("0")["id"], "0")
        self.assertEqual(self.source_task("42")["id"], "42")
        self.assertEqual(self.source_task("EXT-9")["id"], "EXT-9")
        self.assertTrue(self.source_task("-7")["id"].startswith("M-"))

    def test_existing_seed_mapping_and_legacy_scope_are_unchanged(self):
        original = self.ledger.add_task(
            "M-000007",
            "Original objective",
            "Original outcome",
            scope=["dashboard:EXT-9"],
            seed_key=f"dashboard:{ORIGIN}#EXT-9",
        )
        events = self.ledger.events()
        result = self.intake([{"id": "EXT-9", "title": "Changed source", "priority": 9}])
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "already seeded")
        self.assertEqual(self.ledger.task_view("M-000007"), original)
        self.assertEqual(self.ledger.events(), events)

    def test_invalid_rows_do_not_reserve_local_ids(self):
        result = self.intake(
            [
                {"id": "M-000001", "title": "Bad priority", "priority": 9},
                {"id": "M-000002", "title": ["Bad title"]},
                {"id": "remote/7", "title": "Valid source"},
            ]
        )
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["skipped_count"], 2)
        self.assertEqual(self.source_task("remote/7")["id"], "M-000001")
        self.assertEqual(len(self.ledger.events()), 1)

    def test_invalid_row_does_not_claim_the_seed_of_a_later_valid_row(self):
        result = self.intake(
            [
                {"id": "EXT-9", "title": {"invalid": "title"}},
                {"id": "EXT-9", "title": "Valid source"},
                {"id": "EXT-9", "title": "Repeated source"},
            ]
        )
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["skipped_count"], 2)
        self.assertEqual(self.source_task("EXT-9")["title"], "Valid source")
        self.assertEqual(len(self.ledger.events()), 1)

    def test_long_source_seed_is_rejected_without_reserving_a_local_id(self):
        result = self.intake(
            [
                {"id": "M-000001" + "x" * 100, "title": "Too long"},
                {"id": "remote/7", "title": "Valid source"},
            ]
        )
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["skipped_count"], 1)
        self.assertIn("100 characters", result["skipped"][0]["reason"])
        self.assertEqual(self.source_task("remote/7")["id"], "M-000001")


if __name__ == "__main__":
    unittest.main()
