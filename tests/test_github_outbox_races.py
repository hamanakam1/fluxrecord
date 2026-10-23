"""Outbox replies must not overwrite a concurrently queued replacement."""

import hashlib
import tempfile
import unittest
from pathlib import Path

from fluxrecord.github_sync import GitHubSync, marker_for


class GitHubOutboxRaceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "sync.sqlite"
        self.comments = []
        self.posted = []
        self.on_lookup = None
        self.on_post = None
        self.sync = GitHubSync(self.path, runner=self.run_gh)
        self.writer = GitHubSync(self.path, runner=self.unexpected_transport)

    def tearDown(self):
        self.writer.close()
        self.sync.close()
        self.temp.cleanup()

    def unexpected_transport(self, args):
        self.fail(f"the enqueue-only connection attempted transport: {args}")

    def run_gh(self, args):
        if args[2:3] == ["-f"]:
            self.posted.append(args[3].removeprefix("body="))
            callback, self.on_post = self.on_post, None
            if callback is not None:
                callback()
            return {"html_url": "https://example.test/comment/old"}
        callback, self.on_lookup = self.on_lookup, None
        if callback is not None:
            callback()
        return self.comments

    def enqueue(self, body, key="summary"):
        return self.writer.enqueue("owner/repo", 7, key, body)

    def row(self):
        return dict(
            self.writer.db.execute(
                "SELECT * FROM outbox WHERE repo=? AND issue=? AND key=?",
                ("owner/repo", 7, "summary"),
            ).fetchone()
        )

    def assert_pending_body(self, body):
        row = self.row()
        digest = hashlib.sha256(body.encode()).hexdigest()
        self.assertEqual(row["body"], body)
        self.assertEqual(row["body_hash"], digest)
        self.assertEqual(row["marker"], marker_for("summary", digest))
        self.assertEqual(row["state"], "pending")
        return row

    def test_stale_discovery_does_not_confirm_a_replacement(self):
        old = self.enqueue("old body")
        self.comments = [{"body": old["marker"]}]
        self.on_lookup = lambda: self.enqueue("replacement body")

        result = self.sync.publish("owner/repo")

        self.assert_pending_body("replacement body")
        self.assertEqual(self.posted, [])
        self.assertEqual(result["sent"], [])
        self.assertEqual(result["confirmed"], [])
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["pending"], 1)

    def test_successful_old_post_preserves_a_replacement_queued_during_transport(self):
        old = self.enqueue("old body")
        self.on_post = lambda: self.enqueue("replacement body")

        result = self.sync.publish("owner/repo")

        row = self.assert_pending_body("replacement body")
        self.assertEqual(row["url"], "")
        self.assertEqual(self.posted, [f"old body\n\n{old['marker']}"])
        # The old body really posted, while the replacement still needs publication.
        self.assertEqual(result["sent"], ["summary"])
        self.assertEqual(result["confirmed"], [])
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["pending"], 1)

    def test_reconciliation_does_not_confirm_a_replacement_queued_during_lookup(self):
        self.enqueue("old body")
        self.sync.publish("owner/repo")
        self.comments = [{"body": self.posted[0]}]
        self.on_lookup = lambda: self.enqueue("replacement body")

        result = self.sync.reconcile("owner/repo")

        row = self.assert_pending_body("replacement body")
        self.assertEqual(row["url"], "")
        self.assertEqual(result["confirmed"], [])
        self.assertEqual(result["missing"], ["summary"])
        self.assertEqual(len(self.posted), 1)

    def test_reconciliation_checks_the_expected_state_even_when_the_body_is_unchanged(self):
        self.enqueue("same body")
        self.sync.publish("owner/repo")
        self.comments = [{"body": self.posted[0]}]
        self.on_lookup = lambda: self.enqueue("same body")

        result = self.sync.reconcile("owner/repo")

        row = self.assert_pending_body("same body")
        self.assertEqual(row["url"], "https://example.test/comment/old")
        self.assertEqual(result["confirmed"], [])
        self.assertEqual(result["missing"], ["summary"])

    def test_reenqueue_clears_a_url_only_when_the_body_changes(self):
        self.enqueue("old body")
        self.sync.publish("owner/repo")
        self.assertEqual(self.row()["url"], "https://example.test/comment/old")

        self.enqueue("old body")
        unchanged = self.assert_pending_body("old body")
        self.assertEqual(unchanged["url"], "https://example.test/comment/old")

        self.enqueue("replacement body")
        changed = self.assert_pending_body("replacement body")
        self.assertEqual(changed["url"], "")

    def test_pending_count_includes_a_new_key_enqueued_during_post(self):
        self.enqueue("old body")
        self.on_post = lambda: self.enqueue("second body", key="second")

        result = self.sync.publish("owner/repo")

        self.assertEqual(result["sent"], ["summary"])
        self.assertEqual(result["pending"], 1)
        self.assertEqual(self.sync.status("owner/repo")["outbox"], {"pending": 1, "sent": 1})

    def test_normal_publish_and_reconcile_preserve_current_marker_and_receipt(self):
        queued = self.enqueue("normal body")

        published = self.sync.publish("owner/repo")

        self.assertEqual(published["sent"], ["summary"])
        self.assertEqual(published["pending"], 0)
        sent = self.row()
        self.assertEqual(sent["state"], "sent")
        self.assertEqual(sent["body_hash"], queued["body_hash"])
        self.assertEqual(sent["marker"], queued["marker"])
        self.assertEqual(sent["url"], "https://example.test/comment/old")
        self.comments = [{"body": self.posted[0]}]

        reconciled = self.sync.reconcile("owner/repo")

        self.assertEqual(reconciled["confirmed"], ["summary"])
        self.assertEqual(reconciled["missing"], [])
        confirmed = self.row()
        self.assertEqual(confirmed["state"], "confirmed")
        self.assertEqual(confirmed["marker"], queued["marker"])
        self.assertEqual(confirmed["url"], sent["url"])
        self.assertEqual(len(self.posted), 1)


if __name__ == "__main__":
    unittest.main()
