"""The gh CLI transport: the only way fluxrecord talks to GitHub.

These tests run a real subprocess. A fake `gh` is put on PATH, so the
argument handling, the exit codes, the timeout and the JSON decoding are
all exercised without a network call.
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fluxrecord import github_sync


class GhTransportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.bin = Path(self.temp.name) / "bin"
        self.bin.mkdir()
        self.gh = self.bin / "gh"
        # The fake gh comes first, but the rest of PATH stays so the shell
        # can still find sleep and head.
        self.patcher = mock.patch.dict(
            os.environ, {"PATH": f"{self.bin}{os.pathsep}{os.environ.get('PATH', '')}"}
        )
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.temp.cleanup()

    def write_gh(self, body: str) -> None:
        self.gh.write_text(f"#!/bin/sh\n{body}\n")
        self.gh.chmod(0o755)

    def test_a_successful_call_returns_the_json(self):
        self.write_gh("echo '[{\"number\": 1}]'")
        self.assertEqual(github_sync._gh_json(["api", "repos/o/r/issues"]), [{"number": 1}])

    def test_the_arguments_reach_gh_unchanged(self):
        self.write_gh('echo "$@" > "$0.args"; echo "[]"')
        github_sync._gh_json(["api", "repos/o/r/issues?per_page=100"])
        self.assertEqual(
            (Path(str(self.gh) + ".args")).read_text().strip(),
            "api repos/o/r/issues?per_page=100",
        )

    def test_a_failing_call_raises_with_the_reason(self):
        self.write_gh('echo "gh: Not Found (HTTP 404)" >&2; exit 1')
        with self.assertRaises(OSError) as caught:
            github_sync._gh_json(["api", "repos/o/r/issues"])
        self.assertIn("Not Found", str(caught.exception))

    def test_a_response_that_is_not_json_raises(self):
        self.write_gh("echo 'not json'")
        with self.assertRaises(OSError):
            github_sync._gh_json(["api", "repos/o/r/issues"])

    def test_an_empty_response_is_none(self):
        self.write_gh("true")
        self.assertIsNone(github_sync._gh_json(["api", "repos/o/r/issues"]))

    def test_a_missing_gh_says_so(self):
        with mock.patch.dict(os.environ, {"PATH": str(self.bin)}):
            with self.assertRaises(OSError) as caught:
                github_sync._gh_json(["api", "repos/o/r/issues"])
        self.assertIn("not found", str(caught.exception))

    def test_a_slow_gh_is_cut_off(self):
        self.write_gh("sleep 5")
        with mock.patch.object(github_sync, "TIMEOUT_SECONDS", 1):
            with self.assertRaises(OSError) as caught:
                github_sync._gh_json(["api", "repos/o/r/issues"])
        self.assertIn("timed out", str(caught.exception))

    def test_an_oversized_response_is_refused(self):
        self.write_gh("head -c 1000 /dev/zero | tr '\\0' 'x'")
        with mock.patch.object(github_sync, "MAX_RESPONSE_BYTES", 10):
            with self.assertRaises(OSError) as caught:
                github_sync._gh_json(["api", "repos/o/r/issues"])
        self.assertIn("exceeded", str(caught.exception))

    def test_a_real_sync_against_a_fake_gh(self):
        self.write_gh('echo \'[{"number": 7, "title": "Fix it", "body": "M-397001"}]\'')
        with tempfile.TemporaryDirectory() as work:
            with github_sync.GitHubSync(Path(work) / "sync.sqlite") as sync:
                result = sync.poll("owner/repo", kinds=["issues"])
                self.assertEqual(result["observed"], 1)
                report = sync.status("owner/repo")
                self.assertEqual(report["observations"]["issues"], 1)
                self.assertEqual(report["tickets"], ["M-397001"])


if __name__ == "__main__":
    unittest.main()
