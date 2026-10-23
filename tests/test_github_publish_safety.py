"""Synthetic transport regressions for retry-safe comment publication."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord import github_sync
from fluxrecord.github_sync import GitHubSync, legacy_marker_for


class PagedComments:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []
        self.posted = []
        self.post_error = None

    def __call__(self, args):
        self.calls.append(args)
        if args[2:3] == ["-f"]:
            if self.post_error:
                raise self.post_error
            self.posted.append(args[3])
            return {"html_url": "https://example.test/comments/1"}
        page = int(args[1].split("&page=")[1]) if "&page=" in args[1] else 1
        result = self.pages[page - 1]
        if isinstance(result, Exception):
            raise result
        return result


class GitHubPublishSafetyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.gh = PagedComments([[]])
        self.sync = GitHubSync(Path(self.temp.name) / "sync.sqlite", runner=self.gh)
        self.addCleanup(self.sync.close)
        self.queued = self.sync.enqueue("owner/repo", 7, "summary", "synthetic body")
        self.full_page = [{"body": f"unrelated comment {i}"} for i in range(100)]

    def test_publish_finds_current_and_legacy_markers_on_later_pages(self):
        markers = [
            self.queued["marker"],
            legacy_marker_for("summary", self.queued["body_hash"]),
        ]
        for marker in markers:
            with self.subTest(marker=marker):
                self.sync.enqueue("owner/repo", 7, "summary", "synthetic body")
                self.gh.calls.clear()
                self.gh.pages = [self.full_page, [{"body": marker}]]
                result = self.sync.publish("owner/repo")
                self.assertEqual(result["confirmed"], ["summary"])
                self.assertEqual(result["pending"], 0)
                self.assertEqual(self.gh.posted, [])
                self.assertEqual(
                    self.gh.calls,
                    [
                        ["api", "repos/owner/repo/issues/7/comments?per_page=100"],
                        ["api", "repos/owner/repo/issues/7/comments?per_page=100&page=2"],
                    ],
                )

    def test_reconcile_finds_a_marker_on_later_pages(self):
        self.sync.publish("owner/repo")
        self.gh.pages = [self.full_page, [{"body": self.queued["marker"]}]]
        result = self.sync.reconcile("owner/repo")
        self.assertEqual(result["confirmed"], ["summary"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(len(self.gh.posted), 1)

    def test_complete_lookup_allows_one_post_after_an_empty_last_page(self):
        self.gh.pages = [self.full_page, []]
        result = self.sync.publish("owner/repo")
        self.assertEqual(result["sent"], ["summary"])
        self.assertEqual(len(self.gh.calls), 3)
        self.assertEqual(len(self.gh.posted), 1)

    def test_later_page_failure_leaves_the_comment_pending(self):
        self.gh.pages = [self.full_page, OSError("synthetic read failure")]
        result = self.sync.publish("owner/repo")
        self.assertEqual(result["failed"], ["summary"])
        self.assertEqual(result["pending"], 1)
        self.assertEqual(self.gh.posted, [])
        self.assertEqual(self.sync.status("owner/repo")["outbox"], {"pending": 1})

    def test_malformed_pages_never_authorize_a_post(self):
        malformed = [
            None,
            {},
            {"message": "denied"},
            [None],
            [{}],
            [{"body": None}],
            [{"body": 7}],
            [{"body": [self.queued["marker"]]}],
            [*self.full_page, {"body": "too many records"}],
        ]
        for payload in malformed:
            for prefix in ([], [self.full_page]):
                with self.subTest(payload=payload, later=bool(prefix)):
                    self.sync.enqueue("owner/repo", 7, "summary", "synthetic body")
                    self.gh.pages = [*prefix, payload]
                    result = self.sync.publish("owner/repo")
                    self.assertEqual(result["failed"], ["summary"])
                    self.assertEqual(result["pending"], 1)
                    self.assertEqual(self.gh.posted, [])

    def test_capped_lookup_never_posts_or_claims_confirmation(self):
        self.gh.pages = [self.full_page] * 3
        with mock.patch.object(github_sync, "MAX_COMMENT_PAGES", 3, create=True):
            result = self.sync.publish("owner/repo")
        self.assertEqual(result["failed"], ["summary"])
        self.assertEqual(result["pending"], 1)
        self.assertEqual(len(self.gh.calls), 3)
        self.assertEqual(self.gh.posted, [])

    def test_a_marker_on_the_full_last_allowed_page_is_enough(self):
        self.gh.pages = [
            self.full_page,
            self.full_page,
            [*self.full_page[:99], {"body": self.queued["marker"]}],
        ]
        with mock.patch.object(github_sync, "MAX_COMMENT_PAGES", 3, create=True):
            result = self.sync.publish("owner/repo")
        self.assertEqual(result["confirmed"], ["summary"])
        self.assertEqual(len(self.gh.calls), 3)
        self.assertEqual(self.gh.posted, [])

    def test_capped_reconcile_leaves_sent_state_unchanged(self):
        self.sync.publish("owner/repo")
        self.gh.calls.clear()
        self.gh.pages = [self.full_page] * 3
        with mock.patch.object(github_sync, "MAX_COMMENT_PAGES", 3, create=True):
            result = self.sync.reconcile("owner/repo")
        self.assertEqual(result["missing"], ["summary"])
        self.assertEqual(self.sync.status("owner/repo")["outbox"], {"sent": 1})
        self.assertEqual(len(self.gh.calls), 3)
        self.assertEqual(len(self.gh.posted), 1)

    def test_failed_posts_are_counted_as_pending(self):
        self.gh.post_error = OSError("synthetic write failure")
        result = self.sync.publish("owner/repo")
        self.assertEqual(result["failed"], ["summary"])
        self.assertEqual(result["pending"], 1)
        self.assertEqual(self.sync.status("owner/repo")["outbox"], {"pending": 1})
