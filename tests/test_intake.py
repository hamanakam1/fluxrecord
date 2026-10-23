"""Intake: GitHub observations become tickets."""

import concurrent.futures
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fluxrecord.core import Ledger
from fluxrecord.github_sync import GitHubSync
from fluxrecord.intake import ACTIONS, actionable, intake, intake_dashboard


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

    def test_resolved_observations_do_not_ticket_stale_revisions(self):
        for kind, records in (
            (
                "issues",
                [{"number": 12, "title": "Issue", "state": state} for state in ("open", "closed")],
            ),
            (
                "runs",
                [
                    {"id": 5, "name": "checks", "conclusion": conclusion}
                    for conclusion in ("failure", "success")
                ],
            ),
        ):
            with self.subTest(kind=kind):
                for record in records:
                    self.observe(kind, record)
                result = intake(self.ledger, self.sync, "owner/repo", kinds=[kind])
                self.assertEqual(result["created_count"], 0)
                self.assertEqual(result["skipped_count"], 1)
                self.assertEqual(len(self.sync.observations("owner/repo", kind=kind)), 2)

    def test_reopened_issue_uses_only_the_latest_observed_revision(self):
        for state in ("open", "closed", "open"):
            self.observe("issues", {"number": 12, "title": "Issue", "state": state})
        result = intake(self.ledger, self.sync, "owner/repo", kinds=["issues"])
        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["skipped_count"], 0)

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

    def test_evidence_does_not_land_on_a_finished_ticket(self):
        self.ledger.add_task("M-397001", "Existing", "Acceptance", scope=["repo/x"])
        started = self.ledger.start("M-397001", "terminal-1")
        self.ledger.checkpoint(started["session_id"], "s", "e", "n", 10)
        self.ledger.finish(started["session_id"], "evidence")
        self.ledger.decide("M-397001", "accept", "reviewed")
        self.observe(
            "comments",
            {"id": 99, "body": "re M-397001 please look", "html_url": "https://x.test/c/99"},
        )
        result = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(result["attached_count"], 0)
        self.assertEqual(result["created_count"], 0)
        self.assertIn("closed", result["skipped"][0]["reason"])

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

    def test_concurrent_intakes_retry_generated_id_collisions(self):
        gate = threading.Barrier(2, timeout=5)

        class RacingLedger(Ledger):
            waited = False

            def next_ticket_id(self):
                candidate = super().next_ticket_id()
                if not self.waited:
                    self.waited = True
                    gate.wait()
                return candidate

        def run(identity):
            observation = {
                "kind": "issues",
                "identity": identity,
                "status": "open",
                "action": "review_external_feedback",
                "summary": f"Issue {identity}",
            }
            sync = SimpleNamespace(observations=lambda *args, **kwargs: [observation])
            with RacingLedger(self.root / "ledger.sqlite") as ledger:
                return intake(ledger, sync, "owner/repo", kinds=["issues"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(run, ["11", "12"]))

        self.assertEqual(sum(result["created_count"] for result in results), 2)
        self.assertTrue(all(not result["skipped"] for result in results))
        tasks = self.ledger.status()["tasks"]
        self.assertEqual({task["id"] for task in tasks}, {"M-000001", "M-000002"})
        for task in tasks:
            identity = task["seed_key"].rsplit("#", 1)[1]
            context = self.ledger.pack(task["id"])["packet"]["context"]
            self.assertEqual(len(context), 1)
            self.assertIn(f'"identity": "{identity}"', context[0]["body"])

    def test_intake_rechecks_a_seed_created_between_allocation_and_insert(self):
        self.observe("issues", {"number": 12, "title": "Issue", "state": "open"})
        original_add_task = self.ledger.add_task

        def competing_add(*args, **kwargs):
            original_add_task("M-000009", "Issue", "Acceptance", seed_key=kwargs["seed_key"])
            return original_add_task(*args, **kwargs)

        with patch.object(self.ledger, "add_task", side_effect=competing_add) as add_task:
            result = intake(self.ledger, self.sync, "owner/repo", kinds=["issues"])

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "already seeded")
        self.assertEqual(add_task.call_count, 1)
        self.assertEqual(len(self.ledger.status()["tasks"]), 1)

    def test_generated_id_retries_are_bounded(self):
        self.ledger.add_task("M-000001", "Existing", "Acceptance")
        self.observe("issues", {"number": 12, "title": "Issue", "state": "open"})
        with patch.object(self.ledger, "next_ticket_id", return_value="M-000001") as next_id:
            result = intake(self.ledger, self.sync, "owner/repo", kinds=["issues"])

        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["skipped_count"], 1)
        self.assertGreater(next_id.call_count, 1)
        self.assertLessEqual(next_id.call_count, 8)
        self.assertEqual(len(self.ledger.status()["tasks"]), 1)

    def test_generated_id_retry_does_not_hide_validation_errors(self):
        repo = "owner/" + "r" * 100
        self.observe("issues", {"number": 12, "title": "Issue", "state": "open"}, repo=repo)
        with patch.object(self.ledger, "add_task", wraps=self.ledger.add_task) as add_task:
            result = intake(self.ledger, self.sync, repo, kinds=["issues"])

        self.assertEqual(add_task.call_count, 1)
        self.assertEqual(result["skipped"][0]["reason"], "seed_key must be at most 100 characters")
        self.assertEqual(len(self.ledger.status()["tasks"]), 0)

    def test_dashboard_generated_ids_retry_collisions(self):
        original_add_task = self.ledger.add_task

        def competing_add(task_id, *args, **kwargs):
            if task_id == "M-000001":
                original_add_task(task_id, "Other work", "Acceptance")
            return original_add_task(task_id, *args, **kwargs)

        with patch.object(self.ledger, "add_task", side_effect=competing_add):
            result = intake_dashboard(
                self.ledger,
                {"tickets": [{"id": "external/12", "title": "External task"}]},
                "http://127.0.0.1:8080",
            )

        self.assertEqual(result["created_count"], 1)
        self.assertEqual(result["created"][0]["ticket"], "M-000002")
        self.assertEqual(self.ledger.task_view("M-000001")["objective"], "Other work")

    def test_dashboard_explicit_id_conflicts_are_not_reassigned(self):
        self.ledger.add_task("EXT-12", "Existing", "Acceptance")
        with patch.object(self.ledger, "next_ticket_id") as next_id:
            result = intake_dashboard(
                self.ledger,
                {"tickets": [{"id": "EXT-12", "title": "External task"}]},
                "http://127.0.0.1:8080",
            )

        next_id.assert_not_called()
        self.assertEqual(result["created_count"], 0)
        self.assertEqual(result["skipped"][0]["reason"], "task already exists: EXT-12")
        self.assertEqual(self.ledger.task_view("EXT-12")["objective"], "Existing")

    def test_a_noisy_kind_cannot_starve_the_kind_that_needs_work(self):
        """A hundred successful runs must not hide one failure."""
        for index in range(30):
            self.observe(
                "runs",
                {
                    "id": 1000 + index,
                    "name": f"build-{index}",
                    "conclusion": "success",
                    "status": "completed",
                },
            )
        self.observe(
            "runs",
            {"id": 9999, "name": "the-build", "conclusion": "failure", "status": "completed"},
        )
        result = intake(self.ledger, self.sync, "owner/repo", limit_per_kind=5)
        self.assertEqual(result["created_count"], 1)
        ticket = self.ledger.task_view(result["created"][0]["ticket"])
        self.assertIn("the-build", ticket["objective"])

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
