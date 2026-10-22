"""The doctor: one report about the ledger, the project and the adapters."""

import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from fluxrecord.core import Ledger
from fluxrecord.doctor import diagnose
from fluxrecord.github_sync import GitHubSync


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        body = b'{"tickets": [{"id": "M-1"}]}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        return


class DoctorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"
        self.project = self.root / "project"
        self.sync_db = self.root / "github.sqlite"

    def tearDown(self):
        self.temp.cleanup()

    def test_a_fresh_ledger_is_reported(self):
        with Ledger(self.db):
            pass
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertTrue(report["ledger"]["readable"])
        self.assertEqual(report["ledger"]["task_count"], 0)
        self.assertIn("schema", report["ledger"])

    def test_the_doctor_does_not_create_a_ledger(self):
        """A diagnostic must not create the thing it diagnoses."""
        missing = self.root / "never-created.sqlite"
        report = diagnose(missing, self.project, self.sync_db)
        self.assertFalse(report["ledger"]["readable"])
        self.assertFalse(report["ledger"]["exists"])
        self.assertFalse(missing.exists())

    def test_tasks_are_counted_by_status(self):
        ledger = Ledger(self.db)
        ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        ledger.close()
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertEqual(report["ledger"]["tasks"], {"ready": 1})

    def test_a_missing_project_is_reported(self):
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertFalse(report["project"]["exists"])
        self.assertTrue(any("project" in note for note in report["notes"]))

    def test_a_project_with_tickets_is_reported(self):
        ledger = Ledger(self.db)
        ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        ledger.close()
        from fluxrecord.files import export

        export(Ledger(self.db), self.project)
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertTrue(report["project"]["exists"])
        self.assertEqual(report["project"]["ticket_files"], 1)
        self.assertEqual(report["project"]["index_entries"], 1)

    def test_a_stale_project_is_noted(self):
        from fluxrecord.files import export

        ledger = Ledger(self.db)
        ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        ledger.close()
        export(Ledger(self.db), self.project)
        Ledger(self.db).add_task("M-2", "Objective", "Acceptance", scope=["repo/y"])
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertTrue(any("flux export" in note for note in report["notes"]))

    def test_observations_are_reported_and_suggested(self):
        sync = GitHubSync(self.sync_db)
        sync._observe(
            "owner/repo",
            "issues",
            {"number": 1, "title": "One", "body": "", "state": "open"},
        )
        sync.close()
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertEqual(report["github"]["observation_count"], 1)
        self.assertTrue(any("github-intake" in note for note in report["notes"]))

    def test_the_tools_report_names_what_is_installed(self):
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertIn("git", report["tools"]["present"])
        self.assertIn("count", report["tools"])

    def test_blocked_tasks_are_noted(self):
        ledger = Ledger(self.db)
        ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])
        started = ledger.start("M-1", "terminal-1")
        ledger.fail(started["session_id"], "tool", "broken")
        ledger.close()
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertTrue(any("blocked" in note for note in report["notes"]))

    def test_the_chain_state_is_reported(self):
        with Ledger(self.db):
            pass
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertFalse(report["ledger"]["chain"]["signed"])
        self.assertTrue(any("not signed" in note for note in report["notes"]))

    def test_a_signed_ledger_is_reported_as_signed(self):
        with Ledger(self.db):
            pass
        with mock.patch.dict(os.environ, {"FLUX_EVENT_KEY": "a-chain-key"}):
            report = diagnose(self.db, self.project, self.sync_db)
        self.assertTrue(report["ledger"]["chain"]["signed"])
        self.assertTrue(report["ledger"]["chain"]["ok"])

    def test_the_environment_is_reported(self):
        report = diagnose(self.db, self.project, self.sync_db)
        self.assertIn("TERM", report["environment"])
        self.assertIn("FLUX_DB", report["environment"])

    def test_a_dashboard_can_be_probed(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            report = diagnose(self.db, self.project, self.sync_db, origin)
            self.assertTrue(report["dashboard"]["probed"])
            self.assertTrue(report["dashboard"]["reachable"])
            self.assertEqual(report["dashboard"]["tickets"], 1)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def test_an_unreachable_dashboard_is_reported(self):
        report = diagnose(self.db, self.project, self.sync_db, "http://127.0.0.1:1")
        self.assertTrue(report["dashboard"]["probed"])
        self.assertFalse(report["dashboard"]["reachable"])

    def test_a_dashboard_that_is_not_local_is_refused(self):
        report = diagnose(self.db, self.project, self.sync_db, "http://example.com")
        self.assertFalse(report["dashboard"]["reachable"])
        self.assertIn("error", report["dashboard"])

    def test_the_report_is_json_serialisable(self):
        report = diagnose(self.db, self.project, self.sync_db)
        json.dumps(report)


if __name__ == "__main__":
    unittest.main()
