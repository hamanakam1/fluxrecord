"""Issue polling does not duplicate pull requests as external feedback."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord.core import Ledger
from fluxrecord.github_sync import GitHubSync
from fluxrecord.intake import intake


def issue_record(number, **extra):
    return {"number": number, "title": f"Synthetic item {number}", "state": "open", **extra}


class IssuePollingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runner = mock.Mock()
        self.sync = GitHubSync(self.root / "sync.sqlite", runner=self.runner)
        self.addCleanup(self.sync.close)
        self.ledger = Ledger(self.root / "ledger.sqlite")
        self.addCleanup(self.ledger.close)

    def test_default_poll_creates_one_issue_task_and_one_pull_review(self):
        def run(args):
            if "/issues?" in args[1]:
                return [
                    issue_record(1),
                    issue_record(2, pull_request={"url": "https://example.test/pr/2"}),
                ]
            if "/pulls?" in args[1]:
                return [issue_record(2)]
            return []

        self.runner.side_effect = run
        result = self.sync.poll("owner/repo")
        self.assertEqual(result["observed"], 2)
        self.assertEqual(result["by_kind"], {"issues": 1, "pulls": 1})
        taken = intake(self.ledger, self.sync, "owner/repo")
        self.assertEqual(taken["created_count"], 2)
        self.assertEqual(
            {(item["key"], item["action"]) for item in taken["created"]},
            {
                ("gh:owner/repo#issues#1", "review_external_feedback"),
                ("gh:owner/repo#pulls#2", "review_code_change"),
            },
        )

    def test_issues_only_excludes_every_present_pull_request_discriminator(self):
        self.runner.return_value = [
            issue_record(index, pull_request=value)
            for index, value in enumerate(({}, None, False, ""), 1)
        ]
        result = self.sync.poll("owner/repo", kinds=["issues"])
        self.assertEqual(result["observed"], 0)
        self.assertEqual(result["by_kind"], {"issues": 0})
        self.assertEqual(self.sync.observations("owner/repo"), [])
        self.assertEqual(intake(self.ledger, self.sync, "owner/repo")["created_count"], 0)
        self.runner.assert_called_once()
        self.assertIn("/issues?", self.runner.call_args.args[0][1])

    def test_a_full_raw_page_of_pulls_does_not_hide_later_issues(self):
        self.runner.side_effect = [
            [issue_record(index, pull_request={}) for index in range(100)],
            [issue_record(101)],
        ]
        result = self.sync.poll("owner/repo", kinds=["issues"])
        self.assertEqual(result["observed"], 1)
        self.assertEqual(result["by_kind"], {"issues": 1})
        self.assertEqual(self.runner.call_count, 2)
        self.assertIn("page=2", self.runner.call_args.args[0][1])
        self.assertEqual(self.sync.observations("owner/repo")[0]["identity"], "101")

    def test_a_mixed_full_page_continues_using_raw_page_length(self):
        self.runner.side_effect = [
            [issue_record(1), *[issue_record(index, pull_request={}) for index in range(2, 101)]],
            [issue_record(101)],
        ]
        result = self.sync.poll("owner/repo", kinds=["issues"])
        self.assertEqual(result["observed"], 2)
        self.assertEqual(result["by_kind"], {"issues": 2})
        self.assertEqual(self.runner.call_count, 2)

    def test_pulls_only_still_observes_its_records(self):
        self.runner.return_value = [issue_record(2)]
        result = self.sync.poll("owner/repo", kinds=["pulls"])
        self.assertEqual(result["observed"], 1)
        self.assertEqual(result["by_kind"], {"pulls": 1})
        self.assertEqual(self.sync.observations("owner/repo")[0]["action"], "review_code_change")
        self.runner.assert_called_once()

    def test_issue_and_pull_pages_include_closed_items_in_recent_update_order(self):
        for kind in ("issues", "pulls"):
            with self.subTest(kind=kind):
                self.runner.reset_mock()
                self.runner.side_effect = [
                    [issue_record(index) for index in range(1, 101)],
                    [issue_record(101, state="closed")],
                ]
                result = self.sync.poll("owner/repo", kinds=[kind])
                self.assertEqual(result["observed"], 101)
                base = f"repos/owner/repo/{kind}?per_page=100&state=all&sort=updated&direction=desc"
                self.assertEqual(
                    self.runner.call_args_list,
                    [mock.call(["api", base]), mock.call(["api", base + "&page=2"])],
                )

    def test_a_closed_revision_is_visible_to_a_subsequent_poll(self):
        for kind in ("issues", "pulls"):
            with self.subTest(kind=kind):
                self.runner.side_effect = None
                self.runner.return_value = [issue_record(7)]
                self.sync.poll("owner/repo", kinds=[kind])
                self.runner.side_effect = lambda args: (
                    [issue_record(7, state="closed")] if "state=all" in args[1] else []
                )
                result = self.sync.poll("owner/repo", kinds=[kind])
                self.assertEqual(result["changed"], 1)
                self.assertEqual(
                    self.sync.observations("owner/repo", kind=kind)[0]["status"], "closed"
                )
