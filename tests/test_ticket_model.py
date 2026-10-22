"""The ticket model: schema migrations, the richer ticket, seed idempotency."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import SCHEMA_VERSION, Ledger


class SchemaTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_a_fresh_database_runs_the_current_schema(self):
        ledger = Ledger(self.path)
        self.assertEqual(ledger.schema_version(), SCHEMA_VERSION)
        ledger.close()

    def test_an_older_database_is_migrated_additively(self):
        # A version 1 database, as written before the ticket model grew.
        db = sqlite3.connect(self.path)
        db.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, objective TEXT NOT NULL,"
            " acceptance TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,"
            " status TEXT NOT NULL, token_budget INTEGER NOT NULL, created REAL NOT NULL);"
        )
        db.execute(
            "INSERT INTO tasks VALUES ('M-1','old','old acceptance','code','[\"repo/a\"]',"
            "'ready',100,1.0)"
        )
        db.commit()
        db.close()

        ledger = Ledger(self.path)
        self.assertEqual(ledger.schema_version(), SCHEMA_VERSION)
        task = ledger.task_view("M-1")
        self.assertEqual(task["objective"], "old")
        self.assertEqual(task["scope"], ["repo/a"])
        self.assertEqual(task["priority"], 2)
        self.assertEqual(task["tags"], [])
        ledger.close()

    def test_an_older_database_is_migrated_to_the_new_vocabulary(self):
        db = sqlite3.connect(self.path)
        db.executescript(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, objective TEXT NOT NULL,"
            " acceptance TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,"
            " status TEXT NOT NULL, token_budget INTEGER NOT NULL, created REAL NOT NULL);"
        )
        db.execute(
            "INSERT INTO tasks VALUES ('M-1','old','old','code','[\"repo/a\"]','running',100,1.0)"
        )
        db.commit()
        db.close()

        ledger = Ledger(self.path)
        self.assertEqual(ledger.task("M-1")["status"], "in_progress")
        self.assertEqual(ledger.schema_version(), SCHEMA_VERSION)
        ledger.close()

    def test_migrating_twice_is_idempotent(self):
        Ledger(self.path).close()
        ledger = Ledger(self.path)
        self.assertEqual(ledger.schema_version(), SCHEMA_VERSION)
        ledger.close()
        again = Ledger(self.path)
        self.assertEqual(again.schema_version(), SCHEMA_VERSION)
        again.close()


class TicketModelTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def add(self, **kwargs):
        base = {
            "task_id": "M-1",
            "objective": "Fix search",
            "acceptance": "Regression passes",
            "scope": ["repo/search"],
        }
        base.update(kwargs)
        return self.ledger.add_task(**base)

    def test_the_full_ticket_shape_is_kept(self):
        task = self.add(
            title="Search is broken",
            priority=1,
            tags=["search", "p1"],
            depends_on=["M-2"],
            blocks=["M-3"],
            owner="terminal-1",
        )
        self.assertEqual(task["title"], "Search is broken")
        self.assertEqual(task["priority"], 1)
        self.assertEqual(task["tags"], ["search", "p1"])
        self.assertEqual(task["depends_on"], ["M-2"])
        self.assertEqual(task["blocks"], ["M-3"])
        self.assertEqual(task["owner"], "terminal-1")
        self.assertEqual(task["scope"], ["repo/search"])

    def test_json_columns_are_decoded_on_the_wire(self):
        task = self.add(tags=["a"])
        self.assertIsInstance(task["tags"], list)
        self.assertIsInstance(task["scope"], list)

    def test_title_defaults_to_the_objective(self):
        self.assertEqual(self.add()["title"], "Fix search")

    def test_priority_is_validated(self):
        for bad in (0, 4, "2", None):
            with self.assertRaises(ValueError):
                self.add(task_id=f"M-{bad}", priority=bad)

    def test_the_working_station_is_called_in_progress(self):
        self.add()
        started = self.ledger.start("M-1", "terminal-1")
        self.assertEqual(self.ledger.task("M-1")["status"], "in_progress")
        self.assertEqual(started["task_id"], "M-1")

    def test_a_duplicate_task_id_is_refused(self):
        self.add()
        with self.assertRaises(ValueError):
            self.add()

    def test_seeding_is_idempotent(self):
        first = self.add(seed_key="search-v1")
        second = self.add(seed_key="search-v1", objective="Different wording")
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["objective"], "Fix search")
        self.assertEqual(len(self.ledger.status()["tasks"]), 1)

    def test_a_seed_key_cannot_be_reused_by_another_ticket(self):
        self.add(seed_key="search-v1")
        with self.assertRaises(ValueError):
            self.add(task_id="M-2", seed_key="search-v1")

    def test_updated_at_moves_when_the_ticket_moves(self):
        task = self.add()
        self.assertIsNotNone(task["updated_at"])
        before = task["updated_at"]
        started = self.ledger.start("M-1", "terminal-1")
        self.assertGreaterEqual(self.ledger.task_view("M-1")["updated_at"], before)
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        self.assertGreaterEqual(self.ledger.task_view("M-1")["updated_at"], before)


if __name__ == "__main__":
    unittest.main()
