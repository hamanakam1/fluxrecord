"""Diagnostics observe stored state without repairing or advancing it."""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord.core import Ledger
from fluxrecord.doctor import _github_report, _ledger_report, diagnose
from fluxrecord.github_sync import GitHubSync


class DoctorReadOnlyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "ledger ?# fixture.sqlite"
        self.key = "synthetic-doctor-chain-key"
        self.env = mock.patch.dict(
            os.environ, {"FLUX_EVENT_KEY": self.key, "FLUX_EVENT_KEY_FILE": ""}
        )
        self.env.start()
        self.addCleanup(self.env.stop)

    def dump(self, path):
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            return list(connection.iterdump())
        finally:
            connection.close()

    def test_expired_lease_is_reported_without_reconciliation(self):
        with Ledger(self.path, chain_key=self.key) as ledger:
            ledger.add_task("M-1", "Synthetic task", "Verified result")
            with mock.patch("fluxrecord.core.time.time", return_value=100.0):
                ledger.start("M-1", "worker", lease_seconds=10)
        before = self.dump(self.path)
        report = _ledger_report(self.path)
        self.assertEqual(report["tasks"], {"in_progress": 1})
        self.assertEqual(report["sessions"], {"active": 1})
        self.assertEqual(report["failure_count"], 0)
        self.assertTrue(report["chain"]["ok"])
        self.assertEqual(self.dump(self.path), before)

    def test_diagnostic_preserves_database_bytes_and_permissions(self):
        with Ledger(self.path, chain_key=self.key) as ledger:
            ledger.add_task("M-1", "Synthetic task", "Verified result")
        self.path.chmod(0o640)
        before = self.path.read_bytes(), self.path.stat().st_mode
        self.assertTrue(_ledger_report(self.path)["readable"])
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mode), before)

    def test_empty_existing_file_is_not_initialized(self):
        self.path.touch(mode=0o640)
        before = self.path.read_bytes(), self.path.stat().st_mode
        report = diagnose(self.path, self.root / "absent-project")
        self.assertTrue(report["ledger"]["exists"])
        self.assertFalse(report["ledger"]["readable"])
        self.assertIn("error", report["ledger"])
        self.assertFalse(any("flux init" in note and "ledger" in note for note in report["notes"]))
        self.assertEqual((self.path.read_bytes(), self.path.stat().st_mode), before)

    def test_legacy_schema_is_reported_without_migration(self):
        connection = sqlite3.connect(self.path)
        connection.executescript(
            "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "INSERT INTO meta VALUES('schema_version', '1');"
            "CREATE TABLE tasks(id TEXT PRIMARY KEY, status TEXT NOT NULL);"
            "INSERT INTO tasks VALUES('M-1', 'running');"
        )
        connection.close()
        before = self.dump(self.path)
        report = _ledger_report(self.path)
        self.assertEqual(report.get("schema"), 1)
        self.assertFalse(report["readable"])
        self.assertIn("error", report)
        self.assertEqual(self.dump(self.path), before)

    def test_github_diagnostic_does_not_migrate_or_backfill(self):
        path = self.root / "sync ?# fixture.sqlite"
        connection = sqlite3.connect(path)
        connection.executescript(
            "CREATE TABLE observations(repo TEXT NOT NULL);"
            "INSERT INTO observations VALUES('owner/repo');"
            "CREATE TABLE outbox(state TEXT NOT NULL);"
            "INSERT INTO outbox VALUES('pending');"
        )
        connection.close()
        path.chmod(0o640)
        before = self.dump(path), path.read_bytes(), path.stat().st_mode
        report = _github_report(path)
        self.assertEqual(report.get("observation_count"), 1)
        self.assertEqual(report.get("outbox"), {"pending": 1})
        self.assertNotIn("error", report)
        self.assertEqual((self.dump(path), path.read_bytes(), path.stat().st_mode), before)

    def test_wal_commits_are_visible_without_checkpointing_writer(self):
        with Ledger(self.path, chain_key=self.key) as writer:
            writer.add_task("M-1", "Synthetic task", "Verified result")
            self.assertTrue(Path(str(self.path) + "-wal").is_file())
            before = writer.db.total_changes
            report = _ledger_report(self.path)
            self.assertEqual(report["task_count"], 1)
            self.assertTrue(report["chain"]["ok"])
            self.assertEqual(writer.db.total_changes, before)

    def test_summary_and_chain_share_one_read_snapshot(self):
        with Ledger(self.path, chain_key=self.key) as writer:
            writer.add_task("M-1", "Synthetic task", "Verified result")
            original_status = Ledger.status

            def status_then_concurrent_tamper(reader):
                status = original_status(reader)
                writer.db.execute("UPDATE events SET data=?", ('{"tampered": true}',))
                return status

            with mock.patch.object(Ledger, "status", status_then_concurrent_tamper):
                report = _ledger_report(self.path)
            self.assertTrue(report["chain"]["ok"])
            self.assertFalse(writer.verify_chain()["ok"])

    def test_github_counts_share_one_read_snapshot(self):
        path = self.root / "github.sqlite"
        writer = GitHubSync(path)
        self.addCleanup(writer.close)
        writer._observe("owner/repo", "issues", {"number": 1, "title": "First", "state": "open"})
        connect = sqlite3.connect

        def connect_with_concurrent_writer(*args, **kwargs):
            reader = connect(*args, **kwargs)

            def change_between_counts(statement):
                if statement.startswith("SELECT state, COUNT(*)"):
                    writer._observe(
                        "owner/repo", "issues", {"number": 2, "title": "Second", "state": "open"}
                    )
                    writer.enqueue("owner/repo", 2, "synthetic-key", "Synthetic body")

            reader.set_trace_callback(change_between_counts)
            return reader

        with mock.patch("fluxrecord.doctor.sqlite3.connect", connect_with_concurrent_writer):
            report = _github_report(path)
        self.assertEqual(report["observation_count"], 1)
        self.assertEqual(report["outbox"], {})
        self.assertEqual(writer.db.execute("SELECT COUNT(*) FROM observations").fetchone()[0], 2)
        self.assertEqual(writer.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0], 1)

    def test_readonly_open_does_not_create_parent_or_database(self):
        missing = self.root / "missing-parent" / "ledger.sqlite"
        with self.assertRaises(sqlite3.OperationalError):
            Ledger(missing, read_only=True)
        self.assertFalse(missing.parent.exists())

    def test_readonly_ledger_rejects_mutations(self):
        with Ledger(self.path, chain_key=self.key) as writer:
            writer.add_task("M-1", "Synthetic task", "Verified result")
        before = self.dump(self.path)
        with Ledger(self.path, read_only=True) as reader:
            self.assertEqual(len(reader.status()["tasks"]), 1)
            with self.assertRaises(sqlite3.OperationalError):
                reader.add_task("M-2", "Other task", "Other result")
        self.assertEqual(self.dump(self.path), before)


if __name__ == "__main__":
    unittest.main()
