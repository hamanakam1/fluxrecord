"""CSV backlog import: the repository's own tickets.csv shape."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.importer import import_csv

HEADER = "ID,Epic,Title,Priority,Category,File,Description,AcceptanceCriteria,Dependencies\n"
ROWS = (
    "M-397001,Core Infrastructure,Split core.py,P0,Infrastructure,fluxrecord/core.py,"
    '"Split the monolith.","All routes work",'
    "\n"
    "M-397002,Core Infrastructure,Lazy loading,P2,Infrastructure,fluxrecord/__main__.py,"
    '"Only import when hit.","Faster startup",'
    "\n"
)


class ImporterTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.csv = self.root / "tickets.csv"
        self.csv.write_text(HEADER + ROWS)
        self.ledger = Ledger(self.root / "ledger.sqlite")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_backlog_becomes_tickets(self):
        result = import_csv(self.ledger, self.csv)
        self.assertEqual(result["imported_count"], 2)
        task = self.ledger.task_view("M-397001")
        self.assertEqual(task["title"], "Split core.py")
        self.assertEqual(task["objective"], "Split the monolith.")
        self.assertEqual(task["acceptance"], "All routes work")
        self.assertEqual(task["scope"], ["fluxrecord/core.py"])
        self.assertEqual(task["priority"], 1)
        self.assertIn("Core Infrastructure", task["tags"])
        self.assertIn("Infrastructure", task["tags"])
        self.assertIn("p0", task["tags"])

    def test_importing_twice_changes_nothing(self):
        import_csv(self.ledger, self.csv)
        again = import_csv(self.ledger, self.csv)
        self.assertEqual(again["imported_count"], 0)
        self.assertEqual(len(again["unchanged"]), 2)
        self.assertEqual(len(self.ledger.status()["tasks"]), 2)

    def test_a_reused_id_is_reported_not_dropped(self):
        self.csv.write_text(
            HEADER + ROWS + "M-397001,Security,JWT auth,P0,Auth,fluxrecord/auth/,"
            '"Implement JWT.","Tokens work",'
            "\n"
        )
        result = import_csv(self.ledger, self.csv)
        self.assertEqual(result["imported_count"], 2)
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["id"], "M-397001")
        self.assertIn("JWT auth", result["conflicts"][0]["title"])

    def test_a_reused_id_can_be_reassigned(self):
        self.csv.write_text(
            HEADER + ROWS + "M-397001,Security,JWT auth,P0,Auth,fluxrecord/auth/,"
            '"Implement JWT.","Tokens work",'
            "\n"
        )
        result = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(result["imported_count"], 3)
        self.assertEqual(result["conflicts"], [])
        self.assertEqual(len(result["reassigned"]), 1)
        fresh = result["reassigned"][0]["to"]
        self.assertNotEqual(fresh, "M-397001")
        self.assertEqual(self.ledger.task_view(fresh)["title"], "JWT auth")

    def test_reassignment_reserves_ids_from_alias_headers(self):
        for alias in ("id", "ticket", "ticket_id", "key", " ID "):
            with self.subTest(alias=alias):
                self.csv.write_text(
                    f"{alias},title,acceptance\n"
                    "M-397001,First,Done\n"
                    "M-397001,Second,Done\n"
                    "M-000001,Third,Done\n"
                )
                result = import_csv(self.ledger, self.csv, dry_run=True, reassign=True)
                self.assertEqual(result["imported"], ["M-397001", "M-397002", "M-000001"])
                self.assertEqual(
                    result["reassigned"],
                    [{"from": "M-397001", "to": "M-397002", "title": "Second"}],
                )

    def test_existing_source_id_conflicts_are_reported(self):
        self.ledger.add_task("M-397001", "Existing work", "Existing acceptance")
        before = self.ledger.status()
        self.csv.write_text("ID,title,acceptance\nM-397001,New work,Done\n")
        result = import_csv(self.ledger, self.csv)
        self.assertEqual(result["imported_count"], 0)
        self.assertEqual(result["unchanged"], [])
        self.assertEqual(len(result["conflicts"]), 1)
        self.assertEqual(result["conflicts"][0]["id"], "M-397001")
        self.assertIn("ledger", result["conflicts"][0]["reason"])
        self.assertEqual(self.ledger.status(), before)

    def test_existing_source_id_can_be_reassigned_without_overwriting_it(self):
        self.ledger.add_task("M-397001", "Existing work", "Existing acceptance")
        original = self.ledger.task_view("M-397001")
        self.csv.write_text("ID,title,acceptance\nM-397001,New work,Done\n")
        result = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(result["imported"], ["M-397002"])
        self.assertEqual(self.ledger.task_view("M-397001"), original)
        self.assertEqual(self.ledger.task_view("M-397002")["objective"], "New work")

    def test_reassignment_skips_occupied_candidates_and_remains_idempotent(self):
        self.ledger.add_task("M-397002", "Existing work", "Existing acceptance")
        original = self.ledger.task_view("M-397002")
        self.csv.write_text("ID,title,acceptance\nM-397001,First,Done\nM-397001,Second,Done\n")
        first = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(first["imported"], ["M-397001", "M-397003"])
        self.assertEqual(self.ledger.task_view("M-397002"), original)
        self.assertEqual(self.ledger.task_view("M-397003")["objective"], "Second")
        before = self.ledger.status()
        second = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(second["imported_count"], 0)
        self.assertEqual(second["unchanged"], first["imported"])
        self.assertEqual(second["reassigned"], first["reassigned"])
        self.assertEqual(self.ledger.status(), before)

    def test_reassignment_reuses_matching_legacy_csv_seeds(self):
        self.ledger.add_task("M-397001", "First", "Done", seed_key="csv:M-397001")
        self.ledger.add_task("M-397002", "Second", "Done", seed_key="csv:M-397002")
        self.csv.write_text("ID,title,acceptance\nM-397001,First,Done\nM-397001,Second,Done\n")
        before = self.ledger.status()
        result = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(result["imported_count"], 0)
        self.assertEqual(result["unchanged"], ["M-397001", "M-397002"])
        self.assertEqual(self.ledger.status(), before)

    def test_matching_unseeded_candidate_is_not_claimed_as_a_previous_import(self):
        self.ledger.add_task("M-397002", "Second", "Done")
        self.csv.write_text("ID,title,acceptance\nM-397001,First,Done\nM-397001,Second,Done\n")
        result = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(result["imported"], ["M-397001", "M-397003"])
        self.assertEqual(self.ledger.task_view("M-397002")["seed_key"], "")

    def test_repeated_reassigned_row_is_a_duplicate(self):
        self.csv.write_text(
            "ID,title,acceptance\nM-397001,First,Done\nM-397001,Second,Done\nM-397001,Second,Done\n"
        )
        first = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(first["imported"], ["M-397001", "M-397002"])
        self.assertEqual(first["duplicates"], ["M-397001"])
        second = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(second["imported_count"], 0)
        self.assertEqual(second["unchanged"], first["imported"])
        self.assertEqual(second["duplicates"], first["duplicates"])

    def test_dry_run_resolves_existing_collisions_like_a_real_import(self):
        self.ledger.add_task("M-397001", "Existing work", "Existing acceptance")
        self.ledger.add_task("M-397003", "Occupied candidate", "Done")
        self.csv.write_text(
            "ticket_id,title,acceptance\n"
            "M-397001,First,Done\nM-397001,Second,Done\nM-397002,Third,Done\n"
        )
        before = self.ledger.status()
        preview = import_csv(self.ledger, self.csv, dry_run=True, reassign=True)
        self.assertEqual(self.ledger.status(), before)
        actual = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(actual["imported"], ["M-397004", "M-397005", "M-397002"])
        self.assertEqual({**preview, "dry_run": False}, actual)
        again = import_csv(self.ledger, self.csv, dry_run=True, reassign=True)
        self.assertEqual(again["imported_count"], 0)
        self.assertEqual(again["unchanged"], actual["imported"])
        self.assertEqual(again["reassigned"], actual["reassigned"])

    def test_only_ticket_ids_count_as_dependencies(self):
        self.csv.write_text(
            HEADER + "M-397001,Core,Split core,P0,Inf,fluxrecord/core.py,"
            '"Split the monolith.","All routes work","M-397002, some prose"'
            "\n" + "M-397002,Core,Second,P0,Inf,fluxrecord/core.py,"
            '"Second task.","Done",'
            "\n"
        )
        import_csv(self.ledger, self.csv)
        self.assertEqual(self.ledger.task_view("M-397001")["depends_on"], ["M-397002"])

    def test_a_dry_run_writes_nothing(self):
        result = import_csv(self.ledger, self.csv, dry_run=True)
        self.assertEqual(result["imported_count"], 2)
        self.assertTrue(result["dry_run"])
        self.assertEqual(self.ledger.status()["tasks"], [])

    def test_rows_without_acceptance_are_skipped_with_a_reason(self):
        self.csv.write_text(
            HEADER + "M-397003,Core,No acceptance,P1,Inf,fluxrecord/core.py,Only a description,,\n"
        )
        result = import_csv(self.ledger, self.csv)
        self.assertEqual(result["imported_count"], 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertIn("acceptance", result["skipped"][0]["reason"])

    def test_the_repository_backlog_imports_cleanly(self):
        repo_csv = Path(__file__).resolve().parent.parent / "tickets.csv"
        if not repo_csv.is_file():
            self.skipTest("tickets.csv is not present")
        result = import_csv(self.ledger, repo_csv)
        self.assertEqual(result["skipped"], [])
        self.assertGreater(result["imported_count"], 0)
        self.assertEqual(len(self.ledger.status()["tasks"]), result["imported_count"])


if __name__ == "__main__":
    unittest.main()
