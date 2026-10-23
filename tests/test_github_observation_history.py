"""Current observations drive intake while immutable revisions remain available."""

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest import mock

from fluxrecord.core import Ledger
from fluxrecord.github_sync import GitHubSync
from fluxrecord.intake import intake


class ObservationHistoryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.path = self.root / "github.sqlite"
        self.sync = GitHubSync(self.path, runner=lambda args: [])
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.open = {"number": 7, "title": "Synthetic issue", "state": "open"}
        self.closed = {**self.open, "state": "closed"}

    def tearDown(self):
        self.sync.close()
        self.ledger.close()
        self.temp.cleanup()

    def observe(self, record, kind="issues"):
        return self.sync._observe("owner/repo", kind, record)

    def test_an_unsupported_sqlite_version_fails_before_creating_storage(self):
        path = self.root / "unsupported" / "sync.sqlite"
        with mock.patch("fluxrecord.github_sync.sqlite3.sqlite_version_info", (3, 24, 0)):
            with self.assertRaisesRegex(OSError, "SQLite 3.25"):
                GitHubSync(path, runner=lambda args: [])
        self.assertFalse(path.parent.exists())

    def test_only_transitions_from_the_current_revision_count_as_changed(self):
        results = [self.observe(record) for record in (self.open, self.closed, self.closed)]
        self.assertEqual(results, [False, True, False])
        self.assertEqual(len(self.sync.observations("owner/repo")), 2)

    def test_unchanged_polls_do_not_move_an_identity_ahead_of_newer_changes(self):
        with mock.patch("fluxrecord.github_sync.time.time", return_value=10):
            self.observe(self.open)
        with mock.patch("fluxrecord.github_sync.time.time", return_value=20):
            self.observe({**self.open, "number": 8})
        with mock.patch("fluxrecord.github_sync.time.time", return_value=30):
            self.assertFalse(self.observe(self.open))
        current = self.sync.observations("owner/repo", latest=True)
        self.assertEqual([row["identity"] for row in current], ["8", "7"])

    def test_reverting_to_an_old_revision_moves_the_head_without_rewriting_history(self):
        with mock.patch("fluxrecord.github_sync.time.time", return_value=10):
            self.observe(self.open)
        with mock.patch("fluxrecord.github_sync.time.time", return_value=20):
            self.observe(self.closed)
        history = self.sync.observations("owner/repo")
        with mock.patch("fluxrecord.github_sync.time.time", return_value=30):
            self.assertTrue(self.observe(self.open))
        current = self.sync.observations("owner/repo", latest=True)
        self.assertEqual([row["status"] for row in current], ["open"])
        self.assertEqual(current[0]["observed"], 10)
        self.assertEqual(self.sync.observations("owner/repo"), history)
        self.sync.close()
        self.sync = GitHubSync(self.path, runner=lambda args: [])
        self.assertEqual(self.sync.observations("owner/repo", latest=True), current)
        self.assertFalse(self.observe(self.open))

    def test_closed_issues_and_successful_runs_do_not_revive_old_actionable_states(self):
        self.observe(self.open)
        self.observe(self.closed)
        self.observe({"id": 9, "name": "Synthetic check", "conclusion": "failure"}, "runs")
        self.observe({"id": 9, "name": "Synthetic check", "conclusion": "success"}, "runs")
        result = intake(self.ledger, self.sync, "owner/repo", kinds=["issues", "runs"])
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["skipped_count"], 2)
        self.assertEqual(self.ledger.list_tasks(), [])
        self.assertEqual(len(self.sync.observations("owner/repo")), 4)

    def test_current_identity_filtering_happens_before_the_limit(self):
        self.observe({"number": 8, "title": "Another issue", "state": "open"})
        self.observe(self.open)
        self.observe({**self.open, "title": "Updated issue"})
        self.observe(self.closed)
        current = self.sync.observations(
            "owner/repo", kind="issues", action="review_external_feedback", limit=2, latest=True
        )
        self.assertEqual([row["identity"] for row in current], ["7", "8"])
        self.assertEqual([row["status"] for row in current], ["closed", "open"])
        self.assertEqual(self.sync.observations("owner/other", latest=True), [])
        self.assertEqual(self.sync.observations("owner/repo", kind="runs", latest=True), [])

    def test_repolls_and_reversions_do_not_duplicate_named_ticket_context(self):
        self.ledger.add_task("M-397001", "Synthetic task", "Acceptance")
        first = {"id": 12, "body": "M-397001 first comment"}
        second = {"id": 12, "body": "M-397001 edited comment"}
        for index, record in enumerate((first, second, second, first, first), 1):
            with mock.patch("fluxrecord.github_sync.time.time", return_value=index * 100):
                self.observe(record, "comments")
                intake(self.ledger, self.sync, "owner/repo", kinds=["comments"])
        count = self.ledger.db.execute("SELECT COUNT(*) FROM task_context").fetchone()[0]
        self.assertEqual(count, 2)
        self.assertEqual(len(self.sync.observations("owner/repo")), 2)

    def test_failed_head_update_rolls_back_the_revision_and_keeps_the_old_head(self):
        self.observe(self.open)
        self.sync.db.execute(
            "CREATE TRIGGER fail_head BEFORE UPDATE ON observation_heads "
            "BEGIN SELECT RAISE(ABORT, 'synthetic head failure'); END"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic head failure"):
            self.observe(self.closed)
        self.assertFalse(self.sync.db.in_transaction)
        self.assertEqual(len(self.sync.observations("owner/repo")), 1)
        self.assertEqual(self.sync.observations("owner/repo", latest=True)[0]["status"], "open")
        self.assertFalse(self.observe(self.open))

    def test_rejected_revision_cannot_commit_a_dangling_head(self):
        malformed = {**self.open, "user": {"login": None}}
        with self.assertRaises(sqlite3.IntegrityError):
            self.observe(malformed)
        self.assertFalse(self.sync.db.in_transaction)
        self.assertEqual(self.sync.observations("owner/repo"), [])
        self.assertEqual(
            self.sync.db.execute("SELECT COUNT(*) FROM observation_heads").fetchone()[0], 0
        )
        self.assertFalse(self.observe(self.open))
        self.assertEqual(len(self.sync.observations("owner/repo", latest=True)), 1)

    def test_concurrent_connections_report_one_transition_to_the_same_revision(self):
        self.observe(self.open)
        ready = Barrier(2)

        def update():
            with GitHubSync(self.path, runner=lambda args: []) as sync:
                ready.wait(timeout=5)
                return sync._observe("owner/repo", "issues", self.closed)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(update) for _ in range(2)]
            results = [future.result(timeout=10) for future in futures]
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(len(self.sync.observations("owner/repo")), 2)
        self.assertEqual(self.sync.observations("owner/repo", latest=True)[0]["status"], "closed")

    def test_legacy_history_backfills_by_observed_time_then_insertion_order(self):
        legacy = self.root / "legacy.sqlite"
        database = sqlite3.connect(legacy)
        try:
            database.execute(
                "CREATE TABLE observations (repo TEXT,kind TEXT,identity TEXT,revision TEXT,"
                "observed REAL,summary TEXT,action TEXT,url TEXT,ticket TEXT,actor TEXT,status TEXT,"
                "PRIMARY KEY(repo,kind,identity,revision))"
            )
            for revision, observed, status in (
                ("old", 5, "open"),
                ("z", 10, "open"),
                ("a", 10, "closed"),
            ):
                database.execute(
                    "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        "owner/repo",
                        "issues",
                        "7",
                        revision,
                        observed,
                        "Synthetic issue",
                        "review_external_feedback",
                        "",
                        None,
                        "",
                        status,
                    ),
                )
            database.commit()
        finally:
            database.close()
        with GitHubSync(legacy, runner=lambda args: []) as sync:
            self.assertEqual(len(sync.observations("owner/repo")), 3)
            current = sync.observations("owner/repo", latest=True)
            self.assertEqual([row["revision"] for row in current], ["a"])
            self.assertEqual(current[0]["status"], "closed")
        with GitHubSync(legacy, runner=lambda args: []) as sync:
            self.assertEqual(sync.observations("owner/repo", latest=True), current)

    def test_failed_backfill_rolls_back_all_heads_and_can_be_retried(self):
        for number in (7, 8):
            self.sync.db.execute(
                "INSERT INTO observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "owner/repo",
                    "issues",
                    str(number),
                    "legacy",
                    number,
                    "Synthetic issue",
                    "review_external_feedback",
                    "",
                    None,
                    "",
                    "open",
                ),
            )
        self.sync.db.execute(
            "CREATE TRIGGER fail_backfill BEFORE INSERT ON observation_heads "
            "WHEN NEW.identity='8' BEGIN SELECT RAISE(ABORT, 'synthetic backfill failure'); END"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "synthetic backfill failure"):
            GitHubSync(self.path, runner=lambda args: [])
        self.assertEqual(
            self.sync.db.execute("SELECT COUNT(*) FROM observation_heads").fetchone()[0], 0
        )
        self.assertEqual(len(self.sync.observations("owner/repo")), 2)
        self.sync.db.execute("DROP TRIGGER fail_backfill")
        with GitHubSync(self.path, runner=lambda args: []) as migrated:
            self.assertEqual(len(migrated.observations("owner/repo", latest=True)), 2)


if __name__ == "__main__":
    unittest.main()
