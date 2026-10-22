"""The CLI itself: every command, and what it says when it fails.

The CLI is the product boundary, so it gets its own tests: the commands
run as a real subprocess, with a fake gh on PATH so the GitHub commands
can be exercised without a network.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class CliTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = self.root / "ledger.sqlite"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.gh = self.bin / "gh"
        # The fake gh answers the issues endpoint and nothing else.
        script = r"""#!/bin/sh
if echo "$2" | grep -qE '/issues\?'; then
  echo '[{"number": 7, "title": "Fix it"}]'
else
  echo '[]'
fi
"""
        self.gh.write_text(script)
        self.gh.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def flux(self, *args, expect=0, env=None):
        environment = dict(os.environ)
        environment["PATH"] = f"{self.bin}{os.pathsep}{environment.get('PATH', '')}"
        environment["FLUX_DB"] = str(self.db)
        if env:
            environment.update(env)
        result = subprocess.run(
            [sys.executable, "-m", "fluxrecord", *args],
            capture_output=True,
            text=True,
            env=environment,
        )
        self.assertEqual(result.returncode, expect, result.stderr)
        return result

    def json_out(self, *args, expect=0, env=None):
        result = self.flux(*args, expect=expect, env=env)
        return json.loads(result.stdout)

    def json_err(self, *args):
        result = self.flux(*args, expect=2)
        return json.loads(result.stderr)

    # ---------------------------------------------------------------- basics

    def test_version(self):
        self.assertEqual(self.json_out("version")["name"], "fluxrecord")

    def test_art_prints_the_wordmark(self):
        result = self.flux("art", "--style", "mini")
        self.assertIn("fluxrecord", result.stdout)

    def test_status_of_an_empty_ledger(self):
        status = self.json_out("status")
        self.assertEqual(status["tasks"], [])
        self.assertEqual(status["metrics"]["accepted"], 0)

    def test_an_unknown_command_is_refused(self):
        error = self.json_err("nonsense")
        self.assertIn("error", error)

    def test_a_missing_required_argument_is_refused(self):
        result = self.flux("task-add", "M-1", expect=2)
        self.assertIn("required", result.stderr.lower())

    def test_a_usage_error_is_json(self):
        error = self.json_err("task-add", "M-1")
        self.assertEqual(error["type"], "UsageError")
        self.assertIn("required", error["error"])

    # --------------------------------------------------------------- the loop

    def test_the_whole_ticket_loop(self):
        added = self.json_out(
            "task-add",
            "M-1",
            "--objective",
            "Objective",
            "--acceptance",
            "Acceptance",
            "--scope",
            "repo/x",
            "--budget",
            "500",
        )
        self.assertEqual(added["status"], "ready")
        self.assertEqual(added["token_budget"], 500)

        started = self.json_out("start", "M-1", "--owner", "terminal-1")
        session = started["session_id"]

        self.assertEqual(self.json_out("renew", session, "--lease", "600")["session_id"], session)
        checkpointed = self.json_out(
            "checkpoint",
            session,
            "--summary",
            "s",
            "--evidence",
            "e",
            "--next",
            "n",
            "--tokens",
            "10",
        )
        self.assertEqual(checkpointed["tokens"], 10)
        self.assertEqual(self.json_out("finish", session, "--evidence", "e")["status"], "review")
        self.assertEqual(
            self.json_out("decide", "M-1", "accept", "--reason", "reviewed")["status"], "accepted"
        )

    def test_show_reports_one_ticket(self):
        self.json_out(
            "task-add", "M-1", "--objective", "O", "--acceptance", "A", "--scope", "repo/x"
        )
        view = self.json_out("show", "M-1")
        self.assertEqual(view["ticket"]["id"], "M-1")

    def test_show_refuses_an_unknown_ticket(self):
        error = self.json_err("show", "M-999")
        self.assertIn("unknown task", error["error"])

    def test_list_filters(self):
        self.json_out(
            "task-add",
            "M-1",
            "--objective",
            "O",
            "--acceptance",
            "A",
            "--scope",
            "repo/x",
            "--priority",
            "1",
            "--tag",
            "urgent",
        )
        self.assertEqual(self.json_out("list")["count"], 1)
        self.assertEqual(self.json_out("list", "--priority", "1")["count"], 1)
        self.assertEqual(self.json_out("list", "--priority", "3")["count"], 0)
        self.assertEqual(self.json_out("list", "--tag", "urgent")["count"], 1)
        self.assertEqual(self.json_out("list", "--tag", "absent")["count"], 0)

    def test_pack_and_context(self):
        self.json_out(
            "task-add", "M-1", "--objective", "O", "--acceptance", "A", "--scope", "repo/x"
        )
        note = self.root / "note.md"
        note.write_text("some context")
        self.json_out("context-add", "M-1", "--file", str(note), "--source", "note")
        packet = self.json_out("pack", "M-1")
        self.assertEqual(len(packet["packet"]["context"]), 1)

    def test_profiles_and_routing(self):
        self.json_out(
            "task-add", "M-1", "--objective", "O", "--acceptance", "A", "--scope", "repo/x"
        )
        self.json_out(
            "profile-add",
            "local-coder",
            "--kind",
            "code",
            "--context-limit",
            "16000",
            "--cost",
            "zero_incremental",
        )
        route = self.json_out("route", "M-1")
        self.assertFalse(route["execution_authorized"])
        self.assertEqual(self.json_out("status")["profiles"][0]["id"], "local-coder")

    def test_events_are_recorded(self):
        self.json_out(
            "task-add", "M-1", "--objective", "O", "--acceptance", "A", "--scope", "repo/x"
        )
        events = self.json_out("events")
        self.assertTrue(any(event["type"] == "task.created" for event in events))

    def test_reconcile_reports_nothing_to_do(self):
        self.assertEqual(self.json_out("reconcile")["count"], 0)

    def test_a_failure_needs_a_known_category(self):
        self.json_out(
            "task-add", "M-1", "--objective", "O", "--acceptance", "A", "--scope", "repo/x"
        )
        started = self.json_out("start", "M-1", "--owner", "terminal-1")
        error = self.json_err(
            "fail", started["session_id"], "--category", "whatever", "--summary", "s"
        )
        self.assertIn("category", error["error"])

    # -------------------------------------------------------------- the files

    def test_init_and_export(self):
        project = self.root / "project"
        self.json_out("init", "--project", str(project))
        self.assertTrue((project / "tickets").is_dir())
        self.json_out(
            "task-add", "M-1", "--objective", "O", "--acceptance", "A", "--scope", "repo/x"
        )
        exported = self.json_out("export", "--project", str(project))
        self.assertEqual(exported["tasks"], 1)
        self.assertTrue((project / "tickets" / "M-1.md").is_file())

    def test_import_a_csv(self):
        csv = self.root / "tickets.csv"
        csv.write_text(
            "ID,Title,Description,AcceptanceCriteria,File\n"
            "M-1,Title,Objective,Acceptance,fluxrecord/core.py\n"
        )
        result = self.json_out("import", "--file", str(csv))
        self.assertEqual(result["imported_count"], 1)
        again = self.json_out("import", "--file", str(csv))
        self.assertEqual(again["imported_count"], 0)

    def test_import_a_missing_csv_is_refused(self):
        error = self.json_err("import", "--file", str(self.root / "missing.csv"))
        self.assertIn("error", error)

    # ------------------------------------------------------------- the adapters

    def test_tools(self):
        tools = self.json_out("tools", "--limit", "5")
        self.assertLessEqual(len(tools["tools"]), 5)

    def test_dashboard_preview_refuses_a_remote_origin(self):
        error = self.json_err(
            "dashboard-preview", "--url", "http://example.com", "--status", "planned"
        )
        self.assertIn("local", error["error"])

    def test_dashboard_preview_refuses_a_bad_status(self):
        error = self.json_err(
            "dashboard-preview", "--url", "http://127.0.0.1:8080", "--status", "planned&x=1"
        )
        self.assertIn("status", error["error"])

    def test_a_dashboard_status_cannot_smuggle_a_query_parameter(self):
        from fluxrecord.dashboard import _target

        with self.assertRaises(ValueError):
            _target("http://127.0.0.1:8080", "planned&offset=999", 0)

    def test_github_poll_against_a_fake_gh(self):
        result = self.json_out("github-poll", "--repo", "owner/repo")
        self.assertEqual(result["observed"], 1)
        report = self.json_out("github-status", "--repo", "owner/repo")
        self.assertEqual(report["observations"]["issues"], 1)

    def test_github_publish_and_reconcile(self):
        published = self.json_out(
            "github-publish",
            "--repo",
            "owner/repo",
            "--issue",
            "7",
            "--key",
            "summary",
            "--body",
            "the body",
        )
        self.assertEqual(published["queued"]["key"], "summary")
        self.assertEqual(published["published"]["sent"], ["summary"])

    def test_github_cycle_needs_a_config(self):
        error = self.json_err("github-cycle", "--config", str(self.root / "missing.json"))
        self.assertIn("config", error["error"])

    def test_github_cycle_runs_a_config(self):
        config = self.root / "cycle.json"
        config.write_text(
            json.dumps(
                {
                    "repos": ["owner/repo"],
                    "kinds": ["issues"],
                    "sync_db": str(self.root / "github.sqlite"),
                }
            )
        )
        result = self.json_out("github-cycle", "--config", str(config))
        self.assertEqual(result["observed"], 1)
        self.assertFalse(result["publish_requested"])


if __name__ == "__main__":
    unittest.main()
