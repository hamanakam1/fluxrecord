import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from fluxrecord import github_cycle
from fluxrecord.github_sync import (
    GitHubSync,
    is_published,
    legacy_marker_for,
    marker_for,
    markers_for,
)


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

    def test_a_key_cannot_break_the_marker(self):
        for bad in ("with space", "new\nline", "<!-- fluxrecord:other -->", "semi;colon"):
            with self.assertRaises(ValueError):
                self.sync.enqueue("owner/repo", 7, bad, "body")

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

    def test_an_oversized_response_is_refused(self):
        from fluxrecord import github_sync

        response = Mock(returncode=0, stdout='["' + "x" * 100 + '"]', stderr="")
        with (
            patch.object(github_sync, "MAX_RESPONSE_BYTES", 10),
            patch.object(github_sync.subprocess, "run", return_value=response),
        ):
            with self.assertRaisesRegex(OSError, "exceeded 10 bytes"):
                github_sync._gh_json(["api", "repos/owner/repo/issues"])

    def test_poll_paginates_workflow_run_envelopes(self):
        runs = [
            {"id": n, "name": "checks", "status": "completed", "conclusion": "failure"}
            for n in range(1, 102)
        ]
        runner = Mock(
            side_effect=[
                {"total_count": 101, "workflow_runs": runs[:100]},
                {"total_count": 101, "workflow_runs": runs[100:]},
            ]
        )
        self.sync._run = runner
        result = self.sync.poll("owner/repo", kinds=["runs"])
        self.assertEqual(result["observed"], 101)
        self.assertEqual(result["by_kind"], {"runs": 101})
        self.assertEqual(
            runner.call_args_list[1].args[0],
            ["api", "repos/owner/repo/actions/runs?per_page=100&page=2"],
        )
        observations = self.sync.observations("owner/repo", kind="runs")
        self.assertEqual(len(observations), 101)
        self.assertTrue(all(row["status"] == "failure" for row in observations))
        self.assertTrue(all(row["action"] == "investigate_failed_check" for row in observations))

    def test_poll_refuses_malformed_workflow_run_envelopes(self):
        for payload in ({"total_count": 1}, {"workflow_runs": {}}, None):
            with self.subTest(payload=payload):
                self.sync._run = Mock(return_value=payload)
                with self.assertRaisesRegex(OSError, "runs"):
                    self.sync.poll("owner/repo", kinds=["runs"])

    def test_publish_finds_current_and_legacy_markers_on_later_pages(self):
        for make_marker in (marker_for, legacy_marker_for):
            with self.subTest(namespace=make_marker.__name__):
                queued = self.sync.enqueue("owner/repo", 7, "summary", "the body")
                runner = Mock(
                    side_effect=[
                        [{"body": "unrelated"}] * 100,
                        [{"body": make_marker("summary", queued["body_hash"])}],
                    ]
                )
                self.sync._run = runner
                result = self.sync.publish("owner/repo")
                self.assertEqual(result["confirmed"], ["summary"])
                self.assertEqual(result["sent"], [])
                self.assertEqual(runner.call_count, 2)
                self.assertEqual(
                    runner.call_args.args[0],
                    ["api", "repos/owner/repo/issues/7/comments?per_page=100&page=2"],
                )

    def test_publish_refuses_incomplete_or_malformed_comment_history(self):
        for payload in ({"message": "unexpected"}, ["bad comment"], [{}], [{"body": None}]):
            with self.subTest(payload=payload):
                self.sync.enqueue("owner/repo", 7, "summary", "the body")
                runner = Mock(return_value=payload)
                self.sync._run = runner
                result = self.sync.publish("owner/repo")
                self.assertEqual(result["failed"], ["summary"])
                self.assertEqual(result["sent"], [])
                self.assertEqual(runner.call_count, 1)
                self.assertEqual(self.sync.status("owner/repo")["outbox"], {"pending": 1})

    def test_publish_refuses_comment_history_at_page_limit(self):
        self.sync.enqueue("owner/repo", 7, "summary", "the body")
        runner = Mock(return_value=[{"body": "unrelated"}] * 100)
        self.sync._run = runner
        with patch("fluxrecord.github_sync.MAX_COMMENT_PAGES", 2, create=True):
            result = self.sync.publish("owner/repo")
        self.assertEqual(result["failed"], ["summary"])
        self.assertEqual(result["sent"], [])
        self.assertEqual(runner.call_count, 2)
        self.assertEqual(self.sync.status("owner/repo")["outbox"], {"pending": 1})

    def test_reconcile_finds_marker_on_later_page(self):
        queued = self.sync.enqueue("owner/repo", 7, "summary", "the body")
        self.sync.publish("owner/repo")
        runner = Mock(
            side_effect=[
                [{"body": "unrelated"}] * 100,
                [{"body": queued["marker"]}],
            ]
        )
        self.sync._run = runner
        result = self.sync.reconcile("owner/repo")
        self.assertEqual(result["confirmed"], ["summary"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(runner.call_count, 2)

    def test_marker_shape_matches_the_spec(self):
        marker = marker_for("summary", "a" * 64)
        self.assertTrue(marker.startswith("<!-- fluxrecord:summary:"))
        self.assertTrue(marker.endswith(" -->"))

    def test_the_legacy_namespace_is_read_but_never_written(self):
        """The marker used to be written in another project's namespace.

        It is still recognised, so a comment published before the rename
        is confirmed rather than sent again, and it is never written now.
        """
        digest = hashlib.sha256(b"the body").hexdigest()
        legacy = legacy_marker_for("summary", digest)
        self.assertTrue(legacy.startswith("<!-- taskrivet:summary:"))
        self.assertNotIn("taskrivet", marker_for("summary", digest))
        self.assertIn(legacy, markers_for("summary", digest))
        self.assertIn(marker_for("summary", digest), markers_for("summary", digest))

    def test_a_comment_published_before_the_rename_is_never_resent(self):
        """The whole point of the dual-read window."""
        body = "the body"
        digest = hashlib.sha256(body.encode()).hexdigest()
        self.sync.enqueue("owner/repo", 7, "summary", body)
        # An earlier version of this tool published the comment, and died
        # before recording that it had.
        self.gh.existing = [{"body": f"{body}\n\n{legacy_marker_for('summary', digest)}"}]
        published = self.sync.publish("owner/repo")
        self.assertEqual(published["confirmed"], ["summary"])
        self.assertEqual(self.gh.posted, [])

    def test_reconciliation_recognises_a_comment_published_before_the_rename(self):
        body = "the body"
        digest = hashlib.sha256(body.encode()).hexdigest()
        self.sync.enqueue("owner/repo", 7, "summary", body)
        self.sync.publish("owner/repo")
        self.gh.existing = [{"body": f"{body}\n\n{legacy_marker_for('summary', digest)}"}]
        reconciled = self.sync.reconcile("owner/repo")
        self.assertEqual(reconciled["confirmed"], ["summary"])
        self.assertEqual(reconciled["missing"], [])

    def test_a_published_comment_carries_the_current_namespace(self):
        self.sync.enqueue("owner/repo", 7, "summary", "the body")
        self.sync.publish("owner/repo")
        self.assertIn("<!-- fluxrecord:summary:", self.gh.posted[0])

    def test_is_published_reads_both_namespaces(self):
        digest = hashlib.sha256(b"the body").hexdigest()
        current = marker_for("summary", digest)
        legacy = legacy_marker_for("summary", digest)
        self.assertTrue(is_published([f"text\n\n{current}"], "summary", digest))
        self.assertTrue(is_published([f"text\n\n{legacy}"], "summary", digest))
        self.assertFalse(is_published([f"text\n\n{current}"], "other", digest))
        self.assertFalse(is_published(["a comment with no marker"], "summary", digest))


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

    def test_cycle_rejects_non_boolean_controls_before_any_side_effect(self):
        for field in ("publish", "intake"):
            for value in ("false", "true", "", 0, 1, None, [], {}, [False]):
                with self.subTest(field=field, value=value):
                    sync_db = self.root / "unused-sync.sqlite"
                    ledger_db = self.root / "unused-ledger.sqlite"
                    path = self.write_config(
                        {
                            "repos": ["owner/repo"],
                            "kinds": ["issues"],
                            "sync_db": str(sync_db),
                            "ledger_db": str(ledger_db),
                            field: value,
                        }
                    )
                    gh = FakeGh(issues=[issue(1)])
                    with self.assertRaisesRegex(ValueError, f"{field} must be a JSON boolean"):
                        github_cycle.cycle(path, runner=gh)
                    self.assertEqual(gh.calls, [])
                    self.assertFalse(sync_db.exists())
                    self.assertFalse(ledger_db.exists())

    def test_explicit_false_leaves_queued_comments_and_tickets_untouched(self):
        sync_db = self.root / "github.sqlite"
        ledger_db = self.root / "ledger.sqlite"
        gh = FakeGh(issues=[issue(1)])
        with GitHubSync(sync_db, runner=gh) as sync:
            sync.enqueue("owner/repo", 1, "summary", "Synthetic review summary")
        path = self.write_config(
            {
                "repos": ["owner/repo"],
                "kinds": ["issues"],
                "sync_db": str(sync_db),
                "ledger_db": str(ledger_db),
                "publish": False,
                "intake": False,
            }
        )
        result = github_cycle.cycle(path, runner=gh)
        self.assertFalse(result["publish_requested"])
        self.assertFalse(result["intake_requested"])
        self.assertEqual(result["published"], 0)
        self.assertEqual(result["ticketed"], 0)
        self.assertEqual(gh.posted, [])
        self.assertFalse(ledger_db.exists())

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
