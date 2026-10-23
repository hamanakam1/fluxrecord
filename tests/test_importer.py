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

    def test_an_existing_id_with_different_content_is_a_conflict(self):
        original = self.ledger.add_task("M-000001", "Original", "Original acceptance")
        for objective, acceptance in (
            ("Changed", "Original acceptance"),
            ("Original", "Changed acceptance"),
        ):
            with self.subTest(objective=objective, acceptance=acceptance):
                self.csv.write_text(
                    f"ID,Description,AcceptanceCriteria\nM-000001,{objective},{acceptance}\n"
                )
                result = import_csv(self.ledger, self.csv)
                self.assertEqual(result["imported_count"], 0)
                self.assertEqual(result["unchanged"], [])
                self.assertEqual(len(result["conflicts"]), 1)
                self.assertEqual(result["conflicts"][0]["id"], "M-000001")
                self.assertEqual(self.ledger.task_view("M-000001"), original)

    def test_reassignment_reserves_all_csv_id_aliases(self):
        for index, alias in enumerate(("ID", "id", "Ticket", "ticket_id", "Key")):
            with self.subTest(alias=alias):
                with Ledger(self.root / f"alias-{index}.sqlite") as ledger:
                    ledger.add_task("M-000001", "Original", "Original acceptance")
                    ledger.add_task("M-000010", "Existing high id", "Keep")
                    self.csv.write_text(
                        f"{alias},Description,AcceptanceCriteria\n"
                        "M-000001,Replacement,Replacement acceptance\n"
                        "M-000020,Reserved row,Done\n"
                    )
                    result = import_csv(ledger, self.csv, reassign=True)
                    self.assertEqual(result["imported"], ["M-000021", "M-000020"])
                    self.assertEqual(result["reassigned"][0]["to"], "M-000021")
                    self.assertEqual(ledger.task_view("M-000010")["objective"], "Existing high id")

    def test_reassignment_reserves_ids_already_in_the_ledger(self):
        self.ledger.add_task("M-000001", "Original", "Original acceptance")
        self.ledger.add_task("M-000100", "Existing high id", "Keep")
        self.csv.write_text(
            "ID,Description,AcceptanceCriteria\nM-000001,Replacement,Replacement acceptance\n"
        )
        result = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(result["imported"], ["M-000101"])
        self.assertEqual(self.ledger.task_view("M-000100")["objective"], "Existing high id")

    def test_reassigned_imports_are_idempotent_and_preserve_normal_seed_keys(self):
        self.csv.write_text(
            "ID,Description,AcceptanceCriteria\n"
            "M-000001,First,First acceptance\n"
            "M-000001,Second,Second acceptance\n"
            "M-000001,Second,Second acceptance\n"
        )
        result = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(result["imported_count"], 2)
        self.assertEqual(result["duplicates"], ["M-000001"])
        tasks = self.ledger.list_tasks()
        events = self.ledger.events()
        again = import_csv(self.ledger, self.csv, reassign=True)
        self.assertEqual(again["imported_count"], 0)
        self.assertEqual(again["unchanged"], result["imported"])
        self.assertEqual(again["reassigned"], result["reassigned"])
        self.assertEqual(again["duplicates"], ["M-000001"])
        self.assertEqual(self.ledger.list_tasks(), tasks)
        self.assertEqual(self.ledger.events(), events)
        self.assertEqual(self.ledger.task_view("M-000001")["seed_key"], "csv:M-000001")

    def test_dry_run_matches_existing_collisions_without_writing(self):
        original = self.ledger.add_task("M-000001", "Original", "Original acceptance")
        self.csv.write_text(
            "ID,Description,AcceptanceCriteria\n"
            "M-000001,Replacement,Replacement acceptance\n"
            "M-000002,New task,Done\n"
        )
        for reassign in (False, True):
            with self.subTest(reassign=reassign):
                with Ledger(self.root / f"dry-{reassign}.sqlite") as ledger:
                    ledger.add_task("M-000001", "Original", "Original acceptance")
                    events = ledger.events()
                    preview = import_csv(ledger, self.csv, dry_run=True, reassign=reassign)
                    self.assertEqual(ledger.events(), events)
                    self.assertEqual(len(ledger.list_tasks()), 1)
                    result = import_csv(ledger, self.csv, reassign=reassign)
                    self.assertEqual(preview | {"dry_run": False}, result)
        self.assertEqual(self.ledger.task_view("M-000001"), original)

    def test_dry_run_reuses_a_previous_reassignment_without_writing(self):
        self.ledger.add_task("M-000001", "Original", "Original acceptance")
        self.csv.write_text(
            "ID,Description,AcceptanceCriteria\nM-000001,Replacement,Replacement acceptance\n"
        )
        first = import_csv(self.ledger, self.csv, reassign=True)
        events = self.ledger.events()
        preview = import_csv(self.ledger, self.csv, dry_run=True, reassign=True)
        self.assertEqual(preview["imported_count"], 0)
        self.assertEqual(preview["unchanged"], first["imported"])
        self.assertEqual(preview["reassigned"], first["reassigned"])
        self.assertEqual(self.ledger.events(), events)

    def test_a_dry_run_does_not_reconcile_expired_sessions(self):
        import_csv(self.ledger, self.csv)
        session = self.ledger.start("M-397001", "synthetic-worker")
        self.ledger.db.execute(
            "UPDATE sessions SET lease_until=0 WHERE id=?", (session["session_id"],)
        )
        tasks = self.ledger.list_tasks()
        events = self.ledger.events()
        result = import_csv(self.ledger, self.csv, dry_run=True)
        self.assertEqual(result["unchanged"], ["M-397001", "M-397002"])
        self.assertEqual(self.ledger.list_tasks(), tasks)
        self.assertEqual(self.ledger.events(), events)
        self.assertEqual(self.ledger.sessions()[0]["status"], "active")

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
