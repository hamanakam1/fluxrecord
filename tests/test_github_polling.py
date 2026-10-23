"""Polling accepts GitHub's workflow-run envelope without losing failures."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord.core import Ledger
from fluxrecord.github_sync import GitHubSync
from fluxrecord.intake import intake


def workflow_run(identity, conclusion="failure"):
    return {
        "id": identity,
        "name": "synthetic-build",
        "status": "completed",
        "conclusion": conclusion,
        "html_url": f"https://example.test/actions/runs/{identity}",
        "actor": {"login": "synthetic-worker"},
    }


class WorkflowPollingTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runner = mock.Mock()
        self.sync = GitHubSync(self.root / "github.sqlite", runner=self.runner)

    def tearDown(self):
        self.sync.close()
        self.temp.cleanup()

    def test_an_enveloped_failed_run_reaches_intake(self):
        self.runner.return_value = {"total_count": 1, "workflow_runs": [workflow_run(17)]}
        result = self.sync.poll("owner/repo", kinds=["runs"])
        self.assertEqual(result["observed"], 1)
        self.assertEqual(result["by_kind"], {"runs": 1})
        observation = self.sync.observations("owner/repo", kind="runs")[0]
        self.assertEqual(observation["identity"], "17")
        self.assertEqual(observation["status"], "failure")
        self.assertEqual(observation["actor"], "synthetic-worker")
        self.assertEqual(observation["url"], "https://example.test/actions/runs/17")
        with Ledger(self.root / "ledger.sqlite") as ledger:
            taken = intake(ledger, self.sync, "owner/repo", kinds=["runs"])
            self.assertEqual(taken["created_count"], 1)
            ticket = ledger.task_view(taken["created"][0]["ticket"])
            self.assertIn("Investigate the failed check", ticket["objective"])
            self.assertEqual(ticket["seed_key"], "gh:owner/repo#runs#17")

    def test_an_empty_workflow_envelope_finishes_after_one_page(self):
        self.runner.return_value = {"total_count": 0, "workflow_runs": []}
        result = self.sync.poll("owner/repo", kinds=["runs"])
        self.assertEqual(result["observed"], 0)
        self.assertEqual(self.sync.observations("owner/repo"), [])
        self.runner.assert_called_once_with(["api", "repos/owner/repo/actions/runs?per_page=100"])

    def test_workflow_pages_are_counted_from_the_nested_list(self):
        self.runner.side_effect = [
            {"total_count": 101, "workflow_runs": [workflow_run(index) for index in range(1, 101)]},
            {"total_count": 101, "workflow_runs": [workflow_run(101)]},
        ]
        result = self.sync.poll("owner/repo", kinds=["runs"])
        self.assertEqual(result["observed"], 101)
        self.assertEqual(result["by_kind"], {"runs": 101})
        self.assertEqual(len(self.sync.observations("owner/repo", kind="runs")), 101)
        self.assertEqual(
            self.runner.call_args_list,
            [
                mock.call(["api", "repos/owner/repo/actions/runs?per_page=100"]),
                mock.call(["api", "repos/owner/repo/actions/runs?per_page=100&page=2"]),
            ],
        )

    def test_workflow_polling_respects_the_page_cap(self):
        self.runner.return_value = {
            "total_count": 101,
            "workflow_runs": [workflow_run(index) for index in range(1, 101)],
        }
        result = self.sync.poll("owner/repo", kinds=["runs"], max_pages=1)
        self.assertEqual(result["observed"], 100)
        self.assertEqual(result["pages_capped"], 1)
        self.runner.assert_called_once()

    def test_malformed_workflow_envelopes_are_errors(self):
        for payload in (
            None,
            "not a workflow page",
            {},
            {"total_count": 1},
            {"message": "request failed"},
            {"workflow_runs": None},
            {"workflow_runs": {}},
            {"workflow_runs": "not a list"},
        ):
            with self.subTest(payload=payload):
                self.runner.return_value = payload
                with self.assertRaisesRegex(OSError, "workflow"):
                    self.sync.poll("owner/repo", kinds=["runs"])
                self.assertEqual(self.sync.observations("owner/repo"), [])

    def test_a_malformed_workflow_record_does_not_partially_store_its_page(self):
        self.runner.return_value = {
            "total_count": 2,
            "workflow_runs": [workflow_run(17), "not an object"],
        }
        with self.assertRaisesRegex(OSError, "workflow"):
            self.sync.poll("owner/repo", kinds=["runs"])
        self.assertEqual(self.sync.observations("owner/repo"), [])

    def test_injected_runners_can_still_return_bare_run_lists(self):
        self.runner.return_value = [workflow_run(17)]
        result = self.sync.poll("owner/repo", kinds=["runs"])
        self.assertEqual(result["observed"], 1)
        self.assertEqual(self.sync.observations("owner/repo")[0]["status"], "failure")

    def test_other_observation_kinds_still_use_list_responses(self):
        self.runner.return_value = [{"number": 23, "title": "Synthetic issue", "state": "open"}]
        result = self.sync.poll("owner/repo", kinds=["issues"])
        self.assertEqual(result["observed"], 1)
        self.assertEqual(result["by_kind"], {"issues": 1})
        self.runner.assert_called_once_with(
            ["api", "repos/owner/repo/issues?per_page=100&state=all&sort=updated&direction=desc"]
        )


if __name__ == "__main__":
    unittest.main()
