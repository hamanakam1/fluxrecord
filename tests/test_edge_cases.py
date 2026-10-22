"""The error paths: the ones a user reaches by getting something wrong."""

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord import ascii_art, dashboard
from fluxrecord.auth import AuthError, bearer_token, require_subject, verify
from fluxrecord.core import Conflict, Ledger
from fluxrecord.github_sync import _identity, _summary


class AsciiArtEdgeTest(unittest.TestCase):
    def test_a_grey_colour_uses_the_grey_palette(self):
        self.assertGreaterEqual(ascii_art._rgb_to_256((128, 128, 128)), 232)
        self.assertLess(ascii_art._rgb_to_256((128, 128, 128)), 256)

    def test_a_colour_uses_the_cube_palette(self):
        self.assertLess(ascii_art._rgb_to_256((34, 211, 238)), 232)

    def test_painting_an_empty_line_is_harmless(self):
        self.assertEqual(ascii_art._paint("   ", (0, 0, 0), (255, 255, 255)), "   ")

    def test_the_module_prints_its_mark_when_run_directly(self):
        result = subprocess.run(
            [sys.executable, "-m", "fluxrecord.ascii_art", "mini"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("fluxrecord", result.stdout)

    def test_a_dumb_terminal_gets_no_colour(self):
        with mock.patch.dict(os.environ, {"TERM": "dumb"}):
            self.assertFalse(ascii_art.color_enabled())

    def test_a_normal_terminal_gets_colour(self):
        with mock.patch.dict(os.environ, {"TERM": "xterm-256color"}):
            with mock.patch.dict(os.environ, {}, clear=True):
                self.assertTrue(ascii_art.color_enabled())


class MiddlewareEdgeTest(unittest.TestCase):
    def test_an_empty_bearer_token_is_refused(self):
        with self.assertRaises(AuthError):
            bearer_token({"authorization": "Bearer   "})

    def test_a_subject_must_match(self):
        from fluxrecord.auth import issue

        token, _ = issue("terminal-1", "secret")
        claims = verify(token, "secret")
        require_subject(claims, "terminal-1")
        with self.assertRaises(AuthError):
            require_subject(claims, "terminal-2")


class DashboardEdgeTest(unittest.TestCase):
    def test_a_response_that_is_not_json_is_refused(self):
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                body = b"<html>not json</html>"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                return

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            origin = f"http://127.0.0.1:{server.server_address[1]}"
            with self.assertRaises(OSError):
                dashboard.preview(origin, "planned", 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


class GithubKindTest(unittest.TestCase):
    def test_every_kind_has_an_identity_and_a_summary(self):
        record = {
            "id": 5,
            "number": 5,
            "title": "Title",
            "body": "Body",
            "name": "run",
            "conclusion": "failure",
            "sha": "abcdef1234567890",
            "tag_name": "v1",
            "type": "PushEvent",
        }
        for kind in (
            "issues",
            "comments",
            "pulls",
            "review_comments",
            "releases",
            "runs",
            "commits",
            "events",
        ):
            self.assertTrue(_identity(kind, record), kind)
            self.assertTrue(_summary(kind, record), kind)

    def test_a_commit_identity_is_short(self):
        self.assertEqual(_identity("commits", {"sha": "abcdef1234567890"}), "abcdef123456")


class LedgerEdgeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "ledger.sqlite"
        self.ledger = Ledger(self.path)
        self.ledger.add_task("M-1", "Objective", "Acceptance", scope=["repo/x"])

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def test_an_unknown_session_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.checkpoint("nope", "s", "e", "n", 1)

    def test_an_invalid_decision_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.decide("M-1", "sideways", "reason")

    def test_a_decision_without_a_reason_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.decide("M-1", "accept", "   ")

    def test_an_empty_scope_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.add_task("M-2", "O", "A", scope=[])

    def test_a_seed_key_that_is_too_long_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.add_task("M-2", "O", "A", seed_key="k" * 101)

    def test_a_context_without_a_source_is_refused(self):
        with self.assertRaises(ValueError):
            self.ledger.add_context("M-1", "body", "")

    def test_a_context_priority_must_be_an_integer(self):
        with self.assertRaises(ValueError):
            self.ledger.add_context("M-1", "body", "source", priority="high")

    def test_a_context_ttl_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.ledger.add_context("M-1", "body", "source", ttl=0)

    def test_a_profile_needs_a_known_cost(self):
        with self.assertRaises(ValueError):
            self.ledger.profile("p", ["code"], 100, "free")

    def test_a_profile_needs_at_least_one_kind(self):
        with self.assertRaises(ValueError):
            self.ledger.profile("p", [], 100, "zero_incremental")

    def test_a_profile_needs_a_context_limit(self):
        with self.assertRaises(ValueError):
            self.ledger.profile("p", ["code"], 0, "zero_incremental")

    def test_a_profile_ttl_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.ledger.profile("p", ["code"], 100, "zero_incremental", ttl=0)

    def test_a_lease_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.ledger.start("M-1", "terminal-1", lease_seconds=0)

    def test_a_checkpoint_token_count_must_be_a_nonnegative_integer(self):
        started = self.ledger.start("M-1", "terminal-1")
        with self.assertRaises(ValueError):
            self.ledger.checkpoint(started["session_id"], "s", "e", "n", -1)

    def test_a_finish_needs_evidence(self):
        started = self.ledger.start("M-1", "terminal-1")
        with self.assertRaises(ValueError):
            self.ledger.finish(started["session_id"], "  ")

    def test_a_task_cannot_start_twice(self):
        self.ledger.start("M-1", "terminal-1")
        with self.assertRaises(Conflict):
            self.ledger.start("M-1", "terminal-2")

    def test_a_blocked_task_cannot_be_accepted(self):
        started = self.ledger.start("M-1", "terminal-1")
        self.ledger.fail(started["session_id"], "tool", "broken")
        with self.assertRaises(Conflict):
            self.ledger.decide("M-1", "accept", "reviewed")


if __name__ == "__main__":
    unittest.main()
