"""Intake: GitHub observations become tickets."""

import tempfile
import unittest
from pathlib import Path

from fluxrecord.core import Ledger
from fluxrecord.github_sync import GitHubSync
from fluxrecord.intake import ACTIONS, actionable, intake


class ActionableTest(unittest.TestCase):
    def test_a_failed_run_is_actionable(self):
        self.assertTrue(actionable("runs", "failure", "investigate_failed_check"))
        self.assertTrue(actionable("runs", "timed_out", "investigate_failed_check"))

    def test_a_successful_run_is_not(self):
        self.assertFalse(actionable("runs", "success", "investigate_failed_check"))

    def test_an_open_pull_request_is_actionable(self):
        self.assertTrue(actionable("pulls", "open", "review_code_change"))
        self.assertFalse(actionable("pulls", "merged", "review_code_change"))
        self.assertFalse(actionable("pulls", "closed", "review_code_change"))

    def test_an_open_issue_is_actionable(self):
        self.assertTrue(actionable("issues", "open", "review_external_feedback"))
        self.assertFalse(actionable("issues", "closed", "review_external_feedback"))

    def test_a_comment_is_always_actionable(self):
        self.assertTrue(actionable("comments", "", "review_external_feedback"))

    def test_activity_implies_nothing(self):
        self.assertFalse(actionable("commits", "", "inspect_repository_activity"))

    def test_every_action_has_a_phrasing(self):
        for action in (
            "investigate_failed_check",
            "review_code_change",
            "review_external_feedback",
            "reconcile_ticket_change",
            "verify_release_artifacts",
            "inspect_repository_activity",
        ):
            self.assertIn(action, ACTIONS)
            self.assertIn("{summary}", ACTIONS[action]["objective"])


class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.sync = GitHubSync(self.root / "github.sqlite")

    def tearDown(self):
        self.sync.close()
        self.ledger.close()
        self.temp.cleanup()

    def observe(self, kind, record, repo="owner/repo"):
        self.sync._observe(repo, kind, record)

    def test_an_open_issue_becomes_a_ticket(self):
        self.observe(
            "issues",
            {
                "number": 12,
                "title": "Login is broken",
                "body": "It fails",
                "state": "open",
                "html_url": "https://example.test/issues/12",
            },
        )
        result = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(result["created_count"], 1)
        task_id = result["created"][0]["ticket"]
        task = self.ledger.task_view(task_id)
        self.assertEqual(task["title"], "Login is broken")
        self.assertIn("Review the external feedback", task["objective"])
        self.assertIn("github", task["tags"])
        self.assertEqual(task["scope"], ["gh:owner/repo#12"])
        self.assertEqual(task["seed_key"], "gh:owner/repo#issues#12")

    def test_the_observation_is_attached_as_context(self):
        self.observe(
            "issues",
            {"number": 12, "title": "Broken", "body": "", "state": "open"},
        )
        result = intake(self.ledger, self.sync, "owner/repo")
        packet = self.ledger.pack(result["created"][0]["ticket"])
        self.assertEqual(len(packet["packet"]["context"]), 1)
        self.assertIn("untrusted", packet["packet"]["context"][0]["body"].lower())

    def test_intake_is_idempotent(self):
        self.observe(
            "issues",
            {"number": 12, "title": "Broken", "body": "", "state": "open"},
        )
        first = intake(self.ledger, self.sync, "owner/repo")
        second = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(first["created_count"], 1)
        self.assertEqual(second["created_count"], 0)
        self.assertEqual(len(self.ledger.status()["tasks"]), 1)

    def test_a_closed_issue_implies_nothing(self):
        self.observe(
            "issues",
            {"number": 12, "title": "Broken", "body": "", "state": "closed"},
        )
        result = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["skipped_count"], 1)

    def test_a_successful_run_implies_nothing(self):
        self.observe(
            "runs",
            {"id": 5, "name": "build", "conclusion": "success", "status": "completed"},
        )
        result = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(result["created_count"], 0)

    def test_a_failed_run_becomes_a_ticket(self):
        self.observe(
            "runs",
            {"id": 5, "name": "build", "conclusion": "failure", "status": "completed"},
        )
        result = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(result["created_count"], 1)
        task = self.ledger.task_view(result["created"][0]["ticket"])
        self.assertIn("Investigate the failed check", task["objective"])
        self.assertEqual(task["priority"], 1)

    def test_an_observation_naming_a_ticket_lands_on_it(self):
        self.ledger.add_task("M-397001", "Existing", "Acceptance", scope=["repo/x"])
        self.observe(
            "comments",
            {"id": 99, "body": "re M-397001 please look", "html_url": "https://x.test/c/99"},
        )
        result = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(result["attached_count"], 1)
        self.assertEqual(result["created_count"], 0)
        packet = self.ledger.pack("M-397001")
        self.assertEqual(len(packet["packet"]["context"]), 1)

    def test_an_observation_naming_an_unknown_ticket_creates_one(self):
        self.observe(
            "comments",
            {"id": 99, "body": "re M-397001 please look", "html_url": "https://x.test/c/99"},
        )
        result = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(len(self.ledger.status()["tasks"]), 1)

    def test_informational_observations_can_be_included(self):
        self.observe(
            "commits",
            {"sha": "abcdef123456", "message": "a commit", "html_url": "https://x.test/c/1"},
        )
        result = intake(self.ledger, self.sync, "owner/repo", include_informational=True)
        self.assertEqual(result["created_count"], 1)

    def test_the_ticket_ids_are_sequential(self):
        self.observe(
            "issues",
            {"number": 1, "title": "One", "body": "", "state": "open"},
        )
        self.observe(
            "issues",
            {"number": 2, "title": "Two", "body": "", "state": "open"},
        )
        intake(self.ledger, self.sync, "owner/repo")
        ids = sorted(task["id"] for task in self.ledger.status()["tasks"])
        self.assertEqual(ids, ["M-000001", "M-000002"])

    def test_a_failed_ticket_blocks_the_ledger_from_lying(self):
        self.observe(
            "issues",
            {"number": 12, "title": "Broken", "body": "", "state": "open"},
        )
        intake(self.ledger, self.sync, "owner/repo")
        ticket = self.ledger.status()["tasks"][0]
        self.assertEqual(ticket["status"], "ready")
        self.assertIn("gh:owner/repo#12", ticket["scope"])


if __name__ == "__main__":
    unittest.main()
