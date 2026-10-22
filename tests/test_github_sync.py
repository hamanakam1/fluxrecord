import json
import tempfile
import unittest
from pathlib import Path

from fluxrecord import github_cycle
from fluxrecord.github_sync import GitHubSync, marker_for


class FakeGh:
    """Stand-in for the gh CLI. Records every call it is handed."""

    def __init__(self, issues=None, comments=None, existing=None):
        self.issues = issues if issues is not None else []
        self.comments = comments if comments is not None else []
        self.existing = existing if existing is not None else []
        self.posted = []
        self.calls = []

    def __call__(self, args):
        self.calls.append(args)
        path = args[1] if len(args) > 1 else ""
        if "?" not in path and args[2:3] == ["-f"]:
            body = args[3][len("body=") :]
            self.posted.append(body)
            return {"html_url": "https://example.test/comment/1"}
        if "/issues/comments?" in path:
            return self.comments
        if path.startswith("repos/") and path.endswith("/comments?per_page=100"):
            return self.existing
        if "/issues?" in path:
            return self.issues
        if "/pulls?" in path:
            return []
        return []


def issue(number, title="Fix the thing", body="no ticket", **extra):
    record = {
        "number": number,
        "title": title,
        "body": body,
        "state": "open",
        "html_url": f"https://example.test/issues/{number}",
        "user": {"login": "octo"},
    }
    record.update(extra)
    return record


class GitHubSyncTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "github.sqlite"
        self.gh = FakeGh(issues=[issue(1), issue(2, body="see M-397001 for context")])
        self.sync = GitHubSync(self.path, runner=self.gh)

    def tearDown(self):
        self.sync.close()
        self.temp.cleanup()

    def test_poll_records_observations(self):
        result = self.sync.poll("owner/repo")
        self.assertEqual(result["observed"], 2)
        self.assertEqual(result["by_kind"]["issues"], 2)
        self.assertEqual(self.sync.status("owner/repo")["observations"]["issues"], 2)

    def test_poll_detects_a_changed_revision(self):
        self.sync.poll("owner/repo")
        self.gh.issues = [
            issue(1, title="Fix the thing differently"),
            issue(2, body="see M-397001 for context"),
        ]
        result = self.sync.poll("owner/repo")
        self.assertEqual(result["changed"], 1)
        self.assertIn("1", self.sync.status("owner/repo")["changed"])

    def test_status_surfaces_ticket_references(self):
        self.sync.poll("owner/repo")
        self.assertEqual(self.sync.status("owner/repo")["tickets"], ["M-397001"])

    def test_poll_rejects_a_bad_repo_or_kind(self):
        with self.assertRaises(ValueError):
            self.sync.poll("not-a-repo")
        with self.assertRaises(ValueError):
            self.sync.poll("owner/repo", kinds=["nonsense"])

    def test_enqueue_validates_key_and_body(self):
        with self.assertRaises(ValueError):
            self.sync.enqueue("owner/repo", 1, "", "body")
        with self.assertRaises(ValueError):
            self.sync.enqueue("owner/repo", 1, "k" * 101, "body")
        with self.assertRaises(ValueError):
            self.sync.enqueue("owner/repo", 1, "key", "   ")

    def test_enqueue_is_idempotent_on_key(self):
        first = self.sync.enqueue("owner/repo", 7, "summary", "body one")
        second = self.sync.enqueue("owner/repo", 7, "summary", "body one")
        self.assertEqual(first["marker"], second["marker"])
        self.assertEqual(self.sync.status("owner/repo")["outbox"]["pending"], 1)

    def test_publish_appends_the_marker(self):
        queued = self.sync.enqueue("owner/repo", 7, "summary", "the body")
        published = self.sync.publish("owner/repo")
        self.assertEqual(published["sent"], ["summary"])
        self.assertIn(queued["marker"], self.gh.posted[0])

    def test_publish_never_resends_a_marked_comment(self):
        queued = self.sync.enqueue("owner/repo", 7, "summary", "the body")
        # A previous run posted the comment but died before recording it.
        self.gh.existing = [{"body": f"the body\n\n{queued['marker']}"}]
        published = self.sync.publish("owner/repo")
        self.assertEqual(published["confirmed"], ["summary"])
        self.assertEqual(self.gh.posted, [])

    def test_publish_marks_failed_without_raising(self):
        class Broken(FakeGh):
            def __call__(self, args):
                if args[2:3] == ["-f"]:
                    raise OSError("gh api failed: rate limited")
                return super().__call__(args)

        self.sync.close()
        self.gh = Broken()
        self.sync = GitHubSync(self.path, runner=self.gh)
        self.sync.enqueue("owner/repo", 7, "summary", "the body")
        result = self.sync.publish("owner/repo")
        self.assertEqual(result["failed"], ["summary"])
        self.assertEqual(result["sent"], [])

    def test_reconcile_confirms_a_visible_comment(self):
        self.sync.enqueue("owner/repo", 7, "summary", "the body")
        self.sync.publish("owner/repo")
        self.gh.existing = [{"body": self.gh.posted[0]}]
        result = self.sync.reconcile("owner/repo")
        self.assertEqual(result["confirmed"], ["summary"])
        self.assertEqual(self.sync.status("owner/repo")["outbox"], {"confirmed": 1})

    def test_marker_shape_matches_the_spec(self):
        marker = marker_for("summary", "a" * 64)
        self.assertTrue(marker.startswith("<!-- taskrivet:summary:"))
        self.assertTrue(marker.endswith(" -->"))


class GitHubCycleTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / "cycle.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_config(self, payload):
        self.config.write_text(json.dumps(payload))
        return str(self.config)

    def test_cycle_requires_a_config(self):
        with self.assertRaises(ValueError):
            github_cycle.cycle(str(self.root / "missing.json"))

    def test_cycle_rejects_a_config_without_repos(self):
        path = self.write_config({"repos": []})
        with self.assertRaises(ValueError):
            github_cycle.cycle(path)

    def test_cycle_observes_without_publishing_by_default(self):
        path = self.write_config(
            {
                "repos": ["owner/repo"],
                "kinds": ["issues"],
                "sync_db": str(self.root / "github.sqlite"),
            }
        )
        gh = FakeGh(issues=[issue(1)])
        result = github_cycle.cycle(path, runner=gh)
        self.assertEqual(result["observed"], 1)
        self.assertFalse(result["publish_requested"])
        self.assertEqual(result["published"], 0)
        self.assertEqual(gh.posted, [])

    def test_cycle_publishes_only_when_asked(self):
        path = self.write_config(
            {
                "repos": ["owner/repo"],
                "kinds": ["issues"],
                "sync_db": str(self.root / "github.sqlite"),
                "publish": True,
            }
        )
        gh = FakeGh(issues=[issue(1)])
        result = github_cycle.cycle(path, runner=gh)
        self.assertTrue(result["publish_requested"])
        self.assertEqual(result["published"], 0)

    def test_cycle_records_a_repository_error_without_stopping(self):
        path = self.write_config(
            {
                "repos": [{"repo": "not-a-repo"}, {"repo": "owner/repo"}],
                "kinds": ["issues"],
                "sync_db": str(self.root / "github.sqlite"),
            }
        )
        result = github_cycle.cycle(path, runner=FakeGh(issues=[issue(1)]))
        self.assertEqual(len(result["repos"]), 2)
        self.assertIn("error", result["repos"][0])
        self.assertNotIn("error", result["repos"][1])


if __name__ == "__main__":
    unittest.main()
