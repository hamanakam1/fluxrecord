"""The error paths: what a user reaches by getting something wrong."""

import json
import tempfile
import unittest
from pathlib import Path

from fluxrecord import benchmark, wire
from fluxrecord.core import Ledger
from fluxrecord.doctor import diagnose
from fluxrecord.github_cycle import cycle
from fluxrecord.importer import import_csv
from fluxrecord.intake import actionable
from fluxrecord.watch import format_event, watch, watch_json


class CycleEdgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "cycle.json"

    def tearDown(self):
        self.temp.cleanup()

    def write(self, payload):
        self.config.write_text(json.dumps(payload))
        return str(self.config)

    def test_a_cycle_can_ticket_what_it_observed(self):
        path = self.write(
            {
                "repos": ["owner/repo"],
                "kinds": ["issues"],
                "intake": True,
                "ledger_db": str(self.root / "ledger.sqlite"),
                "sync_db": str(self.root / "github.sqlite"),
            }
        )
        gh = lambda args: [  # noqa: E731 - a one-line fake
            {"number": 7, "title": "Broken", "body": "", "state": "open"}
        ]
        result = cycle(path, runner=gh)
        self.assertTrue(result["intake_requested"])
        self.assertEqual(result["ticketed"], 1)
        with Ledger(self.root / "ledger.sqlite") as book:
            self.assertEqual(len(book.status()["tasks"]), 1)

    def test_a_cycle_survives_a_repository_that_fails(self):
        path = self.write(
            {
                "repos": ["not-a-repo", "owner/repo"],
                "kinds": ["issues"],
                "sync_db": str(self.root / "github.sqlite"),
            }
        )
        gh = lambda args: []  # noqa: E731
        result = cycle(path, runner=gh)
        self.assertEqual(len(result["repos"]), 2)
        self.assertIn("error", result["repos"][0])


class DoctorEdgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_an_unreadable_ledger_is_reported(self):
        unreadable = self.root / "a-directory"
        unreadable.mkdir()
        report = diagnose(unreadable, self.root, self.root / "g.sqlite")
        self.assertFalse(report["ledger"]["readable"])
        self.assertTrue(report["notes"])

    def test_a_project_with_a_broken_index_is_reported(self):
        project = self.root / "project"
        (project / "tickets").mkdir(parents=True)
        (project / "index.json").write_text("{not json")
        report = diagnose(self.db, project, self.root / "g.sqlite")
        self.assertIn("index_error", report["project"])

    def test_a_ledger_with_profiles_is_reported(self):
        with Ledger(self.db) as book:
            book.profile("local-coder", ["code"], 16000, "zero_incremental")
        report = diagnose(self.db, self.root, self.root / "g.sqlite")
        self.assertEqual(len(report["ledger"]["profiles"]), 1)


class BenchmarkEdgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["a.py"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_file_that_cannot_be_read_is_not_counted(self):
        broken = self.root / "broken.py"
        broken.mkdir()  # a directory is not a file
        self.ledger.add_task("M-2", "Objective", "Acceptance", scope=["broken.py"])
        result = benchmark.measure(self.ledger, "M-2", self.root)
        self.assertEqual(result["reread_bytes"], 0)


class WatchEdgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_the_watch_loop_stops_at_its_limit(self):
        self.ledger.add_task("M-2", "Another", "Acceptance", scope=["repo/y"])
        lines: list[str] = []
        slept: list[float] = []
        cursor = watch(
            self.ledger,
            limit=1,
            emit=lines.append,
            sleep=slept.append,
        )
        self.assertEqual(len(lines), 1)
        self.assertEqual(cursor, 1)
        self.assertEqual(slept, [])

    def test_the_watch_loop_waits_between_polls(self):
        lines: list[str] = []
        slept: list[float] = []

        def stop(interval: float) -> None:
            slept.append(interval)
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            watch(self.ledger, interval=0.25, emit=lines.append, sleep=stop)
        self.assertEqual(slept, [0.25])

    def test_an_event_with_no_detail_still_formats(self):
        event = {"seq": 1, "type": "task.created", "task_id": None, "data": {}, "created": 0}
        line = format_event(event)
        self.assertIn("task.created", line)

    def test_json_watching_stops_at_its_limit(self):
        self.ledger.add_task("M-2", "Another", "Acceptance", scope=["repo/y"])
        lines: list[str] = []
        watch_json(self.ledger, limit=1, once=True, emit=lines.append)
        self.assertEqual(len(lines), 1)
        json.loads(lines[0])


class WireEdgeTest(unittest.TestCase):
    def test_an_unknown_shape_is_a_problem(self):
        self.assertTrue(wire.problems("nonsense", {}))

    def test_a_value_that_is_not_an_object_is_a_problem(self):
        self.assertTrue(wire.problems("ticket", "not a ticket"))

    def test_a_wrong_type_is_a_problem(self):
        found = wire.problems("ticket", {"id": 1})
        self.assertIn("ticket.id is not string", found)


class ImporterEdgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.csv = self.root / "tickets.csv"
        self.ledger = Ledger(self.root / "ledger.sqlite")

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_a_row_with_no_id_is_skipped(self):
        self.csv.write_text("ID,Title,Description,AcceptanceCriteria\n,No id,Objective,Done\n")
        result = import_csv(self.ledger, self.csv)
        self.assertEqual(result["imported_count"], 0)
        self.assertEqual(len(result["skipped"]), 1)

    def test_a_csv_with_no_rows_imports_nothing(self):
        self.csv.write_text("ID,Title,Description,AcceptanceCriteria\n")
        result = import_csv(self.ledger, self.csv)
        self.assertEqual(result["rows"], 0)
        self.assertEqual(result["imported_count"], 0)

    def test_an_unknown_kind_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.add_task("M-1", "O", "A", scope=["s"], kind="nonsense-kind")


class IntakeEdgeTest(unittest.TestCase):
    def test_an_unknown_kind_implies_nothing(self):
        self.assertFalse(actionable("something-new", "open", "review_external_feedback"))

    def test_a_release_that_is_a_draft_implies_nothing(self):
        self.assertFalse(actionable("releases", "draft", "verify_release_artifacts"))

    def test_a_published_release_is_worth_a_ticket(self):
        self.assertTrue(actionable("releases", "published", "verify_release_artifacts"))


if __name__ == "__main__":
    unittest.main()
